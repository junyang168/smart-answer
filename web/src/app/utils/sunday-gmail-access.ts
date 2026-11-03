import { promises as fs } from "node:fs";
import path from "node:path";
import { hasEditorAccess } from "./roles";

// Personal mailbox review is narrower than the general church editor policy.
export async function canReviewSundayGmail(email: string | null | undefined, role: unknown): Promise<boolean> {
  if (!email || !hasEditorAccess(role)) return false;
  const filename = process.env.SUNDAY_GMAIL_CONFIG || path.join(process.env.DATA_BASE_DIR || path.join(process.cwd(), "data"), "config", "sunday_gmail.json");
  try {
    const config = JSON.parse(await fs.readFile(filename, "utf8"));
    return Array.isArray(config.reviewers) && config.reviewers.some((value: unknown) => typeof value === "string" && value.toLowerCase() === email.toLowerCase());
  } catch {
    return false;
  }
}
