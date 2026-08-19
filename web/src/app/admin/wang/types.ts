export type WorkflowStage =
  | "composition_ready"
  | "knowledge_ready"
  | "authoring"
  | "independent_editorial_review"
  | "revision"
  | "final_delta_review"
  | "program_audit"
  | "publication_decision"
  | "repository_published"
  | "production_visible";

export type ProgressArticle = {
  article_unit_id: string;
  passage: {
    osis: string;
    display: string;
    start: { chapter: number; verse: number };
    end: { chapter: number; verse: number };
    cross_chapter: boolean;
  };
  title: string;
  draft_id: string | null;
  slug?: string | null;
  manifest_status?: string | null;
  current_stage: WorkflowStage;
  stages: { stage: WorkflowStage; state: "complete" | "active" | "blocked" | "not_started" | "unknown" }[];
  editorial: null | {
    score: number | null;
    passed: boolean | null;
    hard_gate_failures: unknown[];
    declared_hard_failures: unknown[];
  };
  program_audit: null | { status: string; error_count: number | null; warning_count: number | null };
  publication_decision: null | { kind: "human" | "automated" | "unknown"; schema_version: string | null; authority: string | null; valid: boolean };
  sha_integrity: { status: "consistent" | "partial" | "mismatch" | "not_applicable"; checks: { name: string; status: string; actual?: string | null; expected?: string | null }[] };
  media: { covered_decision_count: number; player_count: number };
  repository_published: boolean;
  production_visible: boolean | null;
  blockers: { code: string; severity: string; message?: string }[];
  next_step: string | null;
  updated_at: string | null;
  links: {
    public: string | null;
    manifest: string | null;
    manuscript: string | null;
    editorial_review: string | null;
    program_audit: string | null;
    publication_decision: string | null;
  };
};

export type MatthewProgress = {
  schema_version: "wang-matthew-exposition-progress.v1";
  generated_at: string;
  book: { osis: "Matt"; label: string; chapter_count: 28 };
  runtime: {
    environment: string;
    production_probe_configured: boolean;
    production_probe_available: boolean;
    deployment_state: string;
    recognized_publication_decision_schemas: string[];
  };
  summary: {
    planned_article_count: number;
    generated_article_count: number;
    repository_published_count: number;
    production_visible_count: number | null;
    cross_chapter_article_count: number;
    blocked_article_count: number;
    total_verse_count: number;
    planned_verse_count: number;
    generated_verse_count: number;
    repository_verse_count: number;
    production_verse_count: number | null;
  };
  chapters: {
    chapter: number;
    verse_count: number;
    planned_verse_count: number;
    generated_verse_count: number;
    repository_verse_count: number;
    production_verse_count: number | null;
    coverage_gap_count: number;
    article_unit_ids: string[];
  }[];
  articles: ProgressArticle[];
  warnings: { code: string; severity: string; message?: string }[];
};
