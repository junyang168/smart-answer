const { test } = require('node:test');
const assert = require('node:assert/strict');
const { Preparation, waitUntil } = require('../preparation.cjs');
function fixture(options = {}) {
  const pages = { sermon: { id: 2, ok: false }, worship: { id: 1, ok: false } }, steps = [];
  const driver = { folder: async () => ({ date: '2026-11-08', folderExists: true }), documents: async () => pages,
    active: () => null, ready: async page => page.ok,
    prepare: async page => { steps.push(page.id); page.ok = true; },
    present: async page => { steps.push('show:' + page.id); }, ...options };
  return { flow: new Preparation({ driver }), pages, steps, driver };
}
test('prepares sermon first, worship last, and presents only after both verify', async () => {
  const { flow, steps } = fixture(); await flow.start();
  assert.deepEqual(steps, [2, 1, 'show:1']); assert.equal(flow.snapshot().status, 'ready');
});
test('a retry preserves the successful other deck and shows a concrete error', async () => {
  const f = fixture(); let fail = true;
  f.driver.prepare = async page => { f.steps.push(page.id); if (page.id === 1 && fail) throw Error('敬拜尚在載入'); page.ok = true; };
  await f.flow.start(); assert.equal(f.flow.snapshot().status, 'blocked'); assert.equal(f.flow.snapshot().message, '敬拜尚在載入');
  assert.deepEqual(f.steps, [2, 1]); fail = false; await f.flow.start();
  assert.deepEqual(f.steps, [2, 1, 1, 'show:1']);
});
test('duplicate presses share one run, and repeated preparation keeps the active deck/page', async () => {
  let release; const pending = new Promise(resolve => { release = resolve; });
  const f = fixture({ folder: async () => { await pending; return { folderExists: true }; }, active: () => 2 });
  const first = f.flow.start(); assert.equal(f.flow.start(), first); release(); await first;
  await f.flow.start(); assert.deepEqual(f.steps, [2, 1, 'show:2', 'show:2']);
});
test('a changed fullscreen state cannot be labelled ready or presented', async () => {
  const f = fixture(); let checks = 0;
  f.driver.ready = async page => { checks++; return checks === 5 ? false : page.ok; };
  await f.flow.start(); assert.equal(f.flow.snapshot().status, 'blocked'); assert.ok(!f.steps.some(s => String(s).startsWith('show:')));
});
test('cancel stops late continuation after a slow load without presenting', async () => {
  let release; const pending = new Promise(resolve => { release = resolve; });
  const f = fixture({ documents: async () => { await pending; return f.pages; } });
  const run = f.flow.start(); await Promise.resolve(); f.flow.cancel(); release(); await run;
  assert.deepEqual(f.steps, []);
});
test('condition waiting resolves on actual readiness, times out and aborts cleanly', async () => {
  let ready = false; setTimeout(() => { ready = true; }, 5);
  assert.equal(await waitUntil(() => ready, { timeoutMs: 100, intervalMs: 1, message: 'slow' }), true);
  await assert.rejects(waitUntil(() => false, { timeoutMs: 3, intervalMs: 1, message: 'not ready' }), /not ready/);
  const abort = new AbortController(); const run = waitUntil(() => false, { signal: abort.signal, timeoutMs: 100, message: 'slow' }); abort.abort(); await assert.rejects(run);
});

test('a hung readiness probe still has an overall timeout', async () => {
  await assert.rejects(waitUntil(() => new Promise(() => {}), { timeoutMs: 5, message: 'hung probe' }), /hung probe/);
});
test('later navigation revokes the completed status instead of leaving a green indicator', async () => {
  const f=fixture(); f.driver.intact=()=>Object.values(f.pages).every(p=>p.ok);
  await f.flow.start(); f.pages.sermon.ok=false; f.flow.observe(); assert.equal(f.flow.snapshot().status,'blocked');
});
