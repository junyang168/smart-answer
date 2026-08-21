import Link from "next/link";
import { ratio, type Metric } from "./types";

/**
 * One metric as the shape of the whole corpus, not as a colour.
 *
 * Every measured document is a tick on one axis, so a dense cluster reads as
 * "nothing here" at a glance and a tick out on its own reads as "go look" --
 * faster than a badge, and without anyone having had to declare where good
 * stops and bad starts.  The axis runs from the corpus's own smallest value to
 * its own largest, because the range of a metric nobody has calibrated is only
 * knowable from the corpus itself.
 */
export function MetricBand({ metric }: { metric: Metric }) {
  const outliers = metric.values.filter((item) => item.outlier);
  const values = metric.values.map((item) => item.value);
  const low = Math.min(...values, 0);
  const high = Math.max(...values, 1);
  const span = high - low || 1;
  const x = (value: number) => 2 + ((value - low) / span) * 96;

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-[0.7rem] font-semibold uppercase tracking-[0.09em] text-slate-500">
          {metric.name}
        </span>
        <span className="font-mono text-lg font-medium text-slate-900">
          {metric.median === null ? "—" : ratio(metric.median)}
        </span>
      </div>
      <p className="text-[0.8rem] leading-snug text-slate-400">{metric.question}</p>

      {metric.state === "pending" ? (
        <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-3 py-3 text-[0.78rem] leading-relaxed text-slate-500">
          還沒有量過。{metric.pending_reason}
        </div>
      ) : (
        // No viewBox: the ticks are positioned as percentages of the real
        // element, so the band stretches to its column without the glyphs
        // stretching with it.
        <svg className="h-[34px] w-full" role="img"
             aria-label={`${metric.name}：${metric.measured_documents} 篇文件的分佈，中位數 ${metric.median === null ? "無" : ratio(metric.median)}`}>
          <line x1="2%" x2="98%" y1="17" y2="17" stroke="#e2e8f0" strokeWidth="1" />
          {metric.values.filter((item) => !item.outlier).map((item) => (
            <line key={item.document_id} x1={`${x(item.value)}%`} x2={`${x(item.value)}%`} y1="8" y2="26"
                  stroke="#94a3b8" strokeWidth="1" opacity="0.55" />
          ))}
          {outliers.map((item) => (
            <line key={item.document_id} x1={`${x(item.value)}%`} x2={`${x(item.value)}%`} y1="3" y2="31"
                  stroke="#be123c" strokeWidth="2" />
          ))}
        </svg>
      )}

      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 font-mono text-[0.72rem]">
        <span className={outliers.length ? "text-rose-700" : "text-emerald-700"}>
          {metric.state === "pending"
            ? "尚無分佈"
            : outliers.length
              ? `${outliers.length} 篇比語料 90% 的文件差（${outliers.map((item) => ratio(item.value)).join("、")}）`
              : "沒有一篇落在後段"}
        </span>
        <span className="text-slate-400">
          {metric.state === "pending" ? "0 篇量過" : `${metric.measured_documents} 篇量過`}
        </span>
      </div>

      {metric.state === "measured" && metric.distribution_is_thin && (
        <p className="text-[0.72rem] leading-relaxed text-amber-700">
          只有 {metric.measured_documents} 篇量過，還排不出前後段，這條帶只顯示形狀。
        </p>
      )}

      {metric.owner_href && (
        <Link href={metric.owner_href} className="font-mono text-[0.72rem] text-indigo-700 hover:underline">
          明細在「{metric.owner}」→
        </Link>
      )}
    </div>
  );
}
