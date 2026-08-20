"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import type { CellState, Overview, OverviewRow, StageCell, StageId } from "./operations-types";

const stageLabels: Record<StageId, string> = {
  extraction: "抽取",
  review: "複審",
  adjudication: "仲裁",
  merge: "合併",
  ingest: "入庫",
};

const stateLabels: Record<CellState, string> = {
  current: "✓",
  stale: "舊",
  never: "✗",
  failed: "失敗",
  running: "執行中",
  queued: "排隊中",
  no_source: "無原文",
};

const stateStyles: Record<CellState, string> = {
  current: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  // Amber, not green: a stale cell is a to-do. Re-running it would not
  // reproduce what is on disk, so it must not read as a pass.
  stale: "bg-amber-50 text-amber-900 ring-amber-200",
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
  from_store_not_ledger: "這一格來自主庫本身：物件在庫裡。這次入庫發生在記錄表上線之前，所以沒有時間與花費",
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
    const unprocessed = n("unprocessed");
    if (pct === null) return null;
    return unprocessed ? `${pct}% · ${unprocessed} 未答` : `${pct}%`;
  }
  if (stage === "review") {
    const reviewed = n("ai_reviewed");
    const onward = n("awaiting_openai_adjudication");
    if (reviewed === null) return null;
    return onward ? `${reviewed} 過 · ${onward} 送仲裁` : `${reviewed} 過`;
  }
  if (stage === "adjudication") {
    const applied = n("auto_applied") ?? 0;
    const human = (n("human_confirmation_required") ?? 0) + (n("human_disagreement_required") ?? 0);
    return human ? `${applied} 修正 · ${human} 人工` : `${applied} 修正`;
  }
  if (stage === "ingest") {
    // Read from the store rather than a run: say so, instead of showing a
    // count this row has no run to have produced.
    const revision = n("revision");
    if (revision !== null && quality.status === undefined) return `rev ${revision}`;
    if (quality.status === "already_applied") return "無變化";
    const created = n("created") ?? 0;
    const updated = n("updated") ?? 0;
    return created || updated ? `+${created} ~${updated}` : "無變化";
  }
  return null;
}

function Cell({ stage, cell }: { stage: StageId; cell: StageCell }) {
  const quality = qualityLabel(stage, cell.quality);
  const tip = [
    cell.reason ? cellReasons[cell.reason] ?? cell.reason : null,
    cell.store?.updated_at ? `主庫更新於：${new Date(cell.store.updated_at).toLocaleString("zh-TW")}` : null,
    cell.run?.started_at ? `最後一次：${new Date(cell.run.started_at).toLocaleString("zh-TW")}` : null,
    cell.run?.trigger ? `觸發：${cell.run.trigger}${cell.run.triggered_by ? ` (${cell.run.triggered_by})` : ""}` : null,
    cell.run && cell.run.cost_usd !== null ? `花費：${money(cell.run.cost_usd)}` : null,
    cell.run?.error_message ?? null,
  ].filter(Boolean).join("\n");
  return (
    <td className="px-2 py-1.5 align-middle">
      <span
        title={tip || undefined}
        className={`inline-flex min-w-[3.5rem] flex-col items-center rounded-md px-2 py-1 text-xs font-semibold ring-1 ring-inset ${stateStyles[cell.state]} ${cell.store ? "ring-dashed" : ""}`}
      >
        <span>{stateLabels[cell.state]}{cell.store ? "*" : ""}</span>
        {quality && <span className="mt-0.5 font-normal opacity-80">{quality}</span>}
      </span>
    </td>
  );
}

export function SermonOverview() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [series, setSeries] = useState("all");
  const [state, setState] = useState("all");

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

  const allSeries = useMemo(() => {
    if (!data) return [];
    return Array.from(new Set(data.rows.map((row) => row.series).filter(Boolean) as string[])).sort();
  }, [data]);

  const rows = useMemo(() => {
    if (!data) return [];
    return data.rows.filter((row) => {
      if (series !== "all" && row.series !== series) return false;
      const states = Object.values(row.stages).map((cell) => cell.state);
      if (state === "problem") return states.some((s) => s === "failed" || s === "stale");
      if (state === "untouched") return states.every((s) => s === "never" || s === "no_source");
      if (state === "started") return states.some((s) => s === "current" || s === "stale");
      return true;
    });
  }, [data, series, state]);

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
        <Metric label="還沒有原文" value={String(summary.without_source)}
          detail="抽取之前要先有逐字稿或母本" />
        <Metric label="已記錄的執行" value={String(summary.runs_recorded)}
          detail={summary.succeeded_runs_without_a_price
            ? `${summary.succeeded_runs_without_a_price} 次沒有價格`
            : "記錄表上線後的執行"} />
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

      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3">
        <label className="text-sm font-semibold text-slate-600">系列
          <select value={series} onChange={(event) => setSeries(event.target.value)}
            className="ml-2 rounded-lg border border-slate-300 px-2 py-1 text-sm font-normal">
            <option value="all">全部</option>
            {allSeries.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
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

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs font-bold text-slate-600">
            <tr>
              <th className="px-4 py-2.5">來源</th>
              {data.stages.map((stage) => (
                <th key={stage} className="px-2 py-2.5 text-center">{stageLabels[stage]}</th>
              ))}
              <th className="px-3 py-2.5 text-center">文章</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((row) => <Row key={`${row.kind}:${row.source_id}`} row={row} stages={data.stages} />)}
          </tbody>
        </table>
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
    <tr className="hover:bg-slate-50/60">
      <td className="px-4 py-2">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-slate-900">{row.title}</span>
          {row.kind === "notes_manuscript" && (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-semibold text-slate-600">母本</span>
          )}
        </div>
        <div className="mt-0.5 text-xs text-slate-500">
          {row.source_id}{row.series ? ` · ${row.series}` : ""}
          {row.manuscript_file ? ` · ${row.manuscript_file}` : ""}
        </div>
      </td>
      {stages.map((stage) => <Cell key={stage} stage={stage} cell={row.stages[stage]} />)}
      <td className="px-3 py-2 text-center text-xs">
        {row.articles.length ? (
          <span title={row.articles.join("\n")} className="font-semibold text-indigo-700">
            {row.articles.length} 篇
          </span>
        ) : (
          <span className="text-slate-300">—</span>
        )}
      </td>
    </tr>
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
