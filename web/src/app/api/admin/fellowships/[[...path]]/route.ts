import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authConfig } from "@/app/utils/auth";

const BACKEND_BASE = process.env.FULL_ARTICLE_SERVICE_URL?.replace(/\/$/, "");

if (!BACKEND_BASE) {
  throw new Error("FULL_ARTICLE_SERVICE_URL environment variable is required for fellowship proxy");
}

function splitPathSegments(pathSegments: string[] | undefined): string[] {
  return (
    pathSegments?.flatMap((segment) => {
      try {
        return decodeURIComponent(segment).split("/").filter(Boolean);
      } catch {
        return segment.split("/").filter(Boolean);
      }
    }) ?? []
  );
}

function toIsoDateSegment(date: string): string | null {
  const match = date.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if (!match) {
    return null;
  }

  const [, month, day, year] = match;
  return `${year}-${month}-${day}`;
}

function normalizeDocumentPathSegments(pathSegments: string[] | undefined): string[] {
  const segments = splitPathSegments(pathSegments);
  const documentsIndex = segments.indexOf("documents");
  if (documentsIndex === -1) {
    return segments;
  }

  if (documentsIndex === 1) {
    const isoDate = toIsoDateSegment(segments[0]);
    return isoDate ? [isoDate, ...segments.slice(1)] : segments;
  }

  if (documentsIndex === 3) {
    const isoDate = toIsoDateSegment(segments.slice(0, 3).join("/"));
    return isoDate ? [isoDate, ...segments.slice(3)] : segments;
  }

  return segments;
}

function buildBackendUrl(pathSegments: string[] | undefined, search: string): string {
  const normalizedSegments = normalizeDocumentPathSegments(pathSegments);
  const joined = normalizedSegments.length ? `/${normalizedSegments.map(encodeURIComponent).join("/")}` : "";
  const searchPart = search ? search : "";
  return `${BACKEND_BASE}/admin/fellowships${joined}${searchPart}`;
}

function isMarkdownDocumentRequest(pathSegments: string[] | undefined): boolean {
  const segments = normalizeDocumentPathSegments(pathSegments);
  const documentsIndex = segments.indexOf("documents");
  if (documentsIndex === -1 || documentsIndex >= segments.length - 1) {
    return false;
  }
  return segments[segments.length - 1]?.toLowerCase().endsWith(".md") ?? false;
}

async function proxy(request: NextRequest, params: { path?: string[] }) {
  const requestedSegments = splitPathSegments(params.path);
  if (requestedSegments.includes("documents")) {
    const session = await getServerSession(authConfig);
    if (!session) {
      return NextResponse.json({ error: "Authentication required" }, { status: 401 });
    }
  }

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

  const backendMethod = request.method === "HEAD" ? "GET" : request.method;
  const backendResponse = await fetch(targetUrl, {
    method: backendMethod,
    headers,
    body,
    redirect: "manual",
  });

  const responseHeaders = new Headers();
  backendResponse.headers.forEach((value, key) => {
    const lowerKey = key.toLowerCase();
    if (lowerKey === "content-length") {
      return;
    }
    if (isMarkdownDocumentRequest(params.path) && lowerKey === "content-disposition") {
      return;
    }
    responseHeaders.set(key, value);
  });
  if (isMarkdownDocumentRequest(params.path)) {
    responseHeaders.set("content-type", "text/markdown; charset=utf-8");
  }

  if (request.method === "HEAD") {
    await backendResponse.body?.cancel();
    return new NextResponse(null, {
      status: backendResponse.status,
      statusText: backendResponse.statusText,
      headers: responseHeaders,
    });
  }

  if (request.method === "GET" && isMarkdownDocumentRequest(params.path)) {
    const buffer = await backendResponse.arrayBuffer();
    return new NextResponse(buffer, {
      status: backendResponse.status,
      statusText: backendResponse.statusText,
      headers: responseHeaders,
    });
  }

  return new NextResponse(backendResponse.body, {
    status: backendResponse.status,
    statusText: backendResponse.statusText,
    headers: responseHeaders,
  });
}

export async function GET(request: NextRequest, { params }: { params: { path?: string[] } }) {
  return proxy(request, params);
}

export async function HEAD(request: NextRequest, { params }: { params: { path?: string[] } }) {
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
