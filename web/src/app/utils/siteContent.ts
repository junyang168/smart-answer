import { readFile } from "node:fs/promises";
import path from "node:path";

// Church website content lives in DATA_BASE_DIR/content, not in the repo, so a
// new version of a page's text is a file replacement rather than a deploy.
// Pages that use this must render per request (`revalidate = 0`), or the file
// is read once at build time and the replacement never shows.
export async function readSiteContent(name: string): Promise<string> {
  const dataBaseDir = process.env.DATA_BASE_DIR;
  if (!dataBaseDir) {
    throw new Error("DATA_BASE_DIR is not set; cannot read site content");
  }
  return readFile(/* turbopackIgnore: true */ path.join(dataBaseDir, "content", name), "utf-8");
}

export interface MissionStatement {
  zhHant: string;
}

export async function readMissionStatement(): Promise<MissionStatement> {
  return JSON.parse(await readSiteContent("mission-statement.json")) as MissionStatement;
}
