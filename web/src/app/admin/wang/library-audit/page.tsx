"use client";

import { useEffect, useState } from "react";
import { FollowUpGroup } from "./FollowUpGroup";
import { LayerCard } from "./LayerCard";
import { RunControl } from "./RunControl";
import { count, type AuditReport } from "./types";

/**
 * 獨立審計的結果，一頁看完。
 *
 * The audit answers a question none of the platform's other dozen checks do:
 * not "is anything missing" but "is any of it right". It writes three files to
 * disk and stops, which meant reading it required finding the newest timestamp
 * on the machine and `cat`-ing a text file. Nobody does that, and the rule
 * those files carry -- the library does not move on to new passages until it
 * passes -- blocks nothing while nobody reads it.
 *
 * Two rules borrowed from the extraction health view, for the same reasons:
 * no invented thresholds and no traffic lights, because the ratios are measured
 * rather than graded; and one question per page, because "has it been run" and
 * "did it come out right" in one grid is how both became unreadable.
 */
export default function LibraryAuditPage() {
  const [report, setReport] = useState<AuditReport | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const load = async () => {
      try {
        const response = await fetch("/api/admin/library-audit", { cache: "no-store" });
        if (!response.ok) throw new Error(`審計服務回傳 ${response.status}`);
        const data = (await response.json()) as AuditReport;
        if (cancelled) return;
        setReport(data);
        setError("");
        // Poll only while something is actually running. A dashboard that
        // refetches forever costs a query every few seconds for a number that
        // changes once a week.
        if (data.run?.state === "running") {
          timer = setTimeout(() => void load(), 3000);
        }
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "無法讀取審計結果");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [tick]);

  if (loading) return <p className="px-1 py-6 text-sm text-slate-400">讀取中…</p>;
  if (error || !report) return <p className="px-1 py-6 text-sm text-rose-700">{error || "沒有資料"}</p>;

  // Never a zero. A page of 0/0 reads as "nothing is wrong", which for a rule
  // that gates new passages is the worst thing it could say.
  if (report.status === "never_run") {
    return (
      <main className="flex flex-col gap-4 pb-10">
        <h1 className="text-base font-semibold tracking-tight text-slate-900">文庫獨立審計</h1>
        <p className="text-sm text-slate-700">還沒有跑過。</p>
        <RunControl
          run={report.run ?? null}
          ranAt="從來沒有"
          onChange={() => setTick((n) => n + 1)}
        />
        <p className="break-all font-mono text-[0.72rem] text-slate-400">
          輸出會落在 {report.reports_root}
        </p>
      </main>
    );
  }

  const { scope, corpus, layers = [], followups = [] } = report;
  const needsHuman = report.needs_human ?? 0;
  const mechanical = report.mechanical ?? 0;
  const checked = report.generated_at
    ? new Date(report.generated_at).toLocaleString("zh-TW", { hour12: false })
    : "—";

  return (
    <main className="flex flex-col gap-8 pb-12">
      {/* The split that decides what happens next. Four items needing someone
          to read a claim and a hundred needing a batch re-run are not the same
          backlog, and a single total of 113 hides which is which. */}
      <section className="flex flex-col gap-2">
        <h1 className="text-base font-semibold tracking-tight text-slate-900">文庫獨立審計</h1>
        {needsHuman === 0 && mechanical === 0 ? (
          <p className="text-2xl leading-snug font-semibold text-emerald-700">這一輪沒有查出問題。</p>
        ) : (
          <p className="text-2xl leading-snug text-slate-900">
            {needsHuman > 0 ? (
              <>
                <span className="font-semibold text-rose-700">{needsHuman} 條要人看</span>
                <span className="text-slate-500">——教授的意思有沒有被寫歪。</span>
              </>
            ) : (
              <span className="font-semibold text-emerald-700">沒有一條要人看。</span>
            )}
            {mechanical > 0 && (
              <>
                <br />
                <span className="font-mono">{count(mechanical)}</span> 條是程序問題
                <span className="text-slate-500">——記錄之間對不上，不必判斷對錯。</span>
              </>
            )}
          </p>
        )}
        <p className="font-mono text-xs leading-relaxed text-slate-500">
          {checked} · {report.run_id} · 判讀模型 {report.model}
        </p>
      </section>

      <RunControl run={report.run ?? null} ranAt={checked} onChange={() => setTick((n) => n + 1)} />

      <section className="flex flex-col gap-2 rounded-xl border border-slate-200 bg-slate-50 p-4">
        <p className="text-[0.82rem] leading-relaxed text-slate-700">
          <span className="font-semibold">範圍</span>　{scope?.text}：
          <span className="font-mono"> {scope?.sources} </span>份來源記錄 ·
          <span className="font-mono"> {count(corpus?.fragments ?? 0)} </span>條片段 ·
          <span className="font-mono"> {count(corpus?.claims ?? 0)} </span>條主張 ·
          <span className="font-mono"> {corpus?.viewpoints} </span>個觀點。
          {(scope?.sources_out_of_scope ?? 0) > 0 && (
            <>
              另有 <span className="font-mono">{scope?.sources_out_of_scope}</span> 份更早批次的來源沒查。
            </>
          )}
        </p>
        {(scope?.duplicate_sources.length ?? 0) > 0 && (
          <div className="border-t border-slate-200 pt-2">
            <p className="text-[0.8rem] text-slate-700">
              同一份逐字稿登記了兩筆 <span className="font-mono">source_document</span>，兩筆各自帶錨點：
            </p>
            <ul className="mt-1 flex flex-col gap-0.5">
              {scope?.duplicate_sources.map((row) => (
                <li key={row.name} className="text-[0.78rem] text-slate-500">
                  {row.name}
                  <span className="font-mono text-slate-700"> {row.source_ids.join(" · ")}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {layers.map((layer) => (
          <LayerCard key={layer.key} layer={layer} />
        ))}
      </section>

      {followups.length > 0 && (
        <div className="flex flex-col gap-8">
          <div className="flex flex-col gap-1">
            <h2 className="text-sm font-semibold tracking-tight text-slate-900">要處理的</h2>
            <p className="text-[0.8rem] leading-snug text-slate-400">
              「要人看」的排在前面。審計只讀，這些不會被自動修掉，也不會被自動接受；
              處置走既有的複審路徑。
            </p>
          </div>
          {followups.map((group) => (
            <FollowUpGroup key={group.kind} group={group} />
          ))}
        </div>
      )}

      <p className="border-t border-slate-100 pt-4 text-[0.75rem] leading-relaxed text-slate-400">
        這一頁沒有紅黃綠燈，也沒有及格線：數字是量出來的，不是評出來的，只有一輪資料時
        畫一條線會讓人把那條線當真。第 3、4 層是抽樣，「{layers.find((l) => l.key === "claims")?.disputed ?? 0} 條看起來不對」
        說的是抽到的那 {layers.find((l) => l.key === "claims")?.judged ?? 0} 條，不是整個文庫的比例。
        看起來不對也不等於錯，等於需要人看一眼。
      </p>
    </main>
  );
}
