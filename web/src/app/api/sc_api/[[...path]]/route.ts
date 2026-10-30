import { NextRequest, NextResponse } from "next/server";
import { createHash, createHmac } from "node:crypto";
import { getServerSession } from "next-auth";
import { authConfig } from "@/app/utils/auth";

const RAW_BASE =
  process.env.SC_API_SERVICE_URL ?? process.env.FULL_ARTICLE_SERVICE_URL;
const BACKEND_BASE = RAW_BASE?.replace(/\/$/, "");

if (!BACKEND_BASE) {
  throw new Error(
    "SC_API_SERVICE_URL (or FULL_ARTICLE_SERVICE_URL) environment variable is required for sc_api proxy",
  );
}

function buildBackendUrl(
  pathSegments: string[] | undefined,
  search: string,
): string {
  const joined = pathSegments?.length
    ? `/${pathSegments.map(encodeURIComponent).join("/")}`
    : "";
  const searchPart = search ? search : "";
  return `${BACKEND_BASE}/sc_api${joined}${searchPart}`;
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
  const range = request.headers.get("range");
  if (range) {
    headers.set("range", range);
  }
  const accept = request.headers.get("accept");
  if (accept) {
    headers.set("accept", accept);
  }

  let body: BodyInit | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    const text = await request.text();
    body = text.length ? text : undefined;
  }

  const guarded = [
    "review_document",
    "review_candidate",
    "review_mode",
    "review_public",
  ].includes(params.path?.[0] || "");
  if (guarded && !["GET", "HEAD"].includes(request.method)) {
    const origin = request.headers.get("origin");
    let sameOrigin = false;
    try {
      sameOrigin =
        !!origin && new URL(origin).host === request.headers.get("host");
    } catch {
      /* Reject malformed origins. */
    }
    if (!sameOrigin)
      return NextResponse.json(
        { detail: "請從本網站的審核頁提交修改" },
        { status: 403 },
      );
  }
  const session =
    guarded || process.env.TRANSCRIPT_REVIEW_ACTION_SECRET
      ? await getServerSession(authConfig)
      : null;
  const actor =
    session?.user?.email ??
    (process.env.NODE_ENV === "development"
      ? process.env.TRANSCRIPT_REVIEW_DEV_ACTOR
      : undefined);
  if (guarded && !actor)
    return NextResponse.json({ detail: "請登入後再操作" }, { status: 401 });
  if (
    guarded &&
    process.env.NODE_ENV === "production" &&
    actor !== "junyang168@gmail.com"
  )
    return NextResponse.json(
      { detail: "圖文審核 beta 目前只對指定帳號開放" },
      { status: 403 },
    );
  const secret = process.env.TRANSCRIPT_REVIEW_ACTION_SECRET;
  if (guarded && !secret)
    return NextResponse.json(
      { detail: "圖文審核身份驗證尚未設定" },
      { status: 503 },
    );
  if (actor && secret) {
    const timestamp = String(Math.floor(Date.now() / 1000));
    const target = new URL(targetUrl);
    const hash = createHash("sha256")
      .update(typeof body === "string" ? body : "")
      .digest("hex");
    const message = [
      request.method,
      target.pathname + target.search,
      actor,
      timestamp,
      hash,
    ].join("\n");
    headers.set("X-Review-Actor", actor);
    headers.set("X-Review-Time", timestamp);
    headers.set(
      "X-Review-Signature",
      createHmac("sha256", secret).update(message).digest("hex"),
    );
  }
  const condition = request.headers.get("if-match");
  if (condition) headers.set("if-match", condition);
  let backendResponse: Response;
  try {
    backendResponse = await fetch(targetUrl, {
      method: request.method,
      headers,
      body,
      redirect: "manual",
    });
  } catch {
    return NextResponse.json(
      { detail: "服務暫時無法連線；請保留此頁，恢復後重試同步。" },
      { status: 503 },
    );
  }

  const responseHeaders = new Headers();
  backendResponse.headers.forEach((value, key) => {
    responseHeaders.set(key, value);
  });

  if (request.method === "HEAD") {
    await backendResponse.body?.cancel();
    return new NextResponse(null, {
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

export async function GET(
  request: NextRequest,
  props: { params: Promise<{ path?: string[] }> },
) {
  const params = await props.params;
  return proxy(request, params);
}

export async function HEAD(
  request: NextRequest,
  props: { params: Promise<{ path?: string[] }> },
) {
  const params = await props.params;
  return proxy(request, params);
}

export async function POST(
  request: NextRequest,
  props: { params: Promise<{ path?: string[] }> },
) {
  const params = await props.params;
  return proxy(request, params);
}

export async function PUT(
  request: NextRequest,
  props: { params: Promise<{ path?: string[] }> },
) {
  const params = await props.params;
  return proxy(request, params);
}

export async function PATCH(
  request: NextRequest,
  props: { params: Promise<{ path?: string[] }> },
) {
  const params = await props.params;
  return proxy(request, params);
}

export async function DELETE(
  request: NextRequest,
  props: { params: Promise<{ path?: string[] }> },
) {
  const params = await props.params;
  return proxy(request, params);
}
