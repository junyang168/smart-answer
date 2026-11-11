// components/series/SeriesPlayerView.tsx
"use client";

import Link from "next/link";
import { notFound, useParams, useRouter, useSearchParams, usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Sermon, SermonSeries } from "@/app/interfaces/article";
import { STATUS_CLASS, STATUS_LABEL, STATUS_ORDER, countByStatus, day, deliveredOn, seriesName, sermonEditorHref, seriesIdFromRoute } from "./progress";

/**
 * 同工的系列校對頁：按系列次序列出講道日期、狀態、認領人、發佈人與發佈日期。
 * 點講道進編輯器（在那裡認領、校對、發佈），不進給讀者的講道頁。
 */

type Row = Sermon & { deliver_date?: string | null };

export const SeriesPlayerView = () => {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const seriesId = seriesIdFromRoute(String(params.seriesId ?? ""));
  const filter = searchParams.get("status") ?? "";
  const [series, setSeries] = useState<SermonSeries | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/sc_api/sermon_series", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then((all: SermonSeries[]) => {
        const found = all.find((item) => item.id === seriesId);
        if (found) setSeries(found);
        else setMissing(true);
      })
      .catch((status) => setError(`讀取失敗（${status}）`));
  }, [seriesId]);

  if (missing) notFound();
  if (error) return <p className="py-20 text-center text-rose-800">{error}</p>;
  if (!series) return <p className="py-20 text-center text-stone-600" role="status">正在讀取…</p>;

  const counts = countByStatus(series.sermons);
  const { name, subtitle } = seriesName(series);
  const rows = series.sermons
    .map((sermon, index) => ({ sermon: sermon as Row, order: index + 1 }))
    .filter(({ sermon }) => !filter || sermon.status === filter);

  const choose = (status: string) =>
    router.replace(status ? `${pathname}?status=${status}` : pathname, { scroll: false });

  const chip = (status: string, label: string) => (
    <button
      key={status || "all"}
      type="button"
      onClick={() => choose(status)}
      aria-pressed={filter === status}
      className={`rounded-full border px-3 py-1 text-sm ${
        filter === status ? "border-stone-900 bg-stone-900 text-white" : "border-stone-300 text-stone-700 hover:bg-stone-100"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold text-stone-950">{name}</h1>
          {subtitle ? <p className="mt-1 text-stone-600">{subtitle}</p> : null}
        </div>
      </header>

      <div className="flex flex-wrap gap-2" role="group" aria-label="按狀態篩選">
        {chip("", `全部 ${series.sermons.length}`)}
        {STATUS_ORDER.map((status) => chip(status, `${STATUS_LABEL[status]} ${counts[status] ?? 0}`))}
      </div>

      <div className="overflow-x-auto rounded-2xl border border-stone-200 bg-white">
        <table className="w-full min-w-[800px] text-left text-sm">
          <thead className="bg-stone-50 text-stone-600">
            <tr>
              <th className="px-4 py-3 font-medium">#</th>
              <th className="px-4 py-3 font-medium">講道</th>
              <th className="px-4 py-3 font-medium">講道日期</th>
              <th className="px-4 py-3 font-medium">狀態</th>
              <th className="px-4 py-3 font-medium">認領人</th>
              <th className="px-4 py-3 font-medium">發佈人</th>
              <th className="px-4 py-3 font-medium">最近發佈日期</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ sermon, order }) => (
              <tr key={sermon.item || sermon.id} className="border-t border-stone-100">
                <td className="px-4 py-3 font-mono text-stone-500">{order}</td>
                <td className="px-4 py-3">
                  <Link
                    href={sermonEditorHref(sermon)}
                    className="font-medium text-stone-900 hover:text-amber-800 hover:underline"
                  >
                    {sermon.title}
                  </Link>
                </td>
                <td className="whitespace-nowrap px-4 py-3 font-mono text-stone-700">{deliveredOn(sermon)}</td>
                <td className="px-4 py-3">
                  <span className={`whitespace-nowrap rounded-full px-2.5 py-0.5 ${STATUS_CLASS[sermon.status] ?? "bg-stone-100 text-stone-700"}`}>
                    {STATUS_LABEL[sermon.status] ?? sermon.status}
                  </span>
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-stone-700">{sermon.assigned_to_name || "未認領"}</td>
                <td className="whitespace-nowrap px-4 py-3 text-stone-700">{sermon.published_by_name || "—"}</td>
                <td className="whitespace-nowrap px-4 py-3 font-mono text-stone-700">
                  {day(sermon.published_date) || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
