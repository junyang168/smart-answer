const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function viewer() {
  let now = 0, tick, pc;
  const timers = new Map(), messages = [], classes = new Set();
  const video = { paused: false, videoWidth: 1280, videoHeight: 720, play() { this.paused = false; return Promise.resolve(); } };
  class Peer {
    constructor() { pc = this; this.frames = 1; this.connectionState = 'connected'; this.iceGatheringState = 'complete'; }
    close() { this.closed = true; }
    async setRemoteDescription() {}
    getTransceivers() { return [{ receiver: { track: { kind: 'video' } } }]; }
    async createAnswer() { return { type: 'answer', sdp: 'fixture' }; }
    async setLocalDescription(answer) { this.localDescription = answer; }
    async getStats() { return new Map([['v', { type: 'inbound-rtp', kind: 'video', framesDecoded: this.frames }]]); }
    getReceivers() { return [{ track: { kind: 'video' } }]; }
  }
  const context = { RTCPeerConnection: Peer, MediaStream: class {}, performance: { now: () => now },
    document: { querySelector: selector => selector === 'video' ? video : {}, body: { classList: { add: c => classes.add(c), remove: c => classes.delete(c) } } },
    window: { webkit: { messageHandlers: { video: { postMessage: m => messages.push(m) } } } },
    setInterval: callback => { tick = callback; },
    setTimeout: (callback, ms) => { const id = {}; timers.set(id, { callback, at: now + ms }); return id; },
    clearTimeout: id => timers.delete(id) };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../media/remote.js'), 'utf8'), context);
  return { video, messages, classes, api: context.window.remoteVideo, get pc() { return pc; },
    async advance(ms) { now += ms; for (const [id, timer] of timers) if (timer.at <= now) { timers.delete(id); timer.callback(); } await tick(); } };
}
const start = v => v.api.apply({ sessionId: 'one', offer: { type: 'offer', sdp: 'fixture' } });
test('unchanged slides remain connected without clicks; paused playback resumes automatically', async () => {
  const v = viewer(); await start(v); await v.advance(1000);
  v.video.srcObject = {}; v.video.paused = true;
  await v.advance(180000);
  assert.equal(v.pc.closed, undefined);
  assert.ok(v.classes.has('receiving'));
  assert.equal(v.video.paused, false);
  assert.equal(v.messages.filter(m => m.op === 'failed').length, 0);
});
test('short ICE interruption recovers in the existing session', async () => {
  const v = viewer(); await start(v); await v.advance(1000);
  v.pc.connectionState = 'disconnected'; v.pc.onconnectionstatechange(); await v.advance(2000);
  v.pc.connectionState = 'connected'; v.pc.onconnectionstatechange(); await v.advance(10000);
  assert.equal(v.pc.closed, undefined);
  assert.equal(v.messages.filter(m => m.op === 'failed').length, 0);
});
test('persistent ICE interruption or failure automatically requests reconnect', async () => {
  for (const state of ['disconnected', 'failed']) {
    const v = viewer(); await start(v); v.pc.connectionState = state; v.pc.onconnectionstatechange(); await v.advance(5001);
    assert.ok(v.pc.closed); assert.equal(v.messages.filter(m => m.op === 'failed').length, 1);
  }
});
test('connection that never receives its first frame is retried', async () => {
  const v = viewer(); await start(v); v.pc.frames = 0; await v.advance(26000);
  assert.ok(v.pc.closed); assert.equal(v.messages.filter(m => m.op === 'failed').length, 1);
});
