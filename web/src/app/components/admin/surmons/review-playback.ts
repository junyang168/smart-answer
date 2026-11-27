type Media = Pick<
  HTMLMediaElement,
  "currentTime" | "paused" | "play" | "pause"
>;
type Binding = { start_ms: number; end_ms: number } | undefined;

export function prepareParagraphPlayback(
  media: Media,
  binding: Binding,
  fresh: boolean,
): boolean {
  if (!binding || binding.end_ms <= binding.start_ms) {
    media.pause();
    return false;
  }
  const start = binding.start_ms / 1000;
  const end = binding.end_ms / 1000;
  if (fresh || media.currentTime < start || media.currentTime >= end)
    media.currentTime = start;
  return true;
}

export function toggleParagraphPlayback(
  media: Media,
  binding: Binding,
  fresh: boolean,
): number | undefined {
  if (!fresh && !media.paused) media.pause();
  else if (prepareParagraphPlayback(media, binding, fresh)) void media.play();
  return binding?.end_ms === undefined ? undefined : binding.end_ms / 1000;
}

export function seekParagraphPlayback(
  media: Media,
  binding: Binding,
  seconds: number,
  fresh: boolean,
): void {
  if (!binding) {
    media.pause();
    return;
  }
  const start = binding.start_ms / 1000;
  const end = binding.end_ms / 1000;
  const from = fresh
    ? start
    : Math.max(start, Math.min(end, media.currentTime));
  const target = Math.max(start, Math.min(end, from + seconds));
  if (Math.abs(media.currentTime - target) > 0.001) media.currentTime = target;
  if (media.currentTime >= end) media.pause();
}

export function stopAtParagraphEnd(media: Media, binding: Binding): boolean {
  if (!binding || media.currentTime < binding.end_ms / 1000) return false;
  media.pause();
  const end = binding.end_ms / 1000;
  if (Math.abs(media.currentTime - end) > 0.001) media.currentTime = end;
  return true;
}
