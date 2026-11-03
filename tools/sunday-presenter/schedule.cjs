const { comingSunday } = require('./website.cjs');
const clock = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Chicago',
  weekday: 'short', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

class SundaySchedule {
  constructor({ flow, handledDate = '', save, changed = () => {}, now = () => new Date() }) {
    Object.assign(this, { flow, handledDate, save, changed, now });
    this.message = '每主日 10:00（Dallas）自動開始；請提前完成準備主日。';
    if (handledDate === comingSunday(now())) this.message = '本次主日定時已處理／取消；不會重複啟動，仍可手動開始。';
  }
  snapshot() { return { time: '10:00', timeZone: 'America/Chicago', handledDate: this.handledDate, message: this.message }; }
  remember(date) {
    // Persist before starting. Restarting the host must not repeat a microphone
    // or recorder transition after an uncertain result.
    this.save(date); this.handledDate = date;
  }
  cancel() {
    this.remember(comingSunday(this.now()));
    this.message = '本次主日的自動開始已取消；仍可由主電腦手動開始。'; this.changed();
  }
  async tick() {
    const time = Object.fromEntries(clock.formatToParts(this.now()).map(p => [p.type, p.value]));
    const date = `${time.year}-${time.month}-${time.day}`;
    if (time.weekday !== 'Sun' || Number(time.hour) < 10 || this.handledDate === date) return;
    this.remember(date);
    if (time.hour !== '10' || time.minute !== '00') {
      this.message = '已錯過本主日 10:00；不會補開麥克風或錄影，請由主電腦手動開始。'; this.changed(); return;
    }
    const state = this.flow.snapshot();
    if (['starting', 'live', 'ending', 'ended'].includes(state.phase)) {
      this.message = '10:00 定時已處理；不會重複開始本次崇拜。'; this.changed(); return;
    }
    if (state.phase !== 'ready') {
      const missing = [!state.hostReady && '主電腦 Meet', !state.peerReady && '講員連線／靜音', !state.slidesReady && '兩份觀眾簡報'].filter(Boolean);
      this.message = `10:00 未能自動開始：${missing.join('、') || '目前流程尚未就緒'}。完成後請由主電腦按開始崇拜。`;
      this.changed(); return;
    }
    this.message = '10:00 正在自動開始崇拜。'; this.changed();
    try {
      await this.flow.begin();
      this.message = this.flow.phase === 'live' ? '10:00 已自動開始崇拜。' : '定時開始已取消，請查看主日流程。';
    } catch (error) { this.message = `定時開始未完成：${error.message}`; }
    this.changed();
  }
}
module.exports = { SundaySchedule };
