import { NextRequest, NextResponse } from "next/server";

const BACKEND_BASE =
  process.env.FULL_ARTICLE_SERVICE_URL ||
  process.env.NEXT_PUBLIC_FULL_ARTICLE_SERVICE_URL ||
  "http://127.0.0.1:8555";

function buildBackendUrl(filename: string): string {
  const encoded = encodeURIComponent(filename);
  const url = new URL(`/webcast/depth-of-faith/audio/${encoded}`, BACKEND_BASE);
  return url.toString();
}

export async function GET(request: NextRequest, { params }: { params: { filename: string } }) {
  const filename = params.filename;
  if (!filename) {
    return NextResponse.json({ error: "Missing filename" }, { status: 400 });
  }

  const headers = new Headers();
  const range = request.headers.get("range");
  if (range) {
    headers.set("range", range);
  }

  const backendResponse = await fetch(buildBackendUrl(filename), {
    method: "GET",
    headers,
  });

  const proxyHeaders = new Headers();
  backendResponse.headers.forEach((value, key) => {
    // Content-Length handled automatically by NextResponse when possible.
    if (key.toLowerCase() === "content-length") {
      return;
    }
    proxyHeaders.set(key, value);
  });

  if (!proxyHeaders.has("content-type")) {
    proxyHeaders.set("content-type", "audio/mpeg");
  }

  return new NextResponse(backendResponse.body, {
    status: backendResponse.status,
    headers: proxyHeaders,
  });
}
