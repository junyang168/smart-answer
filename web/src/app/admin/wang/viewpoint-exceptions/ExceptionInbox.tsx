"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, AlertTriangle, ArrowLeft, GitCompare, Loader2, ShieldCheck } from "lucide-react";
import { AsOfStrip } from "../viewpoints/ViewpointChrome";
import type { Envelope, ExceptionSummary } from "../viewpoints/types";

type Assessment = { recommended_action?: string; core_proposition?: string; disposition?: string; rationale?: string };
type ExceptionDetail = ExceptionSummary & {
  claims: Array<{ claim_id: string; statement: string; source_id: string; evidence: Array<{ verbatim_excerpt: string; paragraph_key?: string | number; media_time?: number }> }>;
  proposal: Assessment;
  blind_review: Assessment;
  semantic_deltas: Array<{ field_path: string; proposal_value: unknown; blind_review_value: unknown }>;
  deterministic_blockers: Array<{ code: string; detail: string; record_ids: string[] }>;
  requested_editor_decision: string;
  artifact_sha256: string;
};

async function read<T>(url: string): Promise<T> {
  const response = await fetch(url, { cache: "no-store" }); const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `服务返回 ${response.status}`); return data as T;
}

function impactLabel(value: string) {
  return ({ withdrawal: "可能撤回已公开产品", publication: "影响发布", planning: "影响编排", none: "无已知产品影响" } as Record<string, string>)[value] ?? value;
}

export function ExceptionInbox() {
  const search = useSearchParams(); const router = useRouter();
  const [queue, setQueue] = useState<Envelope<{ items: ExceptionSummary[]; total: number; next_cursor: string | null }> | null>(null);
  const [detail, setDetail] = useState<Envelope<ExceptionDetail> | null>(null);
  const [error, setError] = useState("");
  const selected = search.get("bundle");
  const load = useCallback(async () => {
    try {
      const list = await read<Envelope<{ items: ExceptionSummary[]; total: number; next_cursor: string | null }>>("/api/admin/wang/viewpoint-exceptions");
      setQueue(list);
      const id = selected || list.data.items[0]?.exception_bundle_id;
      if (id) {
        if (!selected) router.replace(`/admin/wang/viewpoint-exceptions?bundle=${encodeURIComponent(id)}`);
        setDetail(await read<Envelope<ExceptionDetail>>(`/api/admin/wang/viewpoint-exceptions/${encodeURIComponent(id)}`));
      } else setDetail(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取观点例外队列"); }
  }, [router, selected]);
  // Async repository synchronization; state updates occur as requests settle.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  if (error) return <p className="flex items-center gap-2 rounded-xl bg-rose-50 p-4 text-sm text-rose-800"><AlertCircle className="h-4 w-4" />{error}</p>;
  if (!queue) return <p className="flex items-center gap-2 py-10 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />载入唯一人工例外队列…</p>;
  return (
    <main className="space-y-5 pb-10">
      <Link href="/admin/wang/viewpoints" className="inline-flex items-center gap-2 text-sm font-bold text-slate-600 hover:text-indigo-700"><ArrowLeft className="h-4 w-4" />返回观点主数据</Link>
      <header className="rounded-3xl bg-slate-950 px-6 py-7 text-white sm:px-9"><div className="flex flex-wrap items-start justify-between gap-5"><div><p className="text-sm font-bold text-amber-300">Single-editor workflow</p><h1 className="mt-2 text-3xl font-black">观点例外收件箱</h1><p className="mt-3 max-w-2xl text-sm leading-6 text-slate-300">这里只出现自动门无法决定的 identity bundle；普通候选和低风险系统核准不会制造人工 backlog。</p></div><span className="inline-flex items-center gap-2 rounded-xl bg-white/10 px-3 py-2 text-sm"><ShieldCheck className="h-4 w-4 text-emerald-300" />第一版只读</span></div></header>
      <AsOfStrip asOf={queue.as_of} projectionSha={queue.projection_sha256} />
      {queue.data.total === 0 ? <div className="rounded-2xl border border-slate-200 bg-white px-5 py-14 text-center"><ShieldCheck className="mx-auto h-7 w-7 text-emerald-600" /><p className="mt-3 font-black text-slate-900">没有需要人工判断的观点例外</p><p className="mt-1 text-sm text-slate-500">这不代表尚未运行的候选已被处理；请以 ResolutionLedger 的 unprocessed/deferred 状态为准。</p></div> : (
        <div className="grid gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="space-y-2" aria-label="按风险排序的观点例外">{queue.data.items.map((item) => <button key={item.exception_bundle_id} onClick={() => router.push(`/admin/wang/viewpoint-exceptions?bundle=${encodeURIComponent(item.exception_bundle_id)}`)} className={`w-full rounded-xl border p-4 text-left ${selected === item.exception_bundle_id ? "border-amber-400 bg-amber-50" : "border-slate-200 bg-white hover:border-slate-300"}`}><div className="flex items-center justify-between gap-2"><span className="text-xs font-black text-amber-800">优先级 {item.priority}</span><span className="text-xs text-slate-500">{impactLabel(item.consumer_impact)}</span></div><p className="mt-2 font-mono text-xs text-slate-700">{item.candidate_id}</p><p className="mt-2 text-xs leading-5 text-slate-500">{item.blocker_codes.join(" · ")}</p></button>)}</aside>
          {detail ? <DecisionBundle detail={detail.data} /> : <p className="text-sm text-slate-500">选择一个 exception bundle。</p>}
        </div>
      )}
    </main>
  );
}

function DecisionBundle({ detail }: { detail: ExceptionDetail }) {
  return <article className="space-y-5 min-w-0">
    <section className="rounded-2xl border border-amber-300 bg-amber-50 p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-xs font-black text-amber-800">{impactLabel(detail.consumer_impact)} · 优先级 {detail.priority}</p><h2 className="mt-1 break-all font-mono text-sm font-bold text-slate-950">{detail.exception_bundle_id}</h2></div><AlertTriangle className="h-5 w-5 text-amber-700" /></div><div className="mt-3 flex flex-wrap gap-2">{detail.blocker_codes.map((code) => <span key={code} className="rounded bg-white px-2 py-1 text-xs font-bold text-amber-900">{code}</span>)}</div>{detail.remaining_findings.length > 0 && <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-slate-700">{detail.remaining_findings.map((finding) => <li key={finding}>{finding}</li>)}</ul>}</section>
    <section><div className="mb-3 flex items-center gap-2"><GitCompare className="h-4 w-4 text-indigo-600" /><h3 className="font-black text-slate-950">Proposal 与 independent review</h3></div><div className="grid gap-3 md:grid-cols-2"><AssessmentPanel title="Proposal" assessment={detail.proposal} /><AssessmentPanel title="Blind review" assessment={detail.blind_review} /></div>{detail.semantic_deltas.length > 0 && <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-sm"><thead className="text-xs text-slate-500"><tr><th className="py-2 pr-3">分歧字段</th><th className="py-2 pr-3">Proposal</th><th className="py-2">Review</th></tr></thead><tbody className="divide-y divide-slate-100">{detail.semantic_deltas.map((delta) => <tr key={delta.field_path}><td className="py-3 pr-3 font-mono text-xs">{delta.field_path}</td><td className="py-3 pr-3">{JSON.stringify(delta.proposal_value)}</td><td className="py-3">{JSON.stringify(delta.blind_review_value)}</td></tr>)}</tbody></table></div>}</section>
    <section><h3 className="mb-3 font-black text-slate-950">逐字证据</h3><div className="space-y-3">{detail.claims.map((claim) => <article key={claim.claim_id} className="rounded-xl border border-slate-200 bg-white p-4"><p className="font-bold leading-6 text-slate-900">{claim.statement}</p><p className="mt-1 font-mono text-[10px] text-slate-400">{claim.claim_id} · {claim.source_id}</p>{claim.evidence.map((evidence, index) => <blockquote key={index} className="mt-3 border-l-2 border-indigo-300 pl-3 text-sm leading-6 text-slate-700">{evidence.verbatim_excerpt}</blockquote>)}</article>)}</div></section>
    <section className="rounded-xl border border-dashed border-slate-300 p-4 text-sm text-slate-600"><strong>本版没有决定按钮。</strong> 后续写入只能提交带 expected revision、input SHA 与 impact preview SHA 的 ChangeSet，不能从浏览器 PATCH master record。</section>
  </article>;
}

function AssessmentPanel({ title, assessment }: { title: string; assessment: Assessment }) {
  return <div className="rounded-xl border border-slate-200 bg-white p-4"><p className="text-xs font-black text-slate-500">{title}</p><p className="mt-2 text-sm font-bold leading-6 text-slate-900">{assessment.core_proposition ?? assessment.disposition ?? "未提供规范措辞"}</p><p className="mt-2 text-xs leading-5 text-slate-500">{assessment.recommended_action ?? assessment.rationale ?? "无附加说明"}</p></div>;
}
