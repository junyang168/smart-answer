"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type SeriesItem = {
  id: string;
  title: string;
  lecture_count: number;
  unit_count: number;
  ready_count: number;
  loaded_count?: number;
  approved_count: number;
};

export default function SeriesListPage() {
  const [items, setItems] = useState<SeriesItem[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/admin/lecture-argument/series", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`讀取失敗（${r.status}）`))))
      .then((data) => setItems(data.series))
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  return (
    <main className="mx-auto max-w-3xl px-4 py-6">
      <h1 className="text-2xl font-bold text-slate-900">系列的論證（按邏輯單元）</h1>
      <p className="mt-2 text-sm leading-6 text-slate-600">
        以整套系列為單位切分無重疊的邏輯單元（跨講不斷裂）。點進系列後，可逐個單元讀論證、聽原音並做人工驗收。
      </p>
      {error && <p className="mt-6 text-rose-700">{error}</p>}
      {!items && !error && <p className="mt-6 text-slate-400">載入中…</p>}
      {items && items.length === 0 && <p className="mt-6 text-slate-500">目前尚無已切分的系列。</p>}
      <ul className="mt-6 divide-y divide-slate-200 rounded-xl border border-slate-200 bg-white">
        {items?.map((s) => (
          <li key={s.id}>
            <Link href={`/admin/wang/series/${encodeURIComponent(s.id)}`} className="block px-4 py-4 hover:bg-slate-50">
              <div className="flex items-center justify-between gap-3">
                <span className="font-semibold text-slate-900">{s.title}</span>
                <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-700">
                  {(s.loaded_count ?? 0) > 0
                    ? `${s.loaded_count} / ${s.unit_count} 單元已入庫`
                    : `${s.ready_count} / ${s.unit_count} 單元已生成`}
                </span>
              </div>
              <p className="mt-1 text-xs text-slate-500">
                共 {s.lecture_count} 講 · {s.unit_count} 個邏輯單元 · 已入庫 {s.loaded_count ?? 0} 個
                {s.approved_count > 0 ? ` · 已人工通過 ${s.approved_count} 個` : ""}
              </p>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
