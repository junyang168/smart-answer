const path = require('node:path');
const { BrowserWindow, ipcMain, session } = require('electron');
const { MusicLibrary, musicResponse } = require('./music-library.cjs');

class Music {
  constructor({ file, changed }) {
    this.library = new MusicLibrary(file); this.changed = changed; this.token = 0; this.intent = 0;
    this.state = { status: 'stopped', position: 0, duration: 0, error: this.library.error };
    this.media = session.fromPartition('sunday-music');
    this.media.setPermissionRequestHandler((_wc,_permission, callback) => callback(false));
    this.media.setPermissionCheckHandler(() => false);
    this.media.protocol.handle('sunday-music', request => musicResponse(request, this.library));
    this.listener = (event, state) => {
      if (!this.window || this.window.isDestroyed() || event.sender !== this.window.webContents || event.senderFrame !== event.sender.mainFrame || state?.token !== this.token) return;
      if (!['loading','playing','paused','stopped','ended','error'].includes(state.status)) return;
      this.state = { status: state.status, position: Number.isFinite(state.position) ? Math.max(0,state.position) : 0,
        duration: Number.isFinite(state.duration) ? Math.max(0,state.duration) : 0,
        error: state.status === 'error' ? '無法播放這個 MP3，請確認檔案已下載且可正常播放。' : null };
      this.emit();
    };
    ipcMain.on('music:state', this.listener);
  }
  snapshot() { return { ...this.library.snapshot(), ...this.state, activeSlot: this.activeSlot || null }; }
  emit() { this.changed?.(this.snapshot()); }
  async open() {
    if (this.closed) throw Error('音樂播放器已關閉。');
    if (this.loading) return this.loading;
    if (this.window && !this.window.isDestroyed()) return;
    const window = this.window = new BrowserWindow({ show: false, width: 16, height: 16,
      webPreferences: { session: this.media, preload: path.join(__dirname,'music/preload.cjs'), sandbox: true,
        contextIsolation: true, nodeIntegration: false, backgroundThrottling: false } });
    window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    window.webContents.on('will-navigate', event => event.preventDefault());
    window.webContents.on('render-process-gone', () => {
      if (this.window !== window) return;
      this.token++; this.loaded = null; this.window = null; this.loading = null;
      this.state = { status:'error',position:0,duration:0,error:'音樂播放器已停止，請重新按播放。' }; this.emit();
      if (!window.isDestroyed()) window.destroy();
    });
    this.loading = window.loadFile(path.join(__dirname,'music/player.html')).finally(() => { this.loading = null; });
    return this.loading;
  }
  async call(action, args = {}) {
    await this.open();
    const window = this.window;
    let timer;
    try {
      return await Promise.race([
        window.webContents.executeJavaScript(`window.musicPlayer.command(${JSON.stringify(action)},${JSON.stringify(args)})`, true),
        new Promise((_, reject) => { timer = setTimeout(() => { if(this.window===window){this.token++;this.loaded=null;this.window=null;this.loading=null;if(!window.isDestroyed())window.destroy();this.state={status:'error',position:0,duration:0,error:'音樂播放器未回應，已停止播放；請重試。'};this.emit();} reject(Error('音樂播放器未回應，已停止播放；請重試。')); },10000); timer.unref(); })
      ]);
    } finally { clearTimeout(timer); }
  }
  async assign(slot, file) {
    if (!['hymn','responseHymn','doxology'].includes(slot)) throw Error('未知音樂項目。');
    if (this.activeSlot === slot) { await this.stop(); this.loaded = null; this.token++; this.state={status:'stopped',position:0,duration:0,error:null}; }
    this.library.assign(slot, file); this.emit();
  }
  async toggle(slot) {
    const id = this.library.slots[slot]; this.library.get(id);
    if (this.activeSlot === slot && this.state.status === 'playing') return this.pause();
    const intent = ++this.intent;
    if (this.activeSlot !== slot) { if(this.loaded) await this.call('stop',{token:this.token}); this.loaded = null; }
    if(intent!==this.intent)return;
    this.activeSlot = slot;
    await this.select(id);
    if(intent!==this.intent)return;
    return this.play(intent);
  }
  async select(id) {
    this.library.get(id);
    if (this.loaded === id && this.state.status !== 'error') return;
    this.library.selected = id; this.library.save(); this.loaded = id;
    const token = ++this.token;
    this.state = { status:'loading',position:0,duration:0,error:null }; this.emit();
    try { await this.call('load', { url:`sunday-music://track/${id}`,token,volume:this.library.volume }); }
    catch (error) { if (token === this.token) { this.loaded=null; this.state.status='error';this.state.error=error.message;this.emit(); } throw error; }
  }
  async play(intent = this.intent) {
    const id = this.library.selected; this.library.get(id);
    if (this.loaded !== id || this.state.status === 'error') await this.select(id);
    if(intent!==this.intent)return;
    await this.call('play', { token:this.token });
  }
  async pause() { this.intent++; if (this.loaded) await this.call('pause', { token:this.token }); }
  async stop() { this.intent++; if (this.loaded) await this.call('stop', { token:this.token }); }
  async seek(position) {
    if (!this.loaded || !Number.isFinite(position) || position < 0 || position > this.state.duration) throw Error('請選擇有效播放位置。');
    await this.call('seek', { token:this.token,position });
  }
  async volume(value) {
    if (!Number.isFinite(value) || value < 0 || value > 1) throw Error('音量必須在 0–100% 之間。');
    this.library.volume=value;this.library.save();this.emit();
    if (this.loaded) await this.call('volume', { token:this.token,volume:value });
  }
  async remove() {
    const id=this.library.selected;if (!id) return;
    await this.stop();this.token++;this.loaded=null;
    this.library.tracks=this.library.tracks.filter(t=>t.id!==id);this.library.selected=this.library.tracks[0]?.id||null;this.library.save();
    this.state={status:'stopped',position:0,duration:0,error:null};this.emit();
  }
  async close() {
    this.closed=true;this.intent++;
    this.media.protocol.unhandle('sunday-music');
    this.token++;ipcMain.removeListener('music:state',this.listener);
    if (this.window && !this.window.isDestroyed()) this.window.destroy();
    this.window=null;
  }
}
module.exports = { Music };
