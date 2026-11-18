/** A bookmark is valid only for the same source versions and playback plan. */
export type ListeningBookmark = { version: string; section: string; source: string; time: number };
type ResumeClip = { section: string; parent?: string; source: string; start: number; end: number };

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
  if (explicit.time !== undefined) {
    const matches = (c: ResumeClip) => c.source === explicit.source && c.start <= explicit.time! && explicit.time! < c.end;
    let index = clips.findIndex(c => (c.section === explicit.section || c.parent === explicit.section) && matches(c));
    // A historic passage assignment can change; exact recording/time remains authoritative.
    if (index < 0) index = clips.findIndex(matches);
    return index < 0 ? null : { index, time: explicit.time };
  }
  if (!bookmark || bookmark.version !== version || (explicit.section && explicit.section !== bookmark.section)) return null;
  const index = clips.findIndex(c => c.section === bookmark.section && c.source === bookmark.source &&
    c.start <= bookmark.time && bookmark.time < c.end);
  return index < 0 ? null : { index, time: bookmark.time };
}
