"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Bot,
  BookOpenCheck,
  CheckCircle2,
  ChevronRight,
  Database,
  ExternalLink,
  FileQuestion,
  FlaskConical,
  GitMerge,
  Loader2,
  RotateCcw,
  Search,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { CitationMediaPlayer } from "@/app/components/canonical-repository/CitationMediaPlayer";

type ReviewStatus = "candidate" | "approved" | "changes_requested" | "rejected";
type Attention =
  | "human_required"
  | "human_spot_check"
  | "pending_ai_review"
  | "pending_evidence_review"
  | "pending_ai"
  | "ai_cleared"
  | "resolved";
type Tab = "knowledge" | "synthesis" | "qa" | "validation";
type Review = {
  status: ReviewStatus;
  note: string;
  reviewer: string;
  reviewed_at: string | null;
  revision?: number;
};
type AiIssue = {
  issue_type: string;
  severity: string;
  explanation: string;
};
type AiReview = {
  decision: "pass" | "changes_suggested" | "human_review_required";
  routing_status: string;
  spot_check_selected: boolean;
  issues: AiIssue[];
  rationale: string;
  confidence: string;
  human_review_reason: string;
  reviewed_at: string | null;
  adjudication: {
    status: string;
    openai_decision: string;
    openai_rationale: string;
    reconsideration_decision?: string | null;
    reconsideration_rationale?: string;
    structural_notes: string[];
    approval_status: string;
  } | null;
};
type ClaimSummary = {
  claim_id: string;
  title: string;
  claim_type: string;
  scripture_refs: string[];
  lectures: string[];
  recurrence: number;
  cross_lecture?: string | null;
  review: Review;
  ai_review?: AiReview | null;
  attention?: Attention;
  attention_reason?: string;
};
type Synthesis = {
  synthesis_id: string;
  synthesis_type: string;
  title: string;
  description: string;
  claim_ids: string[];
  claim_titles: string[];
  corpus_scope: string;
  validation_only: boolean;
  review: Review;
};
type Experiment = {
  experiment_id: string;
  product_type: string;
  title: string;
  question: string;
  acceptance_criteria: string[];
  status: string;
  product_plan_id?: string;
};
type Decision = {
  decision_id: string;
  passage: string;
  section_title: string;
  action: string;
  decision: string;
  rationale: string;
  claim_ids: string[];
  coverage: string;
  plan_id?: string;
  plan_title?: string;
  axis?: string;
  review: Review;
  ai_review?: CompositionAiReview | null;
};
type CompositionAiReview = {
  decision: "pass" | "changes_suggested" | "human_review_required";
  issues: AiIssue[];
  rationale: string;
  confidence: string;
  human_review_reason: string;
  outcome: string;
  openai?: {
    decision: "accept" | "reject";
    rationale: string;
  } | null;
  claude_reconsideration?: {
    decision: "withdraw" | "maintain";
    rationale: string;
  } | null;
  reviewed_at?: string | null;
};
type ArgumentLayerAssessment = {
  summary: string;
  argument_layer_status: "solid" | "usable_with_gaps" | "not_solid";
  argument_layer_findings: {
    finding_type: string;
    severity: string;
    explanation: string;
    claim_ids: string[];
    relation_ids: string[];
    recommended_action: string;
  }[];
  systemic_risks: string[];
  reviewed_at?: string | null;
};
type CompositionPlan = {
  plan_id: string;
  title: string;
  description: string;
  axis: string;
  product_type: string;
  decision_ids: string[];
  counts: Record<ReviewStatus, number>;
  ai_assessment?: ArgumentLayerAssessment | null;
};
type Workspace = {
  title: string;
  subtitle: string;
  pilot: {
    title: string;
    corpus_scope: {
      description: string;
      warning: string;
      completeness: string;
      source_count: number;
    };
    counts: Record<string, number>;
  };
  claims: ClaimSummary[];
  claim_counts: Record<ReviewStatus, number>;
  ai_review: {
    available: boolean;
    reviewed_at: string | null;
    spot_check_percent: number | null;
    counts: Record<Attention, number>;
  };
  synthesis: {
    items: Synthesis[];
    counts: Record<ReviewStatus, number>;
    open_questions: {
      question_id: string;
      question: string;
      answer_state: string;
      note?: string;
    }[];
  };
  validation: {
    experiments: Experiment[];
    tensions: {
      tension_id: string;
      question: string;
      status: string;
      claim_ids: string[];
    }[];
    editorial_checks: {
      check_id: string;
      title: string;
      note: string;
      status: string;
    }[];
  };
  composition: {
    plans: CompositionPlan[];
    decisions: Decision[];
    counts: Record<ReviewStatus, number>;
  };
  qa: {
    plan_id: string;
    title: string;
    description: string;
    corpus_scope: string;
    counts: Record<"answered" | "partially_answered" | "unanswered", number>;
    cases: QaCaseSummary[];
    diagnostics: QaDiagnosticsSummary;
  };
  authoring_store: {
    backend: "postgresql" | "json_fallback";
    database_connected: boolean;
    objects?: Record<string, number>;
    review_counts?: Record<string, Record<string, number>>;
    latest_change_set?: {
      change_set_id: string;
      source_kind: string;
      applied_at: string;
    } | null;
    active_snapshot?: {
      build_id: string;
      generated_at: string;
      snapshot_sha256: string;
      counts: Record<string, number>;
    } | null;
    error?: string;
  };
};
type QaDiagnosticsSummary = {
  available: boolean;
  stale: boolean;
  summary: Record<string, number>;
  models: Record<string, string>;
};
type QaDiagnosticOutcome = {
  issue_id: string;
  issue_type: string;
  earliest_error_layer: string;
  status: string;
  explanation: string;
  repair_action: string;
  answer_excerpt?: string | null;
  recommended_action?: string | null;
};
type QaDiagnostics = {
  available: boolean;
  stale: boolean;
  generated_at?: string;
  models?: Record<string, string>;
  review?: {
    decision: string;
    answer_state_assessment: string;
    rationale: string;
    confidence: string;
  } | null;
  outcomes?: QaDiagnosticOutcome[];
  repairs?: {
    repair_id: string;
    target_layer: string;
    target: string;
    verification: string;
    action: string;
    status: string;
  }[];
  human_required?: boolean;
};
type QaAnswerState = "answered" | "partially_answered" | "unanswered";
type QaCaseSummary = {
  case_id: string;
  case_type: string;
  question: string;
  answer_state: QaAnswerState;
  answer_claim_count: number;
  source_question_count: number;
  human_required?: boolean;
};
type QaCaseDetail = {
  plan: {
    plan_id: string;
    title: string;
    description: string;
    corpus_scope: string;
  };
  case: QaCaseSummary & {
    answer_summary: string;
    full_answer_sections?: {
      heading: string;
      section_type: "answer" | "background" | "boundary";
      paragraphs: string[];
      claim_ids: string[];
    }[];
    limitation: string;
    attribution_trap?: string;
  };
  answer_claims: {
    claim_id: string;
    title: string;
    claim_type: string;
    scripture_refs: string[];
    evidence: Evidence[];
    eligible_evidence_count: number;
    warnings: string[];
  }[];
  context_claims: {
    claim_id: string;
    title: string;
    claim_type: string;
    scripture_refs: string[];
  }[];
  source_questions: {
    question_id: string;
    text: string;
    questioner?: string | null;
    answer_state?: string | null;
    answer_state_origin?: string | null;
    argument_link_state?: string | null;
    answered_subquestions?: string[];
    unanswered_subquestions?: string[];
    answer_state_note?: string;
    source_fragments: {
      fragment_id: string;
      lecture?: string;
      verbatim_excerpt?: string;
      source_url?: string;
      media_time?: number | null;
    }[];
  }[];
  opposed_positions: {
    position_id: string;
    title?: string;
    statement?: string;
  }[];
  related_products: {
    plan_id: string;
    title: string;
    axis: string;
    product_type: string;
  }[];
  quality_warnings: string[];
  diagnostics: QaDiagnostics;
};
type Evidence = {
  id: string;
  lec: string;
  ty: string;
  full: string;
  q: string;
  scr: string[];
  qt?: number | null;
  source_url?: string | null;
  speaker?: string;
  stance?: string;
  discourse_role?: string;
  anchor_quality?: string;
  support_eligibility?: string;
  review_note?: string;
  anchor_origin?: string;
};
type ClaimRelation = {
  relation_type: string;
  source_id: string;
  target_id: string;
  source_title?: string;
  target_title?: string;
  reason?: string;
};
type ClaimRelationConstraint = {
  constraint_id: string;
  source_id: string;
  target_id: string;
  source_title?: string;
  target_title?: string;
  forbidden_relation_types: string[];
  composition_role?: string;
  reason?: string;
};
type ClaimDetail = {
  claim: ClaimSummary & { opposes?: string };
  review: Review;
  ai_review?: AiReview | null;
  attention: Attention;
  attention_reason: string;
  evidence: Evidence[];
  candidate_evidence: Evidence[];
  context_evidence: Evidence[];
  withheld_evidence: Evidence[];
  relations: { relation_type: string }[];
  claim_relations: ClaimRelation[];
  claim_relation_constraints: ClaimRelationConstraint[];
  knowledge_routes: {
    route_id: string;
    route_type: string;
    target_id: string;
    decision_ids: string[];
    axis?: string;
    route_type_label?: string;
    target_label?: string;
    candidate_href?: string | null;
  }[];
  review_gate: {
    can_approve: boolean;
    eligible_evidence_count: number;
    warnings: string[];
  };
};
type SynthesisDetail = {
  synthesis: Synthesis;
  review: Review;
  linked_claims: (ClaimSummary & { cross_lecture?: string | null })[];
  corpus_scope: { warning: string };
};
type CompositionDetail = {
  plan: Pick<CompositionPlan, "plan_id" | "title" | "axis" | "product_type">;
  decision: Decision;
  review: Review;
  ai_review?: CompositionAiReview | null;
  argument_layer_assessment?: ArgumentLayerAssessment | null;
  linked_claims: Pick<
    ClaimSummary,
    "claim_id" | "title" | "claim_type" | "scripture_refs"
  >[];
  claim_hierarchy?: ClaimHierarchyData | null;
  source_leads: {
    source_lead_id: string;
    transcript_id: string;
    title: string;
    summary: string;
    scripture_refs: string[];
    evidence_maturity: string;
    priority: string;
  }[];
  source_presentations: {
    presentation_id: string;
    source_id: string;
    transcript_id?: string;
    source_title?: string;
    start_seconds: number;
    end_seconds: number;
    duration_seconds: number;
    label: string;
    claim_ids: string[];
    source?: {
      source_type: string;
      public_url: string;
      media?: {
        kind?: "audio" | "video" | "unknown";
        url?: string | null;
      };
    } | null;
  }[];
  source_presentation_summary?: {
    mode: "continuous" | "segment_group" | "unavailable";
    status: string;
    mapped_claim_ids: string[];
    unmapped_claim_ids: string[];
    note: string;
  } | null;
};

type EvidenceStepScope = {
  claim_id: string;
  evidence_step_ids: string[];
};

type ClaimHierarchyData = Record<
  string,
  string | string[] | EvidenceStepScope[]
>;

const statusMeta: Record<
  ReviewStatus,
  { label: string; className: string; icon: typeof CheckCircle2 }
> = {
  candidate: {
    label: "待審核",
    className: "bg-amber-50 text-amber-800 ring-amber-200",
    icon: AlertCircle,
  },
  approved: {
    label: "已批准",
    className: "bg-emerald-50 text-emerald-800 ring-emerald-200",
    icon: CheckCircle2,
  },
  changes_requested: {
    label: "需要修改",
    className: "bg-blue-50 text-blue-800 ring-blue-200",
    icon: RotateCcw,
  },
  rejected: {
    label: "不採用",
    className: "bg-rose-50 text-rose-800 ring-rose-200",
    icon: XCircle,
  },
};

const attentionMeta: Record<
  Attention,
  { label: string; short: string; className: string; icon: typeof CheckCircle2 }
> = {
  human_required: {
    label: "需人工處理",
    short: "需人工",
    className: "bg-rose-50 text-rose-800 ring-rose-200",
    icon: AlertCircle,
  },
  human_spot_check: {
    label: "抽查核對",
    short: "抽查",
    className: "bg-amber-50 text-amber-900 ring-amber-200",
    icon: ShieldCheck,
  },
  pending_ai_review: {
    label: "待 AI 複審",
    short: "待 AI 複審",
    className: "bg-violet-50 text-violet-800 ring-violet-200",
    icon: Bot,
  },
  pending_evidence_review: {
    label: "待證據審核",
    short: "待證據",
    className: "bg-sky-50 text-sky-800 ring-sky-200",
    icon: Database,
  },
  pending_ai: {
    label: "待 AI 仲裁",
    short: "待仲裁",
    className: "bg-slate-100 text-slate-700 ring-slate-200",
    icon: Loader2,
  },
  ai_cleared: {
    label: "AI 已複審",
    short: "已複審",
    className: "bg-emerald-50 text-emerald-800 ring-emerald-200",
    icon: Bot,
  },
  resolved: {
    label: "已人工處理",
    short: "已處理",
    className: "bg-indigo-50 text-indigo-800 ring-indigo-200",
    icon: CheckCircle2,
  },
};

const issueTypeLabels: Record<string, string> = {
  speaker_attribution: "說話者歸屬",
  opponent_as_professor: "把反方當作教授立場",
  audience_as_evidence: "把聽眾發言當證據",
  unsupported_claim: "來源不支持",
  insufficient_anchor: "錨點不足",
  missing_qualification: "遺漏限定",
  missing_scripture_evidence: "缺經文依據",
  claim_too_broad: "主張過寬",
  claim_should_split: "應拆分",
  duplicate_claim: "重複主張",
  relation_error: "關係標註錯誤",
  route_error: "產品路由錯誤",
  editorial_inference: "編輯推論",
  unresolved_tension: "未解決的張力",
  other: "其他",
};

const severityLabels: Record<string, string> = {
  low: "低",
  medium: "中",
  high: "高",
  critical: "極高",
};

const adjudicationLabels: Record<string, string> = {
  auto_applied: "兩模型一致，修正已寫入候選層",
  withdrawn: "Claude 看過反駁後撤回意見",
  human_confirmation_required: "兩模型一致，但原意見要求人工確認",
  human_disagreement_required: "兩模型持續分歧，等待人工裁決",
};

const synthesisLabels: Record<string, string> = {
  cross_source_claims: "跨講主張",
  topic_retrieval_lead: "全庫檢索線索",
  method_pattern_lead: "方法模式線索",
};
const actionLabels: Record<string, string> = {
  main_section: "主要段落",
  brief_note: "簡短說明",
  coverage_gap: "材料缺口",
  main_with_topic_link: "主要段落，並連結專題",
  background_appendix: "背景附錄",
  topic_link: "轉介專題",
  topic_main_section: "專題核心段落",
  topic_section_pending_scope: "專題段落，待定篇幅",
  thought_development_check: "思想發展檢查",
};

const evidenceMaturityLabels: Record<string, string> = {
  ai_consensus_detailed_claims: "已完成逐句整理與雙模型復審",
  detailed_shared_claims: "已有詳細論證資料",
  survey_claims_with_timecoded_anchors: "已有時間定位，待詳細整理",
  survey_lead: "普查線索，待回聽核實",
};

const evidenceRoleLabels: Record<string, string> = {
  dramatic_paraphrase: "教授的戲劇化轉述（非經文原句）",
  quoted_opponent: "教授轉述的反方立場",
  audience_prompt: "聽眾發言",
  question_context: "問題背景",
  ai_summary: "待核對的整理摘要",
};

const evidenceTypeLabels: Record<string, string> = {
  經文: "經文依據",
  经文: "經文依據",
};

const qaStateMeta: Record<
  QaAnswerState,
  { label: string; className: string }
> = {
  answered: {
    label: "現有資料足以回答",
    className: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  },
  partially_answered: {
    label: "現有資料只能部分回答",
    className: "bg-amber-50 text-amber-900 ring-amber-200",
  },
  unanswered: {
    label: "現有語料未回答",
    className: "bg-rose-50 text-rose-800 ring-rose-200",
  },
};

// The case file is hand-authored, so an unexpected state must degrade to a
// label rather than take the page down.
function qaState(state: string | null | undefined) {
  return (
    qaStateMeta[state as QaAnswerState] ?? {
      label: state ? `未知狀態：${state}` : "未標示狀態",
      className: "bg-slate-100 text-slate-700 ring-slate-300",
    }
  );
}

const qaErrorLayerLabels: Record<string, string> = {
  code_projection: "系統組合答案時出了問題",
  knowledge_data: "共享知識中的資料需要修正",
  generation_prompt: "答案生成規則需要調整",
  source_gap: "現有講道材料不足",
  uncertain: "兩個模型尚未確定原因",
};

const qaIssueTypeLabels: Record<string, string> = {
  unsupported_sentence: "這句答案缺少足夠的原文依據",
  overstated_answer: "答案說得比現有證據更確定",
  missing_qualification: "答案缺少必要說明",
  attribution_error: "引用可能被誤認為教授自己的主張",
  relation_error: "主張之間的關係可能標錯",
  retrieval_or_projection_error: "系統取用或顯示了不合適的資料",
  unanswered_gap: "現有講道沒有完整回答這個問題",
  duplicate_or_irrelevant_material: "答案包含重複或無關內容",
  other: "答案需要進一步檢查",
};

const qaOutcomeMeta: Record<string, { label: string; className: string }> = {
  ai_consensus_issue: {
    label: "兩個模型同意修正",
    className: "bg-amber-100 text-amber-900",
  },
  human_diagnostic_required: {
    label: "需要同工判斷",
    className: "bg-rose-100 text-rose-900",
  },
  withdrawn: {
    label: "複核後已撤回",
    className: "bg-slate-100 text-slate-700",
  },
};

function qaPlainLanguageResult(issue: QaDiagnosticOutcome) {
  if (issue.status === "withdrawn") {
    return "OpenAI 認為這不構成答案錯誤；Claude 再次檢查後撤回意見，不需要處理。";
  }
  if (issue.status === "human_diagnostic_required") {
    return "Claude 與 OpenAI 對這一點仍有不同判斷。請同工只判斷這一項，不需要重審整篇答案。";
  }
  const actions: Record<string, string> = {
    code_projection: "修正系統取用和組合資料的方式，然後重新生成答案。",
    knowledge_data: "修正共享知識中的主張、關係或原文引用，然後重新生成答案。",
    generation_prompt: "調整答案生成規則，然後重新生成答案。",
    source_gap: "補找相關講道原文；若仍找不到，就把答案改成「目前只能部分回答」。",
    uncertain: "先進一步檢查原因，暫時不要採用這項判斷。",
  };
  return actions[issue.earliest_error_layer] ?? "修正上游資料後重新生成答案。";
}

function EvidenceCard({
  evidence,
  muted = false,
}: {
  evidence: Evidence;
  muted?: boolean;
}) {
  return (
    <div
      className={`rounded-xl border p-4 ${muted ? "border-amber-200 bg-amber-50/50" : "border-slate-200"}`}
    >
      <div className="flex flex-wrap justify-between gap-2">
        <div className="flex flex-wrap gap-2">
          <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-bold">
            {evidenceTypeLabels[evidence.ty] ?? evidence.ty}
          </span>
          {evidence.discourse_role &&
            evidenceRoleLabels[evidence.discourse_role] && (
              <span className="rounded-full bg-violet-50 px-2.5 py-1 text-xs font-semibold text-violet-800">
                {evidenceRoleLabels[evidence.discourse_role]}
              </span>
            )}
          {evidence.support_eligibility === "withheld_ai_consensus" && (
            <span className="rounded-full bg-rose-50 px-2.5 py-1 text-xs font-semibold text-rose-800">
              AI 一致排除，不計入證據
            </span>
          )}
          {evidence.anchor_origin === "ai_consensus_adjudication" && (
            <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-800">
              AI 仲裁補入的來源
            </span>
          )}
        </div>
        <span className="text-xs text-slate-500">{evidence.lec}</span>
      </div>
      <p className="mt-3 font-semibold leading-7">{evidence.full}</p>
      {evidence.q && (
        <blockquote className="mt-3 border-l-4 border-indigo-200 pl-4 leading-7 text-slate-600">
          「{evidence.q}」
        </blockquote>
      )}
      {evidence.review_note && (
        <p className="mt-3 rounded-lg bg-white/80 px-3 py-2 text-sm leading-6 text-amber-900">
          {evidence.review_note}
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {(evidence.scr ?? []).map((ref) => (
          <span key={ref} className="text-sm text-sky-700">
            {ref}
          </span>
        ))}
        {evidence.source_url && (
          <a
            href={evidence.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto inline-flex items-center gap-1 text-sm font-semibold text-indigo-700"
          >
            聽原始講道
            <ExternalLink className="h-4 w-4" />
          </a>
        )}
      </div>
    </div>
  );
}

const hierarchyLabels: Record<string, string> = {
  paragraph_thesis: "段落主旨",
  supporting_claims: "支持論據",
  corroborating_claims: "跨講印證",
  conclusion: "段落結論",
  immediate_explanation: "直接解釋",
  theological_ground: "神學根據",
  evidence_step_scopes: "各主張採用的證據範圍",
  note: "編排說明",
};

function isEvidenceStepScope(value: unknown): value is EvidenceStepScope {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<EvidenceStepScope>;
  return (
    typeof candidate.claim_id === "string" &&
    Array.isArray(candidate.evidence_step_ids) &&
    candidate.evidence_step_ids.every((id) => typeof id === "string")
  );
}

function ClaimHierarchy({
  hierarchy,
  claims,
}: {
  hierarchy: ClaimHierarchyData;
  claims: CompositionDetail["linked_claims"];
}) {
  const titleById = new Map(
    claims.map((claim) => [claim.claim_id, claim.title]),
  );
  return (
    <div className="mt-4 space-y-3">
      {Object.entries(hierarchy).map(([role, raw]) => {
        const scopes = Array.isArray(raw)
          ? raw.filter(isEvidenceStepScope)
          : [];
        const values = Array.isArray(raw)
          ? raw.filter((value): value is string => typeof value === "string")
          : typeof raw === "string"
            ? [raw]
            : [];
        return (
          <div
            key={role}
            className="rounded-xl border border-indigo-100 bg-indigo-50/40 p-4"
          >
            <strong className="text-sm text-indigo-800">
              {hierarchyLabels[role] ?? role}
            </strong>
            <div className="mt-2 space-y-1">
              {scopes.map((scope) => (
                <div
                  key={`${scope.claim_id}-${scope.evidence_step_ids.join("-")}`}
                  className="rounded-lg bg-white/70 px-3 py-2"
                >
                  <p className="font-medium leading-6">
                    {titleById.get(scope.claim_id) ?? scope.claim_id}
                  </p>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    採用 {scope.evidence_step_ids.length} 條證據：
                    {scope.evidence_step_ids.join("、")}
                  </p>
                </div>
              ))}
              {values.map((value) => (
                <p key={value} className="leading-6">
                  {titleById.get(value) ?? value}
                </p>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function StatusBadge({ status }: { status: ReviewStatus }) {
  const meta = statusMeta[status];
  const Icon = meta.icon;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset ${meta.className}`}
    >
      <Icon className="h-3.5 w-3.5" />
      {meta.label}
    </span>
  );
}

function AttentionBadge({ attention }: { attention: Attention }) {
  const meta = attentionMeta[attention];
  const Icon = meta.icon;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset ${meta.className}`}
    >
      <Icon className="h-3.5 w-3.5" />
      {meta.label}
    </span>
  );
}

function AiReviewPanel({
  aiReview,
  attention,
  attentionReason,
}: {
  aiReview?: AiReview | null;
  attention: Attention;
  attentionReason: string;
}) {
  const adjudication = aiReview?.adjudication;
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <AttentionBadge attention={attention} />
        <h3 className="text-xl font-bold">AI 複審紀錄</h3>
      </div>
      <p className="mt-2 leading-7 text-slate-700">{attentionReason}</p>
      {!aiReview && (
        <p className="mt-4 rounded-xl bg-slate-50 p-4 text-sm leading-6 text-slate-600">
          這條主張已進入 AI 複審佇列；完成前不會要求同工逐條人工審核。
        </p>
      )}
      {aiReview && (
        <div className="mt-4 space-y-3 text-sm">
          <div className="rounded-xl bg-slate-50 p-4">
            <strong className="text-slate-900">
              第一輪（Claude 依完整逐字稿複核）
            </strong>
            <p className="mt-1 leading-6 text-slate-700">
              {aiReview.decision === "pass"
                ? "未發現來源忠實度問題。"
                : aiReview.rationale || "提出來源忠實度意見。"}
            </p>
            {!!aiReview.issues.length && (
              <ul className="mt-3 space-y-2">
                {aiReview.issues.map((issue, index) => (
                  <li
                    key={`${issue.issue_type}-${index}`}
                    className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-950"
                  >
                    <strong>
                      {issueTypeLabels[issue.issue_type] ?? issue.issue_type}
                    </strong>
                    <span className="ml-2 text-xs">
                      嚴重度：{severityLabels[issue.severity] ?? issue.severity}
                    </span>
                    <p className="mt-1 leading-6">{issue.explanation}</p>
                  </li>
                ))}
              </ul>
            )}
            {aiReview.human_review_reason && (
              <p className="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3 leading-6 text-rose-900">
                要求人工的理由：{aiReview.human_review_reason}
              </p>
            )}
          </div>
          {adjudication && (
            <div className="rounded-xl bg-slate-50 p-4">
              <strong className="text-slate-900">
                第二輪（OpenAI 依同一份逐字稿仲裁）
              </strong>
              <p className="mt-1 leading-6 text-slate-700">
                {adjudication.openai_decision === "accept"
                  ? "接受第一輪意見"
                  : "拒絕第一輪意見"}
                ：{adjudication.openai_rationale}
              </p>
              {adjudication.reconsideration_decision && (
                <p className="mt-2 leading-6 text-slate-700">
                  Claude 再審：
                  {adjudication.reconsideration_decision === "withdraw"
                    ? "撤回原意見"
                    : "仍維持原意見"}
                  {adjudication.reconsideration_rationale
                    ? `——${adjudication.reconsideration_rationale}`
                    : ""}
                </p>
              )}
              <p className="mt-3 font-semibold text-slate-900">
                {adjudicationLabels[adjudication.status] ?? adjudication.status}
              </p>
              {!!adjudication.structural_notes.length && (
                <ul className="mt-2 list-disc space-y-1 pl-5 text-slate-600">
                  {adjudication.structural_notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <p className="text-xs text-slate-500">
            兩個模型只檢查是否忠實呈現教授原意，不做神學批評，也不代表人工批准。
          </p>
        </div>
      )}
    </section>
  );
}

function Progress({ counts }: { counts: Record<ReviewStatus, number> }) {
  const total = Object.values(counts).reduce((sum, count) => sum + count, 0);
  const approved = counts.approved ?? 0;
  const percent = total ? Math.round((approved / total) * 100) : 0;
  return (
    <div>
      <div className="flex justify-between text-sm text-slate-600">
        <span>
          已批准 {approved} / {total}
        </span>
        <strong className="text-slate-900">{percent}%</strong>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200">
        <div
          className="h-full rounded-full bg-emerald-500"
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

function QueueProgress({
  counts,
  total,
}: {
  counts: Record<Attention, number>;
  total: number;
}) {
  const open = counts.human_required + counts.human_spot_check;
  const resolved = counts.resolved;
  const queue = open + resolved;
  const percent = queue ? Math.round((resolved / queue) * 100) : 100;
  return (
    <div>
      <div className="flex flex-wrap justify-between gap-2 text-sm text-slate-600">
        <span>
          <strong className="text-slate-900">
            {total} 條候選主張中，需要同工處理的有 {open} 條
          </strong>
          {counts.pending_ai_review > 0 && `（另有 ${counts.pending_ai_review} 條待 AI 複審）`}
          {counts.pending_evidence_review > 0 && `（${counts.pending_evidence_review} 條待證據審核）`}
          {counts.pending_ai > 0 && `（${counts.pending_ai} 條待 AI 仲裁）`}
        </span>
        <strong className="text-slate-900">
          人工佇列已處理 {resolved} / {queue}（{percent}%）
        </strong>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200">
        <div
          className="h-full rounded-full bg-emerald-500"
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

function ReviewBox({
  review,
  saving,
  approveDisabled = false,
  approveDisabledReason = "",
  optional = false,
  optionalReason = "",
  onSave,
}: {
  review: Review;
  saving: boolean;
  approveDisabled?: boolean;
  approveDisabledReason?: string;
  optional?: boolean;
  optionalReason?: string;
  onSave: (
    status: ReviewStatus,
    note: string,
    reviewer: string,
  ) => Promise<void>;
}) {
  const [note, setNote] = useState(review.note ?? "");
  const [reviewer, setReviewer] = useState(review.reviewer || "同工");
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    setNote(review.note ?? "");
    setReviewer(review.reviewer || "同工");
    setExpanded(false);
  }, [review]);
  if (optional && !expanded) {
    return (
      <section className="rounded-2xl border border-emerald-100 bg-emerald-50/60 p-5">
        <h3 className="text-lg font-bold text-slate-950">不需要逐條人工審核</h3>
        <p className="mt-1 text-sm leading-6 text-slate-700">
          {optionalReason}
          這一項不佔用同工的審核工作量；您仍然可以隨時覆核。
        </p>
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-4 rounded-xl border border-emerald-300 bg-white px-4 py-2.5 font-semibold text-emerald-800"
        >
          我仍要人工覆核
        </button>
      </section>
    );
  }
  return (
    <section className="rounded-2xl border border-indigo-100 bg-indigo-50/60 p-5">
      <h3 className="text-lg font-bold text-slate-950">您的審核意見</h3>
      <p className="mt-1 text-sm leading-6 text-slate-600">
        批准表示這一項可以在標明的語料範圍內使用；需要修改時請說明原因。
      </p>
      {approveDisabled && (
        <p className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-semibold text-rose-800">
          {approveDisabledReason}
        </p>
      )}
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        rows={3}
        placeholder="例如：歸組方向正確，但還需要檢索其他講道。"
        className="mt-4 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
      />
      <input
        value={reviewer}
        onChange={(e) => setReviewer(e.target.value)}
        className="mt-3 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 outline-none sm:w-64"
        aria-label="審核人"
      />
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          disabled={saving || approveDisabled}
          title={approveDisabled ? approveDisabledReason : undefined}
          onClick={() => onSave("approved", note, reviewer)}
          className="rounded-xl bg-emerald-600 px-4 py-2.5 font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          批准
        </button>
        <button
          disabled={saving}
          onClick={() => onSave("changes_requested", note, reviewer)}
          className="rounded-xl bg-blue-600 px-4 py-2.5 font-semibold text-white disabled:opacity-50"
        >
          需要修改
        </button>
        <button
          disabled={saving}
          onClick={() => onSave("rejected", note, reviewer)}
          className="rounded-xl border border-rose-300 bg-white px-4 py-2.5 font-semibold text-rose-700 disabled:opacity-50"
        >
          不採用
        </button>
        {saving && (
          <span className="inline-flex items-center gap-2 text-sm text-slate-600">
            <Loader2 className="h-4 w-4 animate-spin" />
            正在儲存
          </span>
        )}
      </div>
    </section>
  );
}

function Metric({ value, label }: { value?: number; label: string }) {
  return (
    <div className="rounded-xl bg-white/10 px-4 py-3">
      <strong className="block text-2xl">{value ?? 0}</strong>
      <span className="text-xs text-slate-300">{label}</span>
    </div>
  );
}

const HUMAN_QUEUE: Attention[] = ["human_required", "human_spot_check"];

function matchesAttention(
  claim: ClaimSummary,
  filter: Attention | "queue" | "all",
) {
  if (filter === "all") return true;
  const attention = claim.attention ?? "human_required";
  if (filter === "queue") return HUMAN_QUEUE.includes(attention);
  return attention === filter;
}

function firstQueuedClaim(workspace: Workspace) {
  const queued = workspace.claims.find((claim) =>
    HUMAN_QUEUE.includes(claim.attention ?? "human_required"),
  );
  return (queued ?? workspace.claims[0])?.claim_id ?? null;
}

export default function ThoughtReviewPage() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [tab, setTab] = useState<Tab>("knowledge");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<ReviewStatus | "all">("all");
  const [attentionFilter, setAttentionFilter] = useState<Attention | "queue" | "all">(
    "queue",
  );
  const [query, setQuery] = useState("");
  const [claimDetail, setClaimDetail] = useState<ClaimDetail | null>(null);
  const [synthesisDetail, setSynthesisDetail] =
    useState<SynthesisDetail | null>(null);
  const [compositionDetail, setCompositionDetail] =
    useState<CompositionDetail | null>(null);
  const [qaDetail, setQaDetail] = useState<QaCaseDetail | null>(null);
  const [workspaceRevision, setWorkspaceRevision] = useState(0);
  const [detailReloadKey, setDetailReloadKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [buildingSnapshot, setBuildingSnapshot] = useState(false);
  const [error, setError] = useState("");

  const loadWorkspace = useCallback(async () => {
    const response = await fetch("/api/admin/thought-review/workspace", {
      cache: "no-store",
    });
    if (!response.ok)
      throw new Error("無法載入驗證資料，請確認本地服務已經啟動。");
    const data: Workspace = await response.json();
    setWorkspace(data);
    setWorkspaceRevision((current) => current + 1);
    return data;
  }, []);

  useEffect(() => {
    loadWorkspace()
      .then((data) => {
        const params = new URLSearchParams(window.location.search);
        if (params.get("tab") === "validation") {
          setTab("validation");
          const plan = data.composition.plans.find(
            (item) => item.plan_id === params.get("plan"),
          );
          setSelectedId(plan?.decision_ids[0] ?? data.composition.decisions[0]?.decision_id ?? null);
          return;
        }
        setSelectedId(firstQueuedClaim(data));
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [loadWorkspace]);

  useEffect(() => {
    if (!selectedId || (tab === "validation" && !selectedId.startsWith("CD-")))
      return;
    const controller = new AbortController();
    const requestedTab = tab;
    const endpoint =
      requestedTab === "knowledge"
        ? `/api/admin/thought-review/claims/${selectedId}`
        : requestedTab === "synthesis"
          ? `/api/admin/thought-review/synthesis/${selectedId}`
          : requestedTab === "qa"
            ? `/api/admin/thought-review/qa/${selectedId}`
          : `/api/admin/thought-review/composition/${selectedId}`;
    if (requestedTab === "knowledge") setClaimDetail(null);
    else if (requestedTab === "synthesis") setSynthesisDetail(null);
    else if (requestedTab === "qa") setQaDetail(null);
    else setCompositionDetail(null);
    setDetailLoading(true);
    setError("");
    fetch(endpoint, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("無法載入詳細資料。");
        return response.json();
      })
      .then((data) => {
        if (requestedTab === "knowledge") setClaimDetail(data);
        else if (requestedTab === "synthesis") setSynthesisDetail(data);
        else if (requestedTab === "qa") setQaDetail(data);
        else setCompositionDetail(data);
      })
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [selectedId, tab, workspaceRevision, detailReloadKey]);

  const listItems = useMemo(() => {
    if (!workspace || (tab !== "knowledge" && tab !== "synthesis")) return [];
    const source =
      tab === "knowledge" ? workspace.claims : workspace.synthesis.items;
    return source.filter((item) => {
      if ("claim_id" in item) {
        if (!matchesAttention(item, attentionFilter)) return false;
      } else if (statusFilter !== "all" && item.review.status !== statusFilter) {
        return false;
      }
      const text =
        "claim_id" in item
          ? `${item.title} ${item.scripture_refs.join(" ")}`
          : `${item.title} ${item.description}`;
      return text.toLowerCase().includes(query.toLowerCase());
    });
  }, [workspace, tab, statusFilter, attentionFilter, query]);

  const activeCompositionPlan = useMemo(() => {
    if (!workspace || !selectedId?.startsWith("CD-")) return null;
    return (
      workspace.composition.plans.find((plan) =>
        plan.decision_ids.includes(selectedId),
      ) ?? null
    );
  }, [workspace, selectedId]);

  const activeCompositionDecisions = useMemo(() => {
    if (!workspace || !activeCompositionPlan) return [];
    const activeIds = new Set(activeCompositionPlan.decision_ids);
    return workspace.composition.decisions.filter((decision) =>
      activeIds.has(decision.decision_id),
    );
  }, [workspace, activeCompositionPlan]);

  function switchTab(next: Tab) {
    setTab(next);
    setQuery("");
    setStatusFilter("all");
    setAttentionFilter("queue");
    setClaimDetail(null);
    setSynthesisDetail(null);
    setCompositionDetail(null);
    setQaDetail(null);
    if (next === "knowledge")
      setSelectedId(workspace ? firstQueuedClaim(workspace) : null);
    else if (next === "synthesis")
      setSelectedId(workspace?.synthesis.items[0]?.synthesis_id ?? null);
    else if (next === "qa")
      setSelectedId(workspace?.qa.cases[0]?.case_id ?? null);
    else setSelectedId(null);
  }

  function firstDecisionForPlan(planId?: string) {
    if (!planId) return null;
    const plan = workspace?.composition.plans.find(
      (item) => item.plan_id === planId,
    );
    return plan?.decision_ids[0] ?? null;
  }

  async function saveReview(
    status: ReviewStatus,
    note: string,
    reviewer: string,
  ) {
    if (!selectedId) return;
    setSaving(true);
    setError("");
    const section =
      tab === "knowledge"
        ? "claims"
        : tab === "synthesis"
          ? "synthesis"
          : "composition";
    try {
      const response = await fetch(
        `/api/admin/thought-review/${section}/${selectedId}/review`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            status,
            note,
            reviewer,
            expected_revision:
              tab === "knowledge"
                ? claimDetail?.review.revision
                : tab === "synthesis"
                  ? synthesisDetail?.review.revision
                  : compositionDetail?.review.revision,
          }),
        },
      );
      if (!response.ok) throw new Error("審核意見儲存失敗。");
      const data = await response.json();
      if (tab === "knowledge")
        setClaimDetail((current) =>
          current ? { ...current, review: data.review } : current,
        );
      else if (tab === "synthesis")
        setSynthesisDetail((current) =>
          current ? { ...current, review: data.review } : current,
        );
      else
        setCompositionDetail((current) =>
          current ? { ...current, review: data.review } : current,
        );
      await loadWorkspace();
    } catch (e) {
      setError(e instanceof Error ? e.message : "儲存失敗");
    } finally {
      setSaving(false);
    }
  }

  async function rebuildActiveSnapshot() {
    setBuildingSnapshot(true);
    setError("");
    try {
      const response = await fetch(
        "/api/admin/thought-review/active-snapshot/compile",
        { method: "POST" },
      );
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        const detail = data?.detail;
        throw new Error(
          typeof detail === "string"
            ? detail
            : detail?.message || "無法建立 Active Snapshot。",
        );
      }
      await loadWorkspace();
    } catch (e) {
      setError(e instanceof Error ? e.message : "無法建立 Active Snapshot。");
    } finally {
      setBuildingSnapshot(false);
    }
  }

  if (loading)
    return (
      <div className="flex min-h-[60vh] items-center justify-center gap-3 text-slate-600">
        <Loader2 className="h-6 w-6 animate-spin" />
        正在準備驗證資料…
      </div>
    );
  if (!workspace)
    return (
      <div className="mx-auto mt-12 max-w-2xl rounded-2xl border border-rose-200 bg-rose-50 p-6 text-rose-800">
        {error || "沒有資料"}
      </div>
    );
  const counts =
    tab === "knowledge" ? workspace.claim_counts : workspace.synthesis.counts;

  return (
    <main className="min-h-screen bg-slate-50 py-8">
      <div className="mx-auto max-w-7xl px-4 sm:px-6">
        <div className="mb-4 flex flex-wrap items-center gap-4 text-sm font-semibold">
          <Link href="/admin/wang" className="text-indigo-700">← Wang 文庫總覽</Link>
          <Link href="/admin/wang/matthew-progress" className="text-slate-600 hover:text-indigo-700">馬太文章進度</Link>
          <Link href="/admin/thought-review/candidates" className="text-slate-600 hover:text-indigo-700">內容候選</Link>
        </div>
        <header className="rounded-3xl bg-slate-900 p-6 text-white shadow-sm sm:p-8">
          <p className="text-sm font-semibold text-indigo-300">
            王守仁教授思想知識平台 · 設計驗證
          </p>
          <h1 className="mt-2 text-3xl font-bold sm:text-4xl">
            {workspace.title}
          </h1>
          <p className="mt-3 max-w-3xl leading-7 text-slate-300">
            {workspace.subtitle}
          </p>
          <div className="mt-5 rounded-2xl border border-amber-400/30 bg-amber-300/10 p-4 text-sm leading-6 text-amber-100">
            <strong>目前範圍：</strong>
            {workspace.pilot.corpus_scope.description}{" "}
            {workspace.pilot.corpus_scope.warning}
          </div>
          <div
            className={`mt-4 flex flex-col gap-3 rounded-2xl border p-4 text-sm sm:flex-row sm:items-center sm:justify-between ${workspace.authoring_store.database_connected ? "border-emerald-400/30 bg-emerald-300/10 text-emerald-50" : "border-amber-400/30 bg-amber-300/10 text-amber-100"}`}
          >
            <div className="flex items-start gap-3">
              <Database className="mt-0.5 h-5 w-5 shrink-0" />
              <div>
                <strong className="block">
                  編輯主庫：
                  {workspace.authoring_store.database_connected
                    ? "PostgreSQL（已連線）"
                    : "舊 JSON（備援模式）"}
                </strong>
                {workspace.authoring_store.active_snapshot ? (
                  <span className="mt-1 block opacity-90">
                    對外讀取快照：{workspace.authoring_store.active_snapshot.build_id} · 已批准主張 {workspace.authoring_store.active_snapshot.counts.claims ?? 0} 條
                  </span>
                ) : (
                  <span className="mt-1 block opacity-90">
                    尚未建立對外讀取快照。
                  </span>
                )}
              </div>
            </div>
            <button
              type="button"
              onClick={rebuildActiveSnapshot}
              disabled={
                buildingSnapshot ||
                !workspace.authoring_store.database_connected
              }
              className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-white px-4 py-2.5 font-bold text-slate-900 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {buildingSnapshot ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RotateCcw className="h-4 w-4" />
              )}
              重建對外讀取快照
            </button>
          </div>
          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-5">
            <Metric value={workspace.pilot.counts.sources} label="讲道来源" />
            <Metric value={workspace.pilot.counts.fragments} label="原始片段" />
            <Metric value={workspace.pilot.counts.claims} label="候选主张" />
            <Metric value={workspace.pilot.counts.relations} label="论证关系" />
            <Metric value={workspace.pilot.counts.questions} label="问题记录" />
          </div>
          <div className="mt-6 grid gap-3 md:grid-cols-4">
            {(
              [
                [
                  "knowledge",
                  BookOpenCheck,
                  "共享知识",
                  "审核教授说了什么、怎样论证",
                ],
                [
                  "synthesis",
                  GitMerge,
                  "跨讲综合",
                  "审核重复、延伸和全库检索线索",
                ],
                [
                  "qa",
                  FileQuestion,
                  "問答驗證",
                  "獨立測試可回答、部分回答與未回答問題",
                ],
                [
                  "validation",
                  FlaskConical,
                  "设计验证",
                  "用多种产品检验同一知识结构",
                ],
              ] as const
            ).map(([id, Icon, title, description]) => (
              <button
                key={id}
                onClick={() => switchTab(id)}
                className={`flex items-center gap-3 rounded-2xl p-4 text-left transition ${tab === id ? "bg-white text-slate-950" : "bg-white/10 hover:bg-white/15"}`}
              >
                <Icon
                  className={`h-7 w-7 ${tab === id ? "text-indigo-600" : "text-indigo-300"}`}
                />
                <span>
                  <strong className="block text-lg">{title}</strong>
                  <span
                    className={`text-sm ${tab === id ? "text-slate-600" : "text-slate-300"}`}
                  >
                    {description}
                  </span>
                </span>
              </button>
            ))}
          </div>
          <div className="mt-4 flex justify-end">
            <Link href="/admin/thought-review/candidates" className="inline-flex items-center gap-2 rounded-xl bg-indigo-500 px-4 py-2.5 text-sm font-bold text-white transition hover:bg-indigo-400">
              查看釋經與專題候選<ChevronRight className="h-4 w-4" />
            </Link>
          </div>
        </header>

        {tab === "knowledge" && (
          <section className="mt-6 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <QueueProgress
              counts={workspace.ai_review.counts}
              total={workspace.claims.length}
            />
            <p className="mt-3 text-sm leading-6 text-slate-600">
              {workspace.ai_review.available
                ? `已完成的候選主張由獨立 AI 複審，必要時再由第二模型仲裁；人工佇列只收 AI 無法自行裁定的項目，以及 ${workspace.ai_review.spot_check_percent ?? 10}% 的隨機抽查。`
                : "AI 複審尚未執行；這些項目会进入自动处理队列，不会全部转成人工审核。"}
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {(
                [
                  "queue",
                  "human_required",
                  "human_spot_check",
                  "pending_ai_review",
                  "pending_evidence_review",
                  "pending_ai",
                  "ai_cleared",
                  "resolved",
                  "all",
                ] as const
              ).map((key) => {
                const label =
                  key === "queue"
                    ? `待處理 ${workspace.ai_review.counts.human_required + workspace.ai_review.counts.human_spot_check}`
                    : key === "all"
                      ? "全部"
                      : `${attentionMeta[key].short} ${workspace.ai_review.counts[key] ?? 0}`;
                return (
                  <button
                    key={key}
                    onClick={() => setAttentionFilter(key)}
                    className={`rounded-full px-3 py-1.5 text-sm font-semibold ${attentionFilter === key ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-700"}`}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </section>
        )}
        {tab === "synthesis" && (
          <section className="mt-6 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <Progress counts={counts} />
            <div className="mt-4 flex flex-wrap gap-2">
              {(
                [
                  "all",
                  "candidate",
                  "approved",
                  "changes_requested",
                  "rejected",
                ] as const
              ).map((status) => (
                <button
                  key={status}
                  onClick={() => setStatusFilter(status)}
                  className={`rounded-full px-3 py-1.5 text-sm font-semibold ${statusFilter === status ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-700"}`}
                >
                  {status === "all"
                    ? "全部"
                    : `${statusMeta[status].label} ${counts[status] ?? 0}`}
                </button>
              ))}
            </div>
          </section>
        )}
        {error && (
          <div className="mt-5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-rose-800">
            {error}
          </div>
        )}

        {(tab === "knowledge" || tab === "synthesis") && (
          <div className="mt-6 grid items-start gap-6 lg:grid-cols-[minmax(280px,.78fr)_minmax(0,1.7fr)]">
            <aside className="rounded-2xl border border-slate-200 bg-white shadow-sm lg:sticky lg:top-20">
              <div className="border-b border-slate-100 p-4">
                <label className="relative block">
                  <Search className="absolute left-3 top-3 h-5 w-5 text-slate-400" />
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="搜寻标题、主张或经文"
                    className="w-full rounded-xl border border-slate-300 py-2.5 pl-10 pr-3 outline-none"
                  />
                </label>
              </div>
              <div className="max-h-[70vh] overflow-y-auto p-2">
                {listItems.map((item) => {
                  const id =
                    "claim_id" in item ? item.claim_id : item.synthesis_id;
                  return (
                    <button
                      key={id}
                      onClick={() => {
                        if (selectedId === id) {
                          setDetailReloadKey((current) => current + 1);
                        } else {
                          setSelectedId(id);
                        }
                      }}
                      className={`mb-1 flex w-full items-start gap-3 rounded-xl p-3 text-left ${selectedId === id ? "bg-indigo-50 ring-1 ring-indigo-200" : "hover:bg-slate-50"}`}
                    >
                      <span className="min-w-0 flex-1">
                        <strong className="block leading-6 text-slate-900">
                          {item.title}
                        </strong>
                        {"synthesis_type" in item && (
                          <span className="mt-1 block text-xs text-slate-500">
                            {synthesisLabels[item.synthesis_type] ??
                              item.synthesis_type}
                          </span>
                        )}
                        <span className="mt-2 flex flex-wrap gap-2">
                          {"claim_id" in item ? (
                            <AttentionBadge
                              attention={item.attention ?? "human_required"}
                            />
                          ) : null}
                          {(!("claim_id" in item) ||
                            item.review.status !== "candidate") && (
                            <StatusBadge status={item.review.status} />
                          )}
                        </span>
                      </span>
                      <ChevronRight className="mt-1 h-5 w-5 text-slate-400" />
                    </button>
                  );
                })}
                {!listItems.length && (
                  <p className="p-4 text-sm leading-6 text-slate-600">
                    {tab === "knowledge" && attentionFilter === "queue"
                      ? "目前沒有需要人工處理的項目。AI 複審與仲裁已處理其餘主張，您可以切換到「已複審」查看。"
                      : "沒有符合條件的項目。"}
                  </p>
                )}
              </div>
            </aside>
            <article className="min-w-0 space-y-5">
              {detailLoading && (
                <div className="flex min-h-80 items-center justify-center rounded-2xl border bg-white">
                  <Loader2 className="h-6 w-6 animate-spin text-indigo-600" />
                </div>
              )}
              {!detailLoading && tab === "knowledge" && claimDetail && (
                <>
                  <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                    <div className="flex flex-wrap gap-2">
                      <AttentionBadge attention={claimDetail.attention} />
                      {claimDetail.review.status !== "candidate" && (
                        <StatusBadge status={claimDetail.review.status} />
                      )}
                      <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold">
                        {claimDetail.claim.claim_type}
                      </span>
                    </div>
                    <h2 className="mt-4 text-2xl font-bold">
                      {claimDetail.claim.title}
                    </h2>
                    <div className="mt-4 flex flex-wrap gap-2">
                      {claimDetail.claim.scripture_refs.map((ref) => (
                        <span
                          key={ref}
                          className="rounded-lg bg-sky-50 px-2.5 py-1 text-sm text-sky-800"
                        >
                          {ref}
                        </span>
                      ))}
                    </div>
                    <p className="mt-4 text-sm text-slate-600">
                      在本次材料中出現 {claimDetail.claim.recurrence}{" "}
                      次（僅表示頻率，不等於重要性） ·{" "}
                      {claimDetail.claim.lectures.join("、")}
                    </p>
                    {claimDetail.claim.opposes && (
                      <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-4">
                        <strong>教授所反駁的立場</strong>
                        <p className="mt-1 leading-7">
                          {claimDetail.claim.opposes}
                        </p>
                      </div>
                    )}
                    {!!claimDetail.knowledge_routes?.length && (
                      <div className="mt-5 rounded-xl border border-sky-200 bg-sky-50 p-4">
                        <strong>這條主張的後續去向</strong>
                        <div className="mt-3 space-y-3 text-sm text-sky-900">
                          {claimDetail.knowledge_routes.map((route) => (
                            <div key={route.route_id} className="rounded-lg border border-sky-200 bg-white/80 p-3">
                              <span className="text-xs font-bold text-sky-700">
                                {route.route_type_label ?? "後續整理"}
                              </span>
                              <p className="mt-1 font-semibold text-slate-900">
                                {route.target_label ?? route.target_id}
                              </p>
                              {route.candidate_href && (
                                <Link href={route.candidate_href} className="mt-2 inline-flex items-center gap-1 font-semibold text-indigo-700 hover:text-indigo-900">
                                  查看候選內容<ChevronRight className="h-4 w-4" />
                                </Link>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    {!!claimDetail.review_gate?.warnings?.length && (
                      <div className="mt-5 rounded-xl border border-amber-300 bg-amber-50 p-4 text-amber-950">
                        <strong>證據強度提醒</strong>
                        {claimDetail.review_gate.warnings.map((warning) => (
                          <p key={warning} className="mt-1 leading-6">
                            {warning}
                          </p>
                        ))}
                      </div>
                    )}
                  </section>
                  <AiReviewPanel
                    aiReview={claimDetail.ai_review}
                    attention={claimDetail.attention}
                    attentionReason={claimDetail.attention_reason}
                  />
                  <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                    <h3 className="text-xl font-bold">
                      可核查的論證與原始來源
                    </h3>
                    <p className="mt-1 text-sm text-slate-600">
                      只有說話者、立場與原始定位合格的材料，才能支持教授的主張。
                    </p>
                    <div className="mt-5 space-y-4">
                      {claimDetail.evidence.map((evidence) => (
                        <EvidenceCard key={evidence.id} evidence={evidence} />
                      ))}
                    </div>
                    {!claimDetail.evidence.length && (
                      <p className="mt-4 rounded-xl bg-slate-50 p-4 text-slate-600">
                        目前沒有通過來源資格檢查的證據。
                      </p>
                    )}
                    <div className="mt-5 rounded-xl bg-slate-50 p-4 text-sm text-slate-600">
                      證據支持關係{" "}
                      {
                        claimDetail.relations.filter(
                          (r) => r.relation_type === "supports",
                        ).length
                      }{" "}
                      條 · 問答關係{" "}
                      {
                        claimDetail.relations.filter(
                          (r) => r.relation_type === "answers",
                        ).length
                      }{" "}
                      條 · 主張間關係 {claimDetail.claim_relations.length} 條
                    </div>
                  </section>
                  {!!claimDetail.candidate_evidence?.length && (
                    <section className="rounded-2xl border border-sky-200 bg-sky-50/40 p-6 shadow-sm">
                      <h3 className="text-xl font-bold">待審核的可定位證據</h3>
                      <p className="mt-1 text-sm leading-6 text-slate-600">
                        這些原話已有逐字稿位置，但尚未通過證據資格審核；可供同工核對，暫不計入批准門檻。
                      </p>
                      <div className="mt-5 space-y-4">
                        {claimDetail.candidate_evidence.map((evidence) => (
                          <EvidenceCard
                            key={evidence.id}
                            evidence={evidence}
                            muted
                          />
                        ))}
                      </div>
                    </section>
                  )}
                  {!!claimDetail.context_evidence?.length && (
                    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                      <h3 className="text-xl font-bold">對話與反方背景</h3>
                      <p className="mt-1 text-sm text-slate-600">
                        以下材料幫助理解教授在回答誰、反駁什麼，但不作為教授主張的支持證據。
                      </p>
                      <div className="mt-5 space-y-4">
                        {claimDetail.context_evidence.map((evidence) => (
                          <EvidenceCard
                            key={evidence.id}
                            evidence={evidence}
                            muted
                          />
                        ))}
                      </div>
                    </section>
                  )}
                  {!!claimDetail.withheld_evidence?.length && (
                    <section className="rounded-2xl border border-amber-200 bg-amber-50/40 p-6 shadow-sm">
                      <h3 className="text-xl font-bold">待補來源或證據不足</h3>
                      <p className="mt-1 text-sm text-slate-600">
                        這些整理暫時保留，但在補回完整原話與時間定位前，不進入論證過程。
                      </p>
                      <div className="mt-5 space-y-4">
                        {claimDetail.withheld_evidence.map((evidence) => (
                          <EvidenceCard
                            key={evidence.id}
                            evidence={evidence}
                            muted
                          />
                        ))}
                      </div>
                    </section>
                  )}
                  {!!claimDetail.claim_relations?.length && (
                    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                      <h3 className="text-xl font-bold">主張之間的論證連接</h3>
                      <div className="mt-4 space-y-3">
                        {claimDetail.claim_relations.map((relation, index) => (
                          <div
                            key={`${relation.source_id}-${relation.target_id}-${index}`}
                            className="rounded-xl border p-4"
                          >
                            <span className="text-xs font-bold uppercase text-indigo-700">
                              {relation.relation_type}
                            </span>
                            <p className="mt-2 leading-7">
                              {relation.source_title} → {relation.target_title}
                            </p>
                          </div>
                        ))}
                      </div>
                    </section>
                  )}
                  {!!claimDetail.claim_relation_constraints?.length && (
                    <section className="rounded-2xl border border-amber-200 bg-amber-50 p-6 shadow-sm">
                      <h3 className="text-xl font-bold">論證結構約束</h3>
                      <p className="mt-2 text-sm leading-6 text-slate-600">
                        這些主張可以並列使用，但不得被誤寫成目前證據不支持的論證關係。
                      </p>
                      <div className="mt-4 space-y-3">
                        {claimDetail.claim_relation_constraints.map((constraint) => (
                          <div key={constraint.constraint_id} className="rounded-xl border border-amber-200 bg-white p-4">
                            <span className="text-xs font-bold uppercase text-amber-800">
                              禁止：{constraint.forbidden_relation_types.join("、")}
                              {constraint.composition_role ? ` · ${constraint.composition_role}` : ""}
                            </span>
                            <p className="mt-2 leading-7">
                              {constraint.source_title} ↔ {constraint.target_title}
                            </p>
                            {constraint.reason && (
                              <p className="mt-2 text-sm leading-6 text-slate-600">{constraint.reason}</p>
                            )}
                          </div>
                        ))}
                      </div>
                    </section>
                  )}
                  <ReviewBox
                    review={claimDetail.review}
                    saving={saving}
                    approveDisabled={!claimDetail.review_gate?.can_approve}
                    approveDisabledReason="目前沒有合格且可追溯的證據，請先補回來源。"
                    optional={
                      claimDetail.attention === "ai_cleared" ||
                      claimDetail.attention === "pending_ai"
                    }
                    optionalReason={claimDetail.attention_reason}
                    onSave={saveReview}
                  />
                </>
              )}
              {!detailLoading &&
                tab === "knowledge" &&
                selectedId &&
                !claimDetail &&
                !error && (
                  <div className="flex min-h-80 flex-col items-center justify-center gap-4 rounded-2xl border border-slate-200 bg-white p-8 text-center shadow-sm">
                    <p className="text-slate-600">所選主張的詳情尚未載入。</p>
                    <button
                      type="button"
                      onClick={() =>
                        setDetailReloadKey((current) => current + 1)
                      }
                      className="rounded-xl bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-700"
                    >
                      重新載入詳情
                    </button>
                  </div>
                )}
              {!detailLoading && tab === "synthesis" && synthesisDetail && (
                <>
                  <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                    <div className="flex flex-wrap gap-2">
                      <StatusBadge status={synthesisDetail.review.status} />
                      <span className="rounded-full bg-violet-50 px-2.5 py-1 text-xs font-semibold text-violet-800">
                        {
                          synthesisLabels[
                            synthesisDetail.synthesis.synthesis_type
                          ]
                        }
                      </span>
                    </div>
                    <h2 className="mt-4 text-2xl font-bold">
                      {synthesisDetail.synthesis.title}
                    </h2>
                    <p className="mt-3 leading-7 text-slate-700">
                      {synthesisDetail.synthesis.description}
                    </p>
                    <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-4 text-amber-900">
                      <strong>语料范围提醒：</strong>
                      {synthesisDetail.corpus_scope.warning}
                    </div>
                  </section>
                  <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                    <h3 className="text-xl font-bold">关联的候选主张</h3>
                    <div className="mt-4 space-y-3">
                      {synthesisDetail.linked_claims.map((claim) => (
                        <div
                          key={claim.claim_id}
                          className="rounded-xl border border-slate-200 p-4"
                        >
                          <div className="flex flex-wrap gap-2">
                            <span className="text-xs font-bold text-indigo-700">
                              {claim.claim_type}
                            </span>
                            {claim.cross_lecture && (
                              <span className="text-xs text-slate-500">
                                跨讲关系：{claim.cross_lecture}
                              </span>
                            )}
                          </div>
                          <p className="mt-1 font-semibold leading-7">
                            {claim.title}
                          </p>
                          <p className="mt-2 text-sm text-slate-500">
                            {claim.lectures.join("、")}
                          </p>
                        </div>
                      ))}
                    </div>
                  </section>
                  <ReviewBox
                    review={synthesisDetail.review}
                    saving={saving}
                    onSave={saveReview}
                  />
                </>
              )}
            </article>
          </div>
        )}

        {tab === "qa" && (
          <section className="mt-6 space-y-5">
            <header className="rounded-2xl border border-indigo-200 bg-indigo-50/60 p-6">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="text-xs font-bold uppercase tracking-wide text-indigo-700">
                    獨立產品驗證
                  </p>
                  <h2 className="mt-1 text-2xl font-bold">{workspace.qa.title}</h2>
                  <p className="mt-2 max-w-3xl leading-7 text-slate-700">
                    {workspace.qa.description}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2 text-sm font-semibold">
                  <span className="rounded-full bg-emerald-100 px-3 py-1.5 text-emerald-900">
                    可回答 {workspace.qa.counts.answered}
                  </span>
                  <span className="rounded-full bg-amber-100 px-3 py-1.5 text-amber-900">
                    部分回答 {workspace.qa.counts.partially_answered}
                  </span>
                  <span className="rounded-full bg-rose-100 px-3 py-1.5 text-rose-900">
                    未回答 {workspace.qa.counts.unanswered}
                  </span>
                  {workspace.qa.diagnostics.available && (
                    <span className={`rounded-full px-3 py-1.5 ${workspace.qa.diagnostics.stale ? "bg-slate-200 text-slate-700" : "bg-violet-100 text-violet-900"}`}>
                      {workspace.qa.diagnostics.stale
                        ? "AI 診斷待重跑"
                        : `AI 診斷問題 ${workspace.qa.diagnostics.summary.issues ?? 0}`}
                    </span>
                  )}
                </div>
              </div>
              <p className="mt-4 rounded-xl border border-indigo-100 bg-white/80 p-4 text-sm leading-6 text-slate-700">
                <strong>產品邊界：</strong>
                問答不從釋經或專題文章倒推答案。它直接讀取同一份共享主張、論證和原始來源；相關文章只作延伸閱讀。
              </p>
            </header>

            <div className="grid items-start gap-6 lg:grid-cols-[minmax(280px,.78fr)_minmax(0,1.7fr)]">
              <aside className="rounded-2xl border border-slate-200 bg-white p-2 shadow-sm lg:sticky lg:top-20">
                {workspace.qa.cases.map((item) => (
                  <button
                    key={item.case_id}
                    type="button"
                    onClick={() => setSelectedId(item.case_id)}
                    className={`mb-1 w-full rounded-xl p-3 text-left ${selectedId === item.case_id ? "bg-indigo-50 ring-1 ring-indigo-200" : "hover:bg-slate-50"}`}
                  >
                    <span className="flex flex-wrap gap-2">
                      <span
                        className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ring-1 ${qaState(item.answer_state).className}`}
                      >
                        {qaState(item.answer_state).label}
                      </span>
                      {item.human_required && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-rose-50 px-2.5 py-1 text-xs font-bold text-rose-800 ring-1 ring-rose-200">
                          <AlertCircle className="h-3.5 w-3.5" />
                          需同工判斷
                        </span>
                      )}
                    </span>
                    <strong className="mt-2 block leading-6 text-slate-950">
                      {item.question}
                    </strong>
                    <span className="mt-2 block text-xs text-slate-500">
                      {item.answer_claim_count} 條回答主張 · {item.source_question_count} 條原始問題
                    </span>
                  </button>
                ))}
              </aside>

              <article className="space-y-5">
                {detailLoading && (
                  <div className="flex min-h-64 items-center justify-center rounded-2xl bg-white">
                    <Loader2 className="h-6 w-6 animate-spin" />
                  </div>
                )}
                {!detailLoading && qaDetail && (
                  <>
                    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                      <span
                        className={`inline-flex rounded-full px-3 py-1.5 text-sm font-bold ring-1 ${qaState(qaDetail.case.answer_state).className}`}
                      >
                        {qaState(qaDetail.case.answer_state).label}
                      </span>
                      <h2 className="mt-4 text-2xl font-bold leading-9">
                        {qaDetail.case.question}
                      </h2>
                      <div className={`mt-5 rounded-xl p-5 ${qaDetail.case.answer_state === "unanswered" ? "border border-rose-200 bg-rose-50" : "bg-slate-50"}`}>
                        <p className="text-xs font-bold uppercase tracking-wide text-indigo-700">
                          先看結論
                        </p>
                        <p className="mt-2 text-lg font-semibold leading-8 text-slate-900">
                          {qaDetail.case.answer_summary}
                        </p>
                        {qaDetail.case.answer_state === "unanswered" && (
                          <p className="mt-3 text-sm font-semibold text-rose-800">
                            系統不會用背景材料替教授補出答案。
                          </p>
                        )}
                      </div>
                      {(qaDetail.case.full_answer_sections?.length ?? 0) > 0 && (
                        <div className="mt-6 border-t border-slate-200 pt-6">
                          <div className="flex flex-wrap items-baseline justify-between gap-2">
                            <h3 className="text-xl font-bold text-slate-950">
                              {qaDetail.case.answer_state === "unanswered"
                                ? "現有資料能說到哪裡"
                                : "完整回答"}
                            </h3>
                            <p className="text-xs font-medium text-slate-500">
                              依據已核查的共享主張整理，不增加教授未說過的結論
                            </p>
                          </div>
                          <div className="mt-4 space-y-4">
                            {qaDetail.case.full_answer_sections?.map((section, index) => (
                              <section
                                key={`${section.heading}-${index}`}
                                className={`rounded-xl border p-5 ${
                                  section.section_type === "boundary"
                                    ? "border-amber-200 bg-amber-50/70"
                                    : section.section_type === "background"
                                      ? "border-sky-200 bg-sky-50/70"
                                      : "border-slate-200 bg-white"
                                }`}
                              >
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                  <h4 className="font-bold text-slate-950">{section.heading}</h4>
                                  {section.section_type !== "boundary" && section.claim_ids.length > 0 && (
                                    <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-600 ring-1 ring-slate-200">
                                      依據 {section.claim_ids.length} 條共享主張
                                    </span>
                                  )}
                                </div>
                                <div className="mt-3 space-y-3 text-[15px] leading-7 text-slate-700">
                                  {section.paragraphs.map((paragraph, paragraphIndex) => (
                                    <p key={paragraphIndex}>{paragraph}</p>
                                  ))}
                                </div>
                              </section>
                            ))}
                          </div>
                        </div>
                      )}
                      <p className="mt-4 text-sm leading-6 text-slate-600">
                        <strong>限制：</strong>{qaDetail.case.limitation}
                      </p>
                      {qaDetail.case.attribution_trap && (
                        <p className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900">
                          <strong>歸屬陷阱：</strong>{qaDetail.case.attribution_trap}
                        </p>
                      )}
                      {qaDetail.quality_warnings.map((warning) => (
                        <p key={warning} className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900">
                          {warning}
                        </p>
                      ))}
                    </section>

                    {qaDetail.diagnostics.available && (
                      <section className={`rounded-2xl border p-6 shadow-sm ${qaDetail.diagnostics.stale ? "border-slate-300 bg-slate-50" : qaDetail.diagnostics.outcomes?.some((issue) => issue.status !== "withdrawn") ? "border-amber-300 bg-amber-50/60" : "border-emerald-200 bg-emerald-50/50"}`}>
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <p className="text-xs font-bold uppercase tracking-wide text-violet-700">
                              AI 答案複核
                            </p>
                            <h3 className="mt-1 text-xl font-bold">
                              {qaDetail.diagnostics.stale
                                ? "資料更新後需要重新複核"
                                : qaDetail.diagnostics.outcomes?.some((issue) => issue.status !== "withdrawn")
                                  ? "複核發現需要處理的事項"
                                  : "兩個模型都認為答案可以使用"}
                            </h3>
                          </div>
                          <span className="rounded-full bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 ring-1 ring-slate-200">
                            Claude 先審 · OpenAI 複核
                          </span>
                        </div>
                        {qaDetail.diagnostics.review?.rationale && (
                          <details className="mt-4 rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-700">
                            <summary className="cursor-pointer font-semibold">查看整題的模型複核說明</summary>
                            <p className="mt-3 leading-6">{qaDetail.diagnostics.review.rationale}</p>
                          </details>
                        )}
                        {!qaDetail.diagnostics.stale && (qaDetail.diagnostics.outcomes?.length ?? 0) > 0 && (
                          <div className="mt-4 space-y-3">
                            {qaDetail.diagnostics.outcomes?.map((issue) => {
                              const meta = qaOutcomeMeta[issue.status] ?? {
                                label: "等待處理",
                                className: "bg-amber-100 text-amber-900",
                              };
                              return (
                                <article
                                  key={issue.issue_id}
                                  className={`rounded-xl border bg-white p-5 ${issue.status === "human_diagnostic_required" ? "border-rose-200" : issue.status === "withdrawn" ? "border-slate-200" : "border-amber-200"}`}
                                >
                                  <div className="flex flex-wrap items-center gap-2">
                                    <span className={`rounded-full px-2.5 py-1 text-xs font-bold ${meta.className}`}>
                                      {meta.label}
                                    </span>
                                    <span className="rounded-full bg-violet-50 px-2.5 py-1 text-xs font-semibold text-violet-800">
                                      {qaErrorLayerLabels[issue.earliest_error_layer] ?? "需要進一步檢查"}
                                    </span>
                                  </div>
                                  <h4 className="mt-3 text-lg font-bold">
                                    {qaIssueTypeLabels[issue.issue_type] ?? qaIssueTypeLabels.other}
                                  </h4>
                                  {issue.answer_excerpt && (
                                    <div className="mt-3 rounded-lg bg-slate-50 p-3">
                                      <p className="text-xs font-bold text-slate-500">正在檢查的答案</p>
                                      <blockquote className="mt-1 border-l-4 border-slate-300 pl-3 text-sm leading-6 text-slate-700">
                                        {issue.answer_excerpt}
                                      </blockquote>
                                    </div>
                                  )}
                                  <div className={`mt-3 rounded-lg p-3 text-sm leading-6 ${issue.status === "human_diagnostic_required" ? "bg-rose-50 text-rose-950" : issue.status === "withdrawn" ? "bg-slate-50 text-slate-700" : "bg-indigo-50 text-indigo-950"}`}>
                                    <strong>{issue.status === "human_diagnostic_required" ? "需要你判斷：" : "處理結果："}</strong>
                                    {qaPlainLanguageResult(issue)}
                                  </div>
                                  <details className="mt-3 text-sm text-slate-600">
                                    <summary className="cursor-pointer font-semibold">查看模型的技術說明</summary>
                                    <div className="mt-3 space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3 leading-6">
                                      <p><strong>內部編號：</strong>{issue.issue_id}</p>
                                      <p><strong>判斷理由：</strong>{issue.explanation}</p>
                                      <p><strong>建議處理：</strong>{issue.recommended_action ?? issue.repair_action}</p>
                                    </div>
                                  </details>
                                </article>
                              );
                            })}
                          </div>
                        )}
                        {qaDetail.diagnostics.human_required && (
                          <p className="mt-4 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm font-semibold text-rose-900">
                            只有標示「需要同工判斷」的項目需要人工處理；其餘項目由系統按模型共識修正或已經撤回。
                          </p>
                        )}
                      </section>
                    )}

                    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                      <h3 className="text-xl font-bold">教授回答所依據的共享主張</h3>
                      <p className="mt-2 text-sm leading-6 text-slate-600">
                        只有這一區的主張可用來組成答案；每條都必須有可核查的教授原話。
                      </p>
                      {qaDetail.answer_claims.length === 0 ? (
                        <div className="mt-4 rounded-xl bg-slate-100 p-4 font-semibold text-slate-700">
                          沒有可作直接答案的共享主張。
                        </div>
                      ) : (
                        <div className="mt-4 space-y-5">
                          {qaDetail.answer_claims.map((claim) => (
                            <article key={claim.claim_id} className="rounded-xl border border-slate-200 p-4">
                              <span className="text-xs font-bold text-indigo-700">
                                {claim.claim_type} · {claim.claim_id}
                              </span>
                              <h4 className="mt-1 text-lg font-bold leading-7">{claim.title}</h4>
                              {claim.scripture_refs.length > 0 && (
                                <p className="mt-2 text-sm text-slate-500">{claim.scripture_refs.join("、")}</p>
                              )}
                              <div className="mt-4 space-y-3">
                                {claim.evidence.map((evidence) => (
                                  <EvidenceCard key={evidence.id} evidence={evidence} />
                                ))}
                              </div>
                            </article>
                          ))}
                        </div>
                      )}
                    </section>

                    {(qaDetail.context_claims.length > 0 || qaDetail.opposed_positions.length > 0) && (
                      <section className="rounded-2xl border border-amber-200 bg-amber-50/50 p-6">
                        <h3 className="text-xl font-bold text-amber-950">背景與反方，不作直接答案</h3>
                        <div className="mt-4 space-y-3">
                          {qaDetail.context_claims.map((claim) => (
                            <div key={claim.claim_id} className="rounded-xl border border-amber-200 bg-white p-4">
                              <span className="text-xs font-bold text-amber-700">背景主張</span>
                              <p className="mt-1 font-semibold">{claim.title}</p>
                            </div>
                          ))}
                          {qaDetail.opposed_positions.map((position) => (
                            <div key={position.position_id} className="rounded-xl border border-rose-200 bg-white p-4">
                              <span className="text-xs font-bold text-rose-700">教授所反駁的立場</span>
                              <p className="mt-1 font-semibold">{position.title ?? position.statement}</p>
                            </div>
                          ))}
                        </div>
                      </section>
                    )}

                    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                      <h3 className="text-xl font-bold">原始問題與來源</h3>
                      <div className="mt-4 space-y-3">
                        {qaDetail.source_questions.map((question) => (
                          <article key={question.question_id} className="rounded-xl border border-slate-200 p-4">
                            <span className="text-xs font-bold text-indigo-700">{question.question_id}</span>
                            {question.answer_state === "answered" && (
                              <span className="ml-2 rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-900 ring-1 ring-amber-200">
                                現有論證圖已連結回答
                              </span>
                            )}
                            {question.answer_state === "partially_answered" && (
                              <span className="ml-2 rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-900 ring-1 ring-amber-200">
                                只回答了部分問題
                              </span>
                            )}
                            {question.answer_state === "unanswered" && (
                              <span className="ml-2 rounded-full bg-rose-50 px-2.5 py-1 text-xs font-semibold text-rose-800 ring-1 ring-rose-200">
                                論證圖中沒有對應答覆
                              </span>
                            )}
                            <p className="mt-1 font-semibold leading-7">{question.text}</p>
                            {question.answer_state_note && (
                              <p className="mt-2 rounded-lg bg-amber-50 p-3 text-sm leading-6 text-amber-950">
                                {question.answer_state_note}
                              </p>
                            )}
                            {question.source_fragments.map((fragment) => (
                              <div key={fragment.fragment_id} className="mt-3 rounded-lg bg-slate-50 p-3 text-sm leading-6 text-slate-700">
                                <p>{fragment.verbatim_excerpt}</p>
                                {fragment.source_url && (
                                  <a href={fragment.source_url} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 font-semibold text-indigo-700">
                                    打開原始講道 <ExternalLink className="h-4 w-4" />
                                  </a>
                                )}
                              </div>
                            ))}
                          </article>
                        ))}
                      </div>
                    </section>

                    {qaDetail.related_products.length > 0 && (
                      <section className="rounded-2xl border border-indigo-200 bg-indigo-50/50 p-6">
                        <h3 className="text-xl font-bold">延伸閱讀方向</h3>
                        <p className="mt-2 text-sm text-slate-600">這些產品不是答案來源，只是讀者需要更完整脈絡時的後續入口。</p>
                        <div className="mt-4 flex flex-wrap gap-2">
                          {qaDetail.related_products.map((product) => (
                            <span key={product.plan_id} className="rounded-full bg-white px-3 py-2 text-sm font-semibold text-indigo-800 ring-1 ring-indigo-200">
                              {product.axis === "topic" ? "專題" : "釋經"} · {product.title}
                            </span>
                          ))}
                        </div>
                      </section>
                    )}
                  </>
                )}
              </article>
            </div>
          </section>
        )}

        {tab === "validation" && (
          <section className="mt-6 space-y-5">
            <div className="rounded-2xl border border-slate-200 bg-white p-6">
              <h2 className="text-2xl font-bold">五种用途，共同检验一份知识</h2>
              <p className="mt-2 leading-7 text-slate-600">
                这些不是五套数据库。每项实验都要证明同一批主张、问题、论证和来源能够被不同产品安全复用。
              </p>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              {workspace.validation.experiments.map((experiment) => (
                <article
                  key={experiment.experiment_id}
                  className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
                >
                  <span className="text-xs font-bold text-indigo-700">
                    设计验证
                  </span>
                  <h3 className="mt-2 text-xl font-bold">{experiment.title}</h3>
                  <p className="mt-3 leading-7 text-slate-700">
                    {experiment.question}
                  </p>
                  <ul className="mt-4 space-y-2 text-sm text-slate-600">
                    {experiment.acceptance_criteria.map((criterion) => (
                      <li key={criterion} className="flex gap-2">
                        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                        {criterion}
                      </li>
                    ))}
                  </ul>
                  {experiment.product_plan_id && (
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedId(
                          firstDecisionForPlan(experiment.product_plan_id),
                        );
                        window.setTimeout(() => {
                          document
                            .getElementById("dual-axis-composition-review")
                            ?.scrollIntoView({
                              behavior: "smooth",
                              block: "start",
                            });
                        }, 50);
                      }}
                      className="mt-4 rounded-xl bg-indigo-600 px-4 py-2.5 font-semibold text-white"
                    >
                      審核對應的編排計劃 ↓
                    </button>
                  )}
                </article>
              ))}
            </div>
            <div className="grid gap-5 lg:grid-cols-2">
              <section className="rounded-2xl border border-amber-200 bg-amber-50/60 p-6">
                <h2 className="text-xl font-bold text-amber-950">
                  出版前編輯核查
                </h2>
                <p className="mt-2 text-sm leading-6 text-amber-900/80">
                  這些問題不能由 AI
                  代替教授下結論，出版前必須由編輯或具備相關專業的同工處理。
                </p>
                <div className="mt-4 space-y-3">
                  {workspace.validation.editorial_checks.map((check) => (
                    <article
                      key={check.check_id}
                      className="rounded-xl border border-amber-200 bg-white p-4"
                    >
                      <span className="text-xs font-bold text-amber-700">
                        {check.status}
                      </span>
                      <h3 className="mt-1 font-bold">{check.title}</h3>
                      <p className="mt-2 text-sm leading-6 text-slate-700">
                        {check.note}
                      </p>
                    </article>
                  ))}
                </div>
              </section>
              <section className="rounded-2xl border border-rose-200 bg-rose-50/50 p-6">
                <h2 className="text-xl font-bold text-rose-950">
                  尚待處理的解經張力
                </h2>
                <p className="mt-2 text-sm leading-6 text-rose-900/80">
                  這些不是系統替教授補出的答案，而是寫作前需要保留並人工處理的真問題。
                </p>
                <div className="mt-4 space-y-3">
                  {workspace.validation.tensions.map((tension) => (
                    <article
                      key={tension.tension_id}
                      className="rounded-xl border border-rose-200 bg-white p-4"
                    >
                      <span className="text-xs font-bold text-rose-700">
                        {tension.status}
                      </span>
                      <p className="mt-2 font-semibold leading-7">
                        {tension.question}
                      </p>
                      <p className="mt-2 text-xs text-slate-500">
                        關聯主張：{tension.claim_ids.join("、")}
                      </p>
                    </article>
                  ))}
                </div>
              </section>
            </div>
            {selectedId?.startsWith("CD-") && (
              <div
                id="dual-axis-composition-review"
                className="scroll-mt-24 space-y-4"
              >
                <header className="rounded-2xl border border-indigo-200 bg-indigo-50/60 p-5">
                  <p className="text-xs font-bold uppercase tracking-wide text-indigo-700">
                    篇章編排審核
                  </p>
                  <h2 className="mt-1 text-2xl font-bold text-slate-950">
                    編排決定
                  </h2>
                  <p className="mt-2 leading-7 text-slate-700">
                    先選擇一個編排計劃。左側只顯示該計劃的編輯決定；右側說明本段準備怎麼寫、為什麼這樣安排，以及它依據哪些共享主張與來源。
                  </p>
                  <div className="mt-4 grid gap-3 md:grid-cols-2">
                    {workspace.composition.plans.map((plan) => {
                      const active = activeCompositionPlan?.plan_id === plan.plan_id;
                      return (
                        <button
                          key={plan.plan_id}
                          type="button"
                          onClick={() => setSelectedId(plan.decision_ids[0] ?? null)}
                          className={`rounded-xl border p-4 text-left transition ${active ? "border-indigo-500 bg-white ring-2 ring-indigo-200" : "border-indigo-100 bg-white/70 hover:border-indigo-300"}`}
                        >
                          <span className="text-xs font-bold uppercase tracking-wide text-indigo-700">
                            {plan.axis === "topic" ? "專題軸" : "釋經軸"}
                          </span>
                          <strong className="mt-1 block text-slate-950">
                            {plan.title}
                          </strong>
                          <span className="mt-2 block text-sm text-slate-600">
                            {plan.decision_ids.length} 項編排決定
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </header>
                <div className="grid items-start gap-6 lg:grid-cols-[minmax(280px,.78fr)_minmax(0,1.7fr)]">
                <aside className="rounded-2xl border border-slate-200 bg-white p-2">
                  <div className="border-b border-slate-100 px-3 py-3">
                    <span className="text-xs font-bold uppercase tracking-wide text-slate-500">
                      {activeCompositionPlan?.axis === "topic" ? "專題軸" : "釋經軸"}
                    </span>
                    <h3 className="mt-1 font-bold text-slate-950">
                      {activeCompositionPlan?.title}
                    </h3>
                  </div>
                  {activeCompositionDecisions.map((decision) => (
                    <button
                      key={decision.decision_id}
                      onClick={() => setSelectedId(decision.decision_id)}
                      className={`mb-1 w-full rounded-xl p-3 text-left ${selectedId === decision.decision_id ? "bg-indigo-50 ring-1 ring-indigo-200" : "hover:bg-slate-50"}`}
                    >
                      <strong className="block">
                        {decision.passage} · {decision.section_title}
                      </strong>
                      <span className="mt-1 block text-xs text-slate-500">
                        編排決定 {decision.decision_id} · {decision.axis === "topic" ? "專題軸" : "釋經軸"}
                      </span>
                      <span className="mt-2 flex flex-wrap gap-2">
                        <StatusBadge status={decision.review.status} />
                        <span className="rounded-full bg-violet-50 px-2.5 py-1 text-xs font-semibold text-violet-800">
                          {actionLabels[decision.action] ?? decision.action}
                        </span>
                      </span>
                    </button>
                  ))}
                </aside>
                <article className="space-y-5">
                  {detailLoading && (
                    <div className="flex min-h-64 items-center justify-center rounded-2xl bg-white">
                      <Loader2 className="h-6 w-6 animate-spin" />
                    </div>
                  )}
                  {!detailLoading && compositionDetail && (
                    <>
                      <section className="rounded-2xl border border-slate-200 bg-white p-6">
                        <div className="flex gap-2">
                          <span className="rounded-full bg-indigo-100 px-2.5 py-1 text-xs font-bold text-indigo-800">
                            編排決定 {compositionDetail.decision.decision_id}
                          </span>
                          <StatusBadge
                            status={compositionDetail.review.status}
                          />
                          <span className="rounded-full bg-violet-50 px-2.5 py-1 text-xs font-semibold text-violet-800">
                            {actionLabels[compositionDetail.decision.action]}
                          </span>
                        </div>
                        <p className="mt-4 text-xs font-bold uppercase tracking-wide text-violet-700">
                          {compositionDetail.plan.axis === "topic"
                            ? "專題軸"
                            : "釋經軸"} · {compositionDetail.plan.title}
                        </p>
                        <p className="mt-4 text-sm font-bold text-indigo-700">
                          {compositionDetail.decision.passage}
                        </p>
                        <h2 className="mt-1 text-2xl font-bold">
                          {compositionDetail.decision.section_title}
                        </h2>
                        <div className="mt-5 rounded-xl bg-slate-50 p-4">
                          <strong>編排決定：準備怎麼寫</strong>
                          <p className="mt-2 leading-7">
                            {compositionDetail.decision.decision}
                          </p>
                        </div>
                        <div className="mt-4 rounded-xl border p-4">
                          <strong>編排理由：為什麼這樣安排</strong>
                          <p className="mt-2 leading-7">
                            {compositionDetail.decision.rationale}
                          </p>
                        </div>
                        {compositionDetail.decision.coverage === "missing" && (
                          <div className="mt-4 flex gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-amber-900">
                            <FileQuestion className="h-5 w-5 shrink-0" />
                            <p>
                              <strong>材料缺口：</strong>只記錄缺口，不讓 AI
                              冒充教授補寫。
                            </p>
                          </div>
                        )}
                      </section>
                      <section className="rounded-2xl border border-sky-200 bg-sky-50/40 p-6">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <div>
                            <h3 className="text-xl font-bold">與本段編排一致的原聲</h3>
                            <p className="mt-2 text-sm leading-6 text-slate-600">
                              原聲依照同一項編排決定呈現，不另立一套分類；連續材料保留為完整片段，非連續材料則明確分段。
                            </p>
                          </div>
                          {compositionDetail.source_presentation_summary?.mode === "segment_group" && (
                            <span className="rounded-full bg-white px-3 py-1 text-xs font-bold text-sky-800 ring-1 ring-sky-200">
                              多個原聲片段
                            </span>
                          )}
                        </div>
                        {compositionDetail.source_presentations.length ? (
                          <div className="mt-5 space-y-4">
                            {compositionDetail.source_presentations.map((presentation, index) => (
                              <article key={presentation.presentation_id} className="rounded-xl border border-sky-200 bg-white p-4">
                                <div className="flex flex-wrap items-start justify-between gap-3">
                                  <div>
                                    <p className="text-xs font-bold text-sky-800">
                                      原聲教學片段 {index + 1}
                                    </p>
                                    <p className="mt-1 font-semibold text-slate-900">
                                      {presentation.source_title || presentation.transcript_id || "原始講道"}
                                    </p>
                                  </div>
                                  <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
                                    {Math.max(1, Math.round(presentation.duration_seconds / 60))} 分鐘
                                  </span>
                                </div>
                                {presentation.source ? (
                                  <CitationMediaPlayer
                                    source={presentation.source}
                                    startTime={presentation.start_seconds}
                                    endTime={presentation.end_seconds}
                                  />
                                ) : (
                                  <p className="mt-3 text-sm text-amber-700">已找到時間範圍，但尚未解析出可播放的媒體。</p>
                                )}
                              </article>
                            ))}
                          </div>
                        ) : (
                          <p className="mt-5 rounded-xl bg-white p-4 text-sm leading-6 text-slate-600">
                            本段目前只有筆記講稿來源，或尚未完成講道時間定位，因此沒有可播放的原聲片段。
                          </p>
                        )}
                        {compositionDetail.source_presentation_summary?.note && (
                          <p className="mt-4 text-xs leading-5 text-slate-500">
                            {compositionDetail.source_presentation_summary.note}
                          </p>
                        )}
                      </section>
                      {compositionDetail.argument_layer_assessment && (
                        <section className="rounded-2xl border border-cyan-200 bg-cyan-50/50 p-6">
                          <div className="flex flex-wrap items-center gap-2">
                            <h3 className="text-xl font-bold">論證層支撐檢查</h3>
                            <span className="rounded-full bg-white px-2.5 py-1 text-xs font-bold text-cyan-800 ring-1 ring-cyan-200">
                              {compositionDetail.argument_layer_assessment.argument_layer_status === "solid"
                                ? "結構完整"
                                : compositionDetail.argument_layer_assessment.argument_layer_status === "usable_with_gaps"
                                  ? "可用但有缺口"
                                  : "尚不足以支撐"}
                            </span>
                          </div>
                          <p className="mt-3 text-sm leading-7 text-slate-700">
                            {compositionDetail.argument_layer_assessment.summary}
                          </p>
                          {!!compositionDetail.argument_layer_assessment.argument_layer_findings.length && (
                            <div className="mt-4 space-y-3">
                              {compositionDetail.argument_layer_assessment.argument_layer_findings.map((finding, index) => (
                                <article key={`${finding.finding_type}-${index}`} className="rounded-xl border border-cyan-200 bg-white p-4">
                                  <p className="text-xs font-bold text-cyan-800">
                                    {finding.finding_type} · {finding.severity}
                                  </p>
                                  <p className="mt-2 text-sm leading-6">{finding.explanation}</p>
                                  <p className="mt-2 text-sm leading-6 text-slate-600">
                                    <strong>後續：</strong>{finding.recommended_action}
                                  </p>
                                </article>
                              ))}
                            </div>
                          )}
                        </section>
                      )}
                      {compositionDetail.ai_review && (
                        <section className="rounded-2xl border border-indigo-200 bg-indigo-50/40 p-6">
                          <div className="flex flex-wrap items-center gap-2">
                            <h3 className="text-xl font-bold">雙模型編排審核</h3>
                            <span className="rounded-full bg-white px-2.5 py-1 text-xs font-bold text-indigo-800 ring-1 ring-indigo-200">
                              {compositionDetail.ai_review.decision === "pass"
                                ? "Claude 通過"
                                : compositionDetail.ai_review.outcome === "auto_applied"
                                  ? "兩模型同意，已套用"
                                  : compositionDetail.ai_review.outcome === "withdrawn"
                                    ? "Claude 已撤回"
                                    : "需人工處理"}
                            </span>
                          </div>
                          <p className="mt-3 text-sm leading-7 text-slate-700">
                            {compositionDetail.ai_review.rationale}
                          </p>
                          {!!compositionDetail.ai_review.issues.length && (
                            <div className="mt-4 space-y-2">
                              {compositionDetail.ai_review.issues.map((issue, index) => (
                                <div key={`${issue.issue_type}-${index}`} className="rounded-xl bg-white p-4 text-sm leading-6">
                                  <strong>{issue.issue_type}：</strong>{issue.explanation}
                                </div>
                              ))}
                            </div>
                          )}
                          {compositionDetail.ai_review.openai && (
                            <p className="mt-4 border-t border-indigo-200 pt-4 text-sm leading-6 text-slate-700">
                              <strong>OpenAI 獨立復核：</strong>
                              {compositionDetail.ai_review.openai.decision === "accept" ? "接受。" : "拒絕。"}
                              {compositionDetail.ai_review.openai.rationale}
                            </p>
                          )}
                        </section>
                      )}
                      <section className="rounded-2xl border border-slate-200 bg-white p-6">
                        <h3 className="text-xl font-bold">依據的共享主張</h3>
                        {compositionDetail.claim_hierarchy ? (
                          <ClaimHierarchy
                            hierarchy={compositionDetail.claim_hierarchy}
                            claims={compositionDetail.linked_claims}
                          />
                        ) : (
                          <div className="mt-4 space-y-3">
                            {compositionDetail.linked_claims.map((claim) => (
                              <div
                                key={claim.claim_id}
                                className="rounded-xl border p-4"
                              >
                                <span className="text-xs font-bold text-indigo-700">
                                  {claim.claim_type}
                                </span>
                                <p className="mt-1 font-semibold">
                                  {claim.title}
                                </p>
                              </div>
                            ))}
                          </div>
                        )}
                        <div className="mt-4">
                          {!compositionDetail.linked_claims.length && (
                            <p className="rounded-xl bg-slate-50 p-4 text-slate-600">
                              沒有足夠的教授材料。
                            </p>
                          )}
                        </div>
                      </section>
                      {!!compositionDetail.source_leads.length && (
                        <section className="rounded-2xl border border-sky-200 bg-sky-50/40 p-6">
                          <h3 className="text-xl font-bold">全庫來源線索</h3>
                          <p className="mt-2 text-sm leading-6 text-slate-600">
                            這些來源由205篇普查找到。尚待詳細整理或回聽核實的內容，只能用來安排後續工作，不能直接當作已批准主張出版。
                          </p>
                          <div className="mt-4 space-y-3">
                            {compositionDetail.source_leads.map((lead) => (
                              <article
                                key={lead.source_lead_id}
                                className="rounded-xl border border-sky-200 bg-white p-4"
                              >
                                <div className="flex flex-wrap items-center gap-2 text-xs font-semibold">
                                  <span className="rounded-full bg-sky-100 px-2.5 py-1 text-sky-800">
                                    {evidenceMaturityLabels[lead.evidence_maturity] ??
                                      lead.evidence_maturity}
                                  </span>
                                  <span className="text-slate-500">
                                    優先級：{lead.priority}
                                  </span>
                                </div>
                                <h4 className="mt-3 font-bold">{lead.title}</h4>
                                <p className="mt-1 text-sm text-slate-500">
                                  {lead.transcript_id}
                                </p>
                                <p className="mt-2 text-sm leading-6 text-slate-700">
                                  {lead.summary}
                                </p>
                                {!!lead.scripture_refs?.length && (
                                  <p className="mt-2 text-xs text-slate-500">
                                    經文：{lead.scripture_refs.join("、")}
                                  </p>
                                )}
                              </article>
                            ))}
                          </div>
                        </section>
                      )}
                      <ReviewBox
                        review={compositionDetail.review}
                        saving={saving}
                        onSave={saveReview}
                      />
                    </>
                  )}
                </article>
                </div>
              </div>
            )}
          </section>
        )}
      </div>
    </main>
  );
}
