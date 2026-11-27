import { getSessionWithDevFallback } from "@/app/utils/auth";
import { hasEditorAccess } from "@/app/utils/roles";
import { NextResponse } from "next/server";

export async function authorizePresenterDownload() {
  const session = await getSessionWithDevFallback();
  const status = !session?.user?.email ? 401 : !hasEditorAccess(session.user.role) ? 403 : 0;
  if (status) return NextResponse.json({ error: "請登入有後台權限的帳號" }, {
    status, headers: { "Cache-Control": "private, no-store" },
  });
}
