const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function fixture() {
  let resolve; const pending = new Promise(r => { resolve = r; }); let requests = 0, stops = 0;
  const track = { addEventListener() {}, stop() { stops++; } };
  const stream = { getTracks: () => [track], getVideoTracks: () => [track] };
  const window = { videoBridge: { onStop() {} }, addEventListener() {}, dispatchEvent() {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../media/host.js'), 'utf8'), {
    window, navigator: { mediaDevices: { getDisplayMedia() { requests++; return pending; } } }
  });
  return { capture: window.outputCapture, stream, finish: () => resolve(stream), get requests() { return requests; }, get stops() { return stops; } };
}
test('recording and Remote share one capture; stopping either branch leaves the other live', async () => {
  for (const first of ['remote', 'recording']) {
    const f = fixture(), a = f.capture.acquire('recording'), b = f.capture.acquire('remote'); f.finish();
    assert.equal(await a, await b); assert.equal(f.requests, 1);
    f.capture.release(first); assert.equal(f.stops, 0);
    f.capture.release(first === 'remote' ? 'recording' : 'remote'); assert.equal(f.stops, 1);
  }
});
test('late capture completion cannot reopen a canceled recording or stop the remaining viewer', async () => {
  const f = fixture(), canceled = f.capture.acquire('recording'); canceled.catch(() => {});
  f.capture.release('recording'); const remote = f.capture.acquire('remote'); f.finish();
  await assert.rejects(canceled, /取消/); assert.equal(await remote, f.stream); assert.equal(f.stops, 0);
  f.capture.release('remote'); assert.equal(f.stops, 1);
});
