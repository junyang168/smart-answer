export type ArticleStage = {
  stage: string;
  state: "complete" | "active" | "blocked" | "not_started" | "unknown";
};

export type Editorial = {
  dimensions: number;
  passed_dimensions: number;
  below_minimum: string[];
  hard_gate_failures: unknown[];
  declared_hard_failures: unknown[];
  passed: boolean;
  total_score: number | null;
};

export type ArticleRun = {
  run_id: string;
  status: string;
  trigger: string;
  triggered_by: string | null;
  started_at: string | null;
  seconds: number | null;
  cost_usd: number | null;
  error_message: string | null;
};

export type ArticleRow = {
  plan_id: string;
  title: string;
  axis: string | null;
  product_type: string | null;
  decision_count: number | null;
  plan_review_status: string | null;
  draft: string | null;
  slug: string | null;
  passage: string | null;
  current_stage: string | null;
  stages: ArticleStage[];
  editorial: Editorial | null;
  program_audit: { status: string; error_count: number | null; warning_count: number | null } | null;
  publication_decision: { kind: string; valid: boolean } | null;
  sha_integrity: { status: string } | null;
  repository_published: boolean;
  production_visible: boolean | null;
  blockers: { code: string; severity: string; message?: string }[];
  next_step: string | null;
  cited_sources: string[];
  links: Record<string, string | null>;
  runs: ArticleRun[];
  cost_usd: number | null;
};

export type ArticlesPayload = {
  schema_version: string;
  generated_at: string;
  summary: {
    plans: number;
    written: number;
    unwritten: number;
    published: number;
    spend_usd: number;
    article_runs_recorded: number;
  };
  rows: ArticleRow[];
  warnings: { code: string; message: string; detail?: string[] }[];
};
