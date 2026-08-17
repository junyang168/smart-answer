import { NextRequest, NextResponse } from "next/server";

const BASE = (process.env.SC_API_SERVICE_URL ?? process.env.FULL_ARTICLE_SERVICE_URL)?.replace(/\/$/, "");

export async function GET(request: NextRequest) {
  if (!BASE) return NextResponse.json({ detail: "Backend service URL is required" }, { status: 503 });
  const response = await fetch(`${BASE}/admin/wang/matthew-progress${request.nextUrl.search}`, {
    cache: "no-store",
    headers: { cookie: request.headers.get("cookie") ?? "" },
  });
  return new NextResponse(response.body, {
    status: response.status,
    headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
  });
}
