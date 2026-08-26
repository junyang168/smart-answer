"use client";

import { useState } from "react";
import type { RunStatus } from "./types";
import { count } from "./types";

/**
 * Start an audit from the page that reads it, and show how far it has got.
 *
 * The page could only show what had already been written, so a report went
 * stale the moment anything changed and nobody reading it could tell. That is
 * not hypothetical: a check was corrected and two withdrawn findings stayed on
 * screen, because the newest run predated the fix.
 *
 * The cost is stated before the button, not after. A run is roughly 1,400
 * model calls and about ten minutes, and someone who does not know that will
 * either never press it or press it three times. Judgements are cached by
 * prompt, so a re-run only pays for what actually changed -- which is the part
 * that makes pressing it reasonable, so it is on screen too.
 */
export function RunControl({
  run,
  ranAt,
  onChange,
}: {
  run: RunStatus | null;
  ranAt: string;
  onChange: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const running = run?.state === "running";

  async function post(method: "POST" | "DELETE") {
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/admin/library-audit/runs", { method });
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(body?.detail || `服務回傳 ${response.status}`);
      }
      onChange();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "跑不起來");
    } finally {
      setBusy(false);
    }
  }

  if (running) {
    const done = run?.done ?? 0;
    const total = run?.total ?? 0;
    const pct = total > 0 ? Math.round((done / total) * 100) : null;
    return (
      <div className="flex flex-col gap-2 rounded-xl border border-indigo-200 bg-indigo-50/60 p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-sm font-semibold text-indigo-900">
            正在跑 · {run?.stage || "…"}
            {total > 0 && (
              <span className="ml-2 font-mono font-normal">
                {count(done)}/{count(total)}
                {pct !== null && ` （${pct}%）`}
              </span>
            )}
          </p>
          <button
            type="button"
            disabled={busy}
            onClick={() => void post("DELETE")}
            className="rounded-lg px-2 py-1 text-xs font-semibold text-indigo-700 underline decoration-indigo-300 underline-offset-4 hover:decoration-indigo-700 disabled:opacity-50"
          >
            停下來
          </button>
        </div>
        {/* Determinate where the audit reports a total, indeterminate in the
            deterministic layers where there is nothing to count. */}
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-indigo-100">
          <div
            className={`h-full rounded-full bg-indigo-500 ${pct === null ? "w-1/3 animate-pulse" : "transition-[width] duration-500"}`}
            style={pct === null ? undefined : { width: `${pct}%` }}
          />
        </div>
        <p className="text-[0.75rem] text-indigo-900/70">
          審計只讀，停下來不會留下任何半成品；跑完這一頁會自己換成新的一輪。
        </p>
        {error && <p className="text-[0.78rem] text-rose-700">{error}</p>}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[0.82rem] text-slate-600">
          上一輪跑在 <span className="font-mono text-slate-900">{ranAt}</span>
          {run?.state === "died" && (
            <span className="ml-2 text-rose-700">上一次沒跑完就中斷了。</span>
          )}
        </p>
        <button
          type="button"
          disabled={busy}
          onClick={() => void post("POST")}
          className="rounded-lg bg-slate-900 px-3 py-2 text-sm font-semibold text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {busy ? "啟動中…" : "重新跑一次"}
        </button>
      </div>
      <p className="text-[0.75rem] leading-relaxed text-slate-400">
        全查範圍內的主張與觀點，約一千四百次模型呼叫、十來分鐘。判讀結果按 prompt 存檔，
        沒變過的部分不重打，所以第二次通常只花幾分鐘。
      </p>
      {error && <p className="text-[0.78rem] text-rose-700">{error}</p>}
    </div>
  );
}
