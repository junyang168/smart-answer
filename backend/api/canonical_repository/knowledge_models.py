from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


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


class RequiredArgumentStep(BaseModel):
    """A load-bearing step of the base manuscript an article must preserve.

    `source_excerpt` is the verbatim sentence the step is drawn from, so a
    programme can verify the step against the manuscript rather than trusting
    an author's self-report that it was preserved.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    step_id: str
    statement: str
    source_id: str
    source_excerpt: str
    role: str = ""
    source_span: str = ""
    required: bool = True


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
    required_argument_steps: list[RequiredArgumentStep] = Field(default_factory=list)
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
}
