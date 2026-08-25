"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, ArrowLeft, ArrowUpRight, GitBranch, Loader2, Network, Quote, Route, ShieldAlert } from "lucide-react";
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

// A relation reads source-first, so each type needs a label for the *other*
// viewpoint written from where the reader is standing. Two opaque ids, or a
// sentence the reader has to assemble from badges, are both unreadable.
const STRUCTURE_ROLE_LABELS: Record<string, string> = {
  central_claim: "中心主张", negative_boundary: "否定面", positive_identification: "正面识别",
  supporting_conclusion: "支持性结论", qualification: "限定", tension_side: "张力一方",
  application: "应用", methodological_boundary: "方法边界",
};

function relationLabel(type: string, direction: "outgoing" | "incoming") {
  const labels: Record<string, [string, string]> = {
    applies: ["应用自", "被应用于"],
    extends: ["扩展自", "被扩展为"],
    entails: ["蕴含", "被蕴含于"],
    specializes: ["特例来自", "特例是"],
    generalizes: ["概括自", "被概括为"],
    qualifies: ["限定了", "被限定于"],
    tensions_with: ["张力对方", "张力对方"],
    supersedes: ["取代了", "被取代于"],
  };
  const pair = labels[type];
  if (!pair) return type;
  return direction === "outgoing" ? pair[0] : pair[1];
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
    <article key={member.proposition_unit?.proposition_unit_id ?? member.claim.claim_id} className="overflow-hidden rounded-2xl border border-emerald-200 bg-white">
      <div className="border-b border-emerald-100 bg-emerald-50 p-4"><div className="flex flex-wrap items-center gap-2"><span className="rounded bg-emerald-700 px-2 py-1 text-xs font-bold text-white">{member.membership_kind === "proposition_unit" ? "atomic member" : member.link.link_type}</span><StatusBadge value={member.proposition_unit?.review_status ?? member.claim.review_status} /></div><h3 className="mt-2 font-bold leading-6 text-slate-950">{member.proposition_unit?.unit_statement ?? member.claim.statement}</h3><p className="mt-1 font-mono text-[11px] text-slate-500">{member.proposition_unit?.proposition_unit_id ?? member.claim.claim_id}{member.proposition_unit ? ` ← ${member.claim.claim_id}` : ""}</p></div>
      <div className="divide-y divide-slate-100">{member.evidence.map((evidence, index) => (
        <div key={evidence.evidence_step?.evidence_step_id ?? index} className="grid gap-4 p-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <div><p className="text-xs font-bold text-slate-500">Evidence step {index + 1}</p><p className="mt-1 text-sm leading-6 text-slate-800">{evidence.evidence_step?.statement ?? "缺少 EvidenceStep"}</p><p className="mt-2 text-xs text-slate-500">{evidence.evidence_step?.speaker ?? "speaker 未知"} · {evidence.evidence_step?.stance ?? "stance 未知"}</p></div>
          <blockquote className="rounded-xl bg-slate-950 p-4 text-sm leading-6 text-slate-100"><Quote className="mb-2 h-4 w-4 text-indigo-300" />{evidence.source_fragment?.verbatim_excerpt ?? "缺少逐字来源片段"}<footer className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400"><span>{evidence.source?.title ?? evidence.source?.source_id ?? "来源未知"}{evidence.locator.source_file_name ? ` · ${evidence.locator.source_file_name}` : ""} · ¶{String(evidence.locator.paragraph_key ?? "?")}{evidence.locator.media_time != null ? ` · ${Math.floor(evidence.locator.media_time / 60)}:${String(Math.floor(evidence.locator.media_time % 60)).padStart(2, "0")}` : ""}</span>{evidence.locator.source_admin_url ? <Link href={evidence.locator.source_admin_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-indigo-300">{evidence.locator.source_type === "notes_manuscript" ? "打开母本" : "打开讲道"}<ArrowUpRight className="h-3 w-3" /></Link> : evidence.locator.source_url ? <a href={evidence.locator.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-indigo-300">打开来源<ArrowUpRight className="h-3 w-3" /></a> : null}</footer></blockquote>
          <div className="lg:col-span-2 flex flex-wrap gap-2">{evidence.citations.map((citation) => <span key={citation.citation_id} className="rounded bg-slate-100 px-2 py-1 font-mono text-[10px] text-slate-600">{citation.citation_id} · {citation.status}</span>)}</div>
        </div>
      ))}</div>
    </article>
  ))}</div>;
}

function Empty({ text }: { text: string }) { return <p className="rounded-xl border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">{text}</p>; }

const routeRoleLabels: Record<string, string> = {
  observation: "观察", premise: "前提", bridge: "推论桥梁", objection: "反方意见",
  response: "回应", qualification: "限定", conclusion: "结论", application: "应用",
};

function SourceLink({ locator }: { locator: { source_admin_url: string | null; source_url: string | null; source_type: string | null } }) {
  const text = locator.source_type === "notes_manuscript" ? "打开母本" : "打开讲道";
  if (locator.source_admin_url) return <Link href={locator.source_admin_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-bold text-indigo-700">{text}<ArrowUpRight className="h-3 w-3" /></Link>;
  if (locator.source_url) return <a href={locator.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-bold text-indigo-700">打开来源<ArrowUpRight className="h-3 w-3" /></a>;
  return null;
}

function ArgumentRoutes({ detail }: { detail: Detail }) {
  if (!detail.routes.length) return <Empty text="当前 Registry 没有已审核的 source-local ArgumentRoute；不会把平铺经文伪装成完整路线。" />;
  return <section className="space-y-5">{detail.routes.map((route) => {
    const nodes = route.revision?.ordered_inference_nodes ?? [];
    return <article key={route.route_id} className="overflow-hidden rounded-2xl border border-indigo-200 bg-white">
      <header className="border-b border-indigo-100 bg-indigo-50 p-5">
        <div className="flex flex-wrap items-center gap-2"><Route className="h-4 w-4 text-indigo-600" /><h3 className="font-black text-slate-950">{route.route_type}</h3><StatusBadge value={route.coverage.eligibility} /></div>
        <p className="mt-2 text-xs text-slate-600">{route.coverage.full_attestation_count} 篇完整论证 · {route.coverage.partial_attestation_count} 篇局部论证 · {route.coverage.mode === "current_registry" ? "当前 Registry" : "coverage snapshot"}</p>
        <div className="mt-3 flex flex-wrap gap-2">{route.revision?.route_signature.inference_method_codes.map((method) => <span key={method} className="rounded-full bg-white px-2.5 py-1 font-mono text-[10px] text-indigo-700 shadow-sm">{method}</span>)}</div>
      </header>
      <div className="p-5">
        <p className="text-xs font-black uppercase tracking-wide text-slate-500">论证骨架</p>
        <ol className="mt-3 space-y-0">{nodes.map((node, index) => <li key={node.route_step_key} className="relative grid grid-cols-[2rem_minmax(0,1fr)] gap-3 pb-5 last:pb-0">
          {index < nodes.length - 1 ? <span className="absolute bottom-0 left-[0.95rem] top-8 w-px bg-indigo-200" /> : null}
          <span className={`relative z-10 flex h-8 w-8 items-center justify-center rounded-full text-xs font-black ${node.role === "conclusion" ? "bg-indigo-700 text-white" : "bg-indigo-100 text-indigo-800"}`}>{index + 1}</span>
          <div className={`rounded-xl border p-3 ${node.role === "conclusion" ? "border-indigo-300 bg-indigo-50" : "border-slate-200"}`}><p className="text-[11px] font-black text-indigo-700">{routeRoleLabels[node.role] ?? node.role} · {node.route_step_key}</p><p className="mt-1 text-sm font-semibold leading-6 text-slate-900">{node.role === "conclusion" ? detail.revision.core_proposition : node.normalized_proposition}</p></div>
        </li>)}</ol>
        <div className="my-5 flex items-center gap-3"><span className="h-px flex-1 bg-slate-200" /><span className="text-xs font-black text-slate-500">各篇来源怎样论证</span><span className="h-px flex-1 bg-slate-200" /></div>
        <div className="space-y-3">{route.attestations.map((item) => {
          const firstLocator = item.bindings.flatMap((binding) => binding.evidence).flatMap((evidence) => evidence.fragments)[0]?.locator;
          const fileName = firstLocator?.source_file_name;
          return <details key={item.attestation.argument_route_attestation_id} className="rounded-xl border border-slate-200 bg-slate-50 p-4" open={route.attestations.length === 1}>
            <summary className="cursor-pointer list-none"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="font-bold text-slate-950">{fileName ?? item.source?.title ?? item.attestation.source_id}</p><p className="mt-1 text-xs text-slate-500">{item.attestation.completeness === "full" ? "完整论证" : "局部论证"} · {item.bindings.filter((binding) => binding.binding.attestation_status === "attested").length} 个有证据步骤</p></div><div className="flex items-center gap-2"><StatusBadge value={item.attestation.review_status} />{firstLocator ? <SourceLink locator={firstLocator} /> : null}</div></div></summary>
            <div className="mt-4 space-y-4 border-t border-slate-200 pt-4">{item.bindings.map((binding) => <section key={binding.binding.route_step_key} className="grid gap-3 lg:grid-cols-[11rem_minmax(0,1fr)]"><div><p className="text-xs font-black text-indigo-700">{routeRoleLabels[binding.node?.role ?? ""] ?? binding.node?.role ?? "步骤"}</p><p className="mt-1 font-mono text-[10px] text-slate-400">{binding.binding.route_step_key}</p><StatusBadge value={binding.binding.attestation_status} /></div><div className="space-y-3">{binding.evidence.length ? binding.evidence.map((evidence, evidenceIndex) => <div key={evidence.evidence_step?.evidence_step_id ?? evidenceIndex}><p className="text-sm font-semibold leading-6 text-slate-800">{evidence.evidence_step?.statement ?? "缺少 EvidenceStep"}</p>{evidence.fragments.map((fragment) => <blockquote key={fragment.source_fragment.fragment_id} className="mt-2 rounded-xl border-l-4 border-indigo-400 bg-white p-4 text-sm leading-6 text-slate-700"><Quote className="mb-2 h-4 w-4 text-indigo-400" />{fragment.source_fragment.verbatim_excerpt}<footer className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500"><span>{fragment.locator.source_file_name ?? item.source?.title ?? item.attestation.source_id} · ¶{String(fragment.locator.paragraph_key ?? "?")}{fragment.locator.media_time != null ? ` · ${Math.floor(fragment.locator.media_time / 60)}:${String(Math.floor(fragment.locator.media_time % 60)).padStart(2, "0")}` : ""}</span><SourceLink locator={fragment.locator} /></footer></blockquote>)}</div>) : <p className="text-xs text-slate-500">此步骤在这篇来源中标为 {binding.binding.attestation_status}。</p>}</div></section>)}</div>
          </details>;
        })}</div>
        <p className="mt-4 break-all font-mono text-[10px] text-slate-400">{route.route_id}</p>
      </div>
    </article>;
  })}</section>;
}

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
      {d.structures?.map((structure) => (
        <section key={structure.structure_id} className="rounded-2xl border border-indigo-200 bg-indigo-50 p-5">
          <div className="flex flex-wrap items-center gap-2">
            <Network className="h-4 w-4 text-indigo-700" />
            <span className="text-xs font-bold text-indigo-900">本观点在这个中心里的角色</span>
            <span className="rounded bg-indigo-700 px-2 py-1 text-xs font-bold text-white">{STRUCTURE_ROLE_LABELS[structure.structure_role] ?? structure.structure_role}</span>
            <Link href={`/admin/wang/viewpoint-structures`} className="ml-auto text-xs font-bold text-indigo-700 hover:underline">看完整结构（{structure.focal_count} 个观点）→</Link>
          </div>
          <p className="mt-3 text-xs font-bold text-indigo-900">中心综合</p>
          <p className="mt-1 text-sm leading-6 text-slate-900">{structure.central_synthesis}</p>
          {structure.unresolved_items.length > 0 && (
            <details className="mt-3">
              <summary className="cursor-pointer text-xs font-bold text-amber-800">未决 {structure.unresolved_items.length} 项</summary>
              <ul className="mt-2 space-y-1">{structure.unresolved_items.map((text) => <li key={text} className="text-xs leading-5 text-amber-900">· {text}</li>)}</ul>
            </details>
          )}
        </section>
      ))}
      <AsOfStrip asOf={payload.as_of} projectionSha={payload.projection_sha256} />
      <nav className="flex overflow-x-auto border-b border-slate-200" aria-label="观点详情视图">{tabs.map(([value, text]) => <button key={value} onClick={() => setTab(value)} aria-current={tab === value ? "page" : undefined} className={`shrink-0 border-b-2 px-4 py-3 text-sm font-bold ${tab === value ? "border-indigo-600 text-indigo-700" : "border-transparent text-slate-500 hover:text-slate-900"}`}>{text}</button>)}</nav>
      {tab === "graph" && <BoundedGraph detail={d} />}
      {tab === "sources" && <Sources members={d.members} />}
      {tab === "routes" && <ArgumentRoutes detail={d} />}
      {tab === "relations" && <section className="space-y-3">{d.relations.length ? d.relations.map((relation) => <article key={relation.relation_id} className={`rounded-2xl border p-5 ${relation.relation_type === "tensions_with" ? "border-amber-300 bg-amber-50" : "border-slate-200 bg-white"}`}><div className="flex flex-wrap items-center gap-2"><GitBranch className="h-4 w-4" /><strong>{label(relation.relation_type)}</strong><StatusBadge value={relation.review_status} /></div><div className="mt-3 space-y-2"><div className="flex gap-3"><span className="w-20 shrink-0 text-xs font-bold text-slate-500">本观点</span><span className="text-sm leading-6 text-slate-900">{d.revision.core_proposition}</span></div><div className="flex gap-3"><span className="w-20 shrink-0 text-xs font-bold text-indigo-700">{relationLabel(relation.relation_type, relation.direction)}</span><span className="text-sm leading-6 text-slate-900">{relation.counterpart_viewpoint_id ? <Link href={`/admin/wang/viewpoints/${relation.counterpart_viewpoint_id}`} className="hover:text-indigo-700 hover:underline">{relation.counterpart_core_proposition ?? relation.counterpart_viewpoint_id}</Link> : (relation.counterpart_core_proposition ?? "—")}</span></div></div><p className="mt-3 border-t border-slate-100 pt-2 text-xs leading-5 text-slate-500">{relation.claim_statement}</p></article>) : <Empty text="当前快照没有直接 typed relation 或 unresolved tension。" />}</section>}
      {tab === "history" && <div className="grid gap-5 lg:grid-cols-2"><section><h2 className="mb-3 font-black text-slate-950">语义修订历史</h2><div className="space-y-3">{d.history.map((revision) => <article key={revision.viewpoint_revision_id} className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex justify-between gap-3"><strong>Revision {revision.revision_number}</strong><StatusBadge value={revision.review_status} /></div><p className="mt-2 text-sm leading-6">{revision.core_proposition}</p><p className="mt-2 font-mono text-[10px] text-slate-400">{revision.viewpoint_revision_id}</p></article>)}</div></section><section><h2 className="mb-3 font-black text-slate-950">产品影响</h2>{d.impact.dependencies.length || d.impact.events.length ? <pre className="overflow-x-auto rounded-xl bg-slate-950 p-4 text-xs text-slate-200">{JSON.stringify(d.impact, null, 2)}</pre> : <Empty text="当前没有 pinned product dependency 或 pending ImpactEvent。" />}</section></div>}
      <details className="rounded-xl border border-slate-200 bg-slate-50 p-4"><summary className="cursor-pointer text-sm font-bold text-slate-700">Provenance 与 raw projection</summary><pre className="mt-4 max-h-96 overflow-auto whitespace-pre-wrap break-all text-[11px] text-slate-600">{JSON.stringify({ authority: payload.authority, as_of: payload.as_of, projection_sha256: payload.projection_sha256, viewpoint: d.viewpoint, revision: d.revision }, null, 2)}</pre></details>
    </main>
  );
}
