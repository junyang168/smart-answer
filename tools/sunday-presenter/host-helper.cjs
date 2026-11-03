const path = require('node:path');
const { spawn } = require('node:child_process');
const crypto = require('node:crypto');

class HostHelper {
  constructor(binary) { this.binary = binary; this.pending = new Map(); }
  start() {
    if (this.child) return;
    const child = spawn(this.binary, ['--host'], { stdio: ['pipe', 'pipe', 'ignore'] });
    this.child = child;
    let buffer = '';
    child.stdout.on('data', data => {
      buffer += data;
      while (buffer.includes('\n')) {
        const end = buffer.indexOf('\n'), line = buffer.slice(0, end); buffer = buffer.slice(end + 1);
        try {
          const message = JSON.parse(line), pending = this.pending.get(message.id);
          if (pending) { clearTimeout(pending.timer); this.pending.delete(message.id);
            if (message.ok) pending.resolve(message); else pending.reject(Object.assign(
              Error(message.error || 'Meet 設定未完成。'), { recordingNotStarted: message.recordingNotStarted === true })); }
        } catch { /* Native diagnostics never become a trusted result. */ }
      }
    });
    const failed = () => {
      if (this.child !== child) return;
      this.child = null;
      for (const task of this.pending.values()) { clearTimeout(task.timer); task.reject(Error('Meet 控制程序已停止，請重試。')); }
      this.pending.clear();
    };
    child.on('error', failed); child.on('exit', failed);
  }
  run(action, meetingUrl) {
    this.start();
    const id = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id); reject(Error('Meet 操作逾時，請查看會議視窗及輔助使用權限。'));
        this.cancel();
      }, 60000);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(JSON.stringify({ id, action, meetingUrl }) + '\n');
    });
  }
  cancel() {
    const child = this.child; this.child = null; child?.kill();
    for (const task of this.pending.values()) { clearTimeout(task.timer); task.reject(Error('操作已取消。')); }
    this.pending.clear();
  }
  close() { this.child?.stdin.end(); }
}
function helperPath(app, dirname) {
  const root = app.isPackaged ? process.resourcesPath : path.join(dirname, 'output', 'native');
  return path.join(root, 'Sunday Remote.app', 'Contents', 'MacOS', 'Sunday Remote');
}
module.exports = { HostHelper, helperPath };
