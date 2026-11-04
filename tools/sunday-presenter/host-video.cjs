const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { BrowserWindow, session, desktopCapturer } = require('electron');
const { LocalRecording } = require('./local-recording.cjs');
const { VideoSession } = require('./video-session.cjs');

class HostVideo {
  constructor({ output, ready, changed }) {
    this.output = output; this.ready = ready; this.url = pathToFileURL(path.join(__dirname, 'media/host.html')).href;
    const media = session.fromPartition('sunday-video');
    this.media = media;
    const trusted = wc => !!this.window && wc === this.window.webContents && wc.getURL() === this.url;
    const allow = (wc, permission, details) => trusted(wc) && (
      permission === 'display-capture' && !details?.mediaTypes?.includes('audio') &&
        (!!this.sessions.current || this.recording.captureAllowed) ||
      permission === 'media' && this.recording.microphoneAllowed &&
        (details?.mediaType === 'audio' || details?.mediaTypes?.length > 0 && details.mediaTypes.every(type => type === 'audio'))
    );
    media.setPermissionRequestHandler((wc, permission, callback, details) => callback(allow(wc, permission, details)));
    media.setPermissionCheckHandler((wc, permission, _origin, details) => allow(wc, permission, details));
    media.setDisplayMediaRequestHandler(async (request, callback) => {
      try {
        if (!this.window || request.frame !== this.window.webContents.mainFrame || request.frame.url !== this.url ||
            !request.videoRequested || request.audioRequested || !this.ready() || !(this.sessions.current || this.recording.captureAllowed)) { callback({}); return; }
        const requestedSession = this.sessions.current; const recordingId = this.recording.captureAllowed && this.recording.run.id;
        const id = output.getMediaSourceId();
        const sources = await desktopCapturer.getSources({ types: ['window'], thumbnailSize: { width: 0, height: 0 }, fetchWindowIcons: false });
        const source = sources.find(s => s.id === id);
        if (!source || !this.ready() || id !== output.getMediaSourceId() || !(requestedSession && this.sessions.current === requestedSession || recordingId && this.recording.captureAllowed && this.recording.run.id === recordingId)) { callback({}); return; }
        callback({ video: source });
      } catch { callback({}); }
    }, { useSystemPicker: false });
    this.recording = new LocalRecording({ engine: this, ready, changed });
    this.sessions = new VideoSession({ ready, driver: {
      offer: async id => { await this.open(); if (this.sessions.current?.id !== id) throw Error('Expired'); return this.window.webContents.executeJavaScript(`window.videoHost.offer(${JSON.stringify(id)})`, true); },
      answer: (id, sdp) => this.window.webContents.executeJavaScript(`window.videoHost.answer(${JSON.stringify(id)}, ${JSON.stringify(sdp)})`),
      stop: id => { if (this.window && !this.window.isDestroyed()) this.window.webContents.send('video:stop', id); }
    } });
  }
  async open() {
    if (this.loading) return this.loading;
    if (this.window && !this.window.isDestroyed()) return;
    this.window = new BrowserWindow({ show: false, width: 16, height: 16,
      webPreferences: { session: this.media, preload: path.join(__dirname, 'media/preload.cjs'),
        sandbox: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false } });
    this.window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    this.window.webContents.on('will-navigate', event => event.preventDefault());
    const owner = this.window;
    this.window.webContents.on('render-process-gone', () => {
      if (this.window !== owner) return;
      this.sessions.reset(); const old = this.window; this.window = null; this.loading = null;
      this.recording.rendererGone();
      if (old && !old.isDestroyed()) old.destroy();
    });
    this.loading = this.window.loadURL(this.url).finally(() => { this.loading = null; });
    return this.loading;
  }
  abortMedia() {
    this.sessions.reset(); const old = this.window; this.window = null; this.loading = null;
    if (old && !old.isDestroyed()) old.destroy();
  }
  observe() { if (!this.ready()) this.sessions.reset(); this.recording.observe(); }
  reset() { this.sessions.reset(); }
  request(data) { return this.sessions.request(data); }
  async stats() { return this.window && !this.window.isDestroyed() ? this.window.webContents.executeJavaScript('window.videoHost.stats()') : { state: 'stopped' }; }
  async halt() {
    this.sessions.reset();
    if (this.window && !this.window.isDestroyed()) {
      // Acknowledge stopping Blink capture before destroying its owner.
      await Promise.race([
        this.window.webContents.executeJavaScript('window.videoHost.stop(); true').catch(() => {}),
        new Promise(resolve => setTimeout(resolve, 2000))
      ]);
    }
  }
  async close() {
    await this.recording.close(); this.sessions.close(); await this.halt();
    if (this.window && !this.window.isDestroyed()) this.window.destroy();
  }
}
module.exports = { HostVideo };
