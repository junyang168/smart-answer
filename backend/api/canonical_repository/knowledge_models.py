from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


def _knowledge_sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class EvolvingKnowledgeRecord(BaseModel):
    """Versioned authoring record whose optional vocabulary may grow safely.

    The stable identity, attribution, review, and provenance fields are typed.
    Extra fields are preserved so a newer survey package can be imported before
    every optional research field has been promoted into the canonical schema.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_version: str | int = 1
    review_status: str = "candidate"
    visibility: str = "internal"
    revision: int = 1


class KnowledgeSourceDocument(EvolvingKnowledgeRecord):
    source_id: str
    source_type: str
    project_id: Optional[str] = None
    transcript_id: Optional[str] = None
    title: Optional[str] = None
    source_url: Optional[str] = None
    canonical_source_id: Optional[str] = None
    source_sha256: Optional[str] = None


class SourceFragmentRecord(EvolvingKnowledgeRecord):
    fragment_id: str
    source_id: str
    verbatim_excerpt: str
    paragraph_key: Optional[str | int] = None
    media_time: Optional[float] = None
    source_url: Optional[str] = None
    citation_id: Optional[str] = None
    source_sha256: Optional[str] = None
    paragraph_text_sha256: Optional[str] = None
    verbatim_excerpt_sha256: Optional[str] = None
    anchor_state: str = "unresolved"


class QuestionRecord(EvolvingKnowledgeRecord):
    question_id: str
    text: str = Field(validation_alias=AliasChoices("text", "question"))
    questioner: Optional[str] = None
    question_type: Optional[str] = None
    source_fragment_id: Optional[str] = None
    answer_state: str = Field(
        default="unanswered",
        validation_alias=AliasChoices("answer_state", "answer_status"),
    )
    answer_claim_ids: list[str] = Field(default_factory=list)


class ObservationRecord(EvolvingKnowledgeRecord):
    observation_id: str
    statement: str
    observation_type: Optional[str] = None
    # `load_bearing` or `background`; extraction v2 onward always sets it.
    # Optional because the 430 records written before v2 genuinely do not know
    # which they are, and defaulting them to `background` would assert
    # something no one has judged.
    argument_role: Optional[str] = None
    source_fragment_id: Optional[str] = None
    scripture_refs: list[str] = Field(default_factory=list)


class ClaimRecord(EvolvingKnowledgeRecord):
    claim_id: str
    statement: str = Field(validation_alias=AliasChoices("statement", "title"))
    claim_type: str
    attribution: Optional[str] = None
    corpus_scope: Optional[str] = None
    maturity: str = "candidate"
    scripture_refs: list[Any] = Field(default_factory=list)
    topic_ids: list[str] = Field(default_factory=list)
    evidence_step_ids: list[str] = Field(default_factory=list)
    extraction_fingerprints: list[str] = Field(default_factory=list)


class TopicNodeRecord(EvolvingKnowledgeRecord):
    """Authoritative subject identity shared by every product projection."""

    topic_id: str
    label: str
    parent_topic_id: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    definition: str = ""
    legacy_ids: list[str] = Field(default_factory=list)


class TopicIdentityReconciliationRecord(EvolvingKnowledgeRecord):
    """Persistent editorial decision about a discovered topic candidate.

    Candidate ids identify one discovery artifact only.  ``resolved_topic_id``
    is the immutable canonical identity chosen by an editor (or an unambiguous
    exact match to an already established topic).
    """

    reconciliation_id: str
    candidate_topic_id: str
    label: str
    topic_level: str
    parent_candidate_topic_id: Optional[str] = None
    claim_ids: list[str] = Field(default_factory=list)
    status: str = "pending_new"
    candidate_matches: list[dict[str, Any]] = Field(default_factory=list)
    resolved_topic_id: Optional[str] = None
    resolution_action: Optional[str] = None
    origin_batch_id: Optional[str] = None


class EvidenceStepRecord(EvolvingKnowledgeRecord):
    evidence_step_id: str
    source_fragment_id: Optional[str] = None
    source_fragment_ids: list[str] = Field(default_factory=list)
    statement: str = Field(default="", validation_alias=AliasChoices("statement", "observation"))
    step_type: Optional[str] = None
    claim_group_ids: list[str] = Field(default_factory=list)
    produced_claim_ids: list[str] = Field(default_factory=list)
    speaker: Optional[str] = None
    stance: Optional[str] = None
    discourse_role: Optional[str] = None
    anchor_quality: Optional[str] = None
    support_eligibility: str = "withheld_unreviewed"
    citation_ids: list[str] = Field(default_factory=list)
    scripture_refs: list[str] = Field(default_factory=list)


def evidence_fragment_ids(value: Mapping[str, Any] | EvidenceStepRecord) -> list[str]:
    """Return all source fragments across singular and plural extraction eras."""

    if isinstance(value, Mapping):
        singular = value.get("source_fragment_id")
        plural = value.get("source_fragment_ids") or []
    else:
        singular = value.source_fragment_id
        plural = value.source_fragment_ids
    return sorted({str(item) for item in [singular, *plural] if item})


class KnowledgeRelationRecord(EvolvingKnowledgeRecord):
    relation_id: str
    from_id: str = Field(validation_alias=AliasChoices("from_id", "source_id"))
    to_id: str = Field(validation_alias=AliasChoices("to_id", "target_id"))
    relation_type: str
    reason: str = ""


class ClaimRelationRecord(EvolvingKnowledgeRecord):
    claim_relation_id: str
    from_id: str = Field(validation_alias=AliasChoices("from_id", "source_id", "from_claim_id"))
    to_id: str = Field(validation_alias=AliasChoices("to_id", "target_id", "to_claim_id"))
    relation_type: str
    reason: str = ""


class ClaimRelationConstraintRecord(EvolvingKnowledgeRecord):
    constraint_id: str
    source_id: str
    target_id: str
    forbidden_relation_types: list[str] = Field(default_factory=list)
    bidirectional: bool = False
    composition_role: Optional[str] = None
    reason: str = ""


class PositionNodeRecord(EvolvingKnowledgeRecord):
    position_id: str
    title: str
    attribution: str = "external_position"
    corpus_scope: Optional[str] = None


class KnowledgeRouteRecord(EvolvingKnowledgeRecord):
    route_id: str
    claim_id: str
    route_type: str
    target_id: str
    decision_ids: list[str] = Field(default_factory=list)
    canonical_topic_ids: list[str] = Field(default_factory=list)


class ProductDependencyRecord(EvolvingKnowledgeRecord):
    dependency_id: str
    consumer_kind: str
    consumer_id: str
    claim_id: str
    pinned_claim_revision: int
    route_ids: list[str] = Field(default_factory=list)
    status: str = "current"
    invalidation_event_ids: list[str] = Field(default_factory=list)
    viewpoint_revision_ids: list[str] = Field(default_factory=list)
    viewpoint_registry_snapshot_ids: list[str] = Field(default_factory=list)
    argument_route_revision_ids: list[str] = Field(default_factory=list)
    argument_route_snapshot_ids: list[str] = Field(default_factory=list)
    coverage_snapshot_id: Optional[str] = None
    resolution_ledger_id: Optional[str] = None
    quality_report_id: Optional[str] = None
    quality_report_sha256: Optional[str] = None
    projection_sha256: Optional[str] = None
    dependency_manifest: list[dict[str, Any]] = Field(default_factory=list)


class ImpactEventRecord(EvolvingKnowledgeRecord):
    impact_event_id: str
    changed_record_type: str
    changed_record_id: str
    from_revision: int
    to_revision: int
    change_fields: list[str] = Field(default_factory=list)
    affected_dependency_ids: list[str] = Field(default_factory=list)
    affected_targets: list[dict[str, Any]] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    status: str = "open"
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class EditorialSynthesisRecord(EvolvingKnowledgeRecord):
    synthesis_id: str
    synthesis_type: str
    title: str
    description: str = ""
    claim_ids: list[str] = Field(default_factory=list)
    corpus_scope: Optional[str] = None


class CompositionDecisionRecord(EvolvingKnowledgeRecord):
    decision_id: str
    plan_id: str
    decision_type: str = Field(validation_alias=AliasChoices("decision_type", "action"))
    decision: str
    reason: str = Field(default="", validation_alias=AliasChoices("reason", "rationale"))
    claim_ids: list[str] = Field(default_factory=list)


class BaseSourceBinding(BaseModel):
    """Which manuscript an article is built on, and which part of it is in scope."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    source_id: str
    path: str
    sha256: str
    fidelity_status: str = ""
    # Legacy single-heading scope. Selecting scope by heading silently drops
    # passage material filed under a different heading, so this is retained
    # for migration only; scripture-scoped selection supersedes it.
    section_anchor: str = ""
    scripture_scope: list[str] = Field(default_factory=list)


class AuthoringSection(BaseModel):
    """A reader section, the plan decisions it carries, and its required steps."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    section_id: str
    decision_ids: list[str] = Field(default_factory=list)
    reader_heading: str = ""
    thesis: str = ""
    allowed_operations: list[str] = Field(default_factory=list)
    ineligible_operations: list[str] = Field(default_factory=list)
    coverage_boundaries: list[dict[str, Any]] = Field(default_factory=list)


class CompositionPlanRecord(EvolvingKnowledgeRecord):
    plan_id: str
    product_type: str
    title: str
    description: str = ""
    decision_ids: list[str] = Field(default_factory=list)

    # Authoring contract. Previously held in an untracked
    # `base-manuscript-contract-input.json` beside the staging artifacts, which
    # left the load-bearing steps of every published article outside both
    # version control and the authoring store, and left its `editor_confirmed`
    # claim unverifiable. PostgreSQL is the authoring authority; this is where
    # the contract belongs.
    contract_id: Optional[str] = None
    contract_schema_version: Optional[str] = None
    passage: Optional[str] = None
    authoring_mode: Optional[str] = None
    base_source: Optional[BaseSourceBinding] = None
    additional_base_sources: list[BaseSourceBinding] = Field(default_factory=list)
    authoring_sections: list[AuthoringSection] = Field(default_factory=list)
    supplemental_material: list[dict[str, Any]] = Field(default_factory=list)
    global_rules: list[str] = Field(default_factory=list)
    contract_confirmed_by: Optional[str] = None
    contract_confirmed_at: Optional[str] = None


class EditorialCheckRecord(EvolvingKnowledgeRecord):
    check_id: str = Field(validation_alias=AliasChoices("check_id", "editorial_check_id"))
    title: str = ""
    description: str = Field(default="", validation_alias=AliasChoices("description", "note"))


class TensionRecord(EvolvingKnowledgeRecord):
    tension_id: str
    title: str = Field(default="", validation_alias=AliasChoices("title", "question"))
    description: str = Field(default="", validation_alias=AliasChoices("description", "note"))


class SentenceInventoryRecord(EvolvingKnowledgeRecord):
    """One sentence of a source, addressable and stable across revisions.

    The ledger's denominator. It is derived from the source text alone -- no
    model, no claim layer -- because a count taken from what extraction
    produced can never show what extraction missed.

    `sentence_sha256` rather than an ordinal is what makes the ledger survive
    editing: revise the manuscript and only the sentences whose text actually
    changed lose their identity, while an ordinal key would shift on any
    insertion and orphan every downstream verdict after it. `ordinal` only
    disambiguates a sentence repeated verbatim inside one segment.
    """

    sentence_id: str
    source_id: str
    segment_index: int
    ordinal: int = 0
    text: str
    sentence_sha256: str
    char_start: int
    char_end: int
    source_sha256: Optional[str] = None


class SentenceReconciliationRecord(EvolvingKnowledgeRecord):
    """Whether one source sentence reached the argument layer, and how.

    `match_kind` is separate from `status` on purpose. Only `exact_span` may
    conclude `represented`: a similarity score that says a claim "mostly
    resembles" this sentence is the same silent loss the ledger exists to
    close, rebuilt inside the instrument. `proposed_link` records a candidate
    for a human or the second pass, and never settles anything.

    `triage_flags` carries the `load_bearing_flags()` signals for ranking a
    review queue. They rank; they do not authorise -- both sentences the
    grounding gate deleted in #64 are flag-negative.
    """

    reconciliation_id: str
    sentence_id: str
    source_id: str
    status: str = "unprocessed"
    match_kind: str = "none"
    represented_by: list[str] = Field(default_factory=list)
    exclusion_id: Optional[str] = None
    triage_flags: list[str] = Field(default_factory=list)
    reconciled_against: Optional[str] = None


class ExclusionRecord(EvolvingKnowledgeRecord):
    """A recorded decision that one sentence carries no argument.

    This is a record and not an array element in some payload because #68's
    `RequiredArgumentStep` was the latter: a plain model with no review status,
    no revision and no id, which therefore could not be reviewed, revised or
    retired, and rotted for want of an owner.

    Terminality depends on `reason_code`, not on how important the sentence
    looks. `duplicate_of` is checkable without judgement, so it needs no
    human. `background_only` is the interpretive call that failed in #64 and
    #53 and is never delegated, however unremarkable the sentence appears.
    """

    exclusion_id: str
    sentence_id: str
    source_id: str
    reason_code: str
    rationale: str = ""
    duplicate_of_record_id: Optional[str] = None
    decided_by: Optional[str] = None


VIEWPOINT_REVIEW_STATUSES = {
    "candidate",
    "ai_consensus",
    "system_approved",
    "human_approved",
    "approved",
    "rejected",
    "system_verified",
}

ViewpointSourceIneligibilityReason = Literal[
    "external_position",
    "not_professor_claim",
    "non_asserted",
    "unsupported_attribution",
    "upstream_claim_not_approved",
    "anchor_invalid",
]
ViewpointNoAssertionReason = Literal[
    "not_a_registerable_proposition",
    "question_only",
    "observation_only",
    "editorial_metadata",
]
ViewpointBlockerCode = Literal[
    "insufficient_source_maturity",
    "attribution_ambiguous",
    "subject_mismatch",
    "object_mismatch",
    "polarity_mismatch",
    "population_scope_mismatch",
    "scripture_scope_mismatch",
    "temporal_scope_mismatch",
    "condition_mismatch",
    "modality_mismatch",
    "component_locator_required",
    "approved_negative_duplicate_constraint",
    "reviewed_material_relation",
    "different_active_viewpoints",
    "reviewer_disagreement",
    "evidence_invalid",
]
ViewpointQualityDimensionName = Literal[
    "provenance_integrity",
    "source_maturity",
    "resolution_coverage",
    "identity_precision",
    "candidate_recall",
    "route_fidelity",
    "temporal_correctness",
    "consumer_projection_integrity",
]
VIEWPOINT_QUALITY_DIMENSIONS = {
    "provenance_integrity",
    "source_maturity",
    "resolution_coverage",
    "identity_precision",
    "candidate_recall",
    "route_fidelity",
    "temporal_correctness",
    "consumer_projection_integrity",
}


class StrictViewpointRecord(EvolvingKnowledgeRecord):
    """A viewpoint master-data record with a closed, versioned contract."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def validate_review_status(self) -> "StrictViewpointRecord":
        if self.review_status not in VIEWPOINT_REVIEW_STATUSES:
            raise ValueError(f"unsupported viewpoint review_status: {self.review_status}")
        if self.revision < 1:
            raise ValueError("revision must be positive")
        return self


class ViewpointCoverageSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_revision_id: str
    source_sha256: str
    roles: list[
        Literal["source_universe", "detailed_extraction", "viewpoint_reviewed"]
    ] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_roles(self) -> "ViewpointCoverageSource":
        if len(self.roles) != len(set(self.roles)):
            raise ValueError("coverage source roles must be unique")
        if self.roles != sorted(self.roles):
            raise ValueError("coverage source roles must be sorted")
        return self


class ViewpointCoverageSnapshotRecord(StrictViewpointRecord):
    coverage_snapshot_id: str
    schema_version: Literal["wang_viewpoint_coverage_snapshot_v1"] = (
        "wang_viewpoint_coverage_snapshot_v1"
    )
    historical_survey_baseline_id: Optional[str] = None
    source_universe_manifest_id: str
    source_universe_manifest_sha256: str
    sources: list[ViewpointCoverageSource] = Field(min_length=1)
    sources_sha256: str
    coverage_status: Literal["partial", "complete"] = "partial"
    created_at: str
    review_status: Literal["system_verified"] = "system_verified"

    @model_validator(mode="after")
    def validate_sources(self) -> "ViewpointCoverageSnapshotRecord":
        source_ids = [item.source_id for item in self.sources]
        revisions = [item.source_revision_id for item in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("coverage snapshot contains multiple current revisions for one source")
        if revisions != sorted(revisions):
            raise ValueError("coverage snapshot sources must be sorted by source_revision_id")
        if len(revisions) != len(set(revisions)):
            raise ValueError("coverage snapshot contains duplicate source revisions")
        if self.coverage_status == "complete" and any(
            "viewpoint_reviewed" not in source.roles for source in self.sources
        ):
            raise ValueError(
                "complete coverage requires viewpoint_reviewed for every source"
            )
        return self


class CanonicalViewpointRecord(StrictViewpointRecord):
    viewpoint_id: str
    schema_version: Literal["wang_canonical_viewpoint_v1"] = "wang_canonical_viewpoint_v1"
    current_revision_id: str
    identity_status: Literal["active", "redirected", "split", "merged", "retired"] = "active"
    created_from_candidate_id: str
    redirect_to_viewpoint_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_redirect(self) -> "CanonicalViewpointRecord":
        if self.identity_status in {"redirected", "merged"} and not self.redirect_to_viewpoint_id:
            raise ValueError("redirected or merged viewpoint requires redirect_to_viewpoint_id")
        if self.redirect_to_viewpoint_id == self.viewpoint_id:
            raise ValueError("viewpoint cannot redirect to itself")
        return self


class ViewpointPropositionSignature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    predicate: str
    object: str
    polarity: Literal["affirmed", "denied"]
    modality: str
    temporal_scope: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    population_scope: list[str] = Field(default_factory=list)


class ViewpointScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scripture_scope: list[str] = Field(default_factory=list)
    audience_scope: list[str] = Field(default_factory=list)
    historical_scope: list[str] = Field(default_factory=list)


class ViewpointRevisionProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    basis_identity_decision_ids: list[str] = Field(min_length=1)
    review_artifact_sha256: str


class ViewpointRevisionRecord(StrictViewpointRecord):
    viewpoint_revision_id: str
    schema_version: Literal["wang_viewpoint_revision_v1"] = "wang_viewpoint_revision_v1"
    viewpoint_id: str
    revision_number: int = Field(ge=1)
    core_proposition: str = Field(min_length=1)
    proposition_signature: ViewpointPropositionSignature
    attribution_subject: Literal["professor"] = "professor"
    representation_kind: Literal["editorial_normalization_of_source_claims"] = (
        "editorial_normalization_of_source_claims"
    )
    not_a_direct_quote: Literal[True] = True
    scope: ViewpointScope
    provenance: ViewpointRevisionProvenance
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    supersedes_revision_id: Optional[str] = None
    editorial_aliases: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_revision_number(self) -> "ViewpointRevisionRecord":
        if self.revision_number != self.revision:
            raise ValueError("revision_number must equal store revision")
        if self.review_status in {"system_approved", "human_approved", "approved"}:
            if not self.approved_at or not self.approved_by:
                raise ValueError("approved viewpoint revision requires approved_by and approved_at")
        return self


class ViewpointComponentSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    exact_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_span(self) -> "ViewpointComponentSpan":
        if self.end_char <= self.start_char:
            raise ValueError("component span is empty or reversed")
        if len(self.exact_text) != self.end_char - self.start_char:
            raise ValueError("component span exact_text length does not match its range")
        return self


class ViewpointComponentLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement_component: str = Field(min_length=1)
    claim_sha256: str
    canonical_spans: list[ViewpointComponentSpan] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_spans(self) -> "ViewpointComponentLocator":
        spans = [(item.start_char, item.end_char) for item in self.canonical_spans]
        if spans != sorted(set(spans)):
            raise ValueError("component spans must be sorted and unique")
        for earlier, later in zip(spans, spans[1:], strict=False):
            if later[0] < earlier[1]:
                raise ValueError("component spans overlap")
        if self.statement_component != "".join(
            item.exact_text for item in self.canonical_spans
        ):
            raise ValueError("statement_component does not match canonical spans")
        return self


class ViewpointClaimEvidenceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_step_id: str = Field(min_length=1)
    source_fragment_id: str = Field(min_length=1)


class ViewpointClaimLinkRecord(StrictViewpointRecord):
    viewpoint_claim_link_id: str
    schema_version: Literal["wang_viewpoint_claim_link_v1"] = "wang_viewpoint_claim_link_v1"
    viewpoint_id: str
    validated_against_viewpoint_revision_id: str
    claim_id: str
    pinned_claim_revision: int = Field(ge=1)
    link_type: Literal[
        "equivalent_full",
        "equivalent_component",
        "supports",
        "extends",
        "qualifies",
        "applies",
        "tension_evidence",
        "superseding_evidence",
    ]
    component_locator: Optional[ViewpointComponentLocator] = None
    supporting_relation_ids: list[str] = Field(default_factory=list)
    evidence_bindings: list[ViewpointClaimEvidenceBinding] = Field(default_factory=list)
    occurrence_refs: list[str] = Field(default_factory=list)
    decision_id: str
    effective_state: Literal["active", "invalidated", "retired"] = "active"

    @model_validator(mode="after")
    def validate_component(self) -> "ViewpointClaimLinkRecord":
        if self.supporting_relation_ids != sorted(set(self.supporting_relation_ids)):
            raise ValueError("supporting_relation_ids must be sorted and unique")
        if self.occurrence_refs != sorted(set(self.occurrence_refs)):
            raise ValueError("occurrence_refs must be sorted and unique")
        bindings = [
            (item.evidence_step_id, item.source_fragment_id)
            for item in self.evidence_bindings
        ]
        if bindings != sorted(set(bindings)):
            raise ValueError("evidence_bindings must be sorted and unique")
        if self.link_type == "equivalent_component" and not self.component_locator:
            raise ValueError("equivalent_component requires component_locator")
        if self.link_type == "equivalent_full" and self.component_locator is not None:
            raise ValueError("equivalent_full spans the whole Claim and has no locator")
        if self.effective_state == "active" and self.review_status not in {
            "system_approved",
            "human_approved",
            "approved",
        }:
            raise ValueError("active Claim link requires an approved review status")
        return self


class PropositionUnitStatementSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    exact_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_span(self) -> "PropositionUnitStatementSpan":
        if self.end_char <= self.start_char:
            raise ValueError("proposition unit statement span is empty or reversed")
        return self


class PropositionUnitEvidenceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_step_id: str
    source_fragment_id: str


class ViewpointPropositionUnitRecord(StrictViewpointRecord):
    """Durable atomic truth-condition unit promoted from a reviewed candidate."""

    proposition_unit_id: str
    schema_version: Literal["wang_viewpoint_proposition_unit_v1"] = (
        "wang_viewpoint_proposition_unit_v1"
    )
    parent_claim_id: str
    pinned_claim_revision: int = Field(ge=1)
    claim_revision_sha256: str
    source_id: str
    unit_statement: str = Field(min_length=1)
    structural_role: Literal["whole_claim", "conjunct", "qualified_clause"]
    claim_statement_spans: list[PropositionUnitStatementSpan] = Field(min_length=1)
    evidence_bindings: list[PropositionUnitEvidenceBinding] = Field(min_length=1)
    decomposition_artifact_sha256: str
    effective_state: Literal["proposed", "active", "invalidated", "retired"] = "active"

    @model_validator(mode="after")
    def validate_unit(self) -> "ViewpointPropositionUnitRecord":
        spans = [
            (item.start_char, item.end_char, item.exact_text)
            for item in self.claim_statement_spans
        ]
        if spans != sorted(set(spans)):
            raise ValueError("proposition unit spans must be canonical and unique")
        bindings = [
            (item.evidence_step_id, item.source_fragment_id)
            for item in self.evidence_bindings
        ]
        if bindings != sorted(set(bindings)):
            raise ValueError("proposition unit evidence bindings must be canonical and unique")
        if self.effective_state == "active" and self.review_status not in {
            "system_approved",
            "human_approved",
            "approved",
        }:
            raise ValueError("active proposition unit requires approved review status")
        return self


class ViewpointPropositionUnitLinkRecord(StrictViewpointRecord):
    """Exact CanonicalViewpoint membership at the reviewed atomic boundary."""

    viewpoint_proposition_unit_link_id: str
    schema_version: Literal["wang_viewpoint_proposition_unit_link_v1"] = (
        "wang_viewpoint_proposition_unit_link_v1"
    )
    viewpoint_id: str
    validated_against_viewpoint_revision_id: str
    proposition_unit_id: str
    link_type: Literal["equivalent"] = "equivalent"
    decision_id: str
    effective_state: Literal["proposed", "active", "invalidated", "retired"] = "active"

    @model_validator(mode="after")
    def validate_link(self) -> "ViewpointPropositionUnitLinkRecord":
        if self.effective_state == "active" and self.review_status not in {
            "system_approved",
            "human_approved",
            "approved",
        }:
            raise ValueError("active proposition unit link requires approved review status")
        return self


class ViewpointAtomicCoverageSnapshotRecord(StrictViewpointRecord):
    """Closed denominator for one atomic viewpoint promotion decision."""

    atomic_coverage_snapshot_id: str
    schema_version: Literal["wang_viewpoint_atomic_coverage_snapshot_v1"] = (
        "wang_viewpoint_atomic_coverage_snapshot_v1"
    )
    viewpoint_candidate_id: str
    pilot_artifact_sha256: str
    recall_closure_packet_sha256: str
    boundary_run_artifact_sha256: str
    claim_ids: list[str] = Field(min_length=1)
    proposition_unit_ids: list[str] = Field(min_length=2)
    source_ids: list[str] = Field(min_length=1)
    source_eligibility_attestation_sha256s: list[str] = Field(min_length=1)
    coverage_status: Literal["complete"] = "complete"
    artifact_sha256: str
    review_status: Literal["system_verified"] = "system_verified"

    @model_validator(mode="after")
    def validate_snapshot(self) -> "ViewpointAtomicCoverageSnapshotRecord":
        for label, values in (
            ("claim_ids", self.claim_ids),
            ("proposition_unit_ids", self.proposition_unit_ids),
            ("source_ids", self.source_ids),
            (
                "source_eligibility_attestation_sha256s",
                self.source_eligibility_attestation_sha256s,
            ),
        ):
            if values != sorted(set(values)):
                raise ValueError(f"atomic coverage {label} must be sorted and unique")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != _knowledge_sha256_json(payload):
            raise ValueError("atomic coverage snapshot SHA mismatch")
        return self


class ViewpointAtomicResolutionRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposition_unit_id: str
    parent_claim_id: str
    disposition: Literal["member", "adjacent_non_member"]
    identity_decision_id: str
    boundary_run_artifact_sha256: str
    evidence_binding_sha256: str


class ViewpointAtomicResolutionStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_unit_count: int = Field(ge=0)
    member_count: int = Field(ge=0)
    adjacent_non_member_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)


class ViewpointAtomicResolutionLedgerRecord(StrictViewpointRecord):
    """Exact-once member/non-member disposition for an atomic universe."""

    atomic_resolution_ledger_id: str
    schema_version: Literal["wang_viewpoint_atomic_resolution_ledger_v1"] = (
        "wang_viewpoint_atomic_resolution_ledger_v1"
    )
    atomic_coverage_snapshot_id: str
    viewpoint_candidate_id: str
    proposed_viewpoint_id: str
    identity_decision_id: str
    rows: list[ViewpointAtomicResolutionRow] = Field(min_length=2)
    statistics: ViewpointAtomicResolutionStatistics
    coverage_status: Literal["complete"] = "complete"
    artifact_sha256: str
    review_status: Literal["system_verified"] = "system_verified"

    @model_validator(mode="after")
    def validate_ledger(self) -> "ViewpointAtomicResolutionLedgerRecord":
        unit_ids = [item.proposition_unit_id for item in self.rows]
        if unit_ids != sorted(set(unit_ids)):
            raise ValueError("atomic resolution rows must be unit-sorted and unique")
        expected = {
            "input_unit_count": len(self.rows),
            "member_count": sum(item.disposition == "member" for item in self.rows),
            "adjacent_non_member_count": sum(
                item.disposition == "adjacent_non_member" for item in self.rows
            ),
            "unresolved_count": 0,
        }
        if self.statistics.model_dump(mode="json") != expected:
            raise ValueError("atomic resolution statistics mismatch")
        if any(
            item.identity_decision_id != self.identity_decision_id for item in self.rows
        ):
            raise ValueError("atomic resolution row decision mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != _knowledge_sha256_json(payload):
            raise ValueError("atomic resolution ledger SHA mismatch")
        return self


class ViewpointAtomicQualityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "article_acceptance_bound",
        "atomic_resolution_exact_once",
        "consumer_projection_bound",
        "dual_model_boundary_agreed",
        "master_preview_matches_resolution",
        "source_evidence_bound",
        "targeted_recall_closed",
    ]
    status: Literal["pass", "fail"]
    evidence_artifact_sha256s: list[str] = Field(min_length=1)
    detail: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_check(self) -> "ViewpointAtomicQualityCheck":
        if self.evidence_artifact_sha256s != sorted(
            set(self.evidence_artifact_sha256s)
        ):
            raise ValueError("atomic quality evidence SHAs must be sorted and unique")
        return self


class ViewpointAtomicQualityReportRecord(StrictViewpointRecord):
    atomic_quality_report_id: str
    schema_version: Literal["wang_viewpoint_atomic_quality_report_v1"] = (
        "wang_viewpoint_atomic_quality_report_v1"
    )
    viewpoint_candidate_id: str
    proposed_viewpoint_id: str
    atomic_coverage_snapshot_id: str
    atomic_resolution_ledger_id: str
    promotion_proposal_artifact_sha256: str
    consumer_projection_sha256: str
    checks: list[ViewpointAtomicQualityCheck] = Field(min_length=1)
    hard_failures: list[str] = Field(default_factory=list)
    eligibility_decision: Literal["pass", "fail"]
    validator_version: Literal["matthew16_atomic_promotion_quality_v1"] = (
        "matthew16_atomic_promotion_quality_v1"
    )
    artifact_sha256: str
    review_status: Literal["system_verified"] = "system_verified"

    @model_validator(mode="after")
    def validate_report(self) -> "ViewpointAtomicQualityReportRecord":
        codes = [item.code for item in self.checks]
        if codes != sorted(set(codes)):
            raise ValueError("atomic quality checks must be code-sorted and unique")
        expected_codes = {
            "article_acceptance_bound",
            "atomic_resolution_exact_once",
            "consumer_projection_bound",
            "dual_model_boundary_agreed",
            "master_preview_matches_resolution",
            "source_evidence_bound",
            "targeted_recall_closed",
        }
        if set(codes) != expected_codes:
            raise ValueError("atomic quality report must contain every required check")
        failed = [item.code for item in self.checks if item.status == "fail"]
        if self.hard_failures != sorted(set(self.hard_failures)):
            raise ValueError("atomic quality hard failures must be sorted and unique")
        if bool(failed or self.hard_failures) != (self.eligibility_decision == "fail"):
            raise ValueError("atomic quality eligibility decision mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != _knowledge_sha256_json(payload):
            raise ValueError("atomic quality report SHA mismatch")
        return self


class ViewpointAutomatedPromotionDecisionRecord(StrictViewpointRecord):
    automated_promotion_decision_id: str
    schema_version: Literal["wang_viewpoint_automated_promotion_decision_v1"] = (
        "wang_viewpoint_automated_promotion_decision_v1"
    )
    viewpoint_candidate_id: str
    viewpoint_id: str
    viewpoint_revision_id: str
    identity_decision_id: str
    promotion_proposal_artifact_sha256: str
    atomic_coverage_snapshot_artifact_sha256: str
    atomic_resolution_ledger_artifact_sha256: str
    atomic_quality_report_artifact_sha256: str
    consumer_projection_sha256: str
    decision: Literal["approve", "reject"]
    approval_basis: Literal["programmatic_atomic_quality_gate"] = (
        "programmatic_atomic_quality_gate"
    )
    human_approval: Literal[False] = False
    applied_record_ids: list[str] = Field(min_length=1)
    decided_at: str
    artifact_sha256: str
    review_status: Literal["system_approved"] = "system_approved"

    @model_validator(mode="after")
    def validate_decision(self) -> "ViewpointAutomatedPromotionDecisionRecord":
        if self.applied_record_ids != sorted(set(self.applied_record_ids)):
            raise ValueError("automated promotion record ids must be sorted and unique")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != _knowledge_sha256_json(payload):
            raise ValueError("automated promotion decision SHA mismatch")
        return self


class ArgumentRouteSignature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inference_method_codes: list[str] = Field(min_length=1)
    inference_method_note: Optional[str] = None
    conclusion_viewpoint_id: str

    @model_validator(mode="after")
    def validate_methods(self) -> "ArgumentRouteSignature":
        if self.inference_method_codes != sorted(set(self.inference_method_codes)):
            raise ValueError("inference_method_codes must be sorted and unique")
        if "other" in self.inference_method_codes and not self.inference_method_note:
            raise ValueError("other inference method requires a note")
        return self


class ArgumentRouteInferenceNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_step_key: str = Field(min_length=1)
    role: Literal[
        "observation", "premise", "bridge", "objection", "response",
        "qualification", "conclusion", "application",
    ]
    normalized_proposition: Optional[str] = None
    conclusion_viewpoint_revision_id: Optional[str] = None
    required_for_full_attestation: bool

    @model_validator(mode="after")
    def validate_node(self) -> "ArgumentRouteInferenceNode":
        if self.role == "conclusion":
            if not self.conclusion_viewpoint_revision_id:
                raise ValueError("conclusion node requires viewpoint revision")
            if self.normalized_proposition is not None:
                raise ValueError("conclusion node does not duplicate normalized proposition")
        else:
            if not self.normalized_proposition:
                raise ValueError(f"{self.role} node requires normalized proposition")
            if self.conclusion_viewpoint_revision_id is not None:
                raise ValueError("only conclusion node carries viewpoint revision")
        return self


class ArgumentRouteRecord(StrictViewpointRecord):
    argument_route_id: str
    schema_version: Literal["wang_argument_route_v1"] = "wang_argument_route_v1"
    conclusion_viewpoint_id: str
    current_revision_id: str
    route_status: Literal["active", "redirected", "retired"] = "active"
    redirect_to_argument_route_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_redirect(self) -> "ArgumentRouteRecord":
        if self.route_status == "redirected" and not self.redirect_to_argument_route_id:
            raise ValueError("redirected route requires redirect_to_argument_route_id")
        if self.redirect_to_argument_route_id == self.argument_route_id:
            raise ValueError("argument route cannot redirect to itself")
        return self


class ArgumentRouteRevisionRecord(StrictViewpointRecord):
    argument_route_revision_id: str
    schema_version: Literal["wang_argument_route_revision_v2"] = "wang_argument_route_revision_v2"
    argument_route_id: str
    revision_number: int = Field(ge=1)
    validated_against_conclusion_viewpoint_revision_id: str
    route_label: str = Field(min_length=1)
    route_signature: ArgumentRouteSignature
    ordered_inference_nodes: list[ArgumentRouteInferenceNode] = Field(min_length=2)
    representation_kind: Literal["editorial_normalization_of_attested_arguments"] = (
        "editorial_normalization_of_attested_arguments"
    )
    review_artifact_sha256: str
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    supersedes_revision_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_revision(self) -> "ArgumentRouteRevisionRecord":
        if self.revision_number != self.revision:
            raise ValueError("revision_number must equal store revision")
        if self.review_status in {"system_approved", "human_approved", "approved"}:
            if not self.approved_by or not self.approved_at:
                raise ValueError("approved route revision requires approved_by and approved_at")
        keys = [item.route_step_key for item in self.ordered_inference_nodes]
        if keys != list(dict.fromkeys(keys)):
            raise ValueError("route step keys must be unique")
        conclusions = [item for item in self.ordered_inference_nodes if item.role == "conclusion"]
        if len(conclusions) != 1 or self.ordered_inference_nodes[-1].role != "conclusion":
            raise ValueError("route must end in exactly one conclusion node")
        if (
            conclusions[0].conclusion_viewpoint_revision_id
            != self.validated_against_conclusion_viewpoint_revision_id
        ):
            raise ValueError("route conclusion node revision mismatch")
        return self


class ArgumentRouteStepBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_step_key: str = Field(min_length=1)
    claim_component_keys: list[str] = Field(default_factory=list)
    evidence_step_ids: list[str] = Field(default_factory=list)
    source_fragment_ids: list[str] = Field(default_factory=list)
    attestation_status: Literal["attested", "missing", "ambiguous"]

    @model_validator(mode="after")
    def validate_binding(self) -> "ArgumentRouteStepBinding":
        for values, label in (
            (self.claim_component_keys, "claim_component_keys"),
            (self.evidence_step_ids, "evidence_step_ids"),
            (self.source_fragment_ids, "source_fragment_ids"),
        ):
            if values != sorted(set(values)):
                raise ValueError(f"{label} must be sorted and unique")
        if self.attestation_status == "attested" and not (
            self.claim_component_keys and self.evidence_step_ids and self.source_fragment_ids
        ):
            raise ValueError("attested route step requires component, evidence and fragment")
        return self


class ArgumentRouteAttestationRecord(StrictViewpointRecord):
    argument_route_attestation_id: str
    schema_version: Literal["wang_argument_route_attestation_v2"] = "wang_argument_route_attestation_v2"
    argument_route_id: str
    validated_against_route_revision_id: str
    source_id: str
    source_revision_sha256: str = Field(min_length=1)
    claim_ids: list[str] = Field(min_length=1)
    occurrence_ref_id: str
    step_bindings: list[ArgumentRouteStepBinding] = Field(min_length=1)
    terminal_claim_link_id: str
    completeness: Literal["full", "partial"]
    scripture_refs_derived: list[str] = Field(default_factory=list)
    review_artifact_sha256: str = Field(min_length=1)
    effective_state: Literal["active", "invalidated", "retired"] = "active"

    @model_validator(mode="after")
    def validate_ordered_ids(self) -> "ArgumentRouteAttestationRecord":
        if self.claim_ids != sorted(set(self.claim_ids)):
            raise ValueError("claim_ids must be sorted and unique")
        keys = [item.route_step_key for item in self.step_bindings]
        if len(keys) != len(set(keys)):
            raise ValueError("step_bindings must bind each route node at most once")
        if self.scripture_refs_derived != sorted(set(self.scripture_refs_derived)):
            raise ValueError("scripture_refs_derived must be sorted and unique")
        if self.effective_state == "active" and self.review_status not in {
            "system_approved", "human_approved", "approved"
        }:
            raise ValueError("active route attestation requires approved review status")
        return self


class ViewpointTemporalAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asserted_at: str
    effective_from: Optional[str] = None
    correction_evidence_claim_ids: list[str] = Field(default_factory=list)


STRUCTURE_ROLES = (
    "central_claim",
    "negative_boundary",
    "positive_identification",
    "supporting_conclusion",
    "qualification",
    "tension_side",
    "application",
    "methodological_boundary",
)


class ViewpointStructureFocal(StrictViewpointRecord):
    """One approved viewpoint's place in a reviewed centre."""

    viewpoint_revision_id: str
    structure_role: Literal[STRUCTURE_ROLES]  # type: ignore[valid-type]
    basis_claim_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_focal(self) -> "ViewpointStructureFocal":
        if self.basis_claim_ids != sorted(set(self.basis_claim_ids)):
            raise ValueError("basis_claim_ids must be sorted and unique")
        return self


class ViewpointStructureRecord(StrictViewpointRecord):
    """Stable identity for one reviewed centre.

    A structure organises approved viewpoints; it never asserts a new one. It
    is not an identity parent -- a viewpoint may sit in several overlapping
    structures, and retiring a structure leaves its viewpoints untouched.
    """

    structure_id: str
    schema_version: Literal["wang_viewpoint_structure_v1"] = "wang_viewpoint_structure_v1"
    current_revision_id: str
    effective_state: Literal["active", "retired"] = "active"


class ViewpointStructureRevisionRecord(StrictViewpointRecord):
    structure_revision_id: str
    schema_version: Literal["wang_viewpoint_structure_revision_v1"] = (
        "wang_viewpoint_structure_revision_v1"
    )
    structure_id: str
    revision_number: int = Field(ge=1)
    central_synthesis: str = Field(min_length=1)
    representation_kind: Literal["reviewed_editorial_normalization_of_source_claims"] = (
        "reviewed_editorial_normalization_of_source_claims"
    )
    not_a_direct_quote: Literal[True] = True
    focal_viewpoints: list[ViewpointStructureFocal] = Field(min_length=1)
    unresolved_items: list[str] = Field(default_factory=list)
    scope_manifest_sha256: str
    supersedes_revision_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_structure_revision(self) -> "ViewpointStructureRevisionRecord":
        revisions = [item.viewpoint_revision_id for item in self.focal_viewpoints]
        if len(revisions) != len(set(revisions)):
            raise ValueError("a viewpoint may hold only one role in a structure")
        if self.supersedes_revision_id == self.structure_revision_id:
            raise ValueError("structure revision cannot supersede itself")
        return self


class ViewpointRelationRecord(StrictViewpointRecord):
    viewpoint_relation_id: str
    schema_version: Literal["wang_viewpoint_relation_v1"] = "wang_viewpoint_relation_v1"
    source_viewpoint_id: str
    target_viewpoint_id: str
    validated_source_viewpoint_revision_id: str
    validated_target_viewpoint_revision_id: str
    relation_type: Literal[
        "generalizes", "specializes", "entails", "extends", "qualifies",
        "applies", "tensions_with", "supersedes",
    ]
    reason: str = Field(min_length=1)
    supporting_claim_relation_ids: list[str] = Field(default_factory=list)
    supporting_claim_ids: list[str] = Field(default_factory=list)
    temporal_assertion: Optional[ViewpointTemporalAssertion] = None
    effective_state: Literal["active", "invalidated", "retired"] = "active"

    @model_validator(mode="after")
    def validate_relation(self) -> "ViewpointRelationRecord":
        if self.source_viewpoint_id == self.target_viewpoint_id:
            raise ValueError("viewpoint relation endpoints must differ")
        for field_name in ("supporting_claim_relation_ids", "supporting_claim_ids"):
            values = getattr(self, field_name)
            if values != sorted(set(values)):
                raise ValueError(f"{field_name} must be sorted and unique")
        if self.relation_type == "tensions_with" and self.source_viewpoint_id > self.target_viewpoint_id:
            raise ValueError("tensions_with endpoints must use canonical lexical order")
        if self.relation_type == "supersedes":
            if not self.temporal_assertion or not self.temporal_assertion.correction_evidence_claim_ids:
                raise ValueError("supersedes requires temporal assertion and correction evidence")
        elif self.temporal_assertion is not None:
            raise ValueError("temporal_assertion is only valid for supersedes")
        return self


class ViewpointIdentityCandidateRecord(StrictViewpointRecord):
    identity_candidate_id: str
    schema_version: Literal["wang_viewpoint_identity_candidate_v1"] = (
        "wang_viewpoint_identity_candidate_v1"
    )
    candidate_claim_ids: list[str] = Field(min_length=1)
    candidate_viewpoint_ids: list[str] = Field(default_factory=list)
    seed_relation_ids: list[str] = Field(default_factory=list)
    proposed_action: Literal["match_existing", "create_new", "defer"]
    proposed_proposition_signature: Optional[ViewpointPropositionSignature] = None
    coverage_snapshot_id: Optional[str] = None
    scope_manifest_sha256: Optional[str] = None
    blocker_codes: list[ViewpointBlockerCode] = Field(default_factory=list)
    generation_fingerprint: str
    review_status: Literal["candidate"] = "candidate"

    @model_validator(mode="after")
    def validate_candidate(self) -> "ViewpointIdentityCandidateRecord":
        if self.candidate_claim_ids != sorted(set(self.candidate_claim_ids)):
            raise ValueError("candidate_claim_ids must be sorted and unique")
        if self.candidate_viewpoint_ids != sorted(set(self.candidate_viewpoint_ids)):
            raise ValueError("candidate_viewpoint_ids must be sorted and unique")
        if self.seed_relation_ids != sorted(set(self.seed_relation_ids)):
            raise ValueError("seed_relation_ids must be sorted and unique")
        if self.blocker_codes != sorted(set(self.blocker_codes)):
            raise ValueError("blocker_codes must be sorted and unique")
        if self.proposed_action == "match_existing" and not self.candidate_viewpoint_ids:
            raise ValueError("match_existing requires a candidate viewpoint")
        if self.blocker_codes and self.proposed_action != "defer":
            raise ValueError("blocked candidate must be deferred")
        if bool(self.coverage_snapshot_id) == bool(self.scope_manifest_sha256):
            raise ValueError(
                "identity candidate requires exactly one legacy coverage or scope manifest binding"
            )
        return self


class ViewpointClaimLinkDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    link_type: Literal[
        "equivalent_full",
        "equivalent_component",
        "supports",
        "extends",
        "qualifies",
        "applies",
        "tension_evidence",
        "superseding_evidence",
    ]


class ViewpointPropositionUnitLinkDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposition_unit_id: str
    link_type: Literal["equivalent"] = "equivalent"


class ViewpointIdentityDecisionRecord(StrictViewpointRecord):
    identity_decision_id: str
    schema_version: Literal["wang_viewpoint_identity_decision_v1"] = (
        "wang_viewpoint_identity_decision_v1"
    )
    identity_candidate_id: str
    decision: Literal[
        "match_existing",
        "create_new",
        "reject_match",
        "defer",
        "merge_identities",
        "split_identity",
        "retire_identity",
    ]
    resolved_viewpoint_id: Optional[str] = None
    claim_link_decisions: list[ViewpointClaimLinkDecision] = Field(default_factory=list)
    proposition_unit_link_decisions: list[ViewpointPropositionUnitLinkDecision] = Field(
        default_factory=list
    )
    reviewer_kind: Literal["system", "human_editor"]
    reviewer_id: str
    approval_basis: Literal["deterministic", "dual_model_consensus", "human_exception_review"]
    reason: str = Field(min_length=1)
    input_sha256: str
    review_artifact_sha256: Optional[str] = None
    policy_version: Optional[str] = None
    reviewer_model_ids: list[str] = Field(default_factory=list)
    semantic_call_artifact_sha256s: list[str] = Field(default_factory=list)
    created_at: str
    review_status: Literal[
        "candidate", "system_approved", "human_approved", "approved", "rejected"
    ] = "candidate"

    @model_validator(mode="after")
    def validate_decision(self) -> "ViewpointIdentityDecisionRecord":
        link_decisions = [
            (item.claim_id, item.link_type) for item in self.claim_link_decisions
        ]
        if len(link_decisions) != len(set(link_decisions)):
            raise ValueError("claim_link_decisions must be unique")
        unit_link_decisions = [
            (item.proposition_unit_id, item.link_type)
            for item in self.proposition_unit_link_decisions
        ]
        if len(unit_link_decisions) != len(set(unit_link_decisions)):
            raise ValueError("proposition_unit_link_decisions must be unique")
        if self.decision in {"match_existing", "create_new", "merge_identities"}:
            if not self.resolved_viewpoint_id:
                raise ValueError(f"{self.decision} requires resolved_viewpoint_id")
        if self.reviewer_kind == "system" and self.approval_basis == "human_exception_review":
            raise ValueError("system reviewer cannot claim human_exception_review")
        if self.reviewer_kind == "human_editor" and self.approval_basis != "human_exception_review":
            raise ValueError("human editor decision requires human_exception_review basis")
        if self.reviewer_kind == "system" and self.review_status in {"human_approved", "approved"}:
            raise ValueError("system reviewer cannot create human approval")
        if self.reviewer_kind == "human_editor" and self.review_status == "system_approved":
            raise ValueError("human editor decision cannot be labeled system_approved")
        if self.reviewer_model_ids != sorted(set(self.reviewer_model_ids)):
            raise ValueError("reviewer_model_ids must be sorted and unique")
        if self.semantic_call_artifact_sha256s != sorted(
            set(self.semantic_call_artifact_sha256s)
        ):
            raise ValueError(
                "semantic_call_artifact_sha256s must be sorted and unique"
            )
        if self.review_status == "system_approved":
            if self.approval_basis != "dual_model_consensus":
                raise ValueError("system-approved identity requires dual-model consensus")
            if (
                not self.review_artifact_sha256
                or not self.policy_version
                or len(self.reviewer_model_ids) != 2
                or len(self.semantic_call_artifact_sha256s) != 2
            ):
                raise ValueError(
                    "system-approved identity requires complete review provenance"
                )
        return self


class ViewpointResolutionRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    pinned_claim_revision: int = Field(ge=1)
    claim_revision_sha256: str
    processing_status: Literal["resolved", "source_ineligible", "deferred", "unprocessed"]
    resolution_kind: Optional[
        Literal[
            "member_existing",
            "new_viewpoint_candidate",
            "related_only",
            "no_registry_assertion",
        ]
    ] = None
    primary_viewpoint_id: Optional[str] = None
    new_viewpoint_candidate_id: Optional[str] = None
    viewpoint_claim_link_id: Optional[str] = None
    secondary_link_ids: list[str] = Field(default_factory=list)
    source_eligibility_reason_code: Optional[ViewpointSourceIneligibilityReason] = None
    resolution_reason_code: Optional[ViewpointNoAssertionReason] = None
    blocker_codes: list[ViewpointBlockerCode] = Field(default_factory=list)
    decision_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_resolution(self) -> "ViewpointResolutionRow":
        if self.secondary_link_ids != sorted(set(self.secondary_link_ids)):
            raise ValueError("secondary_link_ids must be sorted and unique")
        if self.blocker_codes != sorted(set(self.blocker_codes)):
            raise ValueError("blocker_codes must be sorted and unique")
        identity_fields = (
            self.primary_viewpoint_id,
            self.new_viewpoint_candidate_id,
            self.viewpoint_claim_link_id,
            self.secondary_link_ids,
            self.decision_id,
        )
        if self.processing_status != "resolved":
            if self.resolution_kind is not None or any(identity_fields):
                raise ValueError("non-resolved row cannot carry identity resolution fields")
            if self.resolution_reason_code is not None:
                raise ValueError("non-resolved row cannot carry resolution_reason_code")
            if self.processing_status == "source_ineligible":
                if not self.source_eligibility_reason_code:
                    raise ValueError("source_ineligible requires a closed reason code")
                if self.blocker_codes:
                    raise ValueError("source_ineligible cannot carry blocker_codes")
            elif self.processing_status == "deferred":
                if not self.blocker_codes:
                    raise ValueError("deferred requires blocker_codes")
                if self.source_eligibility_reason_code is not None:
                    raise ValueError("deferred cannot carry source_eligibility_reason_code")
            elif self.source_eligibility_reason_code is not None or self.blocker_codes:
                raise ValueError("unprocessed row cannot carry reason codes or blockers")
            return self
        if not self.resolution_kind or not self.decision_id:
            raise ValueError("resolved row requires resolution_kind and decision_id")
        if self.source_eligibility_reason_code is not None:
            raise ValueError("resolved row cannot carry source_eligibility_reason_code")
        if self.resolution_kind == "member_existing":
            if not self.primary_viewpoint_id or not self.viewpoint_claim_link_id:
                raise ValueError("member_existing requires viewpoint and claim link")
        elif self.resolution_kind == "new_viewpoint_candidate":
            if not self.new_viewpoint_candidate_id:
                raise ValueError("new_viewpoint_candidate requires candidate id")
        elif self.resolution_kind == "related_only":
            if not self.secondary_link_ids:
                raise ValueError("related_only requires a typed secondary link")
        elif self.resolution_kind == "no_registry_assertion":
            if not self.resolution_reason_code:
                raise ValueError("no_registry_assertion requires a closed reason code")
        if self.resolution_kind != "no_registry_assertion" and self.resolution_reason_code:
            raise ValueError(
                "resolution_reason_code is only valid for no_registry_assertion"
            )
        return self


class ViewpointResolutionStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_claim_count: int = Field(ge=0)
    resolved_count: int = Field(ge=0)
    source_ineligible_count: int = Field(ge=0)
    deferred_count: int = Field(ge=0)
    unprocessed_count: int = Field(ge=0)


class ViewpointResolutionLedgerRecord(StrictViewpointRecord):
    resolution_ledger_id: str
    schema_version: Literal["wang_viewpoint_resolution_ledger_v1"] = (
        "wang_viewpoint_resolution_ledger_v1"
    )
    coverage_snapshot_id: str
    input_claim_manifest_sha256: str
    eligibility_policy_version: str
    candidate_blocking_version: str
    rows: list[ViewpointResolutionRow] = Field(default_factory=list)
    statistics: ViewpointResolutionStatistics
    coverage_status: Literal["partial", "complete"]
    build_fingerprint_sha256: str
    artifact_sha256: str
    review_status: Literal["system_verified"] = "system_verified"

    @model_validator(mode="after")
    def validate_statistics(self) -> "ViewpointResolutionLedgerRecord":
        keys = [(item.claim_id, item.pinned_claim_revision) for item in self.rows]
        if keys != sorted(keys):
            raise ValueError("resolution rows must be sorted by claim id and revision")
        if len(keys) != len(set(keys)):
            raise ValueError("resolution ledger contains duplicate Claim revisions")
        expected = {
            "input_claim_count": len(self.rows),
            "resolved_count": sum(item.processing_status == "resolved" for item in self.rows),
            "source_ineligible_count": sum(
                item.processing_status == "source_ineligible" for item in self.rows
            ),
            "deferred_count": sum(item.processing_status == "deferred" for item in self.rows),
            "unprocessed_count": sum(item.processing_status == "unprocessed" for item in self.rows),
        }
        if self.statistics.model_dump() != expected:
            raise ValueError("resolution statistics do not match rows")
        expected_status = "complete" if expected["unprocessed_count"] == 0 else "partial"
        if self.coverage_status != expected_status:
            raise ValueError("coverage_status does not match unprocessed rows")
        return self


class ViewpointQualityDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: ViewpointQualityDimensionName
    applicable: bool
    minimum_policy: str
    observed: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pass", "fail", "not_applicable"]
    evidence_artifact_sha256s: list[str] = Field(default_factory=list)
    reason_not_applicable: Optional[str] = None

    @model_validator(mode="after")
    def validate_applicability(self) -> "ViewpointQualityDimension":
        if self.applicable and self.status == "not_applicable":
            raise ValueError("applicable dimension needs pass or fail")
        if not self.applicable:
            if self.status != "not_applicable" or not self.reason_not_applicable:
                raise ValueError("non-applicable dimension needs status and reason")
        return self


class ViewpointQualityFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    dimension: ViewpointQualityDimensionName
    record_ids: list[str] = Field(default_factory=list)
    detail: str


class ViewpointQualityReportRecord(StrictViewpointRecord):
    quality_report_id: str
    schema_version: Literal["wang_viewpoint_quality_report_v1"] = (
        "wang_viewpoint_quality_report_v1"
    )
    scope_kind: Literal["identity_decision", "registry_snapshot", "consumer_projection"]
    scope_ids: list[str] = Field(min_length=1)
    coverage_snapshot_id: str
    resolution_ledger_id: str
    input_artifact_sha256s: list[str] = Field(min_length=1)
    dimensions: list[ViewpointQualityDimension] = Field(min_length=1)
    hard_failures: list[ViewpointQualityFailure] = Field(default_factory=list)
    eligibility_decision: Literal["pass", "fail", "partial_internal_only"]
    validator_version: str
    build_fingerprint_sha256: str
    artifact_sha256: str
    review_status: Literal["system_verified"] = "system_verified"

    @model_validator(mode="after")
    def validate_decision(self) -> "ViewpointQualityReportRecord":
        if self.scope_ids != sorted(set(self.scope_ids)):
            raise ValueError("quality report scope_ids must be sorted and unique")
        if self.input_artifact_sha256s != sorted(set(self.input_artifact_sha256s)):
            raise ValueError("quality report input artifact SHAs must be sorted and unique")
        names = [item.dimension for item in self.dimensions]
        if len(names) != len(set(names)):
            raise ValueError("quality report contains duplicate dimensions")
        if set(names) != VIEWPOINT_QUALITY_DIMENSIONS:
            missing = sorted(VIEWPOINT_QUALITY_DIMENSIONS - set(names))
            extra = sorted(set(names) - VIEWPOINT_QUALITY_DIMENSIONS)
            raise ValueError(
                f"quality report must contain every dimension; missing={missing}, extra={extra}"
            )
        dimension_status = {item.dimension: item.status for item in self.dimensions}
        invalid_failures = sorted(
            {
                failure.dimension
                for failure in self.hard_failures
                if dimension_status[failure.dimension] != "fail"
            }
        )
        if invalid_failures:
            raise ValueError(
                "hard failures require their dimensions to fail: "
                f"{invalid_failures}"
            )
        failed = bool(self.hard_failures) or any(
            item.applicable and item.status == "fail" for item in self.dimensions
        )
        if failed and self.eligibility_decision != "fail":
            raise ValueError("quality failures require eligibility_decision=fail")
        if not failed and self.eligibility_decision == "fail":
            raise ValueError(
                "eligibility_decision=fail requires a failed dimension or hard failure"
            )
        if self.eligibility_decision == "pass" and any(
            item.applicable and item.status != "pass" for item in self.dimensions
        ):
            raise ValueError("pass requires every applicable dimension to pass")
        return self


class KnowledgePackageManifest(BaseModel):
    schema_version: str = "canonical_knowledge_package_manifest_v1"
    package_id: str
    source_schema_version: str | int
    source_sha256: str
    imported_at: str
    counts: dict[str, int]
    record_ids: dict[str, list[str]]


KNOWLEDGE_COLLECTIONS: dict[str, tuple[type[EvolvingKnowledgeRecord], str]] = {
    "source_documents": (KnowledgeSourceDocument, "source_id"),
    "source_fragments": (SourceFragmentRecord, "fragment_id"),
    "questions": (QuestionRecord, "question_id"),
    "observations": (ObservationRecord, "observation_id"),
    "claims": (ClaimRecord, "claim_id"),
    "topic_nodes": (TopicNodeRecord, "topic_id"),
    "topic_identity_reconciliations": (
        TopicIdentityReconciliationRecord,
        "reconciliation_id",
    ),
    "evidence_steps": (EvidenceStepRecord, "evidence_step_id"),
    "knowledge_relations": (KnowledgeRelationRecord, "relation_id"),
    "claim_relations": (ClaimRelationRecord, "claim_relation_id"),
    "claim_relation_constraints": (ClaimRelationConstraintRecord, "constraint_id"),
    "position_nodes": (PositionNodeRecord, "position_id"),
    "knowledge_routes": (KnowledgeRouteRecord, "route_id"),
    "product_dependencies": (ProductDependencyRecord, "dependency_id"),
    "impact_events": (ImpactEventRecord, "impact_event_id"),
    "editorial_syntheses": (EditorialSynthesisRecord, "synthesis_id"),
    "composition_plans": (CompositionPlanRecord, "plan_id"),
    "composition_decisions": (CompositionDecisionRecord, "decision_id"),
    "editorial_checks": (EditorialCheckRecord, "check_id"),
    "tensions": (TensionRecord, "tension_id"),
    "viewpoint_coverage_snapshots": (
        ViewpointCoverageSnapshotRecord,
        "coverage_snapshot_id",
    ),
    "canonical_viewpoints": (CanonicalViewpointRecord, "viewpoint_id"),
    "viewpoint_structures": (ViewpointStructureRecord, "structure_id"),
    "viewpoint_structure_revisions": (
        ViewpointStructureRevisionRecord,
        "structure_revision_id",
    ),
    "viewpoint_revisions": (ViewpointRevisionRecord, "viewpoint_revision_id"),
    "viewpoint_claim_links": (ViewpointClaimLinkRecord, "viewpoint_claim_link_id"),
    "viewpoint_proposition_units": (
        ViewpointPropositionUnitRecord,
        "proposition_unit_id",
    ),
    "viewpoint_proposition_unit_links": (
        ViewpointPropositionUnitLinkRecord,
        "viewpoint_proposition_unit_link_id",
    ),
    "viewpoint_atomic_coverage_snapshots": (
        ViewpointAtomicCoverageSnapshotRecord,
        "atomic_coverage_snapshot_id",
    ),
    "viewpoint_atomic_resolution_ledgers": (
        ViewpointAtomicResolutionLedgerRecord,
        "atomic_resolution_ledger_id",
    ),
    "viewpoint_atomic_quality_reports": (
        ViewpointAtomicQualityReportRecord,
        "atomic_quality_report_id",
    ),
    "viewpoint_automated_promotion_decisions": (
        ViewpointAutomatedPromotionDecisionRecord,
        "automated_promotion_decision_id",
    ),
    "argument_routes": (ArgumentRouteRecord, "argument_route_id"),
    "argument_route_revisions": (ArgumentRouteRevisionRecord, "argument_route_revision_id"),
    "argument_route_attestations": (ArgumentRouteAttestationRecord, "argument_route_attestation_id"),
    "viewpoint_relations": (ViewpointRelationRecord, "viewpoint_relation_id"),
    "viewpoint_identity_candidates": (
        ViewpointIdentityCandidateRecord,
        "identity_candidate_id",
    ),
    "viewpoint_identity_decisions": (
        ViewpointIdentityDecisionRecord,
        "identity_decision_id",
    ),
    "viewpoint_resolution_ledgers": (
        ViewpointResolutionLedgerRecord,
        "resolution_ledger_id",
    ),
    "viewpoint_quality_reports": (
        ViewpointQualityReportRecord,
        "quality_report_id",
    ),
}
