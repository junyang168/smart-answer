import { NextRequest, NextResponse } from "next/server";
import { getSessionWithDevFallback } from "@/app/utils/auth";
import { hasEditorAccess } from "@/app/utils/roles";

async function proxy(request: NextRequest, props: { params: Promise<{ path?: string[] }> }) {
  const session = await getSessionWithDevFallback();
  if (!session?.user || !hasEditorAccess(session.user.role)) {
    return NextResponse.json({ error: "需要同工管理權限" }, { status: 403 });
  }
  const secret = process.env.SUNDAY_ASSISTANT_SECRET;
  const base = process.env.FULL_ARTICLE_SERVICE_URL;
  if (!secret || !base) {
    return NextResponse.json({ error: "主日助理尚未完成連線設定" }, { status: 503 });
  }
  const { path = [] } = await props.params;
  const joined = path.join("/");
  // Ingestion/config endpoints are for the local bridge, never browser clients.
  const allowed = request.method === "GET" ? /^(state|groups|ppt-ready\/\d{4}-\d{2}-\d{2}\/[a-f0-9]{64})$/ :
    request.method === "PUT" ? /^settings$/ : /^(connect|tick|identity-audit|admin-help-test|proposals\/\d+|messages\/[^/]+\/retry|ppt-retry\/\d{4}-\d{2}-\d{2})$/;
  if (!allowed.test(joined)) return NextResponse.json({ error: "Not found" }, { status: 404 });
  if (request.method !== "GET") {
    const origin = request.headers.get("origin");
    if (!origin || new URL(origin).host !== request.headers.get("host")) {
      return NextResponse.json({ error: "請從管理頁面操作" }, { status: 403 });
    }
  }
  try {
    const response = await fetch(`${base.replace(/\/$/, "")}/admin/sunday-assistant/${path.map(encodeURIComponent).join("/")}`, {
      method: request.method,
      headers: { Authorization: `Bearer ${secret}`, "Content-Type": "application/json" },
      body: request.method === "GET" ? undefined : await request.text(),
      cache: "no-store", signal: AbortSignal.timeout(20000),
    });
    if (joined.startsWith("ppt-ready/") && response.ok) {
      return new NextResponse(await response.arrayBuffer(), { status: response.status, headers: {
        "Content-Type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "Content-Disposition": response.headers.get("Content-Disposition") || "attachment",
        "Cache-Control": "no-store",
      } });
    }
    return new NextResponse(await response.text(), { status: response.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ error: "主日助理暫時無法連線，請稍後重試" }, { status: 502 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
