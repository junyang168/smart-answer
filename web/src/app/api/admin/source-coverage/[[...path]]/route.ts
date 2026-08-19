import { NextRequest, NextResponse } from "next/server";

const BASE = (process.env.SC_API_SERVICE_URL ?? process.env.FULL_ARTICLE_SERVICE_URL)?.replace(/\/$/, "");
if (!BASE) throw new Error("Backend service URL is required");

async function proxy(request: NextRequest, path?: string[]) {
  const suffix = path?.length ? `/${path.map(encodeURIComponent).join("/")}` : "";
  const response = await fetch(`${BASE}/admin/source-coverage${suffix}${request.nextUrl.search}`, {
    method: "GET",
    headers: { cookie: request.headers.get("cookie") ?? "" },
    cache: "no-store",
  });
  return new NextResponse(response.body, {
    status: response.status,
    headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
  });
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ path?: string[] }> }) {
  return proxy(req, (await ctx.params).path);
}
