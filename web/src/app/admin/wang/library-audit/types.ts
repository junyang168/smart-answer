export type Verdict = { code: string; text: string };

export type Evidence = { label: string; field: string; text: string };

export type FollowUpItem = {
  object_id: string;
  collection: string;
  verdict: Verdict;
  reason: string;
  evidence: Evidence[];
  target?: string;
};

export type DanglingTarget = {
  target: string;
  count: number;
  collections: string[];
  object_ids: string[];
};

export type FollowUpGroup = {
  kind: string;
  title: string;
  note: string;
  needs_human: boolean;
  count: number;
  items: FollowUpItem[];
  targets?: DanglingTarget[];
};

export type LayerDetail = { label: string; count: number; text: string };

export type Layer = {
  key: string;
  layer: number;
  name: string;
  kind: "ratio" | "sample";
  unit: string;
  question: string;
  headline: string;
  note?: string;
  skipped?: number;
  skipped_note?: string;
  detail: LayerDetail[];
  passed?: number;
  total?: number;
  judged?: number;
  disputed?: number;
  population?: number;
  model_errors?: number;
};

export type RunStatus = {
  state: "running" | "finished" | "died";
  stage?: string;
  done?: number;
  total?: number;
  started_at?: string;
  finished_at?: string;
  run_id?: string;
  detail?: string;
};

export type AuditReport = {
  status: "ok" | "never_run";
  reports_root?: string;
  run_id?: string;
  generated_at?: string;
  model?: string;
  seed?: number;
  scope?: {
    mode: string;
    sources: number;
    sources_out_of_scope: number;
    text: string;
    duplicate_sources: { name: string; source_ids: string[] }[];
  };
  corpus?: { fragments: number; claims: number; viewpoints: number };
  layers?: Layer[];
  followups?: FollowUpGroup[];
  needs_human?: number;
  mechanical?: number;
  run?: RunStatus | null;
};

/** `7,333/7,343` reads better than `99.9%` when the question is "how many are left". */
export const count = (value: number) => value.toLocaleString("en-US");

export const shortfall = (layer: Layer) =>
  layer.kind === "ratio" ? (layer.total ?? 0) - (layer.passed ?? 0) : (layer.disputed ?? 0);
