"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertCircle, CheckCircle2, ExternalLink, Loader2, RefreshCw } from "lucide-react";

type Group = { id: string; label: string; total: number; processed: number; state: string };
type Source = {
  id: string; title: string; href: string; total: number; processed: number; groups: Group[];
  verification: { passed: boolean; at: string | null; count: number | null };
};
type OwnerItem = {
  id: string; source_title: string; question: string; reason: string; href: string;
  citations: { source_id: string; paragraph_key: string; exact_quote: string }[];
};
type Campaign = {
  id: string; title: string; total: number; processed: number; created: number;
  groups_total: number; groups_processed: number; running_actions: number; last_progress_at: string | null;
  sources: Source[]; owner_items: OwnerItem[];
  agent_items: { source_title: string; reason: string; detail: string; action_id: string | null }[];
  sessions: Record<"gpt" | "opus", { active: number; limit: number; known: boolean }>;
};
type Data = { campaigns: { id: string; title: string }[]; selected: Campaign | null; generated_at: string };

const labels: Record<string, string> = {
  processed: "已處理", running: "處理中", paused: "待接續", partial: "部分處理", pending: "待安排",
  engineering_required: "程序問題，Agent 處理", budget_exhausted: "本組輪次已用完，Agent 核實接續",
  source_drift: "資料版本有變，Agent 核實", human_required: "人工已答覆或待 Agent 核實",
  source_ownership: "來源歸屬待修復", model_failed: "模型服務或輸出問題，Agent 處理",
  call_uncertain: "調用結果待核實", awaiting_agent: "待 Agent 接續", no_progress: "修訂未解決問題，Agent 處理",
};
function when(at: string | null) {
  return at ? new Date(at).toLocaleString("zh-TW", { timeZoneName: "short" }) : "尚無記錄";
}
function tone(state: string) {
  return state === "processed" ? "border-emerald-200 bg-emerald-50 text-emerald-800"
    : state === "running" ? "border-blue-200 bg-blue-50 text-blue-800"
    : state === "paused" || state === "partial" ? "border-amber-200 bg-amber-50 text-amber-900"
    : "border-slate-200 bg-slate-50 text-slate-600";
}

export function ClaimRefreshProgress() {
  const [data, setData] = useState<Data | null>(null);
  const [campaign, setCampaign] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    let inFlight = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      if (inFlight || cancelled || document.hidden) return;
      inFlight = true; setBusy(true);
      try {
        const response = await fetch(`/api/admin/wang/claim-refresh${campaign ? `?campaign=${encodeURIComponent(campaign)}` : ""}`, {
          cache: "no-store", signal: controller.signal,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "無法讀取進度");
        if (!cancelled) { setData(result); setError(""); }
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "無法讀取進度");
      } finally {
        inFlight = false;
        if (!cancelled) { setBusy(false); timer = setTimeout(() => void load(), 15000); }
      }
    };
    const visible = () => {
      if (!document.hidden) { clearTimeout(timer); void load(); }
    };
    void load(); document.addEventListener("visibilitychange", visible);
    return () => { cancelled = true; clearTimeout(timer); controller.abort(); document.removeEventListener("visibilitychange", visible); };
  }, [campaign, refresh]);

  const run = data?.selected;
  const remaining = run ? run.total - run.processed : 0;
  const state = error ? "狀態暫時無法核實" : run?.running_actions ? "正在處理"
    : run && remaining === 0 ? "內容已處理，查看下方資格核驗" : "已停止，待 Agent 接續";
  return <main className="space-y-6 pb-12">
    <header className="rounded-3xl bg-slate-950 p-6 text-white sm:p-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div><p className="text-sm font-semibold text-indigo-300">王教授文庫</p><h1 className="mt-2 text-3xl font-black">Claim 維護進度</h1>
          <p className="mt-3 text-sm leading-6 text-slate-300">每 15 秒自動更新。查看處理進度、停因，以及需要你校對的原文。</p></div>
        <button onClick={() => setRefresh(v => v + 1)} disabled={busy} className="inline-flex items-center gap-2 rounded-xl bg-white/10 px-4 py-2.5 text-sm font-semibold hover:bg-white/20 disabled:opacity-50">
          <RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />立即更新</button>
      </div>
      {data && <div className="mt-4 flex flex-wrap items-center gap-3"><p className="text-xs text-slate-400">最後讀取：{when(data.generated_at)}</p>
        {run && <a href="#owner-actions" className={`rounded-full px-3 py-1 text-xs font-bold ${error ? "bg-slate-700 text-slate-200" : run.owner_items.length ? "bg-amber-300 text-amber-950" : "bg-emerald-200 text-emerald-950"}`}>{error ? "人工待辦待核實" : run.owner_items.length ? `需要你處理 ${run.owner_items.length} 件 ↓` : "目前無需人工處理"}</a>}
      </div>}
    </header>
    {error && <div role="alert" className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-rose-900">
      <p className="font-bold">{error}</p><p className="mt-1 text-sm">{data ? "下方保留上次讀取的資料，目前不能確認程序是否仍在運行。" : "讀取失敗不代表任務已完成。頁面會自動重試。"}</p>
    </div>}
    {!data && !error && <p role="status" className="flex items-center gap-2 py-8 text-slate-600"><Loader2 className="h-5 w-5 animate-spin" />正在核實任務進度…</p>}
    {data && data.campaigns.length === 0 && <p className="rounded-2xl border border-dashed border-slate-300 bg-white p-8 text-slate-600">尚未登記 Claim 維護批次。</p>}
    {run && <>
      <section className="rounded-2xl border border-slate-200 bg-white p-5 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <label className="flex min-w-0 flex-col gap-1 text-sm font-semibold text-slate-600">批次
            <select aria-label="選擇維護批次" value={campaign || run.id} onChange={e => { setCampaign(e.target.value); setData(null); }} className="max-w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-base font-bold text-slate-950">
              {data.campaigns.map(c => <option key={c.id} value={c.id}>{c.title}</option>)}
            </select>
          </label>
          <span role="status" className={`rounded-full px-3 py-1 text-sm font-bold ${!error && run.running_actions ? "bg-blue-50 text-blue-800" : "bg-amber-50 text-amber-900"}`}>{state}</span>
        </div>
        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[["原有 Claim", run.total], ["已處理原有", run.processed], ["尚待處理", remaining], ["本批新增", run.created]].map(([label, n]) => <div key={label} className="rounded-xl bg-slate-50 p-4"><p className="text-3xl font-black tabular-nums text-slate-950">{n}</p><p className="mt-1 text-sm text-slate-500">{label}</p></div>)}
        </div>
        <progress aria-label="原有 Claim 處理進度" value={run.processed} max={run.total} className="mt-5 h-3 w-full accent-indigo-600" />
        <div className="mt-2 flex flex-wrap justify-between gap-2 text-sm text-slate-600"><span>{run.groups_processed}／{run.groups_total} 組已處理；{error ? "運行狀態待核實" : `${run.running_actions} 組處理中`}</span><span>最後任務進展：{when(run.last_progress_at)}</span></div>
        <p className="mt-3 text-xs leading-6 text-slate-500">已處理包含正常更新及復用既有有效審閱；每篇最後的資格核驗另列。新增 Claim 不改動原清單分母。</p>
        <div className="mt-4 flex flex-wrap gap-3 border-t border-slate-100 pt-4 text-sm text-slate-600">{(["gpt", "opus"] as const).map(role => <span key={role} className="rounded-lg bg-slate-50 px-3 py-1.5">{role === "gpt" ? "GPT" : "Opus"}：{error || !run.sessions[role].known ? "待核實" : `${run.sessions[role].active}／${run.sessions[role].limit} 個實際調用`}<span className="ml-1 text-xs text-slate-400">（共用名額）</span></span>)}</div>
      </section>

      <section id="owner-actions" className={`scroll-mt-4 rounded-2xl border p-5 sm:p-6 ${error ? "border-slate-200 bg-white" : run.owner_items.length ? "border-amber-200 bg-amber-50" : "border-emerald-200 bg-emerald-50"}`}>
        <h2 className="flex items-center gap-2 text-lg font-black text-slate-950">{error || run.owner_items.length ? <AlertCircle className="h-5 w-5 text-amber-700" /> : <CheckCircle2 className="h-5 w-5 text-emerald-700" />}需要你處理的事{run.owner_items.length > 0 && `（${run.owner_items.length}）`}</h2>
        {!run.owner_items.length ? <p className="mt-2 text-sm text-slate-700">{error ? "上次讀取時無人工待辦；目前資料尚未更新。" : "目前無需人工處理。程序問題及普通修訂由 Agent 接續。"}</p> : <div className="mt-4 space-y-4">{run.owner_items.map(item => <article key={item.id} className="rounded-xl border border-amber-200 bg-white p-4">
          <p className="text-sm font-semibold text-slate-500">{item.source_title}</p><h3 className="mt-2 whitespace-pre-wrap font-bold leading-7 text-slate-950">{item.question}</h3>
          {item.citations.map((c, i) => <blockquote key={i} className="mt-3 whitespace-pre-wrap border-l-4 border-indigo-200 bg-indigo-50/50 px-4 py-3 leading-7 text-slate-800">{c.exact_quote}<footer className="mt-1 text-xs text-slate-500">原文段落 {c.paragraph_key}</footer></blockquote>)}
          <details className="mt-3 text-sm text-slate-600"><summary className="cursor-pointer font-semibold">查看審閱依據</summary><p className="mt-2 whitespace-pre-wrap leading-7">{item.reason}</p></details>
          <Link href={item.href} className="mt-4 inline-flex items-center gap-1 text-sm font-bold text-indigo-700">對照完整原文<ExternalLink className="h-4 w-4" /></Link>
        </article>)}</div>}
      </section>

      <section className="space-y-3"><h2 className="text-lg font-black text-slate-950">各篇進度</h2>{run.sources.map(source => <article key={source.id} className="rounded-2xl border border-slate-200 bg-white p-5">
        <div className="flex flex-wrap justify-between gap-3"><Link href={source.href} className="font-bold leading-7 text-slate-950 hover:text-indigo-700">{source.title}</Link><span className="font-bold tabular-nums text-slate-700">{source.processed}／{source.total} 條已處理</span></div>
        <div className="mt-3 flex flex-wrap gap-2">{source.groups.map(g => <span key={g.id} title={`${g.label}：${labels[g.state] || g.state}`} className={`rounded-lg border px-2.5 py-1.5 text-xs font-semibold ${tone(error && g.state === "running" ? "pending" : g.state)}`}>第 {g.id} 組 · {g.processed}/{g.total} · {error && g.state === "running" ? "待核實" : labels[g.state]}</span>)}</div>
        <p className="mt-3 text-sm text-slate-600">{source.verification.passed ? `最近資格核驗：${source.verification.count} 條通過（${when(source.verification.at)}）` : "資格：待整篇最終核驗"}</p>
        {source.verification.passed && <p className="mt-1 text-xs text-slate-400">這是所列時間的核驗結果；後續資料變動須重新核實相關項。</p>}
      </article>)}</section>

      {run.agent_items.length > 0 && <section className="rounded-2xl border border-slate-200 bg-white p-5"><h2 className="text-lg font-black text-slate-950">Agent 待接續</h2><p className="mt-1 text-sm text-slate-500">以下不是人工校對待辦。</p><div className="mt-4 divide-y divide-slate-100">{run.agent_items.map((item, i) => <div key={`${item.action_id}-${i}`} className="py-3"><p className="font-semibold text-slate-800">{item.source_title}</p><p className="mt-1 text-sm text-amber-800">{labels[item.reason] || "待 Agent 核實處置"}</p>{item.detail && <details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">具體停因</summary><p className="mt-2 whitespace-pre-wrap break-words leading-6">{item.detail}</p></details>}</div>)}</div></section>}
    </>}
  </main>;
}
