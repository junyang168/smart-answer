"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowLeft, CheckCircle2, AlertTriangle } from "lucide-react";

type UnitRow = {
  unit_id: string;
  title: string;
  passage: string;
  chars: number;
  cross_lecture: boolean;
  lectures: string[];
  ready: boolean;
  loaded_in_db?: boolean;
  node_count: number;
  edge_count: number;
  claim_count: number;
  fidelity: { faithful: number; total: number; changed: number; added: number } | null;
  roundtrip: { core_covered: number; core_total: number; distortions: number; omissions: number } | null;
  ruling: { verdict: "approved" | "needs_revision"; note: string; at: string } | null;
};

type SeriesDetail = {
  id: string;
  title: string;
  lectures: { lecture: string; label: string; title: string }[];
  units: UnitRow[];
  non_argument_shells: { pk: string; reason: string }[];
};

export default function SeriesDetailPage() {
  const params = useParams<{ seriesId: string }>();
  const seriesId = decodeURIComponent(params.seriesId);
  const [series, setSeries] = useState<SeriesDetail | null>(null);
  const [error, setError] = useState("");
  const [showShells, setShowShells] = useState(false);

  useEffect(() => {
    fetch(`/api/admin/lecture-argument/series/${encodeURIComponent(seriesId)}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`讀取失敗（${r.status}）`))))
      .then(setSeries)
      .catch((e) => setError(String(e.message ?? e)));
  }, [seriesId]);

  if (error) return <main className="mx-auto max-w-4xl px-4 py-6 text-rose-700">{error}</main>;
  if (!series) return <main className="mx-auto max-w-4xl px-4 py-6 text-slate-400">載入中…</main>;

  return (
    <main className="mx-auto max-w-4xl px-4 py-6 pb-20">
      <Link href="/admin/wang/series" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800">
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />所有系列
      </Link>
      <h1 className="mt-2 text-2xl font-bold text-slate-900">{series.title}</h1>
      <p className="mt-1 text-sm text-slate-600">
        共 {series.lectures.length} 講，切分為 {series.units.length} 個無重疊邏輯單元。點進已生成的單元可逐點核對原話、聽錄音並做人工驗收。
      </p>

      <ul className="mt-6 divide-y divide-slate-200 rounded-xl border border-slate-200 bg-white">
        {series.units.map((u, idx) => {
          const content = (
            <div className="px-4 py-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-slate-400">單元 {idx + 1}</span>
                  <span className="font-semibold text-slate-900">{u.title}</span>
                  {u.cross_lecture && (
                    <span className="rounded bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800">
                      跨講：{u.lectures.join(" → ")}
                    </span>
                  )}
                  {!u.cross_lecture && (
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
                      {u.lectures.join("、")}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2 text-xs">
                  {u.ruling?.verdict === "approved" && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-0.5 font-medium text-emerald-700">
                      <CheckCircle2 className="h-3.5 w-3.5" />已通過驗收
                    </span>
                  )}
                  {u.ruling?.verdict === "needs_revision" && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-rose-50 px-2.5 py-0.5 font-medium text-rose-700">
                      <AlertTriangle className="h-3.5 w-3.5" />需要修改
                    </span>
                  )}
                  {!u.ready && <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-slate-500">待生成論證</span>}
                  {u.ready && !u.ruling && u.loaded_in_db && (
                    <span className="rounded-full bg-emerald-50 px-2.5 py-0.5 font-medium text-emerald-700">已入庫（候選）</span>
                  )}
                  {u.ready && !u.ruling && !u.loaded_in_db && (
                    <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 font-medium text-indigo-700">待入庫</span>
                  )}
                </div>
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
                {u.passage && <span>經文：{u.passage}</span>}
                <span>約 {u.chars.toLocaleString()} 字</span>
                {u.ready && <span>{u.claim_count} 個論點 · {u.edge_count} 條關係</span>}
                {u.fidelity && (
                  <span className={u.fidelity.changed + u.fidelity.added > 0 ? "text-amber-700" : "text-emerald-700"}>
                    逐點忠實度 {u.fidelity.faithful}/{u.fidelity.total}
                  </span>
                )}
                {u.roundtrip && (
                  <span className={u.roundtrip.distortions > 0 ? "text-rose-700" : "text-emerald-700"}>
                    重構覆蓋 {u.roundtrip.core_covered}/{u.roundtrip.core_total}（扭曲 {u.roundtrip.distortions}）
                  </span>
                )}
              </div>
            </div>
          );
          return (
            <li key={u.unit_id}>
              {u.ready ? (
                <Link href={`/admin/wang/series/${encodeURIComponent(series.id)}/${encodeURIComponent(u.unit_id)}`} className="block hover:bg-slate-50">
                  {content}
                </Link>
              ) : (
                <div className="opacity-70">{content}</div>
              )}
            </li>
          );
        })}
      </ul>

      {series.non_argument_shells.length > 0 && (
        <section className="mt-6 rounded-xl border border-slate-200 bg-slate-50 p-4">
          <button
            onClick={() => setShowShells((v) => !v)}
            className="text-sm font-medium text-slate-700 underline"
          >
            {showShells ? "收起" : "查看"}本系列已剝離的純禱告／報幕段落（{series.non_argument_shells.length} 段）
          </button>
          {showShells && (
            <ul className="mt-3 space-y-1 text-xs text-slate-600">
              {series.non_argument_shells.map((s) => (
                <li key={s.pk}>{s.pk} · {s.reason === "prayer" ? "禱告" : "場務／報幕"}</li>
              ))}
            </ul>
          )}
        </section>
      )}
    </main>
  );
}
