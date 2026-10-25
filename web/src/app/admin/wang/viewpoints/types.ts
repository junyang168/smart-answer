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
  structure_role?: string | null;
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
  structures?: Array<{
    structure_id: string; structure_revision_id: string; central_synthesis: string;
    structure_role: string; focal_count: number; unresolved_items: string[];
  }>;
  members: Member[];
  observation_supports?: Array<{
    current_registry_ready: boolean;
    observation_support: {
      observation_support_id: string; observation_summary: string; supports: string;
      support_scope_note: string; source_id: string; review_status: string;
    };
    bindings: Array<{
      binding: { claim_id: string; evidence_step_id: string; source_fragment_id: string };
      source_fragment: null | { verbatim_excerpt: string; paragraph_key?: string };
      source: null | { title?: string };
      locator: { source_admin_url: string | null; source_url: string | null; source_type: string | null };
    }>;
  }>;
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
    direction: "outgoing" | "incoming";
    counterpart_viewpoint_id: string | null; counterpart_core_proposition: string | null;
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
