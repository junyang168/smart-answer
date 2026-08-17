"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, BookOpen, DatabaseZap, FileCheck2, Search, ShieldAlert } from "lucide-react";

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
  const [loadError, setLoadError] = useState("");

  const load = async () => {
    setLoading(true);
    setLoadError("");
    try {
      const response = await fetch("/api/admin/canonical-repository/units", { cache: "no-store" });
      if (!response.ok) throw new Error(`出版單元服務回傳 ${response.status}`);
      const data = await response.json();
      setUnits(data.units ?? []);
    } catch (reason) {
      setLoadError(reason instanceof Error ? reason.message : "無法讀取出版單元");
    } finally {
      setLoading(false);
    }
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
  const backfillSources = async () => {
    setMessage("正在回填原始來源…");
    const response = await fetch("/api/admin/canonical-repository/units/backfill-source-citations", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
    const result = await response.json();
    setMessage(response.ok
      ? `已為 ${result.processed} 個單元建立來源，共新增 ${result.citations_created} 個待審閱引用；${result.errors.length} 個需要人工處理。`
      : result.detail ?? "來源回填失敗");
    await load();
  };

  const passageCount = units.filter((unit) => unit.unit_type === "passage").length;
  const conceptCount = units.filter((unit) => unit.unit_type === "concept").length;
  const reviewCount = units.filter((unit) => unit.status === "candidate").length;

  return <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-10">
    <Link href="/admin/wang" className="text-sm font-black text-indigo-700">← 返回 Wang 文庫總覽</Link>
    <header className="mt-4 overflow-hidden rounded-3xl bg-slate-950 p-6 text-white sm:p-8">
      <div className="flex flex-wrap items-start justify-between gap-6">
        <div className="max-w-3xl"><p className="text-sm font-black tracking-wide text-amber-300">CANONICAL REPOSITORY</p><h1 className="mt-2 text-3xl font-black tracking-tight sm:text-4xl">出版單元</h1><p className="mt-3 leading-7 text-slate-300">審閱舊式 canonical units、來源引用與公開索引。這裡的「發布」與馬太福音文章 runner 的 repository publication 是不同管線。</p></div>
        <Link href="/resources/wang-repository" className="inline-flex items-center gap-2 rounded-xl bg-white/10 px-4 py-2.5 text-sm font-black hover:bg-white/20">預覽公開頁<ArrowUpRight className="h-4 w-4" /></Link>
      </div>
      <div className="mt-7 grid grid-cols-3 gap-3">
        <div className="rounded-2xl bg-white/10 p-4"><p className="text-2xl font-black">{passageCount}</p><p className="mt-1 text-xs text-slate-300">經文單元</p></div>
        <div className="rounded-2xl bg-white/10 p-4"><p className="text-2xl font-black">{conceptCount}</p><p className="mt-1 text-xs text-slate-300">主題單元</p></div>
        <div className="rounded-2xl bg-white/10 p-4"><p className="text-2xl font-black">{reviewCount}</p><p className="mt-1 text-xs text-slate-300">待審閱</p></div>
      </div>
    </header>

    <section className="mt-6 rounded-2xl border border-amber-200 bg-amber-50 p-4">
      <div className="flex items-start gap-3"><ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-700" /><div><h2 className="font-black text-amber-950">維護與出版動作</h2><p className="mt-1 text-sm leading-6 text-amber-800">以下動作會改動 canonical repository。候選單元不會自動發布；建立公開索引前仍會執行既有驗證。</p></div></div>
      <div className="mt-4 flex flex-wrap gap-2 pl-0 sm:pl-8"><button onClick={importSeed} className="inline-flex items-center gap-2 rounded-lg border border-indigo-300 bg-white px-4 py-2 text-sm font-black text-indigo-700 hover:bg-indigo-50"><BookOpen className="h-4 w-4" />匯入 Seed</button><button onClick={backfillSources} className="inline-flex items-center gap-2 rounded-lg border border-amber-300 bg-white px-4 py-2 text-sm font-black text-amber-800 hover:bg-amber-100"><DatabaseZap className="h-4 w-4" />回填來源</button><button onClick={buildRepository} className="inline-flex items-center gap-2 rounded-lg bg-slate-950 px-4 py-2 text-sm font-black text-white hover:bg-slate-800"><FileCheck2 className="h-4 w-4" />驗證並建立公開索引</button></div>
    </section>
    {message ? <p role="status" className="mt-4 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm font-semibold text-blue-800">{message}</p> : null}

    <section className="mt-6 rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
      <div className="flex flex-wrap gap-2">
        {([['passage','按經文'],['concept','按主題']] as const).map(([key,label]) => <button key={key} onClick={() => setView(key)} className={`rounded-xl px-4 py-2.5 text-sm font-black ${view===key?'bg-indigo-600 text-white':'text-slate-600 hover:bg-slate-100'}`}>{label}（{key === "passage" ? passageCount : conceptCount}）</button>)}
        <label className="relative min-w-64 flex-1"><Search className="absolute left-3 top-3 h-4 w-4 text-slate-400" /><span className="sr-only">搜尋出版單元</span><input value={filter} onChange={(event)=>setFilter(event.target.value)} placeholder="搜尋單元、經文或主題…" className="w-full rounded-xl border border-slate-300 py-2.5 pl-9 pr-3 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100" /></label>
      </div>
    </section>

    {loading ? <p className="py-16 text-center text-slate-500">正在讀取出版單元…</p> : loadError ? <div className="mt-5 rounded-2xl border border-rose-200 bg-rose-50 p-6 text-rose-800"><p className="font-black">出版單元資料無法載入</p><p className="mt-2 text-sm">{loadError}</p><button onClick={() => void load()} className="mt-4 rounded-lg bg-rose-700 px-4 py-2 text-sm font-black text-white">重試</button></div> : shown.length === 0 ? <div className="mt-5 rounded-2xl border border-dashed border-slate-300 bg-white py-16 text-center text-slate-500">沒有符合條件的單元。</div> : <div className="mt-5 grid gap-4 lg:grid-cols-2">{shown.map((unit) => <article key={unit.unit_id} className="group rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:border-indigo-300 hover:shadow-md">
      <div className="flex items-start justify-between gap-4"><div className="min-w-0"><div className="flex flex-wrap gap-2">{unit.primary_bible_refs.map((ref)=><span key={ref.osis} className="rounded-lg bg-indigo-50 px-2.5 py-1 text-xs font-black text-indigo-700">{ref.display}</span>)}<span className={`rounded-lg px-2.5 py-1 text-xs font-black ${unit.status === 'published' ? 'bg-emerald-100 text-emerald-800' : unit.status === 'candidate' ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-700'}`}>{unit.status==='candidate'?'待審閱':unit.status==='published'?'已發布':unit.status}</span></div><h2 className="mt-3 text-lg font-black leading-7 text-slate-950"><Link href={`/admin/canonical-repository/${encodeURIComponent(unit.unit_id)}`} className="group-hover:text-indigo-700">{unit.title}</Link></h2><p className="mt-2 text-sm leading-6 text-slate-500">{unit.topic_assignments.map((item)=>item.path.join(' › ')).join('；') || '尚未指定主題'}</p></div><ArrowUpRight className="h-5 w-5 shrink-0 text-slate-300 group-hover:text-indigo-600" /></div>
      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4 text-sm"><span className="font-semibold text-slate-600">{unit.citation_count} 個來源引用</span><Link className="font-black text-indigo-700" href={`/admin/notes-to-sermon/project/${encodeURIComponent(unit.manuscript.project_id)}`}>Manuscript：{unit.manuscript.heading_title || unit.manuscript.project_id}</Link></div>
    </article>)}</div>}
  </main>;
}
