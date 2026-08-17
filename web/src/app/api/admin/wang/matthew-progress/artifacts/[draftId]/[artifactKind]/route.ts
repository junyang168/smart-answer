import { NextRequest, NextResponse } from "next/server";

const BASE = (process.env.SC_API_SERVICE_URL ?? process.env.FULL_ARTICLE_SERVICE_URL)?.replace(/\/$/, "");

export async function GET(
  request: NextRequest,
  props: { params: Promise<{ draftId: string; artifactKind: string }> },
) {
  const params = await props.params;
  if (!BASE) return NextResponse.json({ detail: "Backend service URL is required" }, { status: 503 });
  const response = await fetch(`${BASE}/admin/wang/matthew-progress/artifacts/${encodeURIComponent(params.draftId)}/${encodeURIComponent(params.artifactKind)}`, {
    cache: "no-store",
    headers: { cookie: request.headers.get("cookie") ?? "" },
  });
  return new NextResponse(response.body, {
    status: response.status,
    headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
  });
}
