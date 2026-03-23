import type { MicroSermon as MicroSermonDto } from "@/app/types/microSermon";

const DEFAULT_BASE_URL =
  process.env.FULL_ARTICLE_SERVICE_URL ||
  process.env.NEXT_PUBLIC_FULL_ARTICLE_SERVICE_URL ||
  "http://127.0.0.1:8555";

export const MICRO_SERMON_REVALIDATE = 0;

export type MicroSermonData = MicroSermonDto;

function sanitize(raw: MicroSermonDto): MicroSermonData | null {
  if (!raw.id || !raw.title) {
    return null;
  }
  return { ...raw };
}

export async function fetchFeaturedMicroSermon(): Promise<MicroSermonData | null> {
  const endpoint = new URL("/micro-sermons/featured", DEFAULT_BASE_URL);
  const response = await fetch(endpoint.toString(), { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load featured micro sermon (${response.status})`);
  }
  const payload = await response.json();
  if (!payload) return null;
  return sanitize(payload as MicroSermonDto);
}

export async function fetchMicroSermons(): Promise<MicroSermonData[]> {
  const endpoint = new URL("/micro-sermons", DEFAULT_BASE_URL);
  const response = await fetch(endpoint.toString(), { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load micro sermons (${response.status})`);
  }
  const payload = (await response.json()) as MicroSermonDto[];
  return payload.map(sanitize).filter((s): s is MicroSermonData => s !== null);
}
