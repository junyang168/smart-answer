"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useSession } from "next-auth/react";
import { ArrowLeft, BookOpen, ExternalLink, FileText, Lock, Users } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { FellowshipDocument } from "@/app/types/fellowship";
import { PublicFellowshipEntry } from "@/app/types/publicFellowship";

async function fetchFellowship(date: string): Promise<PublicFellowshipEntry> {
  const response = await fetch(`/api/sc_api/fellowships/${encodeURIComponent(date)}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error("Unable to load fellowship detail");
  }
  return response.json();
}

async function fetchDocuments(date: string): Promise<FellowshipDocument[]> {
  const response = await fetch(`/api/admin/fellowships/${encodeURIComponent(date)}/documents`, {
    cache: "no-store",
  });
  if (!response.ok) {
    return [];
  }
  return response.json();
}

function toProxyDocumentUrl(url: string): string {
  return url.startsWith("/admin/") ? `/api${url}` : url;
}

export function FellowshipDetail({ date }: { date: string }) {
  const { status } = useSession();
  const [entry, setEntry] = useState<PublicFellowshipEntry | null>(null);
  const [documents, setDocuments] = useState<FellowshipDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchFellowship(date)
      .then((data) => {
        setEntry(data);
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "載入團契回顧失敗"))
      .finally(() => setLoading(false));
  }, [date]);

  useEffect(() => {
    if (status === "authenticated" && entry?.date) {
      fetchDocuments(entry.date).then(setDocuments).catch(() => setDocuments([]));
    } else {
      setDocuments([]);
    }
  }, [entry?.date, status]);

  if (loading) {
    return <div className="py-16 text-center text-gray-500">正在載入團契回顧...</div>;
  }

  if (error || !entry) {
    return (
      <div className="rounded-md border border-red-200 bg-red-50 p-4 text-red-700">
        {error || "找不到此團契回顧"}
      </div>
    );
  }

  return (
    <article className="space-y-8">
      <Link href="/resources/fellowship" className="inline-flex items-center gap-2 text-sm text-[#8B4513] hover:underline">
        <ArrowLeft className="h-4 w-4" />
        返回團契回顧
      </Link>

      <header className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <p className="text-sm font-semibold text-gray-500">{entry.date}</p>
        <h1 className="mt-2 text-3xl font-bold text-gray-900 md:text-4xl">{entry.title || "團契查經"}</h1>
        <p className="mt-3 text-gray-600">
          {[entry.series, entry.sequence ? `第 ${entry.sequence} 講` : null, entry.host]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </header>

      {entry.summary && (
        <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
          <div className="mb-3 flex items-center gap-2 text-lg font-semibold text-gray-900">
            <Users className="h-5 w-5 text-[#8B4513]" />
            本次回顧
          </div>
          <div className="prose prose-sm max-w-none text-gray-700 prose-p:leading-7">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{entry.summary}</ReactMarkdown>
          </div>
        </section>
      )}

      <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <div className="mb-4 flex items-center gap-2 text-lg font-semibold text-gray-900">
          <BookOpen className="h-5 w-5 text-[#8B4513]" />
          學習重點
        </div>
        {entry.keyLearnings.length > 0 ? (
          <ul className="space-y-3">
            {entry.keyLearnings.map((learning, index) => (
              <li key={index} className="rounded-md bg-gray-50 p-4">
                <div className="prose prose-sm max-w-none text-gray-700 prose-p:my-0">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{learning}</ReactMarkdown>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-gray-500">此團契尚未整理公開學習重點。</p>
        )}
      </section>

      <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <div className="mb-4 flex items-center gap-2 text-lg font-semibold text-gray-900">
          <ExternalLink className="h-5 w-5 text-[#8B4513]" />
          來源連結
        </div>
        {entry.sourceLinks.length > 0 ? (
          <div className="grid gap-3 md:grid-cols-2">
            {entry.sourceLinks.map((source, index) => (
              <a
                key={`${source.url}-${index}`}
                href={source.url}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-md border border-gray-200 p-4 text-sm text-blue-700 hover:border-blue-200 hover:bg-blue-50"
              >
                {source.label || source.url}
              </a>
            ))}
          </div>
        ) : (
          <p className="text-gray-500">此團契尚未加入公開來源連結。</p>
        )}
      </section>

      <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <div className="mb-4 flex items-center gap-2 text-lg font-semibold text-gray-900">
          <FileText className="h-5 w-5 text-[#8B4513]" />
          團契文件
        </div>
        {status === "authenticated" ? (
          documents.length > 0 ? (
            <div className="grid gap-3 md:grid-cols-2">
              {documents.map((document) => (
                <a
                  key={document.name}
                  href={toProxyDocumentUrl(document.url)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-md border border-gray-200 p-4 text-sm text-blue-700 hover:border-blue-200 hover:bg-blue-50"
                >
                  {document.name}
                </a>
              ))}
            </div>
          ) : (
            <p className="text-gray-500">此團契目前沒有可下載文件。</p>
          )
        ) : (
          <div className="flex items-center gap-2 rounded-md bg-gray-50 p-4 text-sm text-gray-600">
            <Lock className="h-4 w-4" />
            團契文件僅提供已登入使用者查看。公開頁面仍可閱讀學習重點與來源連結。
          </div>
        )}
      </section>

      <section className="rounded-lg bg-[#8B4513] p-6 text-white">
        <h2 className="text-xl font-bold">想一起參與團契查經？</h2>
        <p className="mt-2 text-sm text-white/90">
          歡迎透過聯絡頁面了解聚會時間與參與方式。
        </p>
        <Link
          href="/contact"
          className="mt-4 inline-block rounded-md bg-white px-4 py-2 text-sm font-semibold text-[#8B4513] hover:bg-gray-100"
        >
          聯絡我們
        </Link>
      </section>
    </article>
  );
}
