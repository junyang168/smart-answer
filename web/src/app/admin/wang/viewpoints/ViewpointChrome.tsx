import Link from "next/link";
import { AlertTriangle, Database, ShieldCheck } from "lucide-react";
import type { AsOf } from "./types";

const labels: Record<string, string> = {
  complete: "完整", partial: "部分", unavailable: "尚无资料", pass: "通过", fail: "阻断",
  system_approved: "系统核准", human_approved: "人工例外核准", approved: "已核准", candidate: "候选",
  dual_model_consensus: "双模型共识", human_exception_review: "人工例外", not_approved: "未核准",
};

export function label(value: string | null | undefined) {
  return value ? labels[value] ?? value : "未知";
}

export function StatusBadge({ value }: { value: string | null | undefined }) {
  const tone = value === "fail" || value === "partial" || value === "unavailable"
    ? "bg-amber-100 text-amber-900"
    : value?.includes("approved") || value === "pass" || value === "complete"
      ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-700";
  return <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ${tone}`}>{label(value)}</span>;
}

export function WorkbenchHeader({ exceptions = 0 }: { exceptions?: number }) {
  return (
    <header className="overflow-hidden rounded-3xl bg-slate-950 px-6 py-7 text-white sm:px-9">
      <div className="flex flex-wrap items-start justify-between gap-5">
        <div className="max-w-3xl">
          <p className="text-sm font-bold tracking-wide text-indigo-300">Canonical master data</p>
          <h1 className="mt-2 text-3xl font-black tracking-tight">观点主数据</h1>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-300">
            一行一个稳定观点身份。成员、推理路线、支持关系与张力分别呈现；规范措辞是编辑归一化，不是王教授的逐字原话。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className="inline-flex items-center gap-2 rounded-xl bg-white/10 px-3 py-2 text-sm text-slate-200">
            <ShieldCheck className="h-4 w-4 text-emerald-300" />只读
          </span>
          <Link href="/admin/wang/viewpoint-exceptions" className="inline-flex items-center gap-2 rounded-xl bg-amber-300 px-3 py-2 text-sm font-bold text-slate-950 hover:bg-amber-200">
            <AlertTriangle className="h-4 w-4" />例外 {exceptions}
          </Link>
        </div>
      </div>
    </header>
  );
}

export function AsOfStrip({ asOf, projectionSha }: { asOf: AsOf; projectionSha: string }) {
  return (
    <details id="snapshot-provenance" className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-xs text-slate-600">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 font-semibold text-slate-800">
        <span className="inline-flex items-center gap-2"><Database className="h-4 w-4 text-indigo-600" />同一快照：{asOf.coverage_snapshot_id ?? "尚无 CoverageSnapshot"}</span>
        <span className="font-mono text-[11px] text-slate-400">{projectionSha.slice(0, 12)}</span>
      </summary>
      <dl className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div><dt className="text-slate-400">Registry snapshot</dt><dd className="mt-0.5 break-all font-mono">{asOf.registry_snapshot_id}</dd></div>
        <div><dt className="text-slate-400">Resolution ledger</dt><dd className="mt-0.5 break-all font-mono">{asOf.resolution_ledger_id ?? "unavailable"}</dd></div>
        <div><dt className="text-slate-400">Quality report</dt><dd className="mt-0.5 break-all font-mono">{asOf.quality_report_id ?? "unavailable"}</dd></div>
        <div><dt className="text-slate-400">状态</dt><dd className="mt-0.5 flex gap-1"><StatusBadge value={asOf.coverage_status} /><StatusBadge value={asOf.quality_decision} /></dd></div>
      </dl>
    </details>
  );
}
