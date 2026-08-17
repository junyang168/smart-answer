import { NextRequest, NextResponse } from "next/server";

const RAW_BASE = process.env.SC_API_SERVICE_URL ?? process.env.FULL_ARTICLE_SERVICE_URL;
const BACKEND_BASE = RAW_BASE?.replace(/\/$/, "");

if (!BACKEND_BASE) {
  throw new Error("SC_API_SERVICE_URL (or FULL_ARTICLE_SERVICE_URL) is required");
}

async function proxy(request: NextRequest, path: string[] | undefined) {
  const suffix = path?.length ? `/${path.map(encodeURIComponent).join("/")}` : "";
  const response = await fetch(
    `${BACKEND_BASE}/canonical-repository${suffix}${request.nextUrl.search}`,
    { headers: { cookie: request.headers.get("cookie") ?? "" }, cache: "no-store" },
  );
  return new NextResponse(response.body, {
    status: response.status,
    headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
  });
}

export async function GET(request: NextRequest, props: { params: Promise<{ path?: string[] }> }) {
  const params = await props.params;
  return proxy(request, params.path);
}
