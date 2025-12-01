import { SermonSeries } from "@/app/types/sermon-series";

const API_BASE_PATH = "/api/admin/surmon-series";

function resolveApiUrl(path: string): string {
  if (typeof window !== "undefined") {
    return path;
  }

  const backendBase =
    process.env.FULL_ARTICLE_SERVICE_URL ||
    process.env.NEXT_PUBLIC_FULL_ARTICLE_SERVICE_URL ||
    "http://127.0.0.1:8222";
  if (backendBase) {
    return new URL(path.replace(/^\/api\//, "/"), backendBase).toString();
  }

  const siteBase =
    process.env.NEXT_PUBLIC_SITE_URL ||
    process.env.NEXT_PUBLIC_APP_URL ||
    process.env.APP_URL ||
    "http://127.0.0.1:3000";
  return new URL(path, siteBase).toString();
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function fetchSermonSeries(): Promise<SermonSeries[]> {
  const response = await fetch(resolveApiUrl(API_BASE_PATH), { cache: "no-store" });
  return parseJson(response);
}

export async function createSermonSeries(payload: SermonSeries): Promise<SermonSeries> {
  const response = await fetch(resolveApiUrl(API_BASE_PATH), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function updateSermonSeries(
  seriesId: string,
  payload: SermonSeries,
): Promise<SermonSeries> {
  const response = await fetch(
    resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(seriesId)}`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  return parseJson(response);
}

export async function deleteSermonSeries(seriesId: string): Promise<void> {
  const response = await fetch(
    resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(seriesId)}`),
    { method: "DELETE" },
  );
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
}

export async function generateSeriesMetadata(
  seriesId: string,
  userId: string,
): Promise<{
  title: string;
  summary: string;
  topics: string[];
  keypoints: string[];
}> {
  const response = await fetch(resolveApiUrl("/api/sc_api/generate_series_metadata"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ series_id: seriesId, user_id: userId }),
  });
  return parseJson(response);
}
