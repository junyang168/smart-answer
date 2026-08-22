"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, ArrowRight, BookOpen, ChevronRight, Loader2, Search } from "lucide-react";
import { AsOfStrip, StatusBadge, WorkbenchHeader } from "./ViewpointChrome";
import type { Envelope, OverviewData, RecallDiagnostics, ViewpointSummary } from "./types";

async function read<T>(url: string): Promise<T> {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || `服务返回 ${response.status}`);
  return payload as T;
}

export function ViewpointExplorer() {
  const router = useRouter();
  const params = useSearchParams();
  const [overview, setOverview] = useState<Envelope<OverviewData> | null>(null);
  const [listing, setListing] = useState<Envelope<{ items: ViewpointSummary[]; total: number; next_cursor: string | null }> | null>(null);
  const [recall, setRecall] = useState<Envelope<RecallDiagnostics> | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState(params.get("q") ?? "");

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const queryString = params.toString();
      const [summary, rows, recallDiagnostics] = await Promise.all([
        read<Envelope<OverviewData>>("/api/admin/wang/viewpoints/overview"),
        read<Envelope<{ items: ViewpointSummary[]; total: number; next_cursor: string | null }>>(`/api/admin/wang/viewpoints${queryString ? `?${queryString}` : ""}`),
        read<Envelope<RecallDiagnostics>>("/api/admin/wang/viewpoints/recall-blocking?limit=8"),
      ]);
      setOverview(summary); setListing(rows); setRecall(recallDiagnostics);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取观点主数据");
    } finally { setLoading(false); }
  }, [params]);

  // Async repository synchronization; state updates occur as requests settle.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const next = new URLSearchParams(params.toString());
    query.trim() ? next.set("q", query.trim()) : next.delete("q");
    next.delete("cursor");
    router.push(`/admin/wang/viewpoints${next.size ? `?${next}` : ""}`);
  }

  if (loading && !listing) return <p className="flex items-center gap-2 py-10 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />正在编译同一快照的观点投影…</p>;
  if (error || !overview || !listing || !recall) return <p className="flex items-center gap-2 rounded-xl bg-rose-50 p-4 text-sm text-rose-800"><AlertCircle className="h-4 w-4" />{error || "没有资料"}</p>;

  const coverage = overview.data.source_coverage;
  const resolution = overview.data.claim_resolution;
  const resolved = resolution?.resolved_count;
  const totalClaims = resolution?.input_claim_count;
  const recallStats = recall.data.statistics;
  const metrics = [
    { label: "来源覆盖", value: coverage.total == null ? "未知" : `${coverage.covered}/${coverage.total}`, href: "/admin/wang/source-coverage", detail: coverage.status },
    { label: "Claim 归宿", value: totalClaims == null ? "未知" : `${resolved}/${totalClaims}`, href: "/admin/wang/viewpoints#snapshot-provenance", detail: overview.as_of.resolution_status },
    { label: "活跃观点", value: String(overview.data.active_viewpoints), href: "/admin/wang/viewpoints", detail: "稳定 identity" },
    { label: "人工例外", value: String(overview.data.exceptions), href: "/admin/wang/viewpoint-exceptions", detail: "仅高风险判断" },
    { label: "受影响产品", value: String(overview.data.affected_products), href: "/admin/wang/viewpoints?impact=1", detail: "文章 / QA / 搜索" },
    { label: "召回邻域", value: recall.data.available && recallStats ? `${recallStats.covered_claim_count}/${recallStats.eligible_claim_count}` : "未配置", href: "/admin/wang/viewpoints#recall-blocking", detail: "只决定比较范围" },
  ];

  return (
    <main className="space-y-6 pb-10">
      <WorkbenchHeader exceptions={overview.data.exceptions} />
      <AsOfStrip asOf={listing.as_of} projectionSha={listing.projection_sha256} />
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6" aria-label="当前快照状态">
        {metrics.map((metric) => (
          <Link key={metric.label} href={metric.href} className="group rounded-2xl border border-slate-200 bg-white p-4 hover:border-indigo-300">
            <p className="text-xs font-bold text-slate-500">{metric.label}</p>
            <p className="mt-1 flex items-center justify-between text-2xl font-black text-slate-950">{metric.value}<ArrowRight className="h-4 w-4 text-slate-300 group-hover:text-indigo-600" /></p>
            <p className="mt-1 text-xs text-slate-400">{metric.detail}</p>
          </Link>
        ))}
      </section>

      <section id="recall-blocking" className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div className="border-b border-slate-200 p-4 sm:p-5">
          <h2 className="text-lg font-black text-slate-950">Candidate Recall Blocking</h2>
          <p className="mt-1 text-sm text-slate-500">关键词、经文章节与 Claim role 只负责把可能相关的主张放在一起；这里的连线不是 duplicate 或 viewpoint membership。</p>
        </div>
        {!recall.data.available || !recallStats ? (
          <p className="p-5 text-sm text-slate-500">后端尚未配置 SHA-bound recall artifact。</p>
        ) : (
          <div className="space-y-5 p-4 sm:p-5">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {([
                ["有邻居 Claim", recallStats.covered_claim_count],
                ["无邻居 Claim", recallStats.uncovered_claim_count],
                ["候选对", recallStats.unique_candidate_pair_count],
                ["被抑制大 Block", recallStats.suppressed_block_count],
              ] as const).map(([label, value]) => <div key={label} className="rounded-xl bg-slate-50 p-3"><p className="text-xs font-bold text-slate-500">{label}</p><p className="mt-1 text-xl font-black text-slate-950">{value}</p></div>)}
            </div>
            <p className="text-xs text-slate-500">Known-positive recall：{recall.data.known_positive_recall?.eligible_pair_count ? `${recall.data.known_positive_recall.found_pair_count}/${recall.data.known_positive_recall.eligible_pair_count}（${Math.round((recall.data.known_positive_recall.recall ?? 0) * 100)}%）` : "当前19篇 scope 内没有 reviewed duplicate gold pair，因此不报告百分比"}；无法解析到章节的宽泛经文标签 {recall.data.unparsed_scripture_refs.length} 个。</p>
            {recall.data.suppressed_blocks.length > 0 && <div><h3 className="text-sm font-black text-slate-800">预算外的大 Block</h3><div className="mt-2 flex flex-wrap gap-2">{recall.data.suppressed_blocks.map((block) => <span key={block.block_key} className="rounded-full bg-amber-50 px-3 py-1 text-xs font-bold text-amber-800">{block.block_key} · {block.claim_count}</span>)}</div></div>}
            <div className="divide-y divide-slate-100 rounded-xl border border-slate-200">
              {recall.data.items.map((item) => <div key={item.focal_claim_id} className="p-3 sm:p-4">
                <div className="flex flex-wrap items-center gap-2"><span className="rounded bg-indigo-50 px-2 py-1 text-xs font-bold text-indigo-700">{item.claim_role}</span><span className="font-mono text-[11px] text-slate-400">{item.focal_claim_id}</span><span className="ml-auto text-xs font-bold text-slate-500">{item.neighbors.length} 个邻居</span></div>
                <p className="mt-2 text-sm font-semibold leading-6 text-slate-900">{item.focal_statement ?? "Claim 已不在当前 store"}</p>
                <div className="mt-2 flex flex-wrap gap-1.5">{item.neighbors.slice(0, 4).map((neighbor) => <span key={neighbor.claim_id} title={neighbor.statement ?? neighbor.claim_id} className="max-w-full truncate rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">{neighbor.claim_id} · {neighbor.shared_topic_terms.join(" / ") || neighbor.shared_scripture_chapters.join(" / ")}</span>)}</div>
              </div>)}
            </div>
            <p className="break-all font-mono text-[11px] text-slate-400">artifact {recall.data.artifact_sha256}</p>
          </div>
        )}
      </section>

      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div className="border-b border-slate-200 p-4 sm:p-5">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div><h2 className="text-lg font-black text-slate-950">Viewpoint Explorer</h2><p className="mt-1 text-sm text-slate-500">{listing.data.total} 个稳定观点身份</p></div>
            <form onSubmit={submit} className="flex w-full max-w-md gap-2 sm:w-auto">
              <label className="relative flex-1">
                <span className="sr-only">按观点措辞或 ID 搜索</span><Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
                <input value={query} onChange={(event) => setQuery(event.target.value)} className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-3 text-sm" placeholder="观点措辞或 ID" />
              </label>
              <button className="rounded-lg bg-slate-950 px-4 py-2 text-sm font-bold text-white">查找</button>
            </form>
          </div>
        </div>
        {listing.data.items.length === 0 ? (
          <div className="px-5 py-14 text-center"><BookOpen className="mx-auto h-7 w-7 text-slate-300" /><p className="mt-3 font-bold text-slate-800">这个快照尚无符合条件的观点</p><p className="mt-1 text-sm text-slate-500">这不是零覆盖；请查看上方 CoverageSnapshot 与 ResolutionLedger 状态。</p></div>
        ) : (
          <div className="divide-y divide-slate-100">
            {listing.data.items.map((item) => {
              const back = encodeURIComponent(`/admin/wang/viewpoints?${params.toString()}`);
              return (
                <Link key={item.viewpoint_id} href={`/admin/wang/viewpoints/${encodeURIComponent(item.viewpoint_id)}?snapshot=${encodeURIComponent(listing.as_of.registry_snapshot_id)}&from=${back}`} className="group grid gap-4 p-4 hover:bg-slate-50 sm:p-5 lg:grid-cols-[minmax(0,1fr)_auto]">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2"><StatusBadge value={item.review_status} /><span className="text-xs font-medium text-indigo-700">{item.wording_label}</span></div>
                    <h3 className="mt-2 text-base font-bold leading-6 text-slate-950 group-hover:text-indigo-700">{item.core_proposition}</h3>
                    <p className="mt-2 break-all font-mono text-[11px] text-slate-400">{item.viewpoint_id} · {item.viewpoint_revision_id}</p>
                    <div className="mt-2 flex flex-wrap gap-1.5">{item.scripture_scope.map((ref) => <span key={ref} className="rounded bg-indigo-50 px-2 py-1 text-xs font-semibold text-indigo-700">{ref}</span>)}</div>
                  </div>
                  <div className="flex items-center gap-5 text-center text-xs text-slate-500 lg:justify-end">
                    {([['成员', item.counts.members], ['来源', item.counts.sources], ['路线', item.counts.routes], ['张力', item.counts.tensions]] as const).map(([label, value]) => <span key={label}><strong className="block text-lg text-slate-900">{value}</strong>{label}</span>)}
                    <ChevronRight className="h-5 w-5 text-slate-300 group-hover:text-indigo-600" />
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </section>
      {listing.data.next_cursor && <button onClick={() => { const next = new URLSearchParams(params.toString()); next.set("cursor", listing.data.next_cursor!); router.push(`/admin/wang/viewpoints?${next}`); }} className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-bold text-slate-700">下一页</button>}
    </main>
  );
}
