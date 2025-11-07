import { NextResponse } from "next/server";

const serviceUrl = process.env.FULL_ARTICLE_SERVICE_URL;

if (!serviceUrl) {
  throw new Error("FULL_ARTICLE_SERVICE_URL is not configured");
}

const baseUrl = serviceUrl.replace(/\/$/, "");

export async function POST(
  request: Request,
  context: { params: { item: string; capture: string } }
) {
  const { item, capture } = context.params;
  const target = `${baseUrl}/slides/${encodeURIComponent(item)}/captures/${encodeURIComponent(capture)}/persist`;

  const response = await fetch(target, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: await request.text(),
    next: { revalidate: 0 },
  });

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
