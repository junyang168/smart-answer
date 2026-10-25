"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import cytoscape, { type Core } from "cytoscape";
import { AlertCircle, ArrowLeft, Loader2, Search, Share2, Wrench } from "lucide-react";
import { SourceText } from "../../source-coverage/SourceText";
import { matchesAll, searchTerms } from "../../viewpoints/graph-types";
import type { SourceDetail } from "../../source-coverage/types";

type SermonClaim = {
  id: string;
  text: string;
  claim_type: string | null;
  scripture: string[];
  viewpoints: { id: string; text: string }[];
};

type SermonGraphData = {
  source: { source_id: string; title: string; source_type: string | null };
  claims: SermonClaim[];
  edges: { id: string; source: string; target: string; type: string; label: string; reason: string | null }[];
  relation_labels: Record<string, string>;
  fold: Record<string, string>;
};

const COLORS: Record<string, string> = {
  supports: "#22c55e",
  applies: "#0ea5e9",
  contextualizes: "#94a3b8",
  qualifies: "#f97316",
  answers: "#a855f7",
  refutes: "#dc2626",
  explains: "#eab308",
};

function short(text: string, length = 14): string {
  return text.length > length ? `${text.slice(0, length)}…` : text;
}

async function read<T>(url: string): Promise<T> {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || `服务返回 ${response.status}`);
  return payload as T;
}

// One sermon: its text beside the graph of its Claims and the recorded
// relations among them (#453). Claims of two sermons are never joined here;
// the way across sermons is a Claim's viewpoint, back on the viewpoint graph.
export function SermonView({ sourceId }: { sourceId: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const selected = params.get("claim");
  const [graph, setGraph] = useState<SermonGraphData | null>(null);
  const [text, setText] = useState<SourceDetail | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [scrollTo, setScrollTo] = useState<{ ordinal: number; nonce: number } | null>(null);
  const container = useRef<HTMLDivElement | null>(null);
  const cy = useRef<Core | null>(null);
  const nonce = useRef(0);

  const select = useCallback((claimId: string | null) => {
    const next = new URLSearchParams(params.toString());
    if (claimId) next.set("claim", claimId); else next.delete("claim");
    router.replace(`${pathname}${next.size ? `?${next}` : ""}`, { scroll: false });
  }, [params, pathname, router]);

  useEffect(() => {
    let active = true;
    read<SermonGraphData>(`/api/admin/wang/graph/sermons/${encodeURIComponent(sourceId)}`)
      .then((payload) => active && setGraph(payload))
      .catch((reason) => active && setError(reason instanceof Error ? reason.message : "无法读取这篇讲道"));
    // The text comes from the source-coverage reader, which takes a few seconds.
    read<SourceDetail>(`/api/admin/source-coverage/sources/${encodeURIComponent(sourceId)}`)
      .then((payload) => active && setText(payload))
      .catch(() => undefined);
    return () => { active = false; };
  }, [sourceId]);

  useEffect(() => {
    if (!graph || !container.current) return;
    const instance = cytoscape({
      container: container.current,
      elements: [
        ...graph.claims.map((claim) => ({
          data: { id: claim.id, label: short(claim.text) },
          classes: claim.viewpoints.length > 0 ? "member" : "",
        })),
        ...graph.edges.map((edge) => ({
          data: { id: edge.id, source: edge.source, target: edge.target, color: COLORS[edge.type] ?? "#94a3b8" },
        })),
      ],
      style: [
        { selector: "node", style: {
          width: 16, height: 16, "background-color": "#cbd5e1", label: "data(label)", "font-size": 8,
          color: "#334155", "text-valign": "bottom", "text-margin-y": 3, "min-zoomed-font-size": 6,
        } },
        { selector: "node.member", style: { "background-color": "#6366f1" } },
        { selector: ".dim", style: { opacity: 0.12 } },
        { selector: "node.match", style: {
          "background-color": "#f59e0b", "border-width": 3, "border-color": "#fde68a", "z-index": 10,
          "font-size": 10,
          "min-zoomed-font-size": 0, "text-background-color": "#fffbeb", "text-background-opacity": 0.9, "text-background-padding": "2px",
        } },
        { selector: "node:selected", style: { "background-color": "#dc2626", width: 22, height: 22 } },
        { selector: "edge", style: {
          width: 1.5, "line-color": "data(color)", "target-arrow-color": "data(color)",
          "target-arrow-shape": "triangle", "curve-style": "bezier", "arrow-scale": 0.7,
        } },
      ],
      layout: { name: "cose", animate: false, nodeRepulsion: () => 6000, idealEdgeLength: () => 50, padding: 16 },
      minZoom: 0.2,
      maxZoom: 3,
      wheelSensitivity: 0.3,
    });
    instance.on("tap", "node", (event) => select(event.target.id()));
    cy.current = instance;
    return () => { instance.destroy(); cy.current = null; };
    // Built once per sermon; selection restyles it below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph]);

  const claim = useMemo(() => graph?.claims.find((item) => item.id === selected) ?? null, [graph, selected]);
  const fragmentIds = useMemo(() => new Set(selected && text?.claims[selected]?.fragment_ids || []), [selected, text]);

  useEffect(() => {
    const instance = cy.current;
    if (!instance) return;
    instance.nodes().unselect();
    if (selected) {
      const node = instance.getElementById(selected);
      if (node.nonempty()) {
        node.select();
        instance.animate({ center: { eles: node } }, { duration: 200 });
      }
    }
    const ordinal = selected ? text?.claims[selected]?.first_ordinal : null;
    if (ordinal !== null && ordinal !== undefined) {
      nonce.current += 1;
      setScrollTo({ ordinal, nonce: nonce.current });
    }
  }, [selected, graph, text]);

  const terms = useMemo(() => (graph ? searchTerms(query, graph.fold) : []), [graph, query]);
  const found = useMemo(() => (graph ? graph.claims.filter((item) => matchesAll(
    [item.text, ...item.scripture.map(String), ...item.viewpoints.map((viewpoint) => viewpoint.text)], terms, graph.fold,
  )) : []), [graph, terms]);
  const term = terms.length > 0;
  useEffect(() => {
    const instance = cy.current;
    if (!instance) return;
    instance.elements().removeClass("match dim");
    if (!term) return;
    found.forEach((item) => instance.getElementById(item.id).addClass("match"));
    instance.nodes().not(".match").addClass("dim");
    instance.edges().addClass("dim");
    const drawn = instance.nodes(".match");
    if (drawn.nonempty()) {
      instance.fit(drawn, 60);
      if (instance.zoom() > 1.5) { instance.zoom(1.5); instance.center(drawn); }
    }
  }, [found, term]);

  // A highlight in the text selects the first Claim built on that fragment.
  const selectFragment = useCallback((fragmentId: string) => {
    const owner = text && Object.values(text.claims).find((item) => item.fragment_ids.includes(fragmentId));
    if (owner) select(owner.id);
  }, [select, text]);

  if (error) return <p className="flex items-center gap-2 rounded-xl bg-rose-50 p-4 text-sm text-rose-800"><AlertCircle className="h-4 w-4" />{error}</p>;
  if (!graph) return <p className="flex items-center gap-2 py-10 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />读取这篇讲道…</p>;

  const names = new Map(graph.claims.map((item) => [item.id, item.text]));
  const related = claim ? graph.edges.filter((edge) => edge.source === claim.id || edge.target === claim.id) : [];
  return (
    <main className="space-y-4 pb-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link href="/admin/wang/sermons" className="inline-flex items-center gap-2 text-sm font-semibold text-indigo-700 hover:underline">
          <ArrowLeft className="h-4 w-4" />讲道目录
        </Link>
        <Link href={`/admin/wang/source-coverage?source=${encodeURIComponent(sourceId)}`} className="inline-flex items-center gap-1.5 text-xs font-semibold text-slate-500 hover:text-indigo-700">
          <Wrench className="h-3.5 w-3.5" />抽取诊断视图
        </Link>
      </div>
      <header>
        <h1 className="text-2xl font-black text-slate-950">{graph.source.title}</h1>
        <p className="mt-1 text-sm text-slate-600">
          {graph.source.source_type === "notes_manuscript" ? "母本" : "讲道"} · {graph.claims.length} 条主张、{graph.edges.length} 条推理关系；
          蓝点属于某个跨讲道的观点，灰点还不属于任何观点。
        </p>
      </header>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_34rem]">
        <section className="flex h-[calc(100vh-12rem)] min-h-[32rem] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white">
          <p className="border-b border-slate-200 px-4 py-2 text-xs font-bold text-slate-500">原文 · 点高亮处看它对应的主张</p>
          {text ? (
            <SourceText detail={text} selectedFragmentIds={fragmentIds} onSelectFragment={selectFragment}
              markUncovered={false} onlyUncovered={false} filter="" scrollTo={scrollTo} plain />
          ) : <p className="flex items-center gap-2 p-6 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />读取原文…</p>}
        </section>

        <section className="space-y-4">
          <div>
            <label className="relative mb-2 block">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="按主张文字、经文或所属观点找"
                className="w-full rounded-xl border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm outline-none focus:border-indigo-400" />
            </label>
            {term ? (
              <div className="mb-2 rounded-2xl border border-amber-200 bg-white">
                <p className="border-b border-amber-100 px-4 py-2 text-xs font-bold text-amber-800">
                  找到 {found.length} 条主张（图上橙色）{found.length === 0 ? "。换个词，或用空格分开几个词试试" : ""}
                </p>
                <ul className="max-h-56 divide-y divide-slate-100 overflow-y-auto text-sm leading-6">
                  {found.map((item) => (
                    <li key={item.id}>
                      <button type="button" onClick={() => select(item.id)}
                        className={`w-full px-4 py-1.5 text-left hover:bg-amber-50 ${selected === item.id ? "bg-rose-50" : ""}`}>{item.text}</button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <div ref={container} className="h-[24rem] rounded-2xl border border-slate-200 bg-white" aria-label="主张关系图" />
            <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-600">
              {Object.entries(graph.relation_labels).map(([type, label]) => (
                <span key={type} className="inline-flex items-center gap-1.5"><span className="inline-block h-0.5 w-4" style={{ backgroundColor: COLORS[type] ?? "#94a3b8" }} />{label}</span>
              ))}
            </div>
          </div>

          {claim ? (
            <article className="rounded-2xl border border-slate-200 bg-white p-5">
              {claim.scripture.length > 0 ? <p className="text-xs font-bold text-slate-500">{claim.scripture.join("；")}</p> : null}
              <h2 className="mt-1 text-base font-bold leading-7 text-slate-950">{claim.text}</h2>
              {claim.viewpoints.length > 0 ? (
                <div className="mt-3">
                  <p className="text-xs font-black text-slate-500">属于观点</p>
                  {claim.viewpoints.map((viewpoint) => (
                    <Link key={viewpoint.id} href={`/admin/wang/viewpoints?v=${encodeURIComponent(viewpoint.id)}`}
                      className="mt-1 flex items-start gap-1.5 text-sm text-indigo-700 hover:underline">
                      <Share2 className="mt-1 h-3.5 w-3.5 shrink-0" />{viewpoint.text}
                    </Link>
                  ))}
                </div>
              ) : <p className="mt-3 text-xs text-slate-500">还不属于任何跨讲道的观点。</p>}
              {related.length > 0 ? (
                <div className="mt-4">
                  <p className="text-xs font-black text-slate-500">本篇里的推理关系</p>
                  <ul className="mt-1 space-y-2 text-sm leading-6">
                    {related.map((edge) => {
                      const other = edge.source === claim.id ? edge.target : edge.source;
                      return (
                        <li key={edge.id}>
                          <span className="mr-1.5 rounded px-1.5 py-0.5 text-xs font-bold text-white" style={{ backgroundColor: COLORS[edge.type] ?? "#94a3b8" }}>
                            {edge.source === claim.id ? `${edge.label}→` : `←${edge.label}`}
                          </span>
                          <button type="button" onClick={() => select(other)} className="text-left text-slate-800 hover:text-indigo-700 hover:underline">{names.get(other)}</button>
                          {edge.reason ? <span className="block text-xs text-slate-500">{edge.reason}</span> : null}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ) : null}
            </article>
          ) : (
            <p className="rounded-2xl border border-dashed border-slate-300 p-5 text-sm leading-7 text-slate-500">
              点图上的一条主张，或原文里的高亮处：这里显示它属于哪个观点、和本篇其他主张怎样推理连接，原文会滚到那一段。
            </p>
          )}
        </section>
      </div>
    </main>
  );
}
