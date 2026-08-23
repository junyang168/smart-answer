"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AlertCircle, ArrowLeft, CheckCircle2, FlaskConical, Loader2, Quote } from "lucide-react";
import { StatusBadge } from "../ViewpointChrome";
import type { PilotEnvelope } from "../types";

export function Matthew16PilotDetail() {
  const [payload, setPayload] = useState<PilotEnvelope | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { void fetch("/api/admin/wang/viewpoints/pilot", { cache: "no-store" }).then(async (response) => {
    const data = await response.json(); if (!response.ok) throw new Error(data.detail || `服务返回 ${response.status}`); setPayload(data);
  }).catch((reason) => setError(reason instanceof Error ? reason.message : "无法读取 pilot")); }, []);
  if (error) return <p className="flex items-center gap-2 rounded-xl bg-rose-50 p-4 text-sm text-rose-800"><AlertCircle className="h-4 w-4" />{error}</p>;
  if (!payload) return <p className="flex items-center gap-2 py-10 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />载入唯一释经 viewpoint pilot…</p>;
  const pilot = payload.data;
  return <main className="space-y-6 pb-10">
    <Link href="/admin/wang/viewpoints" className="inline-flex items-center gap-2 text-sm font-bold text-slate-600"><ArrowLeft className="h-4 w-4" />返回观点主数据</Link>
    <header className="rounded-3xl bg-slate-950 px-6 py-7 text-white sm:px-9"><div className="flex flex-wrap items-center gap-2"><FlaskConical className="h-5 w-5 text-violet-300" /><span className="rounded-full bg-violet-400/20 px-3 py-1 text-xs font-black text-violet-200">太 16 释经 pilot · WIP 1</span><StatusBadge value={pilot.review_status} /></div><h1 className="mt-4 text-2xl font-black">{pilot.core_proposition}</h1><p className="mt-2 text-sm text-slate-300">{pilot.wording_label} · {pilot.consumer_eligibility} · 不写 master data</p><p className="mt-3 break-all font-mono text-xs text-slate-500">{pilot.viewpoint_candidate_id} · {pilot.viewpoint_revision_candidate_id}</p></header>
    <section className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5"><div className="flex items-center gap-2 text-emerald-800"><CheckCircle2 className="h-5 w-5" /><h2 className="font-black">文章验收：{pilot.article_acceptance.status}</h2></div><blockquote className="mt-3 rounded-xl bg-white p-4 text-base font-bold text-slate-900">「{pilot.article_acceptance.article_proposition}」</blockquote><p className="mt-2 text-xs text-emerald-800">{pilot.article_acceptance.draft_id} · 文章不是来源 authority；支持仍回到下方 {pilot.members.length} 个 current units 与逐字证据。</p></section>
    <section><h2 className="text-lg font-black text-slate-950">Identity members · {pilot.members.length}</h2><p className="mt-1 text-sm text-slate-500">Sol/high 与 Opus 5/high 独立逐项审完 17 个 units，并对每项 disposition 完全一致。</p><div className="mt-4 space-y-4">{pilot.members.map((member) => <article key={member.proposition_unit.proposition_unit_id} className="overflow-hidden rounded-2xl border border-emerald-200 bg-white"><div className="bg-emerald-50 p-4"><h3 className="font-bold text-slate-950">{member.proposition_unit.unit_statement}</h3><p className="mt-1 font-mono text-[11px] text-slate-500">{member.proposition_unit.proposition_unit_id} ← {member.parent_claim.claim_id} · {member.proposition_unit.source_id}</p></div><div className="divide-y divide-slate-100">{member.proposition_unit.evidence.map((evidence) => <blockquote key={`${evidence.evidence_step_id}-${evidence.source_fragment_id}`} className="p-4 text-sm leading-6 text-slate-800"><Quote className="mb-2 h-4 w-4 text-violet-500" />{evidence.verbatim_excerpt}<footer className="mt-2 break-all font-mono text-[10px] text-slate-400">{evidence.evidence_step_id} · {evidence.source_fragment_id}</footer></blockquote>)}</div></article>)}</div></section>
    <section className="rounded-2xl border border-amber-200 bg-amber-50 p-5"><h2 className="font-black text-slate-950">相邻但不是成员 · {pilot.adjacent_non_members.length}</h2><p className="mt-1 text-sm text-slate-600">正面所指与语法理由保留为不同真值条件，不能被这个 viewpoint 吞并。</p><div className="mt-3 grid gap-2 sm:grid-cols-2">{pilot.adjacent_non_members.map((unit) => <div key={unit.proposition_unit_id} className="rounded-xl bg-white p-3"><p className="text-sm font-semibold text-slate-800">{unit.unit_statement}</p><p className="mt-1 font-mono text-[10px] text-slate-400">{unit.proposition_unit_id}</p></div>)}</div></section>
    <details className="rounded-xl border border-slate-200 bg-slate-50 p-4"><summary className="cursor-pointer text-sm font-bold">SHA 与 blockers</summary><pre className="mt-3 overflow-auto text-[11px] text-slate-600">{JSON.stringify({ projection_sha256: payload.projection_sha256, artifact_sha256: pilot.artifact_sha256, model_ids: pilot.model_ids, blockers: pilot.blockers, apply_allowed: pilot.apply_allowed }, null, 2)}</pre></details>
  </main>;
}
