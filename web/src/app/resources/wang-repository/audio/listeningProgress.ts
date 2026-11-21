/** A bookmark is valid only for the same source versions and playback plan. */
export type ListeningBookmark = { version: string; section: string; source: string; time: number; audioRevision?: string };
type ResumeClip = { section: string; parent?: string; source: string; start: number; end: number;
  excluded?: [number, number][]; audioRevision?: string };

export function readBookmark(storage: Pick<Storage, "getItem">, key: string): ListeningBookmark | null {
  try {
    const value = JSON.parse(storage.getItem(key) ?? "null");
    return value && typeof value.version === "string" && typeof value.section === "string" &&
      typeof value.source === "string" && typeof value.time === "number" && Number.isFinite(value.time)
      ? value : null;
  } catch { return null; }
}

export function resumeClip(clips: ResumeClip[], bookmark: ListeningBookmark | null, version: string,
  explicit: { section?: string; source?: string; time?: number }) {
  function skipped(recording: string | undefined, time: number, revision?: string) {
    const matches = (clip: ResumeClip) => clip.source === recording &&
      (revision === undefined || clip.audioRevision === revision) &&
      clip.excluded?.some(([start, end]) => start <= time && time < end);
    const eligible = clips.map((clip, index) => ({ clip, index })).filter(({ clip }) => matches(clip));
    const next = eligible.filter(({ clip }) => clip.start >= time).sort((a, b) => a.clip.start - b.clip.start)[0];
    if (next) return { index: next.index, time: next.clip.start };
    const previous = eligible.sort((a, b) => b.clip.end - a.clip.end)[0];
    return previous ? { index: previous.index, time: previous.clip.end } : null;
  }
  if (explicit.time !== undefined) {
    const matches = (c: ResumeClip) => c.source === explicit.source && c.start <= explicit.time! && explicit.time! < c.end;
    let index = clips.findIndex(c => (c.section === explicit.section || c.parent === explicit.section) && matches(c));
    // A historic passage assignment can change; exact recording/time remains authoritative.
    if (index < 0) index = clips.findIndex(matches);
    return index < 0 ? skipped(explicit.source, explicit.time) : { index, time: explicit.time };
  }
  if (!bookmark || (explicit.section && explicit.section !== bookmark.section)) return null;
  if (bookmark.version !== version && !bookmark.audioRevision) return null;
  const index = clips.findIndex(c => c.section === bookmark.section && c.source === bookmark.source &&
    (bookmark.version === version || bookmark.audioRevision === c.audioRevision) &&
    c.start <= bookmark.time && bookmark.time < c.end);
  return index < 0 ? skipped(bookmark.source, bookmark.time, bookmark.audioRevision) : { index, time: bookmark.time };
}
