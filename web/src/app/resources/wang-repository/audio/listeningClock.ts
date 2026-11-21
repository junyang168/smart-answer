type PlaybackInterval = { start: number; end: number; media_start?: number };

export function mediaTime(interval: PlaybackInterval, sourceSeconds: number): number {
  return interval.media_start === undefined ? sourceSeconds : interval.media_start + (sourceSeconds - interval.start);
}

export function sourceTime(interval: PlaybackInterval, mediaSeconds: number): number {
  return interval.media_start === undefined ? mediaSeconds : interval.start + (mediaSeconds - interval.media_start);
}
