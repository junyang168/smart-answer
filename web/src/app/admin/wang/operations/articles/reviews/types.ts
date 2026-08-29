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
  stage_checks: ReviewStageCheck[];
  href: string;
};

export type TopicEssayReview = TopicEssayReviewSummary & {
  markdown: string;
  source_annotations: ReviewSourceAnnotation[];
};

export type ReviewSourceMedia = {
  kind: "audio" | "video";
  url: string;
  start_seconds: number | null;
  end_seconds: number | null;
};

export type ReviewSourceFragment = {
  fragment_ids: string[];
  source_type: "sermon_transcript" | "notes_manuscript";
  title: string;
  excerpts: string[];
  full_source_url: string | null;
  media: ReviewSourceMedia | null;
  mapping_kind: "argument_route_attestation" | "claim_evidence";
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
