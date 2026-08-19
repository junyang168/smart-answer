"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  ChevronDown,
  CircleDashed,
  Loader2,
  Radio,
  RefreshCw,
} from "lucide-react";
import type { MatthewProgress, ProgressArticle, WorkflowStage } from "../types";

const stageLabels: Record<WorkflowStage, string> = {
  composition_ready: "編排就緒",
  knowledge_ready: "知識就緒",
  authoring: "文章生成",
  independent_editorial_review: "獨立編審",
  revision: "修訂",
  final_delta_review: "最終差異審核",
  program_audit: "程式審計",
  publication_decision: "出版決定",
  repository_published: "寫入文庫",
  production_visible: "線上可見",
};

const blockerLabels: Record<string, string> = {
  knowledge_scope_incomplete: "跨章知識範圍尚未驗證",
  production_deployment_lag: "Production 尚未識別此稿",
  repository_gate_failed: "Repository 出版門檻未通過",
  sha_mismatch: "稿件與審核 SHA 不一致",
};

function percent(value: number, total: number) {
  return total ? Math.round((value / total) * 100) : 0;
}

function formatTime(value: string | null) {
  if (!value) return "無時間資料";
  return new Intl.DateTimeFormat("zh-TW", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

async function fetchProgress(): Promise<MatthewProgress> {
  const response = await fetch("/api/admin/wang/matthew-progress", { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "無法讀取文章進度");
  return payload;
}

function Metric({ label, value, detail, tone = "slate" }: { label: string; value: string; detail: string; tone?: "slate" | "indigo" | "emerald" | "amber" }) {
  const tones = {
    slate: "bg-slate-950 text-white",
    indigo: "border-indigo-200 bg-indigo-50 text-indigo-950",
    emerald: "border-emerald-200 bg-emerald-50 text-emerald-950",
    amber: "border-amber-200 bg-amber-50 text-amber-950",
  };
  return (
    <div className={`rounded-2xl border border-transparent p-5 ${tones[tone]}`}>
      <p className="text-xs font-black uppercase tracking-[0.16em] opacity-60">{label}</p>
      <p className="mt-2 text-3xl font-black tracking-tight">{value}</p>
      <p className="mt-2 text-sm leading-5 opacity-70">{detail}</p>
    </div>
  );
}

function StatusPill({ value }: { value: boolean | null }) {
  if (value === null) return <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-bold text-slate-600">未知</span>;
  if (value) return <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-bold text-emerald-800">是</span>;
  return <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-bold text-slate-500">否</span>;
}

function Workflow({ article }: { article: ProgressArticle }) {
  return (
    <ol className="grid grid-cols-5 gap-x-1 gap-y-3 lg:grid-cols-10" aria-label="文章工作流階段">
      {article.stages.map((item) => {
        const complete = item.state === "complete";
        const active = item.state === "active";
        const unknown = item.state === "unknown";
        return (
          <li key={item.stage} className="min-w-0">
            <div className={`mb-2 h-1.5 rounded-full ${complete ? "bg-emerald-500" : active ? "bg-indigo-500" : unknown ? "bg-amber-300" : "bg-slate-200"}`} />
            <p className={`text-[11px] font-bold leading-4 ${complete ? "text-emerald-800" : active ? "text-indigo-700" : unknown ? "text-amber-700" : "text-slate-400"}`}>
              {stageLabels[item.stage]}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

function ArticleCard({ article }: { article: ProgressArticle }) {
  const [open, setOpen] = useState(false);
  const failures = (article.editorial?.hard_gate_failures.length ?? 0) + (article.editorial?.declared_hard_failures.length ?? 0);
  return (
    <article className={`rounded-2xl border bg-white p-5 shadow-sm ${article.blockers.length ? "border-amber-300" : "border-slate-200"}`}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-lg bg-indigo-50 px-2.5 py-1 text-xs font-black text-indigo-700">{article.passage.display}</span>
            {article.passage.cross_chapter && <span className="rounded-lg bg-violet-100 px-2.5 py-1 text-xs font-black text-violet-800">跨章單元</span>}
            <span className="rounded-lg bg-slate-100 px-2.5 py-1 text-xs font-bold text-slate-600">{stageLabels[article.current_stage]}</span>
          </div>
          <h3 className="mt-3 text-lg font-black text-slate-950">{article.title}</h3>
          <p className="mt-1 font-mono text-xs text-slate-500">{article.draft_id ?? "尚無 draft ID"}</p>
        </div>
        <div className="grid grid-cols-4 gap-3 text-center text-xs">
          <div><p className="text-slate-400">已生成</p><div className="mt-1"><StatusPill value={Boolean(article.draft_id)} /></div></div>
          <div><p className="text-slate-400">Repository</p><div className="mt-1"><StatusPill value={article.repository_published} /></div></div>
          <div><p className="text-slate-400">出版決定</p><div className="mt-1"><StatusPill value={article.publication_decision ? true : false} /></div></div>
          <div><p className="text-slate-400">Production</p><div className="mt-1"><StatusPill value={article.production_visible} /></div></div>
        </div>
      </div>

      <div className="mt-5"><Workflow article={article} /></div>

      {article.blockers.length > 0 && (
        <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-3">
          {article.blockers.map((blocker) => (
            <p key={blocker.code} className="flex items-start gap-2 text-sm font-semibold text-amber-900">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{blocker.message || blockerLabels[blocker.code] || blocker.code}
            </p>
          ))}
          {article.next_step && <p className="mt-2 pl-6 text-sm text-amber-800"><span className="font-black">下一步：</span>{article.next_step}</p>}
        </div>
      )}

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4">
        <div className="flex flex-wrap gap-x-5 gap-y-2 text-sm text-slate-600">
          <span>編審分數 <strong className="text-slate-950">{article.editorial?.score ?? "—"}</strong></span>
          <span>Hard failures <strong className={failures ? "text-rose-700" : "text-slate-950"}>{article.editorial ? failures : "—"}</strong></span>
          <span>Audit errors <strong className={article.program_audit?.error_count ? "text-rose-700" : "text-slate-950"}>{article.program_audit?.error_count ?? "—"}</strong></span>
          <span>原聲播放器 <strong className="text-slate-950">{article.media.player_count}</strong></span>
        </div>
        <button type="button" onClick={() => setOpen((value) => !value)} className="inline-flex items-center gap-1 text-sm font-bold text-indigo-700">
          {open ? "收起技術資料" : "查看技術資料"}<ChevronDown className={`h-4 w-4 transition ${open ? "rotate-180" : ""}`} />
        </button>
      </div>

      {open && (
        <div className="mt-4 grid gap-4 rounded-xl bg-slate-50 p-4 text-sm md:grid-cols-3">
          <div>
            <p className="font-black text-slate-900">出版判定</p>
            <p className="mt-1 text-slate-600">{article.publication_decision ? `${article.publication_decision.kind === "automated" ? "自動" : article.publication_decision.kind === "human" ? "人工" : "未知"} · ${article.publication_decision.schema_version ?? "舊／缺失 schema"}` : "尚無出版決定"}</p>
          </div>
          <div>
            <p className="font-black text-slate-900">SHA 完整性</p>
            <p className={`mt-1 ${article.sha_integrity.status === "mismatch" ? "font-bold text-rose-700" : "text-slate-600"}`}>{article.sha_integrity.status === "consistent" ? "稿件、審核、審計一致" : article.sha_integrity.status === "partial" ? "部分 SHA 缺失" : article.sha_integrity.status === "mismatch" ? "SHA 不一致" : "尚不適用"}</p>
          </div>
          <div>
            <p className="font-black text-slate-900">最後更新</p>
            <p className="mt-1 text-slate-600">{formatTime(article.updated_at)}</p>
          </div>
          <div className="flex flex-wrap gap-3 md:col-span-3">
            {article.links.public && <Link href={article.links.public} className="inline-flex items-center gap-1 font-bold text-indigo-700">開啟本機公開文章 <ArrowUpRight className="h-4 w-4" /></Link>}
            {([[
              "編審 JSON", article.links.editorial_review,
            ], ["稿件", article.links.manuscript], ["Program Audit", article.links.program_audit], ["出版決定", article.links.publication_decision], ["Manifest", article.links.manifest]] as const).map(([label, href]) => href ? <a key={label} href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-bold text-slate-600 hover:text-indigo-700">{label}<ArrowUpRight className="h-3.5 w-3.5" /></a> : null)}
          </div>
        </div>
      )}
    </article>
  );
}

export default function MatthewProgressPage() {
  const [data, setData] = useState<MatthewProgress | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [chapter, setChapter] = useState(16);

  async function load() {
    setLoading(true);
    setError("");
    try {
      setData(await fetchProgress());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "無法讀取文章進度");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    fetchProgress()
      .then((payload) => {
        if (active) setData(payload);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "無法讀取文章進度");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const chapterData = data?.chapters.find((item) => item.chapter === chapter);
  const shownArticles = useMemo(() => data?.articles.filter((item) => item.passage.start.chapter <= chapter && item.passage.end.chapter >= chapter) ?? [], [chapter, data]);

  if (!data && loading) return <div className="flex min-h-[55vh] items-center justify-center gap-3 text-slate-600"><Loader2 className="h-5 w-5 animate-spin" />正在從權威資料源計算進度…</div>;
  if (!data) return <div className="rounded-2xl border border-rose-200 bg-rose-50 p-6 text-rose-800"><p className="font-black">進度資料載入失敗</p><p className="mt-2">{error}</p><button onClick={() => void load()} className="mt-4 rounded-lg bg-rose-700 px-4 py-2 font-bold text-white">重試</button></div>;

  const summary = data.summary;
  return (
    <main className="pb-10">
      <header className="flex flex-wrap items-end justify-between gap-5">
        <div>
          <p className="text-sm font-black tracking-wide text-indigo-700">MATTHEW EXPOSITION</p>
          <h1 className="mt-1 text-3xl font-black tracking-tight text-slate-950 sm:text-4xl">馬太福音文章進度</h1>
          <p className="mt-3 max-w-3xl leading-7 text-slate-600">由 CompositionPlan、審核 artifact、Wang repository 與 production 探測結果即時計算；此頁不修改文章或出版狀態。</p>
        </div>
        <button onClick={() => void load()} disabled={loading} className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-black text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-60">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />重新計算
        </button>
      </header>

      <section className="mt-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="已規劃單元" value={`${summary.planned_article_count}`} detail={`${summary.planned_verse_count} / ${summary.total_verse_count} 節進入文章計畫`} tone="slate" />
        <Metric label="已生成稿件" value={`${summary.generated_article_count}`} detail={`${percent(summary.generated_verse_count, summary.planned_verse_count)}% 已規劃經文有稿件`} tone="indigo" />
        <Metric label="Repository 已發布" value={`${summary.repository_published_count}`} detail={`${summary.repository_verse_count} 節通過 repository gates`} tone="emerald" />
        <Metric label="Production 可見" value={summary.production_visible_count === null ? "未知" : `${summary.production_visible_count}`} detail={summary.production_visible_count === null ? "尚未設定或無法完成 production 探測" : `${summary.production_verse_count} 節已由 production API 回傳`} tone="amber" />
      </section>

      {(data.runtime.deployment_state !== "current" || data.warnings.length > 0) && (
        <section className="mt-5 grid gap-3 lg:grid-cols-2">
          {data.runtime.deployment_state !== "current" && (
            <div className="flex gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-amber-950">
              <Radio className="mt-0.5 h-5 w-5 shrink-0" />
              <div><p className="font-black">Production 狀態未獲確認</p><p className="mt-1 text-sm leading-6">Workspace 能讀到 repository，不代表 production backend 已部署相同程式或能識別新版出版決定 schema。</p></div>
            </div>
          )}
          {data.warnings.map((warning) => (
            <div key={warning.code} className="flex gap-3 rounded-2xl border border-slate-200 bg-white p-4 text-slate-700">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
              <div><p className="font-black text-slate-950">資料來源提醒</p><p className="mt-1 text-sm leading-6">{warning.message || warning.code}</p></div>
            </div>
          ))}
        </section>
      )}

      <section className="mt-8 rounded-3xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div><p className="text-sm font-black text-indigo-700">全書覆蓋</p><h2 className="mt-1 text-2xl font-black text-slate-950">28 章工作地圖</h2></div>
          <div className="flex flex-wrap gap-4 text-xs font-semibold text-slate-600"><span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-indigo-500" />已規劃</span><span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />已生成</span><span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-slate-200" />未規劃</span></div>
        </div>
        <div className="mt-5 grid grid-cols-4 gap-2 sm:grid-cols-7 lg:grid-cols-14">
          {data.chapters.map((item) => {
            const planned = percent(item.planned_verse_count, item.verse_count);
            const generated = percent(item.generated_verse_count, item.verse_count);
            return (
              <button key={item.chapter} onClick={() => setChapter(item.chapter)} className={`relative overflow-hidden rounded-xl border px-2 py-3 text-left transition ${chapter === item.chapter ? "border-indigo-500 ring-2 ring-indigo-100" : "border-slate-200 hover:border-slate-400"}`}>
                <span className="relative z-10 text-sm font-black text-slate-900">{item.chapter}</span>
                <span className="relative z-10 mt-3 block text-[10px] font-bold text-slate-500">{item.planned_verse_count}/{item.verse_count}</span>
                <span className="absolute inset-x-0 bottom-0 h-1 bg-slate-100"><span className="block h-full bg-indigo-300" style={{ width: `${planned}%` }} /><span className="absolute inset-y-0 left-0 bg-emerald-500" style={{ width: `${generated}%` }} /></span>
              </button>
            );
          })}
        </div>

        <div className="mt-5 grid gap-3 border-t border-slate-100 pt-5 sm:grid-cols-4">
          <div><p className="text-xs font-bold text-slate-400">目前章</p><p className="mt-1 text-xl font-black">馬太福音 {chapter}</p></div>
          <div><p className="text-xs font-bold text-slate-400">已規劃</p><p className="mt-1 text-xl font-black">{chapterData?.planned_verse_count ?? 0} / {chapterData?.verse_count ?? 0} 節</p></div>
          <div><p className="text-xs font-bold text-slate-400">已生成</p><p className="mt-1 text-xl font-black">{chapterData?.generated_verse_count ?? 0} 節</p></div>
          <div><p className="text-xs font-bold text-slate-400">覆蓋缺口</p><p className="mt-1 text-xl font-black text-amber-700">{chapterData?.coverage_gap_count ?? 0} 節</p></div>
        </div>
      </section>

      <section className="mt-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div><p className="text-sm font-black text-indigo-700">文章單元</p><h2 className="mt-1 text-2xl font-black text-slate-950">第 {chapter} 章工作流</h2></div>
          <p className="text-sm text-slate-500">{shownArticles.length} 個單元 · {shownArticles.filter((item) => item.passage.cross_chapter).length} 個跨章</p>
        </div>
        {shownArticles.length ? <div className="mt-4 space-y-4">{shownArticles.map((article) => <ArticleCard key={`${article.article_unit_id}-${article.draft_id ?? "planned"}`} article={article} />)}</div> : (
          <div className="mt-4 rounded-2xl border border-dashed border-slate-300 bg-white py-14 text-center"><CircleDashed className="mx-auto h-8 w-8 text-slate-300" /><p className="mt-3 font-black text-slate-700">本章尚無已規劃文章單元</p><p className="mt-1 text-sm text-slate-500">這是明確的覆蓋缺口，不代表文章生成失敗。</p></div>
        )}
      </section>

      <footer className="mt-8 flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-slate-950 p-5 text-sm text-slate-300">
        <span>Schema: <code className="text-white">{data.schema_version}</code> · 計算於 {formatTime(data.generated_at)}</span>
        <Link href="/admin/wang/source-coverage" className="inline-flex items-center gap-1 font-black text-indigo-300">查看來源覆蓋 <ArrowUpRight className="h-4 w-4" /></Link>
      </footer>
    </main>
  );
}
