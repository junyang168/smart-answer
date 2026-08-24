"""Batch CanonicalViewpoint resolution: proposal, review, deterministic gates.

One reviewed Claim batch goes to a single proposer call, deterministic code
checks every reference and span against the pinned Claims, an independent
reviewer judges the semantics, and only the survivors reach a ChangeSet.

The model never assigns a canonical id, an approval status, or a derived
count.  It emits character offsets and dispositions; this module does the
slicing, the coverage arithmetic and the fail-closed comparisons.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .viewpoint_foundation import sha256_json
from .viewpoint_resolution import ReviewClaim

BATCH_PACKET_VERSION = "wang_canonical_viewpoint_batch_packet_v1"
PROPOSAL_VERSION = "wang_canonical_viewpoint_proposal_v1"
REVIEW_VERSION = "wang_canonical_viewpoint_review_v1"
VALIDATION_VERSION = "wang_canonical_viewpoint_batch_validation_v1"

#: Claims per batch.  The 62-Claim POC ran over ten minutes against a 900s
#: subprocess ceiling, and the reviewer emits more than the proposer does, so
#: the ceiling is set well below the size that has actually been observed.
DEFAULT_BATCH_SIZE = 20

EXISTING_DISPOSITIONS = frozenset(
    {
        "member_existing",
        "support_existing",
        "qualification_existing",
        "tension_existing",
    }
)
TERMINAL_DISPOSITIONS = frozenset(
    {"new_viewpoint", "no_registry_assertion", "deferred"}
)
DISPOSITIONS = EXISTING_DISPOSITIONS | TERMINAL_DISPOSITIONS


class BatchResolutionError(ValueError):
    def __init__(self, findings: Sequence[str]):
        self.findings = list(findings)
        super().__init__("batch resolution failed: " + " | ".join(self.findings))


class StrictBatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposedSpan(StrictBatchModel):
    """One character range of a pinned Claim statement.

    Offsets are Unicode code points over the raw stored statement, `start`
    inclusive and `end` exclusive, matching Python slicing.  No normalization
    of any kind is applied.  ``exact_text`` is not redundant: without it an
    off-by-one offset would be checked against the very slice it selected, so
    it is the only evidence that the model meant this text.
    """

    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    exact_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_span(self) -> "ProposedSpan":
        if self.end_char <= self.start_char:
            raise ValueError("span range is empty or reversed")
        if len(self.exact_text) != self.end_char - self.start_char:
            raise ValueError("span exact_text length does not match its range")
        return self


class ProposedComponent(StrictBatchModel):
    """One truth condition carved out of a Claim, with its disposition.

    ``statement_component`` is deliberately absent: it is the concatenation of
    the spans and carries no independent signal, so making the model emit it
    would spend output tokens on text the validator can rebuild.
    """

    spans: list[ProposedSpan] = Field(min_length=1)
    disposition: Literal[
        "member_existing",
        "support_existing",
        "qualification_existing",
        "tension_existing",
        "new_viewpoint",
        "no_registry_assertion",
        "deferred",
    ]
    target_viewpoint_revision_id: str | None = None
    local_new_viewpoint_key: str | None = None
    evidence_step_ids: list[str] = Field(default_factory=list)
    source_fragment_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_component(self) -> "ProposedComponent":
        keys = [(item.start_char, item.end_char) for item in self.spans]
        if keys != sorted(set(keys)):
            raise ValueError("component spans must be range-sorted and unique")
        for earlier, later in zip(keys, keys[1:], strict=False):
            if later[0] < earlier[1]:
                raise ValueError("component spans overlap")
        if self.evidence_step_ids != sorted(set(self.evidence_step_ids)):
            raise ValueError("evidence step ids must be sorted and unique")
        if self.source_fragment_ids != sorted(set(self.source_fragment_ids)):
            raise ValueError("source fragment ids must be sorted and unique")
        if self.disposition in EXISTING_DISPOSITIONS:
            if not self.target_viewpoint_revision_id:
                raise ValueError(f"{self.disposition} requires a target viewpoint revision")
            if self.local_new_viewpoint_key:
                raise ValueError(f"{self.disposition} may not claim a new viewpoint key")
        elif self.disposition == "new_viewpoint":
            if not self.local_new_viewpoint_key:
                raise ValueError("new_viewpoint requires a local candidate key")
            if self.target_viewpoint_revision_id:
                raise ValueError("new_viewpoint may not target an existing revision")
        else:
            if self.target_viewpoint_revision_id or self.local_new_viewpoint_key:
                raise ValueError(f"{self.disposition} may not carry an identity target")
        needs_evidence = self.disposition in EXISTING_DISPOSITIONS | {"new_viewpoint"}
        if needs_evidence and not (self.evidence_step_ids and self.source_fragment_ids):
            raise ValueError(f"{self.disposition} requires evidence bindings")
        return self

    def statement_component(self) -> str:
        """Rebuild the component text the model referred to."""

        return "".join(item.exact_text for item in self.spans)

    def canonical_spans(self) -> list[list[Any]]:
        return [[item.start_char, item.end_char, item.exact_text] for item in self.spans]


class ProposedClaimDecision(StrictBatchModel):
    claim_id: str
    components: list[ProposedComponent] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_decision(self) -> "ProposedClaimDecision":
        keys = [
            (span.start_char, span.end_char)
            for component in self.components
            for span in component.spans
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("a Claim may not bind the same span to two components")
        return self


class NewViewpointCandidate(StrictBatchModel):
    local_key: str = Field(min_length=1)
    core_proposition: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    predicate_object: str = Field(min_length=1)
    polarity: Literal["affirmed", "denied"]
    modality: str = Field(min_length=1)
    scripture_scope: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    population_scope: list[str] = Field(default_factory=list)
    novelty_comparison: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_candidate(self) -> "NewViewpointCandidate":
        for name in ("scripture_scope", "conditions", "population_scope"):
            values = getattr(self, name)
            if values != sorted(set(values)):
                raise ValueError(f"{name} must be sorted and unique")
        return self


class CanonicalViewpointProposalResponse(StrictBatchModel):
    """Exactly what the proposer model returns.

    No ids, no approval status, no counts: everything derivable is derived by
    :func:`validate_proposal` instead.
    """

    schema_version: Literal["wang_canonical_viewpoint_proposal_v1"] = PROPOSAL_VERSION
    batch_id: str
    claim_decisions: list[ProposedClaimDecision] = Field(min_length=1)
    new_viewpoint_candidates: list[NewViewpointCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_response(self) -> "CanonicalViewpointProposalResponse":
        claim_ids = [item.claim_id for item in self.claim_decisions]
        if claim_ids != sorted(set(claim_ids)):
            raise ValueError("proposal claim decisions must be id-sorted and unique")
        keys = [item.local_key for item in self.new_viewpoint_candidates]
        if keys != sorted(set(keys)):
            raise ValueError("new viewpoint candidates must be key-sorted and unique")
        return self


class ReviewedChange(StrictBatchModel):
    claim_id: str
    component_index: int = Field(ge=0)
    decision: Literal["pass", "correct", "reject", "defer"]
    finding_codes: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    correction: str | None = None

    @model_validator(mode="after")
    def validate_change(self) -> "ReviewedChange":
        if self.finding_codes != sorted(set(self.finding_codes)):
            raise ValueError("finding codes must be sorted and unique")
        if self.decision == "pass":
            if self.finding_codes or self.correction:
                raise ValueError("a passing change carries no finding or correction")
        elif not self.finding_codes:
            raise ValueError(f"{self.decision} requires at least one finding code")
        if self.decision == "correct" and not self.correction:
            raise ValueError("correct requires the acceptance criteria of the correction")
        return self


class NoveltyReview(StrictBatchModel):
    status: Literal["pass", "missed_novelty"]
    missed_claim_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_novelty(self) -> "NoveltyReview":
        if self.missed_claim_ids != sorted(set(self.missed_claim_ids)):
            raise ValueError("missed claim ids must be sorted and unique")
        if self.status == "missed_novelty" and not self.missed_claim_ids:
            raise ValueError("missed_novelty requires the Claims that were missed")
        if self.status == "pass" and self.missed_claim_ids:
            raise ValueError("a passing novelty review names no missed Claims")
        return self


class CanonicalViewpointReviewResponse(StrictBatchModel):
    schema_version: Literal["wang_canonical_viewpoint_review_v1"] = REVIEW_VERSION
    proposal_sha256: str
    change_reviews: list[ReviewedChange] = Field(min_length=1)
    novelty_review: NoveltyReview

    @model_validator(mode="after")
    def validate_review(self) -> "CanonicalViewpointReviewResponse":
        keys = [(item.claim_id, item.component_index) for item in self.change_reviews]
        if keys != sorted(set(keys)):
            raise ValueError("change reviews must be pointer-sorted and unique")
        return self

    def outcome(self) -> str:
        if self.novelty_review.status != "pass":
            return "findings"
        if any(item.decision != "pass" for item in self.change_reviews):
            return "findings"
        return "pass"


def split_batches(
    claim_ids: Sequence[str], *, batch_size: int = DEFAULT_BATCH_SIZE
) -> list[list[str]]:
    """Cut a scope into ordered batches of at most ``batch_size`` Claims."""

    if batch_size < 1:
        raise ValueError("batch size must be positive")
    ordered = sorted(set(claim_ids))
    if len(ordered) != len(claim_ids):
        raise ValueError("batch scope contains duplicate Claim ids")
    return [ordered[index : index + batch_size] for index in range(0, len(ordered), batch_size)]


def component_key(claim: ReviewClaim, component: ProposedComponent) -> str:
    """Derived uniqueness key for one Claim component.

    Never stored: `component_locator` is only valid on `equivalent_component`
    links, so an `equivalent_full` link has nowhere to put one.  Computing the
    key instead lets both link types share the single-active-owner invariant.
    """

    return sha256_json(
        {
            "claim_id": claim.claim_id,
            "claim_revision_sha256": claim.claim_revision_sha256,
            "canonical_spans": component.canonical_spans(),
        }
    )


def validate_proposal(
    *,
    proposal: CanonicalViewpointProposalResponse,
    batch_id: str,
    claims: Sequence[ReviewClaim],
    registry_revision_ids: Sequence[str],
) -> dict[str, Any]:
    """Fail closed on everything a program can decide, before the reviewer runs.

    Returns a validation report.  Raises :class:`BatchResolutionError` with
    every finding rather than the first, so one run surfaces the whole problem.
    """

    findings: list[str] = []
    if proposal.batch_id != batch_id:
        findings.append(f"proposal batch {proposal.batch_id} is not batch {batch_id}")

    claim_index = {item.claim_id: item for item in claims}
    expected_ids = sorted(claim_index)
    decided_ids = [item.claim_id for item in proposal.claim_decisions]
    for missing in sorted(set(expected_ids) - set(decided_ids)):
        findings.append(f"{missing}: Claim has no disposition")
    for extra in sorted(set(decided_ids) - set(expected_ids)):
        findings.append(f"{extra}: Claim is not in this batch")

    known_revisions = set(registry_revision_ids)
    candidate_keys = {item.local_key for item in proposal.new_viewpoint_candidates}
    referenced_keys: set[str] = set()
    seen_component_keys: dict[str, tuple[str, int]] = {}
    counts = {name: 0 for name in sorted(DISPOSITIONS)}

    for decision in proposal.claim_decisions:
        claim = claim_index.get(decision.claim_id)
        if claim is None:
            continue
        statement = claim.statement
        evidence_pairs = {
            (item.evidence_step_id, item.source_fragment_id) for item in claim.evidence
        }
        eligible_pairs = {
            (item.evidence_step_id, item.source_fragment_id)
            for item in claim.evidence
            if item.valid_for_identity_review
        }
        for index, component in enumerate(decision.components):
            where = f"{decision.claim_id}#{index}"
            counts[component.disposition] += 1
            for span in component.spans:
                if span.end_char > len(statement):
                    findings.append(f"{where}: span {span.start_char}-{span.end_char} runs past the statement")
                elif statement[span.start_char : span.end_char] != span.exact_text:
                    findings.append(f"{where}: span text does not match the pinned statement")
            if (
                component.target_viewpoint_revision_id
                and component.target_viewpoint_revision_id not in known_revisions
            ):
                findings.append(
                    f"{where}: target revision {component.target_viewpoint_revision_id} "
                    "was not in the packet"
                )
            if component.local_new_viewpoint_key:
                referenced_keys.add(component.local_new_viewpoint_key)
                if component.local_new_viewpoint_key not in candidate_keys:
                    findings.append(
                        f"{where}: local key {component.local_new_viewpoint_key} has no candidate"
                    )
            pairs = set(zip(component.evidence_step_ids, component.source_fragment_ids, strict=False))
            for step_id in component.evidence_step_ids:
                if not any(step_id == pair[0] for pair in evidence_pairs):
                    findings.append(f"{where}: EvidenceStep {step_id} does not belong to the Claim")
            for fragment_id in component.source_fragment_ids:
                if not any(fragment_id == pair[1] for pair in evidence_pairs):
                    findings.append(f"{where}: SourceFragment {fragment_id} does not belong to the Claim")
            if component.disposition in EXISTING_DISPOSITIONS | {"new_viewpoint"}:
                if pairs and not (pairs & eligible_pairs):
                    findings.append(f"{where}: no identity-eligible evidence pair")
            key = component_key(claim, component)
            if component.disposition in {"member_existing", "new_viewpoint"}:
                owner = seen_component_keys.get(key)
                if owner is not None:
                    findings.append(
                        f"{where}: component already claimed as a member by "
                        f"{owner[0]}#{owner[1]}"
                    )
                else:
                    seen_component_keys[key] = (decision.claim_id, index)

    for orphan in sorted(candidate_keys - referenced_keys):
        findings.append(f"{orphan}: new viewpoint candidate has no member component")

    if findings:
        raise BatchResolutionError(findings)

    report = {
        "schema_version": VALIDATION_VERSION,
        "batch_id": batch_id,
        "input_claim_count": len(expected_ids),
        "decided_claim_count": len(decided_ids),
        "component_count": sum(len(item.components) for item in proposal.claim_decisions),
        "disposition_counts": counts,
        "new_viewpoint_candidate_count": len(proposal.new_viewpoint_candidates),
        "member_component_keys": sorted(seen_component_keys),
        "checks_passed": [
            "exact_once_claim_coverage",
            "span_within_statement",
            "span_text_matches_pinned_statement",
            "target_revision_in_packet",
            "new_viewpoint_key_resolvable",
            "evidence_belongs_to_claim",
            "identity_evidence_eligible",
            "member_component_single_owner",
            "candidate_has_member_component",
        ],
    }
    report["artifact_sha256"] = sha256_json(report)
    return report


def validate_review(
    *,
    review: CanonicalViewpointReviewResponse,
    proposal: CanonicalViewpointProposalResponse,
    proposal_sha256: str,
) -> dict[str, Any]:
    """Require the reviewer to have answered every proposed change."""

    findings: list[str] = []
    if review.proposal_sha256 != proposal_sha256:
        findings.append("review is bound to a different proposal")

    expected = sorted(
        (decision.claim_id, index)
        for decision in proposal.claim_decisions
        for index in range(len(decision.components))
    )
    reviewed = sorted((item.claim_id, item.component_index) for item in review.change_reviews)
    for missing in sorted(set(expected) - set(reviewed)):
        findings.append(f"{missing[0]}#{missing[1]}: no review decision")
    for extra in sorted(set(reviewed) - set(expected)):
        findings.append(f"{extra[0]}#{extra[1]}: review points at no proposed change")

    claim_ids = {item.claim_id for item in proposal.claim_decisions}
    for claim_id in review.novelty_review.missed_claim_ids:
        if claim_id not in claim_ids:
            findings.append(f"{claim_id}: novelty finding names a Claim outside the batch")

    if findings:
        raise BatchResolutionError(findings)

    outcome = review.outcome()
    report = {
        "schema_version": "wang_canonical_viewpoint_review_validation_v1",
        "proposal_sha256": proposal_sha256,
        "reviewed_change_count": len(reviewed),
        "outcome": outcome,
        "decision_counts": {
            name: sum(1 for item in review.change_reviews if item.decision == name)
            for name in ("pass", "correct", "reject", "defer")
        },
        "novelty_status": review.novelty_review.status,
        "reconsideration_required": outcome != "pass",
    }
    report["artifact_sha256"] = sha256_json(report)
    return report


def build_batch_packet(
    *,
    batch_id: str,
    scope_label: str,
    claims: Sequence[ReviewClaim],
    registry_context: Sequence[Mapping[str, Any]],
    pending_candidates: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Compile the deterministic packet one proposer call receives."""

    claim_ids = [item.claim_id for item in claims]
    if claim_ids != sorted(set(claim_ids)):
        raise ValueError("batch packet claims must be id-sorted and unique")
    if not claims:
        raise ValueError("batch packet requires at least one Claim")
    packet = {
        "schema_version": BATCH_PACKET_VERSION,
        "batch_id": batch_id,
        "scope_label": scope_label,
        "registry_completeness_warning": (
            "现有 CanonicalViewpoints 是开放参考集，不是封闭 taxonomy。"
            "Registry 可能不完整；没有匹配项时必须提出 new_viewpoint，不得强行归类。"
        ),
        "claims": [item.model_dump(mode="json") for item in claims],
        "registry_context": [dict(item) for item in registry_context],
        "pending_candidates": [dict(item) for item in pending_candidates],
    }
    packet["packet_sha256"] = sha256_json(packet)
    return packet
