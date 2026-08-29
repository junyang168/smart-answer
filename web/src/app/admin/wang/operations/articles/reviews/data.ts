import type { TopicEssayReview, TopicEssayReviewList } from "./types";

const BACKEND_BASE = (
  process.env.DEV_BACKEND_ORIGIN
  || process.env.SC_API_SERVICE_URL
  || process.env.FULL_ARTICLE_SERVICE_URL
  || "http://127.0.0.1:8222"
).replace(/\/$/, "");

async function errorMessage(response: Response): Promise<string> {
  try {
    const payload = await response.json() as { detail?: string };
    return payload.detail || `审稿服务返回 ${response.status}`;
  } catch {
    return `审稿服务返回 ${response.status}`;
  }
}

export async function fetchTopicEssayReviews(): Promise<TopicEssayReviewList> {
  const response = await fetch(`${BACKEND_BASE}/admin/wang/article-reviews`, { cache: "no-store" });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export async function fetchTopicEssayReview(reviewId: string): Promise<TopicEssayReview | null> {
  const response = await fetch(
    `${BACKEND_BASE}/admin/wang/article-reviews/${encodeURIComponent(reviewId)}`,
    { cache: "no-store" },
  );
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}
