import { NextRequest, NextResponse } from "next/server";

const BACKEND_BASE = process.env.FULL_ARTICLE_SERVICE_URL?.replace(/\/$/, "");

if (!BACKEND_BASE) {
  throw new Error("FULL_ARTICLE_SERVICE_URL environment variable is required for fellowship proxy");
}

function buildBackendUrl(pathSegments: string[] | undefined, search: string): string {
  const joined = pathSegments?.length ? `/${pathSegments.map(encodeURIComponent).join("/")}` : "";
  const searchPart = search ? search : "";
  return `${BACKEND_BASE}/admin/fellowships${joined}${searchPart}`;
}

async function proxy(request: NextRequest, params: { path?: string[] }) {
  const targetUrl = buildBackendUrl(params.path, request.nextUrl.search);
  const headers = new Headers();

  const contentType = request.headers.get("content-type");
  if (contentType) {
    headers.set("content-type", contentType);
  }
  const auth = request.headers.get("authorization");
  if (auth) {
    headers.set("authorization", auth);
  }
  const cookies = request.headers.get("cookie");
  if (cookies) {
    headers.set("cookie", cookies);
  }

  let body: BodyInit | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    const text = await request.text();
    body = text.length ? text : undefined;
  }

  const backendResponse = await fetch(targetUrl, {
    method: request.method,
    headers,
    body,
    redirect: "manual",
  });

  const responseBody = await backendResponse.arrayBuffer();
  const response = new NextResponse(responseBody, {
    status: backendResponse.status,
  });

  backendResponse.headers.forEach((value, key) => {
    if (key.toLowerCase() === "content-length") {
      return;
    }
    response.headers.set(key, value);
  });

  return response;
}

export async function GET(request: NextRequest, { params }: { params: { path?: string[] } }) {
  return proxy(request, params);
}

export async function POST(request: NextRequest, { params }: { params: { path?: string[] } }) {
  return proxy(request, params);
}

export async function PUT(request: NextRequest, { params }: { params: { path?: string[] } }) {
  return proxy(request, params);
}

export async function PATCH(request: NextRequest, { params }: { params: { path?: string[] } }) {
  return proxy(request, params);
}

export async function DELETE(request: NextRequest, { params }: { params: { path?: string[] } }) {
  return proxy(request, params);
}
