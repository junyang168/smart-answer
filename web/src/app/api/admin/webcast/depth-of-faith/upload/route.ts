import { NextRequest, NextResponse } from "next/server";

const BACKEND_BASE = process.env.FULL_ARTICLE_SERVICE_URL?.replace(/\/$/, "");

if (!BACKEND_BASE) {
  throw new Error("FULL_ARTICLE_SERVICE_URL environment variable is required for webcast admin upload");
}

const TARGET_URL = `${BACKEND_BASE}/admin/webcast/depth-of-faith/upload`;

export async function POST(request: NextRequest) {
  const formData = await request.formData();
  const outgoingForm = new FormData();

  formData.forEach((value, key) => {
    if (value instanceof File) {
      outgoingForm.append(key, value, value.name);
    } else {
      outgoingForm.append(key, value);
    }
  });

  const response = await fetch(TARGET_URL, {
    method: "POST",
    body: outgoingForm,
    redirect: "manual",
  });

  if (!response.ok) {
    const text = await response.text();
    return NextResponse.json({ error: text || response.statusText }, { status: response.status });
  }

  const json = await response.json();
  return NextResponse.json(json, { status: response.status });
}
