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
  membership_kind?: "proposition_unit";
  link: Record<string, unknown> & {
    link_type: string; viewpoint_claim_link_id?: string;
    viewpoint_proposition_unit_link_id?: string;
  };
  proposition_unit?: Record<string, unknown> & {
    proposition_unit_id: string; parent_claim_id: string; unit_statement: string;
    review_status: string;
  };
  claim: Record<string, unknown> & { claim_id: string; statement: string; attribution?: string; review_status: string };
  evidence: Array<{
    evidence_step: null | Record<string, unknown> & { evidence_step_id: string; statement: string; speaker?: string; stance?: string };
    source_fragment: null | Record<string, unknown> & { fragment_id: string; verbatim_excerpt: string };
    source: null | Record<string, unknown> & { source_id: string; title?: string };
    citations: Array<Record<string, unknown> & { citation_id: string; status: string }>;
    locator: {
      source_url: string | null; source_admin_url?: string | null;
      source_file_name?: string | null; source_type?: string | null;
      paragraph_key: string | number | null; media_time: number | null;
    };
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
    revision: null | Record<string, unknown> & {
      route_label: string;
      route_signature: { inference_method_codes: string[]; inference_method_note?: string | null };
      ordered_inference_nodes: Array<{
        route_step_key: string;
        role: "observation" | "premise" | "bridge" | "objection" | "response" | "qualification" | "conclusion" | "application";
        normalized_proposition: string | null;
        conclusion_viewpoint_revision_id: string | null;
        required_for_full_attestation: boolean;
      }>;
    };
    attestations: Array<{
      attestation: Record<string, unknown> & {
        argument_route_attestation_id: string; completeness: "full" | "partial";
        review_status: string; source_id: string;
      };
      source: null | Record<string, unknown> & {
        source_id: string; title?: string; source_type?: string; source_path?: string;
      };
      bindings: Array<{
        binding: {
          route_step_key: string; claim_component_keys: string[];
          attestation_status: "attested" | "missing" | "ambiguous";
        };
        node: null | {
          route_step_key: string; role: string; normalized_proposition: string | null;
        };
        evidence: Array<{
          evidence_step: null | Record<string, unknown> & {
            evidence_step_id: string; statement: string; speaker?: string; stance?: string;
          };
          fragments: Array<{
            source_fragment: Record<string, unknown> & {
              fragment_id: string; verbatim_excerpt: string;
            };
            locator: {
              source_url: string | null; source_admin_url: string | null;
              source_file_name: string | null; source_type: string | null;
              paragraph_key: string | number | null; media_time: number | null;
            };
          }>;
        }>;
      }>;
    }>;
    snapshot: null | Record<string, unknown> & { eligibility: string; full_attestation_count: number; partial_attestation_count: number };
    coverage: {
      mode: "coverage_snapshot" | "current_registry"; eligibility: string;
      full_attestation_count: number; partial_attestation_count: number;
    };
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

export type ViewpointPilot = {
  viewpoint_candidate_id: string;
  viewpoint_revision_candidate_id: string;
  core_proposition: string;
  wording_label: string;
  review_status: string;
  consumer_eligibility: "internal_candidate";
  scope: { scripture_scope: string[] };
  members: Array<{
    proposition_unit: {
      proposition_unit_id: string; parent_claim_id: string; source_id: string;
      unit_statement: string;
      evidence: Array<{
        evidence_step_id: string; source_fragment_id: string; verbatim_excerpt: string;
        source_id: string; media_time: number | null; paragraph_key: string | number | null;
      }>;
    };
    parent_claim: { claim_id: string; statement: string; source_id: string; review_status: string };
  }>;
  adjacent_non_members: Array<{
    proposition_unit_id: string; parent_claim_id: string; unit_statement: string;
    disposition: "adjacent_non_member"; reason: "different_truth_condition";
  }>;
  article_acceptance: {
    draft_id: string; manuscript_sha256: string; article_proposition: string;
    status: "supported"; article_is_source_authority: false; supporting_proposition_unit_ids: string[];
  };
  model_ids: string[];
  blockers: string[];
  artifact_sha256: string;
  apply_allowed: false;
};

export type PilotEnvelope = {
  schema_version: string;
  authority: { kind: string; projection: string; representation: string; read_only: true };
  projection_sha256: string;
  consumer_projection: {
    consumer_kind: "composition_plan"; eligibility: "internal_candidate";
    projection_sha256: string; blocker_codes: string[];
  };
  knowledge_classification: {
    schema_version: "wang_viewpoint_knowledge_classification_v1";
    knowledge_role: "passage_interpretation";
    processing_phase: "passage_exegesis";
    scripture_scope: string[];
    policy_version: "matthew16_pilot_classification_v1";
    basis_fields: string[];
  };
  promotion: null | {
    schema_version: "wang_matthew16_viewpoint_promotion_proposal_v1";
    artifact_sha256: string;
    canonical_viewpoint: { viewpoint_id: string; review_status: string };
    proposition_units: Array<{ proposition_unit_id: string; effective_state: "proposed" }>;
    proposition_unit_links: Array<{ proposition_unit_id: string; effective_state: "proposed" }>;
    excluded_proposition_unit_ids: string[];
    quality_checks: Array<{ code: string; status: "pass"; detail: string }>;
    blockers: string[];
    claim_membership_link_count: 0;
    master_data_mutations: 0;
    apply_allowed: false;
  };
  finalization: null | {
    schema_version: "wang_matthew16_viewpoint_finalization_bundle_v1";
    artifact_sha256: string;
    master_data_mutation_count: number;
    apply_allowed: true;
    atomic_coverage_snapshot: {
      atomic_coverage_snapshot_id: string; coverage_status: "complete";
      proposition_unit_ids: string[];
    };
    atomic_resolution_ledger: {
      atomic_resolution_ledger_id: string; coverage_status: "complete";
      statistics: {
        input_unit_count: number; member_count: number;
        adjacent_non_member_count: number; unresolved_count: number;
      };
    };
    atomic_quality_report: {
      atomic_quality_report_id: string; eligibility_decision: "pass" | "fail";
      checks: Array<{ code: string; status: "pass" | "fail"; detail: string }>;
    };
    automated_promotion_decision: {
      automated_promotion_decision_id: string; decision: "approve" | "reject";
      approval_basis: "programmatic_atomic_quality_gate"; human_approval: false;
    };
    canonical_viewpoint: { viewpoint_id: string; review_status: string };
  };
  master_application: null | {
    change_set_id: string; status: "applied" | "not_applied";
    operation_count: number; unchanged_count: number;
  };
  source_files: Record<string, {
    source_id: string; title: string; source_type: string; file_name: string;
  }>;
  source_files_sha256: string;
  data: ViewpointPilot;
};
