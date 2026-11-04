const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { createRequire } = require('node:module');
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { resolve, promise }; };
const until = async check => { for (let i = 0; i < 200; i++) { if (check()) return; await new Promise(r => setTimeout(r, 5)); } throw Error('fixture timeout'); };
function fixture() {
  const opening = deferred(), handlers = new Map(); let ready = true;
  const ipcMain = new EventEmitter(); ipcMain.handle = (key, fn) => handlers.set(key, fn); ipcMain.removeHandler = key => handlers.delete(key);
  const expressions = [];
  class File {
    constructor(target) { this.target = target; this.partial = target + '.partial'; this.bytes = 0; }
    async open() { await opening.promise; this.created = true; }
    async finish(failure) { this.closed = true; if (failure || !this.bytes) throw Error(failure || 'empty'); return { path: this.target, bytes: this.bytes }; }
  }
  const engine = { url: 'file:///trusted', open: async () => {}, abortMedia() { this.aborted = true; },
    window: { isDestroyed: () => false, webContents: { executeJavaScript: async expression => {
      expressions.push(expression);
      if (expression.includes('.start(') && engine.startupError) return { startupError: engine.startupError };
      if (expression.includes('.start(')) return { microphone:'fixture', mimeType:'video/mp4', audio:{},video:{} };
      return {};
    } } } };
  const module = { exports: {} }, source = path.join(__dirname, '../local-recording.cjs'), realRequire = createRequire(source);
  vm.runInNewContext(fs.readFileSync(source, 'utf8'), { module, require: name => name === 'electron' ?
    { ipcMain, systemPreferences: { askForMediaAccess: async () => true }, shell: {} } :
    name === './recording-file.cjs' ? { RecordingFile: File } : realRequire(name), process, setTimeout, clearTimeout });
  let recorder;
  recorder = new module.exports.LocalRecording({ engine, ready: () => ready, changed: () => recorder?.observe() });
  recorder.state.devices = [{ id:'selected', label:'fixture' }];
  return { recorder, opening, handlers, expressions, engine, setReady: value => { ready = value; recorder.observe(); },
    start: () => recorder.start({ target:path.join(os.tmpdir(), 'unused-recording-lifecycle.mp4'), deviceId:'selected' }) };
}
test('stop during file opening prevents microphone startup and drains cleanup exactly once', async () => {
  const f = fixture(); const start = f.start(); start.catch(() => {});
  await until(() => !!f.recorder.run);
  const stopped = f.recorder.stop('cancel startup', true); f.opening.resolve();
  await assert.rejects(start, /取消/); await stopped;
  assert.equal(f.expressions.some(s => s.includes('.start(')), false);
  assert.equal(f.recorder.run, null); assert.equal(f.recorder.state.status, 'error');
  await f.recorder.close();
});
test('source invalidation during startup safely handles reentrant state notifications', async () => {
  const f = fixture(); const start = f.start(); start.catch(() => {});
  await until(() => !!f.recorder.run); f.setReady(false); f.opening.resolve();
  await assert.rejects(start, /取消/); await until(() => f.recorder.run === null);
  assert.equal(f.expressions.some(s => s.includes('.start(')), false);
  await f.recorder.close();
});
test('recording intent excludes concurrent microphone refresh or another start', async () => {
  const f = fixture(); const start = f.start(); start.catch(() => {});
  await assert.rejects(f.recorder.devices(), /停止/);
  await assert.rejects(f.start(), /停止/);
  f.opening.resolve(); await start;
  await f.recorder.stop(); await f.recorder.close();
});
test('Office/untrusted frames cannot send recording bytes even during an active recording', async () => {
  const f = fixture(); f.opening.resolve(); await f.start();
  await assert.rejects(f.handlers.get('recording:chunk')({sender:{},senderFrame:{}}, f.recorder.run.id, 0, new ArrayBuffer(4)), /Untrusted/);
  await f.recorder.stop(); await f.recorder.close();
});

test('permission/capture startup errors retain the original message through cleanup', async () => {
  const f = fixture(); f.engine.startupError = 'NotReadableError: permission denied'; f.opening.resolve();
  await assert.rejects(f.start(), /NotReadableError: permission denied/);
  assert.equal(f.recorder.state.error, 'NotReadableError: permission denied');
  assert.equal(f.recorder.state.status, 'error');
  await f.recorder.close();
});
