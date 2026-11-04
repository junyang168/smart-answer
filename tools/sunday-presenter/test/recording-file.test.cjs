const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { RecordingFile } = require('../recording-file.cjs');
const raw = value => Uint8Array.from(Buffer.from(value)).buffer;
async function fixture(t) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'sunday-recording-'));
  t.after(() => fs.rm(dir, { recursive: true, force: true })); return path.join(dir, 'test.mp4');
}
test('recording writes incrementally in order and publishes only after successful flush', async t => {
  const target = await fixture(t), file = new RecordingFile(target); await file.open();
  await assert.rejects(fs.access(target));
  await file.append(0, raw('first')); await file.append(1, raw('second'));
  assert.equal(await fs.readFile(file.partial, 'utf8'), 'firstsecond');
  await file.finish(); assert.equal(await fs.readFile(target, 'utf8'), 'firstsecond');
  await assert.rejects(fs.access(file.partial));
});
test('existing recording is never overwritten, including a collision at finalization', async t => {
  const target = await fixture(t); await fs.writeFile(target, 'existing');
  await assert.rejects(new RecordingFile(target).open(), /已存在/);
  await fs.unlink(target);
  const file = new RecordingFile(target); await file.open(); await file.append(0, raw('new'));
  await fs.writeFile(target, 'other'); await assert.rejects(file.finish());
  assert.equal(await fs.readFile(target, 'utf8'), 'other');
  assert.equal(await fs.readFile(file.partial, 'utf8'), 'new');
});
test('bad sequence, oversize, empty or nonbinary chunks are rejected', async t => {
  const file = new RecordingFile(await fixture(t)); await file.open();
  await assert.rejects(file.append(1, raw('x')));
  await assert.rejects(file.append(0, new ArrayBuffer(0)));
  await assert.rejects(file.append(0, new ArrayBuffer(16 * 1024 * 1024 + 1)));
  await assert.rejects(file.append(0, 'not binary'));
  await file.append(0, raw('valid')); await file.finish();
});
test('write failure preserves the partial file and never claims a saved MP4', async t => {
  const target = await fixture(t), file = new RecordingFile(target); await file.open();
  await file.append(0, raw('header'));
  const handle = file.handle;
  file.handle = { write: async () => { throw Object.assign(Error('disk full'), { code: 'ENOSPC' }); }, close: () => handle.close() };
  await assert.rejects(file.append(1, raw('body')), /disk full/);
  await assert.rejects(file.finish(), /disk full/);
  await assert.rejects(fs.access(target));
  assert.equal(await fs.readFile(file.partial, 'utf8'), 'header');
});
test('encoder failure keeps data under partial name instead of publishing success', async t => {
  const target = await fixture(t), file = new RecordingFile(target); await file.open();
  await file.append(0, raw('part')); await assert.rejects(file.finish('encoder failed'), /encoder failed/);
  await assert.rejects(fs.access(target)); assert.equal(await fs.readFile(file.partial, 'utf8'), 'part');
});
