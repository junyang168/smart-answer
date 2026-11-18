"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type Lecture = { id: string; title: string };

/** 已整理好論證的講次。點一講，讀他的論點、聽他的原話。 */
export default function LecturesPage() {
  const [lectures, setLectures] = useState<Lecture[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/admin/lecture-argument/lectures", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`讀取失敗（${r.status}）`))))
      .then((data) => setLectures(data.lectures))
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  return (
    <main className="mx-auto max-w-3xl px-4 py-6">
      <h1 className="text-2xl font-bold text-slate-900">講次的論證</h1>
      <p className="mt-2 text-sm leading-6 text-slate-600">
        每一講整理出他講的幾個論點、每一點的理由，以及每條理由是他的哪幾句話。點進去可以邊讀邊聽他的錄音。
      </p>
      {error && <p className="mt-6 text-rose-700">{error}</p>}
      {!lectures && !error && <p className="mt-6 text-slate-400">載入中…</p>}
      <ul className="mt-6 divide-y divide-slate-200 rounded-xl border border-slate-200 bg-white">
        {lectures?.map((l) => (
          <li key={l.id}>
            <Link href={`/admin/wang/lectures/${encodeURIComponent(l.id)}`} className="block px-4 py-3 text-slate-800 hover:bg-slate-50">
              {l.title}
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
