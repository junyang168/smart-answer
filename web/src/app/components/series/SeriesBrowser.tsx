// components/series/SeriesBrowser.tsx
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { SermonSeries } from "@/app/interfaces/article";
import { MyWork } from "./MyWork";
import { STATUS_CLASS, STATUS_LABEL, STATUS_ORDER, countByStatus, seriesName } from "./progress";

/** 同工的系列總覽：每個系列幾篇已發佈、幾篇編輯中、幾篇待認領。 */

const BAR_CLASS: Record<string, string> = {
  processing: "bg-sky-300",
  published: "bg-emerald-500",
  assigned: "bg-amber-400",
  ready: "bg-stone-200",
};

function Counts({ counts }: { counts: Record<string, number> }) {
  return (
    <span className="flex flex-wrap gap-2 text-sm">
      {STATUS_ORDER.map((status) => (
        <span key={status} className={`rounded-full px-2.5 py-0.5 ${STATUS_CLASS[status]}`}>
          {STATUS_LABEL[status]} {counts[status] ?? 0}
        </span>
      ))}
    </span>
  );
}

function Bar({ counts, total }: { counts: Record<string, number>; total: number }) {
  return (
    <span className="flex h-2 w-full overflow-hidden rounded-full bg-stone-100" aria-hidden="true">
      {STATUS_ORDER.map((status) => (
        <span key={status} className={BAR_CLASS[status]} style={{ width: `${total ? ((counts[status] ?? 0) / total) * 100 : 0}%` }} />
      ))}
    </span>
  );
}

export const SeriesBrowser = () => {
  const [seriesList, setSeriesList] = useState<SermonSeries[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/sc_api/sermon_series", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then(setSeriesList)
      .catch((status) => setError(`讀取失敗（${status}）`));
  }, []);

  if (error) return <p className="py-20 text-center text-rose-800">{error}</p>;
  if (!seriesList) return <p className="py-20 text-center text-stone-600" role="status">正在讀取…</p>;

  const all = seriesList.flatMap((series) => series.sermons);
  const totals = countByStatus(all);

  return (
    <div className="flex flex-col gap-6">
      <MyWork seriesList={seriesList} />

      <section className="flex flex-col gap-3 rounded-2xl border border-stone-200 bg-white p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="text-lg font-bold text-stone-900">全部 {all.length} 篇</h2>
          <Counts counts={totals} />
        </div>
        <Bar counts={totals} total={all.length} />
      </section>

      <ol className="overflow-hidden rounded-2xl border border-stone-200 bg-white">
        {seriesList.map((series) => {
          const counts = countByStatus(series.sermons);
          const { name, subtitle } = seriesName(series);
          return (
            <li key={series.id} className="border-t border-stone-100 first:border-t-0">
              <Link
                href={`/resources/series/${encodeURIComponent(series.id)}`}
                className="flex flex-col gap-2 px-5 py-4 hover:bg-stone-50 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-amber-700"
              >
                <span className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                  <span className="flex flex-col">
                    <span className="font-semibold text-stone-900">
                      {name}
                      <span className="ml-2 font-mono text-sm font-normal text-stone-500">{series.sermons.length} 篇</span>
                    </span>
                    {subtitle ? <span className="text-sm text-stone-500">{subtitle}</span> : null}
                  </span>
                  <Counts counts={counts} />
                </span>
                <Bar counts={counts} total={series.sermons.length} />
              </Link>
            </li>
          );
        })}
      </ol>
    </div>
  );
};
