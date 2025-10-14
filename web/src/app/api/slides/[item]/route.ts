import { NextResponse } from "next/server";

const serviceUrl = process.env.FULL_ARTICLE_SERVICE_URL;

if (!serviceUrl) {
  throw new Error("FULL_ARTICLE_SERVICE_URL is not configured");
}

const baseUrl = serviceUrl.replace(/\/$/, "");

export async function GET(_request: Request, context: { params: { item: string } }) {
  const { item } = context.params;
  const target = `${baseUrl}/slides/${encodeURIComponent(item)}`;

  const response = await fetch(target, {
    next: { revalidate: 0 },
  });

  if (!response.ok) {
    const text = await response.text();
    return NextResponse.json(
      { error: `Upstream error: ${response.status} ${response.statusText}`, detail: text },
      { status: response.status }
    );
  }

  const payload = await response.json();
  return NextResponse.json(payload);
}
