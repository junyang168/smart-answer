const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

// Incremental writes: never retain a service's recording in memory. A failed
// recording keeps its partial file; only a fully flushed stop publishes MP4.
class RecordingFile {
  constructor(target) {
    if (!path.isAbsolute(target) || path.extname(target).toLowerCase() !== '.mp4') throw Error('請選擇 MP4 儲存位置。');
    this.target = target; this.partial = `${target}.${crypto.randomUUID()}.partial.mp4`;
    this.sequence = 0; this.bytes = 0; this.pendingBytes = 0; this.tail = Promise.resolve(); this.error = null;
  }
  async open() {
    try { await fs.access(this.target); throw Error('檔案已存在，請選擇新的檔名。'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    this.handle = await fs.open(this.partial, 'wx', 0o600); this.created = true;
  }
  append(sequence, raw) {
    if (!this.handle || this.closed || this.error) return Promise.reject(this.error || Error('錄影檔已關閉。'));
    if (sequence !== this.sequence || !(raw instanceof ArrayBuffer) || raw.byteLength === 0 || raw.byteLength > 16 * 1024 * 1024) return Promise.reject(Error('錄影資料順序或大小不符。'));
    if (this.pendingBytes + raw.byteLength > 16 * 1024 * 1024) return Promise.reject(Error('錄影排隊超過 16 MiB。'));
    this.sequence++; this.pendingBytes += raw.byteLength;
    const queuedAt = Date.now();
    const chunk = Buffer.from(raw);
    this.tail = this.tail.then(async () => {
      if (this.error) throw this.error;
      let offset = 0;
      while (offset < chunk.length) {
        const { bytesWritten } = await this.handle.write(chunk, offset, chunk.length - offset);
        if (!bytesWritten) throw Error('磁碟未能寫入錄影。');
        offset += bytesWritten; this.bytes += bytesWritten;
      }
      if (Date.now() - queuedAt > 2000) throw Error('磁碟寫入超過兩秒，錄影已停止；請換用較快的儲存位置。');
    }).catch(error => { this.error = error; throw error; }).finally(() => { this.pendingBytes -= chunk.length; });
    // Keep a handled copy even if a renderer crashes before receiving its reply.
    this.tail.catch(() => {});
    return this.tail;
  }
  async finish(failure) {
    if (this.closed) throw Error('錄影已完成。');
    this.closed = true;
    try {
      await this.tail;
      if (failure) throw Error(failure);
      if (!this.bytes) throw Error('錄影沒有收到影音資料。');
      await this.handle.sync(); await this.handle.close(); this.handle = null;
      // Reserve without overwriting another recording, then atomically replace
      // our own empty placeholder. Works on APFS and external exFAT volumes.
      const reserved = await fs.open(this.target, 'wx', 0o600); await reserved.close();
      try { await fs.rename(this.partial, this.target); }
      catch (error) { await fs.unlink(this.target).catch(() => {}); throw error; }
      return { path: this.target, bytes: this.bytes };
    } catch (error) {
      await this.handle?.close().catch(() => {}); this.handle = null;
      error.partialPath = this.partial; throw error;
    }
  }
}
module.exports = { RecordingFile };
