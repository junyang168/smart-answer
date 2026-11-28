"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { seriesName } from "@/app/components/series/progress";

type Task = { id: string; stage: string; state: string; live: boolean; attempt: number; heartbeat: string | null; error: string | null; model_calls?: Record<string, number>; progress: { completed?: number; total?: number; unit?: string } };
type Membership = { id: string; name: string; order: number; series_order: number };
type Sermon = { item: string; title: string; state: string; groups: Membership[]; source_count: number; manual: boolean; editor: boolean; published: boolean; batch: string | null; editor_href: string | null; tasks: Task[] };
type Snapshot = { initialized: boolean; records: Sermon[]; series: (Membership & { total: number; pending: number })[]; counts: Record<string, number>; total: number; pending?: number; live_count?: number; filtered_total: number; snapshot_revision?: string; generated_at: string; synced_at?: string };
const states: Record<string, string> = { ready_to_plan: "待核來源與排程", waiting_source: "缺來源／多版本待核", existing_transcript: "已有人工稿・待查補件", rough_complete: "僅有舊粗稿", pending: "排隊中", running: "處理中", paused: "已暫停", failed: "處理有問題", unknown_outcome: "呼叫結果待核", unverified: "工作失聯待核", interrupted: "中斷待接續", complete: "技術交付完成", needs_resolution: "目錄／交付版本待核", needs_sync: "交付索引待同步" };
const stages: Record<string, string> = { audio: "抽取音頻", transcribe: "Gemini 粗轉錄", flash: "原聲精修", timing: "分段與時間", speakers: "講員辨識", repetitions: "重複軟刪除", layout: "整理稿件", media: "播放媒體／壓縮", editor: "Editor 交接", slides: "投影片核對與綁定", delivery: "交付核驗" };
const bad = new Set(["failed", "unknown_outcome", "unverified", "needs_resolution", "interrupted"]);
const when = (value?: string | null) => value ? new Date(value).toLocaleString("zh-TW") : "尚無回報";

function Badge({ state }: { state: string }) {
  return <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ${bad.has(state) ? "bg-rose-50 text-rose-800" : state === "running" ? "bg-sky-100 text-sky-900" : state === "complete" ? "bg-emerald-50 text-emerald-800" : "bg-stone-100 text-stone-700"}`}>{states[state] || state}</span>;
}

export function SermonPipelineMonitor() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [series, setSeries] = useState("");
  const [state, setState] = useState("actionable");
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState<string>();
  const [refresh, setRefresh] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [budget, setBudget] = useState(100);
  const [calls, setCalls] = useState(600);
  const [plan, setPlan] = useState<{ batch: string; items: string[]; budget_usd: number; max_calls: number } | null>(null);
  useEffect(() => { const timer = setTimeout(() => { setSearch(query); setOffset(0); setRevision(undefined); }, 250); return () => clearTimeout(timer); }, [query]);
  useEffect(() => {
    let stopped = false, pending = false;
    let timer: ReturnType<typeof setTimeout>;
    const abort = new AbortController();
    const load = async () => {
      if (stopped || document.hidden || pending) return;
      pending = true;
      try {
        const q = new URLSearchParams({ search, series, state, offset: String(offset), limit: "30" });
        if (revision) q.set("snapshot_revision", revision);
        const response = await fetch(`/api/sc_api/media_batches/sermons?${q}`, { cache: "no-store", signal: abort.signal });
        const value = await response.json();
        if (response.status === 409 && value.detail === "snapshot_changed") { if (!stopped) setRevision(undefined); return; }
        if (!response.ok) throw Error(value.detail || "無法讀取管線狀態");
        if (!stopped) { setData(value); setError(""); }
      } catch (e) { if (!stopped) setError(e instanceof Error ? e.message : "尚未確認最新狀態"); }
      finally { pending = false; if (!stopped) timer = setTimeout(() => void load(), 5000); }
    };
    const visible = () => { if (!document.hidden) { clearTimeout(timer); void load(); } };
    void load(); document.addEventListener("visibilitychange", visible);
    return () => { stopped = true; abort.abort(); clearTimeout(timer); document.removeEventListener("visibilitychange", visible); };
  }, [search, series, state, offset, revision, refresh]);
  async function action(path: string, body?: unknown) {
    setBusy(path); setError("");
    try {
      const response = await fetch(`/api/sc_api/media_batches/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
      const value = await response.json();
      if (!response.ok) throw Error(value.detail || "操作未完成");
      setRefresh(n => n + 1); return value;
    } catch (e) { setError(e instanceof Error ? e.message : "操作未完成"); return null; }
    finally { setBusy(""); }
  }
  const filter = (next: string) => { setState(next); setOffset(0); setRevision(undefined); };
  const groups = data?.series.filter(g => data.records.some(r => r.groups.some(m => m.id === g.id))) || [];
  return <section aria-labelledby="pipeline-title" className="mx-auto max-w-7xl space-y-6 px-5 py-8 text-stone-900">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div><p className="text-xs font-semibold uppercase tracking-widest text-sky-800">講道來源工作台</p><h1 id="pipeline-title" className="mt-2 text-3xl font-semibold tracking-tight">影音處理進度</h1><p className="mt-2 text-sm text-stone-600">按熟悉的系列查看全部講道，從來源準備到圖文稿交付。</p></div>
      <div className="flex gap-2"><Link href="/resources/series" className="rounded-lg border bg-white px-3 py-2 text-sm">系列校對頁</Link><Link href="/admin/media-batches?view=batches" className="rounded-lg border bg-white px-3 py-2 text-sm">按批次查看</Link><button disabled={!!busy} onClick={() => void action("sermons/sync")} className="rounded-lg bg-stone-900 px-3 py-2 text-sm text-white disabled:opacity-50">{busy === "sermons/sync" ? "同步中…" : "同步講道目錄"}</button></div>
    </header>
    {error && <p role="alert" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">{error}。保留上次資料；尚未確認最新狀態。</p>}
    {data && !data.initialized && <p className="rounded-xl border border-dashed p-8 text-center text-stone-600">尚未建立本環境的來源索引。設定管線後，按「同步講道目錄」；同步不啟動轉錄。</p>}
    <section aria-label="管線總覽" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {[{ label: "全部講道", n: data?.total, s: "all" }, { label: "待完成交付", n: data?.pending, s: "actionable" }, { label: "實際處理中", n: data?.live_count, s: "running" }, { label: "已完成技術交付", n: data?.counts.complete || 0, s: "complete" }].map(v => <button key={v.s} onClick={() => filter(v.s)} className={`rounded-xl border bg-white p-5 text-left shadow-sm ${state === v.s ? "border-sky-700 ring-1 ring-sky-700" : "border-stone-200"}`}><span className="text-sm text-stone-600">{v.label}</span><strong className="mt-1 block text-3xl font-semibold tabular-nums">{v.n ?? "—"}</strong></button>)}
    </section>
    <section aria-label="搜尋與篩選" className="flex flex-wrap gap-3 rounded-xl border bg-white p-4">
      <input aria-label="搜尋全部講道與系列" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜尋全部講道、系列或講道 ID" className="min-w-64 flex-1 rounded-lg border px-3 py-2 text-sm" />
      <select aria-label="系列" value={series} onChange={e => { setSeries(e.target.value); setOffset(0); setRevision(undefined); }} className="max-w-full rounded-lg border bg-white px-3 py-2 text-sm"><option value="">全部系列</option>{data?.series.map(g => <option key={g.id} value={g.id}>{g.name}（{g.pending}/{g.total} 待完成）</option>)}</select>
      <select aria-label="處理狀態" value={state} onChange={e => filter(e.target.value)} className="rounded-lg border bg-white px-3 py-2 text-sm"><option value="actionable">全部待完成</option><option value="all">全部狀態</option>{Object.entries(states).map(([k,v]) => <option key={k} value={k}>{v}（{data?.counts[k] || 0}）</option>)}</select>
    </section>
    {selected.size > 0 && <section className="flex flex-wrap items-center gap-3 rounded-xl border border-sky-200 bg-sky-50 p-4 text-sm"><strong>已選 {selected.size} 講</strong><label>預留上限 USD <input aria-label="費用上限" type="number" min="1" max="1000" value={budget} onChange={e => setBudget(Number(e.target.value))} className="w-20 rounded border px-2 py-1" /></label><label>最多呼叫 <input aria-label="呼叫上限" type="number" min="1" max="10000" value={calls} onChange={e => setCalls(Number(e.target.value))} className="w-24 rounded border px-2 py-1" /></label><button disabled={!!busy || selected.size > 20} onClick={async () => { const value = await action("sermons/plan", { items: [...selected], budget_usd: budget, max_calls: calls, operation_id: crypto.randomUUID() }); if (value) setPlan(value); }} className="rounded-lg bg-sky-900 px-4 py-2 text-white disabled:opacity-50">{busy.includes("plan") ? "核對來源與計畫中…" : "預覽完整處理計畫"}</button><button onClick={() => { setSelected(new Set()); setPlan(null); }} className="underline">清除</button></section>}
    {plan && <section className="rounded-xl border border-sky-300 bg-white p-5"><h2 className="font-semibold">計畫已準備，尚未啟動</h2><p className="my-2 text-sm">{plan.items.length} 講 · 預留上限 ${plan.budget_usd} · 最多 {plan.max_calls} 次呼叫。原片保留；生成稿交接 Editor 並接續幻燈。</p><button disabled={!!busy} onClick={async () => { const result = await action(`sermons/start/${plan.batch}`); if (result) { setPlan(null); setSelected(new Set()); } }} className="rounded-lg bg-sky-900 px-4 py-2 text-sm text-white">開始此計畫</button></section>}
    <p className="text-xs text-stone-500">來源索引：{when(data?.synced_at)} · 狀態：{when(data?.generated_at)} · 處理中與問題支線可能重疊；技術交付不代表人工校對或發布。</p>
    {!data && !error && <p role="status" className="py-12 text-center text-stone-500">讀取全部講道與工作狀態…</p>}
    {groups.map(group => <section key={group.id} className="overflow-hidden rounded-xl border border-stone-200 bg-white shadow-sm">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b bg-stone-50 px-5 py-4"><h2 className="font-semibold">{seriesName({id:group.name}).name}</h2><button onClick={() => { setSeries(group.id); setOffset(0); setRevision(undefined); }} className="text-xs text-stone-600 hover:underline">全系列 {group.total} 講 · {group.pending} 待完成</button></header>
      <div className="divide-y divide-stone-100">{data?.records.filter(r => r.groups.some(g => g.id === group.id)).map(r => <details key={r.item} className="group px-5 py-4">
        <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-3"><div className="flex min-w-0 items-start gap-3"><input aria-label={`選擇 ${r.title}`} type="checkbox" disabled={r.manual || r.source_count !== 1 || !!r.batch} checked={selected.has(r.item)} onClick={e => e.stopPropagation()} onChange={e => setSelected(old => { const next = new Set(old); if(e.target.checked) next.add(r.item); else next.delete(r.item); setPlan(null); return next; })} className="mt-1" /><div><span className="font-medium">{r.title}</span><p className="mt-1 max-w-3xl break-words text-xs text-stone-500">{r.item}</p><p className="mt-1 text-xs text-stone-500">{r.manual ? "已有稿件受保護，可展開查看" : r.source_count === 1 ? "已定位一份來源，開始前核實 SHA" : `來源 ${r.source_count} 份，需先核實`}</p></div></div><div className="flex items-center gap-3"><Badge state={r.state} /><span className="text-xs text-stone-400 group-open:hidden">展開 ↓</span></div></summary>
        <div className="mt-4 space-y-3 pl-6">{r.tasks.length ? <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{[...r.tasks].sort((a,b) => Object.keys(stages).indexOf(a.stage)-Object.keys(stages).indexOf(b.stage)).map(t => <li key={t.id} className={`rounded-lg border p-3 ${t.live ? "border-sky-300 bg-sky-50" : "border-stone-100 bg-stone-50"}`}><div className="flex justify-between gap-2 text-xs"><strong>{stages[t.stage] || t.stage}</strong><span>{t.live ? "處理中" : t.state === "complete" ? "已完成" : states[t.state] || t.state}</span></div>{typeof t.progress?.completed === "number" && !!t.progress.total && <><progress aria-label={stages[t.stage] || t.stage} value={t.progress.completed} max={t.progress.total} className="mt-2 h-2 w-full accent-sky-700" /><p className="mt-1 text-xs tabular-nums">{Math.floor(t.progress.completed)} / {Math.ceil(t.progress.total)} {t.progress.unit === "root_windows" ? "根窗口（含子分支）" : t.progress.unit === "seconds" ? "秒" : t.progress.unit}</p></>}{t.model_calls && Object.keys(t.model_calls).length > 0 && <p className="mt-2 text-xs tabular-nums">模型呼叫：{t.model_calls.complete || 0} 已返回{t.model_calls.unknown ? ` · ${t.model_calls.unknown} 執行中／結果待核` : ""}（含自適應子窗口）</p>}{t.error && <p className="mt-2 break-words text-xs text-rose-800">{t.error}</p>}<p className="mt-2 text-xs text-stone-400">嘗試 {t.attempt} · {when(t.heartbeat)}</p></li>)}</ol> : <p className="text-sm text-stone-600">尚無目前計畫的任務。講道保留在清單，不因尚未排程而略過。</p>}
        <div className="flex flex-wrap gap-4 text-sm">{r.editor_href && <Link className="text-sky-900 underline" href={r.editor_href}>開啟 Editor</Link>}{r.batch && <Link className="text-sky-900 underline" href={`/admin/media-batches/${r.batch}`}>批次詳情與暫停／恢復</Link>}<span className="text-stone-500">人工狀態：{r.published ? "已發布" : r.manual ? "已有編輯稿" : "尚無人工稿"}</span></div></div>
      </details>)}</div>
    </section>)}
    {data?.initialized && !data.records.length && <p className="rounded-xl border border-dashed py-10 text-center text-stone-500">此篩選條件沒有講道。</p>}
    {data?.initialized && <footer className="flex flex-wrap items-center justify-between gap-3 text-sm"><span>符合 {data.filtered_total} 講 · 本頁 {data.records.length} 講（系列重複歸屬只計一次）</span><div className="flex gap-3"><button disabled={offset === 0} onClick={() => { setOffset(Math.max(0,offset-30)); setRevision(data.snapshot_revision); }} className="whitespace-nowrap rounded-lg border px-4 py-2 disabled:opacity-30">上一頁</button><button disabled={offset+30 >= data.filtered_total} onClick={() => { setOffset(offset+30); setRevision(data.snapshot_revision); }} className="whitespace-nowrap rounded-lg border px-4 py-2 disabled:opacity-30">下一頁</button></div></footer>}
  </section>;
}
