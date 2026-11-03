const crypto = require('node:crypto');

class WorshipFlow {
  constructor({ website, helper, remote, stage, meeting, changed = () => {} }) {
    Object.assign(this, { website, helper, remote, stage, meeting, changed });
    this.phase = 'idle'; this.runId = ''; this.meetingUrl = ''; this.error = ''; this.hostReady = false;
    this.recording = false; this.peer = null;
    this.epoch = 0; this.recordAttempted = false;
  }
  info() { return { phase: this.phase, runId: this.runId, meetingUrl: this.meetingUrl }; }
  peerReport(value) {
    if (value.runId !== this.runId) return;
    this.peer = value; this.reconcile(); this.changed();
  }
  peerReady() { return this.remote.snapshot().paired && this.peer?.joined === true && this.peer?.speakerMuted === true && this.peer?.microphoneVerified === true && this.peer?.microphoneMuted === true; }
  slidesReady() {
    const state = this.stage.snapshot();
    return ['worship', 'sermon'].every(deck => state.pages.some(page => page.id === state.selected[deck] && page.ready && page.loaded && !page.error));
  }
  reconcile() {
    if (['preparing', 'ready'].includes(this.phase)) {
      this.phase = this.hostReady && this.peerReady() && this.slidesReady() ? 'ready' : 'preparing';
    }
  }
  snapshot() {
    this.reconcile();
    return { ...this.info(), error: this.error, recording: this.recording,
      hostReady: this.hostReady, peerReady: this.peerReady(), slidesReady: this.slidesReady(),
      speakerLive: this.remote.snapshot().paired && this.peer?.microphoneVerified === true && this.peer?.microphoneMuted === false && this.peer?.speakerMuted === true,
      peerError: this.peer?.error || '' };
  }
  async prepare() {
    if (this.recordAttempted || ['live', 'starting', 'ending', 'testing'].includes(this.phase)) throw Error('請先結束目前的崇拜。');
    this.phase = 'preparing'; this.runId = crypto.randomUUID(); this.error = ''; this.hostReady = false; this.peer = null;
    const epoch = ++this.epoch;
    this.changed();
    try {
      await this.website.refresh();
      if (epoch !== this.epoch) return;
      const meeting = this.meeting();
      if (!/^https:\/\/meet\.google\.com\/[a-z]{3}-[a-z]{4}-[a-z]{3}$/.test(meeting)) throw Error('請在首次設定填入主日 Meet 連結。');
      this.meetingUrl = meeting;
      await this.remote.start(); this.changed();
      if (epoch !== this.epoch) return;
      const state = await this.helper.run('prepare', this.meetingUrl);
      if (epoch !== this.epoch) return;
      if (!state.joined || !state.microphoneMuted) throw Error('主電腦尚未確認加入 Meet 並關閉麥克風。');
      if (!state.sharing) throw Error('請在 Meet 選擇分享 Sunday Presenter — OUTPUT 視窗，再按準備主日確認。');
      this.hostReady = true; this.reconcile(); this.changed();
    } catch (error) { if (epoch !== this.epoch) return; this.error = error.message; this.phase = 'error'; this.changed(); throw error; }
  }
  async begin() {
    this.reconcile();
    if (this.phase !== 'ready') throw Error('請先完成準備主日，檢查 Meet、講員連線和兩份簡報。');
    this.phase = 'starting'; this.error = ''; this.changed();
    const epoch = ++this.epoch;
    try {
      this.recordAttempted = true;
      const state = await this.helper.run('begin', this.meetingUrl);
      if (epoch !== this.epoch) return;
      if (!state.microphoneMuted || !state.recording || !state.sharing) throw Error('尚未確認主電腦靜音、投影片分享及錄影已開始。');
      this.recording = true;
      this.stage.take('worship');
      this.phase = 'live'; this.changed();
    } catch (error) { if (epoch !== this.epoch) return; this.error = error.message; this.phase = 'error'; this.changed(); throw error; }
  }
  async testRecording() {
    if (!this.meetingUrl || this.recordAttempted || ['live', 'starting', 'ending', 'testing'].includes(this.phase)) throw Error('請先準備主日；測試前須結束既有錄影。');
    const epoch = ++this.epoch;
    this.phase = 'testing'; this.recordAttempted = true; this.error = ''; this.changed();
    try {
      const state = await this.helper.run('test-recording', this.meetingUrl);
      if (epoch !== this.epoch) return;
      if (!state.recordingTestPassed || !state.microphoneMuted || state.recording !== false) throw Error('錄影測試尚未確認結束，請按結束並查看 Meet。');
      this.recordAttempted = false; this.recording = false; this.phase = 'preparing';
      this.error = '短錄影開始及停止測試通過；尚未開始崇拜。'; this.reconcile(); this.changed();
    } catch (error) { if (epoch !== this.epoch) return;
      if (error.recordingNotStarted === true) this.recordAttempted = false;
      this.error = error.message; this.phase = 'error'; this.changed(); throw error; }
  }
  async confirmShare() {
    if (!this.meetingUrl || this.recordAttempted || ['live', 'starting', 'ending', 'testing'].includes(this.phase)) throw Error('請先結束目前的錄影，再準備主日。');
    const state = await this.helper.run('confirm-share', this.meetingUrl);
    if (!state.joined || !state.microphoneMuted || !state.sharing) throw Error('Meet 尚未確認分享及靜音。');
    this.hostReady = true; this.error = ''; this.phase = 'preparing'; this.reconcile(); this.changed();
  }
  async end() {
    ++this.epoch;
    this.helper.cancel?.();
    if (!this.meetingUrl) { await this.remote.stop(); return; }
    this.phase = 'ending'; this.error = ''; this.changed();
    // The remote sees ending and mutes before recording is stopped.
    const deadline = Date.now() + 8000;
    while (this.remote.snapshot().paired && (!this.peer?.microphoneVerified || this.peer?.microphoneMuted !== true) && Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    try {
      const state = await this.helper.run(this.recordAttempted ? 'end' : 'leave', this.meetingUrl);
      if ((this.recordAttempted && state.recording !== false) || state.microphoneMuted !== true) throw Error('尚未確認停止錄影及靜音，請查看 Meet。');
      this.recording = false; this.phase = 'ended'; this.stage.black();
      if (this.peer && (!this.remote.snapshot().paired || !this.peer.microphoneVerified || this.peer.microphoneMuted !== true)) {
        this.error = '主電腦已結束；講員靜音尚未收到確認，請查看講員電腦。';
      }
      this.changed();
      this.recordAttempted = false;
    } catch (error) { this.error = error.message; this.phase = 'error'; this.changed(); throw error; }
  }
}
module.exports = { WorshipFlow };
