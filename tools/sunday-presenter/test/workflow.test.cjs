const test = require('node:test');
const assert = require('node:assert/strict');
const { WorshipFlow } = require('../workflow.cjs');

function fixture() {
  const stage = { active: null, selected: { worship: 1, sermon: 2 },
    pages: [{ id: 1, ready: true, loaded: true }, { id: 2, ready: true, loaded: true }],
    snapshot() { return this; }, take(deck) { this.active = this.selected[deck]; }, black() { this.active = null; } };
  const calls = [];
  const helper = { async run(action) { calls.push(action); return { joined: true, microphoneMuted: true, sharing: true, recording: action === 'begin' }; } };
  const remote = { start: async () => {}, stop: async () => {}, snapshot: () => ({ paired: true }) };
  const flow = new WorshipFlow({ stage, helper, remote, website: { refresh: async () => {} }, meeting: () => 'https://meet.google.com/abc-mnop-xyz' });
  return { flow, stage, helper, calls };
}
function report(flow, microphoneMuted = true) { flow.peerReport({ runId: flow.runId, joined: true, speakerMuted: true, microphoneMuted, microphoneVerified: true }); }

test('only verified preparation can begin; recorder is confirmed before speaker mic is enabled', async () => {
  const { flow, stage, calls } = fixture();
  await assert.rejects(flow.begin());
  await flow.prepare(); assert.equal(flow.phase, 'preparing');
  report(flow); assert.equal(flow.phase, 'ready');
  await flow.begin(); assert.equal(flow.phase, 'live'); assert.equal(stage.active, 1);
  assert.deepEqual(calls, ['prepare', 'begin']);
  assert.equal(flow.snapshot().speakerLive, false);
  report(flow, false); assert.equal(flow.snapshot().speakerLive, true);
  await assert.rejects(flow.prepare(), /結束/);
});
test('no recording claim or live microphone transition on failed/unknown recorder state', async () => {
  const { flow, helper } = fixture(); await flow.prepare(); report(flow);
  helper.run = async () => ({ microphoneMuted: true, sharing: true });
  await assert.rejects(flow.begin(), /錄影/);
  assert.equal(flow.phase, 'error'); assert.equal(flow.recording, false);
  await assert.rejects(flow.prepare(), /結束/);
  await assert.rejects(flow.confirmShare(), /結束/);
});
test('home recording check never enters live, and unknown stop requires cleanup', async () => {
  const { flow, helper, stage } = fixture(); await flow.prepare(); report(flow);
  helper.run = async action => ({ joined: true, microphoneMuted: true, sharing: true,
    recording: false, recordingTestPassed: action === 'test-recording' });
  await flow.testRecording();
  assert.equal(flow.phase, 'ready'); assert.equal(stage.active, null); assert.equal(flow.recordAttempted, false);
  helper.run = async () => ({ microphoneMuted: true, recordingTestPassed: true });
  await assert.rejects(flow.testRecording(), /尚未確認/);
  assert.notEqual(flow.phase, 'live'); assert.equal(flow.recordAttempted, true);
  await assert.rejects(flow.prepare(), /結束/);
});
test('stale reports, unprepared slides and open host mic block readiness', async () => {
  const { flow, stage } = fixture(); await flow.prepare();
  flow.peerReport({ runId: 'old-run', joined: true, speakerMuted: true, microphoneMuted: true });
  assert.equal(flow.phase, 'preparing');
  stage.pages[1].ready = false; report(flow); assert.equal(flow.phase, 'preparing');
  await assert.rejects(flow.begin());
  const other = fixture(); other.helper.run = async () => ({ joined: true, microphoneMuted: false, sharing: true });
  await assert.rejects(other.flow.prepare(), /麥克風/);
});
test('ending before recording never stops somebody else’s recording', async () => {
  const { flow, calls } = fixture(); await flow.prepare(); report(flow); await flow.end();
  assert.deepEqual(calls, ['prepare', 'leave']); assert.equal(flow.phase, 'ended');
});
test('a recording check refused before start never owns an existing recording', async () => {
  const { flow, helper, calls } = fixture(); await flow.prepare(); report(flow);
  const original = helper.run;
  helper.run = async action => {
    if (action === 'test-recording') throw Object.assign(Error('already recording'), { recordingNotStarted: true });
    return original(action);
  };
  await assert.rejects(flow.testRecording(), /already recording/);
  await flow.end(); assert.deepEqual(calls, ['prepare', 'leave']);
});
test('ending invalidates an in-flight preparation so late success cannot reopen a run', async () => {
  const { flow, helper } = fixture(); let release;
  helper.run = async action => action === 'prepare' ? new Promise(resolve => { release = resolve; }) : { microphoneMuted: true };
  const pending = flow.prepare(); while (!release) await new Promise(resolve => setTimeout(resolve, 1));
  await flow.end(); release({ joined: true, microphoneMuted: true, sharing: true }); await pending;
  assert.equal(flow.phase, 'ended');
});
