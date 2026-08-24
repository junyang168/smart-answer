"""Source-local argument routes: how the professor got to a viewpoint.

Viewpoint identity answers what he holds; a route answers how he argued it in
one particular sermon.  The two have opposite locality requirements — identity
is established by comparing across sources, a route may never be assembled
across them — so this runs as its own pass, over one source at a time.

Feeding a single source is the structural guarantee: a model that cannot see
another sermon cannot borrow a premise from it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .viewpoint_batch_resolution import BatchResolutionError
from .viewpoint_foundation import sha256_json

ROUTE_PROPOSAL_VERSION = "wang_canonical_viewpoint_route_proposal_v1"


class StrictRouteModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposedRouteAttestation(StrictRouteModel):
    """One argument, as actually delivered in one source."""

    conclusion_key: str = Field(min_length=1)
    route_label: str = Field(min_length=1)
    inference_pattern: str = Field(min_length=1)
    ordered_evidence_step_ids: list[str] = Field(min_length=1)
    completeness: Literal["full", "partial"]
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_attestation(self) -> "ProposedRouteAttestation":
        steps = self.ordered_evidence_step_ids
        if len(steps) != len(set(steps)):
            raise ValueError("a route may not repeat an evidence step")
        return self


class UnusedComponent(StrictRouteModel):
    claim_id: str
    component_index: int = Field(ge=0)
    reason: str = Field(min_length=1)


class RouteProposalResponse(StrictRouteModel):
    schema_version: Literal["wang_canonical_viewpoint_route_proposal_v1"] = (
        ROUTE_PROPOSAL_VERSION
    )
    source_id: str
    attestations: list[ProposedRouteAttestation] = Field(default_factory=list)
    unused_components: list[UnusedComponent] = Field(default_factory=list)


def validate_route_proposal(
    *,
    proposal: RouteProposalResponse,
    source_id: str,
    source_evidence_step_ids: Sequence[str],
    conclusion_keys: Sequence[str],
    member_evidence_step_ids: Sequence[str],
    identity_components: Sequence[tuple[str, int, str]],
) -> dict[str, Any]:
    """Fail closed on cross-source borrowing and on unexplained material.

    ``identity_components`` is (claim_id, component_index, evidence-step key)
    for every member/support component this source contributed, so nothing the
    identity pass judged relevant can quietly vanish here.
    """

    findings: list[str] = []
    if proposal.source_id != source_id:
        findings.append(f"route proposal is for {proposal.source_id}, not {source_id}")

    known_steps = set(source_evidence_step_ids)
    known_conclusions = set(conclusion_keys)
    member_steps = set(member_evidence_step_ids)

    for index, attestation in enumerate(proposal.attestations):
        where = f"attestation#{index}"
        for step_id in attestation.ordered_evidence_step_ids:
            if step_id not in known_steps:
                # The whole point of the per-source pass: a step from another
                # sermon is a fabricated argument, not a richer one.
                findings.append(f"{where}: EvidenceStep {step_id} is not in this source")
        if attestation.conclusion_key not in known_conclusions:
            findings.append(
                f"{where}: conclusion {attestation.conclusion_key} was not linked from this source"
            )
        if attestation.completeness == "full" and not (
            set(attestation.ordered_evidence_step_ids) & member_steps
        ):
            findings.append(
                f"{where}: full requires the source to state the conclusion, not only premises"
            )

    used_pairs = {
        (item.claim_id, item.component_index) for item in proposal.unused_components
    }
    expected_pairs = {(claim_id, index) for claim_id, index, _ in identity_components}
    covered_steps = {
        step_id
        for attestation in proposal.attestations
        for step_id in attestation.ordered_evidence_step_ids
    }
    for claim_id, index, step_key in identity_components:
        if step_key in covered_steps:
            continue
        if (claim_id, index) not in used_pairs:
            findings.append(
                f"{claim_id}#{index}: identity component is in no route and not explained"
            )
    for extra in sorted(used_pairs - expected_pairs):
        findings.append(f"{extra[0]}#{extra[1]}: unused entry names no identity component")

    if findings:
        raise BatchResolutionError(findings)

    report = {
        "schema_version": "wang_canonical_viewpoint_route_validation_v1",
        "source_id": source_id,
        "attestation_count": len(proposal.attestations),
        "full_count": sum(1 for a in proposal.attestations if a.completeness == "full"),
        "partial_count": sum(1 for a in proposal.attestations if a.completeness == "partial"),
        "inference_patterns": sorted({a.inference_pattern for a in proposal.attestations}),
        "unused_component_count": len(proposal.unused_components),
        "checks_passed": [
            "evidence_steps_are_source_local",
            "conclusion_linked_from_this_source",
            "full_requires_stated_conclusion",
            "identity_components_accounted_for",
        ],
    }
    report["artifact_sha256"] = sha256_json(report)
    return report
