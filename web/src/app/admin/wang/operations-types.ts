export type StageId = "extraction" | "review" | "adjudication" | "merge" | "ingest";

/** `stale` is the load-bearing one: it succeeded, but not against what is here now. */
export type CellState =
  | "current"
  | "stale"
  | "never"
  | "failed"
  | "running"
  | "queued"
  | "no_source";

export type RunSummary = {
  run_id: string;
  status: string;
  trigger: string;
  triggered_by: string | null;
  started_at: string | null;
  seconds: number | null;
  model_id: string | null;
  cost_usd: number | null;
  error_message: string | null;
};

export type StageCell = {
  state: CellState;
  reason?: string;
  quality: Record<string, unknown> | null;
  run: RunSummary | null;
  had_earlier_success?: boolean;
  /** Present when the authoring store, not the ledger, is what says this is done. */
  store?: { source_id: string; revision: number; updated_at: string | null };
};

export type OverviewRow = {
  source_id: string;
  kind: "sermon" | "notes_manuscript";
  title: string;
  series: string | null;
  year: number | null;
  book: string | null;
  chapter: number | null;
  verse_start: number | null;
  topics: string[];
  manuscript_file?: string;
  source_available: boolean;
  stages: Record<StageId, StageCell>;
  articles: string[];
};

export type OverviewWarning = { code: string; message: string; detail?: string[] };

export type Overview = {
  schema_version: string;
  generated_at: string;
  price_version: string;
  price_effective: string;
  price_source: string;
  stages: StageId[];
  summary: {
    rows: number;
    sermons: number;
    notes_manuscripts: number;
    without_source: number;
    by_stage: Record<StageId, Record<string, number>>;
    runs_recorded: number;
    spend_usd: number;
    succeeded_runs_without_a_price: number;
  };
  rows: OverviewRow[];
  warnings: OverviewWarning[];
};
