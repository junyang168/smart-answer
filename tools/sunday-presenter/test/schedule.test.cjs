const test = require('node:test');
const assert = require('node:assert/strict');
const { SundaySchedule } = require('../schedule.cjs');
function fixture(at, extra = {}) {
  let date = new Date(at), calls = 0, saved = '';
  const flow = { phase: 'ready', snapshot() { return { phase: this.phase, hostReady: true, peerReady: true, slidesReady: true }; },
    async begin() { calls++; this.phase = 'live'; } };
  const schedule = new SundaySchedule({ flow, now: () => date, save: d => { saved = d; }, ...extra });
  return { schedule, flow, set: at => { date = new Date(at); }, get calls() { return calls; }, get saved() { return saved; } };
}
test('10am follows Dallas daylight-saving time, never the machine time zone', async () => {
  for (const [before, due] of [['2026-11-08T15:59:59Z', '2026-11-08T16:00:00Z'],
    ['2026-07-05T14:59:59Z', '2026-07-05T15:00:00Z'],
    ['2026-11-01T15:59:59Z', '2026-11-01T16:00:00Z']]) {
    const f = fixture(before); await f.schedule.tick(); assert.equal(f.calls, 0);
    f.set(due); await f.schedule.tick(); assert.equal(f.calls, 1);
    await f.schedule.tick(); assert.equal(f.calls, 1);
    assert.match(f.schedule.message, /已自動/);
  }
});
test('not-ready, recording failure and host restarts never retry an uncertain start', async () => {
  const f = fixture('2026-11-08T16:00:00Z'); f.flow.phase = 'preparing';
  await f.schedule.tick(); assert.equal(f.calls, 0); assert.match(f.schedule.message, /未能自動/);
  f.flow.phase = 'ready'; await f.schedule.tick(); assert.equal(f.calls, 0);
  const restarted = fixture('2026-11-08T16:00:30Z', { handledDate: f.saved });
  await restarted.schedule.tick(); assert.equal(restarted.calls, 0);
  const failing = fixture('2026-11-08T16:00:00Z'); let attempts = 0;
  failing.flow.begin = async () => { attempts++; throw Error('unknown recorder state'); };
  await failing.schedule.tick(); await failing.schedule.tick();
  assert.equal(attempts, 1); assert.match(failing.schedule.message, /unknown recorder/);
});
test('only Sunday is scheduled; late launch or waking after ten needs a manual start', async () => {
  const f = fixture('2026-11-02T16:00:00Z'); await f.schedule.tick(); assert.equal(f.calls, 0);
  f.set('2026-11-08T16:02:00Z'); await f.schedule.tick(); assert.equal(f.calls, 0); assert.match(f.schedule.message, /錯過/);
});
test('operator cancellation persists for the coming Sunday and expires the next week', async () => {
  const f = fixture('2026-11-02T16:00:00Z'); f.schedule.cancel(); assert.equal(f.saved, '2026-11-08');
  f.set('2026-11-08T16:00:00Z'); await f.schedule.tick(); assert.equal(f.calls, 0);
  f.set('2026-11-15T16:00:00Z'); await f.schedule.tick(); assert.equal(f.calls, 1);
});
test('manual start and ending are never restarted by the timer', async () => {
  for (const phase of ['starting', 'live', 'ending', 'ended']) {
    const f = fixture('2026-11-08T16:00:00Z'); f.flow.phase = phase;
    await f.schedule.tick(); assert.equal(f.calls, 0);
  }
});
test('a failed durable reservation prevents automatic start', async () => {
  const f = fixture('2026-11-08T16:00:00Z', { save: () => { throw Error('disk full'); } });
  await assert.rejects(f.schedule.tick(), /disk full/); assert.equal(f.calls, 0);
});
