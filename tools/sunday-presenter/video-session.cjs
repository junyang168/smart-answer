const crypto = require('node:crypto');
const net = require('node:net');

function localAddress(address) {
  if (/^[a-z0-9-]{1,63}\.local\.?$/i.test(address)) return true;
  if (net.isIP(address) === 6) return address === '::1' || /^(f[cd]|fe80:)/i.test(address);
  if (net.isIP(address) !== 4) return false;
  const [a, b] = address.split('.').map(Number);
  return a === 127 || a === 10 || (a === 192 && b === 168) ||
    (a === 172 && b >= 16 && b <= 31) || (a === 169 && b === 254);
}
function validateSDP(sdp) {
  if (typeof sdp !== 'string' || Buffer.byteLength(sdp) > 256 * 1024) throw Error('Invalid video description');
  const lines = sdp.split(/\r?\n/);
  const media = lines.filter(line => line.startsWith('m='));
  if (media.length !== 1 || !/^m=video \d+ UDP\/TLS\/RTP\/SAVPF /.test(media[0]) ||
      !lines.some(line => /^a=fingerprint:sha-256 /i.test(line)) ||
      !lines.some(line => line.startsWith('a=ice-ufrag:')) || !lines.some(line => line.startsWith('a=ice-pwd:'))) {
    throw Error('Only encrypted video is supported');
  }
  const candidates = lines.filter(line => line.startsWith('a=candidate:'));
  if (!candidates.length || candidates.length > 64) throw Error('No local video route');
  for (const line of candidates) {
    const parts = line.trim().split(/\s+/);
    if (parts[6] !== 'typ' || parts[7] !== 'host' || !localAddress(parts[4])) throw Error('Only local host candidates are supported');
  }
  return sdp;
}

// One paired viewer. Signaling polls renew a bounded lease; slide keys use their
// own queue and never await capture, ICE gathering or renderer work.
class VideoSession {
  constructor({ driver, ready, now = Date.now }) {
    this.driver = driver; this.ready = ready; this.now = now; this.current = null; this.starts = []; this.closed = false;
    this.timer = setInterval(() => this.expire(), 1000); this.timer.unref();
  }
  expire() { if (this.current && (this.now() - this.current.seen > 15000 || (['starting', 'offer', 'answering'].includes(this.current.status) && this.now() - this.current.created > 30000) || !this.ready())) this.reset(); }
  reset() {
    const old = this.current; this.current = null;
    if (old) this.driver.stop(old.id);
  }
  close() { this.closed = true; clearInterval(this.timer); this.reset(); }
  describe(item) {
    return { sessionId: item.id, status: item.status,
      ...(item.status === 'offer' ? { offer: { type: 'offer', sdp: item.offer } } : {}),
      ...(item.error ? { error: item.error } : {}) };
  }
  request(data) {
    if (this.closed) throw Error('Video service stopped');
    if (!data || typeof data !== 'object') throw Error('Invalid video request');
    this.expire();
    if (data.op === 'start') {
      if (!/^[a-zA-Z0-9-]{16,80}$/.test(data.clientId || '')) throw Error('Invalid viewer');
      if (!this.ready()) throw Error('請先播放已確認的 PPT；輸出不可最小化。');
      if (!this.current || this.current.client !== data.clientId) {
        this.starts = this.starts.filter(time => this.now() - time < 30000);
        if (this.starts.length >= 6) throw Error('畫面連線重試過快，請稍候。');
        this.starts.push(this.now());
        this.reset();
        const item = { id: crypto.randomUUID(), client: data.clientId, status: 'starting', created: this.now(), seen: this.now() };
        this.current = item;
        Promise.resolve().then(() => this.current === item ? this.driver.offer(item.id) : undefined).then(sdp => {
          if (this.current !== item) return;
          item.offer = validateSDP(sdp); item.status = 'offer';
        }).catch(() => {
          if (this.current === item) { item.status = 'failed'; item.error = '畫面分享未完成，請檢查主電腦的螢幕錄製權限並重試。'; this.driver.stop(item.id); }
        });
      }
      this.current.seen = this.now();
      return this.describe(this.current);
    }
    const item = this.current;
    if (!item || data.sessionId !== item.id) throw Error('Video session expired');
    item.seen = this.now();
    if (data.op === 'poll') return this.describe(item);
    if (data.op === 'stop') { this.reset(); return { status: 'stopped' }; }
    if (data.op !== 'answer' || !['offer', 'answering', 'streaming'].includes(item.status)) throw Error('Invalid video operation');
    const sdp = validateSDP(data.sdp);
    if (item.answer && item.answer !== sdp) throw Error('Conflicting video answer');
    if (!item.answer) {
      item.answer = sdp; item.status = 'answering';
      Promise.resolve().then(() => this.driver.answer(item.id, sdp)).then(() => {
        if (this.current === item) item.status = 'streaming';
      }).catch(() => { if (this.current === item) { item.status = 'failed'; item.error = '畫面連線失敗，請重新連線。'; this.driver.stop(item.id); } });
    }
    return this.describe(item);
  }
}
module.exports = { VideoSession, validateSDP, localAddress };
