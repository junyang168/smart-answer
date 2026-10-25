"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import cytoscape, { type Core } from "cytoscape";
import { AlertCircle, Loader2, Search } from "lucide-react";
import { ViewpointPanel } from "./ViewpointPanel";
import { RELATION_COLORS, matchesAll, searchTerms, type GraphNode, type ViewpointGraphData } from "./graph-types";

function short(text: string, length = 18): string {
  return text.length > length ? `${text.slice(0, length)}…` : text;
}


// One graph of viewpoints across sermons; its edges are the recorded viewpoint
// relations and nothing else (#453). Isolated viewpoints are listed beside it,
// not drawn, so the islands stay readable and the gap stays visible.
export function ViewpointGraph() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const selected = params.get("v");
  const [data, setData] = useState<ViewpointGraphData | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [hover, setHover] = useState<{ node: GraphNode; x: number; y: number } | null>(null);
  const container = useRef<HTMLDivElement | null>(null);
  const cy = useRef<Core | null>(null);

  const select = useCallback((id: string | null) => {
    const next = new URLSearchParams(params.toString());
    if (id) next.set("v", id); else next.delete("v");
    router.replace(`${pathname}${next.size ? `?${next}` : ""}`, { scroll: false });
  }, [params, pathname, router]);

  useEffect(() => {
    let active = true;
    fetch("/api/admin/wang/graph/viewpoints", { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || `服务返回 ${response.status}`);
        if (active) setData(payload);
      })
      .catch((reason) => active && setError(reason instanceof Error ? reason.message : "无法读取观点图"));
    return () => { active = false; };
  }, []);

  const connected = useMemo(() => data?.nodes.filter((node) => node.group !== null) ?? [], [data]);
  const isolated = useMemo(() => data?.nodes.filter((node) => node.group === null) ?? [], [data]);

  useEffect(() => {
    if (!data || !container.current) return;
    const instance = cytoscape({
      container: container.current,
      elements: [
        ...connected.map((node) => ({
          data: { id: node.id, label: short(node.text), size: 18 + node.sermons * 6 },
        })),
        ...data.edges.map((edge) => ({
          data: { id: edge.id, source: edge.source, target: edge.target, color: RELATION_COLORS[edge.type] ?? "#94a3b8", label: edge.label },
          classes: edge.type === "tensions_with" ? "tension" : "",
        })),
      ],
      style: [
        { selector: "node", style: {
          width: "data(size)", height: "data(size)", "background-color": "#6366f1", label: "data(label)",
          "font-size": 9, color: "#334155", "text-valign": "bottom", "text-margin-y": 4, "text-wrap": "none",
          // Labels appear once they are big enough to read.
          "min-zoomed-font-size": 7,
        } },
        { selector: ".dim", style: { opacity: 0.12 } },
        // Matches are labelled at any zoom, so a search can be read off the graph.
        { selector: "node.match", style: {
          "background-color": "#f59e0b", "border-width": 3, "border-color": "#fde68a", "z-index": 10,
          "font-size": 11,
          "min-zoomed-font-size": 0, "text-background-color": "#fffbeb", "text-background-opacity": 0.9, "text-background-padding": "2px",
        } },
        { selector: "node:selected", style: { "background-color": "#dc2626", "border-width": 3, "border-color": "#fecaca" } },
        { selector: "edge", style: {
          width: 1.5, "line-color": "data(color)", "target-arrow-color": "data(color)", "target-arrow-shape": "triangle",
          "curve-style": "bezier", "arrow-scale": 0.8,
        } },
        { selector: "edge.tension", style: { width: 3, "line-style": "dashed" } },
      ],
      layout: { name: "cose", animate: false, nodeRepulsion: () => 9000, idealEdgeLength: () => 70, componentSpacing: 60, padding: 20 },
      minZoom: 0.2,
      maxZoom: 3,
      wheelSensitivity: 0.3,
    });
    instance.on("tap", "node", (event) => select(event.target.id()));
    // Labels are cut short, so hovering a node shows its whole statement.
    const byId = new Map(connected.map((node) => [node.id, node]));
    instance.on("mouseover", "node", (event) => {
      const node = byId.get(event.target.id());
      const at = event.target.renderedPosition();
      if (node) setHover({ node, x: at.x, y: at.y + event.target.renderedHeight() / 2 });
    });
    instance.on("mouseout", "node", () => setHover(null));
    instance.on("viewport tap", () => setHover(null));
    cy.current = instance;
    return () => { instance.destroy(); cy.current = null; };
    // The graph is built once per payload; selection and search restyle it below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, connected]);

  useEffect(() => {
    const instance = cy.current;
    if (!instance) return;
    instance.nodes().unselect();
    if (!selected) return;
    const node = instance.getElementById(selected);
    if (node.nonempty()) {
      node.select();
      instance.animate({ center: { eles: node }, zoom: Math.max(instance.zoom(), 1.2) }, { duration: 250 });
    }
  }, [selected, data]);

  const terms = useMemo(() => (data ? searchTerms(query, data.fold) : []), [data, query]);
  const found = useMemo(
    () => (data ? data.nodes.filter((node) => matchesAll([node.text, ...node.scripture], terms, data.fold)) : []),
    [data, terms],
  );
  useEffect(() => {
    const instance = cy.current;
    if (!instance) return;
    instance.elements().removeClass("match dim");
    if (terms.length === 0) return;
    found.forEach((node) => instance.getElementById(node.id).addClass("match"));
    instance.nodes().not(".match").addClass("dim");
    instance.edges().addClass("dim");
    const drawn = instance.nodes(".match");
    if (drawn.nonempty()) {
      instance.fit(drawn, 80);
      if (instance.zoom() > 1.5) { instance.zoom(1.5); instance.center(drawn); }
    }
  }, [found, terms]);

  if (error) return <p className="flex items-center gap-2 rounded-xl bg-rose-50 p-4 text-sm text-rose-800"><AlertCircle className="h-4 w-4" />{error}</p>;
  if (!data) return <p className="flex items-center gap-2 py-10 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />正在读取观点图…</p>;

  const searching = terms.length > 0;
  const shownIsolated = searching ? isolated.filter((node) => found.includes(node)) : isolated;
  return (
    <main className="space-y-4 pb-10">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-slate-200 pb-4">
        <div>
          <h1 className="text-3xl font-black tracking-tight text-slate-950">观点图</h1>
          <p className="mt-2 max-w-3xl text-sm leading-7 text-slate-600">
            教授跨讲道的观点，以及记录在案的观点关系：{data.stats.viewpoints} 个观点、{data.stats.relations} 条关系；
            有关系的 {data.stats.connected} 个分成 {data.stats.groups} 群，另有 {data.stats.isolated} 个还没有任何关系。点大小表示出现在几篇讲道里。
          </p>
        </div>
        <label className="relative w-full max-w-sm">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="按观点文字或经文找，如 人子 神性"
            className="w-full rounded-xl border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm outline-none focus:border-indigo-400" />
        </label>
      </header>

      <div className="flex flex-wrap gap-3 text-xs text-slate-600">
        {Object.entries(data.relation_labels).map(([type, label]) => (
          <span key={type} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-5" style={{ backgroundColor: RELATION_COLORS[type] ?? "#94a3b8" }} />{label}
          </span>
        ))}
        {searching ? <span className="font-semibold text-amber-700">找到 {found.length} 个（橙色），列在右边</span> : null}
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_26rem]">
        <div className="space-y-4">
          <div className="relative">
            <div ref={container} className="h-[38rem] rounded-2xl border border-slate-200 bg-white" aria-label="观点关系图" />
            {hover ? (
              <div className="pointer-events-none absolute z-10 w-72 -translate-x-1/2 rounded-xl bg-slate-900 px-3 py-2 text-sm leading-6 text-white shadow-lg"
                style={{ left: hover.x, top: hover.y + 8 }}>
                {hover.node.text}
                <p className="mt-1 text-xs text-slate-300">
                  {[...hover.node.scripture.slice(0, 3), `${hover.node.sermons} 篇讲道`].join(" · ")}
                </p>
              </div>
            ) : null}
          </div>
          <details className="rounded-2xl border border-slate-200 bg-white">
            <summary className="cursor-pointer px-5 py-3 text-sm font-bold text-slate-800">
              还没有任何关系的观点（{shownIsolated.length}）
            </summary>
            <ul className="grid max-h-96 gap-1 overflow-y-auto border-t border-slate-200 p-3 text-sm sm:grid-cols-2">
              {shownIsolated.map((node) => (
                <li key={node.id}>
                  <button type="button" onClick={() => select(node.id)} className={`w-full rounded-lg px-3 py-1.5 text-left hover:bg-slate-50 ${selected === node.id ? "bg-rose-50" : ""}`}>
                    {node.text}
                  </button>
                </li>
              ))}
            </ul>
          </details>
        </div>
        <aside className="lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto">
          {selected ? (
            <>
              {searching ? (
                <button type="button" onClick={() => select(null)} className="mb-2 text-sm font-semibold text-indigo-700 hover:underline">
                  ← 回到搜索结果（{found.length}）
                </button>
              ) : null}
              <ViewpointPanel viewpointId={selected} onSelect={select} />
            </>
          ) : searching ? (
            <section className="rounded-2xl border border-amber-200 bg-white">
              <p className="border-b border-amber-100 px-4 py-2 text-sm font-bold text-amber-800">
                找到 {found.length} 个观点{found.length === 0 ? "。换个词，或用空格分开几个词试试" : "，点一条看详情"}
              </p>
              <ul className="divide-y divide-slate-100 text-sm leading-6">
                {found.map((node) => (
                  <li key={node.id}>
                    <button type="button" onClick={() => select(node.id)} className="w-full px-4 py-2 text-left hover:bg-amber-50">
                      {node.text}
                      <span className="mt-0.5 block text-xs text-slate-400">
                        {node.sermons} 篇讲道{node.group === null ? " · 没有关系，不在图上" : ""}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <p className="rounded-2xl border border-dashed border-slate-300 p-6 text-sm leading-7 text-slate-500">
              点图上的一个观点，这里显示它的论证路线、链接的主张、相关观点和版本历史。
            </p>
          )}
        </aside>
      </div>
    </main>
  );
}
