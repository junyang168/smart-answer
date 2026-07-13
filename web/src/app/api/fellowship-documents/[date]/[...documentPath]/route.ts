import { createReadStream } from "fs";
import { readFile, stat } from "fs/promises";
import path from "path";
import { Readable } from "stream";
import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const CONTENT_TYPES: Record<string, string> = {
  ".md": "text/markdown; charset=utf-8",
  ".mp4": "video/mp4",
  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
};

const HIDDEN_PREFIXES = ["audio/", "tmp/", "temp/", "cache/"];
const FELLOWSHIP_ANALYSIS_DOCUMENT = "主題與查經重點.md";
const FELLOWSHIP_GENERATED_TRANSCRIPT = "recording.transcript.generated.md";

async function readRootEnvValue(key: string): Promise<string | null> {
  const candidates = [path.resolve(process.cwd(), ".env"), path.resolve(process.cwd(), "..", ".env")];
  for (const envPath of candidates) {
    try {
      const content = await readFile(envPath, "utf-8");
      const line = content
        .split(/\r?\n/)
        .find((entry) => entry.trim().startsWith(`${key}=`));
      if (line) {
        return line.slice(line.indexOf("=") + 1).trim().replace(/^['"]|['"]$/g, "");
      }
    } catch {
      // Ignore missing env files; callers will return 404 if no docs root can be resolved.
    }
  }
  return null;
}

function fellowshipDateToFolderName(date: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return date;
  }
  const usMatch = date.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if (usMatch) {
    const [, month, day, year] = usMatch;
    return `${year}-${month}-${day}`;
  }
  throw new Error("Invalid fellowship date");
}

function isPublicFellowshipDocument(documentPath: string): boolean {
  const lowerPath = documentPath.toLowerCase();
  const segments = documentPath.split("/");
  const name = segments.pop() ?? documentPath;
  const lowerName = name.toLowerCase();
  const extension = path.extname(lowerName);

  if (segments.some((segment) => segment === "" || segment === "." || segment === "..")) {
    return false;
  }
  if (!Object.prototype.hasOwnProperty.call(CONTENT_TYPES, extension)) {
    return false;
  }
  if (HIDDEN_PREFIXES.some((prefix) => lowerPath.startsWith(prefix))) {
    return false;
  }
  if (lowerName === FELLOWSHIP_GENERATED_TRANSCRIPT || lowerName === FELLOWSHIP_ANALYSIS_DOCUMENT.toLowerCase()) {
    return false;
  }
  if (lowerPath.includes(" - chat") || lowerPath.endsWith(" chat.md") || lowerPath.endsWith(" chat.txt")) {
    return false;
  }
  return true;
}

async function resolveFellowshipDocument(date: string, documentPath: string): Promise<{
  path: string;
  name: string;
  contentType: string;
  size: number;
  modifiedAt: Date;
} | null> {
  if (!isPublicFellowshipDocument(documentPath)) {
    return null;
  }

  const dataBaseDir = process.env.DATA_BASE_DIR ?? (await readRootEnvValue("DATA_BASE_DIR"));
  const docsRoot =
    process.env.FELLOWSHIP_DOCS_DIR ??
    (dataBaseDir ? path.join(dataBaseDir, "fellowship", "docs") : null);
  if (!docsRoot) {
    return null;
  }

  const folder = path.resolve(docsRoot, fellowshipDateToFolderName(date));
  const filePath = path.resolve(folder, documentPath);
  if (filePath !== folder && !filePath.startsWith(`${folder}${path.sep}`)) {
    return null;
  }

  try {
    const fileStat = await stat(filePath);
    if (!fileStat.isFile()) {
      return null;
    }
    const extension = path.extname(filePath).toLowerCase();
    return {
      path: filePath,
      name: path.basename(filePath),
      contentType: CONTENT_TYPES[extension] ?? "application/octet-stream",
      size: fileStat.size,
      modifiedAt: fileStat.mtime,
    };
  } catch {
    return null;
  }
}

function encodeContentDispositionFilename(filename: string): string {
  return encodeURIComponent(filename).replace(/['()]/g, (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`);
}

function parseRange(range: string | null, size: number): { start: number; end: number } | null | "invalid" {
  if (!range) {
    return null;
  }
  const match = range.match(/^bytes=(\d*)-(\d*)$/);
  if (!match) {
    return "invalid";
  }
  const [, rawStart, rawEnd] = match;
  if (!rawStart && !rawEnd) {
    return "invalid";
  }

  let start: number;
  let end: number;
  if (!rawStart) {
    const suffixLength = Number(rawEnd);
    if (!Number.isInteger(suffixLength) || suffixLength <= 0) {
      return "invalid";
    }
    start = Math.max(size - suffixLength, 0);
    end = size - 1;
  } else {
    start = Number(rawStart);
    end = rawEnd ? Number(rawEnd) : size - 1;
  }

  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start || start >= size) {
    return "invalid";
  }
  return { start, end: Math.min(end, size - 1) };
}

function baseHeaders(file: Awaited<ReturnType<typeof resolveFellowshipDocument>>): Headers {
  const headers = new Headers();
  if (!file) {
    return headers;
  }
  headers.set("accept-ranges", "bytes");
  headers.set("content-type", file.contentType);
  headers.set("last-modified", file.modifiedAt.toUTCString());
  headers.set("cache-control", "public, max-age=14400");
  headers.set("content-disposition", `attachment; filename*=UTF-8''${encodeContentDispositionFilename(file.name)}`);
  return headers;
}

export async function GET(request: NextRequest, { params }: { params: { date: string; documentPath: string[] } }) {
  const documentPath = params.documentPath.join("/");
  const file = await resolveFellowshipDocument(params.date, documentPath);
  if (!file) {
    return NextResponse.json({ detail: "Fellowship document not found" }, { status: 404 });
  }

  const headers = baseHeaders(file);
  const range = parseRange(request.headers.get("range"), file.size);
  if (range === "invalid") {
    headers.set("content-range", `bytes */${file.size}`);
    return new NextResponse(null, { status: 416, headers });
  }

  const start = range?.start ?? 0;
  const end = range?.end ?? file.size - 1;
  const contentLength = end - start + 1;
  headers.set("content-length", String(contentLength));
  if (range) {
    headers.set("content-range", `bytes ${start}-${end}/${file.size}`);
  }

  const stream = createReadStream(file.path, { start, end });
  return new NextResponse(Readable.toWeb(stream) as ReadableStream, {
    status: range ? 206 : 200,
    headers,
  });
}

export async function HEAD(request: NextRequest, { params }: { params: { date: string; documentPath: string[] } }) {
  const documentPath = params.documentPath.join("/");
  const file = await resolveFellowshipDocument(params.date, documentPath);
  if (!file) {
    return NextResponse.json({ detail: "Fellowship document not found" }, { status: 404 });
  }

  const headers = baseHeaders(file);
  const range = parseRange(request.headers.get("range"), file.size);
  if (range === "invalid") {
    headers.set("content-range", `bytes */${file.size}`);
    return new NextResponse(null, { status: 416, headers });
  }
  if (range) {
    headers.set("content-length", String(range.end - range.start + 1));
    headers.set("content-range", `bytes ${range.start}-${range.end}/${file.size}`);
    return new NextResponse(null, { status: 206, headers });
  }

  headers.set("content-length", String(file.size));
  return new NextResponse(null, { status: 200, headers });
}
