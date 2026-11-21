// OS shortcuts arrive even when another app owns keyboard focus. Registration
// is scoped to audience playback, never document preparation or settings.
class Clicker {
  constructor({ shortcuts, send, changed }) {
    this.shortcuts = shortcuts; this.send = send; this.changed = changed;
    this.enabled = true; this.active = false; this.error = ''; this.generation = 0;
    this.keys = { PageDown: 'Right', PageUp: 'Left', Right: 'Right', Left: 'Left' };
    this.registered = []; this.inputs = [];
  }
  sync(playing) {
    const wanted = this.enabled && playing;
    if (wanted === this.active && (!wanted || this.registered.length)) return;
    this.release(); this.error = '';
    if (!wanted) return;
    const generation = this.generation;
    for (const [key, direction] of Object.entries(this.keys)) {
      if (!this.shortcuts.register(key, () => {
        if (!this.active || generation !== this.generation) return;
        this.inputs.push({at:new Date().toISOString(),key,direction});
        if(this.inputs.length>100)this.inputs.shift();
        Promise.resolve(this.send(direction, () => this.active && generation === this.generation))
          .catch(error => { this.error = error.message || '換頁未完成。'; this.changed(); });
      })) {
        this.release();
        this.error = `無法接收 ${key}；請關閉佔用快捷鍵的程式，或暫停後重新啟用 Clicker。`;
        return;
      }
      this.registered.push(key);
    }
    this.active = true;
  }
  release() {
    this.active = false; this.generation++;
    for (const key of this.registered) this.shortcuts.unregister(key);
    this.registered = [];
  }
  snapshot() { return { enabled: this.enabled, active: this.active, error: this.error, inputs: [...this.inputs] }; }
}
module.exports = { Clicker };
