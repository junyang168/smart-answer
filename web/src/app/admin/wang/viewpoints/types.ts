export type AsOf = {
  registry_snapshot_id: string;
  coverage_snapshot_id: string | null;
  coverage_status: string;
  resolution_ledger_id: string | null;
  resolution_status: string;
  quality_report_id: string | null;
  quality_decision: string;
};

export type Envelope<T> = {
  schema_version: string;
  authority: { kind: string; projection: string; representation: string; read_only: true };
  as_of: AsOf;
  projection_sha256: string;
  links: Record<string, string>;
  data: T;
};

export type ViewpointSummary = {
  viewpoint_id: string;
  viewpoint_revision_id: string;
  core_proposition: string;
  wording_label: string;
  identity_status: string;
  review_status: string;
  approval_basis: string;
  scripture_scope: string[];
  topic_ids: string[];
  counts: { members: number; sources: number; routes: number; tensions: number; related: number };
  product_impact_count: number;
  quality_blocked: boolean | null;
};

export type OverviewData = {
  source_coverage: { covered: number | null; total: number | null; status: string };
  claim_resolution: null | Record<string, number>;
  active_viewpoints: number;
  exceptions: number;
  affected_products: number;
  quality_dimensions: Array<{ dimension: string; status: string; applicable: boolean }>;
  recall: {
    available: boolean;
    artifact_sha256?: string;
    blocking_version?: string;
    normalization_version?: string;
    statistics?: Record<string, number>;
    known_positive_recall?: { eligible_pair_count: number; found_pair_count: number; recall: number | null };
  };
};

export type RecallNeighbor = {
  claim_id: string;
  statement: string | null;
  score: number;
  signals: string[];
  shared_topic_terms: string[];
  shared_scripture_chapters: string[];
  candidate_viewpoint_ids: string[];
};

export type RecallDiagnostics = {
  available: boolean;
  artifact_sha256?: string;
  blocking_version?: string;
  normalization_version?: string;
  statistics?: Record<string, number>;
  known_positive_recall?: { eligible_pair_count: number; found_pair_count: number; recall: number | null };
  items: Array<{
    focal_claim_id: string;
    focal_statement: string | null;
    claim_role: string;
    normalized_topic_terms: string[];
    scripture_chapter_keys: string[];
    neighbors: RecallNeighbor[];
  }>;
  total: number;
  next_cursor: string | null;
  suppressed_blocks: Array<{ block_key: string; signal_kind: string; claim_count: number; reason_code: string }>;
  unparsed_scripture_refs: string[];
};

export type Member = {
  link: Record<string, unknown> & { link_type: string; viewpoint_claim_link_id: string };
  claim: Record<string, unknown> & { claim_id: string; statement: string; attribution?: string; review_status: string };
  evidence: Array<{
    evidence_step: null | Record<string, unknown> & { evidence_step_id: string; statement: string; speaker?: string; stance?: string };
    source_fragment: null | Record<string, unknown> & { fragment_id: string; verbatim_excerpt: string };
    source: null | Record<string, unknown> & { source_id: string; title?: string };
    citations: Array<Record<string, unknown> & { citation_id: string; status: string }>;
    locator: { source_url: string | null; paragraph_key: string | number | null; media_time: number | null };
  }>;
};

export type ViewpointDetail = {
  viewpoint: Record<string, unknown> & { viewpoint_id: string; identity_status: string };
  revision: Record<string, unknown> & {
    viewpoint_revision_id: string; core_proposition: string; review_status: string;
    representation_kind: string; not_a_direct_quote: true; scope: { scripture_scope: string[] };
  };
  members: Member[];
  routes: Array<Record<string, unknown> & {
    route_id: string; route_type: string; claim_id: string | null; evidence_step_ids: string[];
    attestations: Array<Record<string, unknown>>;
    snapshot: null | Record<string, unknown> & { eligibility: string; full_attestation_count: number; partial_attestation_count: number };
  }>;
  relations: Array<{
    relation_id: string; relation_type: string; from_viewpoint_id: string; to_viewpoint_id: string | null;
    claim_id: string | null; claim_statement: string | null; review_status: string;
  }>;
  history: Array<Record<string, unknown> & { viewpoint_revision_id: string; revision_number: number; core_proposition: string; review_status: string }>;
  impact: { dependencies: Array<Record<string, unknown>>; events: Array<Record<string, unknown>> };
  graph: {
    nodes: Array<{ id: string; kind: string; label: string }>;
    edges: Array<{ from: string; to: string; kind: string }>;
  };
  quality: null | Record<string, unknown> & { eligibility_decision: string; dimensions: Array<{ dimension: string; status: string }> };
};

export type ExceptionSummary = {
  exception_bundle_id: string; candidate_id: string; priority: number;
  consumer_impact: string; blocker_codes: string[]; remaining_findings: string[]; claim_count: number;
};
