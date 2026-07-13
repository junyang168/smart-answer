"use client";

import Link from "next/link";
import { ReactNode } from "react";
import { useEffect, useState } from "react";
import { ArrowLeft, BookOpen, ExternalLink, FileText, Users } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { FellowshipDocument } from "@/app/types/fellowship";
import { PublicFellowshipEntry } from "@/app/types/publicFellowship";
import { isMarkdownDocument, toFellowshipDocumentHref } from "@/app/utils/fellowshipDocuments";

const MEET_RECORDINGS_FOLDER_ID = "19VF_eDRUkpBy0vc7YljpTFFPzgHiuTUX";

function teachingSourceLinks(entry: PublicFellowshipEntry) {
  return entry.sourceLinks.filter((source) => {
    const label = (source.label || "").trim().toLowerCase();
    const url = source.url || "";
    return (
      !url.includes(`/folders/${MEET_RECORDINGS_FOLDER_ID}`) &&
      !url.includes(`id=${MEET_RECORDINGS_FOLDER_ID}`) &&
      !["meet recordings", "google meet recordings", "recording folder", "recordings folder"].includes(label)
    );
  });
}

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
  const response = await fetch(
    `/api/sc_api/fellowships/${encodeURIComponent(date)}/documents`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    return [];
  }
  return response.json();
}

function isPublicFellowshipDocument(document: FellowshipDocument): boolean {
  const name = document.name;
  const lowerName = name.toLowerCase();
  const extension = lowerName.split(".").pop() ?? "";
  const hiddenPrefixes = ["audio/", "tmp/", "temp/", "cache/"];
  const publicExtensions = new Set(["md", "pptx", "mp4"]);

  if (hiddenPrefixes.some((prefix) => lowerName.startsWith(prefix))) {
    return false;
  }
  if (!publicExtensions.has(extension)) {
    return false;
  }
  if (lowerName === "recording.transcript.generated.md" || name === "主題與查經重點.md") {
    return false;
  }
  if (lowerName.includes(" - chat") || lowerName.endsWith(" chat.md") || lowerName.endsWith(" chat.txt")) {
    return false;
  }
  return true;
}

export function FellowshipDetail({ date }: { date: string }) {
  const [entry, setEntry] = useState<PublicFellowshipEntry | null>(null);
  const [documents, setDocuments] = useState<FellowshipDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const renderMarkdownListSection = (
    title: string,
    items: string[],
    emptyText: string,
    icon: ReactNode,
  ) => (
    <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex items-center gap-3 text-xl font-bold font-display text-gray-900">
        {icon}
        {title}
      </div>
      {items.length > 0 ? (
        <ul className="space-y-3">
          {items.map((item, index) => (
            <li key={index} className="rounded-md bg-gray-50 p-4">
              <div className="prose prose-slate max-w-none text-gray-700 lg:prose-lg prose-p:my-0">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{item}</ReactMarkdown>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-lg text-gray-500">{emptyText}</p>
      )}
    </section>
  );

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
    if (entry?.isoDate) {
      fetchDocuments(entry.isoDate).then(setDocuments).catch(() => setDocuments([]));
    } else {
      setDocuments([]);
    }
  }, [entry?.isoDate]);

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

  const publicDocuments = documents.filter(isPublicFellowshipDocument);
  const publicSourceLinks = teachingSourceLinks(entry);

  return (
    <article className="space-y-8">
      <Link href="/resources/fellowship" className="inline-flex items-center gap-2 text-base text-[#8B4513] hover:underline">
        <ArrowLeft className="h-4 w-4" />
        返回團契回顧
      </Link>

      <header className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <p className="text-base font-semibold text-gray-500">{entry.date}</p>
        <h1 className="mt-2 text-3xl font-bold font-display text-gray-900 lg:text-4xl">{entry.title || "團契查經"}</h1>
        <p className="mt-3 text-lg text-gray-600">
          {[entry.series, entry.sequence ? `第 ${entry.sequence} 講` : null, entry.host]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </header>

      {entry.summary && (
        <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
          <div className="mb-4 flex items-center gap-3 text-xl font-bold font-display text-gray-900">
            <Users className="h-6 w-6 text-[#8B4513]" />
            本次回顧
          </div>
          <div className="prose prose-slate max-w-none text-gray-700 lg:prose-lg prose-p:leading-relaxed">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{entry.summary}</ReactMarkdown>
          </div>
        </section>
      )}

      {renderMarkdownListSection(
        "學習重點",
        entry.keyLearnings ?? [],
        "此團契尚未整理公開學習重點。",
        <BookOpen className="h-6 w-6 text-[#8B4513]" />,
      )}

      {renderMarkdownListSection(
        "會眾問題",
        entry.audienceQuestions ?? [],
        "此團契尚未整理會眾問題。",
        <Users className="h-6 w-6 text-[#8B4513]" />,
      )}

      {renderMarkdownListSection(
        "會眾分享",
        entry.audienceSharings ?? [],
        "此團契尚未整理會眾分享。",
        <Users className="h-6 w-6 text-[#8B4513]" />,
      )}

      {renderMarkdownListSection(
        "帶領者回應",
        entry.leaderResponses ?? [],
        "此團契尚未整理帶領者回應。",
        <BookOpen className="h-6 w-6 text-[#8B4513]" />,
      )}

      <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <div className="mb-4 flex items-center gap-3 text-xl font-bold font-display text-gray-900">
          <ExternalLink className="h-6 w-6 text-[#8B4513]" />
          來源連結
        </div>
        {publicSourceLinks.length > 0 ? (
          <div className="grid gap-3 md:grid-cols-2">
            {publicSourceLinks.map((source, index) => (
              <a
                key={`${source.url}-${index}`}
                href={source.url}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-md border border-gray-200 p-4 text-base text-blue-700 hover:border-blue-200 hover:bg-blue-50"
              >
                {source.label || source.url}
              </a>
            ))}
          </div>
        ) : (
          <p className="text-lg text-gray-500">此團契尚未加入公開來源連結。</p>
        )}
      </section>

      <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <div className="mb-4 flex items-center gap-3 text-xl font-bold font-display text-gray-900">
          <FileText className="h-6 w-6 text-[#8B4513]" />
          團契文件
        </div>
        {publicDocuments.length > 0 ? (
          <div className="grid gap-3 md:grid-cols-2">
            {publicDocuments.map((document) => {
              const isMarkdown = isMarkdownDocument(document);
              return (
                <a
                  key={document.name}
                  href={toFellowshipDocumentHref(entry.isoDate, document)}
                  target={isMarkdown ? "_blank" : undefined}
                  rel={isMarkdown ? "noopener noreferrer" : undefined}
                  download={isMarkdown ? undefined : document.name.split("/").pop()}
                  className="rounded-md border border-gray-200 p-4 text-base text-blue-700 hover:border-blue-200 hover:bg-blue-50"
                >
                  {document.name}
                </a>
              );
            })}
          </div>
        ) : (
          <p className="text-lg text-gray-500">此團契目前沒有可下載文件。</p>
        )}
      </section>

      <section className="rounded-lg bg-[#8B4513] p-6 text-white">
        <h2 className="text-2xl font-bold font-display">想一起參與團契查經？</h2>
        <p className="mt-2 text-base text-white/90">
          歡迎透過聯絡頁面了解聚會時間與參與方式。
        </p>
        <Link
          href="/contact"
          className="mt-4 inline-block rounded-md bg-white px-4 py-2 text-base font-semibold text-[#8B4513] hover:bg-gray-100"
        >
          聯絡我們
        </Link>
      </section>
    </article>
  );
}
