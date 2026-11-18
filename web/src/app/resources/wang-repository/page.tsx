"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { SourceChips, type SourceCount } from "./books/navigation";

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

  const shown = (data?.books ?? []).filter((book) =>
    book.sources.some((item) => item.count > 0 && (!source || item.name === source)),
  );

  return (
    <main className="min-h-screen bg-stone-50">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-5 py-7 sm:gap-8 sm:px-8 sm:py-14">
        <header className="flex flex-col gap-2">
          <h1 className="font-serif text-3xl font-bold leading-tight text-stone-950 sm:text-4xl">王守仁教授聖經講論文庫</h1>
          <p className="text-stone-600">選擇經卷，查看講道原聲和釋經資料。</p>
        </header>

        {error && <p className="text-rose-800">{error}</p>}
        {!data && !error && (
          <p className="text-stone-600" role="status">
            正在讀取…
          </p>
        )}

        {data && (
          <>
            {source && (
              <div className="flex flex-wrap items-center justify-between gap-x-4 rounded-lg bg-stone-100 px-3 text-sm">
                <p className="py-2 text-stone-700">目前來源 · {source}</p>
                <button type="button" onClick={() => choose("")} className="min-h-11 text-amber-900 underline underline-offset-4">
                  清除篩選
                </button>
              </div>
            )}

            {(["new", "old"] as const).map((testament) => {
              const books = shown.filter((book) => book.testament === testament);
              if (books.length === 0) return null;
              return (
                <section key={testament} className="flex flex-col gap-3.5">
                  <div className="flex items-baseline gap-3 border-b border-stone-200 pb-2">
                    <h2 className="font-serif text-2xl font-bold text-stone-900">{testament === "new" ? "新約" : "舊約"}</h2>
                  </div>
                  <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4 lg:grid-cols-6">
                    {books.map((book) => (
                      <Link
                        key={book.slug}
                        href={`/resources/wang-repository/books/${book.slug}${source ? `?source=${encodeURIComponent(source)}` : ""}`}
                        className="flex min-h-11 items-center rounded-xl border border-stone-200 bg-white px-3 py-2.5 font-serif text-lg font-bold text-stone-900 hover:border-amber-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-700"
                      >
                        {book.name}
                      </Link>
                    ))}
                  </div>
                </section>
              );
            })}

            <details className="text-sm text-stone-700">
              <summary className="min-h-11 w-fit cursor-pointer py-3 font-medium text-amber-900 hover:underline">篩選講道與筆記來源</summary>
              <SourceChips sources={data.sources} picked={source} onPick={choose} />
            </details>
          </>
        )}
        <nav className="flex flex-wrap gap-x-5" aria-label="其他入口">
          <Link href="/resources/wang-repository/library?view=topic" className="inline-flex min-h-11 items-center text-sm text-amber-900 underline underline-offset-4">
            按講論主題
          </Link>
          <Link href="/resources/wang-repository/library?kind=article" className="inline-flex min-h-11 items-center text-sm text-amber-900 underline underline-offset-4">
            綜合文章
          </Link>
        </nav>
      </div>
    </main>
  );
}
