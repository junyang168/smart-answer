import { NextRequest, NextResponse } from "next/server";

const BACKEND_BASE = (
  process.env.DEV_BACKEND_ORIGIN
  || process.env.SC_API_SERVICE_URL
  || process.env.FULL_ARTICLE_SERVICE_URL
  || "http://127.0.0.1:8222"
).replace(/\/$/, "");

export async function POST(
  _request: NextRequest,
  context: { params: Promise<{ reviewId: string }> },
) {
  const { reviewId } = await context.params;
  const response = await fetch(
    `${BACKEND_BASE}/admin/wang/article-reviews/${encodeURIComponent(reviewId)}/publish`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
  );
  const payload = await response.json().catch(() => ({}));
  return NextResponse.json(payload, { status: response.status });
}
