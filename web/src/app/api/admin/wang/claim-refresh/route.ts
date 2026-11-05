import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest) {
  const base = (process.env.SC_API_SERVICE_URL ?? process.env.FULL_ARTICLE_SERVICE_URL)?.replace(/\/$/, "");
  if (!base) return NextResponse.json({ detail: "進度服務尚未設定" }, { status: 503 });
  try {
    const response = await fetch(`${base}/admin/wang/claim-refresh${request.nextUrl.search}`, {
      cache: "no-store", headers: { cookie: request.headers.get("cookie") ?? "" },
      signal: AbortSignal.timeout(15000),
    });
    return new NextResponse(response.body, { status: response.status, headers: {
      "content-type": response.headers.get("content-type") ?? "application/json", "cache-control": "no-store",
    } });
  } catch {
    return NextResponse.json({ detail: "暫時無法連接進度服務" }, { status: 503 });
  }
}
