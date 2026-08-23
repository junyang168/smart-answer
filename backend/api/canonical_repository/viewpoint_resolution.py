"""Automation-first CanonicalViewpoint identity resolution.

The engine separates model proposals from authoring authority.  Reviewers see
an immutable evidence packet and may describe semantics, but only deterministic
code can compare their answers, apply risk gates, assign ids, or emit a
ChangeSet-ready package.  This module never applies a ChangeSet.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .knowledge_models import (
    CanonicalViewpointRecord,
    ClaimRecord,
    ClaimRelationConstraintRecord,
    ClaimRelationRecord,
    EvidenceStepRecord,
    evidence_fragment_ids,
    SourceFragmentRecord,
    ViewpointClaimLinkRecord,
    ViewpointCoverageSnapshotRecord,
    ViewpointIdentityCandidateRecord,
    ViewpointIdentityDecisionRecord,
    ViewpointPropositionSignature,
    ViewpointQualityReportRecord,
    ViewpointResolutionLedgerRecord,
    ViewpointRevisionRecord,
    ViewpointScope,
)
from .models import Citation
from .viewpoint_foundation import (
    APPROVED_CONSTRAINT_STATUSES,
    REVIEWED_DUPLICATE_STATUSES,
    canonical_json,
    semantic_record_sha,
    sha256_json,
)


RESOLUTION_ENGINE_VERSION = "viewpoint_resolution_engine_v1"
RESOLUTION_POLICY_VERSION = "viewpoint_identity_automation_policy_v1"
REVIEW_PACKET_VERSION = "wang_viewpoint_identity_review_packet_v1"
REVIEW_CALL_VERSION = "wang_viewpoint_identity_review_call_v1"
DELTA_ADJUDICATION_VERSION = "wang_viewpoint_identity_delta_adjudication_v1"
RESOLUTION_RUN_VERSION = "wang_viewpoint_identity_resolution_run_v1"

APPROVED_STATUSES = frozenset({"system_approved", "human_approved", "approved"})
VALID_ANCHOR_STATES = frozenset(
    {"source_version_bound", "canonical_citation_bound", "verified", "valid"}
)
VALID_EVIDENCE_STATES = frozenset({"eligible", "eligible_with_label"})
MATERIAL_RELATION_TYPES = frozenset(
    {"unrelated", "contrasts", "qualifies", "supersedes"}
)
TRUTH_CONDITION_FIELDS = (
    "subject",
    "predicate_object",
    "polarity",
    "population_scope",
    "scripture_scope",
    "temporal_scope",
    "conditions",
    "modality",
    "attribution",
    "material_qualification",
)


class ViewpointResolutionError(ValueError):
    def __init__(self, findings: Sequence[str]):
        self.findings = list(findings)
        super().__init__("Viewpoint resolution failed: " + " | ".join(self.findings))


class StrictArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewEvidence(StrictArtifact):
    evidence_step_id: str
    source_fragment_id: str
    source_id: str
    paragraph_key: str | int | None = None
    media_time: float | None = None
    evidence_statement: str
    verbatim_excerpt: str
    citation_id: str
    citation_revision: int = Field(ge=1)
    citation_status: str
    source_sha256: str
    support_eligibility: str
    anchor_state: str
    valid_for_identity_review: bool

    @model_validator(mode="after")
    def validate_eligibility(self) -> "ReviewEvidence":
        locally_valid = bool(
            self.source_sha256
            and self.citation_status == "approved"
            and self.support_eligibility in VALID_EVIDENCE_STATES
            and self.anchor_state in VALID_ANCHOR_STATES
        )
        if self.valid_for_identity_review and not locally_valid:
            raise ValueError("evidence cannot self-report identity-review validity")
        return self


class ReviewClaim(StrictArtifact):
    claim_id: str
    pinned_claim_revision: int = Field(ge=1)
    claim_revision_sha256: str
    source_id: str
    statement: str
    attribution: str | None = None
    scripture_refs: list[str] = Field(default_factory=list)
    review_status: str
    active_full_viewpoint_id: str | None = None
    evidence: list[ReviewEvidence] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_claim(self) -> "ReviewClaim":
        if self.scripture_refs != sorted(set(self.scripture_refs)):
            raise ValueError("review Claim scripture refs must be sorted and unique")
        evidence_ids = [item.evidence_step_id for item in self.evidence]
        if evidence_ids != sorted(set(evidence_ids)):
            raise ValueError("review Claim evidence must be sorted and unique")
        if any(item.source_id != self.source_id for item in self.evidence):
            raise ValueError("review Claim evidence must remain source-local")
        return self


class ReviewViewpoint(StrictArtifact):
    viewpoint_id: str
    viewpoint_revision_id: str
    core_proposition: str
    proposition_signature: ViewpointPropositionSignature
    scope: ViewpointScope
    review_status: str


class ReviewRelation(StrictArtifact):
    claim_relation_id: str
    from_id: str
    to_id: str
    relation_type: str
    review_status: str
    reason: str


class ReviewConstraint(StrictArtifact):
    constraint_id: str
    source_id: str
    target_id: str
    forbidden_relation_types: list[str]
    review_status: str
    reason: str

    @model_validator(mode="after")
    def validate_types(self) -> "ReviewConstraint":
        if self.forbidden_relation_types != sorted(set(self.forbidden_relation_types)):
            raise ValueError("forbidden relation types must be sorted and unique")
        return self


class DeterministicBlocker(StrictArtifact):
    code: Literal[
        "candidate_declared_blocker",
        "source_maturity",
        "evidence_invalid",
        "external_attribution",
        "ledger_unresolved",
        "quality_gate_failed",
        "material_relation",
        "approved_negative_constraint",
        "stale_dependency",
    ]
    record_ids: list[str] = Field(default_factory=list)
    detail: str

    @model_validator(mode="after")
    def validate_ids(self) -> "DeterministicBlocker":
        if self.record_ids != sorted(set(self.record_ids)):
            raise ValueError("blocker record_ids must be sorted and unique")
        return self


class ViewpointIdentityReviewPacket(StrictArtifact):
    schema_version: Literal["wang_viewpoint_identity_review_packet_v1"] = (
        REVIEW_PACKET_VERSION
    )
    engine_version: Literal["viewpoint_resolution_engine_v1"] = (
        RESOLUTION_ENGINE_VERSION
    )
    policy_version: Literal["viewpoint_identity_automation_policy_v1"] = (
        RESOLUTION_POLICY_VERSION
    )
    candidate: ViewpointIdentityCandidateRecord
    coverage_snapshot_id: str
    coverage_sources_sha256: str
    resolution_ledger_id: str
    resolution_ledger_artifact_sha256: str
    quality_report_id: str
    quality_report_artifact_sha256: str
    claims: list[ReviewClaim] = Field(min_length=1)
    candidate_viewpoints: list[ReviewViewpoint] = Field(default_factory=list)
    reviewed_relations: list[ReviewRelation] = Field(default_factory=list)
    approved_constraints: list[ReviewConstraint] = Field(default_factory=list)
    deterministic_blockers: list[DeterministicBlocker] = Field(default_factory=list)
    packet_sha256: str

    @model_validator(mode="after")
    def validate_packet(self) -> "ViewpointIdentityReviewPacket":
        claim_ids = [item.claim_id for item in self.claims]
        if claim_ids != sorted(set(claim_ids)):
            raise ValueError("review packet claims must be sorted and unique")
        if claim_ids != self.candidate.candidate_claim_ids:
            raise ValueError("review packet claims must exactly match the candidate")
        viewpoint_ids = [item.viewpoint_id for item in self.candidate_viewpoints]
        if viewpoint_ids != sorted(set(viewpoint_ids)):
            raise ValueError("review packet viewpoints must be sorted and unique")
        relation_ids = [item.claim_relation_id for item in self.reviewed_relations]
        if relation_ids != sorted(set(relation_ids)):
            raise ValueError("review packet relations must be sorted and unique")
        constraint_ids = [item.constraint_id for item in self.approved_constraints]
        if constraint_ids != sorted(set(constraint_ids)):
            raise ValueError("review packet constraints must be sorted and unique")
        blockers = [
            canonical_json(item.model_dump(mode="json"))
            for item in self.deterministic_blockers
        ]
        if blockers != sorted(set(blockers)):
            raise ValueError("deterministic blockers must be sorted and unique")
        payload = self.model_dump(mode="json")
        stated = payload.pop("packet_sha256")
        if stated != sha256_json(payload):
            raise ValueError("review packet SHA mismatch")
        return self


Verdict = Literal["compatible", "mismatch", "unknown"]


class TruthConditionVerdicts(StrictArtifact):
    subject: Verdict
    predicate_object: Verdict
    polarity: Verdict
    population_scope: Verdict
    scripture_scope: Verdict
    temporal_scope: Verdict
    conditions: Verdict
    modality: Verdict
    attribution: Verdict
    material_qualification: Verdict


class SemanticMemberAssessment(StrictArtifact):
    claim_id: str
    member_role: Literal[
        "equivalent_full", "equivalent_component", "related_only", "exclude"
    ]
    truth_conditions: TruthConditionVerdicts
    component_statement: str | None = None
    component_json_pointer: str | None = None

    @model_validator(mode="after")
    def validate_component(self) -> "SemanticMemberAssessment":
        has_locator = bool(self.component_statement and self.component_json_pointer)
        if self.member_role == "equivalent_component" and not has_locator:
            raise ValueError("equivalent_component assessment requires a locator proposal")
        if self.member_role != "equivalent_component" and (
            self.component_statement is not None or self.component_json_pointer is not None
        ):
            raise ValueError("component locator proposal is only valid for equivalent_component")
        return self


class SemanticAssessment(StrictArtifact):
    schema_version: Literal["wang_viewpoint_identity_semantic_assessment_v1"] = (
        "wang_viewpoint_identity_semantic_assessment_v1"
    )
    candidate_id: str
    packet_sha256: str
    proposed_action: Literal["match_existing", "create_new", "defer", "reject_match"]
    target_viewpoint_id: str | None = None
    core_proposition: str | None = None
    proposition_signature: ViewpointPropositionSignature | None = None
    scope: ViewpointScope | None = None
    members: list[SemanticMemberAssessment] = Field(min_length=1)
    canonical_wording_conservative: bool
    added_truth_conditions: list[str] = Field(default_factory=list)
    semantic_blockers: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_assessment(self) -> "SemanticAssessment":
        member_ids = [item.claim_id for item in self.members]
        if member_ids != sorted(set(member_ids)):
            raise ValueError("semantic assessment members must be sorted and unique")
        if self.added_truth_conditions != sorted(set(self.added_truth_conditions)):
            raise ValueError("added_truth_conditions must be sorted and unique")
        if self.semantic_blockers != sorted(set(self.semantic_blockers)):
            raise ValueError("semantic_blockers must be sorted and unique")
        if self.proposed_action == "match_existing" and not self.target_viewpoint_id:
            raise ValueError("match_existing requires target_viewpoint_id")
        if self.proposed_action != "match_existing" and self.target_viewpoint_id:
            raise ValueError("target_viewpoint_id is only valid for match_existing")
        if self.proposed_action in {"match_existing", "create_new"} and (
            not self.core_proposition or not self.proposition_signature or not self.scope
        ):
            raise ValueError("identity proposal requires proposition, signature, and scope")
        return self


ReviewStage = Literal["proposal", "blind_review"]


class ReviewCallArtifact(StrictArtifact):
    schema_version: Literal["wang_viewpoint_identity_review_call_v1"] = (
        REVIEW_CALL_VERSION
    )
    stage: ReviewStage
    semantic_call_ordinal: Literal[1, 2]
    packet_sha256: str
    model_id: str
    prompt_sha256: str
    generation_fingerprint_sha256: str
    assessment: SemanticAssessment
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "ReviewCallArtifact":
        expected_ordinal = 1 if self.stage == "proposal" else 2
        if self.semantic_call_ordinal != expected_ordinal:
            raise ValueError("review stage has the wrong semantic call ordinal")
        if self.assessment.packet_sha256 != self.packet_sha256:
            raise ValueError("assessment packet SHA mismatch")
        payload = self.model_dump(mode="json")
        stated = payload.pop("artifact_sha256")
        if stated != sha256_json(payload):
            raise ValueError("review call artifact SHA mismatch")
        return self


class SemanticCallFailureArtifact(StrictArtifact):
    schema_version: Literal["wang_viewpoint_semantic_call_failure_v1"] = (
        "wang_viewpoint_semantic_call_failure_v1"
    )
    stage: Literal["proposal", "blind_review", "delta_adjudication"]
    semantic_call_ordinal: Literal[1, 2, 3]
    packet_sha256: str
    model_id: str
    prompt_sha256: str
    generation_fingerprint_sha256: str
    error: str
    raw_response: Any
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "SemanticCallFailureArtifact":
        payload = self.model_dump(mode="json")
        stated = payload.pop("artifact_sha256")
        if stated != sha256_json(payload):
            raise ValueError("semantic failure artifact SHA mismatch")
        return self


class SemanticDelta(StrictArtifact):
    field_path: str
    proposal_value: Any
    blind_review_value: Any


class DeltaResolution(StrictArtifact):
    field_path: str
    selected_source: Literal["proposal", "blind_review", "unresolved"]
    reason: str = Field(min_length=1)


class DeltaAdjudicationResponse(StrictArtifact):
    resolutions: list[DeltaResolution]
    remaining_findings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_response(self) -> "DeltaAdjudicationResponse":
        paths = [item.field_path for item in self.resolutions]
        if paths != sorted(set(paths)):
            raise ValueError("delta resolutions must be sorted and unique")
        if self.remaining_findings != sorted(set(self.remaining_findings)):
            raise ValueError("remaining findings must be sorted and unique")
        if any(item.selected_source == "unresolved" for item in self.resolutions):
            if not self.remaining_findings:
                raise ValueError("unresolved delta requires a remaining finding")
        return self


class DeltaAdjudicationArtifact(StrictArtifact):
    schema_version: Literal["wang_viewpoint_identity_delta_adjudication_v1"] = (
        DELTA_ADJUDICATION_VERSION
    )
    semantic_call_ordinal: Literal[3] = 3
    packet_sha256: str
    delta_sha256: str
    model_id: str
    prompt_sha256: str
    generation_fingerprint_sha256: str
    resolutions: list[DeltaResolution] = Field(default_factory=list)
    remaining_findings: list[str] = Field(default_factory=list)
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "DeltaAdjudicationArtifact":
        paths = [item.field_path for item in self.resolutions]
        if paths != sorted(set(paths)):
            raise ValueError("delta resolutions must be sorted and unique")
        if self.remaining_findings != sorted(set(self.remaining_findings)):
            raise ValueError("remaining findings must be sorted and unique")
        if any(item.selected_source == "unresolved" for item in self.resolutions):
            if not self.remaining_findings:
                raise ValueError("unresolved delta requires a remaining finding")
        payload = self.model_dump(mode="json")
        stated = payload.pop("artifact_sha256")
        if stated != sha256_json(payload):
            raise ValueError("delta adjudication artifact SHA mismatch")
        return self


class RiskGateCheck(StrictArtifact):
    gate: str
    passed: bool
    detail: str


class ResolutionRiskAssessment(StrictArtifact):
    policy_version: Literal["viewpoint_identity_automation_policy_v1"] = (
        RESOLUTION_POLICY_VERSION
    )
    auto_approval_eligible: bool
    risk_level: Literal["low", "high"]
    checks: list[RiskGateCheck]
    blocker_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result(self) -> "ResolutionRiskAssessment":
        gates = [item.gate for item in self.checks]
        if gates != sorted(set(gates)):
            raise ValueError("risk checks must be sorted and unique")
        if self.blocker_codes != sorted(set(self.blocker_codes)):
            raise ValueError("risk blocker codes must be sorted and unique")
        expected = all(item.passed for item in self.checks) and not self.blocker_codes
        if self.auto_approval_eligible != expected:
            raise ValueError("auto approval result does not match gate checks")
        if self.risk_level != ("low" if expected else "high"):
            raise ValueError("risk level does not match auto approval result")
        return self


class ViewpointExceptionBundle(StrictArtifact):
    schema_version: Literal["wang_viewpoint_identity_exception_bundle_v1"] = (
        "wang_viewpoint_identity_exception_bundle_v1"
    )
    exception_bundle_id: str
    candidate_id: str
    packet_sha256: str
    priority: int = Field(ge=0)
    consumer_impact: Literal["none", "planning", "publication", "withdrawal"]
    blocker_codes: list[str]
    deterministic_blockers: list[DeterministicBlocker]
    semantic_deltas: list[SemanticDelta]
    remaining_findings: list[str]
    claims: list[ReviewClaim]
    proposal: SemanticAssessment
    blind_review: SemanticAssessment
    proposal_artifact_sha256: str
    blind_review_artifact_sha256: str
    delta_adjudication_artifact_sha256: str | None = None
    requested_editor_decision: Literal["identity_bundle"] = "identity_bundle"
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "ViewpointExceptionBundle":
        for field_name in ("blocker_codes", "remaining_findings"):
            values = getattr(self, field_name)
            if values != sorted(set(values)):
                raise ValueError(f"{field_name} must be sorted and unique")
        delta_paths = [item.field_path for item in self.semantic_deltas]
        if delta_paths != sorted(set(delta_paths)):
            raise ValueError("semantic deltas must be sorted and unique")
        claim_ids = [item.claim_id for item in self.claims]
        if claim_ids != sorted(set(claim_ids)):
            raise ValueError("exception claims must be sorted and unique")
        payload = self.model_dump(mode="json")
        stated = payload.pop("artifact_sha256")
        if stated != sha256_json(payload):
            raise ValueError("exception bundle artifact SHA mismatch")
        identity_payload = dict(payload)
        identity_payload.pop("exception_bundle_id")
        expected_id = f"VEX-{sha256_json(identity_payload)[:20]}"
        if self.exception_bundle_id != expected_id:
            raise ValueError("unstable exception bundle id")
        return self


class ViewpointExceptionQueueArtifact(StrictArtifact):
    schema_version: Literal["wang_viewpoint_identity_exception_queue_v1"] = (
        "wang_viewpoint_identity_exception_queue_v1"
    )
    exception_queue_id: str
    bundles: list[ViewpointExceptionBundle] = Field(min_length=1)
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_queue(self) -> "ViewpointExceptionQueueArtifact":
        keys = [(-item.priority, item.exception_bundle_id) for item in self.bundles]
        if keys != sorted(set(keys)):
            raise ValueError("exception queue must be priority-sorted and unique")
        payload = self.model_dump(mode="json")
        stated = payload.pop("artifact_sha256")
        if stated != sha256_json(payload):
            raise ValueError("exception queue artifact SHA mismatch")
        identity_payload = dict(payload)
        identity_payload.pop("exception_queue_id")
        if self.exception_queue_id != f"VEQ-{sha256_json(identity_payload)[:20]}":
            raise ValueError("unstable exception queue id")
        return self


class ResolutionCallLedgerEntry(StrictArtifact):
    stage: Literal["proposal", "blind_review", "delta_adjudication"]
    semantic_call_ordinal: Literal[1, 2, 3]
    generation_fingerprint_sha256: str
    artifact_sha256: str
    resumed_from_cache: bool


class ViewpointResolutionRunArtifact(StrictArtifact):
    schema_version: Literal["wang_viewpoint_identity_resolution_run_v1"] = (
        RESOLUTION_RUN_VERSION
    )
    resolution_run_id: str
    packet_sha256: str
    run_fingerprint_sha256: str
    call_ledger: list[ResolutionCallLedgerEntry]
    semantic_deltas: list[SemanticDelta]
    risk_assessment: ResolutionRiskAssessment
    disposition: Literal["system_approved", "human_exception"]
    proposed_change_package: dict[str, Any] | None = None
    exception_bundle: ViewpointExceptionBundle | None = None
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_run(self) -> "ViewpointResolutionRunArtifact":
        stages = [item.stage for item in self.call_ledger]
        expected = ["proposal", "blind_review"]
        if self.semantic_deltas:
            expected.append("delta_adjudication")
        if stages != expected:
            raise ValueError("reviewer-call invariant violated")
        ordinals = [item.semantic_call_ordinal for item in self.call_ledger]
        if ordinals != list(range(1, len(ordinals) + 1)):
            raise ValueError("semantic call ordinals are not exact")
        if self.disposition == "system_approved":
            if not self.risk_assessment.auto_approval_eligible:
                raise ValueError("system approval requires every risk gate")
            if not self.proposed_change_package or self.exception_bundle:
                raise ValueError("system approval requires only a ChangeSet proposal")
            expected_keys = {
                "schema_version",
                "package_id",
                "viewpoint_identity_decisions",
                "canonical_viewpoints",
                "viewpoint_revisions",
                "viewpoint_claim_links",
            }
            if set(self.proposed_change_package) != expected_keys:
                raise ValueError("system approval package has unexpected collections")
            decisions = [
                ViewpointIdentityDecisionRecord.model_validate(item)
                for item in self.proposed_change_package["viewpoint_identity_decisions"]
            ]
            if len(decisions) != 1 or decisions[0].review_status != "system_approved":
                raise ValueError("system approval package needs one system-approved decision")
            for item in self.proposed_change_package["canonical_viewpoints"]:
                CanonicalViewpointRecord.model_validate(item)
            for item in self.proposed_change_package["viewpoint_revisions"]:
                ViewpointRevisionRecord.model_validate(item)
            for item in self.proposed_change_package["viewpoint_claim_links"]:
                ViewpointClaimLinkRecord.model_validate(item)
        else:
            if self.proposed_change_package or not self.exception_bundle:
                raise ValueError("human exception requires only an exception bundle")
        payload = self.model_dump(mode="json")
        stated = payload.pop("artifact_sha256")
        if stated != sha256_json(payload):
            raise ValueError("resolution run artifact SHA mismatch")
        if self.resolution_run_id != f"VRUN-{self.run_fingerprint_sha256[:20]}":
            raise ValueError("unstable resolution run id")
        return self


class ReviewerAdapter(Protocol):
    model_id: str
    prompt_sha256: str

    def generate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


def _as_model(value: Mapping[str, Any] | Any, model: type[Any]) -> Any:
    return value if isinstance(value, model) else model.model_validate(value)


def _sorted_dict_rows(rows: Sequence[Mapping[str, Any]], id_field: str) -> list[dict[str, Any]]:
    return sorted((dict(row) for row in rows), key=lambda item: str(item[id_field]))


def _with_sha(payload: dict[str, Any], field: str = "artifact_sha256") -> dict[str, Any]:
    value = dict(payload)
    value[field] = sha256_json(payload)
    return value


def _scripture_ref(value: Any) -> str:
    return value if isinstance(value, str) else canonical_json(value)


def _strict_json_schema(value: Mapping[str, Any]) -> dict[str, Any]:
    """Make Pydantic JSON Schema acceptable to strict structured outputs."""

    schema = dict(value)
    schema.pop("default", None)
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        normalized_properties = {
            str(key): _strict_json_schema(item)
            if isinstance(item, Mapping)
            else item
            for key, item in properties.items()
        }
        schema["properties"] = normalized_properties
        schema["required"] = list(normalized_properties)
        schema["additionalProperties"] = False
    for key in ("$defs", "definitions"):
        definitions = schema.get(key)
        if isinstance(definitions, Mapping):
            schema[key] = {
                str(name): _strict_json_schema(item)
                if isinstance(item, Mapping)
                else item
                for name, item in definitions.items()
            }
    items = schema.get("items")
    if isinstance(items, Mapping):
        schema["items"] = _strict_json_schema(items)
    for key in ("anyOf", "oneOf", "allOf"):
        variants = schema.get(key)
        if isinstance(variants, list):
            schema[key] = [
                _strict_json_schema(item) if isinstance(item, Mapping) else item
                for item in variants
            ]
    return schema


def build_identity_review_packet(
    *,
    candidate: Mapping[str, Any] | ViewpointIdentityCandidateRecord,
    coverage_snapshot: Mapping[str, Any] | ViewpointCoverageSnapshotRecord,
    ledger: Mapping[str, Any] | ViewpointResolutionLedgerRecord,
    quality_report: Mapping[str, Any] | ViewpointQualityReportRecord,
    claims: Sequence[Mapping[str, Any] | ClaimRecord],
    evidence_steps: Sequence[Mapping[str, Any] | EvidenceStepRecord],
    source_fragments: Sequence[Mapping[str, Any] | SourceFragmentRecord],
    citations: Sequence[Mapping[str, Any] | Citation],
    claim_relations: Sequence[Mapping[str, Any] | ClaimRelationRecord] = (),
    constraints: Sequence[Mapping[str, Any] | ClaimRelationConstraintRecord] = (),
    existing_links: Sequence[Mapping[str, Any] | ViewpointClaimLinkRecord] = (),
    candidate_viewpoints: Sequence[
        tuple[
            Mapping[str, Any] | CanonicalViewpointRecord,
            Mapping[str, Any] | ViewpointRevisionRecord,
        ]
    ] = (),
) -> ViewpointIdentityReviewPacket:
    """Compile the exact evidence packet both semantic reviewers must see."""

    selected = _as_model(candidate, ViewpointIdentityCandidateRecord)
    coverage = _as_model(coverage_snapshot, ViewpointCoverageSnapshotRecord)
    resolution = _as_model(ledger, ViewpointResolutionLedgerRecord)
    quality = _as_model(quality_report, ViewpointQualityReportRecord)
    findings: list[str] = []
    candidate_identity = {
        "claims": selected.candidate_claim_ids,
        "viewpoints": selected.candidate_viewpoint_ids,
        "relations": selected.seed_relation_ids,
        "action": selected.proposed_action,
        "blockers": selected.blocker_codes,
        "coverage_snapshot_id": selected.coverage_snapshot_id,
        "generation_fingerprint": selected.generation_fingerprint,
    }
    if selected.identity_candidate_id != f"VIC-{sha256_json(candidate_identity)[:20]}":
        findings.append("identity candidate id is not stable")
    if selected.coverage_snapshot_id != coverage.coverage_snapshot_id:
        findings.append("candidate and coverage snapshot mismatch")
    if resolution.coverage_snapshot_id != coverage.coverage_snapshot_id:
        findings.append("ledger and coverage snapshot mismatch")
    if quality.coverage_snapshot_id != coverage.coverage_snapshot_id:
        findings.append("quality report and coverage snapshot mismatch")
    if quality.resolution_ledger_id != resolution.resolution_ledger_id:
        findings.append("quality report and ledger mismatch")

    claim_index = {
        item.claim_id: item for item in (_as_model(row, ClaimRecord) for row in claims)
    }
    evidence_index = {
        item.evidence_step_id: item
        for item in (_as_model(row, EvidenceStepRecord) for row in evidence_steps)
    }
    fragment_index = {
        item.fragment_id: item
        for item in (_as_model(row, SourceFragmentRecord) for row in source_fragments)
    }
    citation_index = {
        item.citation_id: item for item in (_as_model(row, Citation) for row in citations)
    }
    coverage_sources = {item.source_id: item for item in coverage.sources}
    ledger_rows = {item.claim_id: item for item in resolution.rows}
    active_owners: dict[str, set[str]] = {}
    for raw in existing_links:
        link = _as_model(raw, ViewpointClaimLinkRecord)
        if (
            link.effective_state == "active"
            and link.link_type == "equivalent_full"
            and link.review_status in APPROVED_STATUSES
        ):
            active_owners.setdefault(link.claim_id, set()).add(link.viewpoint_id)
    conflicting_owners = sorted(
        claim_id for claim_id, owners in active_owners.items() if len(owners) > 1
    )
    if conflicting_owners:
        findings.extend(
            f"{claim_id}: multiple active viewpoint memberships"
            for claim_id in conflicting_owners
        )
    review_claims: list[ReviewClaim] = []
    blockers: list[DeterministicBlocker] = []

    for claim_id in selected.candidate_claim_ids:
        claim = claim_index.get(claim_id)
        row = ledger_rows.get(claim_id)
        if not claim:
            findings.append(f"{claim_id}: missing Claim")
            continue
        if not row:
            findings.append(f"{claim_id}: missing resolution ledger row")
            continue
        if (
            row.pinned_claim_revision != claim.revision
            or row.claim_revision_sha256 != semantic_record_sha(claim)
        ):
            findings.append(f"{claim_id}: stale Claim revision or SHA")
            continue
        evidence_rows: list[ReviewEvidence] = []
        source_ids: set[str] = set()
        for evidence_id in claim.evidence_step_ids:
            evidence = evidence_index.get(evidence_id)
            if not evidence or not evidence_fragment_ids(evidence):
                findings.append(f"{claim_id}: missing evidence {evidence_id}")
                continue
            for fragment_id in evidence_fragment_ids(evidence):
                fragment = fragment_index.get(fragment_id)
                if not fragment:
                    findings.append(
                        f"{claim_id}: evidence {evidence_id} has no source fragment {fragment_id}"
                    )
                    continue
                source_ids.add(fragment.source_id)
                coverage_source = coverage_sources.get(fragment.source_id)
                citation_id = str(fragment.citation_id or "")
                citation = citation_index.get(citation_id)
                valid = bool(
                    coverage_source
                    and coverage_source.source_sha256 == fragment.source_sha256
                    and evidence.support_eligibility in VALID_EVIDENCE_STATES
                    and fragment.anchor_state in VALID_ANCHOR_STATES
                    and citation_id
                    and citation_id in evidence.citation_ids
                    and citation
                    and citation.status == "approved"
                    and citation.source_id == fragment.source_id
                    and citation.source_sha256 == fragment.source_sha256
                    and evidence.evidence_step_id in citation.evidence_ids
                )
                evidence_rows.append(ReviewEvidence(
                    evidence_step_id=evidence.evidence_step_id,
                    source_fragment_id=fragment.fragment_id,
                    source_id=fragment.source_id,
                    paragraph_key=fragment.paragraph_key,
                    media_time=fragment.media_time,
                    evidence_statement=evidence.statement,
                    verbatim_excerpt=fragment.verbatim_excerpt,
                    citation_id=citation_id,
                    citation_revision=citation.revision if citation else 1,
                    citation_status=citation.status if citation else "unresolved",
                    source_sha256=str(fragment.source_sha256 or ""),
                    support_eligibility=evidence.support_eligibility,
                    anchor_state=fragment.anchor_state,
                    valid_for_identity_review=valid,
                ))
        if len(source_ids) != 1:
            findings.append(f"{claim_id}: Claim evidence is not source-local")
            continue
        evidence_rows.sort(key=lambda item: item.evidence_step_id)
        source_id = next(iter(source_ids))
        review_claims.append(
            ReviewClaim(
                claim_id=claim.claim_id,
                pinned_claim_revision=claim.revision,
                claim_revision_sha256=semantic_record_sha(claim),
                source_id=source_id,
                statement=claim.statement,
                attribution=claim.attribution,
                scripture_refs=sorted(
                    {_scripture_ref(value) for value in claim.scripture_refs}
                ),
                review_status=claim.review_status,
                active_full_viewpoint_id=next(
                    iter(active_owners.get(claim.claim_id, set())), None
                ),
                evidence=evidence_rows,
            )
        )
        if claim.review_status not in APPROVED_STATUSES:
            blockers.append(
                DeterministicBlocker(
                    code="source_maturity",
                    record_ids=[claim_id],
                    detail="Claim is below the approval policy.",
                )
            )
        if claim.attribution and claim.attribution != "professor":
            blockers.append(
                DeterministicBlocker(
                    code="external_attribution",
                    record_ids=[claim_id],
                    detail="Claim attribution is not the professor.",
                )
            )
        if not evidence_rows or not all(item.valid_for_identity_review for item in evidence_rows):
            blockers.append(
                DeterministicBlocker(
                    code="evidence_invalid",
                    record_ids=[claim_id],
                    detail="Claim has evidence that is not citation/source-version bound.",
                )
            )
        candidate_resolution_valid = bool(
            row.processing_status == "resolved"
            and row.resolution_kind == "new_viewpoint_candidate"
            and row.new_viewpoint_candidate_id == selected.identity_candidate_id
        )
        if not candidate_resolution_valid:
            blockers.append(
                DeterministicBlocker(
                    code="ledger_unresolved",
                    record_ids=[claim_id],
                    detail=(
                        "Claim is not resolved to this identity candidate in the "
                        "bound ledger."
                    ),
                )
            )
    if findings:
        raise ViewpointResolutionError(findings)

    for code in selected.blocker_codes:
        blockers.append(
            DeterministicBlocker(
                code="candidate_declared_blocker",
                record_ids=[selected.identity_candidate_id],
                detail=str(code),
            )
        )
    if quality.eligibility_decision != "pass" or quality.hard_failures:
        blockers.append(
            DeterministicBlocker(
                code="quality_gate_failed",
                record_ids=[quality.quality_report_id],
                detail="The bound per-dimension quality report does not pass.",
            )
        )

    selected_claims = set(selected.candidate_claim_ids)
    reviewed_relation_rows: list[dict[str, Any]] = []
    for raw in claim_relations:
        relation = _as_model(raw, ClaimRelationRecord)
        if {relation.from_id, relation.to_id} - selected_claims:
            continue
        if relation.review_status not in REVIEWED_DUPLICATE_STATUSES:
            continue
        row = {
            "claim_relation_id": relation.claim_relation_id,
            "from_id": relation.from_id,
            "to_id": relation.to_id,
            "relation_type": relation.relation_type,
            "review_status": relation.review_status,
            "reason": relation.reason,
        }
        reviewed_relation_rows.append(row)
        if relation.relation_type in MATERIAL_RELATION_TYPES:
            blockers.append(
                DeterministicBlocker(
                    code="material_relation",
                    record_ids=[relation.claim_relation_id],
                    detail=f"Reviewed relation {relation.relation_type} blocks auto merge.",
                )
            )

    reviewed_relation_ids = {
        str(item["claim_relation_id"]) for item in reviewed_relation_rows
    }
    missing_seed_relations = sorted(
        set(selected.seed_relation_ids) - reviewed_relation_ids
    )
    if missing_seed_relations:
        raise ViewpointResolutionError(
            [
                "candidate seed relations are missing or unreviewed: "
                + ", ".join(missing_seed_relations)
            ]
        )

    approved_constraint_rows: list[dict[str, Any]] = []
    for raw in constraints:
        constraint = _as_model(raw, ClaimRelationConstraintRecord)
        if {constraint.source_id, constraint.target_id} - selected_claims:
            continue
        if constraint.review_status not in APPROVED_CONSTRAINT_STATUSES:
            continue
        row = {
            "constraint_id": constraint.constraint_id,
            "source_id": constraint.source_id,
            "target_id": constraint.target_id,
            "forbidden_relation_types": sorted(
                set(constraint.forbidden_relation_types)
            ),
            "review_status": constraint.review_status,
            "reason": constraint.reason,
        }
        approved_constraint_rows.append(row)
        if "duplicate" in constraint.forbidden_relation_types:
            blockers.append(
                DeterministicBlocker(
                    code="approved_negative_constraint",
                    record_ids=[constraint.constraint_id],
                    detail="An approved constraint forbids duplicate identity.",
                )
            )

    review_viewpoints: list[ReviewViewpoint] = []
    for raw_viewpoint, raw_revision in candidate_viewpoints:
        viewpoint = _as_model(raw_viewpoint, CanonicalViewpointRecord)
        revision = _as_model(raw_revision, ViewpointRevisionRecord)
        if viewpoint.viewpoint_id not in selected.candidate_viewpoint_ids:
            continue
        if (
            viewpoint.current_revision_id != revision.viewpoint_revision_id
            or revision.viewpoint_id != viewpoint.viewpoint_id
        ):
            raise ViewpointResolutionError(
                [f"{viewpoint.viewpoint_id}: stale candidate viewpoint revision"]
            )
        review_viewpoints.append(
            ReviewViewpoint(
                viewpoint_id=viewpoint.viewpoint_id,
                viewpoint_revision_id=revision.viewpoint_revision_id,
                core_proposition=revision.core_proposition,
                proposition_signature=revision.proposition_signature,
                scope=revision.scope,
                review_status=revision.review_status,
            )
        )
    if sorted(item.viewpoint_id for item in review_viewpoints) != selected.candidate_viewpoint_ids:
        raise ViewpointResolutionError(["candidate viewpoint context is incomplete"])

    blocker_rows = sorted(
        {canonical_json(item.model_dump(mode="json")): item for item in blockers}.values(),
        key=lambda item: canonical_json(item.model_dump(mode="json")),
    )
    packet_payload: dict[str, Any] = {
        "schema_version": REVIEW_PACKET_VERSION,
        "engine_version": RESOLUTION_ENGINE_VERSION,
        "policy_version": RESOLUTION_POLICY_VERSION,
        "candidate": selected.model_dump(mode="json"),
        "coverage_snapshot_id": coverage.coverage_snapshot_id,
        "coverage_sources_sha256": coverage.sources_sha256,
        "resolution_ledger_id": resolution.resolution_ledger_id,
        "resolution_ledger_artifact_sha256": resolution.artifact_sha256,
        "quality_report_id": quality.quality_report_id,
        "quality_report_artifact_sha256": quality.artifact_sha256,
        "claims": [
            item.model_dump(mode="json")
            for item in sorted(review_claims, key=lambda item: item.claim_id)
        ],
        "candidate_viewpoints": [
            item.model_dump(mode="json")
            for item in sorted(review_viewpoints, key=lambda item: item.viewpoint_id)
        ],
        "reviewed_relations": _sorted_dict_rows(
            reviewed_relation_rows, "claim_relation_id"
        ),
        "approved_constraints": _sorted_dict_rows(
            approved_constraint_rows, "constraint_id"
        ),
        "deterministic_blockers": [item.model_dump(mode="json") for item in blocker_rows],
    }
    packet_payload["packet_sha256"] = sha256_json(packet_payload)
    return ViewpointIdentityReviewPacket.model_validate(packet_payload)


def _semantic_payload(assessment: SemanticAssessment) -> dict[str, Any]:
    payload = assessment.model_dump(mode="json")
    payload.pop("rationale")
    return payload


def compare_semantic_assessments(
    proposal: SemanticAssessment, blind_review: SemanticAssessment
) -> list[SemanticDelta]:
    """Return leaf-level semantic differences; rationale is deliberately ignored."""

    left = _semantic_payload(proposal)
    right = _semantic_payload(blind_review)
    deltas: list[SemanticDelta] = []

    def walk(path: str, first: Any, second: Any) -> None:
        if isinstance(first, dict) and isinstance(second, dict):
            for key in sorted(set(first) | set(second)):
                walk(f"{path}/{key}", first.get(key), second.get(key))
            return
        if first != second:
            deltas.append(
                SemanticDelta(
                    field_path=path,
                    proposal_value=first,
                    blind_review_value=second,
                )
            )

    walk("", left, right)
    return sorted(deltas, key=lambda item: item.field_path)


def assess_resolution_risk(
    packet: ViewpointIdentityReviewPacket,
    proposal: SemanticAssessment,
    blind_review: SemanticAssessment,
    deltas: Sequence[SemanticDelta],
) -> ResolutionRiskAssessment:
    claims = packet.claims
    assessments = proposal.members
    checks: list[RiskGateCheck] = []

    def add(gate: str, passed: bool, detail: str) -> None:
        checks.append(RiskGateCheck(gate=gate, passed=passed, detail=detail))

    add(
        "candidate_action",
        packet.candidate.proposed_action in {"create_new", "match_existing"},
        packet.candidate.proposed_action,
    )
    assessment_action_eligible = (
        proposal.proposed_action in {"create_new", "match_existing"}
        and proposal.proposed_action == packet.candidate.proposed_action
    )
    add(
        "assessment_action_eligible",
        assessment_action_eligible,
        f"candidate={packet.candidate.proposed_action};assessment={proposal.proposed_action}",
    )
    add("independent_semantic_agreement", not deltas, f"delta_count={len(deltas)}")
    distinct_source_count = len({item.source_id for item in claims})
    add(
        "two_independent_sources",
        distinct_source_count >= 2,
        f"source_count={distinct_source_count}",
    )
    add(
        "full_proposition_members_only",
        all(item.member_role == "equivalent_full" for item in assessments),
        "equivalent_component and related-only are exception work",
    )
    verdicts = [
        value
        for member in assessments
        for value in member.truth_conditions.model_dump().values()
    ]
    add(
        "truth_conditions_compatible",
        bool(verdicts) and all(value == "compatible" for value in verdicts),
        f"all {len(TRUTH_CONDITION_FIELDS)} truth-condition fields must be compatible",
    )
    add(
        "canonical_wording_conservative",
        proposal.canonical_wording_conservative
        and not proposal.added_truth_conditions,
        "canonical wording may not add truth conditions",
    )
    add(
        "no_semantic_blockers",
        not proposal.semantic_blockers,
        f"semantic_blockers={proposal.semantic_blockers}",
    )
    add(
        "no_deterministic_blockers",
        not packet.deterministic_blockers,
        f"blocker_count={len(packet.deterministic_blockers)}",
    )
    add(
        "quality_report_passed",
        not any(
            item.code == "quality_gate_failed"
            for item in packet.deterministic_blockers
        ),
        packet.quality_report_id,
    )
    add(
        "ledger_scope_resolved",
        not any(
            item.code == "ledger_unresolved"
            for item in packet.deterministic_blockers
        ),
        packet.resolution_ledger_id,
    )
    invalid_source_codes = {
        "source_maturity",
        "evidence_invalid",
        "external_attribution",
        "stale_dependency",
    }
    add(
        "source_dependencies_valid",
        not any(
            item.code in invalid_source_codes
            for item in packet.deterministic_blockers
        ),
        "Claim maturity, attribution, evidence, and SHAs",
    )
    target_valid = True
    target_semantics_unchanged = True
    adds_new_membership = True
    ownership_compatible = all(
        item.active_full_viewpoint_id is None for item in claims
    )
    if proposal.proposed_action == "match_existing":
        context = next(
            (
                item
                for item in packet.candidate_viewpoints
                if item.viewpoint_id == proposal.target_viewpoint_id
            ),
            None,
        )
        target_valid = context is not None
        ownership_compatible = all(
            item.active_full_viewpoint_id in {None, proposal.target_viewpoint_id}
            for item in claims
        )
        adds_new_membership = any(
            item.active_full_viewpoint_id is None for item in claims
        )
        target_semantics_unchanged = bool(
            context
            and proposal.core_proposition == context.core_proposition
            and proposal.proposition_signature == context.proposition_signature
            and proposal.scope == context.scope
        )
    add("target_viewpoint_resolves", target_valid, str(proposal.target_viewpoint_id or "new"))
    add(
        "membership_ownership_compatible",
        ownership_compatible,
        "existing full memberships must agree with the proposed action and target",
    )
    add(
        "adds_new_membership",
        adds_new_membership,
        "match_existing must add at least one new Claim membership",
    )
    add(
        "target_semantics_unchanged",
        target_semantics_unchanged,
        "match_existing cannot rewrite the current semantic revision",
    )
    action_agrees = proposal.proposed_action == blind_review.proposed_action
    add(
        "review_action_matches",
        action_agrees,
        f"proposal={proposal.proposed_action};blind={blind_review.proposed_action}",
    )

    checks.sort(key=lambda item: item.gate)
    blocker_codes = sorted(
        {
            item.code for item in packet.deterministic_blockers
        }
        | set(proposal.semantic_blockers)
        | ({"semantic_reviewer_disagreement"} if deltas else set())
    )
    eligible = all(item.passed for item in checks) and not blocker_codes
    return ResolutionRiskAssessment(
        auto_approval_eligible=eligible,
        risk_level="low" if eligible else "high",
        checks=checks,
        blocker_codes=blocker_codes,
    )


def _generation_fingerprint(
    *, stage: str, packet_sha256: str, adapter: ReviewerAdapter, extra_sha: str | None = None
) -> str:
    return sha256_json(
        {
            "engine_version": RESOLUTION_ENGINE_VERSION,
            "stage": stage,
            "packet_sha256": packet_sha256,
            "model_id": adapter.model_id,
            "prompt_sha256": adapter.prompt_sha256,
            "extra_sha256": extra_sha,
        }
    )


def _artifact_path(output_dir: Path, stage: str, fingerprint: str) -> Path:
    return output_dir / f"{stage}.{fingerprint[:20]}.json"


def _failure_path(output_dir: Path, stage: str, fingerprint: str) -> Path:
    return output_dir / f"{stage}.{fingerprint[:20]}.failure.json"


def _read_valid(path: Path, model: type[Any]) -> Any | None:
    if not path.is_file():
        return None
    return model.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _write_new(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ViewpointResolutionError([f"refusing to overwrite immutable artifact {path}"])
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(value.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _review_call(
    *,
    stage: ReviewStage,
    ordinal: Literal[1, 2],
    packet: ViewpointIdentityReviewPacket,
    adapter: ReviewerAdapter,
    output_dir: Path,
) -> tuple[ReviewCallArtifact, bool]:
    fingerprint = _generation_fingerprint(
        stage=stage, packet_sha256=packet.packet_sha256, adapter=adapter
    )
    path = _artifact_path(output_dir, stage, fingerprint)
    failure_path = _failure_path(output_dir, stage, fingerprint)
    cached = _read_valid(path, ReviewCallArtifact)
    if cached:
        if (
            cached.generation_fingerprint_sha256 != fingerprint
            or cached.stage != stage
            or cached.semantic_call_ordinal != ordinal
            or cached.packet_sha256 != packet.packet_sha256
            or cached.model_id != adapter.model_id
            or cached.prompt_sha256 != adapter.prompt_sha256
        ):
            raise ViewpointResolutionError([f"{stage}: cached artifact binding mismatch"])
        return cached, True
    cached_failure = _read_valid(failure_path, SemanticCallFailureArtifact)
    if cached_failure:
        raise ViewpointResolutionError(
            [f"{stage}: semantic call already failed and cannot be retried"]
        )
    raw = adapter.generate(packet.model_dump(mode="json"))
    try:
        assessment = SemanticAssessment.model_validate(raw)
        if assessment.candidate_id != packet.candidate.identity_candidate_id:
            raise ViewpointResolutionError([f"{stage}: candidate id mismatch"])
        if assessment.packet_sha256 != packet.packet_sha256:
            raise ViewpointResolutionError([f"{stage}: packet SHA mismatch"])
        if (
            [item.claim_id for item in assessment.members]
            != packet.candidate.candidate_claim_ids
        ):
            raise ViewpointResolutionError(
                [f"{stage}: assessment does not cover candidate Claims"]
            )
    except Exception as exc:
        failure_payload = {
            "schema_version": "wang_viewpoint_semantic_call_failure_v1",
            "stage": stage,
            "semantic_call_ordinal": ordinal,
            "packet_sha256": packet.packet_sha256,
            "model_id": adapter.model_id,
            "prompt_sha256": adapter.prompt_sha256,
            "generation_fingerprint_sha256": fingerprint,
            "error": str(exc),
            "raw_response": raw,
        }
        failure = SemanticCallFailureArtifact.model_validate(
            _with_sha(failure_payload)
        )
        _write_new(failure_path, failure)
        raise
    payload = {
        "schema_version": REVIEW_CALL_VERSION,
        "stage": stage,
        "semantic_call_ordinal": ordinal,
        "packet_sha256": packet.packet_sha256,
        "model_id": adapter.model_id,
        "prompt_sha256": adapter.prompt_sha256,
        "generation_fingerprint_sha256": fingerprint,
        "assessment": assessment.model_dump(mode="json"),
    }
    artifact = ReviewCallArtifact.model_validate(_with_sha(payload))
    _write_new(path, artifact)
    return artifact, False


def _delta_call(
    *,
    packet: ViewpointIdentityReviewPacket,
    deltas: Sequence[SemanticDelta],
    adapter: ReviewerAdapter,
    output_dir: Path,
) -> tuple[DeltaAdjudicationArtifact, bool]:
    delta_payload = [item.model_dump(mode="json") for item in deltas]
    delta_sha = sha256_json(delta_payload)
    fingerprint = _generation_fingerprint(
        stage="delta_adjudication",
        packet_sha256=packet.packet_sha256,
        adapter=adapter,
        extra_sha=delta_sha,
    )
    path = _artifact_path(output_dir, "delta-adjudication", fingerprint)
    failure_path = _failure_path(output_dir, "delta-adjudication", fingerprint)
    cached = _read_valid(path, DeltaAdjudicationArtifact)
    if cached:
        if (
            cached.generation_fingerprint_sha256 != fingerprint
            or cached.packet_sha256 != packet.packet_sha256
            or cached.delta_sha256 != delta_sha
            or cached.model_id != adapter.model_id
            or cached.prompt_sha256 != adapter.prompt_sha256
        ):
            raise ViewpointResolutionError(
                ["delta adjudication: cached artifact binding mismatch"]
            )
        return cached, True
    cached_failure = _read_valid(failure_path, SemanticCallFailureArtifact)
    if cached_failure:
        raise ViewpointResolutionError(
            ["delta adjudication: semantic call already failed and cannot be retried"]
        )
    raw = dict(
        adapter.generate(
            {
                "packet": packet.model_dump(mode="json"),
                "semantic_deltas": delta_payload,
                "instruction": "Resolve only the listed fields and return remaining findings now.",
            }
        )
    )
    try:
        response = DeltaAdjudicationResponse.model_validate(raw)
        resolutions = response.resolutions
        expected_paths = [item.field_path for item in deltas]
        if [item.field_path for item in resolutions] != expected_paths:
            raise ViewpointResolutionError(
                ["delta adjudication must cover every differing field exactly once"]
            )
    except Exception as exc:
        failure_payload = {
            "schema_version": "wang_viewpoint_semantic_call_failure_v1",
            "stage": "delta_adjudication",
            "semantic_call_ordinal": 3,
            "packet_sha256": packet.packet_sha256,
            "model_id": adapter.model_id,
            "prompt_sha256": adapter.prompt_sha256,
            "generation_fingerprint_sha256": fingerprint,
            "error": str(exc),
            "raw_response": raw,
        }
        failure = SemanticCallFailureArtifact.model_validate(
            _with_sha(failure_payload)
        )
        _write_new(failure_path, failure)
        raise
    payload = {
        "schema_version": DELTA_ADJUDICATION_VERSION,
        "semantic_call_ordinal": 3,
        "packet_sha256": packet.packet_sha256,
        "delta_sha256": delta_sha,
        "model_id": adapter.model_id,
        "prompt_sha256": adapter.prompt_sha256,
        "generation_fingerprint_sha256": fingerprint,
        "resolutions": [item.model_dump(mode="json") for item in resolutions],
        "remaining_findings": sorted(set(response.remaining_findings)),
    }
    artifact = DeltaAdjudicationArtifact.model_validate(_with_sha(payload))
    _write_new(path, artifact)
    return artifact, False


def compile_system_approval_package(
    *,
    packet: ViewpointIdentityReviewPacket,
    assessment: SemanticAssessment,
    risk: ResolutionRiskAssessment,
    proposal_call: ReviewCallArtifact,
    blind_call: ReviewCallArtifact,
    decided_at: str,
) -> dict[str, Any]:
    """Assign ids and compile records only after every automation gate passes."""

    if not risk.auto_approval_eligible:
        raise ViewpointResolutionError(["system approval package requested for blocked candidate"])
    creation_seed = {
        "engine_version": RESOLUTION_ENGINE_VERSION,
        "candidate_id": packet.candidate.identity_candidate_id,
        "packet_sha256": packet.packet_sha256,
        "core_proposition": assessment.core_proposition,
        "proposition_signature": assessment.proposition_signature.model_dump(mode="json"),
        "scope": assessment.scope.model_dump(mode="json"),
    }
    if assessment.proposed_action == "match_existing":
        viewpoint_id = str(assessment.target_viewpoint_id)
        context = next(
            item for item in packet.candidate_viewpoints if item.viewpoint_id == viewpoint_id
        )
        revision_id = context.viewpoint_revision_id
        viewpoint_records: list[dict[str, Any]] = []
        revision_records: list[dict[str, Any]] = []
    else:
        viewpoint_id = f"CV-{sha256_json(creation_seed)[:20]}"
        revision_seed = {
            "viewpoint_id": viewpoint_id,
            "core_proposition": assessment.core_proposition,
            "proposition_signature": assessment.proposition_signature.model_dump(mode="json"),
            "scope": assessment.scope.model_dump(mode="json"),
        }
        revision_id = f"CVR-{sha256_json(revision_seed)[:20]}"
        viewpoint_records = []
        revision_records = []

    decision_seed = {
        "candidate_id": packet.candidate.identity_candidate_id,
        "packet_sha256": packet.packet_sha256,
        "viewpoint_id": viewpoint_id,
        "revision_id": revision_id,
        "decided_at": decided_at,
    }
    decision_id = f"VID-{sha256_json(decision_seed)[:20]}"
    review_provenance = {
        "packet_sha256": packet.packet_sha256,
        "policy_version": RESOLUTION_POLICY_VERSION,
        "proposal_artifact_sha256": proposal_call.artifact_sha256,
        "blind_review_artifact_sha256": blind_call.artifact_sha256,
        "risk_assessment": risk.model_dump(mode="json"),
    }
    review_artifact_sha256 = sha256_json(review_provenance)
    decision = ViewpointIdentityDecisionRecord(
        identity_decision_id=decision_id,
        identity_candidate_id=packet.candidate.identity_candidate_id,
        decision=assessment.proposed_action,
        resolved_viewpoint_id=viewpoint_id,
        claim_link_decisions=[
            {"claim_id": item.claim_id, "link_type": "equivalent_full"}
            for item in assessment.members
        ],
        reviewer_kind="system",
        reviewer_id=f"{RESOLUTION_ENGINE_VERSION}:{RESOLUTION_POLICY_VERSION}",
        approval_basis="dual_model_consensus",
        reason="Two blind semantic assessments agreed and every deterministic gate passed.",
        input_sha256=packet.candidate.generation_fingerprint,
        review_artifact_sha256=review_artifact_sha256,
        policy_version=RESOLUTION_POLICY_VERSION,
        reviewer_model_ids=sorted(
            {proposal_call.model_id, blind_call.model_id}
        ),
        semantic_call_artifact_sha256s=sorted(
            {proposal_call.artifact_sha256, blind_call.artifact_sha256}
        ),
        created_at=decided_at,
        review_status="system_approved",
    )
    if assessment.proposed_action == "create_new":
        revision = ViewpointRevisionRecord(
            viewpoint_revision_id=revision_id,
            viewpoint_id=viewpoint_id,
            revision_number=1,
            core_proposition=str(assessment.core_proposition),
            proposition_signature=assessment.proposition_signature,
            scope=assessment.scope,
            provenance={
                "basis_identity_decision_ids": [decision_id],
                "review_artifact_sha256": review_artifact_sha256,
            },
            approved_by=f"{RESOLUTION_ENGINE_VERSION}:{RESOLUTION_POLICY_VERSION}",
            approved_at=decided_at,
            review_status="system_approved",
        )
        viewpoint = CanonicalViewpointRecord(
            viewpoint_id=viewpoint_id,
            current_revision_id=revision_id,
            created_from_candidate_id=packet.candidate.identity_candidate_id,
            review_status="system_approved",
        )
        viewpoint_records = [viewpoint.model_dump(mode="json")]
        revision_records = [revision.model_dump(mode="json")]

    claim_index = {item.claim_id: item for item in packet.claims}
    links: list[dict[str, Any]] = []
    for member in assessment.members:
        claim = claim_index[member.claim_id]
        if claim.active_full_viewpoint_id == viewpoint_id:
            continue
        supporting_relation_ids = sorted(
            relation.claim_relation_id
            for relation in packet.reviewed_relations
            if claim.claim_id in {relation.from_id, relation.to_id}
        )
        occurrence_seed = {
            "claim_id": claim.claim_id,
            "pinned_claim_revision": claim.pinned_claim_revision,
            "source_id": claim.source_id,
            "anchors": [
                {
                    "source_fragment_id": item.source_fragment_id,
                    "paragraph_key": item.paragraph_key,
                    "evidence_step_id": item.evidence_step_id,
                    "citation_id": item.citation_id,
                    "media_time": item.media_time,
                    "source_sha256": item.source_sha256,
                }
                for item in claim.evidence
            ],
        }
        occurrence_ref = f"OCC-{sha256_json(occurrence_seed)[:20]}"
        link_seed = {
            "viewpoint_id": viewpoint_id,
            "viewpoint_revision_id": revision_id,
            "claim_id": claim.claim_id,
            "claim_revision": claim.pinned_claim_revision,
            "decision_id": decision_id,
        }
        link = ViewpointClaimLinkRecord(
            viewpoint_claim_link_id=f"VCL-{sha256_json(link_seed)[:20]}",
            viewpoint_id=viewpoint_id,
            validated_against_viewpoint_revision_id=revision_id,
            claim_id=claim.claim_id,
            pinned_claim_revision=claim.pinned_claim_revision,
            link_type="equivalent_full",
            supporting_relation_ids=supporting_relation_ids,
            occurrence_refs=[occurrence_ref],
            decision_id=decision_id,
            effective_state="active",
            review_status="system_approved",
        )
        links.append(link.model_dump(mode="json"))
    return {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": f"VIEWPOINT-RESOLUTION-{decision_id}",
        "viewpoint_identity_decisions": [decision.model_dump(mode="json")],
        "canonical_viewpoints": viewpoint_records,
        "viewpoint_revisions": revision_records,
        "viewpoint_claim_links": links,
    }


def _exception_bundle(
    *,
    packet: ViewpointIdentityReviewPacket,
    risk: ResolutionRiskAssessment,
    deltas: Sequence[SemanticDelta],
    adjudication: DeltaAdjudicationArtifact | None,
    proposal_call: ReviewCallArtifact,
    blind_call: ReviewCallArtifact,
    consumer_impact: Literal["none", "planning", "publication", "withdrawal"],
) -> ViewpointExceptionBundle:
    impact_weight = {"none": 0, "planning": 20, "publication": 60, "withdrawal": 100}
    blocker_codes = sorted(set(risk.blocker_codes))
    remaining = sorted(set((adjudication.remaining_findings if adjudication else [])))
    priority = impact_weight[consumer_impact] + min(40, 5 * len(blocker_codes)) + min(
        20, len(deltas)
    )
    payload: dict[str, Any] = {
        "schema_version": "wang_viewpoint_identity_exception_bundle_v1",
        "exception_bundle_id": "pending",
        "candidate_id": packet.candidate.identity_candidate_id,
        "packet_sha256": packet.packet_sha256,
        "priority": priority,
        "consumer_impact": consumer_impact,
        "blocker_codes": blocker_codes,
        "deterministic_blockers": [
            item.model_dump(mode="json") for item in packet.deterministic_blockers
        ],
        "semantic_deltas": [item.model_dump(mode="json") for item in deltas],
        "remaining_findings": remaining,
        "claims": [item.model_dump(mode="json") for item in packet.claims],
        "proposal": proposal_call.assessment.model_dump(mode="json"),
        "blind_review": blind_call.assessment.model_dump(mode="json"),
        "proposal_artifact_sha256": proposal_call.artifact_sha256,
        "blind_review_artifact_sha256": blind_call.artifact_sha256,
        "delta_adjudication_artifact_sha256": (
            adjudication.artifact_sha256 if adjudication else None
        ),
        "requested_editor_decision": "identity_bundle",
    }
    identity_payload = dict(payload)
    identity_payload.pop("exception_bundle_id")
    payload["exception_bundle_id"] = f"VEX-{sha256_json(identity_payload)[:20]}"
    payload["artifact_sha256"] = sha256_json(payload)
    return ViewpointExceptionBundle.model_validate(payload)


def build_exception_queue(
    bundles: Sequence[Mapping[str, Any] | ViewpointExceptionBundle],
) -> ViewpointExceptionQueueArtifact:
    """Rank identity-level exceptions without creating pair-by-pair tasks."""

    normalized = [
        _as_model(item, ViewpointExceptionBundle) for item in bundles
    ]
    normalized.sort(key=lambda item: (-item.priority, item.exception_bundle_id))
    payload: dict[str, Any] = {
        "schema_version": "wang_viewpoint_identity_exception_queue_v1",
        "exception_queue_id": "pending",
        "bundles": [item.model_dump(mode="json") for item in normalized],
    }
    identity_payload = dict(payload)
    identity_payload.pop("exception_queue_id")
    payload["exception_queue_id"] = f"VEQ-{sha256_json(identity_payload)[:20]}"
    payload["artifact_sha256"] = sha256_json(payload)
    return ViewpointExceptionQueueArtifact.model_validate(payload)


def run_viewpoint_resolution(
    *,
    packet: Mapping[str, Any] | ViewpointIdentityReviewPacket,
    proposal_reviewer: ReviewerAdapter,
    blind_reviewer: ReviewerAdapter,
    delta_adjudicator: ReviewerAdapter,
    output_dir: Path,
    decided_at: str,
    consumer_impact: Literal["none", "planning", "publication", "withdrawal"] = "none",
) -> ViewpointResolutionRunArtifact:
    """Run the fixed semantic-call workflow with immutable stage-level resume."""

    review_packet = _as_model(packet, ViewpointIdentityReviewPacket)
    if proposal_reviewer.model_id == blind_reviewer.model_id:
        raise ViewpointResolutionError(
            ["proposal and blind review require independent model identities"]
        )
    if proposal_reviewer.prompt_sha256 == blind_reviewer.prompt_sha256:
        raise ViewpointResolutionError(
            ["proposal and blind review require distinct prompt identities"]
        )
    run_identity = {
        "engine_version": RESOLUTION_ENGINE_VERSION,
        "policy_version": RESOLUTION_POLICY_VERSION,
        "packet_sha256": review_packet.packet_sha256,
        "proposal_model": proposal_reviewer.model_id,
        "proposal_prompt_sha256": proposal_reviewer.prompt_sha256,
        "blind_model": blind_reviewer.model_id,
        "blind_prompt_sha256": blind_reviewer.prompt_sha256,
        "delta_model": delta_adjudicator.model_id,
        "delta_prompt_sha256": delta_adjudicator.prompt_sha256,
        "decided_at": decided_at,
        "consumer_impact": consumer_impact,
    }
    run_fingerprint = sha256_json(run_identity)
    run_path = output_dir / f"run.{run_fingerprint[:20]}.json"
    cached_run = _read_valid(run_path, ViewpointResolutionRunArtifact)
    if cached_run:
        if (
            cached_run.run_fingerprint_sha256 != run_fingerprint
            or cached_run.packet_sha256 != review_packet.packet_sha256
        ):
            raise ViewpointResolutionError(["cached resolution run binding mismatch"])
        return cached_run

    proposal_call, proposal_cached = _review_call(
        stage="proposal",
        ordinal=1,
        packet=review_packet,
        adapter=proposal_reviewer,
        output_dir=output_dir,
    )
    blind_call, blind_cached = _review_call(
        stage="blind_review",
        ordinal=2,
        packet=review_packet,
        adapter=blind_reviewer,
        output_dir=output_dir,
    )
    deltas = compare_semantic_assessments(
        proposal_call.assessment, blind_call.assessment
    )
    call_ledger = [
        ResolutionCallLedgerEntry(
            stage="proposal",
            semantic_call_ordinal=1,
            generation_fingerprint_sha256=proposal_call.generation_fingerprint_sha256,
            artifact_sha256=proposal_call.artifact_sha256,
            resumed_from_cache=proposal_cached,
        ),
        ResolutionCallLedgerEntry(
            stage="blind_review",
            semantic_call_ordinal=2,
            generation_fingerprint_sha256=blind_call.generation_fingerprint_sha256,
            artifact_sha256=blind_call.artifact_sha256,
            resumed_from_cache=blind_cached,
        ),
    ]
    adjudication: DeltaAdjudicationArtifact | None = None
    if deltas:
        adjudication, adjudication_cached = _delta_call(
            packet=review_packet,
            deltas=deltas,
            adapter=delta_adjudicator,
            output_dir=output_dir,
        )
        call_ledger.append(
            ResolutionCallLedgerEntry(
                stage="delta_adjudication",
                semantic_call_ordinal=3,
                generation_fingerprint_sha256=adjudication.generation_fingerprint_sha256,
                artifact_sha256=adjudication.artifact_sha256,
                resumed_from_cache=adjudication_cached,
            )
        )

    risk = assess_resolution_risk(
        review_packet, proposal_call.assessment, blind_call.assessment, deltas
    )
    if risk.auto_approval_eligible:
        disposition = "system_approved"
        package = compile_system_approval_package(
            packet=review_packet,
            assessment=proposal_call.assessment,
            risk=risk,
            proposal_call=proposal_call,
            blind_call=blind_call,
            decided_at=decided_at,
        )
        exception = None
    else:
        disposition = "human_exception"
        package = None
        exception = _exception_bundle(
            packet=review_packet,
            risk=risk,
            deltas=deltas,
            adjudication=adjudication,
            proposal_call=proposal_call,
            blind_call=blind_call,
            consumer_impact=consumer_impact,
        )

    payload = {
        "schema_version": RESOLUTION_RUN_VERSION,
        "resolution_run_id": f"VRUN-{run_fingerprint[:20]}",
        "packet_sha256": review_packet.packet_sha256,
        "run_fingerprint_sha256": run_fingerprint,
        "call_ledger": [item.model_dump(mode="json") for item in call_ledger],
        "semantic_deltas": [item.model_dump(mode="json") for item in deltas],
        "risk_assessment": risk.model_dump(mode="json"),
        "disposition": disposition,
        "proposed_change_package": package,
        "exception_bundle": exception.model_dump(mode="json") if exception else None,
    }
    artifact = ViewpointResolutionRunArtifact.model_validate(_with_sha(payload))
    _write_new(run_path, artifact)
    return artifact


class CallableReviewerAdapter:
    """Small adapter for tests and future model-client wiring."""

    def __init__(
        self,
        *,
        model_id: str,
        prompt: str,
        generate: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> None:
        self.model_id = model_id
        self.prompt_sha256 = sha256_json({"prompt": prompt})
        self._generate = generate

    def generate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._generate(payload)


class StructuredJsonReviewerAdapter:
    """Bind an existing structured-output client without semantic repair calls.

    The wrapped Stage1 clients may retry transport failures internally.  This
    adapter invokes ``generate_json`` once, so an invalid semantic answer fails
    the run instead of triggering repeated model calls until agreement.
    """

    def __init__(
        self,
        *,
        client: Any,
        prompt: str,
        response_model: type[BaseModel],
        schema_name: str,
    ) -> None:
        self._client = client
        self._prompt = prompt
        self._response_model = response_model
        self._schema_name = schema_name
        self.model_id = str(getattr(client, "model", ""))
        if not self.model_id:
            raise ValueError("structured reviewer client must expose model")
        self.prompt_sha256 = sha256_json({"prompt": prompt})

    def generate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        schema = {
            "name": self._schema_name,
            "strict": True,
            "schema": _strict_json_schema(self._response_model.model_json_schema()),
        }
        return self._client.generate_json(
            system_prompt=self._prompt,
            user_prompt=json.dumps(payload, ensure_ascii=False, indent=2),
            json_schema=schema,
            temperature=0.0,
        )
