const test = require('node:test');
const assert = require('node:assert/strict');
const { VideoSession, validateSDP, localAddress } = require('../video-session.cjs');
const sdp = 'v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=fingerprint:sha-256 AA:BB\r\na=ice-ufrag:a\r\na=ice-pwd:abcdefghijklmnop\r\na=candidate:1 1 udp 123 192.168.1.2 54321 typ host\r\n';
const turn = () => new Promise(resolve => setImmediate(resolve));
test('signaling rejects audio, application data, public candidates and oversized descriptions', () => {
  assert.equal(validateSDP(sdp), sdp);
  for (const bad of [sdp + 'm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n', sdp + 'm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n', sdp.replace('192.168.1.2', '8.8.8.8'), sdp.replace('typ host', 'typ relay'), sdp.repeat(1000)]) assert.throws(() => validateSDP(bad));
  for (const a of ['192.168.2.3', '172.20.10.2', '::1', 'fe80::1', 'fd00::2', 'device-12.local']) assert.equal(localAddress(a), true, a);
  for (const a of ['example.com', '8.8.8.8', 'device.local.evil', '2001:4860::1']) assert.equal(localAddress(a), false, a);
});
test('lease expiry cancels pending capture and cannot resurrect a superseded session', async t => {
  let finish, clock = 0;
  const stopped = [];
  const service = new VideoSession({ now: () => clock, ready: () => true, driver: { offer: () => new Promise(resolve => { finish = resolve; }), stop: id => stopped.push(id) } });
  t.after(() => service.close());
  const first = service.request({ op: 'start', clientId: 'test-viewer-12345' });
  assert.equal(service.request({ op: 'start', clientId: 'test-viewer-12345' }).sessionId, first.sessionId);
  await turn(); clock = 16000; service.expire(); finish(sdp); await turn();
  assert.equal(service.current, null); assert.deepEqual(stopped, [first.sessionId]);
  assert.throws(() => service.request({ op: 'poll', sessionId: first.sessionId }));
});
test('switches preserve the session while source loss resets it; answer retries are idempotent', async t => {
  let ready = true, answers = 0;
  const service = new VideoSession({ ready: () => ready, driver: { offer: async () => sdp, answer: async () => { answers++; }, stop() {} } });
  t.after(() => service.close());
  const { sessionId } = service.request({ op: 'start', clientId: 'test-viewer-12345' }); await turn();
  assert.equal(service.request({ op: 'poll', sessionId }).status, 'offer');
  service.request({ op: 'answer', sessionId, sdp }); service.request({ op: 'answer', sessionId, sdp }); await turn();
  assert.equal(answers, 1); assert.equal(service.request({ op: 'poll', sessionId }).status, 'streaming');
  ready = false; service.expire(); assert.equal(service.current, null);
  assert.throws(() => service.request({ op: 'start', clientId: 'test-viewer-12345' }));
});

test('capture setup has a total deadline even while an authenticated viewer keeps polling', async t => {
  let clock = 0;
  const service = new VideoSession({ now: () => clock, ready: () => true, driver: { offer: () => new Promise(() => {}), stop() {} } });
  t.after(() => service.close());
  const { sessionId } = service.request({ op: 'start', clientId: 'test-viewer-12345' });
  for (clock = 5000; clock <= 30000; clock += 5000) service.request({ op: 'poll', sessionId });
  clock = 31000; service.expire(); assert.equal(service.current, null);
});
test('repeated viewer replacements are bounded without disabling slide control', t => {
  const service = new VideoSession({ ready: () => true, driver: { offer: async () => sdp, stop() {} } });
  t.after(() => service.close());
  for (let n = 0; n < 6; n++) service.request({ op: 'start', clientId: 'test-viewer-12345-' + n });
  assert.throws(() => service.request({ op: 'start', clientId: 'test-viewer-12345-7' }), /重試過快/);
});

test('a delayed viewer cannot reopen a closed capture service during app shutdown', async t => {
  let calls = 0;
  const service = new VideoSession({ ready: () => true, driver: { offer: async () => { calls++; return sdp; }, stop() {} } });
  t.after(() => service.close());
  service.request({ op: 'start', clientId: 'test-viewer-12345' }); service.close(); await turn();
  assert.throws(() => service.request({ op: 'start', clientId: 'test-viewer-12345' }), /stopped/);
  assert.equal(calls, 0);
});
