"use client";

import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { SourceChips, type SourceCount } from "../navigation";

/**
 * 一卷书的各章。只列有讲道的章（负责人 2026-11-04）；每章写这一章讲什么（有按章
 * 页面的用那一页的标题），以及点进去是什么：逐节聆听、按段落原声；只有讲道的章不加标签。
 * 不写各来源几篇——听的人不在乎（负责人 2026-11-05）。
 */

type Chapter = {
  chapter: number;
  sources: SourceCount[];
  page: { kind: "chapter" | "passages"; source: string; title?: string; media?: "audio" | "video" } | null;
  /** 每個有按章頁面的來源一頁（教授釋經筆記另有 AI 朗讀的一頁）。 */
  pages?: { kind: "chapter" | "passages"; source: string; title?: string; media?: "audio" | "video"; ai_voice?: boolean }[];
  articles: { title: string; href: string }[];
};

type BookData = { slug: string; name: string; sources: SourceCount[]; chapters: Chapter[] };

export default function BookPage() {
  return (
    <Suspense fallback={null}>
      <BookChapters />
    </Suspense>
  );
}

// 標籤寫點進去能做什麼，不寫頁面的內部名稱；錄像叫觀看，錄音叫聆聽（負責人 2026-11-05）。
const pageLabel = (page: NonNullable<Chapter["page"]> & { ai_voice?: boolean }) =>
  page.kind === "passages"
    ? "按段落原聲"
    : page.ai_voice
      ? "AI 朗讀"
      : page.media === "video"
        ? "逐節觀看"
        : "逐節聆聽";

function BookChapters() {
  const slug = String(useParams()?.book ?? "");
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const source = params.get("source") ?? "";
  const [data, setData] = useState<BookData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`/api/public/repository/books/${slug}`, { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then(setData)
      .catch(() => setError("這卷書還沒有講道。"));
  }, [slug]);

  const choose = (name: string) => {
    router.replace(name ? `${pathname}?source=${encodeURIComponent(name)}` : pathname, { scroll: false });
  };

  const chapters = (data?.chapters ?? [])
    .map((chapter) => ({
      ...chapter,
      shown: source ? chapter.sources.filter((item) => item.name === source) : chapter.sources,
    }))
    .filter((chapter) => chapter.shown.length > 0);
  // 来源选了别的，就不说这一章有按章页面——那一页是另一个来源的。
  const pageFor = (chapter: Chapter) =>
    source
      ? (chapter.pages ?? []).find((page) => page.source === source) ??
        (chapter.page && chapter.page.source === source ? chapter.page : null)
      : chapter.page;
  // 沒選來源時，這一章每個來源的按章頁面都列出來（講道、教授釋經筆記的 AI 朗讀；負責人 2026-11-06）。
  const pagesFor = (chapter: Chapter) => {
    if (source) {
      const page = pageFor(chapter);
      return page ? [page] : [];
    }
    return chapter.pages?.length ? chapter.pages : chapter.page ? [chapter.page] : [];
  };

  return (
    <main className="min-h-screen bg-stone-50">
      <div className="mx-auto flex max-w-4xl flex-col gap-7 px-5 py-10 sm:px-8 sm:py-14">
        <nav aria-label="位置" className="flex flex-wrap gap-2 text-sm text-stone-600">
          <Link href={`/resources/wang-repository${source ? `?source=${encodeURIComponent(source)}` : ""}`} className="text-amber-800 hover:text-amber-900">
            講論文庫
          </Link>
          <span aria-hidden="true">/</span>
          <span>{data?.name ?? ""}</span>
        </nav>

        {error && <p className="text-rose-800">{error}</p>}
        {!data && !error && (
          <p className="text-stone-600" role="status">
            正在讀取…
          </p>
        )}

        {data && (
          <>
            <header className="flex flex-col gap-2">
              <h1 className="font-serif text-4xl font-bold leading-tight text-stone-950">{data.name}</h1>
              <p className="text-stone-600">
                {source ? `${source}：` : ""}
                {chapters.length} 章有講道。點一章，按經節次序聽教授的講解。
              </p>
            </header>

            <SourceChips sources={data.sources} picked={source} onPick={choose} />

            <ol className="overflow-hidden rounded-2xl border border-stone-200 bg-white">
              {chapters.map((chapter) => {
                // 一個來源一行：這一章有兩個來源的按章頁面（講道、教授釋經筆記的 AI 朗讀），就分兩行，
                // 各寫各的一句、各有各的按鈕，點哪一行就進哪個來源（負責人 2026-11-06）。
                const pages = pagesFor(chapter);
                const rows: { key: string; source: string; page: (typeof pages)[number] | null }[] = pages.length
                  ? pages.map((page) => ({ key: page.source, source: source || page.source, page }))
                  : [{ key: "", source, page: null }];
                return (
                  <li key={chapter.chapter} className="border-t border-stone-100 py-2 first:border-t-0">
                    {rows.map((row, index) => (
                      <Link
                        key={row.key}
                        href={`/resources/wang-repository/books/${data.slug}/${chapter.chapter}${row.source ? `?source=${encodeURIComponent(row.source)}` : ""}`}
                        className="grid min-h-11 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-5 py-2 text-stone-900 sm:grid-cols-[6rem_minmax(0,1fr)_auto] hover:bg-stone-50 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-amber-700"
                      >
                        {/* 手機上章號自成一行；第二個來源那一行不再空出章號的位置。 */}
                        <span className={`col-span-2 font-serif text-xl font-bold sm:col-span-1 ${index === 0 ? "" : "hidden sm:block"}`}>
                          {index === 0 ? `第 ${chapter.chapter} 章` : ""}
                        </span>
                        <span className="min-w-0 text-[15px] text-stone-700">
                          {/* 這一句是某個來源的講道講了什麼；沒選來源時寫出是哪個來源。 */}
                          {row.page?.title ? (source ? row.page.title : `${row.page.source}：${row.page.title}`) : ""}
                        </span>
                        <span className="flex flex-wrap justify-end gap-2">
                          {index === 0 && chapter.articles.length > 0 && (
                            <span className="rounded-full bg-amber-100 px-2.5 py-1 text-[13px] font-medium text-amber-900">綜合文章</span>
                          )}
                          {/* 沒有逐節聆聽的章不加標籤：「講道列表」什麼也沒告訴讀者（負責人 2026-11-05）。 */}
                          {row.page && (
                            <span
                              className={`rounded-full px-2.5 py-1 text-[13px] ${
                                row.page.kind === "chapter" ? "bg-stone-900 font-medium text-white" : "border border-stone-300 text-stone-700"
                              }`}
                            >
                              {pageLabel(row.page)}
                            </span>
                          )}
                        </span>
                      </Link>
                    ))}
                  </li>
                );
              })}
            </ol>
          </>
        )}
      </div>
    </main>
  );
}
