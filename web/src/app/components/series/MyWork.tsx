// components/series/MyWork.tsx
"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useSession } from "next-auth/react";
import { Sermon, SermonSeries } from "@/app/interfaces/article";
import { STATUS_CLASS, STATUS_LABEL, day, seriesName, sermonEditorHref } from "./progress";

/**
 * 「我的講道」：登入的同工若認領過講道，顯示自己發佈了幾篇、還在編輯幾篇；
 * 點一個數字列出那些講道，點標題進編輯頁（負責人 2026-11-06）。
 */

const KINDS = ["published", "assigned"] as const;
type Kind = (typeof KINDS)[number];

export function MyWork({ seriesList }: { seriesList: SermonSeries[] }) {
  const { data: session } = useSession();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const email = session?.user?.email?.toLowerCase();
  if (!email) return null;

  const mine: Record<Kind, { sermon: Sermon; series: SermonSeries }[]> = { published: [], assigned: [] };
  for (const series of seriesList) {
    for (const sermon of series.sermons) {
      if (sermon.assigned_to?.toLowerCase() !== email) continue;
      if (sermon.status === "published" || sermon.status === "assigned") mine[sermon.status].push({ sermon, series });
    }
  }
  if (mine.published.length + mine.assigned.length === 0) return null;

  // 已發佈的按發佈日期新到舊；編輯中的按認領日期新到舊。
  mine.published.sort((a, b) => (b.sermon.published_date ?? "").localeCompare(a.sermon.published_date ?? ""));
  mine.assigned.sort((a, b) => (b.sermon.assigned_to_date ?? "").localeCompare(a.sermon.assigned_to_date ?? ""));

  const picked = params.get("mine") as Kind | null;
  const open = picked && KINDS.includes(picked) ? picked : null;
  const choose = (kind: Kind) => {
    const next = new URLSearchParams(params.toString());
    if (open === kind) next.delete("mine");
    else next.set("mine", kind);
    const query = next.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  };

  const rows = open ? mine[open] : [];

  return (
    <section className="flex flex-col gap-3 rounded-2xl border border-amber-200 bg-amber-50/60 p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-lg font-bold text-stone-900">我的講道</h2>
        <span className="flex flex-wrap gap-2">
          {KINDS.map((kind) => (
            <button
              key={kind}
              type="button"
              onClick={() => choose(kind)}
              aria-pressed={open === kind}
              aria-expanded={open === kind}
              className={`min-h-9 rounded-full px-3.5 py-1 text-sm font-medium ${
                open === kind ? "bg-stone-900 text-white" : `${STATUS_CLASS[kind]} hover:ring-1 hover:ring-stone-400`
              }`}
            >
              {STATUS_LABEL[kind]} {mine[kind].length} 篇
            </button>
          ))}
        </span>
      </div>

      {open && (
        <div className="overflow-x-auto rounded-xl border border-stone-200 bg-white">
          {rows.length === 0 ? (
            <p className="px-4 py-3 text-sm text-stone-600">沒有{STATUS_LABEL[open]}的講道。</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-stone-50 text-left text-stone-600">
                <tr>
                  <th className="px-4 py-2 font-medium">講道</th>
                  <th className="px-4 py-2 font-medium">系列</th>
                  <th className="whitespace-nowrap px-4 py-2 font-medium">{open === "published" ? "發佈日期" : "認領日期"}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ sermon, series }) => (
                  <tr key={sermon.item || sermon.id} className="border-t border-stone-100">
                    <td className="px-4 py-2.5">
                      <Link
                        href={sermonEditorHref(sermon)}
                        className="text-amber-800 hover:text-amber-900 hover:underline"
                      >
                        {sermon.title || sermon.item}
                      </Link>
                    </td>
                    <td className="px-4 py-2.5 text-stone-600">
                      <Link href={`/resources/series/${encodeURIComponent(series.id)}`} className="hover:underline">
                        {seriesName(series).name}
                      </Link>
                    </td>
                    <td className="whitespace-nowrap px-4 py-2.5 text-stone-700">
                      {day(open === "published" ? sermon.published_date : sermon.assigned_to_date) || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}
