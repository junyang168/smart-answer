"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { ArgumentCanvas } from "./ArgumentCanvas";
import { NodeDetail } from "./NodeDetail";
import type { Focus, SearchHit, Source, SourceSummary, Stats } from "./types";

type Overview = { lanes: string[]; totals: Stats; sources: SourceSummary[] };

/** What `?only=` asks the canvas to keep, and what the chip is called. */
const FOCUS_MODES: Record<string, { label: string; pick: (findings: DocumentFindings) => string[] }> = {
  stranded: {
    label: "只看走不到的",
    pick: (findings) => [...findings.stranded.evidence_step_ids, ...findings.stranded.observation_ids],
  },
  unsound: { label: "只看沒過複審的", pick: (findings) => findings.unsound.claim_ids },
};

type DocumentFindings = {
  label: string;
  stranded: { count: number; evidence_step_ids: string[]; observation_ids: string[] };
  unsound: { count: number; claim_ids: string[] };
};

/**
 * Which source is open, and which records the health view sent the reader
 * here to look at, are part of the address rather than of this component --
 * an exception on `/admin/wang/health` is a link, not an instruction to go
 * and find something.  `useSearchParams` makes the route ask for the query
 * string, and a prerendered route may only do that inside a boundary.
 */
export default function ArgumentLayerRoute() {
  return (
    <Suspense fallback={<p className="px-6 py-5 text-sm text-slate-400">載入中…</p>}>
      <ArgumentLayerPage />
    </Suspense>
  );
}

const numberCell = (value: number, gap = false) => (
  <td className={`px-2 py-1.5 text-right font-mono ${gap && value ? "text-rose-600" : "text-slate-700"}`}>{value}</td>
);

function ArgumentLayerPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [source, setSource] = useState<Source | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [scrollTarget, setScrollTarget] = useState<{ id: string; nonce: number } | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<{ total: number; hits: SearchHit[] } | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [focus, setFocus] = useState<Focus | null>(null);
  const params = useSearchParams();
  const requestedSource = params.get("source");
  const requestedOnly = params.get("only");
  const nonce = useRef(0);
  const shell = useRef<HTMLDivElement>(null);
  const [shellHeight, setShellHeight] = useState<number | null>(null);

  // The canvas fills whatever the admin chrome leaves, measured rather than
  // guessed: a change to the header or the Wang sub-nav must not clip it.
  useEffect(() => {
    const measure = () => {
      if (shell.current) setShellHeight(window.innerHeight - shell.current.getBoundingClientRect().top);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [source]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch("/api/admin/argument-layer/sources", { cache: "no-store" });
        if (!response.ok) throw new Error(`論證層服務回傳 ${response.status}`);
        const data = (await response.json()) as Overview;
        if (!cancelled) setOverview(data);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "無法讀取論證層");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const openSource = useCallback(async (key: string, selectAfter?: string) => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`/api/admin/argument-layer/sources/${encodeURIComponent(key)}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`來源服務回傳 ${response.status}`);
      const data = (await response.json()) as { source: Source };
      setSource(data.source);
      setSelectedId(selectAfter ?? null);
      if (selectAfter) {
        nonce.current += 1;
        setScrollTarget({ id: selectAfter, nonce: nonce.current });
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "無法讀取這個來源");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const needle = query.trim();
    if (!needle) {
      setHits(null);
      return;
    }
    const timer = setTimeout(async () => {
      try {
        const response = await fetch(`/api/admin/argument-layer/search?q=${encodeURIComponent(needle)}`, { cache: "no-store" });
        if (response.ok) setHits(await response.json());
      } catch {
        setHits(null);
      }
    }, 220);
    return () => clearTimeout(timer);
  }, [query]);

  const selectedPlaced = useMemo(() => {
    if (!source || !selectedId) return null;
    const claim = source.claims.find((item) => item.id === selectedId);
    if (claim) {
      const linked = claim.step_ids.length > 0;
      return { id: claim.id, kind: "claim" as const, node: claim, lane: -1, col: 0, linked, x: 0, y: 0, w: 0, h: 0 };
    }
    const lists = [
      ["step", source.steps],
      ["observation", source.observations],
      ["question", source.questions],
      ["position", source.positions],
    ] as const;
    const linkedIds = new Set(source.edges.flatMap((edge) => [edge.from, edge.to]));
    for (const [kind, items] of lists) {
      const node = items.find((item) => item.id === selectedId);
      if (node)
        return { id: node.id, kind, node, lane: 0, col: 0, linked: linkedIds.has(node.id), x: 0, y: 0, w: 0, h: 0 };
    }
    return null;
  }, [source, selectedId]);

  const maxSteps = Math.max(1, ...(overview?.sources ?? []).map((item) => item.stats.steps));

  const goto = useCallback((id: string) => {
    setSelectedId(id);
    nonce.current += 1;
    setScrollTarget({ id, nonce: nonce.current });
  }, []);

  // The health view decides which records are stranded or unsound; this page
  // draws them.  Asking it for the ids -- rather than re-deriving them from
  // the graph -- is what keeps the two pages from naming different records
  // for the same document.
  useEffect(() => {
    if (!requestedSource) return;
    let cancelled = false;
    void openSource(requestedSource);
    const mode = requestedOnly ? FOCUS_MODES[requestedOnly] : undefined;
    if (!mode) return;
    (async () => {
      try {
        const response = await fetch(`/api/admin/extraction-health/documents/${encodeURIComponent(requestedSource)}`, {
          cache: "no-store",
        });
        if (!response.ok) return;
        const findings = (await response.json()) as DocumentFindings;
        const ids = mode.pick(findings);
        if (cancelled || !ids.length) return;
        setFocus({ ids: new Set(ids), label: `${mode.label}（${ids.length}）` });
        // A claim sits at the column of its last piece of evidence, which on a
        // canvas several thousand pixels wide is usually not the part in view.
        // Arriving on a link and seeing empty space is the same as the link
        // not working.
        goto(ids[0]);
      } catch {
        // A health service that is down must not take the argument layer with
        // it: the graph itself is still worth looking at unfiltered.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [requestedSource, requestedOnly, openSource, goto]);

  const header = (
    <header className="flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-4 py-2">
      <div className="whitespace-nowrap">
        <h1 className="text-[15px] font-bold text-slate-900">論證層</h1>
        <p className="text-[11px] text-slate-500">authoring store · 直接讀 PostgreSQL</p>
      </div>
      {source ? (
        <button
          type="button"
          onClick={() => {
            setSource(null);
            setSelectedId(null);
          }}
          className="inline-flex items-center gap-1 rounded-full border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-100"
        >
          <ArrowLeft className="h-3.5 w-3.5" />全庫總覽
        </button>
      ) : null}
      {source ? <span className="text-sm font-semibold text-slate-800">{source.title}</span> : null}
      <div className="relative">
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜尋全庫：節點文字或 ID…"
          className="w-52 rounded-lg border border-slate-300 px-2.5 py-1.5 text-[13px]"
        />
        {hits && query.trim() ? (
          <div className="absolute left-0 top-10 z-40 max-h-[60vh] w-[min(560px,60vw)] overflow-auto rounded-xl border border-slate-300 bg-white p-1.5 shadow-xl">
            {hits.hits.length ? (
              <>
                {hits.hits.map((hit) => (
                  <button
                    key={hit.id}
                    type="button"
                    onClick={() => {
                      setQuery("");
                      setHits(null);
                      if (source?.key === hit.source_key) goto(hit.id);
                      else void openSource(hit.source_key, hit.id);
                    }}
                    className="block w-full rounded-md px-2 py-1.5 text-left text-xs text-slate-800 hover:bg-indigo-50"
                  >
                    {hit.statement.slice(0, 80)}
                    <span className="block font-mono text-[10px] text-slate-400">
                      {hit.kind}　{hit.id}　{hit.source_title}
                    </span>
                  </button>
                ))}
                {hits.total > hits.hits.length ? (
                  <p className="px-2 py-1 text-[11px] text-slate-400">另有 {hits.total - hits.hits.length} 筆未列出</p>
                ) : null}
              </>
            ) : (
              <p className="px-2 py-1 text-[11px] text-slate-400">沒有符合的節點。</p>
            )}
          </div>
        ) : null}
      </div>
      {source ? (
        <div className="ml-auto flex flex-wrap items-center gap-3 font-mono text-xs text-slate-500">
          <span>
            evidence_steps <b className="text-slate-900">{source.stats.steps}</b>
            {source.stats.steps_isolated ? (
              <span className="text-rose-600"> isolated {source.stats.steps_isolated}</span>
            ) : null}
          </span>
          <span>relations <b className="text-slate-900">{source.stats.edges}</b></span>
          <span>claims <b className="text-slate-900">{source.stats.claims}</b></span>
          <span>questions <b className="text-slate-900">{source.stats.questions}</b></span>
          <span className={source.stats.observations_linked < source.stats.observations ? "text-rose-600" : ""}>
            observations <b>{source.stats.observations}</b>（linked {source.stats.observations_linked}）
          </span>
        </div>
      ) : null}
    </header>
  );

  if (error && !source)
    return (
      <div className="rounded-xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700">
        {error}
        <p className="mt-2 text-xs text-rose-600">論證層需要 KNOWLEDGE_DATABASE_URL 指向 authoring store。</p>
      </div>
    );

  if (source)
    return (
      <div
        ref={shell}
        style={{ height: shellHeight ?? undefined }}
        className="-mx-2 -my-6 flex flex-col overflow-hidden bg-white"
      >
        {header}
        <div className="flex min-h-0 min-w-0 flex-1">
          <ArgumentCanvas
            source={source}
            lanes={overview?.lanes ?? []}
            selectedId={selectedId}
            onSelect={setSelectedId}
            scrollTarget={scrollTarget}
            spotlight={focus}
          />
          <NodeDetail placed={selectedPlaced} source={source} onGoto={goto} />
        </div>
      </div>
    );

  const totals = overview?.totals;

  return (
    <div
      ref={shell}
      style={{ height: shellHeight ?? undefined }}
      className="-mx-2 -my-6 flex flex-col overflow-hidden bg-white"
    >
      {header}
      <div className="flex-1 overflow-auto px-6 py-5">
        <h2 className="text-[15px] font-bold text-slate-900">全庫論證層總覽</h2>
        <p className="mb-4 max-w-[74ch] text-[12.5px] leading-7 text-slate-500">
          每一列是一個 source。兩個 <code className="font-mono">isolated</code>{" "}
          欄位是同一個量測：沒有任何關係邊的節點——前一個算 evidence_steps，後一個算 observations。
          它不是資料品質分數，是還沒有人做過的判斷有多少。點任一列進入該 source 的論證圖。
        </p>
        <p className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] text-slate-500">
          <span className="inline-flex items-center gap-1.5">
            <i className="inline-block h-[7px] w-8 rounded-sm bg-indigo-500" />有關係邊
          </span>
          <span className="inline-flex items-center gap-1.5">
            <i className="inline-block h-[7px] w-4 rounded-sm bg-rose-400" />
            <code className="font-mono">isolated</code>
          </span>
          <span>整條長度＝該 source 的 evidence_steps 佔全庫最多者（{maxSteps}）的比例</span>
        </p>
        {loading && !overview ? <p className="text-sm text-slate-400">載入中…</p> : null}
        {overview ? (
          <table className="w-full border-collapse text-[12.5px]">
            <thead>
              <tr className="border-b border-slate-300 text-[11px] font-semibold text-slate-500">
                <th className="px-2 pb-1.5 text-left font-mono">source</th>
                {["evidence_steps", "isolated", "relations", "claims", "questions", "positions", "observations", "isolated"].map(
                  (label, index) => (
                    <th key={`${label}-${index}`} className="whitespace-nowrap px-2 pb-1.5 text-right font-mono">
                      {label}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {overview.sources.map((item) => {
                const stats = item.stats;
                const width = (96 * stats.steps) / maxSteps;
                const isolatedWidth = stats.steps ? (width * stats.steps_isolated) / stats.steps : 0;
                const obsGap = stats.observations - stats.observations_linked;
                return (
                  <tr
                    key={item.key}
                    onClick={() => void openSource(item.key)}
                    className="cursor-pointer border-b border-slate-200 hover:bg-indigo-50"
                  >
                    <td className="max-w-[34ch] truncate px-2 py-1.5">
                      {item.title}
                      {item.source_type === "notes_manuscript" ? (
                        <i className="ml-2 font-mono text-[10.5px] not-italic text-slate-400">手稿</i>
                      ) : null}
                      {item.note ? <i className="ml-2 font-mono text-[10.5px] not-italic text-slate-400">{item.note}</i> : null}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5 text-right font-mono text-slate-700">
                      {stats.steps}
                      <span
                        className="ml-1.5 inline-block h-[7px] rounded-sm bg-indigo-500 align-middle"
                        style={{ width: Math.max(0, width - isolatedWidth) }}
                      />
                      <span className="inline-block h-[7px] rounded-sm bg-rose-400 align-middle" style={{ width: isolatedWidth }} />
                    </td>
                    {numberCell(stats.steps_isolated, true)}
                    {numberCell(stats.edges, stats.edges === 0)}
                    {numberCell(stats.claims)}
                    {numberCell(stats.questions)}
                    {numberCell(stats.positions)}
                    {numberCell(stats.observations)}
                    {numberCell(obsGap, true)}
                  </tr>
                );
              })}
            </tbody>
            {totals ? (
              <tfoot>
                <tr className="font-mono text-slate-700">
                  <td className="px-2 py-2 font-sans font-bold text-slate-900">全庫 {overview.sources.length} 個 source</td>
                  {numberCell(totals.steps)}
                  {numberCell(totals.steps_isolated, true)}
                  {numberCell(totals.edges)}
                  {numberCell(totals.claims)}
                  {numberCell(totals.questions)}
                  {numberCell(totals.positions)}
                  {numberCell(totals.observations)}
                  {numberCell(totals.observations - totals.observations_linked, true)}
                </tr>
              </tfoot>
            ) : null}
          </table>
        ) : null}
      </div>
    </div>
  );
}
