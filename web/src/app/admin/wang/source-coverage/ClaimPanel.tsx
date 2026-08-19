"use client";

import { useMemo } from "react";
import { ArrowLeft, AlertTriangle } from "lucide-react";
import type { Claim, Fragment, Node, NodeKind, SourceDetail } from "./types";
import { ANCHOR_NOTE, KIND_ID_FIELD, KIND_NAME, KIND_STYLE, isPlaced, timecode } from "./types";

type Props = {
  detail: SourceDetail;
  selectedFragmentId: string | null;
  selectedClaimId: string | null;
  onSelectFragment: (fragmentId: string | null) => void;
  onSelectClaim: (claim: Claim) => void;
  onClearClaim: () => void;
};

const PANEL = "w-[42%] min-w-[380px] max-w-[620px] shrink-0 overflow-auto border-l border-slate-200 bg-slate-50 px-4 py-4";

type Row = [label: string, value: string | number | null | undefined, tone?: "warn"];

/**
 * The store's fields, named.
 *
 * A run of bare values reads as noise: `candidate` does not say whether it is
 * `review_status` or `maturity`, and on most claims both are that same word.
 * Naming each row with the field it comes from is also what makes a card
 * comparable to what `psql` would print, which is the point of a diagnostic
 * view. Empty rows are dropped rather than shown blank.
 */
function Fields({ rows }: { rows: Row[] }) {
  const shown = rows.filter(([, value]) => value !== null && value !== undefined && value !== "");
  if (!shown.length) return null;
  return (
    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono text-[10.5px] leading-5">
      {shown.map(([label, value, tone]) => (
        <div key={label} className="contents">
          <dt className="text-slate-400">{label}</dt>
          <dd className={`min-w-0 break-words ${tone === "warn" ? "text-rose-600" : "text-slate-700"}`}>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function BackButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1 rounded-full border border-slate-300 bg-white px-2.5 py-1 text-[11.5px] text-slate-600 hover:bg-slate-100"
    >
      <ArrowLeft className="h-3.5 w-3.5" />全部 claims
    </button>
  );
}

function KindBadge({ kind }: { kind: NodeKind }) {
  return <span className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${KIND_STYLE[kind]}`}>{KIND_NAME[kind]}</span>;
}

/** The fields a node carries, in the order the store defines them. */
function nodeRows(node: Node): Row[] {
  return [
    [KIND_ID_FIELD[node.kind], node.id],
    ["step_type", node.step_type],
    ["observation_type", node.observation_type],
    ["argument_role", node.argument_role],
    ["support_eligibility", node.support_eligibility],
    ["answer_state", node.answer_state],
    ["discourse_role", node.discourse_role],
    ["attribution", node.attribution],
    ["scripture_refs", node.scripture_refs.join("、")],
  ];
}

export function ClaimPanel({
  detail,
  selectedFragmentId,
  selectedClaimId,
  onSelectFragment,
  onSelectClaim,
  onClearClaim,
}: Props) {
  const claims = useMemo(
    () =>
      Object.values(detail.claims).sort(
        (a, b) => (a.first_ordinal ?? Number.MAX_SAFE_INTEGER) - (b.first_ordinal ?? Number.MAX_SAFE_INTEGER),
      ),
    [detail.claims],
  );
  const unplaced = useMemo(() => Object.values(detail.fragments).filter((item) => !isPlaced(item)), [detail.fragments]);

  if (selectedFragmentId && detail.fragments[selectedFragmentId]) {
    return (
      <FragmentDetail
        detail={detail}
        fragment={detail.fragments[selectedFragmentId]}
        onBack={() => onSelectFragment(null)}
        onSelectClaim={onSelectClaim}
      />
    );
  }

  if (selectedClaimId && detail.claims[selectedClaimId]) {
    return (
      <ClaimDetail
        detail={detail}
        claim={detail.claims[selectedClaimId]}
        onBack={onClearClaim}
        onSelectFragment={onSelectFragment}
      />
    );
  }

  return (
    <aside className={PANEL}>
      <h2 className="text-[13px] font-bold text-slate-900">
        claims<span className="ml-2 font-mono text-[11px] font-normal text-slate-500">{claims.length}</span>
      </h2>
      <p className="mt-1 text-[11.5px] leading-6 text-slate-500">
        依它在來源裡出現的先後排列。點一條，左邊會標出它實際引用的每一段話。
        （<code className="font-mono">CL005</code> 這種編號是模型吐出的順序，不是講道的順序。）
      </p>

      {unplaced.length ? (
        <div className="mt-3 flex gap-2 rounded-lg border border-amber-300 bg-amber-50 p-2.5 text-[11.5px] leading-6 text-amber-900">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {unplaced.length} 個 source_fragment 無法落回原文，因此左邊不會標出它們：
            {[...new Set(unplaced.map((item) => ANCHOR_NOTE[item.anchor_method]))].join("；")}
          </span>
        </div>
      ) : null}

      <div className="mt-3 space-y-2">
        {claims.map((claim) => (
          <button
            key={claim.id}
            type="button"
            onClick={() => onSelectClaim(claim)}
            className={`block w-full rounded-xl border bg-white p-3 text-left transition hover:border-indigo-300 hover:shadow-sm ${
              claim.id === selectedClaimId ? "border-amber-500 ring-2 ring-amber-200" : "border-slate-200"
            }`}
          >
            <p className="text-[13px] leading-7 text-slate-900">{claim.statement}</p>
            <Fields
              rows={[
                ["claim_id", claim.id],
                ["claim_type", claim.claim_type],
                ["evidence_step", claim.evidence_step_ids.length],
                ["fragment", claim.fragment_ids.length, claim.fragment_ids.length ? undefined : "warn"],
                ["scripture_refs", claim.scripture_refs.join("、")],
              ]}
            />
          </button>
        ))}
        {claims.length === 0 ? (
          <p className="rounded-xl border border-dashed border-slate-300 bg-white py-10 text-center text-[12.5px] text-slate-400">
            這個來源沒有任何 claim。
          </p>
        ) : null}
      </div>
    </aside>
  );
}

/**
 * One claim: what it asserts, and every step of the source it rests on.
 *
 * The list card carries the statement too, but a reader who clicked a claim is
 * now reading the left pane, where the card has scrolled away.  Leading with
 * the assertion keeps "what is this claim claiming" answerable without
 * navigating back.
 */
function ClaimDetail({
  detail,
  claim,
  onBack,
  onSelectFragment,
}: {
  detail: SourceDetail;
  claim: Claim;
  onBack: () => void;
  onSelectFragment: (fragmentId: string | null) => void;
}) {
  // A claim reaches the source three ways, and walking only `evidence_step_ids`
  // reaches one of them.  334 fragments corpus-wide belong to a question the
  // claim answers, and 66 to a step that names the claim without the claim
  // naming it back.  Missing those made the card's fragment count larger than
  // anything the panel could show.
  const named = Object.values(detail.nodes).filter((node) => node.claim_ids.includes(claim.id));
  const claimNames = new Set(claim.evidence_step_ids);
  const stepIds = new Set([...claimNames, ...named.filter((node) => node.kind === "step").map((node) => node.id)]);
  const steps = [...stepIds].map((id) => detail.nodes[id]).filter(Boolean) as Node[];
  const others = named.filter((node) => node.kind !== "step");
  const namesBack = new Set(named.map((node) => node.id));

  const linkNote = (step: Node) => {
    if (claimNames.has(step.id) && namesBack.has(step.id)) return "";
    return claimNames.has(step.id)
      ? "單向：claim 列了這個 step，step 的 produced_claim_ids 沒有回指"
      : "單向：step 說它產生這條 claim，claim 的 evidence_step_ids 沒有列入它";
  };

  const quotes = (node: Node) => (
    <div className="mt-2 space-y-1">
      {node.fragment_ids.map((fragmentId) => {
        const fragment = detail.fragments[fragmentId];
        if (!fragment) return null;
        return (
          <button
            key={fragmentId}
            type="button"
            onClick={() => onSelectFragment(fragmentId)}
            className="block w-full rounded-lg border-l-2 border-amber-400 bg-amber-50 px-2 py-1 text-left text-[12px] leading-6 text-slate-700 hover:bg-amber-100"
          >
            {fragment.excerpt || "（沒有 verbatim_excerpt）"}
            {fragment.media_time !== null ? (
              <span className="ml-1.5 font-mono text-[10px] text-amber-700">{timecode(fragment.media_time)}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );

  return (
    <aside className={PANEL}>
      <BackButton onClick={onBack} />

      <div className="mt-3 rounded-xl border border-indigo-300 bg-white p-3">
        <p className="font-mono text-[10.5px] text-indigo-700">claim</p>
        <p className="mt-1 text-[14px] leading-8 text-slate-950">{claim.statement}</p>
        <Fields
          rows={[
            ["claim_id", claim.id],
            ["claim_type", claim.claim_type],
            ["attribution", claim.attribution],
            ["maturity", claim.maturity],
            ["review_status", claim.review_status],
            ["scripture_refs", claim.scripture_refs.join("、")],
          ]}
        />
      </div>

      <h3 className="mt-4 text-[12.5px] font-bold text-slate-900">
        它靠這個來源的哪幾步
        <span className="ml-2 font-mono text-[11px] font-normal text-slate-500">{steps.length} evidence_step</span>
      </h3>
      {claim.foreign_evidence_steps ? (
        <p className="mt-1 flex items-start gap-1.5 text-[11.5px] leading-6 text-amber-800">
          <AlertTriangle className="mt-1 h-3.5 w-3.5 shrink-0" />
          另有 {claim.foreign_evidence_steps} 個 evidence_step 不在這個來源——這條 claim 是跨來源建立的，左邊只標得出它在這裡引用的部分。
        </p>
      ) : null}

      <div className="mt-2 space-y-2">
        {steps.map((step) => (
          <div key={step.id} className="rounded-xl border border-slate-200 bg-white p-3">
            <KindBadge kind={step.kind} />
            <p className="mt-1.5 text-[13px] leading-7 text-slate-800">{step.statement}</p>
            {linkNote(step) ? <p className="mt-1 text-[11px] text-amber-700">{linkNote(step)}</p> : null}
            <Fields rows={nodeRows(step)} />
            {quotes(step)}
          </div>
        ))}
        {steps.length === 0 ? (
          <p className="rounded-xl border border-dashed border-slate-300 bg-white py-6 text-center text-[12px] text-slate-400">
            這條 claim 在這個來源沒有任何 evidence_step。
          </p>
        ) : null}
      </div>

      {others.length ? (
        <>
          <h3 className="mt-4 text-[12.5px] font-bold text-slate-900">
            還有哪些記錄指名它
            <span className="ml-2 font-mono text-[11px] font-normal text-slate-500">{others.length}</span>
          </h3>
          <p className="mt-1 text-[11.5px] leading-6 text-slate-500">
            這些不是它的 evidence_step，但它們指名了這條 claim——例如一個 question 把它列為答案。它們各自錨在原文上，所以左邊的標示也包含它們。
          </p>
          <div className="mt-2 space-y-2">
            {others.map((node) => (
              <div key={node.id} className="rounded-xl border border-slate-200 bg-white p-3">
                <KindBadge kind={node.kind} />
                <p className="mt-1.5 text-[13px] leading-7 text-slate-800">{node.statement}</p>
                <Fields rows={nodeRows(node)} />
                {quotes(node)}
              </div>
            ))}
          </div>
        </>
      ) : null}
    </aside>
  );
}

function FragmentDetail({
  detail,
  fragment,
  onBack,
  onSelectClaim,
}: {
  detail: SourceDetail;
  fragment: Fragment;
  onBack: () => void;
  onSelectClaim: (claim: Claim) => void;
}) {
  const nodes = fragment.node_ids.map((id) => detail.nodes[id]).filter(Boolean) as Node[];
  const claimIds = [...new Set(nodes.flatMap((node) => node.claim_ids))];
  const viaSteps = Object.values(detail.claims).filter(
    (claim) => !claimIds.includes(claim.id) && claim.fragment_ids.includes(fragment.id),
  );
  const claims = [...claimIds.map((id) => detail.claims[id]).filter(Boolean), ...viaSteps] as Claim[];

  return (
    <aside className={PANEL}>
      <BackButton onClick={onBack} />

      <div className="mt-3 rounded-xl border border-amber-300 bg-amber-50 p-3">
        <p className="font-mono text-[10.5px] text-amber-800">source_fragment</p>
        <p className="mt-1 text-[13px] leading-7 text-slate-900">{fragment.excerpt || "（沒有 verbatim_excerpt）"}</p>
        <Fields
          rows={[
            ["fragment_id", fragment.id],
            ["paragraph_key", fragment.paragraph_key],
            ["anchor_state", fragment.anchor_state],
            ["media_time", fragment.media_time === null ? "" : timecode(fragment.media_time)],
          ]}
        />
        <p className="mt-2 text-[11px] leading-6 text-amber-900">{ANCHOR_NOTE[fragment.anchor_method]}</p>
      </div>

      <h3 className="mt-4 text-[12.5px] font-bold text-slate-900">
        錨在這段話上的記錄<span className="ml-2 font-mono text-[11px] font-normal text-slate-500">{nodes.length}</span>
      </h3>
      <div className="mt-2 space-y-2">
        {nodes.map((node) => (
          <div key={node.id} className="rounded-xl border border-slate-200 bg-white p-3">
            <KindBadge kind={node.kind} />
            <p className="mt-1.5 text-[13px] leading-7 text-slate-800">{node.statement}</p>
            <Fields rows={nodeRows(node)} />
          </div>
        ))}
        {nodes.length === 0 ? (
          <p className="rounded-xl border border-dashed border-slate-300 bg-white py-6 text-center text-[12px] text-slate-400">
            沒有記錄引用這個 fragment。
          </p>
        ) : null}
      </div>

      <h3 className="mt-4 text-[12.5px] font-bold text-slate-900">
        由此產生的 claims<span className="ml-2 font-mono text-[11px] font-normal text-slate-500">{claims.length}</span>
      </h3>
      <div className="mt-2 space-y-2">
        {claims.map((claim) => (
          <button
            key={claim.id}
            type="button"
            onClick={() => onSelectClaim(claim)}
            className="block w-full rounded-xl border border-indigo-200 bg-white p-3 text-left hover:border-indigo-400"
          >
            <p className="text-[13px] leading-7 text-slate-900">{claim.statement}</p>
            <Fields
              rows={[
                ["claim_id", claim.id],
                ["claim_type", claim.claim_type],
                ["fragment", claim.fragment_ids.length],
              ]}
            />
          </button>
        ))}
        {claims.length === 0 ? (
          <p className="rounded-xl border border-dashed border-slate-300 bg-white py-6 text-center text-[12px] text-slate-400">
            這段話進了論證層，但還沒有支撐任何 claim。
          </p>
        ) : null}
      </div>
    </aside>
  );
}
