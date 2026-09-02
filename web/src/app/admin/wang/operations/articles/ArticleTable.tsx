"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import type { ArticleRow, ArticlesPayload } from "./types";

const stageLabels: Record<string, string> = {
  composition_ready: "編排就緒",
  knowledge_ready: "知識就緒",
  authoring: "寫作",
  independent_editorial_review: "編審",
  revision: "修訂",
  final_delta_review: "差異審核",
  program_audit: "程式審計",
  publication_decision: "出版決定",
  repository_published: "寫入文庫",
  production_visible: "線上可見",
};

function money(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : `$${value.toFixed(2)}`;
}

function Pill({ tone, children, title }: { tone: "ok" | "warn" | "bad" | "idle"; children: React.ReactNode; title?: string }) {
  const tones = {
    ok: "bg-emerald-50 text-emerald-800 ring-emerald-200",
    warn: "bg-amber-50 text-amber-900 ring-amber-200",
    bad: "bg-rose-50 text-rose-800 ring-rose-200",
    idle: "bg-slate-50 text-slate-400 ring-slate-200",
  } as const;
  return (
    <span title={title} className={`inline-block rounded-md px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${tones[tone]}`}>
      {children}
    </span>
  );
}

export function ArticleTable() {
  const [data, setData] = useState<ArticlesPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const response = await fetch("/api/admin/wang/operations/articles", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "無法讀取文章總表");
      setData(payload);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    fetch("/api/admin/wang/operations/articles", { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "無法讀取文章總表");
        if (active) {
          setData(payload);
          setError(null);
        }
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const rows = data?.rows ?? [];

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
        <p className="font-bold">無法讀取文章總表</p>
        <p className="mt-1 text-sm">{error}</p>
      </div>
    );
  }
  if (!data) return null;
  const { summary } = data;

  return (
    <section className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="文章稿件" value={String(summary.articles)} detail={`已發布 ${summary.published}`} />
        <Metric label="已記錄的文章執行" value={String(summary.article_runs_recorded)}
          detail={summary.article_runs_recorded ? "面板或 CLI 觸發" : "記錄表上線後還沒跑過"} />
        <Metric label="文章花費" value={money(summary.spend_usd)} detail="只算記錄表裡的執行" />
      </div>

      {data.warnings.length > 0 && (
        <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4">
          <p className="flex items-center gap-2 font-bold text-amber-900">
            <AlertTriangle className="h-4 w-4" />這張表知道自己哪裡不完整
          </p>
          <ul className="mt-2 space-y-1 text-sm leading-6 text-amber-900">
            {data.warnings.map((warning, index) => <li key={`${warning.code}-${index}`}>· {warning.message}</li>)}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3">
        <span className="text-sm text-slate-500">只列已有稿件；歷史 CompositionPlan 不再充當待寫隊列。</span>
        <span className="ml-auto text-sm text-slate-500">{rows.length}</span>
        <button onClick={load} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-semibold text-slate-700 hover:bg-slate-50">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />重新讀取
        </button>
      </div>

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs font-bold text-slate-600">
            <tr>
              <th className="px-4 py-2.5">文章</th>
              <th className="px-3 py-2.5">經文</th>
              <th className="px-3 py-2.5">目前階段</th>
              <th className="px-3 py-2.5">編審</th>
              <th className="px-3 py-2.5">審計</th>
              <th className="px-3 py-2.5">出版</th>
              <th className="px-3 py-2.5 text-right">花費</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((row) => (
              <Row key={row.draft_id} row={row}
                open={open === row.draft_id}
                onToggle={() => setOpen(open === row.draft_id ? null : row.draft_id)} />
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-xs leading-6 text-slate-500">
        階段、編審、審計與出版決定讀自 <code>matthew-progress</code> 的 read model，不在這裡重算；執行與花費讀自執行記錄表。
        編審顯示的是<strong>過線的維度數</strong>，不是總分——一個總分會讓弱的維度被強的扛過去，這正是 quality profile 廢掉總分門檻的原因。
      </p>
    </section>
  );
}

function Row({ row, open, onToggle }: { row: ArticleRow; open: boolean; onToggle: () => void }) {
  const audit = row.program_audit;
  const editorial = row.editorial;
  return (
    <>
      <tr className="cursor-pointer hover:bg-slate-50/60" onClick={onToggle}>
        <td className="px-4 py-2">
          <div className="flex items-center gap-1.5">
            {open ? <ChevronDown className="h-3.5 w-3.5 text-slate-400" /> : <ChevronRight className="h-3.5 w-3.5 text-slate-400" />}
            <span className="font-semibold text-slate-900">{row.title}</span>
          </div>
          <div className="mt-0.5 pl-5 font-mono text-[10.5px] text-slate-400">
            {row.draft_id}
          </div>
        </td>
        <td className="px-3 py-2 text-xs text-slate-600">{row.passage ?? "—"}</td>
        <td className="px-3 py-2 text-xs">
          {row.current_stage ? stageLabels[row.current_stage] ?? row.current_stage
            : <span className="text-slate-400">未追蹤</span>}
        </td>
        <td className="px-3 py-2">
          {editorial ? (
            <Pill tone={editorial.below_minimum.length || editorial.hard_gate_failures.length ? "bad" : "ok"}
              title={editorial.below_minimum.length ? `未達最低分：${editorial.below_minimum.join("、")}` : `total_score ${editorial.total_score}（僅供參考，不決定任何事）`}>
              {editorial.passed_dimensions}/{editorial.dimensions} 維度過線
            </Pill>
          ) : <span className="text-xs text-slate-300">—</span>}
        </td>
        <td className="px-3 py-2">
          {audit ? (
            <Pill tone={audit.error_count ? "bad" : audit.warning_count ? "warn" : "ok"}>
              {audit.error_count ?? 0} 錯 {audit.warning_count ?? 0} 警
            </Pill>
          ) : <span className="text-xs text-slate-300">—</span>}
        </td>
        <td className="px-3 py-2">
          {row.publication_decision ? (
            <Pill tone={row.publication_decision.valid ? "ok" : "bad"}>
              {row.publication_decision.kind === "human" ? "人工" : row.publication_decision.kind === "automated" ? "自動" : "不明"}
            </Pill>
          ) : <span className="text-xs text-slate-300">—</span>}
        </td>
        <td className="px-3 py-2 text-right text-xs font-mono">{money(row.cost_usd)}</td>
      </tr>
      {open && (
        <tr className="bg-slate-50/60">
          <td colSpan={7} className="px-4 py-3">
            <Detail row={row} />
          </td>
        </tr>
      )}
    </>
  );
}

function Detail({ row }: { row: ArticleRow }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div>
        <p className="text-xs font-bold text-slate-600">階段</p>
        <ol className="mt-1 space-y-0.5 text-xs">
          {row.stages.length ? row.stages.map((stage) => (
            <li key={stage.stage} className="flex items-center gap-2">
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${
                stage.state === "complete" ? "bg-emerald-500"
                : stage.state === "active" ? "bg-indigo-500"
                : stage.state === "blocked" ? "bg-rose-500" : "bg-slate-300"}`} />
              <span className={stage.state === "not_started" ? "text-slate-400" : "text-slate-700"}>
                {stageLabels[stage.stage] ?? stage.stage}
              </span>
            </li>
          )) : <li className="text-slate-400">這篇稿件沒有可顯示的階段資料。</li>}
        </ol>
        {row.blockers.length > 0 && (
          <>
            <p className="mt-3 text-xs font-bold text-rose-700">阻塞</p>
            <ul className="mt-1 space-y-0.5 text-xs text-rose-800">
              {row.blockers.map((blocker) => <li key={blocker.code}>· {blocker.message ?? blocker.code}</li>)}
            </ul>
          </>
        )}
        {row.next_step && <p className="mt-2 text-xs text-slate-600">下一步：{row.next_step}</p>}
      </div>
      <div>
        <p className="text-xs font-bold text-slate-600">
          引用的來源 {row.cited_sources.length ? `（${row.cited_sources.length}）` : ""}
        </p>
        {row.cited_sources.length ? (
          <ul className="mt-1 space-y-0.5 text-xs">
            {row.cited_sources.map((source) => (
              <li key={source} className="truncate">
                <a href={`/admin/wang#${encodeURIComponent(source)}`} className="text-indigo-700 hover:underline">
                  {source}
                </a>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-xs text-slate-400">
            沒有可對應的引用來源。（母本的引用目前還對不到列，見頁首警告。）
          </p>
        )}
        {row.runs.length > 0 && (
          <>
            <p className="mt-3 text-xs font-bold text-slate-600">執行記錄</p>
            <ul className="mt-1 space-y-0.5 font-mono text-[11px] text-slate-600">
              {row.runs.map((run) => (
                <li key={run.run_id}>
                  {run.started_at ? new Date(run.started_at).toLocaleString("zh-TW") : "—"} · {run.status} · {money(run.cost_usd)}
                </li>
              ))}
            </ul>
          </>
        )}
        {row.links?.public && (
          <a href={row.links.public} target="_blank" rel="noopener noreferrer"
            className="mt-3 inline-flex items-center gap-1 text-xs font-semibold text-indigo-700 hover:underline">
            看公開頁 <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </div>
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
