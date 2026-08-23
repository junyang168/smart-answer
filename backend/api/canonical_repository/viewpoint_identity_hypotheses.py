"""Compile screening groups into immutable, evidence-review hypotheses.

Group discovery is recall work, not identity evidence.  This module preserves
that boundary while removing packet-overlap duplicates and producing stable
hypotheses that can later be reviewed independently.  It never writes master
data and it deliberately does not take a transitive closure.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .knowledge_models import (
    ClaimRecord,
    ViewpointCoverageSnapshotRecord,
    ViewpointIdentityCandidateRecord,
)
from .models import Citation
from .viewpoint_foundation import (
    CLAIM_MANIFEST_VERSION,
    build_foundation_quality_report,
    build_resolution_ledger,
    canonical_json,
    semantic_record_sha,
    sha256_json,
)
from .viewpoint_group_discovery import GroupDiscoveryPlan, ScreeningGroupProposal
from .viewpoint_resolution import (
    ViewpointIdentityReviewPacket,
    build_identity_review_packet,
)
from .viewpoint_source_attestation import IdentitySourceEligibilityArtifact


class StrictHypothesisModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScreeningProposalProvenance(StrictHypothesisModel):
    call_artifact_sha256: str
    packet_id: str
    packet_sha256: str
    local_group_id: str
    proposal_sha256: str
    proposed_core_proposition: str
    rationale: str
    material_differences: list[str]
    evidence_required_claim_ids: list[str]

    @model_validator(mode="after")
    def validate_lists(self) -> "ScreeningProposalProvenance":
        for name in ("material_differences", "evidence_required_claim_ids"):
            values = getattr(self, name)
            if values != sorted(set(values)):
                raise ValueError(f"{name} must be canonical")
        return self


class IdentityReviewHypothesis(StrictHypothesisModel):
    hypothesis_id: str
    relation_kind: Literal["possible_equivalent", "component", "tension"]
    participant_roles: dict[str, str] = Field(min_length=2)
    provenances: list[ScreeningProposalProvenance] = Field(min_length=1)
    screening_only: Literal[True] = True
    identity_evidence: Literal[False] = False
    apply_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_hypothesis(self) -> "IdentityReviewHypothesis":
        if list(self.participant_roles) != sorted(self.participant_roles):
            raise ValueError("hypothesis participants must be canonical")
        provenance_keys = [
            (item.packet_id, item.local_group_id, item.call_artifact_sha256)
            for item in self.provenances
        ]
        if provenance_keys != sorted(set(provenance_keys)):
            raise ValueError("hypothesis provenance must be canonical and unique")
        identity = {
            "relation_kind": self.relation_kind,
            "participant_roles": self.participant_roles,
        }
        if self.hypothesis_id != f"VIH-{sha256_json(identity)[:20]}":
            raise ValueError("hypothesis id is not stable")
        return self


class IdentityHypothesisIndex(StrictHypothesisModel):
    schema_version: Literal["wang_viewpoint_identity_hypothesis_index_v1"] = (
        "wang_viewpoint_identity_hypothesis_index_v1"
    )
    group_discovery_plan_sha256: str
    call_artifact_sha256s: list[str]
    hypotheses: list[IdentityReviewHypothesis]
    statistics: dict[str, int]
    screening_only: Literal[True] = True
    identity_evidence: Literal[False] = False
    apply_allowed: Literal[False] = False
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_index(self) -> "IdentityHypothesisIndex":
        if self.call_artifact_sha256s != sorted(set(self.call_artifact_sha256s)):
            raise ValueError("call artifact SHAs must be canonical")
        ids = [item.hypothesis_id for item in self.hypotheses]
        if ids != sorted(set(ids)):
            raise ValueError("hypotheses must be canonical and unique")
        occurrence_count = sum(len(item.provenances) for item in self.hypotheses)
        kinds = Counter(item.relation_kind for item in self.hypotheses)
        expected = {
            "completed_call_count": len(self.call_artifact_sha256s),
            "proposal_occurrence_count": occurrence_count,
            "unique_hypothesis_count": len(self.hypotheses),
            "overlap_duplicate_occurrence_count": occurrence_count
            - len(self.hypotheses),
            "possible_equivalent_hypothesis_count": kinds["possible_equivalent"],
            "component_hypothesis_count": kinds["component"],
            "tension_hypothesis_count": kinds["tension"],
            "unique_participant_claim_count": len(
                {
                    claim_id
                    for item in self.hypotheses
                    for claim_id in item.participant_roles
                }
            ),
        }
        if self.statistics != expected:
            raise ValueError("identity hypothesis statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("identity hypothesis index SHA mismatch")
        return self


class IdentityEvidenceReviewPlanItem(StrictHypothesisModel):
    hypothesis_id: str
    relation_kind: Literal["possible_equivalent", "component", "tension"]
    identity_candidate_id: str
    participant_claim_ids: list[str] = Field(min_length=2)
    review_packet_sha256: str
    review_packet_bytes: int = Field(ge=1)
    deterministic_blocker_codes: list[str]
    distinct_source_count: int = Field(ge=1)
    call_eligible: bool

    @model_validator(mode="after")
    def validate_item(self) -> "IdentityEvidenceReviewPlanItem":
        if self.participant_claim_ids != sorted(set(self.participant_claim_ids)):
            raise ValueError("review-plan Claim ids must be canonical")
        if self.deterministic_blocker_codes != sorted(
            set(self.deterministic_blocker_codes)
        ):
            raise ValueError("review-plan blocker codes must be canonical")
        if self.call_eligible != (not self.deterministic_blocker_codes):
            raise ValueError("call eligibility must fail closed on deterministic blockers")
        return self


class IdentityEvidencePlanningException(StrictHypothesisModel):
    hypothesis_id: str
    relation_kind: Literal["possible_equivalent", "component", "tension"]
    code: Literal["stale_dependency"]
    record_ids: list[str] = Field(min_length=1)
    detail: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_exception(self) -> "IdentityEvidencePlanningException":
        if self.record_ids != sorted(set(self.record_ids)):
            raise ValueError("planning exception record ids must be canonical")
        return self


class IdentityEvidenceReviewPlan(StrictHypothesisModel):
    schema_version: Literal["wang_viewpoint_identity_evidence_review_plan_v1"] = (
        "wang_viewpoint_identity_evidence_review_plan_v1"
    )
    identity_hypothesis_index_sha256: str
    parent_claim_manifest_sha256: str
    coverage_snapshot_id: str
    coverage_sources_sha256: str
    items: list[IdentityEvidenceReviewPlanItem]
    exceptions: list[IdentityEvidencePlanningException]
    statistics: dict[str, int]
    model_calls_executed: Literal[0] = 0
    master_data_mutations: Literal[0] = 0
    apply_allowed: Literal[False] = False
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_plan(self) -> "IdentityEvidenceReviewPlan":
        item_ids = [item.hypothesis_id for item in self.items]
        exception_ids = [item.hypothesis_id for item in self.exceptions]
        if item_ids != sorted(set(item_ids)):
            raise ValueError("identity evidence plan items must be canonical")
        if exception_ids != sorted(set(exception_ids)):
            raise ValueError("identity evidence exceptions must be canonical")
        if set(item_ids) & set(exception_ids):
            raise ValueError("a hypothesis cannot be both planned and excepted")
        expected = {
            "hypothesis_count": len(self.items) + len(self.exceptions),
            "evidence_packet_count": len(self.items),
            "planning_exception_count": len(self.exceptions),
            "call_eligible_hypothesis_count": sum(item.call_eligible for item in self.items),
            "blocked_hypothesis_count": sum(not item.call_eligible for item in self.items)
            + len(self.exceptions),
            "possible_equivalent_hypothesis_count": sum(
                item.relation_kind == "possible_equivalent"
                for item in [*self.items, *self.exceptions]
            ),
            "component_hypothesis_count": sum(
                item.relation_kind == "component"
                for item in [*self.items, *self.exceptions]
            ),
            "tension_hypothesis_count": sum(
                item.relation_kind == "tension"
                for item in [*self.items, *self.exceptions]
            ),
            "evidence_packet_bytes": sum(item.review_packet_bytes for item in self.items),
            "proposal_assessment_count": sum(item.call_eligible for item in self.items),
            "blind_assessment_count": sum(item.call_eligible for item in self.items),
            "maximum_delta_adjudication_count": sum(
                item.call_eligible for item in self.items
            ),
        }
        if self.statistics != expected:
            raise ValueError("identity evidence review statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("identity evidence review plan SHA mismatch")
        return self


class IdentityCalibrationPlan(StrictHypothesisModel):
    schema_version: Literal["wang_viewpoint_identity_calibration_plan_v1"] = (
        "wang_viewpoint_identity_calibration_plan_v1"
    )
    identity_evidence_review_plan_sha256: str
    selected_hypothesis_ids: list[str] = Field(min_length=1)
    selected_packet_sha256s: list[str] = Field(min_length=1)
    strata: dict[str, int]
    proposal_model_id: str
    proposal_backend: Literal["codex_subscription"] = "codex_subscription"
    blind_model_id: str
    blind_backend: Literal["claude_subscription"] = "claude_subscription"
    reasoning_effort: Literal["medium"] = "medium"
    statistics: dict[str, int]
    model_calls_executed: Literal[0] = 0
    master_data_mutations: Literal[0] = 0
    apply_allowed: Literal[False] = False
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_calibration(self) -> "IdentityCalibrationPlan":
        if self.selected_hypothesis_ids != sorted(set(self.selected_hypothesis_ids)):
            raise ValueError("calibration hypotheses must be canonical")
        if len(self.selected_packet_sha256s) != len(self.selected_hypothesis_ids):
            raise ValueError("calibration packet bindings must be exact")
        selected = len(self.selected_hypothesis_ids)
        expected = {
            "selected_hypothesis_count": selected,
            "proposal_assessment_count": selected,
            "blind_assessment_count": selected,
            "maximum_delta_adjudication_count": selected,
            "initial_model_call_count": selected * 2,
            "maximum_model_call_count": selected * 3,
        }
        if self.statistics != expected:
            raise ValueError("identity calibration statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("identity calibration plan SHA mismatch")
        return self


def build_identity_calibration_plan(
    *, plan: IdentityEvidenceReviewPlan, sample_size: int = 24,
    proposal_model_id: str = "gpt-5.6-sol",
    blind_model_id: str = "claude-sonnet-5",
) -> IdentityCalibrationPlan:
    """Choose a byte-stable round-robin sample across semantic/risk strata."""

    if sample_size < 1:
        raise ValueError("calibration sample size must be positive")
    eligible = [item for item in plan.items if item.call_eligible]
    if sample_size > len(eligible):
        raise ValueError("calibration sample exceeds eligible hypotheses")
    buckets: dict[str, list[IdentityEvidenceReviewPlanItem]] = {}
    for item in eligible:
        source_band = "single_source" if item.distinct_source_count == 1 else "multi_source"
        member_band = "pair" if len(item.participant_claim_ids) == 2 else "multi_member"
        key = f"{item.relation_kind}|{source_band}|{member_band}"
        buckets.setdefault(key, []).append(item)
    for key, rows in buckets.items():
        rows.sort(key=lambda item: sha256_json({
            "plan": plan.artifact_sha256,
            "stratum": key,
            "hypothesis_id": item.hypothesis_id,
        }))
    selected: list[IdentityEvidenceReviewPlanItem] = []
    while len(selected) < sample_size:
        advanced = False
        for key in sorted(buckets):
            if buckets[key] and len(selected) < sample_size:
                selected.append(buckets[key].pop(0))
                advanced = True
        if not advanced:
            raise ValueError("calibration sampler exhausted unexpectedly")
    selected.sort(key=lambda item: item.hypothesis_id)
    strata = Counter(
        f"{item.relation_kind}|"
        f"{'single_source' if item.distinct_source_count == 1 else 'multi_source'}|"
        f"{'pair' if len(item.participant_claim_ids) == 2 else 'multi_member'}"
        for item in selected
    )
    statistics = {
        "selected_hypothesis_count": len(selected),
        "proposal_assessment_count": len(selected),
        "blind_assessment_count": len(selected),
        "maximum_delta_adjudication_count": len(selected),
        "initial_model_call_count": len(selected) * 2,
        "maximum_model_call_count": len(selected) * 3,
    }
    payload = {
        "schema_version": "wang_viewpoint_identity_calibration_plan_v1",
        "identity_evidence_review_plan_sha256": plan.artifact_sha256,
        "selected_hypothesis_ids": [item.hypothesis_id for item in selected],
        "selected_packet_sha256s": [item.review_packet_sha256 for item in selected],
        "strata": dict(sorted(strata.items())),
        "proposal_model_id": proposal_model_id,
        "proposal_backend": "codex_subscription",
        "blind_model_id": blind_model_id,
        "blind_backend": "claude_subscription",
        "reasoning_effort": "medium",
        "statistics": statistics,
        "model_calls_executed": 0,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    return IdentityCalibrationPlan(
        **payload, artifact_sha256=sha256_json(payload)
    )


def _proposal_key(proposal: ScreeningGroupProposal) -> str:
    return canonical_json(
        {
            "relation_kind": proposal.relation_kind,
            "participant_roles": {
                item.claim_id: item.role for item in proposal.participants
            },
        }
    )


def build_identity_hypothesis_index(
    *,
    plan: GroupDiscoveryPlan,
    responses_by_packet_id: Mapping[str, Mapping[str, Any]],
    call_artifact_sha_by_packet_id: Mapping[str, str],
) -> IdentityHypothesisIndex:
    """Deduplicate exact overlap occurrences without semantic merging."""

    packets = {item.packet_id: item for item in plan.packets}
    if set(responses_by_packet_id) != set(packets):
        raise ValueError("identity hypothesis index requires all packet responses")
    if set(call_artifact_sha_by_packet_id) != set(packets):
        raise ValueError("identity hypothesis index requires all call provenances")

    grouped: dict[str, dict[str, Any]] = {}
    for packet_id in sorted(packets):
        packet = packets[packet_id]
        response = responses_by_packet_id[packet_id]
        if response.get("packet_sha256") != packet.packet_sha256:
            raise ValueError(f"{packet_id}: response packet SHA mismatch")
        local_ids: set[str] = set()
        for raw in response.get("proposals") or []:
            proposal = ScreeningGroupProposal.model_validate(raw)
            if proposal.local_group_id in local_ids:
                raise ValueError(f"{packet_id}: duplicate local group id")
            local_ids.add(proposal.local_group_id)
            participant_ids = {item.claim_id for item in proposal.participants}
            packet_claim_ids = {item.claim_id for item in packet.claims}
            if not participant_ids <= packet_claim_ids:
                raise ValueError(f"{packet_id}: proposal invents a Claim")
            key = _proposal_key(proposal)
            row = grouped.setdefault(
                key,
                {
                    "relation_kind": proposal.relation_kind,
                    "participant_roles": {
                        item.claim_id: item.role for item in proposal.participants
                    },
                    "provenances": [],
                },
            )
            proposal_payload = proposal.model_dump(mode="json")
            row["provenances"].append(
                ScreeningProposalProvenance(
                    call_artifact_sha256=call_artifact_sha_by_packet_id[packet_id],
                    packet_id=packet_id,
                    packet_sha256=packet.packet_sha256,
                    local_group_id=proposal.local_group_id,
                    proposal_sha256=sha256_json(proposal_payload),
                    proposed_core_proposition=proposal.proposed_core_proposition,
                    rationale=proposal.rationale,
                    material_differences=proposal.material_differences,
                    evidence_required_claim_ids=proposal.evidence_required_claim_ids,
                )
            )

    hypotheses = []
    for key, row in sorted(grouped.items()):
        identity = {
            "relation_kind": row["relation_kind"],
            "participant_roles": row["participant_roles"],
        }
        hypotheses.append(
            IdentityReviewHypothesis(
                hypothesis_id=f"VIH-{sha256_json(identity)[:20]}",
                relation_kind=row["relation_kind"],
                participant_roles=row["participant_roles"],
                provenances=sorted(
                    row["provenances"],
                    key=lambda item: (
                        item.packet_id,
                        item.local_group_id,
                        item.call_artifact_sha256,
                    ),
                ),
            )
        )
    hypotheses.sort(key=lambda item: item.hypothesis_id)
    kinds = Counter(item.relation_kind for item in hypotheses)
    occurrence_count = sum(len(item.provenances) for item in hypotheses)
    statistics = {
        "completed_call_count": len(call_artifact_sha_by_packet_id),
        "proposal_occurrence_count": occurrence_count,
        "unique_hypothesis_count": len(hypotheses),
        "overlap_duplicate_occurrence_count": occurrence_count - len(hypotheses),
        "possible_equivalent_hypothesis_count": kinds["possible_equivalent"],
        "component_hypothesis_count": kinds["component"],
        "tension_hypothesis_count": kinds["tension"],
        "unique_participant_claim_count": len(
            {
                claim_id
                for item in hypotheses
                for claim_id in item.participant_roles
            }
        ),
    }
    payload = {
        "schema_version": "wang_viewpoint_identity_hypothesis_index_v1",
        "group_discovery_plan_sha256": plan.artifact_sha256,
        "call_artifact_sha256s": sorted(call_artifact_sha_by_packet_id.values()),
        "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
        "statistics": statistics,
        "screening_only": True,
        "identity_evidence": False,
        "apply_allowed": False,
    }
    return IdentityHypothesisIndex(
        **payload, artifact_sha256=sha256_json(payload)
    )


def build_identity_evidence_review_plan(
    *,
    hypothesis_index: IdentityHypothesisIndex,
    claim_manifest: Mapping[str, Any],
    coverage_snapshot: Mapping[str, Any] | ViewpointCoverageSnapshotRecord,
    claims: Sequence[Mapping[str, Any] | ClaimRecord],
    evidence_steps: Sequence[Mapping[str, Any]],
    source_fragments: Sequence[Mapping[str, Any]],
    citations: Sequence[Mapping[str, Any] | Citation] = (),
    claim_relations: Sequence[Mapping[str, Any]] = (),
    constraints: Sequence[Mapping[str, Any]] = (),
    existing_links: Sequence[Mapping[str, Any]] = (),
    source_eligibility_artifact: IdentitySourceEligibilityArtifact | None = None,
) -> tuple[IdentityEvidenceReviewPlan, dict[str, ViewpointIdentityReviewPacket]]:
    """Compile one source-bound packet per non-transitive hypothesis.

    The scoped ledger is intentionally limited to the hypothesis Claims.  Its
    parent manifest SHA remains in the plan, while every packet binds the exact
    scoped manifest through its ledger and quality-report SHAs.
    """

    manifest_payload = dict(claim_manifest)
    stated_manifest_sha = str(manifest_payload.pop("manifest_sha256", ""))
    if (
        claim_manifest.get("schema_version") != CLAIM_MANIFEST_VERSION
        or not stated_manifest_sha
        or stated_manifest_sha != sha256_json(manifest_payload)
    ):
        raise ValueError("identity evidence planning requires a valid Claim manifest")
    coverage = (
        coverage_snapshot
        if isinstance(coverage_snapshot, ViewpointCoverageSnapshotRecord)
        else ViewpointCoverageSnapshotRecord.model_validate(coverage_snapshot)
    )
    if claim_manifest.get("coverage_snapshot_id") != coverage.coverage_snapshot_id:
        raise ValueError("Claim manifest and coverage snapshot mismatch")
    if (
        source_eligibility_artifact is not None
        and source_eligibility_artifact.claim_manifest_sha256 != stated_manifest_sha
    ):
        raise ValueError("source eligibility attestation belongs to another manifest")
    eligibility_attestations = {
        item.claim_id: item.attestation_sha256
        for item in (
            source_eligibility_artifact.attestations
            if source_eligibility_artifact is not None else []
        )
    }
    manifest_rows = {
        str(item["claim_id"]): dict(item) for item in claim_manifest.get("claims") or []
    }
    claim_rows = {
        item.claim_id: item
        for item in (
            raw if isinstance(raw, ClaimRecord) else ClaimRecord.model_validate(raw)
            for raw in claims
        )
    }
    packets: dict[str, ViewpointIdentityReviewPacket] = {}
    items: list[IdentityEvidenceReviewPlanItem] = []
    exceptions: list[IdentityEvidencePlanningException] = []
    for hypothesis in hypothesis_index.hypotheses:
        participant_ids = sorted(hypothesis.participant_roles)
        missing = sorted(
            set(participant_ids) - set(manifest_rows)
            | (set(participant_ids) - set(claim_rows))
        )
        if missing:
            raise ValueError(
                f"{hypothesis.hypothesis_id}: missing participant Claims: "
                + ", ".join(missing)
            )
        stale_ids = sorted(
            claim_id
            for claim_id in participant_ids
            if (
                claim_rows[claim_id].revision
                != int(manifest_rows[claim_id]["pinned_claim_revision"])
                or semantic_record_sha(claim_rows[claim_id])
                != manifest_rows[claim_id]["claim_revision_sha256"]
            )
        )
        if stale_ids:
            exceptions.append(
                IdentityEvidencePlanningException(
                    hypothesis_id=hypothesis.hypothesis_id,
                    relation_kind=hypothesis.relation_kind,
                    code="stale_dependency",
                    record_ids=stale_ids,
                    detail=(
                        "Current authoring Claim revision/SHA differs from the pinned "
                        "group-discovery cohort; rebuild or provide the pinned records."
                    ),
                )
            )
            continue
        scoped_manifest = {
            "schema_version": CLAIM_MANIFEST_VERSION,
            "coverage_snapshot_id": coverage.coverage_snapshot_id,
            "claims": [manifest_rows[claim_id] for claim_id in participant_ids],
        }
        scoped_manifest["manifest_sha256"] = sha256_json(scoped_manifest)
        generation_fingerprint = sha256_json(
            {
                "identity_hypothesis_index_sha256": hypothesis_index.artifact_sha256,
                "hypothesis_id": hypothesis.hypothesis_id,
                "parent_claim_manifest_sha256": stated_manifest_sha,
                "scoped_claim_manifest_sha256": scoped_manifest["manifest_sha256"],
            }
        )
        candidate_identity = {
            "claims": participant_ids,
            "viewpoints": [],
            "relations": [],
            "action": "create_new",
            "blockers": [],
            "coverage_snapshot_id": coverage.coverage_snapshot_id,
            "generation_fingerprint": generation_fingerprint,
        }
        candidate = ViewpointIdentityCandidateRecord(
            identity_candidate_id=f"VIC-{sha256_json(candidate_identity)[:20]}",
            candidate_claim_ids=participant_ids,
            candidate_viewpoint_ids=[],
            seed_relation_ids=[],
            proposed_action="create_new",
            coverage_snapshot_id=coverage.coverage_snapshot_id,
            blocker_codes=[],
            generation_fingerprint=generation_fingerprint,
        )
        ledger = build_resolution_ledger(
            scoped_manifest,
            [
                {
                    "claim_id": claim_id,
                    "pinned_claim_revision": manifest_rows[claim_id][
                        "pinned_claim_revision"
                    ],
                    "claim_revision_sha256": manifest_rows[claim_id][
                        "claim_revision_sha256"
                    ],
                    "processing_status": "resolved",
                    "resolution_kind": "new_viewpoint_candidate",
                    "new_viewpoint_candidate_id": candidate.identity_candidate_id,
                    "decision_id": f"VID-PLAN-{hypothesis.hypothesis_id[4:]}",
                }
                for claim_id in participant_ids
            ],
            coverage_snapshot_id=coverage.coverage_snapshot_id,
        )
        quality = build_foundation_quality_report(
            scope_ids=[hypothesis.hypothesis_id],
            coverage_snapshot=coverage,
            ledger=ledger,
            claims=[claim_rows[claim_id] for claim_id in participant_ids],
            evidence_steps=evidence_steps,
            source_fragments=source_fragments,
            candidate_regression_artifact_sha256=hypothesis_index.artifact_sha256,
            candidate_regression_passed=True,
            source_eligibility_attestations={
                claim_id: eligibility_attestations[claim_id]
                for claim_id in participant_ids if claim_id in eligibility_attestations
            },
        )
        packet = build_identity_review_packet(
            candidate=candidate,
            coverage_snapshot=coverage,
            ledger=ledger,
            quality_report=quality,
            claims=[claim_rows[claim_id] for claim_id in participant_ids],
            evidence_steps=evidence_steps,
            source_fragments=source_fragments,
            citations=citations,
            claim_relations=claim_relations,
            constraints=constraints,
            existing_links=existing_links,
            source_eligibility_attestations={
                claim_id: eligibility_attestations[claim_id]
                for claim_id in participant_ids if claim_id in eligibility_attestations
            },
        )
        packet_json = packet.model_dump(mode="json")
        blocker_codes = sorted(
            {item.code for item in packet.deterministic_blockers}
        )
        items.append(
            IdentityEvidenceReviewPlanItem(
                hypothesis_id=hypothesis.hypothesis_id,
                relation_kind=hypothesis.relation_kind,
                identity_candidate_id=candidate.identity_candidate_id,
                participant_claim_ids=participant_ids,
                review_packet_sha256=packet.packet_sha256,
                review_packet_bytes=len(canonical_json(packet_json).encode("utf-8")),
                deterministic_blocker_codes=blocker_codes,
                distinct_source_count=len({item.source_id for item in packet.claims}),
                call_eligible=not blocker_codes,
            )
        )
        packets[hypothesis.hypothesis_id] = packet
    items.sort(key=lambda item: item.hypothesis_id)
    exceptions.sort(key=lambda item: item.hypothesis_id)
    eligible = sum(item.call_eligible for item in items)
    statistics = {
        "hypothesis_count": len(items) + len(exceptions),
        "evidence_packet_count": len(items),
        "planning_exception_count": len(exceptions),
        "call_eligible_hypothesis_count": eligible,
        "blocked_hypothesis_count": len(items) - eligible + len(exceptions),
        "possible_equivalent_hypothesis_count": sum(
            item.relation_kind == "possible_equivalent"
            for item in [*items, *exceptions]
        ),
        "component_hypothesis_count": sum(
            item.relation_kind == "component" for item in [*items, *exceptions]
        ),
        "tension_hypothesis_count": sum(
            item.relation_kind == "tension" for item in [*items, *exceptions]
        ),
        "evidence_packet_bytes": sum(item.review_packet_bytes for item in items),
        "proposal_assessment_count": eligible,
        "blind_assessment_count": eligible,
        "maximum_delta_adjudication_count": eligible,
    }
    payload = {
        "schema_version": "wang_viewpoint_identity_evidence_review_plan_v1",
        "identity_hypothesis_index_sha256": hypothesis_index.artifact_sha256,
        "parent_claim_manifest_sha256": stated_manifest_sha,
        "coverage_snapshot_id": coverage.coverage_snapshot_id,
        "coverage_sources_sha256": coverage.sources_sha256,
        "items": [item.model_dump(mode="json") for item in items],
        "exceptions": [item.model_dump(mode="json") for item in exceptions],
        "statistics": statistics,
        "model_calls_executed": 0,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    return (
        IdentityEvidenceReviewPlan(
            **payload, artifact_sha256=sha256_json(payload)
        ),
        packets,
    )
