"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import type { CellState, Overview, OverviewRow, StageCell, StageId } from "./operations-types";
import { ScriptureGroups, TopicGroups } from "./OverviewGroups";

const stageLabels: Record<StageId, string> = {
  extraction: "抽取",
  cross_section: "跨段關係",
  review: "複審",
  adjudication: "仲裁",
  merge: "合併",
  ingest: "入庫",
};

const stateLabels: Record<CellState, string> = {
  current: "✓",
  stale: "舊",
  pending: "待重跑",
  never: "✗",
  failed: "失敗",
  running: "執行中",
  queued: "排隊中",
  no_source: "無來源",
};

const stateStyles: Record<CellState, string> = {
  current: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  // Amber, not green: a stale cell is a to-do. Re-running it would not
  // reproduce what is on disk, so it must not read as a pass.
  stale: "bg-amber-50 text-amber-900 ring-amber-200",
  // Grey, and carrying no number: whatever this stage last concluded is about
  // to be replaced by the run happening upstream of it right now.
  pending: "bg-slate-50 text-slate-400 ring-slate-200",
  never: "bg-slate-50 text-slate-400 ring-slate-200",
  failed: "bg-rose-50 text-rose-800 ring-rose-200",
  running: "bg-indigo-50 text-indigo-800 ring-indigo-200",
  queued: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  no_source: "bg-slate-50 text-slate-300 ring-slate-100",
};

const cellReasons: Record<string, string> = {
  no_recorded_input: "這次執行沒有記下它讀了什麼，無法證明還是最新的",
  source_changed: "來源原文在這次執行之後改過",
  upstream_rerun: "上游階段在這次執行之後又跑過",
  upstream_running: "上游階段正在重跑，這一格的結果即將被取代",
  from_store_not_ledger: "這一格來自主庫本身：物件在庫裡。這次入庫發生在記錄表上線之前，所以沒有時間與花費",
  no_source: "找不到已發布或已校核的逐字稿",
};

function money(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return `$${value.toFixed(2)}`;
}

/** The one number this stage is judged by, or null if it has none worth a glance. */
function qualityLabel(stage: StageId, quality: Record<string, unknown> | null): string | null {
  if (!quality) return null;
  const n = (key: string) => (typeof quality[key] === "number" ? (quality[key] as number) : null);
  if (stage === "extraction") {
    const pct = n("prose_pct");
    if (pct === null) return null;
    // Only prose carries argument, and only prose shares the percentage's
    // denominator. The whole-source count belongs in the tooltip: pairing
    // "97.7%" (129 of 132 prose) with "64" (of all 208 sentences, 51 of them
    // headings) read as a contradiction and made the good number look bad.
    const proseLeft = n("prose_unprocessed");
    return proseLeft ? `${pct}% · 正文 ${proseLeft}` : `${pct}%`;
  }
  if (stage === "cross_section") {
    // A single-section source has no cross-section relation to find, and the
    // runner writes the package through saying so. "—" rather than "0",
    // because nothing was missed.
    if (quality.skipped === "single_section") return "單段";
    const evidence = n("evidence_relations_added");
    const claims = n("claim_relations_added");
    if (evidence === null && claims === null) return null;
    return `+${evidence ?? 0} 證據 · +${claims ?? 0} 主張`;
  }
  if (stage === "review") {
    const reviewed = n("ai_reviewed");
    const onward = n("awaiting_openai_adjudication");
    if (reviewed === null) return null;
    return onward ? `${reviewed} 過 · ${onward} 仲` : `${reviewed} 過`;
  }
  if (stage === "adjudication") {
    const applied = n("auto_applied") ?? 0;
    const human = (n("human_confirmation_required") ?? 0) + (n("human_disagreement_required") ?? 0);
    return human ? `${applied} 修正 · ${human} 人工` : `${applied} 修正`;
  }
  if (stage === "ingest") {
    // Read from the store rather than a run: say so, instead of showing a
    // count this row has no run to have produced. The count is the material
    // the store holds, not the document record's revision -- that record is
    // metadata and sits at rev 1 through every re-ingest that does not touch
    // the title or the path, so it moved for none of the work done here.
    // What the store holds, every time. `+1365 ~0` was the change set's delta
    // and it answered a question nobody was asking, while hiding the half that
    // mattered: the same ingest retired 209 objects, the claim layer it
    // replaced. The deltas moved to the tooltip.
    const fragments = n("fragments");
    if (fragments !== null) return fragments ? `庫內 ${fragments} 片段` : "庫內無材料";
    if (quality.status === "already_applied") return "無變化";
    const revision = n("revision");
    if (revision !== null) return `rev ${revision}`;
    return null;
  }
  return null;
}

/** What one ingest actually moved, for the tooltip. */
function ingestDetail(stage: StageId, quality: Record<string, unknown> | null): string | null {
  if (stage !== "ingest" || !quality) return null;
  const n = (key: string) => (typeof quality[key] === "number" ? (quality[key] as number) : null);
  const created = n("created");
  const retired = n("retired");
  const updated = n("updated");
  if (created === null && retired === null && updated === null) return null;
  const parts = [
    created ? `新增 ${created}` : null,
    updated ? `更新 ${updated}` : null,
    // The half `+1365 ~0` left out. A re-extraction retires the claim layer it
    // replaces, in the same change set, and that is the number to check.
    retired ? `退役 ${retired}` : null,
  ].filter(Boolean);
  return parts.length ? `這次入庫：${parts.join("、")}` : "這次入庫沒有變動";
}

/** The whole-source picture, for the tooltip: which categories are unaccounted for. */
function coverageDetail(stage: StageId, quality: Record<string, unknown> | null): string | null {
  if (stage !== "extraction" || !quality) return null;
  const byCategory = quality.unprocessed_by_category as Record<string, number> | undefined;
  if (!byCategory || !Object.keys(byCategory).length) return null;
  const labels: Record<string, string> = {
    prose: "正文",
    heading: "標題",
    scripture_quotation: "經文引用",
    list_item: "條列",
    fragment: "片段",
  };
  const parts = Object.entries(byCategory)
    .sort((a, b) => b[1] - a[1])
    .map(([name, count]) => `${labels[name] ?? name} ${count}`);
  const total = quality.unprocessed as number | undefined;
  const recorded = quality.exclusions_recorded as number | undefined;
  const approved = quality.exclusions_terminal as number | undefined;
  const lines = [`全篇 ${total} 句沒有進入論證：${parts.join("、")}`];
  if (recorded) {
    lines.push(
      approved
        ? `其中 ${recorded} 句有理由，${approved} 句已經人工核可`
        : `其中 ${recorded} 句模型都寫了理由，但還沒有人核可`,
    );
  }
  return lines.join("\n");
}

function Cell({ stage, cell }: { stage: StageId; cell: StageCell }) {
  const quality = qualityLabel(stage, cell.quality);
  // The superseded verdict is worth keeping, just not on the face of the cell:
  // it answers "what did it say before this re-run started" without letting a
  // stale green number stand in for a live one.
  const supersededLabel =
    cell.superseded ? qualityLabel(stage, cell.superseded.quality) : null;
  const tip = [
    ingestDetail(stage, cell.quality),
    coverageDetail(stage, cell.quality),
    supersededLabel ? `重跑前：${stateLabels[cell.superseded!.state]} ${supersededLabel}` : null,
    cell.state === "no_source" ? cellReasons.no_source : null,
    cell.reason ? cellReasons[cell.reason] ?? cell.reason : null,
    cell.store?.updated_at ? `主庫材料更新於：${new Date(cell.store.updated_at).toLocaleString("zh-TW")}` : null,
    cell.store ? `來源記錄 rev ${cell.store.revision}` : null,
    cell.run?.started_at ? `最後一次：${new Date(cell.run.started_at).toLocaleString("zh-TW")}` : null,
    cell.run?.trigger ? `觸發：${cell.run.trigger}${cell.run.triggered_by ? ` (${cell.run.triggered_by})` : ""}` : null,
    cell.run && cell.run.cost_usd !== null ? `花費：${money(cell.run.cost_usd)}` : null,
    cell.run?.error_message ?? null,
  ].filter(Boolean).join("\n");
  return (
    <span
      title={tip || undefined}
      className={`inline-flex w-[6.25rem] shrink-0 flex-col items-center overflow-hidden rounded-md px-1.5 py-1 text-[11px] font-semibold leading-tight ring-1 ring-inset ${stateStyles[cell.state]}`}
    >
      <span>{stateLabels[cell.state]}{cell.store ? "*" : ""}</span>
      {quality && (
        <span className="mt-0.5 w-full truncate text-center font-normal opacity-80">{quality}</span>
      )}
    </span>
  );
}

export function SermonOverview() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [state, setState] = useState("all");
  const [tab, setTab] = useState<"scripture" | "topic">("scripture");

  async function load() {
    setLoading(true);
    try {
      const response = await fetch("/api/admin/wang/operations/overview", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "無法讀取總表");
      setData(payload);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  const rows = useMemo(() => {
    if (!data) return [];
    return data.rows.filter((row) => {
      const states = Object.values(row.stages).map((cell) => cell.state);
      if (state === "problem") return states.some((s) => s === "failed" || s === "stale");
      if (state === "untouched") return states.every((s) => s === "never" || s === "no_source");
      if (state === "started") return states.some((s) => s === "current" || s === "stale");
      return true;
    });
  }, [data, state]);

  const renderRow = (row: OverviewRow) => <Row row={row} stages={data?.stages ?? []} />;

  if (loading && !data) {
    return (
      <div className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-white p-8 text-slate-600">
        <Loader2 className="h-5 w-5 animate-spin" /> 讀取中…
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-2xl border border-rose-200 bg-rose-50 p-6 text-rose-900">
        <p className="font-bold">無法讀取總表</p>
        <p className="mt-1 text-sm">{error}</p>
      </div>
    );
  }
  if (!data) return null;

  const { summary } = data;

  return (
    <section className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="來源" value={String(summary.rows)}
          detail={`講道 ${summary.sermons} · 母本 ${summary.notes_manuscripts}`} />
        <Metric label="無來源" value={String(summary.unproofread)}
          detail="找不到已發布或已校核的逐字稿" />
        <Metric label="已入庫" value={`${summary.ingested} 篇`}
          detail={`已記錄 ${summary.runs_recorded} 次執行`} />
        <Metric label="已記錄的花費" value={money(summary.spend_usd)}
          detail={`價目表 ${data.price_version}`} />
      </div>

      {data.warnings.length > 0 && (
        <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4">
          <p className="flex items-center gap-2 font-bold text-amber-900">
            <AlertTriangle className="h-4 w-4" />這張表知道自己哪裡不完整
          </p>
          <ul className="mt-2 space-y-1 text-sm leading-6 text-amber-900">
            {data.warnings.map((warning) => (
              <li key={warning.code}>· {warning.message}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {([["scripture", "聖經目錄"], ["topic", "講道主題"]] as const).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`rounded-xl px-4 py-2 text-[13px] font-bold ${
              tab === key ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-100"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3">
        <label className="text-sm font-semibold text-slate-600">狀態
          <select value={state} onChange={(event) => setState(event.target.value)}
            className="ml-2 rounded-lg border border-slate-300 px-2 py-1 text-sm font-normal">
            <option value="all">全部</option>
            <option value="problem">有問題（失敗或舊）</option>
            <option value="started">動過的</option>
            <option value="untouched">完全沒動</option>
          </select>
        </label>
        <span className="ml-auto text-sm text-slate-500">{rows.length} / {summary.rows} 篇</span>
        <button onClick={load}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-semibold text-slate-700 hover:bg-slate-50">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />重新讀取
        </button>
      </div>

      <div className="overflow-x-auto">
        <div className="min-w-[68rem]">
          <div className="flex items-center gap-2 px-4 pb-1 pl-10 text-[11px] font-bold text-slate-500">
            <span className="min-w-0 flex-1">來源</span>
            {data.stages.map((stage) => (
              <span key={stage} className="w-[6.25rem] shrink-0 text-center">{stageLabels[stage]}</span>
            ))}
            <span className="w-14 shrink-0 text-center">文章</span>
          </div>
          {tab === "scripture" ? (
            <ScriptureGroups rows={rows} render={renderRow} />
          ) : (
            <TopicGroups rows={rows} render={renderRow} />
          )}
        </div>
      </div>

      <p className="text-xs leading-6 text-slate-500">
        每一格都來自執行記錄表，不掃描 staging 目錄。<strong>入庫是例外</strong>：那一格直接讀主庫——物件在不在庫裡，主庫自己知道，
        而且記錄表上線之前就知道。標成 <code>✓*</code> 的入庫格就是這種：確實在庫裡，但那次入庫早於記錄表，所以沒有時間與花費。
        其他四個階段只認記錄表，所以磁碟上已有產出的來源在這裡仍然顯示「✗ 沒跑過」——這是實話，不是漏抓。價目表 <code>{data.price_version}</code>（{data.price_effective} 起）：{data.price_source}。
      </p>
    </section>
  );
}

function Row({ row, stages }: { row: OverviewRow; stages: StageId[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-slate-100 py-1.5 pl-10 pr-4 text-[12.5px] last:border-b-0 hover:bg-indigo-50/40">
      <span className="min-w-0 flex-1">
        <span className="block truncate text-slate-900">
          {row.coverage_source_id ? (
            // New tab on purpose: reading one sermon's coverage is a detour
            // from working down the queue, and losing your place in 242 rows
            // to come back is the whole cost of the trip.
            <a
              href={`/admin/wang/source-coverage?source=${encodeURIComponent(row.coverage_source_id)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="font-semibold text-indigo-700 hover:underline"
              title="在新分頁開啟來源覆蓋"
            >
              {row.title}
            </a>
          ) : (
            row.title
          )}
          {row.kind === "notes_manuscript" ? (
            <i className="ml-2 font-mono text-[10.5px] not-italic text-slate-400">母本</i>
          ) : null}
        </span>
        <span className="block truncate font-mono text-[10.5px] text-slate-400">
          {row.source_id}
          {row.manuscript_file ? ` · ${row.manuscript_file}` : ""}
        </span>
      </span>
      {stages.map((stage) => (
        <Cell key={stage} stage={stage} cell={row.stages[stage]} />
      ))}
      <span className="w-14 shrink-0 text-center font-mono text-[11px]">
        {row.articles.length ? (
          <span title={row.articles.join("\n")} className="font-semibold text-indigo-700">
            {row.articles.length} 篇
          </span>
        ) : (
          <span className="text-slate-300">—</span>
        )}
      </span>
    </div>
  );
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
      <p className="text-xs font-bold uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-black text-slate-950">{value}</p>
      <p className="mt-0.5 text-xs text-slate-500">{detail}</p>
    </div>
  );
}
