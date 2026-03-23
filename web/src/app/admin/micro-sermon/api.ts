import type {
  MicroSermon,
  MicroSermonPayload,
  MicroSermonUpdatePayload,
} from "@/app/types/microSermon";

const BASE_PATH = "/api/admin/micro-sermons";

function resolveApiUrl(path: string): string {
  if (typeof window !== "undefined") {
    return path;
  }

  const backendBase =
    process.env.FULL_ARTICLE_SERVICE_URL ||
    process.env.NEXT_PUBLIC_FULL_ARTICLE_SERVICE_URL ||
    "http://127.0.0.1:8555";
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

export async function listMicroSermons(): Promise<MicroSermon[]> {
  const response = await fetch(resolveApiUrl(BASE_PATH), { cache: "no-store" });
  return parseJson(response);
}

export async function createMicroSermon(
  payload: MicroSermonPayload,
): Promise<MicroSermon> {
  const response = await fetch(resolveApiUrl(BASE_PATH), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function updateMicroSermon(
  sermonId: string,
  payload: MicroSermonUpdatePayload,
): Promise<MicroSermon> {
  const response = await fetch(
    resolveApiUrl(`${BASE_PATH}/${encodeURIComponent(sermonId)}`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  return parseJson(response);
}

export async function deleteMicroSermon(sermonId: string): Promise<void> {
  const response = await fetch(
    resolveApiUrl(`${BASE_PATH}/${encodeURIComponent(sermonId)}`),
    { method: "DELETE" },
  );
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
}
