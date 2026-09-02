export type ReviewStageState = "complete" | "passed" | "failed" | "not_run";

export type ReviewStageCheck = {
  id: string;
  label: string;
  state: ReviewStageState;
};

export type TopicEssayReviewSummary = {
  review_id: string;
  title: string;
  passage: string;
  registered_at: string;
  status: "internal_review";
  integrity_status: "verified" | "changed";
  manuscript_sha256: string;
  brief_sha256: string;
  authoring_packet_sha256: string;
  workflow_status: string;
  publication_decision?: { decision?: string; public_slug?: string } | null;
  stage_checks: ReviewStageCheck[];
  href: string;
};

export type TopicEssayReview = TopicEssayReviewSummary & {
  markdown: string;
  source_annotations: ReviewSourceAnnotation[];
  source_projection_audit: ReviewSourceProjectionAudit;
  source_playback_audit: ReviewSourcePlaybackAudit;
};

export type ReviewSourceProjectionFinding = {
  code: string;
  paragraph_id: string;
  message: string;
};

export type ReviewSourceProjectionAudit = {
  schema_version: "wang_article_source_projection_audit.v1";
  manuscript_sha256: string;
  authoring_packet_sha256: string;
  paragraphs_checked: number;
  paragraphs_with_sources: number;
  direct_quotes_checked: number;
  findings: ReviewSourceProjectionFinding[];
  passed: boolean;
};

export type ReviewSourcePlaybackFinding = {
  code: string;
  severity: "error" | "warning";
  paragraph_id: string;
  fragment_ids: string[];
  message: string;
};

export type ReviewSourcePlaybackAudit = {
  schema_version: "wang_article_source_playback_audit.v1";
  manuscript_sha256: string;
  authoring_packet_sha256: string;
  clips_checked: number;
  exact_clips: number;
  estimated_clips: number;
  paragraph_fallback_clips: number;
  findings: ReviewSourcePlaybackFinding[];
  passed: boolean;
};

export type ReviewSourceMedia = {
  kind: "audio" | "video";
  url: string;
  start_seconds: number | null;
  end_seconds: number | null;
  excerpt_start_seconds: number | null;
  excerpt_end_seconds: number | null;
  paragraph_start_seconds: number | null;
  paragraph_end_seconds: number | null;
  timing_status: "exact" | "estimated" | "unresolved" | "paragraph_fallback";
  timing_method: string;
  timing_match_ratio: number | null;
  reviewed_text_differs_from_raw: boolean | null;
  lineage_window_expanded: boolean;
  timing_alignment_sha256: string | null;
};

export type ReviewSourceFragment = {
  fragment_ids: string[];
  source_type: "sermon_transcript" | "notes_manuscript";
  title: string;
  excerpts: string[];
  full_source_url: string | null;
  media: ReviewSourceMedia | null;
  mapping_kind:
    | "argument_route_attestation"
    | "claim_evidence"
    | "original_exact_quote"
    | "source_original_context";
  claim_ids: string[];
  evidence_step_ids: string[];
  route_revision_id: string | null;
  route_label: string | null;
  route_steps: ReviewRouteStep[];
};

export type ReviewRouteStep = {
  route_step_key: string;
  role: string;
  proposition: string | null;
  fragment_ids: string[];
  excerpts: string[];
  media_clips: ReviewSourceMedia[];
};

export type ReviewSourceAnnotation = {
  annotation_id: string;
  sources: ReviewSourceFragment[];
};

export type TopicEssayReviewList = {
  schema_version: string;
  reviews: TopicEssayReviewSummary[];
  warnings: { manifest: string; message: string }[];
};
