import { NextRequest, NextResponse } from "next/server";

const BASE = (process.env.SC_API_SERVICE_URL ?? process.env.FULL_ARTICLE_SERVICE_URL)?.replace(/\/$/, "");
if (!BASE) throw new Error("Backend service URL is required");

async function proxy(request: NextRequest, path?: string[]) {
  const suffix = path?.length ? `/${path.map(encodeURIComponent).join("/")}` : "";
  const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.text();
  const response = await fetch(`${BASE}/admin/thought-review${suffix}${request.nextUrl.search}`, {
    method: request.method,
    headers: {
      "content-type": request.headers.get("content-type") ?? "application/json",
      cookie: request.headers.get("cookie") ?? "",
    },
    body: body || undefined,
    cache: "no-store",
  });
  return new NextResponse(response.body, {
    status: response.status,
    headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
  });
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ path?: string[] }> }) { return proxy(req, (await ctx.params).path); }
export async function POST(req: NextRequest, ctx: { params: Promise<{ path?: string[] }> }) { return proxy(req, (await ctx.params).path); }
export async function PATCH(req: NextRequest, ctx: { params: Promise<{ path?: string[] }> }) { return proxy(req, (await ctx.params).path); }
