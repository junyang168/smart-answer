import { NextRequest, NextResponse } from "next/server";

const ALLOWED_CATEGORIES = new Set(["basic", "original"]);

export async function GET(
  _req: NextRequest,
  { params }: { params: { category: string; reference: string } },
) {
  const { category, reference } = params;
  if (!ALLOWED_CATEGORIES.has(category)) {
    return NextResponse.json({ error: "Unsupported category" }, { status: 404 });
  }

  const base =
    process.env.FULL_ARTICLE_SERVICE_URL ||
    process.env.NEXT_PUBLIC_FULL_ARTICLE_SERVICE_URL ||
    "http://127.0.0.1:8555";

  const url = `${base.replace(/\/$/, "")}/scripture/${category}/${reference}`;
  const response = await fetch(url, { cache: "no-store" });
  const text = await response.text();
  return new NextResponse(text, {
    status: response.status,
    headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
  });
}
