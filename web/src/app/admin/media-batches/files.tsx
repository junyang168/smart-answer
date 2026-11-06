"use client";
import { useEffect, useState } from "react";

const labels: Record<string, string> = {
  all: "全部檔案",
  actionable: "所有待處理",
  waiting_download: "待下載／核對",
  ready_to_plan: "待查重與排程",
  needs_assembly: "待組裝",
  needs_scope_review: "待確認講員",
  pending: "排隊",
  running: "處理中",
  complete: "已完成",
  reused: "已沿用逐字稿",
  existing_transcript: "已有逐字稿",
  duplicate: "重複檔案",
  excluded: "不需轉錄",
  failed: "失敗",
  needs_resolution: "來源待確認",
  unknown_outcome: "呼叫結果待核實",
  unverified: "程序失聯待核實",
  interrupted: "中斷待接續",
};
const stages: Record<string, string> = {
  prepare: "準備音訊",
  visuals: "投影片與 OCR",
  speech: "轉錄與校字",
  "final-align": "時間對齊",
  finish: "最終檢查",
  speakers: "講員辨識",
  media: "網站影音",
  handoff: "交接校對",
  existing: "沿用已有逐字稿",
};
type File = {
  path: string;
  bytes: number;
  state: string;
  reason: string | null;
  duplicate_of: string | null;
  item: string | null;
  stage: string | null;
  title?: string | null;
};
type Data = {
  files: File[];
  total: number;
  filtered_total: number;
  counts: Record<string, number>;
  batch: {
    inventory_at: string | null;
    inventory_error?: string | null;
    desired?: string;
  };
  generated_at: string;
};
export function MediaBatchFiles({
  batch,
  refreshToken = 0,
}: {
  batch: string;
  refreshToken?: number;
}) {
  const [data, setData] = useState<Data | null>(null),
    [error, setError] = useState(""),
    [search, setSearch] = useState(""),
    [query, setQuery] = useState(""),
    [state, setState] = useState("all"),
    [offset, setOffset] = useState(0);
  useEffect(() => {
    setData(null);
    setError("");
  }, [batch, offset, query, state]);
  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(search);
      setOffset(0);
    }, 250);
    return () => clearTimeout(timer);
  }, [search]);
  useEffect(() => {
    let stopped = false,
      inFlight = false,
      timer: ReturnType<typeof setTimeout> | undefined;
    const abort = new AbortController();
    const load = async () => {
      if (stopped || document.hidden || inFlight) return;
      inFlight = true;
      try {
        const q = new URLSearchParams({
          offset: String(offset),
          limit: "30",
          search: query,
          state,
        });
        const res = await fetch(
          `/api/sc_api/media_batches/${batch}/files?${q}`,
          { cache: "no-store", signal: abort.signal },
        );
        const value = await res.json();
        if (!res.ok) throw Error(value.detail || "無法讀取檔案清單");
        if (!stopped) {
          setData(value);
          setError("");
        }
      } catch (e) {
        if (!stopped) setError(e instanceof Error ? e.message : "狀態未更新");
      } finally {
        inFlight = false;
        if (!stopped) timer = setTimeout(() => void load(), 15000);
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
  }, [batch, offset, query, state, refreshToken]);
  const choose = (value: string) => {
    setState(value);
    setOffset(0);
  };
  return (
    <section className="space-y-4" aria-label="全部檔案作業狀態">
      {data &&
        (data.batch.inventory_error ||
          data.batch.desired === "paused" ||
          (data.batch.inventory_at &&
            Date.parse(data.generated_at) -
              Date.parse(data.batch.inventory_at) >
              45000)) && (
          <p role="alert" className="rounded-lg bg-amber-50 p-4 text-amber-900">
            {data.batch.inventory_error ||
              (data.batch.desired === "paused"
                ? "來源清單同步已暫停"
                : "來源清單超過 45 秒未同步")}
            ；以下保留最後一次已確認的狀態。
          </p>
        )}
      <div className="rounded-2xl border bg-white p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="text-xl font-bold">
            全部檔案{" "}
            <span className="text-indigo-700">{data?.total ?? "…"}</span>
          </h2>
          <p className="text-xs text-slate-500">
            每 15 秒更新 ·{" "}
            {data?.batch.inventory_at
              ? `清單同步 ${new Date(data.batch.inventory_at).toLocaleTimeString("zh-TW")}`
              : "轉錄作業台帳"}
          </p>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {data &&
            Object.entries(data.counts).map(([key, count]) => (
              <button
                key={key}
                onClick={() => choose(key)}
                className={`rounded-lg border px-3 py-2 text-sm ${state === key ? "border-indigo-500 bg-indigo-50 text-indigo-900" : "bg-slate-50"}`}
              >
                {labels[key] || key} <strong>{count}</strong>
              </button>
            ))}
        </div>
        <div className="mt-4 flex flex-wrap gap-3">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜尋全部檔案：檔名、資料夾或講道 ID"
            aria-label="搜尋全部檔案"
            className="min-w-0 flex-1 rounded-lg border px-3 py-2"
          />
          <select
            value={state}
            onChange={(e) => choose(e.target.value)}
            aria-label="全部檔案狀態篩選"
            className="max-w-full rounded-lg border bg-white px-3 py-2"
          >
            {Object.entries(labels).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </div>
      {error && (
        <p role="alert" className="rounded-lg bg-amber-50 p-4 text-amber-900">
          {error}；保留上次資料，尚未確認最新狀態。
        </p>
      )}
      {!data && !error && <p role="status">正在讀取完整檔案清單…</p>}
      {data && (
        <>
          <div className="overflow-hidden rounded-2xl border bg-white">
            <div className="hidden grid-cols-[minmax(0,1fr)_10rem_6rem] gap-4 border-b bg-slate-100 px-5 py-3 text-sm font-semibold md:grid">
              <span>檔案／等待原因</span>
              <span>目前狀態</span>
              <span>大小</span>
            </div>
            <div className="divide-y">
              {data.files.map((file) => (
                <article
                  key={file.path}
                  className="grid gap-2 px-5 py-4 md:grid-cols-[minmax(0,1fr)_10rem_6rem] md:gap-4"
                >
                  <div className="min-w-0">
                    <h3 className="break-words font-semibold">
                      {file.path.split("/").pop()}
                    </h3>
                    <p className="mt-1 text-sm text-slate-600">
                      講道名稱：{file.title || "尚未辨識"}
                    </p>
                    <p className="mt-1 break-all text-xs text-slate-500">
                      {file.path.includes("/")
                        ? file.path.slice(0, file.path.lastIndexOf("/"))
                        : "來源資料夾根目錄"}
                    </p>
                    {file.reason && (
                      <p className="mt-2 text-sm text-slate-600">
                        {file.reason}
                      </p>
                    )}
                    {file.duplicate_of && (
                      <p className="mt-1 break-all text-xs text-slate-500">
                        相同來源：{file.duplicate_of}
                      </p>
                    )}
                    {file.item && (
                      <p className="mt-1 text-xs text-slate-500">
                        講道 {file.item}
                        {file.stage
                          ? ` · ${stages[file.stage] || file.stage}`
                          : ""}
                      </p>
                    )}
                  </div>
                  <div>
                    <span
                      className={`inline-block rounded-full px-3 py-1 text-xs font-semibold ${file.state === "complete" || file.state === "reused" || file.state === "existing_transcript" ? "bg-emerald-50 text-emerald-800" : file.state === "running" ? "bg-indigo-50 text-indigo-800" : "bg-amber-50 text-amber-900"}`}
                    >
                      {labels[file.state] || file.state}
                    </span>
                  </div>
                  <span className="text-xs text-slate-500">
                    {(file.bytes / 2 ** 20).toFixed(1)} MiB
                  </span>
                </article>
              ))}
            </div>
            {!data.files.length && (
              <p className="p-8 text-center text-slate-500">
                沒有符合條件的檔案
              </p>
            )}
          </div>
          <div className="flex items-center justify-between gap-3 text-sm">
            <button
              disabled={!offset}
              onClick={() => setOffset((x) => Math.max(0, x - 30))}
              className="rounded-lg border bg-white px-4 py-2 disabled:opacity-30"
            >
              上一頁
            </button>
            <p>
              {data.filtered_total ? offset + 1 : 0}–
              {Math.min(offset + 30, data.filtered_total)} /{" "}
              {data.filtered_total} 個檔案
            </p>
            <button
              disabled={offset + 30 >= data.filtered_total}
              onClick={() => setOffset((x) => x + 30)}
              className="rounded-lg border bg-white px-4 py-2 disabled:opacity-30"
            >
              下一頁
            </button>
          </div>
        </>
      )}
    </section>
  );
}
