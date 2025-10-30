import type { DepthOfFaithEpisode as DepthOfFaithEpisodeDto } from "@/app/types/depthOfFaith";

const DEFAULT_BASE_URL =
  process.env.FULL_ARTICLE_SERVICE_URL ||
  process.env.NEXT_PUBLIC_FULL_ARTICLE_SERVICE_URL ||
  "http://127.0.0.1:8555";

export const DEPTH_OF_FAITH_REVALIDATE = 0;

export interface DepthOfFaithEpisode extends DepthOfFaithEpisodeDto {
  audioUrl?: string;
}

function buildAudioUrl(filename: string): string {
  return `/api/webcast/depth_of_faith/audio/${encodeURIComponent(filename)}`;
}

function sanitizeEpisode(raw: DepthOfFaithEpisodeDto): DepthOfFaithEpisode | null {
  if (!raw.id || !raw.title || !raw.description) {
    return null;
  }
  const audioUrl = raw.audioFilename ? buildAudioUrl(raw.audioFilename) : undefined;
  return {
    ...raw,
    audioUrl,
  };
}

export async function fetchDepthOfFaithEpisodes(): Promise<DepthOfFaithEpisode[]> {
  const endpoint = new URL("/webcast/depth-of-faith", DEFAULT_BASE_URL);
  const response = await fetch(endpoint.toString(), {
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`Failed to load Depth of Faith episodes (${response.status})`);
  }

  const payload = (await response.json()) as DepthOfFaithEpisodeDto[];
  const episodes = payload
    .map(sanitizeEpisode)
    .filter((episode): episode is DepthOfFaithEpisode => episode !== null);

  return episodes;
}
