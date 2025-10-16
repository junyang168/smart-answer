import { FellowshipEntry } from "@/app/types/fellowship";

const API_BASE_PATH = "/api/admin/fellowships";

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

export async function fetchFellowships(): Promise<FellowshipEntry[]> {
  const response = await fetch(resolveApiUrl(API_BASE_PATH), { cache: "no-store" });
  return parseJson(response);
}

export async function createFellowship(entry: FellowshipEntry): Promise<FellowshipEntry> {
  const response = await fetch(resolveApiUrl(API_BASE_PATH), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(entry),
  });
  return parseJson(response);
}

export async function updateFellowship(date: string, entry: FellowshipEntry): Promise<FellowshipEntry> {
  const response = await fetch(resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(date)}`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(entry),
  });
  return parseJson(response);
}

export async function deleteFellowship(date: string): Promise<void> {
  const response = await fetch(resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(date)}`), {
    method: "DELETE",
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
}
