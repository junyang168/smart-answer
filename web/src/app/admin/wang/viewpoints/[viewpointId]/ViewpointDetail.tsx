"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, ArrowLeft, ArrowUpRight, GitBranch, Loader2, Quote, Route, ShieldAlert } from "lucide-react";
import { AsOfStrip, StatusBadge, label } from "../ViewpointChrome";
import type { Envelope, Member, ViewpointDetail as Detail } from "../types";

const tabs = [
  ["graph", "关系图"], ["sources", "来源"], ["routes", "路线"],
  ["relations", "关系与张力"], ["history", "历史与影响"],
] as const;

function BoundedGraph({ detail }: { detail: Detail }) {
  const members = detail.graph.nodes.filter((node) => node.kind === "member");
  const routes = detail.graph.nodes.filter((node) => node.kind === "route");
  const related = detail.graph.nodes.filter((node) => node.kind === "related_viewpoint");
  return (
    <div className="space-y-5" aria-label="一个观点的有界关系图">
      <div className="rounded-2xl border-2 border-indigo-300 bg-indigo-50 p-5 text-center">
        <p className="text-xs font-black tracking-wide text-indigo-600">VIEWPOINT IDENTITY · 当前语义修订</p>
        <p className="mx-auto mt-2 max-w-3xl text-lg font-black leading-7 text-slate-950">{detail.revision.core_proposition}</p>
        <p className="mt-2 text-xs text-indigo-700">编辑归一化，不是逐字引文</p>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <GraphGroup title="Identity members" detail="等价成员；决定观点身份" tone="emerald" nodes={members} />
        <GraphGroup title="Argument routes" detail="独立推理骨架；不是成员" tone="indigo" nodes={routes} />
        <GraphGroup title="Typed relations" detail="支持、扩展、限定、应用或张力" tone="amber" nodes={related} relationEdges={detail.graph.edges} />
      </div>
      <p className="text-xs leading-5 text-slate-500">此图只显示当前 viewpoint 的第一圈。逐条来源证据在“来源”中展开；成员、路线和 typed relation 的计数互不混用。</p>
    </div>
  );
}

function GraphGroup({ title, detail, tone, nodes, relationEdges = [] }: { title: string; detail: string; tone: "emerald" | "indigo" | "amber"; nodes: Detail["graph"]["nodes"]; relationEdges?: Detail["graph"]["edges"] }) {
  const styles = { emerald: "border-emerald-200 bg-emerald-50", indigo: "border-indigo-200 bg-indigo-50", amber: "border-amber-300 bg-amber-50" };
  return (
    <section className={`rounded-2xl border p-4 ${styles[tone]}`}>
      <h3 className="text-sm font-black text-slate-950">{title}</h3><p className="mt-1 text-xs text-slate-600">{detail}</p>
      <div className="mt-3 space-y-2">
        {nodes.length ? nodes.map((node) => {
          const edge = relationEdges.find((item) => item.to === node.id);
          return <div key={`${node.kind}-${node.id}`} className="rounded-xl bg-white p-3 text-sm shadow-sm"><p className="font-semibold leading-5 text-slate-900">{node.label}</p><p className="mt-1 break-all font-mono text-[10px] text-slate-400">{edge ? `${edge.kind} → ` : ""}{node.id}</p></div>;
        }) : <p className="rounded-xl bg-white/70 p-3 text-xs text-slate-500">当前快照没有这类记录</p>}
      </div>
    </section>
  );
}

function Sources({ members }: { members: Member[] }) {
  if (!members.length) return <Empty text="当前快照没有 identity member；不能把 related Claim 当作来源成员。" />;
  return <div className="space-y-4">{members.map((member) => (
    <article key={member.claim.claim_id} className="overflow-hidden rounded-2xl border border-emerald-200 bg-white">
      <div className="border-b border-emerald-100 bg-emerald-50 p-4"><div className="flex flex-wrap items-center gap-2"><span className="rounded bg-emerald-700 px-2 py-1 text-xs font-bold text-white">{member.link.link_type}</span><StatusBadge value={member.claim.review_status} /></div><h3 className="mt-2 font-bold leading-6 text-slate-950">{member.claim.statement}</h3><p className="mt-1 font-mono text-[11px] text-slate-500">{member.claim.claim_id}</p></div>
      <div className="divide-y divide-slate-100">{member.evidence.map((evidence, index) => (
        <div key={evidence.evidence_step?.evidence_step_id ?? index} className="grid gap-4 p-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <div><p className="text-xs font-bold text-slate-500">Evidence step {index + 1}</p><p className="mt-1 text-sm leading-6 text-slate-800">{evidence.evidence_step?.statement ?? "缺少 EvidenceStep"}</p><p className="mt-2 text-xs text-slate-500">{evidence.evidence_step?.speaker ?? "speaker 未知"} · {evidence.evidence_step?.stance ?? "stance 未知"}</p></div>
          <blockquote className="rounded-xl bg-slate-950 p-4 text-sm leading-6 text-slate-100"><Quote className="mb-2 h-4 w-4 text-indigo-300" />{evidence.source_fragment?.verbatim_excerpt ?? "缺少逐字来源片段"}<footer className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400"><span>{evidence.source?.title ?? evidence.source?.source_id ?? "来源未知"} · ¶{String(evidence.locator.paragraph_key ?? "?")}{evidence.locator.media_time != null ? ` · ${Math.floor(evidence.locator.media_time / 60)}:${String(Math.floor(evidence.locator.media_time % 60)).padStart(2, "0")}` : ""}</span>{evidence.locator.source_url && <a href={evidence.locator.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-indigo-300">打开来源<ArrowUpRight className="h-3 w-3" /></a>}</footer></blockquote>
          <div className="lg:col-span-2 flex flex-wrap gap-2">{evidence.citations.map((citation) => <span key={citation.citation_id} className="rounded bg-slate-100 px-2 py-1 font-mono text-[10px] text-slate-600">{citation.citation_id} · {citation.status}</span>)}</div>
        </div>
      ))}</div>
    </article>
  ))}</div>;
}

function Empty({ text }: { text: string }) { return <p className="rounded-xl border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">{text}</p>; }

export function ViewpointDetail() {
  const routeParams = useParams<{ viewpointId: string }>();
  const search = useSearchParams(); const router = useRouter();
  const viewpointId = decodeURIComponent(routeParams.viewpointId);
  const tab = tabs.some(([value]) => value === search.get("tab")) ? search.get("tab")! : "graph";
  const [payload, setPayload] = useState<Envelope<Detail> | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const query = new URLSearchParams();
      if (search.get("snapshot")) query.set("registry_snapshot_id", search.get("snapshot")!);
      const response = await fetch(`/api/admin/wang/viewpoints/${encodeURIComponent(viewpointId)}?${query}`, { cache: "no-store" });
      const data = await response.json(); if (!response.ok) throw new Error(data.detail || `服务返回 ${response.status}`); setPayload(data);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取观点详情"); }
  }, [search, viewpointId]);
  // Async repository synchronization; state updates occur as the request settles.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  if (error) return <p className="flex items-center gap-2 rounded-xl bg-rose-50 p-4 text-sm text-rose-800"><AlertCircle className="h-4 w-4" />{error}</p>;
  if (!payload) return <p className="flex items-center gap-2 py-10 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />载入同一快照的观点详情…</p>;
  const d = payload.data;
  const requestedBack = search.get("from");
  const back = requestedBack?.startsWith("/admin/wang/viewpoints") ? requestedBack : "/admin/wang/viewpoints";
  function setTab(nextTab: string) { const next = new URLSearchParams(search.toString()); next.set("tab", nextTab); router.push(`/admin/wang/viewpoints/${encodeURIComponent(viewpointId)}?${next}`); }
  return (
    <main className="space-y-5 pb-10">
      <Link href={back} className="inline-flex items-center gap-2 text-sm font-bold text-slate-600 hover:text-indigo-700"><ArrowLeft className="h-4 w-4" />返回观点列表</Link>
      <header className="rounded-3xl bg-slate-950 px-6 py-7 text-white sm:px-9"><div className="flex flex-wrap items-start justify-between gap-4"><div className="max-w-4xl"><div className="flex flex-wrap gap-2"><StatusBadge value={d.revision.review_status} /><span className="rounded-full bg-indigo-400/20 px-2.5 py-1 text-xs font-bold text-indigo-200">编辑归一化 · 非逐字引文</span></div><h1 className="mt-4 text-2xl font-black leading-9">{d.revision.core_proposition}</h1><p className="mt-3 break-all font-mono text-xs text-slate-400">{d.viewpoint.viewpoint_id} · {d.revision.viewpoint_revision_id}</p></div><ShieldAlert className="h-6 w-6 text-indigo-300" /></div></header>
      <AsOfStrip asOf={payload.as_of} projectionSha={payload.projection_sha256} />
      <nav className="flex overflow-x-auto border-b border-slate-200" aria-label="观点详情视图">{tabs.map(([value, text]) => <button key={value} onClick={() => setTab(value)} aria-current={tab === value ? "page" : undefined} className={`shrink-0 border-b-2 px-4 py-3 text-sm font-bold ${tab === value ? "border-indigo-600 text-indigo-700" : "border-transparent text-slate-500 hover:text-slate-900"}`}>{text}</button>)}</nav>
      {tab === "graph" && <BoundedGraph detail={d} />}
      {tab === "sources" && <Sources members={d.members} />}
      {tab === "routes" && <section className="space-y-3">{d.routes.length ? d.routes.map((route) => <article key={route.route_id} className="rounded-2xl border border-indigo-200 bg-white p-5"><div className="flex items-center gap-2"><Route className="h-4 w-4 text-indigo-600" /><h3 className="font-bold text-slate-950">{route.route_type}</h3></div><p className="mt-2 text-sm text-slate-600">Claim {route.claim_id} · {route.evidence_step_ids.length} 个按来源顺序保存的 EvidenceStep</p><p className="mt-2 font-mono text-[11px] text-slate-400">{route.route_id}</p></article>) : <Empty text="当前快照没有已审核的 source-local ArgumentRoute；不会把平铺经文伪装成完整路线。" />}</section>}
      {tab === "relations" && <section className="space-y-3">{d.relations.length ? d.relations.map((relation) => <article key={relation.relation_id} className={`rounded-2xl border p-5 ${relation.relation_type === "tension_evidence" ? "border-amber-300 bg-amber-50" : "border-slate-200 bg-white"}`}><div className="flex flex-wrap items-center gap-2"><GitBranch className="h-4 w-4" /><strong>{label(relation.relation_type)}</strong><StatusBadge value={relation.review_status} /></div><p className="mt-3 text-sm leading-6 text-slate-800">{relation.claim_statement ?? relation.claim_id}</p><p className="mt-2 font-mono text-[11px] text-slate-500">{relation.from_viewpoint_id} → {relation.to_viewpoint_id ?? "尚未解析的 viewpoint"}</p></article>) : <Empty text="当前快照没有直接 typed relation 或 unresolved tension。" />}</section>}
      {tab === "history" && <div className="grid gap-5 lg:grid-cols-2"><section><h2 className="mb-3 font-black text-slate-950">语义修订历史</h2><div className="space-y-3">{d.history.map((revision) => <article key={revision.viewpoint_revision_id} className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex justify-between gap-3"><strong>Revision {revision.revision_number}</strong><StatusBadge value={revision.review_status} /></div><p className="mt-2 text-sm leading-6">{revision.core_proposition}</p><p className="mt-2 font-mono text-[10px] text-slate-400">{revision.viewpoint_revision_id}</p></article>)}</div></section><section><h2 className="mb-3 font-black text-slate-950">产品影响</h2>{d.impact.dependencies.length || d.impact.events.length ? <pre className="overflow-x-auto rounded-xl bg-slate-950 p-4 text-xs text-slate-200">{JSON.stringify(d.impact, null, 2)}</pre> : <Empty text="当前没有 pinned product dependency 或 pending ImpactEvent。" />}</section></div>}
      <details className="rounded-xl border border-slate-200 bg-slate-50 p-4"><summary className="cursor-pointer text-sm font-bold text-slate-700">Provenance 与 raw projection</summary><pre className="mt-4 max-h-96 overflow-auto whitespace-pre-wrap break-all text-[11px] text-slate-600">{JSON.stringify({ authority: payload.authority, as_of: payload.as_of, projection_sha256: payload.projection_sha256, viewpoint: d.viewpoint, revision: d.revision }, null, 2)}</pre></details>
    </main>
  );
}
