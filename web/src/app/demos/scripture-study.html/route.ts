import { readFile } from "node:fs/promises";
import path from "node:path";
import { getServerSession } from "next-auth";
import { authConfig } from "@/app/utils/auth";
import { canViewStudyDemo } from "@/app/utils/roles";

export const dynamic = "force-dynamic";

export async function GET() {
  const session = await getServerSession(authConfig);
  const headers = { "Cache-Control": "private, no-store", Vary: "Cookie" };
  if (!canViewStudyDemo(session?.user?.role)) {
    return new Response(null, { status: 404, headers });
  }
  const html = await readFile(path.join(process.cwd(), "private/scripture-study.html"));
  return new Response(html, { headers: { ...headers, "Content-Type": "text/html; charset=utf-8" } });
}
