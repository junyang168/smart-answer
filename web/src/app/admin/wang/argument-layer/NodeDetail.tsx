"use client";

import type { Claim, Observation, Position, Question, Source, Step } from "./types";
import { KIND_NAME, REL_COLOR, isWithheld } from "./types";
import type { Placed } from "./geometry";

const Tag = ({ text, tone }: { text: string | number | null | undefined; tone?: "bad" | "good" }) => {
  if (text === null || text === undefined || text === "") return null;
  const color =
    tone === "bad"
      ? "border-rose-300 bg-rose-50 text-rose-700"
      : tone === "good"
        ? "border-emerald-300 text-emerald-700"
        : "border-slate-300 text-slate-500";
  return <span className={`rounded border px-1.5 py-0.5 font-mono text-[10.5px] ${color}`}>{text}</span>;
};

const Note = ({ children }: { children: React.ReactNode }) => (
  <p className="mb-3 rounded-md bg-rose-50 px-2.5 py-1.5 text-xs leading-6 text-rose-700">{children}</p>
);

const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <>
    <h3 className="mt-4 border-t border-slate-200 pt-2.5 text-[11px] font-semibold tracking-wider text-slate-500">
      {title}
    </h3>
    {children}
  </>
);

const fmtTime = (seconds: number) => {
  const value = Math.round(seconds);
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
};

export function NodeDetail({
  placed,
  source,
  onGoto,
}: {
  placed: Placed | null;
  source: Source;
  onGoto: (id: string) => void;
}) {
  if (!placed) {
    return (
      <aside className="flex w-[392px] flex-none items-center justify-center border-l border-slate-200 bg-white p-6 text-center text-sm text-slate-400">
        點任一節點看教授原話、關係與審核狀態
      </aside>
    );
  }

  const { kind, node } = placed;
  const refButton = (id: string, prefix?: string) => {
    const target = source.claims.find((claim) => claim.id === id);
    const other =
      target ??
      [...source.steps, ...source.questions, ...source.positions, ...source.observations].find(
        (item) => item.id === id,
      );
    return (
      <button
        key={id}
        type="button"
        onClick={() => onGoto(id)}
        className="block w-full rounded-md px-1.5 py-1 text-left text-xs text-slate-800 hover:bg-indigo-50"
      >
        <span className="pr-1.5 font-mono text-[10px] text-slate-400">{prefix ?? other?.label ?? id}</span>
        {other ? other.statement.slice(0, 64) : "（不在本來源）"}
      </button>
    );
  };

  const relationList = (edges: typeof source.edges, side: "from" | "to") => {
    if (!edges.length) return <p className="text-xs text-slate-400">無</p>;
    return edges.map((edge) => {
      const otherId = edge[side];
      const other =
        source.claims.find((claim) => claim.id === otherId) ??
        [...source.steps, ...source.questions, ...source.positions, ...source.observations].find(
          (item) => item.id === otherId,
        );
      return (
        <button
          key={edge.id}
          type="button"
          onClick={() => onGoto(otherId)}
          className="block w-full rounded-md px-1.5 py-1 text-left text-xs text-slate-800 hover:bg-indigo-50"
        >
          <span className="pr-1.5 font-mono text-[10px]" style={{ color: REL_COLOR[edge.type] ?? "#64748b" }}>
            {edge.type}
          </span>
          {other ? other.statement.slice(0, 70) : otherId}
          {edge.reason ? <span className="block pl-0.5 text-[11px] text-slate-500">{edge.reason}</span> : null}
        </button>
      );
    });
  };

  const claim = kind === "claim" ? (node as Claim) : null;
  const step = kind === "step" ? (node as Step) : null;
  const observation = kind === "observation" ? (node as Observation) : null;
  const question = kind === "question" ? (node as Question) : null;
  const withheld = !!step && isWithheld(step.eligibility);
  // Only the anchored kinds carry verbatim quotes; a claim points at steps instead.
  const anchored = claim ? null : (node as Step | Observation | Question | Position);
  const outgoing = source.edges.filter((edge) => edge.from === node.id);
  const incoming = source.edges.filter((edge) => edge.to === node.id);

  return (
    <aside className="w-[392px] flex-none overflow-auto border-l border-slate-200 bg-white px-5 py-4">
      <h2 className="text-sm font-bold text-slate-900">{KIND_NAME[kind]}</h2>
      <div className="mb-2.5 font-mono text-[11px] text-slate-400">{node.id}</div>
      <p className="mb-3 text-[14.5px] leading-8 text-slate-900">{node.statement}</p>

      <div className="mb-3 flex flex-wrap gap-1.5">
        {claim ? (
          <>
            <Tag text={claim.claim_type} />
            <Tag text={claim.attribution} />
            <Tag text={`maturity: ${claim.maturity}`} />
            <Tag text={claim.review_status} tone={claim.review_status === "approved" ? "good" : undefined} />
            {claim.scripture_refs.map((ref) => (
              <Tag key={ref} text={ref} />
            ))}
            {claim.topic_terms.map((term) => (
              <Tag key={term} text={term} />
            ))}
          </>
        ) : (
          <>
            <Tag text={step?.step_type || observation?.observation_type || question?.question_type} />
            <Tag text={step?.discourse_role} />
            {step && step.speaker !== "professor" ? (
              <Tag text={`speaker: ${step.speaker}`} tone="bad" />
            ) : (
              <Tag text={step?.speaker} />
            )}
            <Tag text={step?.stance} />
            {step?.eligibility ? <Tag text={step.eligibility} tone={withheld ? "bad" : "good"} /> : null}
            {step?.anchor_quality ? (
              <Tag text={`anchor_quality: ${step.anchor_quality}`} tone={step.anchor_quality === "missing" ? "bad" : undefined} />
            ) : null}
            {observation ? (
              observation.argument_role ? (
                <Tag text={`argument_role: ${observation.argument_role}`} />
              ) : (
                <Tag text="argument_role 未判定" tone="bad" />
              )
            ) : null}
            {question ? <Tag text={`answer_state: ${question.answer_state}`} /> : null}
            {question?.answer_verified_by_human === false ? <Tag text="未經人工確認" tone="bad" /> : null}
            {question?.questioner ? <Tag text={`questioner: ${question.questioner}`} /> : null}
            <Tag text={node.review_status} />
            {anchored?.scripture_refs?.map((ref) => (
              <Tag key={ref} text={ref} />
            ))}
          </>
        )}
      </div>

      {claim && claim.review_status !== "approved" ? (
        <Note>尚未經人工批准。批准只代表可在當前語料範圍內代表教授，不等於完成事實或神學核查。</Note>
      ) : null}
      {withheld ? <Note>沒有合格錨點，依審核規則不得作為論證證據。</Note> : null}
      {observation && !placed.linked ? <Note>這條 observation 沒有任何關係邊，尚未進入任何論證。</Note> : null}

      {claim ? (
        <>
          {claim.review_note ? (
            <Section title="人工審核註記">
              <p className="text-xs leading-6 text-slate-700">
                {claim.review_note}
                {claim.reviewed_by ? `（${claim.reviewed_by}）` : ""}
              </p>
            </Section>
          ) : null}
          <Section title={`由這些 evidence_step 走到（${claim.step_ids.length}）`}>
            {claim.step_ids.length ? (
              claim.step_ids.map((id) => refButton(id))
            ) : (
              <p className="text-xs text-slate-400">沒有掛上任何 evidence_step。</p>
            )}
          </Section>
          {claim.opposed_position_ids.length ? (
            <Section title={`教授反對的立場（${claim.opposed_position_ids.length}）`}>
              {claim.opposed_position_ids.map((id) => refButton(id))}
            </Section>
          ) : null}
        </>
      ) : (
        <>
          <Section title="教授原話">
            {anchored?.quotes.length ? (
              anchored.quotes.map((quote) => (
                <p
                  key={quote.id}
                  className="mb-2 border-l-2 border-slate-300 py-0.5 pl-2.5 font-serif text-[13.5px] leading-8 text-slate-800"
                >
                  {quote.text}
                  <span className="mt-1 block font-mono text-[10px] text-slate-400">
                    {quote.id}　段落 {quote.paragraph_key ?? "—"}
                    {quote.media_time !== null ? `　${fmtTime(quote.media_time)}` : ""}　{quote.anchor_state}
                  </span>
                </p>
              ))
            ) : (
              <p className="text-xs text-slate-400">沒有掛上任何逐字稿片段。</p>
            )}
          </Section>
          <Section title={`這一步支撐了什麼（${outgoing.length}）`}>{relationList(outgoing, "to")}</Section>
          <Section title={`什麼支撐了這一步（${incoming.length}）`}>{relationList(incoming, "from")}</Section>
          {(step?.claim_ids ?? question?.claim_ids ?? []).length ? (
            <Section
              title={`${question ? "回答它的 claim" : "產生的 claim"}（${(step?.claim_ids ?? question?.claim_ids ?? []).length}）`}
            >
              {(step?.claim_ids ?? question?.claim_ids ?? []).map((id) => refButton(id))}
            </Section>
          ) : null}
        </>
      )}
    </aside>
  );
}
