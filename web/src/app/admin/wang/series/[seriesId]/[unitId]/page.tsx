"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ChevronDown, ChevronRight, Play, AlertTriangle, CheckCircle2 } from "lucide-react";

type Quote = { text: string; source_id: string; lecture_label: string; paragraph: string; start: number | null };
type FidelityWarn = { id: string; kind: string; verdict: string; detail: string };
type Incoming = {
  kind_label: string;
  relation: string;
  from_point_no: number | null;
  statement: string;
  reason: string;
  quotes: Quote[];
  fidelity_warning: FidelityWarn | null;
};
type Outgoing = {
  relation_label: string;
  to_point_no: number | null;
  to_question_no: number | null;
  to_statement: string;
  reason: string;
};
type Point = {
  no: number;
  statement: string;
  source_id: string;
  lecture_label: string;
  start: number | null;
  quotes: Quote[];
  incoming: Incoming[];
  outgoing: Outgoing[];
  fidelity_warning: FidelityWarn | null;
};
type QuestionItem = {
  no: number;
  statement: string;
  source_id: string;
  lecture_label: string;
  start: number | null;
  state: string;
  remaining: string;
  answers: { point_no: number | null; statement: string; reason: string; lecture_label: string }[];
};
type MediaInfo = { source_id: string; label: string; title: string; media_url: string | null; duration: number | null };
type TranscriptLecture = {
  source_id: string;
  lecture_label: string;
  title: string;
  paragraphs: { paragraph: string; pieces: { text: string; start: number | null; uses: number[] }[] }[];
};
type UnitDetail = {
  series_id: string;
  series_title: string;
  unit_id: string;
  title: string;
  passage: string;
  chars: number;
  cross_lecture: boolean;
  media: Record<string, MediaInfo>;
  questions: QuestionItem[];
  points: Point[];
  transcript: TranscriptLecture[];
  fidelity: { total: number; faithful: number; changed: number; added: number; findings: FidelityWarn[] } | null;
  roundtrip: {
    metrics: { core_units_covered: number; core_units_total: number; distortion_count: number; omission_count: number; overall_assessment: string };
    omissions: { severity: string; explanation: string; original_excerpt: string }[];
    distortions: { severity: string; explanation: string }[];
  } | null;
  ruling: { verdict: "approved" | "needs_revision"; note: string; at: string } | null;
};

function clock(seconds: number | null): string {
  if (seconds == null) return "";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const two = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${two(m)}:${two(r)}` : `${m}:${two(r)}`;
}

export default function UnitReviewPage() {
  const params = useParams<{ seriesId: string; unitId: string }>();
  const seriesId = decodeURIComponent(params.seriesId);
  const unitId = decodeURIComponent(params.unitId);
  const [unit, setUnit] = useState<UnitDetail | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    fetch(`/api/admin/lecture-argument/series/${encodeURIComponent(seriesId)}/units/${encodeURIComponent(unitId)}`, { cache: "no-store" })
      .then(async (r) => (r.ok ? r.json() : Promise.reject(new Error((await r.json().catch(() => ({}))).detail ?? `讀取失敗（${r.status}）`))))
      .then(setUnit)
      .catch((e) => setError(String(e.message ?? e)));
  }, [seriesId, unitId]);
  useEffect(load, [load]);

  if (error) return <main className="mx-auto max-w-3xl px-4 py-6 text-rose-700">{error}</main>;
  if (!unit) return <main className="mx-auto max-w-3xl px-4 py-6 text-slate-400">載入中…</main>;
  return <UnitReviewView unit={unit} reload={load} />;
}

function UnitReviewView({ unit, reload }: { unit: UnitDetail; reload: () => void }) {
  const mediaList = Object.values(unit.media);
  const [activeSourceId, setActiveSourceId] = useState<string>(mediaList[0]?.source_id ?? "");
  const audioRef = useRef<HTMLAudioElement>(null);
  const pendingSeek = useRef<number | null>(null);
  const [open, setOpen] = useState<Set<number>>(new Set([1]));
  const [flash, setFlash] = useState<string | null>(null);
  const [leftOutOnly, setLeftOutOnly] = useState(false);
  const [showChecks, setShowChecks] = useState(false);
  const [note, setNote] = useState(unit.ruling?.note ?? "");
  const [saving, setSaving] = useState(false);

  const activeMedia = unit.media[activeSourceId] ?? mediaList[0];

  const playAt = useCallback((sourceId: string, seconds: number | null) => {
    if (seconds == null) return;
    const target = Math.max(seconds - 1, 0);
    if (sourceId !== activeSourceId) {
      pendingSeek.current = target;
      setActiveSourceId(sourceId);
      return;
    }
    const el = audioRef.current;
    if (!el) return;
    el.currentTime = target;
    void el.play().catch(() => undefined);
  }, [activeSourceId]);

  useEffect(() => {
    const el = audioRef.current;
    if (!el || pendingSeek.current == null) return;
    const target = pendingSeek.current;
    const onReady = () => {
      el.currentTime = target;
      pendingSeek.current = null;
      void el.play().catch(() => undefined);
    };
    el.addEventListener("loadedmetadata", onReady, { once: true });
    el.load();
    return () => el.removeEventListener("loadedmetadata", onReady);
  }, [activeSourceId]);

  const goToPoint = (no: number) => {
    setOpen((s) => new Set(s).add(no));
    window.setTimeout(() => {
      const id = `point-${no}`;
      document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "center" });
      setFlash(id);
      window.setTimeout(() => setFlash(null), 2000);
    }, 60);
  };

  const toggle = (no: number) => setOpen((s) => {
    const next = new Set(s);
    if (next.has(no)) next.delete(no); else next.add(no);
    return next;
  });

  const submitRuling = async (verdict: "approved" | "needs_revision") => {
    setSaving(true);
    try {
      const r = await fetch(
        `/api/admin/lecture-argument/series/${encodeURIComponent(unit.series_id)}/units/${encodeURIComponent(unit.unit_id)}/rulings`,
        { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ verdict, note }) }
      );
      if (r.ok) reload();
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="mx-auto max-w-3xl px-4 pb-28">
      {/* Sticky header + multi-lecture audio player */}
      <div className="sticky top-0 z-10 -mx-4 border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur">
        <div className="flex items-center justify-between gap-2 text-sm">
          <Link href={`/admin/wang/series/${encodeURIComponent(unit.series_id)}`} className="inline-flex items-center gap-1 text-slate-500 hover:text-slate-800">
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />{unit.series_title}
          </Link>
          <span className="text-xs text-slate-500">{unit.passage} · 約 {unit.chars.toLocaleString()} 字</span>
        </div>
        <h1 className="mt-1 text-lg font-bold text-slate-900">{unit.title}</h1>
        {mediaList.length > 1 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {mediaList.map((m) => (
              <button
                key={m.source_id}
                onClick={() => setActiveSourceId(m.source_id)}
                className={`rounded-md px-2.5 py-1 text-xs font-medium ${m.source_id === activeSourceId ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
              >
                {m.label}錄音
              </button>
            ))}
          </div>
        )}
        {activeMedia?.media_url ? (
          <audio ref={audioRef} src={activeMedia.media_url} controls preload="metadata" className="mt-2 w-full" />
        ) : (
          <p className="mt-2 text-xs text-slate-500">{activeMedia?.label ?? "本單元"}無可用錄音檔。</p>
        )}
      </div>

      {/* Machine dual-check disclosure */}
      {(unit.fidelity || unit.roundtrip) && (
        <section className="mt-5 rounded-xl border border-slate-200 bg-slate-50 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className="font-semibold text-slate-800">機器雙校驗參考</span>
              {unit.fidelity && (
                <span className={unit.fidelity.changed + unit.fidelity.added > 0 ? "text-amber-700 font-medium" : "text-emerald-700 font-medium"}>
                  逐點忠實度：{unit.fidelity.faithful}/{unit.fidelity.total}（改寫 {unit.fidelity.changed}、多加 {unit.fidelity.added}）
                </span>
              )}
              {unit.roundtrip && (
                <span className={unit.roundtrip.metrics.distortion_count > 0 ? "text-rose-700 font-medium" : "text-emerald-700 font-medium"}>
                  盲測重構：{unit.roundtrip.metrics.core_units_covered}/{unit.roundtrip.metrics.core_units_total} 核心覆蓋，{unit.roundtrip.metrics.distortion_count} 扭曲
                </span>
              )}
            </div>
            <button onClick={() => setShowChecks((v) => !v)} className="text-xs font-medium text-indigo-700 underline">
              {showChecks ? "收起詳情" : "展開細節提示"}
            </button>
          </div>
          {showChecks && (
            <div className="mt-3 space-y-2 border-t border-slate-200 pt-3 text-xs leading-5 text-slate-700">
              {unit.fidelity?.findings.map((f, i) => (
                <p key={i} className="rounded bg-amber-50 p-2 text-amber-900">
                  <b>忠實度提醒（{f.verdict === "added" ? "多加內容" : "改寫判斷"}）</b>：{f.detail}
                </p>
              ))}
              {unit.roundtrip?.metrics.overall_assessment && (
                <p className="text-slate-600">{unit.roundtrip.metrics.overall_assessment}</p>
              )}
              {unit.roundtrip?.omissions.map((o, i) => (
                <p key={i} className="text-slate-600">• 細節遺漏（{o.severity}）：{o.explanation}</p>
              ))}
            </div>
          )}
        </section>
      )}

      {/* Questions -> Answers overview */}
      {unit.questions.length > 0 && (
        <section className="mt-6 rounded-xl border border-indigo-200 bg-indigo-50/40 p-4">
          <h2 className="text-base font-bold text-slate-900">本單元提出與回答的問題</h2>
          <p className="mt-1 text-xs text-slate-600">從教授留下的問題直接跳到他後來作答的論點，核對跨講呼應是否接上。</p>
          <ul className="mt-3 space-y-3">
            {unit.questions.map((q) => (
              <li key={q.no} className="rounded-lg border border-indigo-100 bg-white p-3">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">問題 {q.no}：{q.statement}</p>
                  {q.start != null && (
                    <button onClick={() => playAt(q.source_id, q.start)} className="shrink-0 inline-flex items-center gap-1 rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700 hover:bg-indigo-100">
                      <Play className="h-3 w-3" />{q.lecture_label} {clock(q.start)}
                    </button>
                  )}
                </div>
                {q.answers.length > 0 ? (
                  <ul className="mt-2 space-y-1 text-xs text-slate-700">
                    {q.answers.map((a, i) => (
                      <li key={i}>
                        → {a.lecture_label}作答：
                        {a.point_no ? (
                          <button onClick={() => goToPoint(a.point_no!)} className="font-medium text-indigo-700 underline">
                            第 {a.point_no} 點
                          </button>
                        ) : null}{" "}
                        {a.statement}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-xs text-amber-700">狀態：{q.state} {q.remaining}</p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Points list */}
      <section className="mt-8">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-bold text-slate-900">本單元的論點與推論（{unit.points.length} 點）</h2>
          <div className="flex gap-2 text-xs">
            <button onClick={() => setOpen(new Set(unit.points.map((p) => p.no)))} className="text-indigo-700 underline">全部展開</button>
            <button onClick={() => setOpen(new Set())} className="text-slate-500 underline">全部收起</button>
          </div>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          點開每一點可看支持它的依據、例證、他反對的看法，以及它如何匯入更上層的總結論；按 ▶ 直接切換對應講次錄音並跳轉原話。
        </p>

        <div className="mt-4 space-y-3">
          {unit.points.map((p) => {
            const isOpen = open.has(p.no);
            const isFlash = flash === `point-${p.no}`;
            return (
              <div
                key={p.no}
                id={`point-${p.no}`}
                className={`rounded-xl border bg-white transition ${isFlash ? "border-indigo-500 ring-2 ring-indigo-200" : "border-slate-200"}`}
              >
                <button
                  onClick={() => toggle(p.no)}
                  className="flex w-full items-start gap-2 px-4 py-3 text-left hover:bg-slate-50"
                >
                  {isOpen ? <ChevronDown className="mt-1 h-4 w-4 shrink-0 text-slate-400" /> : <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-slate-400" />}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs font-bold text-indigo-700">第 {p.no} 點</span>
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">{p.lecture_label}</span>
                      {p.fidelity_warning && (
                        <span className="inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-900">
                          <AlertTriangle className="h-3 w-3" />忠實度待核對
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-sm font-medium leading-6 text-slate-900">{p.statement}</p>
                  </div>
                </button>

                {isOpen && (
                  <div className="space-y-4 border-t border-slate-100 px-4 py-4 text-sm">
                    {p.fidelity_warning && (
                      <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-900">
                        <b>機器忠實度提醒（{p.fidelity_warning.verdict === "added" ? "疑似多加" : "疑似改寫"}）</b>：{p.fidelity_warning.detail}
                      </div>
                    )}

                    {/* Direct quotes of this claim */}
                    {p.quotes.length > 0 && (
                      <div>
                        <p className="text-xs font-semibold text-slate-500">他的原話</p>
                        <div className="mt-1 space-y-1.5">
                          {p.quotes.map((q, i) => (
                            <div key={i} className="rounded-lg bg-slate-50 px-3 py-2 text-xs leading-6 text-slate-700">
                              <button
                                onClick={() => playAt(q.source_id, q.start)}
                                className="mr-2 inline-flex items-center gap-1 rounded bg-white px-1.5 py-0.5 font-medium text-indigo-700 shadow-xs ring-1 ring-slate-200 hover:bg-indigo-50"
                              >
                                <Play className="h-3 w-3" />{q.lecture_label} {clock(q.start)}
                              </button>
                              「{q.text}」
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Incoming reasons / examples / opposed positions */}
                    {p.incoming.length > 0 && (
                      <div className="space-y-2.5">
                        {p.incoming.map((inc, idx) => (
                          <div key={idx} className={`rounded-lg border p-3 ${inc.kind_label === "他反對的看法" ? "border-rose-200 bg-rose-50/40" : "border-slate-200 bg-white"}`}>
                            <div className="flex flex-wrap items-center gap-2 text-xs">
                              <span className={`font-bold ${inc.kind_label === "他反對的看法" ? "text-rose-700" : "text-indigo-700"}`}>
                                {inc.kind_label}
                              </span>
                              {inc.from_point_no && (
                                <button onClick={() => goToPoint(inc.from_point_no!)} className="text-indigo-600 underline">
                                  見第 {inc.from_point_no} 點
                                </button>
                              )}
                            </div>
                            <p className="mt-1 text-sm text-slate-900">{inc.statement}</p>
                            {inc.reason && <p className="mt-1 text-xs leading-5 text-slate-600">推論連接：{inc.reason}</p>}
                            {inc.quotes.length > 0 && (
                              <div className="mt-2 space-y-1">
                                {inc.quotes.map((q, qi) => (
                                  <div key={qi} className="text-xs leading-5 text-slate-600">
                                    <button
                                      onClick={() => playAt(q.source_id, q.start)}
                                      className="mr-1.5 inline-flex items-center gap-0.5 rounded bg-slate-100 px-1.5 py-0.5 text-indigo-700 hover:bg-indigo-100"
                                    >
                                      <Play className="h-2.5 w-2.5" />{q.lecture_label} {clock(q.start)}
                                    </button>
                                    「{q.text}」
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Outgoing links to higher claims or questions */}
                    {p.outgoing.length > 0 && (
                      <div className="rounded-lg bg-indigo-50/50 p-3 text-xs">
                        <p className="font-semibold text-indigo-900">這一點往下支持／回答了：</p>
                        <ul className="mt-1.5 space-y-1 text-slate-700">
                          {p.outgoing.map((out, oi) => (
                            <li key={oi}>
                              • <b>{out.relation_label}</b> →{" "}
                              {out.to_point_no ? (
                                <button onClick={() => goToPoint(out.to_point_no!)} className="font-medium text-indigo-700 underline">
                                  第 {out.to_point_no} 點
                                </button>
                              ) : out.to_question_no ? (
                                <span className="font-medium text-indigo-700">問題 {out.to_question_no}</span>
                              ) : null}
                              ：{out.to_statement}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>

      {/* Owner acceptance ruling */}
      <section className="mt-10 rounded-xl border-2 border-indigo-200 bg-white p-5">
        <h2 className="text-base font-bold text-slate-900">人工驗收裁定</h2>
        {unit.ruling && (
          <p className="mt-1 text-xs text-slate-600">
            目前狀態：
            <b className={unit.ruling.verdict === "approved" ? "text-emerald-700" : "text-rose-700"}>
              {unit.ruling.verdict === "approved" ? "已通過驗收" : "需要修改"}
            </b>
            （{unit.ruling.at}）{unit.ruling.note ? ` — ${unit.ruling.note}` : ""}
          </p>
        )}
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={2}
          placeholder="可填驗收備註（例如哪一點要微調、或確認無誤）"
          className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
        />
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            disabled={saving}
            onClick={() => submitRuling("approved")}
            className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            <CheckCircle2 className="h-4 w-4" />通過驗收
          </button>
          <button
            disabled={saving}
            onClick={() => submitRuling("needs_revision")}
            className="inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-4 py-2 text-sm font-semibold text-white hover:bg-rose-700 disabled:opacity-50"
          >
            <AlertTriangle className="h-4 w-4" />需要修改
          </button>
        </div>
      </section>

      {/* Multi-lecture transcript with highlight/gray-out */}
      <section id="transcript" className="mt-10">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-xl font-bold text-slate-900">本單元完整逐字稿</h2>
          <label className="inline-flex items-center gap-2 text-xs text-slate-600">
            <input type="checkbox" checked={leftOutOnly} onChange={(e) => setLeftOutOnly(e.target.checked)} />
            只看沒整理進任何一點的話
          </label>
        </div>
        <p className="mt-1 text-xs text-slate-500">有底色的句子已被上方論證引用，灰字為未引用原話；點任一句可直接播放錄音。</p>

        <div className="mt-4 space-y-6">
          {unit.transcript.map((lec) => (
            <div key={lec.source_id} className="rounded-xl border border-slate-200 bg-white p-4">
              <h3 className="text-sm font-bold text-indigo-800">{lec.lecture_label} · {lec.title}</h3>
              <div className="mt-3 space-y-3 text-sm leading-7">
                {lec.paragraphs.map((para) => {
                  const pieces = leftOutOnly ? para.pieces.filter((pc) => pc.uses.length === 0) : para.pieces;
                  if (pieces.length === 0) return null;
                  return (
                    <p key={para.paragraph}>
                      <span className="mr-2 font-mono text-xs text-slate-400">{para.paragraph}</span>
                      {pieces.map((pc, pi) => (
                        <span
                          key={pi}
                          onClick={() => playAt(lec.source_id, pc.start)}
                          title={pc.uses.length ? `用於第 ${pc.uses.join("、")} 點` : "未引用"}
                          className={`cursor-pointer rounded px-0.5 ${pc.uses.length ? "bg-amber-100/70 text-slate-900 hover:bg-amber-200" : "text-slate-400 hover:text-slate-700"}`}
                        >
                          {pc.text}
                        </span>
                      ))}
                    </p>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
