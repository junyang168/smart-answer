import { NextResponse } from "next/server";

const serviceUrl = process.env.FULL_ARTICLE_SERVICE_URL;

if (!serviceUrl) {
  throw new Error("FULL_ARTICLE_SERVICE_URL is not configured");
}

const baseUrl = serviceUrl.replace(/\/$/, "");

async function proxyRequest(
  request: Request,
  context: { params: { item: string } },
  method: "GET" | "PUT"
) {
  const { item } = context.params;
  const target = `${baseUrl}/slides/${encodeURIComponent(item)}/frame`;

  const init: RequestInit = {
    method,
    next: { revalidate: 0 },
  };

  if (method === "PUT") {
    init.headers = {
      "content-type": "application/json",
    };
    init.body = await request.text();
  }

  const response = await fetch(target, init);

  if (!response.ok) {
    const detail = await response.text();
    return NextResponse.json(
      { error: `Upstream error: ${response.status} ${response.statusText}`, detail },
      { status: response.status }
    );
  }

  const payload = await response.json();
  return NextResponse.json(payload, { status: response.status });
}

export async function GET(request: Request, context: { params: { item: string } }) {
  return proxyRequest(request, context, "GET");
}

export async function PUT(request: Request, context: { params: { item: string } }) {
  return proxyRequest(request, context, "PUT");
}
