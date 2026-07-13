import Link from "next/link";
import { ArrowLeft, FileText } from "lucide-react";
import { readFile } from "fs/promises";
import path from "path";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const FELLOWSHIP_ANALYSIS_DOCUMENT = "主題與查經重點.md";
const FELLOWSHIP_GENERATED_TRANSCRIPT = "recording.transcript.generated.md";
const RAW_BACKEND_BASE =
  process.env.SC_API_SERVICE_URL ??
  process.env.FULL_ARTICLE_SERVICE_URL ??
  process.env.NEXT_PUBLIC_FULL_ARTICLE_SERVICE_URL ??
  (process.env.NODE_ENV === "production" ? "http://127.0.0.1:8555" : "http://127.0.0.1:8222");
const BACKEND_BASE = RAW_BACKEND_BASE.replace(/\/$/, "");

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
      // Ignore missing env files; the HTTP fallback below still works.
    }
  }
  return null;
}

function fellowshipDateToFolderName(date: string): string {
  const isoMatch = date.match(/^\d{4}-\d{2}-\d{2}$/);
  if (isoMatch) {
    return date;
  }
  const usMatch = date.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if (usMatch) {
    const [, month, day, year] = usMatch;
    return `${year}-${month}-${day}`;
  }
  throw new Error("Invalid fellowship date");
}

function isPublicMarkdownDocument(documentPath: string): boolean {
  const lowerPath = documentPath.toLowerCase();
  const segments = documentPath.split("/");
  const name = segments.pop() ?? documentPath;
  const lowerName = name.toLowerCase();
  const hiddenPrefixes = ["audio/", "tmp/", "temp/", "cache/"];
  if (segments.some((segment) => segment === "" || segment === "." || segment === "..")) {
    return false;
  }
  if (!lowerPath.endsWith(".md")) {
    return false;
  }
  if (hiddenPrefixes.some((prefix) => lowerPath.startsWith(prefix))) {
    return false;
  }
  if (lowerName === FELLOWSHIP_GENERATED_TRANSCRIPT || name === FELLOWSHIP_ANALYSIS_DOCUMENT) {
    return false;
  }
  if (lowerPath.includes(" - chat") || lowerPath.endsWith(" chat.md") || lowerPath.endsWith(" chat.txt")) {
    return false;
  }
  return true;
}

function encodePathSegments(path: string): string {
  return path
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

async function readMarkdownDocumentFromDisk(date: string, documentPath: string): Promise<string | null> {
  if (!isPublicMarkdownDocument(documentPath)) {
    throw new Error("Unable to load fellowship document");
  }

  const dataBaseDir = process.env.DATA_BASE_DIR ?? (await readRootEnvValue("DATA_BASE_DIR"));
  const docsDir =
    process.env.FELLOWSHIP_DOCS_DIR ??
    (dataBaseDir ? path.join(dataBaseDir, "fellowship", "docs") : null);
  if (!docsDir) {
    return null;
  }

  const folder = path.resolve(docsDir, fellowshipDateToFolderName(date));
  const candidate = path.resolve(folder, documentPath);
  if (candidate !== folder && !candidate.startsWith(`${folder}${path.sep}`)) {
    throw new Error("Unable to load fellowship document");
  }

  try {
    return await readFile(candidate, "utf-8");
  } catch {
    return null;
  }
}

async function fetchMarkdownDocument(date: string, documentPath: string): Promise<string> {
  const diskContent = await readMarkdownDocumentFromDisk(date, documentPath);
  if (diskContent !== null) {
    return diskContent;
  }

  const url = `${BACKEND_BASE}/sc_api/fellowships/${encodeURIComponent(
    fellowshipDateToFolderName(date),
  )}/document-text/${encodePathSegments(documentPath)}`;
  const response = await fetch(url, {
    cache: "no-store",
    headers: { Accept: "text/markdown, text/plain;q=0.9" },
  });
  if (!response.ok) {
    throw new Error("Unable to load fellowship document");
  }
  const content = await response.arrayBuffer();
  return new TextDecoder("utf-8").decode(content);
}

export async function FellowshipMarkdownDocument({
  date,
  documentPath,
}: {
  date: string;
  documentPath: string;
}) {
  const documentName = documentPath.split("/").pop() || documentPath;
  let markdown = "";
  let error: string | null = null;
  try {
    markdown = await fetchMarkdownDocument(date, documentPath);
  } catch (err) {
    console.error("Unable to load fellowship document", { date, documentPath, error: err });
    error = "Unable to load fellowship document";
  }

  return (
    <article className="space-y-8">
      <Link href={`/resources/fellowship/${encodeURIComponent(date)}`} className="inline-flex items-center gap-2 text-base text-[#8B4513] hover:underline">
        <ArrowLeft className="h-4 w-4" />
        返回團契回顧
      </Link>

      <header className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <div className="flex items-center gap-3 text-gray-500">
          <FileText className="h-5 w-5 text-[#8B4513]" />
          <span className="text-base font-semibold">{date}</span>
        </div>
        <h1 className="mt-2 text-3xl font-bold font-display text-gray-900 lg:text-4xl">{documentName}</h1>
      </header>

      {error ? (
        <div className="rounded-md border border-red-200 bg-red-50 p-4 text-red-700">{error}</div>
      ) : (
        <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
          <div className="prose prose-slate max-w-none lg:prose-lg">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
          </div>
        </section>
      )}
    </article>
  );
}
