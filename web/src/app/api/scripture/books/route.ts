import { NextRequest, NextResponse } from "next/server";

const BACKEND_BASE = process.env.FULL_ARTICLE_SERVICE_URL?.replace(/\/$/, "");

if (!BACKEND_BASE) {
  throw new Error("FULL_ARTICLE_SERVICE_URL environment variable is required for scripture proxy");
}

export async function GET(request: NextRequest) {
  const targetUrl = `${BACKEND_BASE}/scripture/books`;
  const backendResponse = await fetch(targetUrl, {
    method: "GET",
    headers: {
      "content-type": "application/json",
    },
    cache: request.cache,
  });

  const body = await backendResponse.arrayBuffer();
  const response = new NextResponse(body, { status: backendResponse.status });
  backendResponse.headers.forEach((value, key) => {
    if (key.toLowerCase() === "content-length") {
      return;
    }
    response.headers.set(key, value);
  });
  return response;
}
