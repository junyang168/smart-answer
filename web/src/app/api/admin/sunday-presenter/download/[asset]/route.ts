import { createReadStream } from "fs";
import { Readable } from "stream";
import { NextRequest, NextResponse } from "next/server";
import { loadPresenterRelease } from "@/app/utils/sunday-presenter-downloads";
import { authorizePresenterDownload } from "../../authorize";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export async function GET(_request: NextRequest, props: { params: Promise<{ asset: string }> }) {
  const denied = await authorizePresenterDownload();
  if (denied) return denied;
  try {
    const { asset } = await props.params;
    const release = await loadPresenterRelease();
    const artifact = release.artifacts.find(item => item.id === asset);
    if (!artifact) return NextResponse.json({ error: "找不到安裝包" }, { status: 404 });
    return new NextResponse(Readable.toWeb(createReadStream(artifact.file)) as ReadableStream, { headers: {
      "Content-Type": artifact.type, "Content-Length": String(artifact.bytes),
      "Content-Disposition": `attachment; filename="${artifact.filename}"`,
      "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    } });
  } catch {
    return NextResponse.json({ error: "安裝包暫時無法下載，請稍後重試。" }, { status: 503, headers: { "Cache-Control": "private, no-store" } });
  }
}
