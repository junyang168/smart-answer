type Media = Pick<
  HTMLMediaElement,
  "currentTime" | "paused" | "play" | "pause"
>;
type Binding = { start_ms: number; end_ms: number } | undefined;

export function toggleParagraphPlayback(
  media: Media,
  binding: Binding,
  fresh: boolean,
): number | undefined {
  if (fresh && binding) {
    media.currentTime = binding.start_ms / 1000;
    void media.play();
  } else if (!media.paused) {
    media.pause();
  } else {
    void media.play();
  }
  return binding && media.currentTime < binding.end_ms / 1000
    ? binding.end_ms / 1000
    : undefined;
}
