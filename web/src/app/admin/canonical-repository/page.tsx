"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

type Unit = {
  unit_id: string; title: string; unit_type: "passage" | "concept"; status: string;
  primary_bible_refs: { osis: string; display: string }[];
  topic_assignments: { path: string[]; role: string }[];
  manuscript: { project_id: string; project_type: string; heading_title: string; heading_anchor: string };
  citation_count: number;
};

export default function CanonicalRepositoryPage() {
  const [units, setUnits] = useState<Unit[]>([]);
  const [view, setView] = useState<"passage" | "concept">("passage");
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  const load = async () => {
    setLoading(true);
    const response = await fetch("/api/admin/canonical-repository/units", { cache: "no-store" });
    const data = await response.json();
    setUnits(data.units ?? []);
    setLoading(false);
  };
  useEffect(() => { void load(); }, []);

  const shown = useMemo(() => units.filter((unit) => {
    const needle = filter.trim().toLowerCase();
    const haystack = [unit.title, ...unit.primary_bible_refs.map((item) => item.display), ...unit.topic_assignments.flatMap((item) => item.path)].join(" ").toLowerCase();
    return unit.unit_type === view && (!needle || haystack.includes(needle));
  }), [filter, units, view]);

  const importSeed = async () => {
    setMessage("正在匯入候選單元…");
    const response = await fetch("/api/admin/canonical-repository/units/import-candidates", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
    const result = await response.json();
    setMessage(response.ok ? `已匯入 ${result.imported} 個，略過 ${result.skipped} 個既有單元。` : result.detail ?? "匯入失敗");
    await load();
  };
  const buildRepository = async () => {
    setMessage("正在驗證並建立公開索引…");
    const response = await fetch("/api/admin/canonical-repository/build", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
    const result = await response.json();
    if (response.ok) setMessage(`公開索引已建立：${result.unit_count} 個單元。`);
    else setMessage(`尚不能發布：${(result.detail?.findings ?? [result.detail?.message ?? result.detail ?? '驗證失敗']).join('；')}`);
  };

  return <main className="mx-auto max-w-6xl px-6 py-10">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div><p className="text-sm font-semibold text-sky-700">王守仁教授釋經與專題講論文庫</p><h1 className="mt-1 text-3xl font-bold text-slate-950">單元審閱列表</h1><p className="mt-2 text-slate-600">候選單元不會自動發布；先檢查歸類、題目、manuscript 與來源。</p></div>
      <div className="flex flex-wrap gap-2"><button onClick={importSeed} className="rounded-lg border border-indigo-300 px-4 py-2 font-semibold text-indigo-700 hover:bg-indigo-50">匯入 Seed 候選單元</button><button onClick={buildRepository} className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-700">建立公開索引</button><Link href="/resources/wang-repository" className="rounded-lg border px-4 py-2 font-semibold text-slate-700">預覽公開頁</Link></div>
    </div>
    {message ? <p className="mt-4 rounded-lg bg-blue-50 px-4 py-3 text-sm text-blue-800">{message}</p> : null}
    <div className="mt-8 flex flex-wrap gap-3 border-b border-slate-200 pb-4">
      {([['passage','按經文'],['concept','按主題']] as const).map(([key,label]) => <button key={key} onClick={() => setView(key)} className={`rounded-lg px-4 py-2 font-semibold ${view===key?'bg-sky-600 text-white':'bg-slate-100 text-slate-700'}`}>{label}（{units.filter((u)=>u.unit_type===key).length}）</button>)}
      <input value={filter} onChange={(event)=>setFilter(event.target.value)} placeholder="搜尋單元、經文或主題…" className="min-w-64 flex-1 rounded-lg border border-slate-300 px-3 py-2" />
    </div>
    {loading ? <p className="py-12 text-center text-slate-500">讀取中…</p> : shown.length === 0 ? <p className="py-12 text-center text-slate-500">尚無單元。請先匯入 Seed 候選單元。</p> : <div className="mt-5 space-y-3">{shown.map((unit) => <article key={unit.unit_id} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex flex-wrap gap-2">{unit.primary_bible_refs.map((ref)=><span key={ref.osis} className="rounded bg-sky-50 px-2 py-1 text-xs font-semibold text-sky-700">{ref.display}</span>)}<span className="rounded bg-amber-50 px-2 py-1 text-xs font-semibold text-amber-700">{unit.status==='candidate'?'待審閱':unit.status}</span></div><h2 className="mt-2 text-lg font-bold text-slate-900"><Link href={`/admin/canonical-repository/${encodeURIComponent(unit.unit_id)}`} className="hover:text-indigo-700 hover:underline">{unit.title}</Link></h2><p className="mt-1 text-sm text-slate-500">{unit.topic_assignments.map((item)=>item.path.join(' › ')).join('；') || '尚未指定主題'}</p></div><span className="text-sm text-slate-500">來源引用 {unit.citation_count}</span></div>
      <div className="mt-4 border-t border-slate-100 pt-3 text-sm"><Link className="font-semibold text-indigo-600 hover:underline" href={`/admin/notes-to-sermon/project/${encodeURIComponent(unit.manuscript.project_id)}`}>開啟 manuscript project</Link><span className="mx-2 text-slate-300">|</span><span className="text-slate-600">{unit.manuscript.heading_title}</span></div>
    </article>)}</div>}
  </main>;
}
