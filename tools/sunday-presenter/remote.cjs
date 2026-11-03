const http = require('node:http');
const https = require('node:https');
const crypto = require('node:crypto');
const os = require('node:os');
const { spawn } = require('node:child_process');

const { bonjourType: TYPE } = require('./protocol.json');
function localPeer(ip = '') {
  ip = ip.replace(/^::ffff:/, '');
  if (ip === '::1' || /^f[cd][0-9a-f]:/i.test(ip) || /^fe80:/i.test(ip)) return true;
  const bits = ip.split('.').map(Number);
  return bits.length === 4 && bits.every(n => Number.isInteger(n) && n >= 0 && n <= 255) &&
    (bits[0] === 127 || bits[0] === 10 || (bits[0] === 192 && bits[1] === 168) ||
      (bits[0] === 172 && bits[1] >= 16 && bits[1] <= 31) || (bits[0] === 169 && bits[1] === 254));
}
function equal(a, b) {
  const left = Buffer.from(a || ''), right = Buffer.from(b || '');
  return left.length === right.length && crypto.timingSafeEqual(left, right);
}
function publish(port, id, failed) {
  let stopping = false;
  const child = spawn('/usr/bin/dns-sd', ['-R', `Sunday Presenter · ${os.hostname().split('.')[0]}`,
    TYPE, 'local', String(port), `id=${id}`], { stdio: 'ignore' });
  child.on('error', failed);
  child.on('exit', () => { if (!stopping) failed(); });
  return () => { stopping = true; child.kill(); };
}

class RemoteControl {
  constructor({ state, key, changed = () => {}, advertise = publish, now = Date.now, identity, video } = {}) {
    this.getState = state; this.key = key; this.changed = changed; this.advertise = advertise; this.now = now;
    this.server = null; this.error = ''; this.observe(null); this.queue = Promise.resolve();
    this.video = video; this.identity = identity; this.peerHash = identity?.peerHash;
  }
  observe(active) {
    if (this.active !== active) { this.active = active; this.binding = crypto.randomBytes(16).toString('hex'); }
  }
  publicState() {
    const value = this.getState(); this.observe(value.active);
    const page = value.pages.find(p => p.id === value.active);
    return { live: !!page?.ready, deck: page?.deck === 'worship' ? '主日崇拜' : page ? '講道' : '尚未播放',
      binding: this.binding, ...(this.video ? { protocolVersion: 2, capabilities: ['ppt-video-v1'] } : {}) };
  }
  snapshot() {
    return { enabled: !!this.server, code: this.code || '', paired: !!this.lastSeen && this.now() - this.lastSeen < 10000,
      remembered: !!this.peerHash,
      error: this.error, expiresAt: this.codeUntil || null };
  }
  async start({ host = '0.0.0.0' } = {}) {
    if (this.server) return;
    if (!this.identity && host !== '127.0.0.1') throw Error('講員連線需要加密設定。');
    this.code = this.peerHash ? null : String(crypto.randomInt(100000, 1000000)); this.codeUntil = this.now() + 5 * 60000;
    this.lastSeen = null; this.attempts = []; this.seen = new Map(); this.error = ''; this.generation = crypto.randomUUID();
    const handler = (req, res) => this.request(req, res);
    const server = this.identity ? https.createServer(this.identity.tls, handler) : http.createServer(handler);
    server.requestTimeout = 5000; server.headersTimeout = 5000;
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, host, resolve); });
    this.server = server; this.stopAdvertising = this.advertise(server.address().port, this.identity?.id || 'fixture', () => {
      this.error = '本機網路廣播未啟動，請檢查本機網路權限。'; this.changed();
    });
    this.changed();
  }
  async stop() {
    this.stopAdvertising?.(); this.stopAdvertising = null;
    this.video?.reset();
    this.generation = null; this.lastSeen = null; this.code = null;
    const server = this.server; this.server = null;
    if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
    this.changed();
  }
  async forget() {
    await this.stop(); this.identity?.remember(null); this.peerHash = null; await this.start();
  }
  async request(req, res) {
    const send = (status, data) => {
      if (!res.destroyed) { res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(data)); }
    };
    if (!localPeer(req.socket.remoteAddress) || req.headers.origin) { send(403, { error: '僅供同一區域網路的講員 app 使用。' }); return; }
    try {
      const authorization = req.headers.authorization || '';
      const digest = /^Bearer [a-zA-Z0-9_-]{43}$/.test(authorization) ?
        crypto.createHash('sha256').update(authorization.slice(7)).digest('hex') : '';
      const paired = !!this.peerHash && equal(digest, this.peerHash);
      const generation = this.generation;
      if (paired) { this.lastSeen = this.now(); this.changed(); }
      if (req.method === 'GET' && req.url === '/state') {
        send(paired ? 200 : 401, paired ? this.publicState() : { error: '請先連線。' }); return;
      }
      if (req.method !== 'POST' || !['/pair', '/key', ...(this.video ? ['/video'] : [])].includes(req.url) ||
          req.headers['content-type']?.split(';')[0] !== 'application/json') { send(404, { error: 'Not found' }); return; }
      if (req.url !== '/pair' && !paired) { send(401, { error: '請重新連線。' }); return; }
      let body = '';
      for await (const chunk of req) {
        body += chunk;
        if (Buffer.byteLength(body) > (req.url === '/video' ? 270000 : 4096)) { send(413, { error: 'Request too large' }); return; }
      }
      const data = JSON.parse(body);
      if (req.url === '/pair') {
        this.attempts = this.attempts.filter(at => this.now() - at < 60000);
        if (this.attempts.length >= 5) { send(429, { error: '請等一分鐘再試。' }); return; }
        this.attempts.push(this.now());
        if (!this.code || this.now() > this.codeUntil || !equal(data.code, this.code)) {
          send(401, { error: '數字不符或已過期，請在主電腦重新開啟講員翻頁。' }); return;
        }
        const token = crypto.randomBytes(32).toString('base64url');
        const digest = crypto.createHash('sha256').update(token).digest('hex');
        this.identity?.remember(digest); this.peerHash = digest;
        this.lastSeen = this.now(); this.code = null; this.changed();
        send(200, { token, ...this.publicState() }); return;
      }
      if (req.url === '/video') {
        if (generation !== this.generation || !equal(digest, this.peerHash)) { send(401, { error: '請重新連線。' }); return; }
        try { send(200, this.video.request(data)); }
        catch (error) { send(409, { error: error.message }); }
        return;
      }
      if (!['Left', 'Right'].includes(data.key) || typeof data.id !== 'string' ||
          !/^[a-zA-Z0-9-]{16,80}$/.test(data.id) || typeof data.binding !== 'string') { send(400, { error: 'Invalid command' }); return; }
      const previous = this.seen.get(data.id);
      const fingerprint = data.key + ':' + data.binding;
      if (previous && previous.fingerprint !== fingerprint) { send(409, { error: 'Command identifier conflict' }); return; }
      if (this.seen.size >= 2000 && !previous) { send(429, { error: '請在主電腦重新開啟講員翻頁。' }); return; }
      let result = previous?.result;
      if (!result) {
        const task = this.queue.then(async () => {
          const state = this.publicState();
          if (this.generation !== generation || !equal(digest, this.peerHash) || !state.live || state.binding !== data.binding) throw Error('畫面已切換，請再按一次。');
          const active = this.active;
          await this.key(data.key, active, () => this.generation === generation && equal(digest, this.peerHash) && this.publicState().binding === data.binding);
          return this.publicState();
        });
        result = task.then(value => ({ status: 200, value }), error => ({ status: 409, value: { error: error.message } }));
        this.seen.set(data.id, { fingerprint, result }); this.queue = result.then(() => {});
      }
      const response = await result; send(response.status, response.value);
    } catch { send(400, { error: 'Invalid request' }); }
  }
}
module.exports = { RemoteControl, localPeer, TYPE };
