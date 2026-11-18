"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ChevronDown, ChevronRight, Play } from "lucide-react";

/**
 * 一講的論證，給教會同工讀、聽、核對。
 *
 * 設計與試題：docs/wang-knowledge-platform/10-extraction/lecture_argument_reader_design.md。
 * 由上而下照他講的次序：他的段落標題 → 這一段的論點 → 每點的理由 → 每條理由
 * 是他的哪幾句話（可播）。下面是整篇逐字稿，被理由用到的話有底色，沒用到的是
 * 灰色。畫面上不出現編號或系統用語。
 */

type Verse = { label: string; book: string; from: [number, number]; to: [number, number] };
type Quote = { text: string; start: number | null; paragraph: number | null; fragment: string };
type Reason = { kind: string; text: string; quotes: Quote[] };
type Point = {
  no: number; claim_id: string; statement: string; application: boolean; verses: Verse[];
  reasons: Reason[]; start: number | null; section: number; questions?: string[];
};
type Piece = { text: string; start: number | null; uses: [number, number][] };
type Paragraph = { paragraph: number; section: number; heading: string | null; pieces: Piece[] };
type Ruling = { verdict: "right" | "wrong" | "no_summary"; note: string; at: string };
type Candidate = { no: number; claim_id: string; why: string; covers: number[]; ruling: Ruling | null };
type Lecture = {
  id: string; title: string; media_url: string | null; duration: number | null;
  sections: string[]; points: Point[]; transcript: Paragraph[];
  core: { candidates: Candidate[]; note: string; no_summary: Ruling | null };
};

const VERDICT_LABEL: Record<Ruling["verdict"], string> = { right: "對", wrong: "不對", no_summary: "他沒有總結" };

function clock(seconds: number | null): string {
  if (seconds == null) return "";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const two = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${two(m)}:${two(r)}` : `${m}:${two(r)}`;
}

/** 「7:12」「太 7:12」「林前10:13」→ 書卷、章、節。沒寫書卷就是馬太福音。 */
function parseVerse(input: string): { book: string; chapter: number; verse: number } | null {
  const m = input.replace(/\s+/g, "").match(/^([一-鿿]*)(\d+)[:：](\d+)$/);
  if (!m) return null;
  return { book: m[1] || "太", chapter: Number(m[2]), verse: Number(m[3]) };
}

function covers(v: Verse, q: { book: string; chapter: number; verse: number }): boolean {
  if (v.book !== q.book) return false;
  const at = q.chapter * 1000 + q.verse;
  return v.from[0] * 1000 + v.from[1] <= at && at <= v.to[0] * 1000 + v.to[1];
}

export default function LectureRoute() {
  const params = useParams<{ id: string }>();
  const id = decodeURIComponent(params.id);
  const [lecture, setLecture] = useState<Lecture | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    fetch(`/api/admin/lecture-argument/lectures/${encodeURIComponent(id)}`, { cache: "no-store" })
      .then(async (r) => (r.ok ? r.json() : Promise.reject(new Error((await r.json().catch(() => ({}))).detail ?? `讀取失敗（${r.status}）`))))
      .then(setLecture)
      .catch((e) => setError(String(e.message ?? e)));
  }, [id]);
  useEffect(load, [load]);

  if (error) return <main className="mx-auto max-w-3xl px-4 py-6 text-rose-700">{error}</main>;
  if (!lecture) return <main className="mx-auto max-w-3xl px-4 py-6 text-slate-400">載入中…</main>;
  return <LectureView lecture={lecture} reload={load} />;
}

function LectureView({ lecture, reload }: { lecture: Lecture; reload: () => void }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [now, setNow] = useState(0);
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [verseInput, setVerseInput] = useState("");
  const [flash, setFlash] = useState<string | null>(null);

  const play = useCallback((seconds: number | null) => {
    const el = audio.current;
    if (!el || seconds == null) return;
    el.currentTime = Math.max(seconds - 1, 0);
    void el.play().catch(() => undefined);
  }, []);

  // 每一小段原話與它開始的秒數，播放時用來找出「現在在講哪一句」。
  const timeline = useMemo(() => {
    const rows: { key: string; start: number; uses: [number, number][] }[] = [];
    lecture.transcript.forEach((p) => p.pieces.forEach((piece, i) => {
      if (piece.start != null) rows.push({ key: `${p.paragraph}-${i}`, start: piece.start, uses: piece.uses });
    }));
    return rows.sort((a, b) => a.start - b.start);
  }, [lecture]);
  const current = useMemo(() => {
    let found: (typeof timeline)[number] | null = null;
    for (const row of timeline) {
      if (row.start <= now + 0.5) found = row; else break;
    }
    return found;
  }, [timeline, now]);
  const playing = now > 0 ? current : null;
  const currentPoint = playing?.uses[0] ? lecture.points[playing.uses[0][0] - 1] : null;

  const query = parseVerse(verseInput);
  const shown = (p: Point) => !query || p.verses.some((v) => covers(v, query));
  const matches = lecture.points.filter(shown);

  const goTo = (target: string) => {
    const el = document.getElementById(target);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    setFlash(target);
    window.setTimeout(() => setFlash(null), 2000);
  };
  const openPoint = (no: number) => {
    setVerseInput("");
    setOpen((s) => new Set(s).add(no));
    window.setTimeout(() => goTo(`point-${no}`), 50);
  };
  const toggle = (no: number) => setOpen((s) => {
    const next = new Set(s);
    if (next.has(no)) next.delete(no); else next.add(no);
    return next;
  });

  return (
    <main className="mx-auto max-w-3xl px-4 pb-24">
      <div className="sticky top-0 z-10 -mx-4 border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur">
        <Link href="/admin/wang/lectures" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />所有講次
        </Link>
        <h1 className="mt-1 text-lg font-bold text-slate-900">{lecture.title}</h1>
        {lecture.media_url ? (
          <audio ref={audio} src={lecture.media_url} controls preload="metadata" className="mt-2 w-full"
            onTimeUpdate={(e) => setNow(e.currentTarget.currentTime)} />
        ) : <p className="mt-2 text-sm text-slate-500">這一講沒有錄音。</p>}
        <p className="mt-1 min-h-5 text-sm text-slate-700" aria-live="polite">
          {playing && (currentPoint
            ? <>現在講：<button className="font-semibold text-indigo-700 underline" onClick={() => openPoint(currentPoint.no)}>第 {currentPoint.no} 點</button>　{currentPoint.statement.slice(0, 40)}{currentPoint.statement.length > 40 ? "…" : ""}</>
            : <span className="text-slate-500">現在這段話沒有整理進任何一點</span>)}
        </p>
      </div>

      <section className="mt-5">
        <label className="flex items-center gap-2 text-sm text-slate-700">
          找經文
          <input value={verseInput} onChange={(e) => setVerseInput(e.target.value)} placeholder="例如 7:12 或 林前10:13"
            className="w-48 rounded-lg border border-slate-300 px-3 py-1.5 text-base" />
          {verseInput && <button className="text-slate-500 underline" onClick={() => setVerseInput("")}>清除</button>}
        </label>
        {query && (
          <p className="mt-2 text-sm text-slate-600">
            {matches.length ? `談到 ${query.book} ${query.chapter}:${query.verse} 的有 ${matches.length} 點：` : `這一講沒有論點談到 ${query.book} ${query.chapter}:${query.verse}。`}
          </p>
        )}
        {verseInput && !query && <p className="mt-2 text-sm text-slate-500">請寫成「章:節」，例如 7:12。</p>}
      </section>

      {!query && <CoreSection lecture={lecture} reload={reload} openPoint={openPoint} />}

      <section className="mt-8">
        <h2 className="text-xl font-bold text-slate-900">他講了哪幾點</h2>
        <p className="mt-1 text-sm text-slate-500">
          大標題是他自己這一講的段落。每一點的句子是<Tag>整理</Tag>的話；點開看理由，引號裡是他的<Tag tone="quote">原話</Tag>，按 ▶ 從那一句開始聽。
        </p>
        {[-1, ...lecture.sections.map((_, i) => i)].map((s) => {
          const points = lecture.points.filter((p) => p.section === s && shown(p));
          if (!points.length) return null;
          return (
            <div key={s} className="mt-6">
              {s >= 0 && <h3 className="text-lg font-bold text-slate-800">{lecture.sections[s]}</h3>}
              <ol className="mt-2 space-y-2">
                {points.map((p) => (
                  <PointCard key={p.no} point={p} open={open.has(p.no) || !!query} toggle={() => toggle(p.no)} play={play}
                    goTo={goTo} active={currentPoint?.no === p.no} flash={flash === `point-${p.no}`} />
                ))}
              </ol>
            </div>
          );
        })}
      </section>

      {!query && (
        <section className="mt-10">
          <h2 className="text-xl font-bold text-slate-900">逐字稿（他的原話）</h2>
          <p className="mt-1 text-sm leading-6 text-slate-600">
            <span className="rounded bg-amber-100 px-1">有底色</span>的話整理進了某一點，旁邊寫著是第幾點的第幾條理由，點一下回到那一點。
            <span className="text-slate-400">灰色</span>的話沒有整理進任何一點。點任何一句，從那裡開始聽。
          </p>
          <div className="mt-4 space-y-4 text-[17px] leading-8">
            {lecture.transcript.map((p) => (
              <div key={p.paragraph}>
                {p.heading && <h3 className="mb-1 mt-6 text-lg font-bold text-slate-800">{p.heading}</h3>}
                <p>
                  {p.pieces.map((piece, i) => {
                    const key = `${p.paragraph}-${i}`;
                    const live = playing?.key === key;
                    const used = piece.uses.length > 0;
                    return (
                      <span key={key} id={`q-${key}`}>
                        <span role="button" tabIndex={0} onClick={() => play(piece.start)}
                          onKeyDown={(e) => { if (e.key === "Enter") play(piece.start); }}
                          className={`cursor-pointer rounded ${used ? "bg-amber-100 text-slate-900" : "text-slate-400"} ${live ? "outline outline-2 outline-indigo-500" : ""} ${flash === `q-${key}` ? "bg-indigo-200" : ""}`}>
                          {piece.text}
                        </span>
                        {used && piece.uses.map(([no, r]) => (
                          <button key={`${no}-${r}`} onClick={() => openPoint(no)}
                            className="mx-0.5 align-super text-xs font-semibold text-indigo-700 hover:underline">
                            第{no}點·理由{r}
                          </button>
                        ))}
                      </span>
                    );
                  })}
                </p>
              </div>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}

function Tag({ children, tone = "edit" }: { children: React.ReactNode; tone?: "edit" | "quote" }) {
  return (
    <span className={`mx-0.5 inline-block rounded px-1.5 py-px align-middle text-xs font-semibold ${tone === "edit" ? "bg-slate-200 text-slate-700" : "bg-amber-100 text-amber-900"}`}>
      {children}
    </span>
  );
}

function PointCard({ point, open, toggle, play, goTo, active, flash }: {
  point: Point; open: boolean; toggle: () => void; play: (s: number | null) => void;
  goTo: (id: string) => void; active: boolean; flash: boolean;
}) {
  return (
    <li id={`point-${point.no}`} className={`rounded-xl border bg-white transition ${active ? "border-indigo-400 shadow" : "border-slate-200"} ${flash ? "ring-2 ring-indigo-300" : ""}`}>
      <div className="flex items-start gap-2 px-3 py-3">
        <button onClick={toggle} className="mt-0.5 text-slate-500" aria-label={open ? "收起" : "展開"}>
          {open ? <ChevronDown className="h-5 w-5" /> : <ChevronRight className="h-5 w-5" />}
        </button>
        <div className="min-w-0 flex-1">
          <button onClick={toggle} className="text-left">
            <span className="font-bold text-slate-900">第 {point.no} 點</span>
            <Tag>整理</Tag>
            {point.application && <Tag>應用</Tag>}
            <span className="ml-1 text-slate-900">{point.statement}</span>
          </button>
          {point.verses.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {point.verses.map((v) => <span key={v.label} className="rounded-full bg-sky-50 px-2 py-0.5 text-xs text-sky-800">{v.label}</span>)}
            </div>
          )}
        </div>
        <button onClick={() => play(point.start)} className="shrink-0 rounded-full p-1.5 text-indigo-700 hover:bg-indigo-50" aria-label="從這一點開始聽">
          <Play className="h-5 w-5" />
        </button>
      </div>
      {open && (
        <ol className="space-y-3 border-t border-slate-100 px-4 py-3">
          {point.questions?.map((q) => <li key={q} className="text-sm text-slate-700">回答的問題：{q}</li>)}
          {point.reasons.map((r, i) => (
            <li key={i}>
              <div className="text-sm">
                <span className="font-semibold text-slate-700">{r.kind === "理由" ? `理由 ${i + 1}` : `${r.kind}（理由 ${i + 1}）`}</span>
                {r.text && <><Tag>整理</Tag><span className="text-slate-800">{r.text}</span></>}
              </div>
              <ul className="mt-1 space-y-1">
                {r.quotes.map((q) => (
                  <li key={q.fragment} className="flex items-start gap-2">
                    <button onClick={() => play(q.start)} className="mt-1 shrink-0 text-indigo-700" aria-label="聽這一句">
                      <Play className="h-4 w-4" />
                    </button>
                    <blockquote className="flex-1 border-l-4 border-amber-300 bg-amber-50 px-3 py-1 text-[15px] leading-7 text-slate-900">
                      「{q.text}」
                      <span className="ml-2 whitespace-nowrap text-xs text-slate-500">{clock(q.start)}</span>
                      {q.paragraph != null && (
                        <button onClick={() => goToQuote(q, goTo)} className="ml-2 whitespace-nowrap text-xs text-indigo-700 hover:underline">在逐字稿中看</button>
                      )}
                    </blockquote>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      )}
    </li>
  );
}

/** 原話在逐字稿裡的那一小段：同一段落裡、文字包含這句話的第一小段。 */
function goToQuote(q: Quote, goTo: (id: string) => void) {
  const spans = Array.from(document.querySelectorAll<HTMLElement>(`[id^="q-${q.paragraph}-"]`));
  const head = q.text.slice(0, 12);
  const hit = spans.find((el) => el.textContent?.includes(head)) ?? spans[0];
  if (hit) goTo(hit.id);
}

function CoreSection({ lecture, reload, openPoint }: { lecture: Lecture; reload: () => void; openPoint: (no: number) => void }) {
  const { candidates, note, no_summary } = lecture.core;
  return (
    <section className="mt-6 rounded-xl border-2 border-indigo-200 bg-indigo-50/50 p-4">
      <h2 className="text-xl font-bold text-slate-900">核心論點（請確認）</h2>
      {!candidates.length && <p className="mt-2 text-sm text-slate-600">這一講還沒有選出核心論點的候選。</p>}
      {note && <p className="mt-2 text-sm leading-6 text-slate-700"><Tag>整理</Tag>{note}</p>}
      <div className="mt-3 space-y-4">
        {candidates.map((c) => {
          const point = lecture.points[c.no - 1];
          return (
            <div key={c.claim_id} className="rounded-lg bg-white p-3">
              <button onClick={() => openPoint(c.no)} className="text-left">
                <span className="font-bold text-indigo-800 underline">第 {c.no} 點</span>
                <span className="ml-1 text-slate-900">{point.statement}</span>
              </button>
              <p className="mt-1 text-sm text-slate-600"><span className="font-semibold">為什麼選它：</span>{c.why}</p>
              {c.covers.length > 0 && (
                <p className="mt-1 text-sm text-slate-600">
                  <span className="font-semibold">涵蓋：</span>
                  {c.covers.map((n) => (
                    <button key={n} onClick={() => openPoint(n)} className="mr-2 text-indigo-700 underline">第 {n} 點</button>
                  ))}
                </p>
              )}
              <RulingForm lectureId={lecture.id} claimId={c.claim_id} ruling={c.ruling} choices={["right", "wrong"]} reload={reload} />
            </div>
          );
        })}
        <div className="rounded-lg bg-white p-3">
          <p className="text-sm text-slate-700">如果他這一講根本沒有一個總結全講的論點：</p>
          <RulingForm lectureId={lecture.id} claimId={null} ruling={no_summary} choices={["no_summary"]} reload={reload} />
        </div>
      </div>
    </section>
  );
}

function RulingForm({ lectureId, claimId, ruling, choices, reload }: {
  lectureId: string; claimId: string | null; ruling: Ruling | null; choices: Ruling["verdict"][]; reload: () => void;
}) {
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const save = async (verdict: Ruling["verdict"]) => {
    setSaving(true);
    setError("");
    const response = await fetch(`/api/admin/lecture-argument/lectures/${encodeURIComponent(lectureId)}/core-rulings`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ verdict, claim_id: claimId, note }),
    });
    setSaving(false);
    if (!response.ok) { setError("沒有存成功，請再試一次。"); return; }
    setNote("");
    reload();
  };
  return (
    <div className="mt-2">
      {ruling && (
        <p className="mb-1 text-sm text-emerald-800">
          已確認：<b>{VERDICT_LABEL[ruling.verdict]}</b>{ruling.note && `——${ruling.note}`}
          <span className="ml-2 text-xs text-slate-500">{new Date(ruling.at).toLocaleString("zh-TW")}</span>
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {choices.map((v) => (
          <button key={v} disabled={saving} onClick={() => save(v)}
            className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${v === "right" ? "bg-emerald-600 text-white" : v === "wrong" ? "bg-rose-600 text-white" : "bg-slate-600 text-white"} disabled:opacity-50`}>
            {VERDICT_LABEL[v]}
          </button>
        ))}
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="可以加一句說明（不必填）"
          className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-base" />
      </div>
      {error && <p className="mt-1 text-sm text-rose-700">{error}</p>}
    </div>
  );
}
