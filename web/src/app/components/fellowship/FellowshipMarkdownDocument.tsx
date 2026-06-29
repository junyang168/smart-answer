import Link from "next/link";
import { ArrowLeft, FileText } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const RAW_BACKEND_BASE =
  process.env.SC_API_SERVICE_URL ??
  process.env.FULL_ARTICLE_SERVICE_URL ??
  process.env.NEXT_PUBLIC_FULL_ARTICLE_SERVICE_URL ??
  (process.env.NODE_ENV === "production" ? "http://127.0.0.1:8555" : "http://127.0.0.1:8222");
const BACKEND_BASE = RAW_BACKEND_BASE.replace(/\/$/, "");

function encodePathSegments(path: string): string {
  return path
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

async function fetchMarkdownDocument(date: string, documentPath: string): Promise<string> {
  const url = `${BACKEND_BASE}/sc_api/fellowships/${encodeURIComponent(date)}/documents/${encodePathSegments(documentPath)}`;
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error("Unable to load fellowship document");
  }
  return response.text();
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
    error = err instanceof Error ? err.message : "載入團契文件失敗";
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
