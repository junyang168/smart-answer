"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { SourceChips, total, type SourceCount } from "./books/navigation";

/**
 * 文库入口：先选书卷和来源。
 *
 * 负责人 2026-11-04：读者从圣经书卷和来源（紐約靈命進深會、教授釋經筆記、達拉斯
 * 聖道教會……）来选；选好书卷列出章，再进按章页面。来源以后还会增加，所以来源
 * 清单从后端汇总的数据来，不写在这里。原来的「綜合文章／教授原聲」两排 tab 移到
 * `library`，从右上角进去。
 */

type Book = { slug: string; name: string; testament: "old" | "new"; sources: SourceCount[] };

export default function WangRepositoryPage() {
  return (
    <Suspense fallback={null}>
      <Books />
    </Suspense>
  );
}

function Books() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const source = params.get("source") ?? "";
  const [data, setData] = useState<{ sources: SourceCount[]; books: Book[] } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/public/repository/books", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then(setData)
      .catch(() => setError("讀取書卷失敗。"));
  }, []);

  const choose = (name: string) => {
    const next = new URLSearchParams(params.toString());
    if (name) next.set("source", name);
    else next.delete("source");
    router.replace(next.size ? `${pathname}?${next}` : pathname, { scroll: false });
  };

  const shown = (data?.books ?? [])
    .map((book) => ({
      ...book,
      count: source ? book.sources.find((item) => item.name === source)?.count ?? 0 : total(book.sources),
    }))
    .filter((book) => book.count > 0);

  return (
    <main className="min-h-screen bg-stone-50">
      <div className="mx-auto flex max-w-6xl flex-col gap-9 px-5 py-10 sm:px-8 sm:py-14">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex flex-col gap-2">
            <h1 className="font-serif text-4xl font-bold leading-tight text-stone-950">王守仁教授聖經講論文庫</h1>
            <p className="text-lg text-stone-600">選一卷書，聽教授按章講解。</p>
          </div>
          <nav className="flex flex-wrap gap-2.5" aria-label="其他入口">
            <Link
              href="/resources/wang-repository/library?view=topic"
              className="inline-flex min-h-11 items-center rounded-full border border-stone-300 bg-white px-4 text-sm text-stone-700 hover:bg-stone-100"
            >
              按講論主題
            </Link>
            <Link
              href="/resources/wang-repository/library?kind=article"
              className="inline-flex min-h-11 items-center rounded-full border border-stone-300 bg-white px-4 text-sm text-stone-700 hover:bg-stone-100"
            >
              綜合文章
            </Link>
          </nav>
        </header>

        {error && <p className="text-rose-800">{error}</p>}
        {!data && !error && (
          <p className="text-stone-600" role="status">
            正在讀取…
          </p>
        )}

        {data && (
          <>
            <section className="flex flex-col gap-3">
              <h2 className="text-sm font-bold tracking-widest text-stone-500">來源</h2>
              <SourceChips sources={data.sources} picked={source} onPick={choose} withCount />
            </section>

            {(["new", "old"] as const).map((testament) => {
              const books = shown.filter((book) => book.testament === testament);
              if (books.length === 0) return null;
              return (
                <section key={testament} className="flex flex-col gap-3.5">
                  <div className="flex items-baseline gap-3 border-b border-stone-200 pb-2">
                    <h2 className="font-serif text-2xl font-bold text-stone-900">{testament === "new" ? "新約" : "舊約"}</h2>
                    <span className="text-sm text-stone-600">{books.length} 卷有講道</span>
                  </div>
                  <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-2.5">
                    {books.map((book) => (
                      <Link
                        key={book.slug}
                        href={`/resources/wang-repository/books/${book.slug}${source ? `?source=${encodeURIComponent(source)}` : ""}`}
                        className="flex flex-col gap-1 rounded-xl border border-stone-200 bg-white px-4 py-3.5 text-stone-900 hover:border-amber-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-700"
                      >
                        <span className="font-serif text-lg font-bold">{book.name}</span>
                        <span className="text-sm text-stone-600">{book.count} 篇</span>
                      </Link>
                    ))}
                  </div>
                </section>
              );
            })}
          </>
        )}
      </div>
    </main>
  );
}
