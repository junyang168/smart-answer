"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

type ExpositionBook = { slug: string; name: string; available: boolean };

export default function WangRepositoryPage() {
  return (
    <Suspense fallback={null}>
      <Books />
    </Suspense>
  );
}

function Books() {
  const source = useSearchParams().get("source") ?? "";
  const [books, setBooks] = useState<ExpositionBook[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/public/repository/books", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then((data) => setBooks(data.exposition_books))
      .catch(() => setError("讀取書卷失敗。"));
  }, []);

  const available = books?.filter((book) => book.available) ?? [];
  const inPreparation = books?.filter((book) => !book.available) ?? [];

  return (
    <main className="min-h-screen bg-stone-50">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-5 py-7 sm:gap-8 sm:px-8 sm:py-14">
        <header className="flex flex-col gap-2">
          <h1 className="font-serif text-3xl font-bold leading-tight text-stone-950 sm:text-4xl">王守仁教授聖經講論文庫</h1>
          <p className="text-stone-600">選擇經卷，聆聽已上架的釋經系列。</p>
        </header>

        {error && <p className="text-rose-800">{error}</p>}
        {!books && !error && <p className="text-stone-600" role="status">正在讀取…</p>}

        {available.length > 0 && (
          <section aria-labelledby="available-books" className="space-y-3">
            <h2 id="available-books" className="font-serif text-2xl font-bold text-stone-900">已開放</h2>
            <div className="grid gap-2.5 sm:grid-cols-3 lg:grid-cols-4">
              {available.map((book) => (
                <Link
                  key={book.slug}
                  href={`/resources/wang-repository/books/${book.slug}${source ? `?source=${encodeURIComponent(source)}` : ""}`}
                  className="flex min-h-11 items-center justify-between gap-2 rounded-xl border border-stone-200 bg-white px-3 py-2.5 font-serif text-lg font-bold text-stone-900 hover:border-amber-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-700"
                >
                  {book.name}
                  <span aria-hidden="true" className="text-sm text-amber-900">→</span>
                </Link>
              ))}
            </div>
          </section>
        )}

        {inPreparation.length > 0 && (
          <section aria-labelledby="preparation-books" className="space-y-3">
            <h2 id="preparation-books" className="font-serif text-lg font-bold text-stone-700">整理校對中</h2>
            <ul className="grid grid-cols-2 gap-2.5 sm:grid-cols-4 lg:grid-cols-6">
              {inPreparation.map((book) => (
                <li key={book.slug} className="rounded-lg border border-stone-200 bg-stone-100 px-3 py-2.5 text-stone-600">
                  {book.name}
                </li>
              ))}
            </ul>
          </section>
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
