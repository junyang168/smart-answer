const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { Readable } = require('node:stream');

class MusicLibrary {
  constructor(file) {
    this.file = file; this.tracks = []; this.selected = null; this.slots = {}; this.volume = 0.7; this.error = null;
    try {
      const saved = JSON.parse(fs.readFileSync(file, 'utf8'));
      this.tracks = (Array.isArray(saved.tracks) ? saved.tracks : []).slice(0, 100).filter(t =>
        /^[0-9a-f-]{36}$/.test(t.id) && typeof t.path === 'string' && path.isAbsolute(t.path) && path.extname(t.path).toLowerCase() === '.mp3')
        .map(t => ({ id: t.id, path: t.path, name: path.basename(t.path) }));
      this.selected = this.tracks.some(t => t.id === saved.selected) ? saved.selected : this.tracks[0]?.id || null;
      for (const slot of ['hymn','responseHymn','doxology']) {
        if (this.tracks.some(t => t.id === saved.slots?.[slot])) this.slots[slot] = saved.slots[slot];
      }
      if (Number.isFinite(saved.volume)) this.volume = Math.max(0, Math.min(1, saved.volume));
    } catch (error) { if (error.code !== 'ENOENT') this.error = '音樂清單無法讀取，請重新加入 MP3。'; }
  }
  save() {
    fs.mkdirSync(path.dirname(this.file), { recursive: true, mode: 0o700 });
    const temp = this.file + '.tmp';
    fs.writeFileSync(temp, JSON.stringify({ tracks: this.tracks, selected: this.selected, slots: this.slots, volume: this.volume }), { mode: 0o600 });
    fs.chmodSync(temp, 0o600); fs.renameSync(temp, this.file);
  }
  add(files) {
    const additions = [];
    for (const file of files) {
      if (typeof file !== 'string' || !path.isAbsolute(file) || path.extname(file).toLowerCase() !== '.mp3') throw Error('請選擇本地 MP3 檔案。');
      const stat = fs.statSync(file);
      if (!stat.isFile() || !stat.size) throw Error('MP3 不存在或內容為空，請先確認已下載。');
      const real = fs.realpathSync(file);
      if (path.extname(real).toLowerCase() !== '.mp3') throw Error('請選擇實際的 MP3 檔案。');
      if (!this.tracks.some(t => t.path === real) && !additions.some(t => t.path === real)) additions.push({ id: randomUUID(), path: real, name: path.basename(real) });
    }
    if (this.tracks.length + additions.length > 100) throw Error('最多加入 100 首音樂，請先移除不需要的項目。');
    this.tracks.push(...additions); this.selected ||= this.tracks[0]?.id || null; this.error = null; this.save();
  }
  assign(slot, file) {
    if (!['hymn','responseHymn','doxology'].includes(slot)) throw Error('請選擇詩歌、回應詩歌或三一頌。');
    this.add([file]); this.slots[slot] = this.tracks.find(t => t.path === fs.realpathSync(file)).id;
    this.tracks = this.tracks.filter(t => Object.values(this.slots).includes(t.id));
    if (!this.tracks.some(t => t.id === this.selected)) this.selected = this.slots[slot];
    this.save();
  }
  get(id) { const track = this.tracks.find(t => t.id === id); if (!track) throw Error('請先選擇音樂。'); return track; }
  snapshot() { return { tracks: this.tracks.map(({ id, name }) => ({ id, name })), selected: this.selected, slots: { ...this.slots }, volume: this.volume }; }
}

function byteRange(header, size) {
  if (!header) return { start: 0, end: size - 1, partial: false };
  const match = /^bytes=(\d*)-(\d*)$/.exec(header);
  if (!match || (!match[1] && !match[2])) return null;
  let start, end;
  if (!match[1]) { const suffix = Number(match[2]); if (!Number.isSafeInteger(suffix) || suffix <= 0) return null; start = Math.max(0, size - suffix); end = size - 1; }
  else { start = Number(match[1]); end = match[2] ? Number(match[2]) : size - 1; }
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || start >= size || end < start) return null;
  return { start, end: Math.min(end, size - 1), partial: true };
}

async function musicResponse(request, library) {
  const url = new URL(request.url);
  if (url.protocol !== 'sunday-music:' || url.hostname !== 'track' || url.search || url.hash || !/^\/[0-9a-f-]{36}$/.test(url.pathname)) return new Response(null, { status: 404 });
  if (!['GET','HEAD'].includes(request.method)) return new Response(null, { status: 405 });
  let track, stat;
  try { track = library.get(url.pathname.slice(1)); stat = await fs.promises.stat(track.path); }
  catch { return new Response(null, { status: 404 }); }
  if (!stat.isFile() || !stat.size) return new Response(null, { status: 404 });
  const range = byteRange(request.headers.get('range'), stat.size);
  if (!range) return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${stat.size}` } });
  const headers = { 'Content-Type': 'audio/mpeg', 'Accept-Ranges': 'bytes', 'Content-Length': String(range.end - range.start + 1), 'Cache-Control': 'no-store' };
  if (range.partial) headers['Content-Range'] = `bytes ${range.start}-${range.end}/${stat.size}`;
  const stream = request.method === 'HEAD' ? null : fs.createReadStream(track.path, { start: range.start, end: range.end });
  if (stream) {
    const abort = () => stream.destroy();
    if (request.signal.aborted) abort();
    else { request.signal.addEventListener('abort', abort, { once: true }); stream.on('close', () => request.signal.removeEventListener('abort', abort)); }
  }
  return new Response(stream ? Readable.toWeb(stream) : null, { status: range.partial ? 206 : 200, headers });
}
module.exports = { MusicLibrary, byteRange, musicResponse };
