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
};

export type TopicEssayReviewList = {
  schema_version: string;
  reviews: TopicEssayReviewSummary[];
  warnings: { manifest: string; message: string }[];
};
