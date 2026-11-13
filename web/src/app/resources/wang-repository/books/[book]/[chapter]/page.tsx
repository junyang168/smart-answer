"use client";

import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import OriginalAudio from "../../../audio/OriginalAudio";

/**
 * 一章，按来源分 tab。有按章页面的来源放按章页面（太 5 的 NYSC 系列）；只有按
 * 段落原声页的放那几页的链接；其余列出讲道或笔记，链到它们自己的页面。
 */

type Item = { kind: "sermon" | "notes"; id: string; title: string; date: string | null; href: string };
type Page =
  | { kind: "chapter"; source: string; passage: string }
  | { kind: "passages"; source: string; passages: { passage: string; label: string; href: string }[] };
type Source = { name: string; count: number; unit: string; items: Item[]; page?: Page };
type ChapterData = {
  slug: string;
  name: string;
  chapter: number;
  sources: Source[];
};

export default function ChapterPage() {
  return (
    <Suspense fallback={null}>
      <Chapter />
    </Suspense>
  );
}

function Chapter() {
  const routeParams = useParams();
  const slug = String(routeParams?.book ?? "");
  const number = String(routeParams?.chapter ?? "");
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [data, setData] = useState<ChapterData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`/api/public/repository/books/${slug}/${number}`, { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then(setData)
      .catch(() => setError("這一章還沒有講道。"));
  }, [slug, number]);

  const wanted = params.get("source") ?? "";
  // 读者在前面选了来源就用它；没选，先给有按章页面的来源。
  const current =
    data?.sources.find((item) => item.name === wanted) ??
    data?.sources.find((item) => item.page?.kind === "chapter") ??
    data?.sources[0];

  const fixed = Boolean(wanted) && data?.sources.some((item) => item.name === wanted);
  const choose = (name: string) => router.replace(`${pathname}?source=${encodeURIComponent(name)}`, { scroll: false });

  return (
    <main className="min-h-screen bg-stone-50">
      <div className="mx-auto flex max-w-4xl flex-col gap-6 px-5 py-10 sm:px-8 sm:py-14">
        <nav aria-label="位置" className="flex flex-wrap gap-2 text-sm text-stone-600">
          <Link href="/resources/wang-repository" className="text-amber-800 hover:text-amber-900">
            講論文庫
          </Link>
          <span aria-hidden="true">/</span>
          <Link
            href={`/resources/wang-repository/books/${slug}${wanted ? `?source=${encodeURIComponent(wanted)}` : ""}`}
            className="text-amber-800 hover:text-amber-900"
          >
            {data?.name ?? ""}
          </Link>
          <span aria-hidden="true">/</span>
          <span>第 {number} 章</span>
        </nav>

        {error && <p className="text-rose-800">{error}</p>}
        {!data && !error && (
          <p className="text-stone-600" role="status">
            正在讀取…
          </p>
        )}

        {data && current && (
          <>
            <h1 className="font-serif text-4xl font-bold leading-tight text-stone-950">
              {data.name} 第 {data.chapter} 章
            </h1>

            {/* 網址已經指定來源（從書卷頁選了來源進來），就不必再給來源的分頁。 */}
            {fixed ? (
              <p className="-mt-3 text-[15px] text-stone-600">{current.name}</p>
            ) : (
            <div role="tablist" aria-label="來源" className="flex flex-wrap gap-1 border-b border-stone-200">
              {data.sources.map((source) => {
                const on = source.name === current.name;
                return (
                  <button
                    key={source.name}
                    type="button"
                    role="tab"
                    aria-selected={on}
                    onClick={() => choose(source.name)}
                    className={`-mb-px inline-flex min-h-11 items-center gap-1.5 border-b-2 px-4 text-[15px] ${
                      on ? "border-amber-800 font-bold text-stone-900" : "border-transparent text-stone-600 hover:text-stone-900"
                    }`}
                  >
                    {source.name}
                    <span className="font-mono text-[13px] text-stone-500">
                      {source.count} {source.unit}
                    </span>
                  </button>
                );
              })}
            </div>
            )}

            {slug === "mat" && number === "17" && current.name === "紐約靈命進深會" && (
              <Link
                className="w-fit text-sm font-medium text-amber-900 underline-offset-4 hover:underline"
                href={`/resources/wang-repository/books/mat/16/listen?source=${encodeURIComponent(current.name)}&section=mat-16-28&recording=SRC-2016_NYSC_3-f35be4755f9b&at=2391.69`}
              >
                登山變像（太 16:28–17:2）
              </Link>
            )}

            {current.page?.kind === "chapter" ? (
              <OriginalAudio
                key={`${current.name}-${current.page.passage}`}
                passage={current.page.passage}
                source={current.name}
                embedded
                listenHref={(section, cue) =>
                  `/resources/wang-repository/books/${slug}/${number}/listen?source=${encodeURIComponent(current.name)}&section=${section}${cue ? `&recording=${encodeURIComponent(cue.sourceId)}&at=${cue.at}` : ""}`
                }
              />
            ) : (
              <div className="flex flex-col gap-4">
                {current.page?.kind === "passages" && (
                  <section className="flex flex-col gap-2">
                    <h2 className="text-sm font-bold text-stone-600">按段落聽原聲</h2>
                    <ul className="overflow-hidden rounded-2xl border border-stone-200 bg-white">
                      {current.page.passages.map((passage) => (
                        <li key={passage.passage} className="border-t border-stone-100 first:border-t-0">
                          <Link href={passage.href} className="flex min-h-11 items-center px-5 py-3 font-semibold text-stone-900 hover:bg-stone-50">
                            {passage.label}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
                <section className="flex flex-col gap-2">
                  <h2 className="text-sm font-bold text-stone-600">
                    {current.items[0]?.kind === "notes" ? "釋經筆記" : "講道"}（這一章還沒有逐節聆聽）
                  </h2>
                  <ul className="overflow-hidden rounded-2xl border border-stone-200 bg-white">
                    {current.items.map((item) => (
                      <li key={item.id} className="border-t border-stone-100 first:border-t-0">
                        <Link
                          href={item.href}
                          className="flex min-h-11 flex-wrap items-baseline gap-x-4 gap-y-1 px-5 py-3 text-stone-900 hover:bg-stone-50"
                        >
                          <span className="flex-1 font-semibold">{item.title}</span>
                          {item.date && <span className="font-mono text-[13px] text-stone-500">{item.date}</span>}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </section>
              </div>
            )}
          </>
        )}
      </div>
    </main>
  );
}
