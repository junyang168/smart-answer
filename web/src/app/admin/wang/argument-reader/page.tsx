"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";

type Anchor = { id: string; quote: string; paragraph: string | null; start: number | null; end: number | null; paragraph_key: string };
type Node = { id: string; kind: string; statement: string; reason: string; question: string; scriptures: string[]; anchors: Anchor[] };
type Edge = { id: string; from: string; to: string; type: string; reason: string };
type Group = { id: string; title: string; claims: string[]; steps: string[]; relations: Edge[]; related_claims: string[] };
type View = { title: string; source_id: string; change_set: string; source_sha: string; media_url: string; groups: Group[]; nodes: Record<string, Node> };
const API = "/api/admin/argument-layer/instance/lectures";
const labels: Record<string, string> = { supports: "支持", qualifies: "限定", refutes: "反駁", answers: "回答", contextualizes: "背景", illustrates: "例證", applies: "應用" };
function time(value: number | null) {
  return value === null ? "時間未記錄" : `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
}
async function read<T,>(url: string): Promise<T> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`載入失敗（${response.status}）`);
  return response.json();
}
export default function Page() {
  return <Suspense fallback={<p>載入論證…</p>}><Reader /></Suspense>;
}
function Reader() {
  const params = useSearchParams();
  const router = useRouter();
  const [lectures, setLectures] = useState<{ id: string; title: string }[]>([]);
  const [view, setView] = useState<View | null>(null);
  const [selected, setSelected] = useState("");
  const [activeClaim, setActiveClaim] = useState("");
  const [error, setError] = useState("");
  const [clip, setClip] = useState<Anchor | null>(null);
  const [playing, setPlaying] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  const detail = useRef<HTMLElement>(null);
  const source = params.get("source") || "SRC-nysc_2011_2_1-e855641bcead";
  const requestedGroup = params.get("group") || "G002";
  const group = view?.groups.find(g => g.id === requestedGroup) || view?.groups[0];
  const node = view?.nodes[selected];
  useEffect(() => {
    read<{ lectures: typeof lectures }>(API).then(r => setLectures(r.lectures)).catch(e => setError(String(e)));
  }, []);
  useEffect(() => {
    let active = true;
    setView(null); setSelected(""); setClip(null); setError("");
    read<View>(`${API}/${encodeURIComponent(source)}`).then(v => { if (active) setView(v); })
      .catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [source]);
  useEffect(() => {
    setSelected(group?.claims[0] || group?.steps[0] || ""); setActiveClaim(group?.claims[0] || ""); setClip(null);
    audio.current?.pause();
  }, [group?.id, source]);
  function choose(id: string) {
    setSelected(id); if (view?.nodes[id]?.kind === "論點") setActiveClaim(id); setClip(null); audio.current?.pause();
    if (window.matchMedia("(max-width: 1279px)").matches) {
      detail.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }
  function navigate(nextSource: string, nextGroup: string) {
    router.replace(`?source=${encodeURIComponent(nextSource)}&group=${encodeURIComponent(nextGroup)}`);
  }
  async function play(anchor: Anchor) {
    setClip(anchor);
    const player = audio.current;
    if (!player || anchor.start === null) return;
    try { player.currentTime = anchor.start; await player.play(); }
    catch { setError("錄音暫時無法播放，請再按一次。"); }
  }
  const incoming = group?.relations.filter(e => e.to === selected) || [];
  const outgoing = group?.relations.filter(e => e.from === selected) || [];
  const path = new Set([activeClaim]);
  if (group) {
    let previous = -1;
    while (previous !== path.size) {
      previous = path.size;
      for (const edge of group.relations) {
        if (path.has(edge.to) && ["supports", "qualifies"].includes(edge.type)) path.add(edge.from);
        if (path.has(edge.from) && ["answers", "refutes"].includes(edge.type)) path.add(edge.to);
      }
    }
  }
  const steps = group?.steps.filter(id => showAll || !activeClaim || path.has(id)) || [];
  function target(id: string) {
    return <button onClick={() => choose(id)} className="text-left text-teal-800 underline decoration-teal-300 underline-offset-4 hover:text-teal-600">{view?.nodes[id]?.statement || id}</button>;
  }
  function relation(edge: Edge, direction: "incoming" | "outgoing") {
    return <div key={edge.id} className="border-l-2 border-teal-200 py-1 pl-3 text-sm leading-7">
      <p className="mb-1 text-xs text-slate-500">{direction === "incoming" ? "由以下內容" : ""}{labels[edge.type] || edge.type}</p>
      {target(direction === "incoming" ? edge.from : edge.to)}
      {edge.reason && <p className="mt-1 text-slate-600">{edge.reason}</p>}
    </div>;
  }
  return <main className="text-slate-800">
    <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
      <div><p className="mb-1 text-xs text-teal-700">#787 · 新版論證層</p><h1 className="text-xl font-semibold">沿著教授的論證讀</h1></div>
      <select aria-label="選擇講道" value={source} onChange={e => navigate(e.target.value, "G001")} className="max-w-full rounded-lg border border-slate-300 bg-white p-2 text-sm">
        {lectures.map(l => <option value={l.id} key={l.id}>{l.title}</option>)}
      </select>
    </div>
    {error && <p role="alert" className="mb-4 rounded-lg bg-rose-50 p-3 text-rose-800">{error}</p>}
    {!view && !error && <p className="py-12 text-center text-slate-500">讀取新庫論證與原文…</p>}
    {view && group && <>
      <nav aria-label="論證分組" className="mb-6 grid gap-2 sm:grid-cols-2">
        {view.groups.map((g, i) => <button key={g.id} onClick={() => navigate(source, g.id)}
          aria-current={g.id === group.id ? "page" : undefined}
          className={`rounded-lg border p-3 text-left text-sm leading-6 ${g.id === group.id ? "border-teal-700 bg-teal-50 text-teal-900" : "border-slate-200 bg-white hover:border-teal-400"}`}>
          <span className="mr-2 text-slate-400">{i + 1}.</span>{g.title}
        </button>)}
      </nav>
      <h2 className="mb-5 max-w-4xl text-2xl font-semibold leading-relaxed">{group.title}</h2>
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(340px,0.8fr)]">
        <section aria-label="完整論點和論證步驟" className="min-w-0 space-y-6">
          <div className="rounded-xl border border-teal-200 bg-teal-50/60 p-5">
            <h3 className="mb-3 font-semibold text-teal-900">本組完整論點</h3>
            <div className="space-y-2">{[...group.claims, ...group.related_claims].map(id => {
              const parents = group.relations.filter(e => e.from === id && view.nodes[e.to]?.kind === "論點");
              return <div key={id} className={`rounded-lg border bg-white p-3 ${selected === id ? "border-teal-600 ring-1 ring-teal-600" : "border-transparent"}`}>
                <button onClick={() => choose(id)} className="w-full text-left text-base leading-8">
                  <span className="mb-1 block text-xs text-teal-700">{id === group.claims[0] ? "主要結論 · 生成提綱" : "本組要點"}</span>
                  {view.nodes[id].question && <span className="mb-2 block text-sm font-medium text-slate-500">{view.nodes[id].question}</span>}
                  {view.nodes[id].statement}
                </button>
                {parents.map(e => <p key={e.id} className="mt-2 border-t pt-2 text-xs leading-6 text-slate-500">
                  {labels[e.type] || e.type} → {target(e.to)}
                </p>)}
              </div>;
            })}</div>
          </div>
          <div>
            <h3 className="mb-1 font-semibold">論據如何接起來</h3>
            <p className="mb-3 text-sm text-slate-500">依錄音順序排列。點選上方論點，看它的完整論證路徑。</p>
            <label className="mb-4 flex cursor-pointer items-center gap-2 text-sm text-slate-600">
              <input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} />顯示本組全部論據
            </label>
            <ol className="ml-3 space-y-4 border-l-2 border-slate-200 pl-5">
              {steps.map(id => {
                const n = view.nodes[id];
                const edges = group.relations.filter(e => e.from === id || (e.to === id && ["refutes", "answers"].includes(e.type)));
                return <li key={id} className={`relative rounded-xl border bg-white p-4 ${selected === id ? "border-teal-600 ring-1 ring-teal-600" : "border-slate-200"}`}>
                  <span className="absolute -left-[28px] top-6 h-3 w-3 rounded-full border-2 border-white bg-teal-600" />
                  <button onClick={() => choose(id)} className="w-full text-left">
                    <span className="mb-2 block text-xs text-slate-500">{n.kind} · {time(n.anchors[0]?.start ?? null)}</span>
                    <span className="block text-base leading-8">{n.statement}</span>
                    {n.reason && <span className="mt-3 block rounded-md bg-teal-50 p-3 text-sm leading-7 text-teal-900">{n.reason}</span>}
                    {!!n.scriptures.length && <span className="mt-2 block text-sm text-teal-800">{n.scriptures.join(" · ")}</span>}
                  </button>
                  <div className="mt-3 space-y-2 border-t border-slate-100 pt-3">
                    {edges.map(e => <p key={e.id} className="text-xs leading-6 text-slate-500">{e.to === id ? "由論點" : ""}{labels[e.type] || e.type} → {target(e.from === id ? e.to : e.from)}</p>)}
                    {!edges.length && <p className="text-xs text-amber-700">此記錄尚無連接</p>}
                  </div>
                </li>;
              })}
            </ol>
          </div>
        </section>
        <aside ref={detail} aria-label="教授原文與錄音" className="rounded-xl border border-slate-200 bg-white xl:sticky xl:top-4 xl:max-h-[90vh] xl:overflow-y-auto">
          <div className="border-b border-slate-100 p-5">
            <h3 className="font-semibold">教授原文與錄音</h3>
            <audio ref={audio} src={view.media_url} controls preload="metadata" className="mt-3 w-full"
              onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
              onTimeUpdate={() => { if (audio.current && clip?.end !== null && clip?.end !== undefined && audio.current.currentTime >= clip.end) audio.current.pause(); }} />
            {clip && <p className="mt-2 text-xs text-slate-500">{playing ? "正在播放" : "所選原文"} {time(clip.start)}–{time(clip.end)}</p>}
          </div>
          {node && <div className="space-y-5 p-5">
            <button onClick={() => document.querySelector('[aria-label="完整論點和論證步驟"]')?.scrollIntoView({ behavior: "smooth" })} className="text-sm text-teal-800 xl:hidden">↑ 返回論證步驟</button>
            <div><p className="mb-2 text-xs text-teal-700">{node.kind}</p><p className="text-lg font-medium leading-8">{node.statement}</p></div>
            {node.anchors.map(a => <div key={a.id} className="border-t pt-4">
              <div className="mb-3 flex items-center justify-between gap-2 text-xs text-slate-500">
                <span>{time(a.start)}–{time(a.end)}</span>
                {a.start !== null && <button onClick={() => void play(a)} className="rounded-full bg-teal-800 px-4 py-2 text-sm text-white hover:bg-teal-700">▶ 聽這一段</button>}
              </div>
              <blockquote className="whitespace-pre-wrap rounded-lg bg-amber-50 p-3 text-base leading-8">{a.quote}</blockquote>
              {a.paragraph && <details className="mt-3 text-sm leading-7">
                <summary className="cursor-pointer text-slate-500">展開所在段落</summary>
                <p className="mt-2 whitespace-pre-wrap">{a.paragraph.split(a.quote).map((part, i) => <span key={i}>{i > 0 && <mark className="bg-amber-200">{a.quote}</mark>}{part}</span>)}</p>
              </details>}
            </div>)}
            {!node.anchors.length && <p className="text-sm text-amber-700">這條記錄沒有直接表述的原文；可點上方依據查看教授的論證。</p>}
            {!!incoming.length && <div className="space-y-3"><h4 className="text-sm font-semibold">這個判斷的依據</h4>{incoming.map(e => relation(e, "incoming"))}</div>}
            {!!outgoing.length && <div className="space-y-3"><h4 className="text-sm font-semibold">接到下一步</h4>{outgoing.map(e => relation(e, "outgoing"))}</div>}
            <Link href="/admin/argument-review" className="block border-t pt-4 text-sm text-teal-800 underline">進入同工審閱</Link>
          </div>}
        </aside>
      </div>
      <details className="mt-8 text-xs text-slate-500"><summary className="cursor-pointer">資料版本</summary><p className="mt-2 break-all">{view.change_set}</p><p>原文 {view.source_sha}</p></details>
    </>}
  </main>;
}
