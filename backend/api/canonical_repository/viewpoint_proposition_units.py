"""Evidence-bound atomic proposition candidates for viewpoint identity.

This layer separates Claim composition from CanonicalViewpoint identity.  A
model may propose local units and character spans, but deterministic code binds
them to the pinned Claim/Evidence and assigns stable candidate ids.  Nothing in
this module creates or approves master data.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .viewpoint_foundation import sha256_json
from .viewpoint_resolution import ReviewClaim, ReviewEvidence


class StrictUnitModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaimStatementSpan(StrictUnitModel):
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    exact_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_range(self) -> "ClaimStatementSpan":
        if self.end_char <= self.start_char:
            raise ValueError("statement span range is empty or reversed")
        return self


class PropositionEvidenceReference(StrictUnitModel):
    evidence_step_id: str
    source_fragment_id: str


class ProposedAtomicUnit(StrictUnitModel):
    local_unit_id: str = Field(pattern=r"^U[0-9]{3}$")
    unit_statement: str = Field(min_length=1)
    structural_role: Literal["whole_claim", "conjunct", "qualified_clause"]
    claim_statement_spans: list[ClaimStatementSpan] = Field(min_length=1)
    evidence_references: list[PropositionEvidenceReference] = Field(min_length=1)
    wording_is_verbatim_or_conservative: Literal[True] = True
    added_truth_conditions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unit(self) -> "ProposedAtomicUnit":
        span_keys = [
            (item.start_char, item.end_char, item.exact_text)
            for item in self.claim_statement_spans
        ]
        if span_keys != sorted(set(span_keys)):
            raise ValueError("unit statement spans must be canonical and unique")
        evidence_keys = [
            (item.evidence_step_id, item.source_fragment_id)
            for item in self.evidence_references
        ]
        if evidence_keys != sorted(set(evidence_keys)):
            raise ValueError("unit evidence references must be canonical and unique")
        if self.added_truth_conditions:
            raise ValueError("atomic unit proposal may not add truth conditions")
        return self


class ClaimCoverageSegment(StrictUnitModel):
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    exact_text: str = Field(min_length=1)
    disposition: Literal["proposition_unit", "non_propositional"]
    local_unit_ids: list[str] = Field(default_factory=list)
    non_propositional_reason: Literal[
        "connector", "attribution_context", "example_label", "punctuation", "other"
    ] | None = None

    @model_validator(mode="after")
    def validate_segment(self) -> "ClaimCoverageSegment":
        if self.end_char <= self.start_char:
            raise ValueError("coverage segment range is empty or reversed")
        if self.local_unit_ids != sorted(set(self.local_unit_ids)):
            raise ValueError("coverage local unit ids must be canonical")
        if self.disposition == "proposition_unit":
            if not self.local_unit_ids or self.non_propositional_reason is not None:
                raise ValueError("proposition coverage requires only local unit ids")
        elif self.local_unit_ids or self.non_propositional_reason is None:
            raise ValueError("non-propositional coverage requires only a closed reason")
        return self


class AtomicDecompositionProposal(StrictUnitModel):
    schema_version: Literal["wang_viewpoint_atomic_decomposition_proposal_v1"] = (
        "wang_viewpoint_atomic_decomposition_proposal_v1"
    )
    parent_packet_sha256: str
    claim_id: str
    pinned_claim_revision: int = Field(ge=1)
    claim_revision_sha256: str
    units: list[ProposedAtomicUnit] = Field(min_length=1)
    coverage_segments: list[ClaimCoverageSegment] = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_proposal(self) -> "AtomicDecompositionProposal":
        local_ids = [item.local_unit_id for item in self.units]
        expected_ids = [f"U{index:03d}" for index in range(1, len(self.units) + 1)]
        if local_ids != expected_ids:
            raise ValueError("local unit ids must be sorted, unique, and sequential")
        segment_keys = [
            (item.start_char, item.end_char) for item in self.coverage_segments
        ]
        if segment_keys != sorted(set(segment_keys)):
            raise ValueError("coverage segments must be range-sorted and unique")
        referenced_ids = {
            local_id
            for segment in self.coverage_segments
            for local_id in segment.local_unit_ids
        }
        if not referenced_ids <= set(local_ids):
            raise ValueError("coverage references an unknown local unit")
        if referenced_ids != set(local_ids):
            raise ValueError("every proposed unit needs statement coverage")
        return self


class AtomicDecompositionBatchResponse(StrictUnitModel):
    schema_version: Literal["wang_viewpoint_atomic_decomposition_batch_response_v1"] = (
        "wang_viewpoint_atomic_decomposition_batch_response_v1"
    )
    parent_packet_sha256: str
    proposals: list[AtomicDecompositionProposal] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_response(self) -> "AtomicDecompositionBatchResponse":
        claim_ids = [item.claim_id for item in self.proposals]
        if claim_ids != sorted(set(claim_ids)):
            raise ValueError("atomic batch proposals must be Claim-sorted and unique")
        if any(
            item.parent_packet_sha256 != self.parent_packet_sha256
            for item in self.proposals
        ):
            raise ValueError("atomic batch contains a foreign parent packet")
        return self


class PropositionUnitCandidate(StrictUnitModel):
    proposition_unit_id: str
    parent_claim_id: str
    pinned_claim_revision: int = Field(ge=1)
    claim_revision_sha256: str
    source_id: str
    unit_statement: str
    structural_role: Literal["whole_claim", "conjunct", "qualified_clause"]
    claim_statement_spans: list[ClaimStatementSpan]
    evidence: list[ReviewEvidence]
    candidate_status: Literal["atomic_candidate"] = "atomic_candidate"
    approval_status: Literal["not_human_approved"] = "not_human_approved"
    apply_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_candidate(self) -> "PropositionUnitCandidate":
        identity = self.model_dump(
            mode="json", exclude={"proposition_unit_id", "apply_allowed"}
        )
        if self.proposition_unit_id != f"VPU-{sha256_json(identity)[:20]}":
            raise ValueError("unstable proposition unit candidate id")
        return self


class ClaimAtomicDecompositionArtifact(StrictUnitModel):
    schema_version: Literal["wang_viewpoint_claim_atomic_decomposition_v1"] = (
        "wang_viewpoint_claim_atomic_decomposition_v1"
    )
    parent_packet_sha256: str
    claim: ReviewClaim
    proposal: AtomicDecompositionProposal
    proposition_units: list[PropositionUnitCandidate]
    coverage_segments: list[ClaimCoverageSegment]
    model_calls_executed: int = Field(ge=0)
    master_data_mutations: Literal[0] = 0
    apply_allowed: Literal[False] = False
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "ClaimAtomicDecompositionArtifact":
        unit_ids = [item.proposition_unit_id for item in self.proposition_units]
        if unit_ids != sorted(set(unit_ids)):
            raise ValueError("proposition units must be id-sorted and unique")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("atomic decomposition artifact SHA mismatch")
        return self


def build_claim_atomic_decomposition(
    *,
    parent_packet_sha256: str,
    claim: ReviewClaim,
    proposal: AtomicDecompositionProposal,
    model_calls_executed: int,
) -> ClaimAtomicDecompositionArtifact:
    """Validate total Claim coverage and compile stable candidate unit ids."""

    if proposal.parent_packet_sha256 != parent_packet_sha256:
        raise ValueError("atomic proposal parent packet mismatch")
    if (
        proposal.claim_id != claim.claim_id
        or proposal.pinned_claim_revision != claim.pinned_claim_revision
        or proposal.claim_revision_sha256 != claim.claim_revision_sha256
    ):
        raise ValueError("atomic proposal Claim binding mismatch")
    cursor = 0
    statement = claim.statement
    for segment in proposal.coverage_segments:
        if segment.start_char != cursor:
            raise ValueError("Claim coverage has a gap or overlap")
        if statement[segment.start_char : segment.end_char] != segment.exact_text:
            raise ValueError("Claim coverage text does not match the pinned statement")
        cursor = segment.end_char
    if cursor != len(statement):
        raise ValueError("Claim coverage does not reach the end of the statement")
    evidence_by_key = {
        (item.evidence_step_id, item.source_fragment_id): item
        for item in claim.evidence
    }
    candidates: list[PropositionUnitCandidate] = []
    for unit in proposal.units:
        for span in unit.claim_statement_spans:
            if statement[span.start_char : span.end_char] != span.exact_text:
                raise ValueError(f"{unit.local_unit_id}: statement span text mismatch")
        evidence = []
        for reference in unit.evidence_references:
            key = (reference.evidence_step_id, reference.source_fragment_id)
            item = evidence_by_key.get(key)
            if item is None:
                raise ValueError(f"{unit.local_unit_id}: invented evidence reference")
            if not item.valid_for_identity_review:
                raise ValueError(f"{unit.local_unit_id}: evidence is not identity-eligible")
            evidence.append(item)
        identity = {
            "parent_claim_id": claim.claim_id,
            "pinned_claim_revision": claim.pinned_claim_revision,
            "claim_revision_sha256": claim.claim_revision_sha256,
            "source_id": claim.source_id,
            "unit_statement": unit.unit_statement,
            "structural_role": unit.structural_role,
            "claim_statement_spans": [
                item.model_dump(mode="json") for item in unit.claim_statement_spans
            ],
            "evidence": [item.model_dump(mode="json") for item in evidence],
            "candidate_status": "atomic_candidate",
            "approval_status": "not_human_approved",
        }
        candidates.append(
            PropositionUnitCandidate(
                proposition_unit_id=f"VPU-{sha256_json(identity)[:20]}",
                **identity,
            )
        )
    candidates.sort(key=lambda item: item.proposition_unit_id)
    payload = {
        "schema_version": "wang_viewpoint_claim_atomic_decomposition_v1",
        "parent_packet_sha256": parent_packet_sha256,
        "claim": claim.model_dump(mode="json"),
        "proposal": proposal.model_dump(mode="json"),
        "proposition_units": [item.model_dump(mode="json") for item in candidates],
        "coverage_segments": [
            item.model_dump(mode="json") for item in proposal.coverage_segments
        ],
        "model_calls_executed": model_calls_executed,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    return ClaimAtomicDecompositionArtifact(
        **payload, artifact_sha256=sha256_json(payload)
    )
