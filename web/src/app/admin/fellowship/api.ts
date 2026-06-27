import {
  FellowshipAnalysisAssets,
  FellowshipAnalysisJob,
  FellowshipDocument,
  FellowshipEmailContent,
  FellowshipEmailResult,
  FellowshipEntry,
  FellowshipLearningContent,
} from "@/app/types/fellowship";

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

export async function fetchFellowshipDocuments(date: string): Promise<FellowshipDocument[]> {
  const response = await fetch(
    resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(date)}/documents`),
    { cache: "no-store" },
  );
  return parseJson(response);
}

function encodePathSegments(path: string): string {
  return path
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

export async function fetchFellowshipDocumentText(
  date: string,
  documentPath: string,
): Promise<string> {
  const response = await fetch(
    resolveApiUrl(
      `${API_BASE_PATH}/${encodeURIComponent(date)}/documents/${encodePathSegments(documentPath)}`,
    ),
    { cache: "no-store" },
  );
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
  return response.text();
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

export async function updateFellowshipLearning(
  date: string,
  content: FellowshipLearningContent,
): Promise<FellowshipLearningContent> {
  const response = await fetch(resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(date)}/learning`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(content),
  });
  return parseJson(response);
}

export async function generateFellowshipLearning(date: string): Promise<FellowshipLearningContent> {
  const response = await fetch(
    resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(date)}/learning/generate`),
    {
      method: "POST",
    },
  );
  return parseJson(response);
}

export async function fetchFellowshipAnalysisAssets(date: string): Promise<FellowshipAnalysisAssets> {
  const response = await fetch(
    resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(date)}/analysis/assets`),
    { cache: "no-store" },
  );
  return parseJson(response);
}

export async function generateFellowshipAnalysis(date: string): Promise<FellowshipAnalysisJob> {
  const response = await fetch(
    resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(date)}/analysis/generate`),
    { method: "POST" },
  );
  return parseJson(response);
}

export async function fetchFellowshipAnalysisJob(
  date: string,
  jobId: string,
): Promise<FellowshipAnalysisJob> {
  const response = await fetch(
    resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(date)}/analysis/jobs/${encodeURIComponent(jobId)}`),
    { cache: "no-store" },
  );
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

export async function fetchFellowshipEmailContent(
  date: string,
): Promise<FellowshipEmailContent> {
  const response = await fetch(
    resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(date)}/email-body`),
    { cache: "no-store" },
  );
  return parseJson(response);
}

export async function updateFellowshipEmailContent(
  date: string,
  content: FellowshipEmailContent,
): Promise<FellowshipEmailContent> {
  const response = await fetch(
    resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(date)}/email-body`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(content),
    },
  );
  return parseJson(response);
}

export async function sendFellowshipEmail(date: string): Promise<FellowshipEmailResult> {
  const response = await fetch(
    resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(date)}/email`),
    {
      method: "POST",
    },
  );
  return parseJson(response);
}
