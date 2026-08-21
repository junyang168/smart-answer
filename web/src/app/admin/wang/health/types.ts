export type MetricValue = {
  document_id: string;
  label: string;
  value: number;
  outlier: boolean;
};

export type Metric = {
  name: string;
  question: string;
  state: "measured" | "pending";
  pending_reason: string | null;
  bad_direction: "low" | "high";
  owner: string;
  owner_href: string | null;
  measured_documents: number;
  median: number | null;
  spread: number | null;
  cutoff: number | null;
  distribution_is_thin: boolean;
  values: MetricValue[];
};

export type Trend = {
  points: { date: string; median: number; packages: number }[];
  events: { date: string; prompt_sha256: string; label: string }[];
};

export type ExceptionReason = {
  metric: string;
  value: number;
  sentence: string;
  link: { label: string; href: string } | null;
};

export type CorpusException = {
  document_id: string;
  label: string;
  argument_layer_key: string | null;
  source_id: string | null;
  generated_at: string | null;
  reasons: ExceptionReason[];
};

export type HealthReport = {
  schema_version: "wang_extraction_health_v1";
  generated_at: string;
  advisory: string;
  corpus: {
    documents: number;
    measured: number;
    never_extracted: number;
    needs_attention: number;
    within_normal_range: number;
    measured_manuscripts: number;
    packages_on_disk: number;
    off_corpus_documents: string[];
  };
  metrics: Metric[];
  trend: Trend;
  exceptions: CorpusException[];
  documents: {
    document_id: string;
    label: string;
    kind: string;
    argument_layer_key: string | null;
    source_id: string | null;
    generated_at: string | null;
    model_id: string | null;
    prompt_sha256: string | null;
    findings: number;
    claims: number;
    coverage: number | null;
    stranded: number;
    stranded_rate: number | null;
    sound: number | null;
    sound_unavailable: string | null;
  }[];
};

/** Every band is a ratio, so one formatter keeps them reading alike. */
export const ratio = (value: number) => `${Math.round(value * 100)}%`;

export const shortDate = (value: string) => value.slice(5).replace("-", "/");
