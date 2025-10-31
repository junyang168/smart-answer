import {
  SundayServiceEntry,
  SundayServiceResources,
  SundaySong,
  SundaySongPayload,
  SundayWorker,
  HymnMetadata,
  GenerateHymnLyricsResponse,
  ScriptureBook,
  SundayServiceEmailResult,
} from "@/app/types/sundayService";

const SERVICES_BASE_PATH = "/api/admin/sunday-services";
const SONGS_BASE_PATH = "/api/admin/sunday-songs";
const WORKERS_BASE_PATH = "/api/admin/sunday-workers";
const SCRIPTURE_BOOKS_PATH = "/api/scripture/books";

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

export async function fetchSundayServices(): Promise<SundayServiceEntry[]> {
  const response = await fetch(resolveApiUrl(SERVICES_BASE_PATH), { cache: "no-store" });
  return parseJson(response);
}

export async function fetchSundayResources(): Promise<SundayServiceResources> {
  const response = await fetch(resolveApiUrl(`${SERVICES_BASE_PATH}/resources`), {
    cache: "no-store",
  });
  return parseJson(response);
}

export async function createSundayService(entry: SundayServiceEntry): Promise<SundayServiceEntry> {
  const response = await fetch(resolveApiUrl(SERVICES_BASE_PATH), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(entry),
  });
  return parseJson(response);
}

export async function updateSundayService(
  date: string,
  entry: SundayServiceEntry,
): Promise<SundayServiceEntry> {
  const response = await fetch(resolveApiUrl(`${SERVICES_BASE_PATH}/${encodeURIComponent(date)}`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(entry),
  });
  return parseJson(response);
}

export async function deleteSundayService(date: string): Promise<void> {
  const response = await fetch(resolveApiUrl(`${SERVICES_BASE_PATH}/${encodeURIComponent(date)}`), {
    method: "DELETE",
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
}

export async function fetchSundayWorkers(): Promise<SundayWorker[]> {
  const response = await fetch(resolveApiUrl(WORKERS_BASE_PATH), { cache: "no-store" });
  return parseJson(response);
}

export async function createSundayWorker(worker: SundayWorker): Promise<SundayWorker> {
  const response = await fetch(resolveApiUrl(WORKERS_BASE_PATH), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(worker),
  });
  return parseJson(response);
}

export async function updateSundayWorker(
  currentName: string,
  worker: SundayWorker,
): Promise<SundayWorker> {
  const response = await fetch(
    resolveApiUrl(`${WORKERS_BASE_PATH}/${encodeURIComponent(currentName)}`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(worker),
    },
  );
  return parseJson(response);
}

export async function deleteSundayWorker(name: string): Promise<void> {
  const response = await fetch(resolveApiUrl(`${WORKERS_BASE_PATH}/${encodeURIComponent(name)}`), {
    method: "DELETE",
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
}

export async function fetchSundaySongs(): Promise<SundaySong[]> {
  const response = await fetch(resolveApiUrl(SONGS_BASE_PATH), { cache: "no-store" });
  return parseJson(response);
}

export async function createSundaySong(payload: SundaySongPayload): Promise<SundaySong> {
  const response = await fetch(resolveApiUrl(SONGS_BASE_PATH), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function updateSundaySong(
  songId: string,
  payload: SundaySongPayload,
): Promise<SundaySong> {
  const response = await fetch(resolveApiUrl(`${SONGS_BASE_PATH}/${encodeURIComponent(songId)}`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function deleteSundaySong(songId: string): Promise<void> {
  const response = await fetch(resolveApiUrl(`${SONGS_BASE_PATH}/${encodeURIComponent(songId)}`), {
    method: "DELETE",
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
}

export async function fetchHymnMetadata(index: number): Promise<HymnMetadata> {
  const response = await fetch(resolveApiUrl(`${SONGS_BASE_PATH}/hymnal/${index}`), {
    cache: "no-store",
  });
  return parseJson(response);
}

export async function generateHymnLyrics(
  index: number,
  title: string,
): Promise<GenerateHymnLyricsResponse> {
  const response = await fetch(resolveApiUrl(`${SONGS_BASE_PATH}/hymnal/${encodeURIComponent(index)}/lyrics`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  return parseJson(response);
}

export async function generateSundayServicePpt(date: string): Promise<Blob> {
  const response = await fetch(resolveApiUrl(`${SERVICES_BASE_PATH}/${encodeURIComponent(date)}/ppt`), {
    method: "POST",
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
  return response.blob();
}

export async function uploadFinalSundayServicePpt(
  date: string,
  file: File,
): Promise<SundayServiceEntry> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(
    resolveApiUrl(`${SERVICES_BASE_PATH}/${encodeURIComponent(date)}/ppt/final`),
    {
      method: "POST",
      body: formData,
    },
  );
  return parseJson(response);
}

export async function downloadFinalSundayServicePpt(date: string): Promise<Blob> {
  const response = await fetch(
    resolveApiUrl(`${SERVICES_BASE_PATH}/${encodeURIComponent(date)}/ppt/final`),
    { cache: "no-store" },
  );
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
  return response.blob();
}

export async function sendSundayServiceEmail(date: string): Promise<SundayServiceEmailResult> {
  const response = await fetch(
    resolveApiUrl(`${SERVICES_BASE_PATH}/${encodeURIComponent(date)}/email`),
    {
      method: "POST",
    },
  );
  return parseJson(response);
}

export async function fetchScriptureBooks(): Promise<ScriptureBook[]> {
  const response = await fetch(resolveApiUrl(SCRIPTURE_BOOKS_PATH), { cache: "force-cache" });
  return parseJson(response);
}
