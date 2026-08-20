"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, AlertTriangle } from "lucide-react";
import { CatalogView } from "./CatalogView";
import { ClaimPanel } from "./ClaimPanel";
import { SourceText } from "./SourceText";
import type { Claim, Overview, SourceDetail, SourceMeta } from "./types";
import { percent } from "./types";

/**
 * Which source is open is part of the address, not of this component.
 *
 * `useSearchParams` makes the route ask for the request's query string, and a
 * prerendered route may only do that inside a boundary — without this one the
 * production build refuses to build the page at all.
 */
export default function SourceCoveragePage() {
  return (
    <Suspense fallback={<p className="px-6 py-5 text-sm text-slate-400">載入中…</p>}>
      <SourceCoverageView />
    </Suspense>
  );
}

function SourceCoverageView() {
  const router = useRouter();
  const pathname = usePathname();
  const requestedSourceId = useSearchParams().get("source");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [detail, setDetail] = useState<SourceDetail | null>(null);
  const [detailError, setDetailError] = useState<{ sourceId: string; message: string } | null>(null);
  const [selectedFragmentId, setSelectedFragmentId] = useState<string | null>(null);
  const [claimFragmentIds, setClaimFragmentIds] = useState<Set<string>>(new Set());
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [scrollTo, setScrollTo] = useState<{ ordinal: number; nonce: number } | null>(null);
  const [overviewTab, setOverviewTab] = useState<"catalog" | "table">("catalog");
  const [markUncovered, setMarkUncovered] = useState(true);
  const [onlyUncovered, setOnlyUncovered] = useState(false);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const nonce = useRef(0);
  const shell = useRef<HTMLDivElement>(null);
  const [shellHeight, setShellHeight] = useState<number | null>(null);

  // What is loaded counts as open only while it is the source the address asks
  // for, so neither a stale detail nor a stale failure outlives the navigation
  // away from it.
  const activeDetail = detail && detail.source.source_id === requestedSourceId ? detail : null;
  const failure = requestedSourceId ? (detailError?.sourceId === requestedSourceId ? detailError.message : "") : error;

  // The two panes fill whatever the admin chrome leaves, measured rather than
  // guessed: a change to the header or the Wang sub-nav must not clip them.
  useEffect(() => {
    const measure = () => {
      if (shell.current) setShellHeight(window.innerHeight - shell.current.getBoundingClientRect().top);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [activeDetail]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch("/api/admin/source-coverage/sources", { cache: "no-store" });
        if (!response.ok) throw new Error(`來源覆蓋服務回傳 ${response.status}`);
        const data = (await response.json()) as Overview;
        if (!cancelled) setOverview(data);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "無法讀取來源覆蓋");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // The address decides which source is loaded, so opening a pasted link, a
  // refresh and the back button all take the same path as clicking a row.
  useEffect(() => {
    if (!requestedSourceId) return;
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(`/api/admin/source-coverage/sources/${encodeURIComponent(requestedSourceId)}`, {
          cache: "no-store",
        });
        if (!response.ok) throw new Error(`來源服務回傳 ${response.status}`);
        const data = (await response.json()) as SourceDetail;
        if (cancelled) return;
        setDetail(data);
        // A source opens on itself: nothing selected, and no filter carried
        // over from whichever source was read before it.
        setSelectedFragmentId(null);
        setClaimFragmentIds(new Set());
        setSelectedClaimId(null);
        setFilter("");
        setOnlyUncovered(false);
      } catch (reason) {
        if (!cancelled)
          setDetailError({
            sourceId: requestedSourceId,
            message: reason instanceof Error ? reason.message : "無法讀取這個來源",
          });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [requestedSourceId]);

  const openSource = useCallback(
    (sourceId: string) => router.push(`${pathname}?source=${encodeURIComponent(sourceId)}`),
    [pathname, router],
  );

  // Deep link from the operations overview: `?source=<id>` opens straight into
  // that source instead of landing on the catalog and asking the reader to find
  // a sermon they had already picked. Read from `location` rather than
  // `useSearchParams` so this client page needs no Suspense boundary.
  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("source");
    if (requested) void openSource(requested);
  }, [openSource]);

  const scrollToOrdinal = useCallback((ordinal: number | null) => {
    if (ordinal === null) return;
    nonce.current += 1;
    setScrollTo({ ordinal, nonce: nonce.current });
  }, []);

  const selectFragment = useCallback(
    (fragmentId: string | null) => {
      setSelectedFragmentId(fragmentId);
      setClaimFragmentIds(new Set());
      setSelectedClaimId(null);
      if (fragmentId && activeDetail) scrollToOrdinal(activeDetail.fragments[fragmentId]?.segment_ordinal ?? null);
    },
    [activeDetail, scrollToOrdinal],
  );

  const selectClaim = useCallback(
    (claim: Claim) => {
      setSelectedFragmentId(null);
      setClaimFragmentIds(new Set(claim.fragment_ids));
      setSelectedClaimId(claim.id);
      setOnlyUncovered(false);
      scrollToOrdinal(claim.first_ordinal);
    },
    [scrollToOrdinal],
  );

  const highlighted = useMemo(
    () => (selectedFragmentId ? new Set([selectedFragmentId]) : claimFragmentIds),
    [claimFragmentIds, selectedFragmentId],
  );

  if (failure && !activeDetail)
    return (
      <div className="rounded-xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700">
        {failure}
        <p className="mt-2 text-xs text-rose-600">來源覆蓋需要 KNOWLEDGE_DATABASE_URL 指向 authoring store，且來源檔案可讀。</p>
        {/* A link can now name a source that the store does not have; that
            reader needs a way out other than editing the address. */}
        {requestedSourceId ? (
          <button
            type="button"
            onClick={() => router.push(pathname)}
            className="mt-3 inline-flex items-center gap-1 rounded-full border border-rose-300 px-2.5 py-1 text-xs text-rose-700 hover:bg-rose-100"
          >
            <ArrowLeft className="h-3.5 w-3.5" />全庫總覽
          </button>
        ) : null}
      </div>
    );

  if (activeDetail) {
    const stats = activeDetail.source.stats;
    return (
      <div ref={shell} style={{ height: shellHeight ?? undefined }} className="-mx-2 -my-6 flex flex-col overflow-hidden bg-white">
        <header className="flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-4 py-2">
          <button
            type="button"
            onClick={() => router.push(pathname)}
            className="inline-flex items-center gap-1 rounded-full border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-100"
          >
            <ArrowLeft className="h-3.5 w-3.5" />全庫總覽
          </button>
          <span className="text-sm font-semibold text-slate-800">{activeDetail.source.title}</span>
          <span className="font-mono text-[11px] text-slate-400">{activeDetail.source.source_type}</span>
          <input
            type="search"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="在這份來源裡找…"
            className="w-44 rounded-lg border border-slate-300 px-2.5 py-1.5 text-[13px]"
          />
          <label className="inline-flex items-center gap-1.5 text-[11.5px] text-slate-600">
            <input type="checkbox" checked={onlyUncovered} onChange={(event) => setOnlyUncovered(event.target.checked)} />
            只看完全未抽取的 segment
          </label>
          <label className="inline-flex items-center gap-1.5 text-[11.5px] text-slate-600">
            <input type="checkbox" checked={markUncovered} onChange={(event) => setMarkUncovered(event.target.checked)} />
            標出未抽取的句子
          </label>
          <div className="ml-auto flex flex-wrap items-center gap-3 font-mono text-xs text-slate-500">
            <span>
              segments <b className="text-slate-900">{stats.segments_covered}</b>/{stats.segments}
              {stats.heading_segments ? <span className="ml-1 text-slate-400">（{stats.heading_segments} 個是標題）</span> : null}
            </span>
            <span>
              句子 <b className="text-slate-900">{stats.prose_sentences_covered}</b>/{stats.prose_sentences}
              <span className={percent(stats.prose_sentences_covered, stats.prose_sentences) < 50 ? "ml-1 text-rose-600" : "ml-1"}>
                {percent(stats.prose_sentences_covered, stats.prose_sentences)}%
              </span>
            </span>
            <span>
              fragments <b className="text-slate-900">{stats.fragments_placed}</b>/{stats.fragments}
            </span>
            <span>claims <b className="text-slate-900">{stats.claims}</b></span>
          </div>
        </header>
        {activeDetail.source.file_state !== "current" ? (
          <p className="flex items-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-1.5 text-[12px] text-amber-900">
            <AlertTriangle className="h-4 w-4" />
            {activeDetail.source.file_state === "missing"
              ? `讀不到來源檔案：${activeDetail.source.source_path || "（store 沒有記下路徑）"}`
              : "來源檔案的 SHA256 與 source_document 記錄的不同——這份文字在抽取之後被改過，下面的每一個位置都只是近似。"}
          </p>
        ) : null}
        <div className="flex min-h-0 min-w-0 flex-1">
          <SourceText
            detail={activeDetail}
            selectedFragmentIds={highlighted}
            onSelectFragment={selectFragment}
            markUncovered={markUncovered}
            onlyUncovered={onlyUncovered}
            filter={filter}
            scrollTo={scrollTo}
          />
          <ClaimPanel
            detail={activeDetail}
            selectedFragmentId={selectedFragmentId}
            selectedClaimId={selectedClaimId}
            onSelectFragment={selectFragment}
            onSelectClaim={selectClaim}
            onClearClaim={() => {
              setSelectedClaimId(null);
              setClaimFragmentIds(new Set());
            }}
          />
        </div>
      </div>
    );
  }

  // A source was asked for by name: wait for it rather than showing the
  // catalog, which would flash a list the reader never asked to see.
  if (requestedSourceId)
    return <p className="px-6 py-5 text-sm text-slate-400">載入 {requestedSourceId} …</p>;

  const totals = overview?.totals;

  return (
    <div ref={shell} style={{ height: shellHeight ?? undefined }} className="-mx-2 -my-6 flex flex-col overflow-hidden bg-white">
      <header className="border-b border-slate-200 bg-white px-4 py-2">
        <h1 className="text-[15px] font-bold text-slate-900">來源覆蓋</h1>
      </header>
      <div className="flex-1 overflow-auto px-6 py-5">
        {totals ? (
          <p className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-1 font-mono text-[12px] text-slate-600">
            <span>
              講道目錄 <b className="text-slate-900">{totals.catalog_sermons_extracted}</b>/{totals.catalog_sermons} 篇已抽取
              <span className="ml-1 text-rose-600">{percent(totals.catalog_sermons_extracted, totals.catalog_sermons)}%</span>
            </span>
            <span>
              經卷 <b className="text-slate-900">{totals.catalog_books_extracted}</b>/{totals.catalog_books}
              <span className="ml-1 text-rose-600">
                （{totals.catalog_books - totals.catalog_books_extracted} 卷在 claim 層裡是空的）
              </span>
            </span>
            <span>母本 {totals.notes_manuscripts} 份</span>
          </p>
        ) : null}
        <div className="mb-4 flex flex-wrap gap-2 border-b border-slate-200 pb-3">
          {(
            [
              ["catalog", "按目錄瀏覽"],
              ["table", "已抽取來源總表"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setOverviewTab(key)}
              className={`rounded-xl px-4 py-2 text-[13px] font-bold ${
                overviewTab === key ? "bg-slate-950 text-white" : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {loading && !overview ? <p className="text-sm text-slate-400">載入中…</p> : null}
        {overview && overviewTab === "catalog" ? (
          <CatalogView catalog={overview.catalog} sources={overview.sources} onOpen={openSource} />
        ) : null}
        {overview && overviewTab === "table" ? (
          <table className="w-full border-collapse text-[12.5px]">
            <thead>
              <tr className="border-b border-slate-300 text-[11px] font-semibold text-slate-500">
                <th className="px-2 pb-1.5 text-left font-mono">source</th>
                {["segments", "covered", "句子", "covered", "%", "fragments", "placed", "evidence_steps", "claims"].map(
                  (label, index) => (
                    <th key={`${label}-${index}`} className="whitespace-nowrap px-2 pb-1.5 text-right font-mono">
                      {label}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {overview.sources.map((source) => (
                <SourceRow key={source.source_id} source={source} onOpen={() => openSource(source.source_id)} />
              ))}
            </tbody>
            {totals ? (
              <tfoot>
                <tr className="font-mono text-slate-700">
                  <td className="px-2 py-2 font-sans font-bold text-slate-900">全庫 {totals.sources} 個 source</td>
                  <td className="px-2 py-2 text-right">{totals.segments}</td>
                  <td className="px-2 py-2 text-right">{totals.segments_covered}</td>
                  <td className="px-2 py-2 text-right">{totals.prose_sentences}</td>
                  <td className="px-2 py-2 text-right">{totals.prose_sentences_covered}</td>
                  <td className="px-2 py-2 text-right text-rose-600">{percent(totals.prose_sentences_covered, totals.prose_sentences)}%</td>
                  <td className="px-2 py-2 text-right">{totals.fragments}</td>
                  <td className="px-2 py-2 text-right">{totals.fragments_placed}</td>
                  <td className="px-2 py-2 text-right">{totals.steps}</td>
                  <td className="px-2 py-2 text-right">{totals.claims}</td>
                </tr>
              </tfoot>
            ) : null}
          </table>
        ) : null}
      </div>
    </div>
  );
}

function SourceRow({ source, onOpen }: { source: SourceMeta; onOpen: () => void }) {
  const stats = source.stats;
  const share = percent(stats.sentences_covered, stats.sentences);
  const unplaced = stats.fragments - stats.fragments_placed;
  return (
    <tr onClick={onOpen} className="cursor-pointer border-b border-slate-200 hover:bg-indigo-50">
      <td className="max-w-[38ch] truncate px-2 py-1.5">
        {source.title}
        {source.source_type === "notes_manuscript" ? (
          <i className="ml-2 font-mono text-[10.5px] not-italic text-slate-400">母本</i>
        ) : null}
        {source.file_state !== "current" ? (
          <i className="ml-2 font-mono text-[10.5px] not-italic text-amber-700">
            {source.file_state === "missing" ? "讀不到檔案" : "來源已變更"}
          </i>
        ) : null}
      </td>
      <td className="px-2 py-1.5 text-right font-mono text-slate-700">{stats.segments}</td>
      <td className="px-2 py-1.5 text-right font-mono text-slate-700">{stats.segments_covered}</td>
      <td className="px-2 py-1.5 text-right font-mono text-slate-700">{stats.sentences}</td>
      <td className="px-2 py-1.5 text-right font-mono text-slate-700">{stats.sentences_covered}</td>
      <td className="whitespace-nowrap px-2 py-1.5 text-right font-mono text-slate-700">
        {share}%
        <span className="ml-1.5 inline-block h-[7px] w-24 rounded-sm bg-rose-200 align-middle">
          <span className="block h-[7px] rounded-sm bg-indigo-500" style={{ width: `${share}%` }} />
        </span>
      </td>
      <td className="px-2 py-1.5 text-right font-mono text-slate-700">{stats.fragments}</td>
      <td className={`px-2 py-1.5 text-right font-mono ${unplaced ? "text-rose-600" : "text-slate-700"}`}>
        {stats.fragments_placed}
      </td>
      <td className="px-2 py-1.5 text-right font-mono text-slate-700">{stats.steps}</td>
      <td className="px-2 py-1.5 text-right font-mono text-slate-700">{stats.claims}</td>
    </tr>
  );
}
