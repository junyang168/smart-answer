const { ipcMain, systemPreferences, shell, app } = require('electron');
const crypto = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');
const { RecordingFile } = require('./recording-file.cjs');
const bounded = (task, ms, message) => {
  let timer;
  return Promise.race([task, new Promise((_, reject) => { timer = setTimeout(() => reject(Object.assign(Error(message), { code: 'RECORDING_TIMEOUT' })), ms); })]).finally(() => clearTimeout(timer));
};
class LocalRecording {
  constructor({ engine, ready, changed = () => {} }) {
    this.engine = engine; this.ready = ready; this.changed = changed;
    this.state = { status: 'idle', devices: [], level: 0 }; this.intent = null;
    const trusted = event => this.trusted(event) && this.run && ['starting', 'recording', 'stopping'].includes(this.state.status);
    ipcMain.handle('recording:chunk', async (event, id, sequence, raw) => {
      if (!trusted(event) || id !== this.run.id) throw Error('Untrusted recording data');
      try { await this.run.file.append(sequence, raw); }
      catch (error) { this.stop(error.message, true).catch(() => {}); throw error; }
    });
    this.onFault = (event, id, message, graceful = false) => {
      if (!trusted(event) || id !== this.run.id) return;
      // Preserve only capture/encoder state, never audio samples or document URLs.
      const diagnostic = this.execute('window.recordingHost.stats()').catch(() => ({}));
      this.stop(String(message).slice(0, 300), graceful !== true).catch(() => {});
      diagnostic.then(stats => {
        if (!app) return;
        const entry = { at: new Date().toISOString(), reason: String(message).slice(0, 300), stats };
        fs.appendFile(path.join(app.getPath('userData'), 'recording-errors.jsonl'), JSON.stringify(entry) + '\n').catch(() => {});
      });
    };
    this.onLevel = (event, id, level) => {
      if (!trusted(event) || id !== this.run.id || !Number.isFinite(level) || level < 0 || level > 1) return;
      this.state.level = level; this.state.bytes = this.run.file.bytes; this.changed();
    };
    ipcMain.on('recording:fault', this.onFault); ipcMain.on('recording:level', this.onLevel);
  }
  trusted(event) {
    return this.engine.window && !this.engine.window.isDestroyed() && event.sender === this.engine.window.webContents &&
      event.senderFrame === event.sender.mainFrame && event.senderFrame.url === this.engine.url;
  }
  get captureAllowed() { return !!this.run && ['starting', 'recording'].includes(this.state.status); }
  get microphoneAllowed() { return this.intent === 'devices' || this.captureAllowed; }
  snapshot() { return { ...this.state }; }
  update(patch) { Object.assign(this.state, patch); this.changed(); }
  async execute(expression) {
    await this.engine.open();
    return this.engine.window.webContents.executeJavaScript(expression, true);
  }
  async permission() {
    if (process.platform === 'darwin' && !await systemPreferences.askForMediaAccess('microphone')) throw Error('請在系統設定允許 Sunday Presenter 使用麥克風。');
  }
  async devices() {
    if (this.run || this.intent) throw Error('請先停止錄影。');
    this.intent = 'devices';
    try {
      await this.permission();
      const devices = await bounded(this.execute('window.recordingHost.devices()'), 20000, '麥克風裝置讀取逾時。');
      this.update({ devices, error: '', status: 'idle' }); return devices;
    } finally { this.intent = null; }
  }
  async start(options) {
    if (this.run || this.intent) throw Error('錄影尚未停止。');
    this.intent = 'starting';
    try { return await this.begin(options); }
    finally { this.intent = null; }
  }
  async begin({ target, deviceId }) {
    if (!this.ready()) throw Error('請先播放已確認的 PPT，再開始錄影。');
    if (!this.state.devices.some(device => device.id === deviceId)) throw Error('請先選擇並更新麥克風裝置。');
    await this.permission();
    const free = await fs.statfs(path.dirname(target));
    if (free.bavail * free.bsize < 64 * 1024 * 1024) throw Error('錄影位置可用空間不足 64 MiB，請選擇其他磁碟。');
    // Re-check after permission/filesystem waits before opening any capture.
    if (this.run || !this.ready()) throw Error('PPT 狀態已變更，請重新開始錄影。');
    const run = { id: crypto.randomUUID(), file: new RecordingFile(target) };
    run.opening = run.file.open(); this.run = run;
    this.update({ status: 'starting', path: target, partialPath: '', error: '', startedAt: null, stoppedAt: null, stopReason: '', level: 0, bytes: 0 });
    try {
      await run.opening;
      if (this.run !== run || this.state.status !== 'starting' || !this.ready()) throw Error('錄影啟動已取消。');
      const result = await bounded(this.execute(`window.recordingHost.start(${JSON.stringify({ id: run.id, deviceId })}).catch(error => ({ startupError: (error.name || 'Error') + ': ' + (error.message || '請確認麥克風及螢幕錄影權限。') }))`), 25000, '錄影啟動逾時，請檢查麥克風及螢幕錄影權限。');
      if (result.startupError) throw Error(result.startupError);
      if (this.run !== run || this.state.status !== 'starting' || !this.ready()) throw Error('錄影啟動已取消。');
      this.update({ status: 'recording', startedAt: Date.now(), microphone: result.microphone, format: result.mimeType, audio: result.audio, video: result.video });
      return this.snapshot();
    } catch (error) {
      const message = error?.message || String(error) || '錄影擷取啟動失敗，請確認權限。';
      if (this.run === run) await this.stop(message, true);
      throw Error(message);
    }
  }
  stop(reason = '', failure = false) {
    const run = this.run;
    if (!run) return Promise.resolve(this.snapshot());
    if (failure) run.failure ||= reason || '影音擷取或寫入失敗。';
    if (run.stopping) return run.stopping;
    run.stopping = Promise.resolve().then(async () => {
      try {
        await run.opening.catch(() => {});
        const result = this.engine.window && !this.engine.window.isDestroyed() ?
          await bounded(this.execute('window.recordingHost.stop()'), 12000, '影音程序未完成收尾，已保留未完成的錄影檔。') : { error: '影音程序已停止。' };
        const saved = await run.file.finish(run.failure || result.error);
        this.update({ status: 'saved', ...saved, stoppedAt: Date.now(), error: '', partialPath: '', level: 0 });
      } catch (error) {
        if (error.code === 'RECORDING_TIMEOUT') this.engine.abortMedia();
        // Close the file even when the renderer disappears before stop completes.
        if (!run.file.closed) await run.file.finish(error.message).catch(() => {});
        this.update({ status: 'error', error: error.message, partialPath: run.file.created ? run.file.partial : '', stoppedAt: Date.now(), level: 0 });
      } finally { if (this.run === run) this.run = null; this.changed(); }
      return this.snapshot();
    });
    this.update({ status: 'stopping', level: 0, stopReason: reason });
    return run.stopping;
  }
  observe() {
    if (this.run && !this.ready()) this.stop('PPT 輸出已停止，錄影已自動儲存。').catch(() => {});
  }
  rendererGone() {
    if (this.run) this.stop('影音程序已停止，未完成檔案已保留。', true).catch(() => {});
  }
  reveal() {
    const target = this.state.status === 'saved' ? this.state.path : this.state.partialPath;
    if (target) shell.showItemInFolder(target);
  }
  async close() {
    await this.stop('程式結束，錄影已自動儲存。');
    ipcMain.removeHandler('recording:chunk'); ipcMain.removeListener('recording:fault', this.onFault); ipcMain.removeListener('recording:level', this.onLevel);
  }
}
module.exports = { LocalRecording };
