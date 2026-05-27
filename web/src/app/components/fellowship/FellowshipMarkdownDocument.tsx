"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { signIn, useSession } from "next-auth/react";
import { ArrowLeft, FileText, Lock } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

async function fetchMarkdownDocument(date: string, documentPath: string): Promise<string> {
  const response = await fetch(
    `/api/admin/fellowships/${encodeURIComponent(date)}/documents/${encodeURIComponent(documentPath)}`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error("Unable to load fellowship document");
  }
  return response.text();
}

export function FellowshipMarkdownDocument({
  date,
  documentPath,
}: {
  date: string;
  documentPath: string;
}) {
  const { status } = useSession();
  const [markdown, setMarkdown] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const documentName = useMemo(() => documentPath.split("/").pop() || documentPath, [documentPath]);

  useEffect(() => {
    if (status !== "authenticated") {
      setLoading(false);
      return;
    }

    setLoading(true);
    fetchMarkdownDocument(date, documentPath)
      .then((content) => {
        setMarkdown(content);
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "載入團契文件失敗"))
      .finally(() => setLoading(false));
  }, [date, documentPath, status]);

  if (status === "loading" || loading) {
    return <div className="py-16 text-center text-gray-500">正在載入團契文件...</div>;
  }

  if (status !== "authenticated") {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-8 text-center shadow-sm">
        <Lock className="mx-auto h-8 w-8 text-[#8B4513]" />
        <h1 className="mt-4 text-2xl font-bold font-display text-gray-900">需要登入</h1>
        <p className="mt-2 text-gray-600">團契文件僅提供已登入使用者查看。</p>
        <button
          type="button"
          onClick={() => signIn("google")}
          className="mt-5 rounded-md bg-[#8B4513] px-4 py-2 font-semibold text-white hover:bg-[#6f3710]"
        >
          使用 Google 登入
        </button>
      </div>
    );
  }

  if (error) {
    return <div className="rounded-md border border-red-200 bg-red-50 p-4 text-red-700">{error}</div>;
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

      <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <div className="prose prose-slate max-w-none lg:prose-lg">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
        </div>
      </section>
    </article>
  );
}
