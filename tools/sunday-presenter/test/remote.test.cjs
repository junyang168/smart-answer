const test = require('node:test');
test('Bonjour service uses a valid short protocol label shared by both app bundles', () => {
  const { TYPE } = require('../remote.cjs');
  const assert = require('node:assert/strict');
  assert.match(TYPE, /^_[a-z][a-z0-9-]{0,14}\._tcp$/);
  assert.equal(TYPE, require('../protocol.json').bonjourType);
});
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const https = require('node:https');
const { RemoteControl, localPeer } = require('../remote.cjs');
const { identity } = require('../remote-identity.cjs');

function request(remote, route, body, token, headers = {}) {
  return new Promise((resolve, reject) => {
    const options = { hostname: '127.0.0.1', port: remote.server.address().port, path: route,
      method: body === undefined ? 'GET' : 'POST', ca: remote.identity.tls.cert,
      checkServerIdentity: (_host, cert) => cert.fingerprint256.replaceAll(':', '').toLowerCase() === remote.identity.id ? undefined : Error('Wrong host'),
      headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...(token ? { Authorization: 'Bearer ' + token } : {}), ...headers } };
    const req = https.request(options, res => {
      let text = ''; res.on('data', data => { text += data; });
      res.on('end', () => resolve({ status: res.statusCode, data: JSON.parse(text) }));
    });
    req.on('error', reject); req.end(body === undefined ? undefined : JSON.stringify(body));
  });
}
async function fixture(t, options = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'remote-test-'));
  const source = identity(directory), calls = [];
  const state = { active: 1, pages: [{ id: 1, ready: true, deck: 'worship' }] };
  const remote = new RemoteControl({ identity: source, advertise: () => () => {},
    state: () => state, key: async (...args) => { calls.push(args); }, ...options });
  await remote.start({ host: '127.0.0.1' });
  t.after(async () => { await remote.stop(); fs.rmSync(directory, { recursive: true, force: true }); });
  const pair = await request(remote, '/pair', { code: remote.snapshot().code });
  assert.equal(pair.status, 200);
  return { remote, state, directory, token: pair.data.token, binding: pair.data.binding, calls };
}

test('encrypted pairing remembers a hash; reconnect survives a source restart and new port', async t => {
  const f = await fixture(t);
  const saved = fs.readFileSync(path.join(f.directory, 'remote-peer.json'), 'utf8');
  assert.ok(!saved.includes(f.token)); assert.match(saved, /peerHash/);
  const publicState = await request(f.remote, '/state', undefined, f.token);
  assert.equal(publicState.status, 200);
  assert.deepEqual(Object.keys(publicState.data).sort(), ['binding','deck','live']);
  await f.remote.stop();
  const again = new RemoteControl({ identity: identity(f.directory), state: () => f.state, key: async () => {}, advertise: () => () => {} });
  await again.start({ host: '127.0.0.1' }); t.after(() => again.stop());
  assert.equal(again.snapshot().code, '');
  assert.equal((await request(again, '/state', undefined, f.token)).status, 200);
});

test('both directions share one live page; duplicate command IDs do not double-advance', async t => {
  const f = await fixture(t), id = crypto.randomUUID();
  const command = { key: 'Right', id, binding: f.binding };
  const replies = await Promise.all([request(f.remote, '/key', command, f.token), request(f.remote, '/key', command, f.token)]);
  assert.ok(replies.every(r => r.status === 200)); assert.equal(f.calls.length, 1);
  assert.equal((await request(f.remote, '/key', { ...command, key: 'Left' }, f.token)).status, 409);
  f.state.active = null; f.remote.observe(null);
  assert.equal((await request(f.remote, '/key', { ...command, id: crypto.randomUUID() }, f.token)).status, 409);
});

test('stale deck changes and lost authorization are checked again before key delivery', async t => {
  let release, valid;
  const f = await fixture(t, { key: async (_key, _active, check) => { valid = check; await new Promise(resolve => { release = resolve; }); } });
  const pending = request(f.remote, '/key', { key: 'Right', id: crypto.randomUUID(), binding: f.binding }, f.token);
  while (!release) await new Promise(resolve => setTimeout(resolve, 5));
  f.state.active = 2; f.remote.observe(2);
  assert.equal(valid(), false); release(); await pending;
  await f.remote.stop(); assert.equal(valid(), false);
});

test('only paired slide keys are accepted, never Meet reports or workflow commands', async t => {
  const f = await fixture(t);
  assert.equal((await request(f.remote, '/state')).status, 401);
  for (const route of ['/begin', '/end', '/record', '/open', '/report']) {
    assert.equal((await request(f.remote, route, {}, f.token)).status, 404);
  }
  assert.equal((await request(f.remote, '/key', { key: 'Escape', id: crypto.randomUUID(), binding: f.binding }, f.token)).status, 400);
  assert.equal((await request(f.remote, '/state', undefined, f.token, { Origin: 'https://example.com' })).status, 403);
  assert.equal((await request(f.remote, '/key', { giant: 'x'.repeat(5000) }, f.token)).status, 413);
  assert.equal(localPeer('8.8.8.8'), false); assert.equal(localPeer('192.168.1.2'), true);
});

test('pairing code is single use, expires, and is rate limited', async t => {
  const f = await fixture(t);
  assert.equal((await request(f.remote, '/pair', { code: '111111' })).status, 401);
  let clock = 100000;
  await f.remote.stop(); f.remote.peerHash = null; f.remote.now = () => clock;
  await f.remote.start({ host: '127.0.0.1' });
  const code = f.remote.snapshot().code;
  clock += 301000;
  assert.equal((await request(f.remote, '/pair', { code })).status, 401);
  for (let i = 0; i < 4; i++) await request(f.remote, '/pair', { code: 'wrong' });
  assert.equal((await request(f.remote, '/pair', { code })).status, 429);
});

module.exports = { request };

test('video signaling requires the existing pair, rejects browser origins and stays isolated from slide commands', async t => {
  const calls = [];
  const video = { reset() {}, request(data) { calls.push(data); return { sessionId: 'fixture', status: 'starting' }; } };
  const f = await fixture(t, { video });
  assert.equal((await request(f.remote, '/video', { op: 'start' })).status, 401);
  assert.equal((await request(f.remote, '/video', { op: 'start' }, f.token, { Origin: 'https://example.com' })).status, 403);
  assert.equal(calls.length, 0);
  assert.equal((await request(f.remote, '/video', { op: 'start' }, f.token)).status, 200);
  assert.equal((await request(f.remote, '/state', undefined, f.token)).data.capabilities[0], 'ppt-video-v1');
  assert.equal((await request(f.remote, '/video', { op: 'answer', sdp: 'x'.repeat(280000) }, f.token)).status, 413);
  assert.equal((await request(f.remote, '/key', { key: 'Right', id: crypto.randomUUID(), binding: f.binding }, f.token)).status, 200);
  assert.equal(f.calls.length, 1);
});

test('Mac and iPad pairings survive app upgrade/restart without replacing one another', async t => {
  const f=await fixture(t);
  const first=f.token, originalID=f.remote.identity.id;
  f.remote.openPairing();
  assert.equal((await request(f.remote,'/state',undefined,first)).status,200);
  const second=await request(f.remote,'/pair',{code:f.remote.snapshot().code});
  assert.equal(second.status,200);
  assert.equal((await request(f.remote,'/state',undefined,first)).status,200);
  assert.equal((await request(f.remote,'/state',undefined,second.data.token)).status,200);
  const saved=fs.readFileSync(path.join(f.directory,'remote-peer.json'),'utf8');
  assert.ok(!saved.includes(first)&&!saved.includes(second.data.token));
  assert.equal(JSON.parse(saved).peerHashes.length,2);
  await f.remote.stop();
  const again=new RemoteControl({identity:identity(f.directory),state:()=>f.state,key:async()=>{},advertise:()=>()=>{}});
  await again.start({host:'127.0.0.1'});t.after(()=>again.stop());
  assert.equal(again.identity.id,originalID);
  assert.equal(again.snapshot().rememberedCount,2);
  for(const token of [first,second.data.token])assert.equal((await request(again,'/state',undefined,token)).status,200);
  await again.forget();
  for(const token of [first,second.data.token])assert.equal((await request(again,'/state',undefined,token)).status,401);
});

test('upgrading legacy single-peer storage preserves the original approved token', async t => {
  const f=await fixture(t);await f.remote.stop();
  const pathName=path.join(f.directory,'remote-peer.json');
  const legacy={peerHash:JSON.parse(fs.readFileSync(pathName)).peerHash};
  fs.writeFileSync(pathName,JSON.stringify(legacy));
  const nextIdentity=identity(f.directory);assert.deepEqual(nextIdentity.peerHashes,[legacy.peerHash]);
  nextIdentity.remember('a'.repeat(64));
  assert.deepEqual(JSON.parse(fs.readFileSync(pathName)).peerHashes,[legacy.peerHash,'a'.repeat(64)]);
});

test('additional pairing remains single-use and expires without revoking an existing device', async t=>{
  let clock=100000;const f=await fixture(t,{now:()=>clock});
  f.remote.openPairing();const code=f.remote.snapshot().code;clock+=301000;
  assert.equal((await request(f.remote,'/pair',{code})).status,401);
  assert.equal((await request(f.remote,'/state',undefined,f.token)).status,200);
  const invalid=path.join(f.directory,'remote-peer.json');
  fs.writeFileSync(invalid,JSON.stringify({peerHashes:['not-a-hash']}));
  assert.throws(()=>identity(f.directory),/配對資料無效/);
});
