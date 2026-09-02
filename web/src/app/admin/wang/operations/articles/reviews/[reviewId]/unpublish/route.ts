import { NextRequest, NextResponse } from "next/server";
import {
  PublicationAuthorizationError,
  publicationActionHeaders,
} from "../../publication-auth";

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
  let headers: Headers;
  try {
    headers = await publicationActionHeaders("unpublish", reviewId);
  } catch (error) {
    if (error instanceof PublicationAuthorizationError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    throw error;
  }
  const response = await fetch(
    `${BACKEND_BASE}/admin/wang/article-reviews/${encodeURIComponent(reviewId)}/unpublish`,
    { method: "POST", headers, body: "{}" },
  );
  const payload = await response.json().catch(() => ({}));
  return NextResponse.json(payload, { status: response.status });
}
