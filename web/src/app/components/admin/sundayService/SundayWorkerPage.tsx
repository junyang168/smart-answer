"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { fetchSundayWorkers } from "@/app/admin/sunday-service/api";
import { SundayWorker } from "@/app/types/sundayService";
import { SundayWorkerManager } from "@/app/components/admin/sundayService/SundayWorkerManager";

type LoadStatus = "loading" | "ready" | "error";

export function SundayWorkerPage() {
  const [workers, setWorkers] = useState<SundayWorker[]>([]);
  const [status, setStatus] = useState<LoadStatus>("loading");
  const [error, setError] = useState<string | null>(null);

  const loadWorkers = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const data = await fetchSundayWorkers();
      setWorkers(data);
      setStatus("ready");
    } catch (err) {
      const message = err instanceof Error ? err.message : "載入同工名單失敗";
      setError(message);
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    void loadWorkers();
  }, [loadWorkers]);

  return (
    <div className="space-y-6">
      <header className="rounded-lg border border-blue-100 bg-blue-50 p-6">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="text-2xl font-bold text-gray-900">主日同工名單</h2>
            <p className="mt-1 text-sm text-gray-600">管理服事同工與可服事日期。</p>
          </div>
          <div className="flex gap-3">
            <Link
              href="/admin/sunday-service"
              className="rounded border border-blue-300 px-4 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-100"
            >
              返回主日服事
            </Link>
            <button
              type="button"
              className="rounded border border-gray-300 px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
              onClick={() => loadWorkers()}
              disabled={status === "loading"}
            >
              {status === "loading" ? "載入中…" : "重新載入"}
            </button>
          </div>
        </div>
      </header>

      {error && (
        <div className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <SundayWorkerManager workers={workers} onWorkersChanged={loadWorkers} />
      </section>
    </div>
  );
}
