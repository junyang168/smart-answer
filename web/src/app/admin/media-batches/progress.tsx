"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { MediaBatchFiles } from "./files";
import {
  RefreshCw,
  Pause,
  Play,
  RotateCcw,
  ChevronRight,
  AlertCircle,
} from "lucide-react";

type Task = {
  id: string;
  stage: string;
  unit: string;
  state: string;
  live: boolean;
  attempt: number;
  heartbeat: string | null;
  updated_at: string;
  error: string | null;
  progress: {
    stage?: string;
    completed?: number;
    total?: number;
    unit?: string;
    at?: number;
    attempt_started_at?: number;
  };
  result?: { editor_href?: string };
};
type Recording = {
  id: string;
  item: string;
  name: string;
  duration: string;
  state: string;
  ready: boolean;
  tasks: Task[];
};
type Batch = {
  id: string;
  title: string;
  revision: number;
  desired: string;
  mode?: string;
  calls: number;
  reserved_usd: string;
  estimated_usd?: string | null;
  costed_calls?: number;
  budget_limit_usd?: string;
  budget_exposure_usd?: string;
};
type Snapshot = {
  batches: Batch[];
  selected: Batch | null;
  records: Recording[];
  summary: Record<string, number>;
  generated_at: string;
  offset: number;
  limit: number;
  capacity?: Record<string, { active: number; unknown: number; limit: number }>;
  disk?: Record<
    string,
    { available_bytes?: number; reserved_bytes?: number; unavailable?: boolean }
  >;
};
const names: Record<string, string> = {
  prepare: "準備音訊",
  visuals: "投影片與 OCR",
  speech: "轉錄與校字",
  "final-align": "時間對齊",
  finish: "最終檢查",
  speakers: "講員辨識",
  media: "網站影音",
  handoff: "交接校對",
  existing: "沿用已有逐字稿",
  reused: "保留舊稿",
  pending: "排隊",
  running: "處理中",
  complete: "已完成",
  failed: "失敗",
  unknown_outcome: "呼叫結果待核實",
  needs_resolution: "來源待確認",
  unverified: "工作程序失聯，待核實",
  interrupted: "工作中斷，待接續",
  asr: "音訊辨識",
};
const when = (at: string | null) =>
  at ? new Date(at).toLocaleString("zh-TW") : "尚無回報";
function Pill({ state }: { state: string }) {
  return (
    <span
      className={`rounded-full px-3 py-1 text-xs font-semibold ${state === "complete" ? "bg-emerald-50 text-emerald-800" : state === "running" ? "bg-indigo-50 text-indigo-800" : "bg-amber-50 text-amber-900"}`}
    >
      {names[state] || state}
    </span>
  );
}
function Work({ task }: { task: Task }) {
  const p = task.progress;
  const measurable =
    typeof p.completed === "number" &&
    typeof p.total === "number" &&
    p.total > 0;
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap justify-between gap-2">
        <span className="font-semibold">
          {names[p.stage || task.stage] || names[task.stage] || task.stage}
        </span>
        <Pill
          state={
            task.state === "running" && !task.live ? "unverified" : task.state
          }
        />
      </div>
      {measurable && (
        <>
          <progress
            className="mt-2 h-2 w-full accent-indigo-600"
            value={p.completed}
            max={p.total}
          />
          <p className="text-xs text-slate-500">
            {Math.floor(p.completed!)} / {Math.ceil(p.total!)}{" "}
            {p.unit === "windows"
              ? "窗口"
              : p.unit === "chunks"
                ? "音訊片段"
                : p.unit === "seconds"
                  ? "秒"
                  : p.unit}
          </p>
        </>
      )}
      {task.error && (
        <p className="mt-2 break-words text-sm text-amber-900">{task.error}</p>
      )}
      <p className="mt-1 text-xs text-slate-500">
        第 {task.attempt} 次 · 最後回報 {when(task.heartbeat)}
        {p.at && <> · 最近進展 {when(new Date(p.at * 1000).toISOString())}</>}
      </p>
    </div>
  );
}
export function MediaBatchProgress({ batch }: { batch?: string }) {
  const [data, setData] = useState<Snapshot | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [refresh, setRefresh] = useState(0),
    [offset, setOffset] = useState(0),
    [search, setSearch] = useState(""),
    [filter, setFilter] = useState("all"),
    [controlError, setControlError] = useState("");
  useEffect(() => {
    setData(null);
  }, [batch, offset]);
  useEffect(() => {
    let stopped = false,
      timer: ReturnType<typeof setTimeout> | undefined,
      inFlight = false,
      pollDelay = 5000;
    const abort = new AbortController();
    const load = async () => {
      if (stopped || inFlight || document.hidden) return;
      inFlight = true;
      try {
        const q = new URLSearchParams({ offset: String(offset), limit: "30" });
        if (batch) q.set("batch", batch);
        const res = await fetch(`/api/sc_api/media_batches?${q}`, {
          cache: "no-store",
          signal: abort.signal,
        });
        const value = await res.json();
        if (!res.ok) throw Error(value.detail || "讀取失敗");
        pollDelay =
          value.summary?.running || value.summary?.pending ? 5000 : 30000;
        if (!stopped) {
          setData(value);
          setError("");
        }
      } catch (e) {
        pollDelay = Math.min(60000, pollDelay * 2);
        if (!stopped) setError(e instanceof Error ? e.message : "無法讀取進度");
      } finally {
        inFlight = false;
        if (!stopped) timer = setTimeout(() => void load(), pollDelay);
      }
    };
    const visible = () => {
      if (!document.hidden) {
        clearTimeout(timer);
        void load();
      }
    };
    void load();
    document.addEventListener("visibilitychange", visible);
    return () => {
      stopped = true;
      abort.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [batch, offset, refresh]);
  const control = useCallback(
    async (action: string) => {
      if (!data?.selected || busy) return;
      setBusy(true);
      setControlError("");
      try {
        const res = await fetch(
          `/api/sc_api/media_batches/${data.selected.id}/control`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              action,
              revision: data.selected.revision,
              operation_id: crypto.randomUUID(),
            }),
          },
        );
        const result = await res.json();
        if (!res.ok) throw Error(result.detail || "指令未確認");
        setRefresh((x) => x + 1);
      } catch (e) {
        setControlError(e instanceof Error ? e.message : "指令未確認");
      } finally {
        setBusy(false);
      }
    },
    [data, busy],
  );
  const run = data?.selected;
  const summary = data?.summary;
  const completed =
    !!summary && summary.complete + (summary.reused || 0) === summary.total;
  const rows =
    data?.records.filter(
      (r) =>
        (filter === "all" || r.state === filter) &&
        `${r.name} ${r.item}`.toLowerCase().includes(search.toLowerCase()),
    ) || [];
  return (
    <main className="mx-auto max-w-7xl space-y-5 pb-12 text-slate-800">
      <header className="flex flex-wrap items-start justify-between gap-4 rounded-2xl bg-slate-950 p-6 text-white">
        <div>
          <Link href="/admin/media-batches" className="text-sm text-indigo-200">
            批次影音轉錄
          </Link>
          <h1 className="mt-2 text-2xl font-bold">
            {run?.mode === "inventory"
              ? "影音作業總覽 · Operation Dashboard"
              : run?.title || "影音作業總覽"}
          </h1>
          <p className="mt-2 text-sm text-slate-300">
            {run?.mode === "inventory"
              ? `${run.title} · 全部檔案的處理狀態與等待原因`
              : "自動更新 · 關閉頁面後任務繼續執行"}
          </p>
        </div>
        <button
          onClick={() => setRefresh((x) => x + 1)}
          className="flex items-center gap-2 rounded-lg bg-white/10 px-4 py-2"
        >
          <RefreshCw size={16} />
          立即更新
        </button>
      </header>
      {error && (
        <div role="alert" className="rounded-xl bg-amber-50 p-4 text-amber-900">
          <AlertCircle className="mr-2 inline" size={18} />
          {error}
          <p className="mt-1 text-sm">
            保留上次資料；目前無法確認工作是否仍在執行。
          </p>
        </div>
      )}
      {!data && !error && <p role="status">讀取任務台帳…</p>}
      {data && !data.batches.length && (
        <div className="rounded-xl border border-dashed p-8">
          尚無批次。請先在影音所在的電腦建立資料夾處理計畫。
        </div>
      )}
      {data && run?.mode !== "inventory" && (
        <nav className="flex flex-wrap gap-2" aria-label="批次選擇">
          {data.batches.map((b) => (
            <Link
              key={b.id}
              href={`/admin/media-batches/${b.id}`}
              className={`rounded-lg border px-3 py-2 text-sm ${run?.id === b.id ? "border-indigo-400 bg-indigo-50" : "bg-white"}`}
            >
              {b.title}
            </Link>
          ))}
        </nav>
      )}
      {run?.mode === "inventory" && (
        <MediaBatchFiles key={run.id} batch={run.id} refreshToken={refresh} />
      )}
      {run && summary && run.mode !== "inventory" && (
        <>
          <section className="rounded-2xl border bg-white p-5">
            <div className="flex flex-wrap justify-between gap-3">
              <p className="font-semibold">
                {completed
                  ? summary.total
                    ? "批次處理完成"
                    : "沒有需要處理的錄音"
                  : run.desired === "paused"
                    ? summary.running
                      ? "暫停中，等待在途工作結束"
                      : "已暫停排程"
                    : summary.running
                      ? "正在處理"
                      : "目前無已確認的執行中工作"}
              </p>
              <div className="flex gap-2">
                <button
                  disabled={busy || !!error || completed}
                  onClick={() =>
                    void control(run.desired === "paused" ? "resume" : "pause")
                  }
                  className="flex items-center gap-1 rounded-lg border px-3 py-2 text-sm disabled:opacity-40"
                >
                  {run.desired === "paused" ? (
                    <Play size={15} />
                  ) : (
                    <Pause size={15} />
                  )}{" "}
                  {run.desired === "paused" ? "繼續" : "暫停排程"}
                </button>
                <button
                  disabled={
                    busy || !!error || !(summary.failed || summary.interrupted)
                  }
                  onClick={() => void control("retry")}
                  className="flex items-center gap-1 rounded-lg border px-3 py-2 text-sm disabled:opacity-40"
                >
                  <RotateCcw size={15} />
                  重試可恢復失敗
                </button>
              </div>
            </div>
            {controlError && (
              <p role="alert" className="mt-3 text-rose-700">
                {controlError}
              </p>
            )}
            <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
              {[
                ["轉錄與交接完成", `${summary.complete} / ${summary.total} 講`],
                ["處理中", summary.running],
                [
                  "排隊／保留舊稿",
                  `${summary.pending} / ${summary.reused || 0}`,
                ],
                [
                  "失敗／待核實",
                  summary.failed +
                    summary.unknown_outcome +
                    summary.needs_resolution +
                    summary.unverified +
                    (summary.interrupted || 0),
                ],
              ].map(([name, value]) => (
                <div key={name} className="rounded-xl bg-slate-50 p-4">
                  <p className="text-2xl font-bold tabular-nums">{value}</p>
                  <p className="mt-1 text-sm text-slate-500">{name}</p>
                </div>
              ))}
            </div>
            <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-sm">
              {data.capacity &&
                Object.entries(data.capacity).map(([name, c]) => (
                  <p key={name}>
                    {name === "sol" ? "GPT Sol" : name}：
                    {error ? "待核實" : `${c.active} / ${c.limit}`}{" "}
                    {c.unknown > 0 && `· ${c.unknown} 名額結果待核實`}
                  </p>
                ))}
            </div>
            <div className="mt-3 text-xs leading-6 text-slate-500">
              <p>
                呼叫 {run.calls} 次 · 用量費用估算：
                {run.estimated_usd != null
                  ? `$${Number(run.estimated_usd).toFixed(2)}`
                  : run.calls
                    ? "待彙整"
                    : "$0.00"}
                （已核算 {run.costed_calls || 0}/{run.calls} 次，非供應商帳單）
              </p>
              <p>
                派工預算佔用{" "}
                {run.budget_exposure_usd != null
                  ? `$${Number(run.budget_exposure_usd).toFixed(2)}`
                  : "待核實"}{" "}
                / 上限 ${Number(run.budget_limit_usd || 0).toFixed(2)}
                （含在途預留）
              </p>
              <details>
                <summary className="cursor-pointer">預留紀錄（非費用）</summary>
                歷次派工預留累計 ${Number(run.reserved_usd).toFixed(2)}
                ，保留供追溯；已完成請求按保存用量計入預算。
              </details>
              <p>最後讀取 {when(data.generated_at)}</p>
            </div>
            <p className="text-xs text-slate-500">
              {data.disk &&
                Object.entries(data.disk)
                  .map(
                    ([name, d]) =>
                      `${name === "artifact_root" ? "工作碟" : "媒體碟"}：${d.unavailable ? "未掛載" : `${((d.available_bytes || 0) / 2 ** 30).toFixed(1)} GiB 可用／${((d.reserved_bytes || 0) / 2 ** 30).toFixed(1)} GiB 已預約`}`,
                  )
                  .join(" · ")}
            </p>
          </section>
          <section>
            <div className="mb-3 flex flex-wrap gap-3">
              <input
                aria-label="搜尋本頁講道"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜尋本頁講題或 ID"
                className="min-w-0 flex-1 rounded-lg border bg-white px-3 py-2"
              />
              <select
                aria-label="本頁狀態篩選"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                className="rounded-lg border bg-white px-3 py-2"
              >
                <option value="all">本頁全部狀態</option>
                {[
                  "running",
                  "pending",
                  "complete",
                  "failed",
                  "needs_resolution",
                  "unknown_outcome",
                  "unverified",
                  "interrupted",
                ].map((s) => (
                  <option value={s} key={s}>
                    {names[s]}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-3">
              {rows.map((r) => {
                const current =
                  r.tasks.find((t) => t.state === "running") ||
                  r.tasks.find((t) =>
                    ["failed", "unknown_outcome", "needs_resolution"].includes(
                      t.state,
                    ),
                  ) ||
                  r.tasks.find((t) => t.state !== "complete") ||
                  r.tasks[r.tasks.length - 1];
                const link = r.tasks.find((t) => t.result?.editor_href)?.result
                  ?.editor_href;
                return (
                  <article
                    key={r.id}
                    className="rounded-2xl border bg-white p-5"
                  >
                    <div className="grid gap-4 md:grid-cols-[1fr_1fr_auto]">
                      <div className="min-w-0">
                        <h2 className="break-words font-bold">{r.name}</h2>
                        <p className="mt-1 text-xs text-slate-500">
                          {r.item} · {Math.round(Number(r.duration) / 60)} 分鐘
                        </p>
                      </div>
                      <Work task={current} />
                      <div>
                        {link && (
                          <Link
                            href={link}
                            className="inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-3 py-2 text-sm text-white"
                          >
                            開啟校對
                            <ChevronRight size={15} />
                          </Link>
                        )}
                      </div>
                    </div>
                    <details className="mt-4 border-t pt-3">
                      <summary className="cursor-pointer text-sm font-semibold text-slate-500">
                        各階段與錯誤詳情
                      </summary>
                      <div className="mt-4 grid gap-4 md:grid-cols-2">
                        {r.tasks.map((t) => (
                          <Work key={t.id} task={t} />
                        ))}
                      </div>
                    </details>
                  </article>
                );
              })}
            </div>
            <div className="mt-4 flex items-center justify-between text-sm">
              <button
                disabled={offset === 0}
                onClick={() => setOffset((x) => Math.max(0, x - 30))}
                className="rounded-lg border px-4 py-2 disabled:opacity-30"
              >
                上一頁
              </button>
              <span>
                {offset + 1}–{Math.min(offset + 30, summary.total)} /{" "}
                {summary.total} 講
              </span>
              <button
                disabled={offset + 30 >= summary.total}
                onClick={() => setOffset((x) => x + 30)}
                className="rounded-lg border px-4 py-2 disabled:opacity-30"
              >
                下一頁
              </button>
            </div>
          </section>
        </>
      )}
    </main>
  );
}
