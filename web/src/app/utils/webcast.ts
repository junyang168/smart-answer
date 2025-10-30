const AUDIO_BASE = (process.env.NEXT_PUBLIC_WEBCAST_AUDIO_BASE || "").replace(/\/$/, "");
const AUDIO_PATH_PREFIX = (process.env.NEXT_PUBLIC_WEBCAST_AUDIO_PATH || "/web/data/webcast").replace(
  /\/$/,
  "",
);

export function resolveWebcastAudioUrl(filename?: string | null): string | undefined {
  if (!filename) {
    return undefined;
  }
  const encoded = encodeURIComponent(filename);
  if (AUDIO_BASE) {
    const needsSlash = AUDIO_PATH_PREFIX.startsWith("/") ? "" : "/";
    return `${AUDIO_BASE}${needsSlash}${AUDIO_PATH_PREFIX}/${encoded}`;
  }
  return `${AUDIO_PATH_PREFIX}/${encoded}`;
}
