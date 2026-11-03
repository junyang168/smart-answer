import { NextResponse } from "next/server";
import { loadPresenterRelease } from "@/app/utils/sunday-presenter-downloads";
import { authorizePresenterDownload } from "./authorize";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export async function GET() {
  const denied = await authorizePresenterDownload();
  if (denied) return denied;
  try {
    const release = await loadPresenterRelease();
    return NextResponse.json({ version: release.version, status: release.status,
      artifacts: release.artifacts.map(({ id, filename, bytes, sha256 }) => ({ id, filename, bytes, sha256,
        url: `/api/admin/sunday-presenter/download/${id}` })) }, { headers: { "Cache-Control": "private, no-store" } });
  } catch {
    return NextResponse.json({ error: "安裝包尚未發布，請稍後重試。" }, { status: 503, headers: { "Cache-Control": "private, no-store" } });
  }
}
