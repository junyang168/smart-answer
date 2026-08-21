export type Quote = {
  id: string;
  text: string;
  paragraph_key: string | number | null;
  media_time: number | null;
  anchor_state: string;
};

type Common = {
  id: string;
  label: string;
  statement: string;
  review_status: string;
  ordinal: number;
  rank: number | null;
  quotes: Quote[];
  scripture_refs: string[];
};

export type Step = Common & {
  step_type: string;
  discourse_role: string;
  speaker: string;
  stance: string;
  eligibility: string;
  anchor_quality: string;
  claim_ids: string[];
  claim_group_label: string;
  lane: number;
};

export type Observation = Common & { observation_type: string; argument_role: string };

export type Question = Common & {
  questioner: string;
  question_type: string;
  answer_state: string;
  answer_verified_by_human: boolean | null;
  claim_ids: string[];
};

export type Position = Common & { attribution: string };

export type Claim = {
  id: string;
  label: string;
  statement: string;
  claim_type: string;
  attribution: string;
  maturity: string;
  review_status: string;
  scripture_refs: string[];
  topic_terms: string[];
  step_ids: string[];
  opposed_position_ids: string[];
  ordinal: number;
  reviewed_by: string;
  review_note: string;
};

export type Edge = {
  id: string;
  from: string;
  to: string;
  type: string;
  review_status: string;
  reason: string;
};

export type Stats = {
  steps: number;
  steps_linked: number;
  steps_isolated: number;
  observations: number;
  observations_linked: number;
  claims: number;
  questions: number;
  positions: number;
  edges: number;
};

export type SourceSummary = {
  key: string;
  title: string;
  note: string;
  source_type: string;
  source_ids: string[];
  stats: Stats;
};

export type Source = SourceSummary & {
  steps: Step[];
  observations: Observation[];
  claims: Claim[];
  questions: Question[];
  positions: Position[];
  edges: Edge[];
};

export type Kind = "step" | "observation" | "question" | "position" | "claim";

export type AnyNode = Step | Observation | Question | Position | Claim;

export type SearchHit = {
  kind: Kind;
  id: string;
  label: string;
  statement: string;
  source_key: string;
  source_title: string;
};

export const LANE_HINT = ["問題與反方立場", "經文、原文、歷史背景", "推理與限定", "結論", "應用"];
export const LANE_COLOR = ["#b45309", "#0369a1", "#4338ca", "#047857", "#6d28d9"];
export const OBS_COLOR = "#94a3b8";
export const CLAIM_COLOR = "#3730a3";
export const REL_COLOR: Record<string, string> = {
  supports: "#4338ca",
  qualifies: "#b45309",
  refutes: "#be123c",
  answers: "#047857",
  applies: "#6d28d9",
  contextualizes: "#94a3b8",
};
// Store collections keep their own names; only editorial concepts get Chinese.
export const KIND_NAME: Record<Kind, string> = {
  step: "evidence_step",
  question: "question",
  position: "position_node",
  observation: "observation",
  claim: "claim",
};

/**
 * A set of records another page sent the reader here to look at.
 *
 * The ids come from whoever decided them -- the health view decides which
 * records are stranded -- so this page never has to make the same judgement a
 * second time and reach a different answer.
 */
export type Focus = { ids: Set<string>; label: string };

/** Steps whose anchor does not qualify them as evidence for a claim. */
export const isWithheld = (eligibility: string) =>
  !!eligibility &&
  (eligibility.startsWith("withheld") || eligibility === "context_only" || eligibility === "contextual_only");
