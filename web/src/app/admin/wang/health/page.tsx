"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { MetricBand } from "./MetricBand";
import { TrendLine } from "./TrendLine";
import type { HealthReport } from "./types";

/**
 * The front door: three seconds to decide whether to close it again.
 *
 * The overview table answers "which sources have been run"; this page answers
 * "is there anything I should be looking at".  Two questions, two pages, and
 * this one deliberately shows no stage status at all -- "extracted yet" and
 * "extracted well" in one 205-row grid is how both became unreadable.
 *
 * There are no red/amber/green lights and no thresholds anywhere on it.  Every
 * number so far comes from a corpus of twenty-five measured documents; drawing
 * a line at 0.8 today would invent one and, worse, would make everyone treat it
 * as meaningful.  A document is named only when it is worse than nine tenths of
 * the documents that have actually been measured, which is a statement the
 * corpus makes rather than one this page makes up.
 */
export default function ExtractionHealthPage() {
  const [report, setReport] = useState<HealthReport | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch("/api/admin/extraction-health", { cache: "no-store" });
        if (!response.ok) throw new Error(`健康視圖服務回傳 ${response.status}`);
        const data = (await response.json()) as HealthReport;
        if (!cancelled) setReport(data);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "無法讀取健康視圖");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) return <p className="px-1 py-6 text-sm text-slate-400">量測中…</p>;
  if (error || !report) return <p className="px-1 py-6 text-sm text-rose-700">{error || "沒有資料"}</p>;

  const { corpus } = report;
  const checked = new Date(report.generated_at).toLocaleString("zh-TW", { hour12: false });

  return (
    <main className="flex flex-col gap-6 pb-10">
      <section className="flex flex-col gap-2">
        <h1 className="text-base font-semibold tracking-tight text-slate-900">抽取健康視圖</h1>
        <p className="text-2xl leading-snug text-slate-900">
          <span className="font-mono">{corpus.documents}</span> 篇文件 ·{" "}
          <span className="font-mono">{corpus.measured}</span> 篇量過 ·{" "}
          {corpus.needs_attention > 0 ? (
            <span className="font-semibold text-rose-700">{corpus.needs_attention} 篇需要處理</span>
          ) : (
            <span className="font-semibold text-emerald-700">沒有一篇落在後段</span>
          )}
          ，其餘都在正常範圍。
        </p>
        {/* The number that keeps a quiet dashboard from reading as a healthy
            one: nothing was run on these, so nothing about them is known. */}
        <p className="font-mono text-xs text-slate-500">
          {corpus.never_extracted} 篇逐字稿從未抽取，所以從未量過 · 量測於 {checked}
        </p>
        <p className="text-xs leading-relaxed text-slate-400">
          分母是總表的來源清單（講道目錄 + 母本專案）。量過的 {corpus.measured} 篇來自
          {" "}{corpus.packages_on_disk} 份抽取包，同一篇重跑過的只取最近一次。
          {corpus.off_corpus_documents.length > 0 &&
            ` 另有 ${corpus.off_corpus_documents.length} 篇抽取包不在清單上。`}
        </p>
      </section>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {report.metrics.map((metric) => (
          <MetricBand key={metric.name} metric={metric} />
        ))}
      </section>

      <section className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-slate-900">每天抽取的 stranded 比例中位數</h2>
        <TrendLine trend={report.trend} />
        <p className="text-[0.78rem] leading-relaxed text-slate-500">
          虛線是 prompt 換版的那一天，取自每份包自己記下的 <span className="font-mono">prompt_sha256</span>。
          整個語料一起移動的變化，單篇的分數看不出來。
        </p>
      </section>

      <section className="flex flex-col gap-1 rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-slate-900">需要處理</h2>
        {report.exceptions.length === 0 ? (
          <p className="pt-2 text-sm text-slate-600">
            量過的 {corpus.measured} 篇裡，沒有一篇在任何一項落到後段。
          </p>
        ) : (
          <div className="flex flex-col divide-y divide-slate-100">
            {report.exceptions.map((item) => (
              <article key={item.document_id} className="flex flex-col gap-1.5 py-3.5">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="h-[7px] w-[7px] shrink-0 rounded-full bg-rose-600" aria-hidden="true" />
                  <span className="font-mono text-[0.84rem] text-slate-900">{item.label}</span>
                  {item.generated_at && (
                    <span className="font-mono text-[0.7rem] text-slate-400">
                      抽於 {item.generated_at.slice(0, 10)}
                    </span>
                  )}
                </div>
                {item.reasons.map((reason) => (
                  <div key={reason.metric} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 pl-[19px]">
                    <p className="min-w-[16rem] flex-1 text-[0.86rem] leading-relaxed text-slate-600">
                      {reason.sentence}
                    </p>
                    {reason.link && (
                      <Link href={reason.link.href} className="whitespace-nowrap text-[0.8rem] text-indigo-700 hover:underline">
                        {reason.link.label} →
                      </Link>
                    )}
                  </div>
                ))}
              </article>
            ))}
          </div>
        )}
        <p className="pt-2 text-[0.82rem] italic text-slate-400">
          其餘 {corpus.within_normal_range} 篇量過的文件每項都在正常範圍。
        </p>
      </section>

      <footer className="flex flex-wrap gap-x-6 gap-y-1 font-mono text-[0.72rem] text-slate-400">
        <span>{report.advisory}</span>
        <span>coverage · stranded · sound 由現有的包算出，沒有多跑</span>
        <span>reachable 要等 #148 的第二次執行</span>
        <Link href="/admin/wang" className="text-indigo-700 hover:underline">
          哪幾篇跑過 → 總覽
        </Link>
      </footer>
    </main>
  );
}
