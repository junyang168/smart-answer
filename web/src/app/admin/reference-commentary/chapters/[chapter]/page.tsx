"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { API, SIZES, chapterHref, getJson, loadSize, pageHref, saveSize, type Chapter } from "../../types";

/**
 * 章節閱讀：一章連續讀下去。
 *
 * Pages are laid end to end in printed order; a small page marker at each
 * page start links to that page's scan and proofreading. On a phone this is
 * one column, and the reader can change the type size.
 */
export default function ChapterReading() {
  const params = useParams();
  const chapter = Number(Array.isArray(params.chapter) ? params.chapter[0] : params.chapter);
  const [data, setData] = useState<Chapter | null>(null);
  const [error, setError] = useState("");
  const [size, setSize] = useState(1);

  useEffect(() => setSize(loadSize()), []);
  useEffect(() => {
    setData(null);
    getJson<Chapter>(`${API}/chapters/${chapter}`)
      .then((value) => {
        setData(value);
        setError("");
      })
      .catch((reason: Error) => setError(reason.message));
  }, [chapter]);

  const changeSize = (delta: number) => {
    const next = Math.min(SIZES.length - 1, Math.max(0, size + delta));
    setSize(next);
    saveSize(next);
  };

  return (
    <main className="mx-auto max-w-2xl px-4 pb-20 pt-4">
      <nav className="sticky top-[env(safe-area-inset-top,0px)] z-10 -mx-4 mb-4 flex items-center justify-between gap-2 border-b border-slate-200 bg-white/95 px-4 py-2 text-sm backdrop-blur">
        <Link href="/admin/reference-commentary" className="text-sky-800">
          ← 書架
        </Link>
        <span className="font-medium text-slate-800">馬太福音 {chapter} 章</span>
        <span className="flex items-center gap-1">
          <button onClick={() => changeSize(-1)} aria-label="縮小字" className="rounded border border-slate-300 px-2 text-xs">
            A−
          </button>
          <button onClick={() => changeSize(1)} aria-label="放大字" className="rounded border border-slate-300 px-2 text-base">
            A+
          </button>
        </span>
      </nav>

      {error && <p className="text-sm text-rose-700">{error}</p>}
      {!data && !error && <p className="text-sm text-slate-400">讀取中…</p>}

      {data && (
        <>
          {data.missing_pages.length > 0 && (
            <p className="mb-4 rounded bg-amber-50 px-3 py-2 text-sm text-amber-800">
              這一章中間缺第 {data.missing_pages.join("、")} 頁，還沒掃。
            </p>
          )}
          <article className={`prose ${SIZES[size]} max-w-none font-serif prose-headings:font-sans prose-headings:text-slate-800`}>
            {data.pages.map((page) => (
              <section key={`${page.volume_id}-${page.printed_page}`}>
                <Link
                  href={pageHref(page.volume_id, page.printed_page)}
                  className="not-prose mb-2 mt-8 flex items-center gap-2 border-t border-slate-200 pt-2 font-sans text-xs text-slate-400 no-underline hover:text-sky-700"
                >
                  <span>p. {page.printed_page}</span>
                  {page.status === "proofread" ? (
                    <span className="text-emerald-600">已校對</span>
                  ) : (
                    <span>機器轉寫</span>
                  )}
                  {page.gaps > 0 && <span className="text-amber-600">{page.gaps} 處看不清</span>}
                </Link>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{page.text}</ReactMarkdown>
              </section>
            ))}
          </article>
          <nav className="mt-10 flex justify-between text-sm">
            {chapter > 1 ? <Link href={chapterHref(chapter - 1)} className="text-sky-800">← {chapter - 1} 章</Link> : <span />}
            {chapter < 28 ? <Link href={chapterHref(chapter + 1)} className="text-sky-800">{chapter + 1} 章 →</Link> : <span />}
          </nav>
        </>
      )}
    </main>
  );
}
