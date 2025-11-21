import {
  FullArticleDetail,
  FullArticleSummary,
  SaveFullArticlePayload,
  GenerateArticleResponse,
  GenerateSummaryResponse,
} from "@/app/types/full-article";

const API_BASE_PATH = "/api/admin/full-articles";

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

export async function fetchFullArticleList(): Promise<FullArticleSummary[]> {
  const response = await fetch(resolveApiUrl(API_BASE_PATH), { cache: "no-store" });
  return parseJson(response);
}

export async function fetchFullArticle(articleId: string): Promise<FullArticleDetail> {
  const response = await fetch(resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(articleId)}`), {
    cache: "no-store",
  });
  return parseJson(response);
}

export async function fetchNewArticleTemplate(): Promise<FullArticleDetail> {
  const response = await fetch(resolveApiUrl(`${API_BASE_PATH}/new`), { cache: "no-store" });
  return parseJson(response);
}

export async function saveFullArticle(payload: SaveFullArticlePayload): Promise<FullArticleDetail> {
  const response = await fetch(resolveApiUrl(API_BASE_PATH), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function deleteFullArticle(articleId: string): Promise<void> {
  const response = await fetch(resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(articleId)}`), {
    method: "DELETE",
  });
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || "刪除失敗");
  }
}

export async function commitArticle(id: string): Promise<{ message: string; commit_id: string }> {
  const response = await fetch(resolveApiUrl(`${API_BASE_PATH}/${id}/commit`), {
    method: "POST",
  });
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || "Commit failed");
  }
  return response.json();
}

export async function regenerateFullArticle(
  articleId: string,
  scriptMarkdown?: string,
  promptMarkdown?: string,
): Promise<GenerateArticleResponse> {
  const response = await fetch(resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(articleId)}/generate`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scriptMarkdown, promptMarkdown }),
  });
  return parseJson(response);
}

export async function updatePrompt(promptMarkdown: string): Promise<string> {
  const response = await fetch(resolveApiUrl(`${API_BASE_PATH}/prompt`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ promptMarkdown }),
  });
  const data = await parseJson<{ promptMarkdown: string }>(response);
  return data.promptMarkdown;
}

export async function regenerateSummary(articleId: string): Promise<GenerateSummaryResponse> {
  const response = await fetch(resolveApiUrl(`${API_BASE_PATH}/${encodeURIComponent(articleId)}/summary`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return parseJson(response);
}
