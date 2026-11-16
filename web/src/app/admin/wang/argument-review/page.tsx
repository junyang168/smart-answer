"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Option = { key: string; label: string };
type Sentence = { text: string; focus: boolean };
type Question = {
  question_id: string;
  question: string;
  options: Option[];
  sentences: Sentence[];
  audio: { url: string; start: number | null; end: number | null };
  disagreement?: string | null;
};
type Followup = { followup_id: number; kind: "confirm" | "ask"; text: string; suggested_choice: string | null };
type Next =
  | { mode: "question"; progress: Progress; question: Question; is_owner: boolean }
  | { mode: "followup"; progress: Progress; question: Question; followup: Followup; your_answer: { choice: string | null; opinion: string | null }; is_owner: boolean }
  | { mode: "done"; progress: Progress; is_owner: boolean };
type Progress = { answered: number; total: number };
type Mine = { question_id: string; choice: string | null; opinion: string | null; focus: string; choice_label: string | null };
type Overview = {
  question: Question & { group_id: string; item_id: string };
  state: { status: string; settled: Record<string, number>; pending: number; unclear: number; choice?: string | null; reason?: string };
  answers: { reviewer: string; choice: string | null; opinion: string | null; followups: { kind: string; text: string; reply_choice: string | null; reply_text: string | null }[] }[];
};

const API = "/api/sc_api/argument_review";

async function call<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API}/${path}`, {
    method: body ? "POST" : "GET",
    cache: "no-store",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(typeof detail.detail === "string" ? detail.detail : "暫時無法連接，請稍後再試");
  }
  return response.json();
}

function Player({ audio }: { audio: Question["audio"] }) {
  const ref = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const start = audio.start ?? 0;
  const end = audio.end ?? undefined;
  const toggle = () => {
    const el = ref.current;
    if (!el) return;
    if (playing) {
      el.pause();
      return;
    }
    if (el.currentTime < start || (end !== undefined && el.currentTime >= end)) el.currentTime = start;
    void el.play();
  };
  const minutes = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  return (
    <div className="flex items-center gap-3">
      <audio
        ref={ref}
        src={audio.url}
        preload="none"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={(e) => {
          if (end !== undefined && e.currentTarget.currentTime >= end) e.currentTarget.pause();
        }}
      />
      <button
        type="button"
        onClick={toggle}
        className="flex h-14 min-w-14 items-center justify-center rounded-full bg-blue-700 px-5 text-lg font-semibold text-white shadow active:bg-blue-800"
        aria-label={playing ? "暫停" : "播放這一段"}
      >
        {playing ? "❚❚ 暫停" : "▶ 聽這一段"}
      </button>
      {audio.start !== null && audio.end !== null && (
        <span className="text-sm text-slate-500">
          {minutes(audio.start)}–{minutes(audio.end)}
        </span>
      )}
    </div>
  );
}

const CONTEXT_CHARS = 60;

function Passage({ sentences }: { sentences: Sentence[] }) {
  // Context only orients the reader; long grey text buries the highlighted part on a phone.
  // Runs of grey are cut: before the first highlight keep its end, after the last keep its
  // start, and between two highlights keep both ends around a "……".
  const runs: { focus: boolean; text: string }[] = [];
  for (const s of sentences) {
    const tail = runs[runs.length - 1];
    if (tail && tail.focus === s.focus) tail.text += s.text;
    else runs.push({ focus: s.focus, text: s.text });
  }
  const grey = (text: string, i: number) => {
    if (i === 0) return text.length > CONTEXT_CHARS ? `…${text.slice(-CONTEXT_CHARS)}` : text;
    if (i === runs.length - 1) return text.length > CONTEXT_CHARS ? `${text.slice(0, CONTEXT_CHARS)}…` : text;
    return text.length > CONTEXT_CHARS * 2 ? `${text.slice(0, CONTEXT_CHARS)}　……　${text.slice(-CONTEXT_CHARS)}` : text;
  };
  return (
    <p className="text-lg leading-9">
      {runs.map((r, i) =>
        r.focus ? (
          <span key={i} className="rounded bg-amber-200 px-0.5 text-slate-900">
            {r.text}
          </span>
        ) : (
          <span key={i} className="text-slate-400">
            {grey(r.text, i)}
          </span>
        ),
      )}
    </p>
  );
}

function Choices({ options, onPick, busy }: { options: Option[]; onPick: (key: string) => void; busy: boolean }) {
  return (
    <div className="grid gap-3">
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          disabled={busy}
          onClick={() => onPick(o.key)}
          className="min-h-14 rounded-xl border-2 border-slate-300 bg-white px-4 py-3 text-left text-lg active:border-blue-700 active:bg-blue-50 disabled:opacity-50"
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Opinion({ onSend, busy, prompt }: { onSend: (text: string) => void; busy: boolean; prompt: string }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  if (!open)
    return (
      <button type="button" onClick={() => setOpen(true)} className="text-base text-blue-700 underline">
        {prompt}
      </button>
    );
  return (
    <div className="grid gap-2">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={4}
        className="w-full rounded-xl border-2 border-slate-300 p-3 text-lg"
        placeholder="用你自己的話寫就可以"
        autoFocus
      />
      <button
        type="button"
        disabled={busy || !text.trim()}
        onClick={() => onSend(text)}
        className="min-h-12 rounded-xl bg-slate-800 px-4 text-lg text-white disabled:opacity-40"
      >
        送出我的看法
      </button>
    </div>
  );
}

function Review() {
  const [next, setNext] = useState<Next>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [thanks, setThanks] = useState("");
  const [redo, setRedo] = useState<Question | null>(null);
  const [mine, setMine] = useState<Mine[]>([]);

  const load = useCallback(async () => {
    setError("");
    try {
      const n = await call<Next>("next");
      setNext(n);
      setMine((await call<{ answers: Mine[] }>("mine")).answers);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const send = async (path: string, body: unknown, message: string) => {
    setBusy(true);
    try {
      await call(path, body);
      setThanks(message);
      setRedo(null);
      window.scrollTo({ top: 0 });
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (error) return <p role="alert" className="rounded-xl bg-red-50 p-4 text-lg text-red-800">{error}</p>;
  if (!next) return <p className="py-10 text-lg text-slate-500">正在載入…</p>;

  const question = redo ?? (next.mode !== "done" ? next.question : null);
  const number = Math.min(next.progress.answered + 1, next.progress.total);

  return (
    <div className="grid gap-6">
      {thanks && <p className="rounded-xl bg-green-50 p-3 text-base text-green-800">{thanks}</p>}
      {!redo && next.mode === "done" && (
        <div className="rounded-2xl bg-white p-6 text-center shadow-sm">
          <p className="text-2xl font-semibold">全部審完了，謝謝！</p>
          <p className="mt-2 text-slate-600">如果之後有追問，會出現在這裡。</p>
        </div>
      )}
      {question && (
        <article className="grid gap-5 rounded-2xl bg-white p-5 shadow-sm">
          <p className="text-sm text-slate-500">
            {redo ? "重新決定" : next.mode === "followup" ? "想再請教你一下" : `第 ${number} 題，共 ${next.progress.total} 題`}
          </p>
          <Player audio={question.audio} />
          <Passage sentences={question.sentences} />
          {!redo && next.mode === "followup" ? (
            <div className="grid gap-4">
              {next.your_answer.opinion && (
                <p className="rounded-xl bg-slate-50 p-3 text-base text-slate-600">你上次說：「{next.your_answer.opinion}」</p>
              )}
              <p className="text-xl font-semibold">{next.followup.text}</p>
              {next.followup.kind === "confirm" && next.followup.suggested_choice ? (
                <div className="grid gap-3">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => send("reply", { followup_id: next.followup.followup_id, choice: next.followup.suggested_choice }, "謝謝，已記下。")}
                    className="min-h-14 rounded-xl bg-blue-700 px-4 text-lg font-semibold text-white"
                  >
                    對，就是這個意思
                  </button>
                  <Opinion
                    busy={busy}
                    prompt="不是，我再說清楚一點"
                    onSend={(text) => send("reply", { followup_id: next.followup.followup_id, text }, "謝謝，我們會再看看。")}
                  />
                </div>
              ) : (
                <div className="grid gap-4">
                  <Choices
                    options={question.options}
                    busy={busy}
                    onPick={(choice) => send("reply", { followup_id: next.followup.followup_id, choice }, "謝謝，已記下。")}
                  />
                  <Opinion
                    busy={busy}
                    prompt="用自己的話回答"
                    onSend={(text) => send("reply", { followup_id: next.followup.followup_id, text }, "謝謝，我們會再看看。")}
                  />
                </div>
              )}
            </div>
          ) : (
            <div className="grid gap-4">
              <p className="text-xl font-semibold">{question.question}</p>
              <Choices
                options={question.options}
                busy={busy}
                onPick={(choice) => send("answer", { question_id: question.question_id, choice }, "謝謝，已記下。")}
              />
              <Opinion
                busy={busy}
                prompt="我想說說我的看法"
                onSend={(opinion) => send("answer", { question_id: question.question_id, opinion }, "謝謝，我們讀過後可能會再請教你。")}
              />
            </div>
          )}
        </article>
      )}
      {mine.length > 0 && (
        <details className="rounded-2xl bg-white p-5 shadow-sm">
          <summary className="cursor-pointer text-base text-slate-600">我已經回答的（{mine.length}）</summary>
          <ul className="mt-3 grid gap-3">
            {mine.map((m) => (
              <li key={m.question_id} className="flex items-start justify-between gap-3 border-t pt-3">
                <span className="text-base">
                  「{m.focus}…」<br />
                  <span className="text-slate-500">{m.choice_label ?? (m.opinion ? `你的看法：${m.opinion}` : "")}</span>
                </span>
                <button
                  type="button"
                  className="shrink-0 text-base text-blue-700 underline"
                  onClick={async () => {
                    setRedo(await call<Question>(`question/${m.question_id}`));
                    window.scrollTo({ top: 0 });
                  }}
                >
                  重新決定
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function OwnerDesk() {
  const [rows, setRows] = useState<Overview[]>();
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<"owner" | "all">("owner");
  const load = useCallback(async () => {
    try {
      setRows((await call<{ questions: Overview[] }>("overview")).questions);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  if (error) return <p role="alert" className="text-red-800">{error}</p>;
  if (!rows) return <p className="text-slate-500">正在載入…</p>;
  const shown = rows.filter((r) => filter === "all" || r.state.status === "owner");
  const count = (s: string) => rows.filter((r) => r.state.status === s).length;
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap gap-2 text-sm">
        <span className="rounded-full bg-amber-100 px-3 py-1">待裁定 {count("owner")}</span>
        <span className="rounded-full bg-green-100 px-3 py-1">已定案 {count("final") + count("ruled")}</span>
        <span className="rounded-full bg-slate-100 px-3 py-1">進行中 {count("open")}</span>
        <button type="button" className="ml-auto text-blue-700 underline" onClick={() => setFilter(filter === "owner" ? "all" : "owner")}>
          {filter === "owner" ? "看全部題目" : "只看待裁定"}
        </button>
      </div>
      {shown.length === 0 && <p className="text-slate-500">{filter === "owner" ? "目前沒有待裁定的題目。" : "還沒有題目。"}</p>}
      {shown.map((r) => (
        <OwnerItem key={r.question.question_id} row={r} onDone={load} />
      ))}
    </div>
  );
}

function OwnerItem({ row, onDone }: { row: Overview; onDone: () => void }) {
  const [choice, setChoice] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const label = (k: string | null) => row.question.options.find((o) => o.key === k)?.label ?? k ?? "";
  return (
    <article className="grid gap-3 rounded-2xl bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <Player audio={row.question.audio} />
        <span className="text-sm text-slate-500">{row.question.group_id}</span>
      </div>
      <Passage sentences={row.question.sentences} />
      <p className="font-semibold">{row.question.question}</p>
      {row.question.disagreement && <p className="text-sm text-slate-500">{row.question.disagreement}</p>}
      <ul className="grid gap-2 text-sm">
        {row.answers.map((a, i) => (
          <li key={i} className="rounded-lg bg-slate-50 p-2">
            <span className="font-medium">{a.reviewer}</span>：{label(a.choice)}
            {a.opinion && <span className="text-slate-600">　「{a.opinion}」</span>}
            {a.followups.map((f, j) => (
              <div key={j} className="mt-1 text-slate-600">
                問：{f.text}　答：{f.reply_choice ? label(f.reply_choice) : f.reply_text ?? "（未回）"}
              </div>
            ))}
          </li>
        ))}
        {row.answers.length === 0 && <li className="text-slate-500">還沒有人回答</li>}
      </ul>
      {row.state.status === "ruled" || row.state.status === "final" ? (
        <p className="text-sm text-green-800">
          {row.state.status === "ruled" ? "已裁定" : "已定案"}：{label(row.state.choice ?? null)}
          {row.state.reason && `（${row.state.reason}）`}
        </p>
      ) : (
        <div className="grid gap-2">
          <select value={choice} onChange={(e) => setChoice(e.target.value)} className="rounded-lg border p-2">
            <option value="">選擇裁定</option>
            {row.question.options.map((o) => (
              <option key={o.key} value={o.key}>
                {o.label}
              </option>
            ))}
          </select>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} className="rounded-lg border p-2" placeholder="一句理由" />
          {error && <p className="text-sm text-red-800">{error}</p>}
          <button
            type="button"
            disabled={!choice || !reason.trim()}
            className="justify-self-start rounded-lg bg-slate-800 px-4 py-2 text-white disabled:opacity-40"
            onClick={async () => {
              try {
                await call("rule", { question_id: row.question.question_id, choice, reason });
                onDone();
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            裁定
          </button>
        </div>
      )}
    </article>
  );
}

export default function ArgumentReviewPage() {
  const [tab, setTab] = useState<"review" | "owner">("review");
  const [isOwner, setIsOwner] = useState(false);
  useEffect(() => {
    call<Next>("next").then((n) => setIsOwner(n.is_owner)).catch(() => undefined);
  }, []);
  return (
    <main className="mx-auto grid max-w-2xl gap-5 px-4 py-6">
      <header className="grid gap-1">
        <h1 className="text-2xl font-semibold">同工審閱</h1>
        <p className="text-base text-slate-600">先聽一聽，再看底色的那幾句，選一個最接近你想法的答案。不確定時，用自己的話說就可以。</p>
      </header>
      {isOwner && (
        <nav className="flex gap-2">
          {(["review", "owner"] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className={`rounded-full px-4 py-2 text-base ${tab === t ? "bg-slate-800 text-white" : "bg-white text-slate-700"}`}
            >
              {t === "review" ? "我來審閱" : "負責人：待裁定"}
            </button>
          ))}
        </nav>
      )}
      {tab === "review" ? <Review /> : <OwnerDesk />}
    </main>
  );
}
