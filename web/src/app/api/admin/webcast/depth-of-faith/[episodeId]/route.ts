import { NextRequest, NextResponse } from "next/server";

const BACKEND_BASE = process.env.FULL_ARTICLE_SERVICE_URL?.replace(/\/$/, "");

if (!BACKEND_BASE) {
  throw new Error("FULL_ARTICLE_SERVICE_URL environment variable is required for webcast admin proxy");
}

function buildTarget(episodeId: string): string {
  return `${BACKEND_BASE}/admin/webcast/depth-of-faith/${encodeURIComponent(episodeId)}`;
}

function buildHeaders(request: NextRequest): Headers {
  const headers = new Headers();
  const forwardHeaders = ["authorization", "cookie", "content-type"];
  for (const key of forwardHeaders) {
    const value = request.headers.get(key);
    if (value) {
      headers.set(key, value);
    }
  }
  return headers;
}

async function proxy(request: NextRequest, target: string) {
  const headers = buildHeaders(request);

  const init: RequestInit = {
    method: request.method,
    headers,
    redirect: "manual",
  };

  if (request.method !== "GET" && request.method !== "HEAD") {
    const bodyText = await request.text();
    init.body = bodyText.length ? bodyText : undefined;
  }

  const response = await fetch(target, init);
  const text = await response.text();

  return new NextResponse(text, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
    },
  });
}

export async function PUT(request: NextRequest, { params }: { params: { episodeId: string } }) {
  return proxy(request, buildTarget(params.episodeId));
}

export async function DELETE(request: NextRequest, { params }: { params: { episodeId: string } }) {
  return proxy(request, buildTarget(params.episodeId));
}
