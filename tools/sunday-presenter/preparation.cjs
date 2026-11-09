// One bounded preparation run; adapters own Office/Electron details.
// Every retry rechecks reality and reuses verified pages rather than replaying clicks.
class Preparation {
  constructor({ driver, changed = () => {} }) {
    this.driver = driver;
    this.changed = changed;
    this.state = { status: 'idle', message: '', decks: { sermon: 'pending', worship: 'pending' } };
  }

  snapshot() { return { ...this.state, decks: { ...this.state.decks }, busy: !!this.running }; }

  update(patch) {
    this.state = { ...this.state, ...patch, decks: { ...this.state.decks, ...patch.decks } };
    this.changed();
  }

  start() {
    if (this.running) return this.running;
    const abort = this.abort = new AbortController();
    const task = Promise.resolve().then(() => this.run(abort.signal));
    this.running = task.catch(error => {
      if (!abort.signal.aborted) this.update({ status: 'blocked', message: error.message || '準備未完成，請重試。',
        decks: Object.fromEntries(Object.entries(this.state.decks).filter(([,state]) => state === 'preparing').map(([deck]) => [deck,'blocked'])) });
    }).finally(() => {
      if (this.abort === abort) { this.running = null; this.abort = null; this.changed(); }
    });
    this.changed();
    return this.running;
  }

  async run(signal) {
    this.update({ status: 'discovering', message: '正在確認本週日期和兩份 PPT…', decks: { sermon: 'pending', worship: 'pending' } });
    const folder = await this.driver.folder(signal);
    signal.throwIfAborted();
    if (!folder.folderExists) throw Error(`${folder.date} 的資料夾尚未建立，請先在網站產生或上傳 PPT。`);
    this.update({ date: folder.date });
    const pages = await this.driver.documents(folder, signal);
    signal.throwIfAborted();
    if (!pages.sermon || !pages.worship || pages.sermon.id === pages.worship.id) throw Error('未能確認本週敬拜及講道兩份不同的 PPT，請檢查檔案。');
    const previous = this.driver.active();
    for (const deck of ['sermon', 'worship']) {
      signal.throwIfAborted();
      const page = pages[deck];
      if (!(await this.driver.ready(page, signal))) {
        this.update({ status: 'preparing', message: `正在準備${deck === 'sermon' ? '講道' : '主日崇拜'}放映畫面…`, decks: { [deck]: 'preparing' } });
        await this.driver.prepare(page, signal);
        signal.throwIfAborted();
        if (!(await this.driver.ready(page, signal))) throw Error(`${deck === 'sermon' ? '講道' : '主日崇拜'}尚未通過放映檢查，請重試這份 PPT。`);
      }
      this.update({ decks: { [deck]: 'ready' } });
    }
    for (const deck of ['sermon', 'worship']) {
      signal.throwIfAborted();
      if (!(await this.driver.ready(pages[deck], signal))) {
        this.update({ decks: { [deck]: 'pending' } });
        throw Error(`${deck === 'sermon' ? '講道' : '主日崇拜'}的放映狀態已改變；請繼續準備，已完成的另一份會保留。`);
      }
    }
    signal.throwIfAborted();
    const deck = Object.keys(pages).find(key => pages[key].id === previous) || 'worship';
    await this.driver.present(pages[deck], signal);
    signal.throwIfAborted();
    this.update({ status: 'ready', message: '兩份 PPT 已就緒，可直接切換。' });
  }

  observe() {
    if (!this.running && this.state.status === 'ready' && this.driver.intact && !this.driver.intact()) {
      this.update({ status: 'blocked', message: '有一份 PPT 的放映狀態已改變，請繼續準備。' });
    }
  }

  cancel() { this.abort?.abort(); }
}

function waitUntil(check, { signal, timeoutMs, message, intervalMs = 200 }) {
  return new Promise((resolve, reject) => {
    let done = false, timer, deadlineTimer;
    const end = Date.now() + timeoutMs;
    const finish = (error, value) => {
      if (done) return;
      done = true; clearTimeout(timer); clearTimeout(deadlineTimer); signal?.removeEventListener('abort', cancelled);
      error ? reject(error) : resolve(value);
    };
    const cancelled = () => finish(signal.reason || Error('準備已停止。'));
    const tick = async () => {
      try {
        signal?.throwIfAborted();
        const value = await check();
        if (done) return;
        signal?.throwIfAborted();
        if (value) return finish(null, value);
        if (Date.now() >= end) return finish(Error(message));
        timer = setTimeout(tick, Math.min(intervalMs, end - Date.now()));
      } catch (error) { finish(error); }
    };
    signal?.addEventListener('abort', cancelled, { once: true });
    deadlineTimer = setTimeout(() => finish(Error(message)), timeoutMs);
    tick();
  });
}
module.exports = { Preparation, waitUntil };
