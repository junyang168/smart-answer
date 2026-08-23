"""Closed, evidence-bound identity boundary review for CanonicalViewpoint bootstrap.

This is phase one of identity resolution.  It deliberately cannot author a
CanonicalViewpoint, proposition signature, scope, master-data id, or approval.
Two independent reviewers classify the exact same participant set.  Only an
exact semantic agreement may advance to synthesis; disagreement remains a
closed review result instead of being repaired into apparent consensus.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .viewpoint_foundation import sha256_json
from .viewpoint_resolution import ReviewerAdapter, ViewpointIdentityReviewPacket
from .viewpoint_identity_hypotheses import IdentityEvidenceReviewPlan


BOUNDARY_ENGINE_VERSION = "viewpoint_identity_boundary_engine_v1"
BOUNDARY_CALL_VERSION = "wang_viewpoint_identity_boundary_call_v1"
BOUNDARY_RUN_VERSION = "wang_viewpoint_identity_boundary_run_v1"

BoundaryRelation = Literal[
    "equivalent_all",
    "component",
    "tension",
    "related_only",
    "mixed",
    "unknown",
]
SuccessorRelation = Literal[
    "equivalent_all", "component", "tension", "related_only", "unknown"
]


class StrictBoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentityBoundaryCalibrationPlan(StrictBoundaryModel):
    schema_version: Literal["wang_viewpoint_identity_boundary_calibration_plan_v1"] = (
        "wang_viewpoint_identity_boundary_calibration_plan_v1"
    )
    identity_evidence_review_plan_sha256: str
    exclusion_plan_sha256s: list[str] = Field(default_factory=list)
    excluded_hypothesis_ids: list[str] = Field(default_factory=list)
    selected_hypothesis_ids: list[str] = Field(min_length=1)
    selected_packet_sha256s: list[str] = Field(min_length=1)
    strata: dict[str, int]
    proposal_model_id: str
    proposal_backend: Literal["codex_subscription"] = "codex_subscription"
    blind_model_id: str
    blind_backend: Literal["claude_subscription"] = "claude_subscription"
    reasoning_effort: Literal["high", "xhigh"] = "high"
    statistics: dict[str, int]
    model_calls_executed: Literal[0] = 0
    master_data_mutations: Literal[0] = 0
    apply_allowed: Literal[False] = False
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_plan(self) -> "IdentityBoundaryCalibrationPlan":
        for name in (
            "exclusion_plan_sha256s",
            "excluded_hypothesis_ids",
            "selected_hypothesis_ids",
        ):
            values = getattr(self, name)
            if values != sorted(set(values)):
                raise ValueError(f"{name} must be canonical")
        if set(self.excluded_hypothesis_ids) & set(self.selected_hypothesis_ids):
            raise ValueError("holdout selection overlaps its exclusions")
        if len(self.selected_packet_sha256s) != len(self.selected_hypothesis_ids):
            raise ValueError("boundary calibration packet bindings must be exact")
        selected = len(self.selected_hypothesis_ids)
        if self.statistics != {
            "selected_hypothesis_count": selected,
            "proposal_assessment_count": selected,
            "blind_assessment_count": selected,
            "model_call_count": selected * 2,
        }:
            raise ValueError("boundary calibration statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("boundary calibration plan SHA mismatch")
        return self


def build_identity_boundary_calibration_plan(
    *,
    evidence_plan: IdentityEvidenceReviewPlan,
    sample_size: int,
    excluded_hypothesis_ids: set[str],
    exclusion_plan_sha256s: list[str],
    proposal_model_id: str = "gpt-5.6-sol",
    blind_model_id: str = "claude-opus-5",
    reasoning_effort: Literal["high", "xhigh"] = "high",
) -> IdentityBoundaryCalibrationPlan:
    """Choose a deterministic stratified holdout disjoint from prior calibration."""

    eligible = [
        item
        for item in evidence_plan.items
        if item.call_eligible and item.hypothesis_id not in excluded_hypothesis_ids
    ]
    if sample_size < 1 or sample_size > len(eligible):
        raise ValueError("invalid boundary calibration sample size")
    buckets: dict[str, list[Any]] = {}
    for item in eligible:
        source_band = "single_source" if item.distinct_source_count == 1 else "multi_source"
        member_band = "pair" if len(item.participant_claim_ids) == 2 else "multi_member"
        key = f"{item.relation_kind}|{source_band}|{member_band}"
        buckets.setdefault(key, []).append(item)
    exclusion_sha = sha256_json(sorted(excluded_hypothesis_ids))
    for key, rows in buckets.items():
        rows.sort(
            key=lambda item: sha256_json(
                {
                    "evidence_plan": evidence_plan.artifact_sha256,
                    "exclusion_sha256": exclusion_sha,
                    "stratum": key,
                    "hypothesis_id": item.hypothesis_id,
                }
            )
        )
    selected: list[Any] = []
    while len(selected) < sample_size:
        advanced = False
        for key in sorted(buckets):
            if buckets[key] and len(selected) < sample_size:
                selected.append(buckets[key].pop(0))
                advanced = True
        if not advanced:
            raise ValueError("boundary holdout sampler exhausted unexpectedly")
    selected.sort(key=lambda item: item.hypothesis_id)
    strata = Counter(
        f"{item.relation_kind}|"
        f"{'single_source' if item.distinct_source_count == 1 else 'multi_source'}|"
        f"{'pair' if len(item.participant_claim_ids) == 2 else 'multi_member'}"
        for item in selected
    )
    payload = {
        "schema_version": "wang_viewpoint_identity_boundary_calibration_plan_v1",
        "identity_evidence_review_plan_sha256": evidence_plan.artifact_sha256,
        "exclusion_plan_sha256s": sorted(set(exclusion_plan_sha256s)),
        "excluded_hypothesis_ids": sorted(excluded_hypothesis_ids),
        "selected_hypothesis_ids": [item.hypothesis_id for item in selected],
        "selected_packet_sha256s": [item.review_packet_sha256 for item in selected],
        "strata": dict(sorted(strata.items())),
        "proposal_model_id": proposal_model_id,
        "proposal_backend": "codex_subscription",
        "blind_model_id": blind_model_id,
        "blind_backend": "claude_subscription",
        "reasoning_effort": reasoning_effort,
        "statistics": {
            "selected_hypothesis_count": len(selected),
            "proposal_assessment_count": len(selected),
            "blind_assessment_count": len(selected),
            "model_call_count": len(selected) * 2,
        },
        "model_calls_executed": 0,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    return IdentityBoundaryCalibrationPlan(
        **payload, artifact_sha256=sha256_json(payload)
    )


class BoundaryPartitionGroup(StrictBoundaryModel):
    relation: SuccessorRelation
    participant_claim_ids: list[str] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_group(self) -> "BoundaryPartitionGroup":
        if self.participant_claim_ids != sorted(set(self.participant_claim_ids)):
            raise ValueError("partition group participants must be canonical")
        return self


class IdentityBoundaryAssessment(StrictBoundaryModel):
    schema_version: Literal["wang_viewpoint_identity_boundary_assessment_v1"] = (
        "wang_viewpoint_identity_boundary_assessment_v1"
    )
    hypothesis_id: str
    packet_sha256: str
    participant_claim_ids: list[str] = Field(min_length=2)
    whole_relation: BoundaryRelation
    mixed_partition: list[BoundaryPartitionGroup] = Field(default_factory=list)
    mixed_unassigned_claim_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def canonicalize_transport_lists(cls, value: Any) -> Any:
        """Normalize ordering only; never infer a relation or participant."""
        if not isinstance(value, dict):
            return value
        result = dict(value)
        if isinstance(result.get("participant_claim_ids"), list):
            result["participant_claim_ids"] = sorted(
                set(str(item) for item in result["participant_claim_ids"])
            )
        if isinstance(result.get("mixed_unassigned_claim_ids"), list):
            result["mixed_unassigned_claim_ids"] = sorted(
                set(str(item) for item in result["mixed_unassigned_claim_ids"])
            )
        groups = result.get("mixed_partition")
        if isinstance(groups, list):
            normalized = []
            for group in groups:
                item = dict(group)
                if isinstance(item.get("participant_claim_ids"), list):
                    item["participant_claim_ids"] = sorted(
                        set(str(claim_id) for claim_id in item["participant_claim_ids"])
                    )
                normalized.append(item)
            result["mixed_partition"] = sorted(
                normalized,
                key=lambda item: (
                    tuple(item.get("participant_claim_ids", [])),
                    str(item.get("relation", "")),
                ),
            )
        return result

    @model_validator(mode="after")
    def validate_assessment(self) -> "IdentityBoundaryAssessment":
        participants = self.participant_claim_ids
        if participants != sorted(set(participants)):
            raise ValueError("boundary participants must be canonical")
        unassigned = self.mixed_unassigned_claim_ids
        if unassigned != sorted(set(unassigned)):
            raise ValueError("mixed unassigned participants must be canonical")
        if self.whole_relation != "mixed":
            if self.mixed_partition or unassigned:
                raise ValueError("only mixed may propose a partition")
            return self
        if len(participants) < 3 or not self.mixed_partition:
            raise ValueError("mixed requires at least three participants and a partition")
        group_keys = [
            (tuple(item.participant_claim_ids), item.relation)
            for item in self.mixed_partition
        ]
        if group_keys != sorted(set(group_keys)):
            raise ValueError("mixed partition groups must be canonical and unique")
        grouped = [
            claim_id
            for item in self.mixed_partition
            for claim_id in item.participant_claim_ids
        ]
        if len(grouped) != len(set(grouped)):
            raise ValueError("mixed partition groups must be disjoint")
        covered = sorted([*grouped, *unassigned])
        if covered != participants:
            raise ValueError("mixed partition must cover the exact participant set")
        if any(item.participant_claim_ids == participants for item in self.mixed_partition):
            raise ValueError("mixed cannot restate the original hypothesis")
        return self


BoundaryStage = Literal["proposal", "blind_review"]


class BoundaryReviewCallArtifact(StrictBoundaryModel):
    schema_version: Literal["wang_viewpoint_identity_boundary_call_v1"] = (
        BOUNDARY_CALL_VERSION
    )
    stage: BoundaryStage
    semantic_call_ordinal: Literal[1, 2]
    packet_sha256: str
    model_id: str
    prompt_sha256: str
    generation_fingerprint_sha256: str
    assessment: IdentityBoundaryAssessment
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "BoundaryReviewCallArtifact":
        expected = 1 if self.stage == "proposal" else 2
        if self.semantic_call_ordinal != expected:
            raise ValueError("boundary call ordinal does not match stage")
        if self.assessment.packet_sha256 != self.packet_sha256:
            raise ValueError("boundary assessment packet SHA mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("boundary call artifact SHA mismatch")
        return self


class BoundarySuccessorHypothesis(StrictBoundaryModel):
    successor_hypothesis_id: str
    parent_hypothesis_id: str
    relation: SuccessorRelation
    participant_claim_ids: list[str] = Field(min_length=2)
    evidence_packet_sha256: str
    apply_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_successor(self) -> "BoundarySuccessorHypothesis":
        if self.participant_claim_ids != sorted(set(self.participant_claim_ids)):
            raise ValueError("successor participants must be canonical")
        identity = self.model_dump(
            mode="json", exclude={"successor_hypothesis_id", "apply_allowed"}
        )
        if self.successor_hypothesis_id != f"VIBH-{sha256_json(identity)[:20]}":
            raise ValueError("unstable successor hypothesis id")
        return self


class IdentityBoundaryRunArtifact(StrictBoundaryModel):
    schema_version: Literal["wang_viewpoint_identity_boundary_run_v1"] = (
        BOUNDARY_RUN_VERSION
    )
    boundary_run_id: str
    hypothesis_id: str
    packet_sha256: str
    proposal_call_artifact_sha256: str
    blind_call_artifact_sha256: str
    semantic_agreement: bool
    agreed_relation: BoundaryRelation | None = None
    synthesis_eligible: bool
    successor_hypotheses: list[BoundarySuccessorHypothesis] = Field(default_factory=list)
    disposition: Literal["agreed_boundary", "boundary_disagreement"]
    master_data_mutations: Literal[0] = 0
    apply_allowed: Literal[False] = False
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_run(self) -> "IdentityBoundaryRunArtifact":
        expected_agreement = self.disposition == "agreed_boundary"
        if self.semantic_agreement != expected_agreement:
            raise ValueError("boundary disposition does not match reviewer agreement")
        if (self.agreed_relation is not None) != self.semantic_agreement:
            raise ValueError("agreed relation must exist exactly on agreement")
        if self.synthesis_eligible != (
            self.semantic_agreement and self.agreed_relation == "equivalent_all"
        ):
            raise ValueError("only equivalent_all agreement may enter synthesis")
        successor_ids = [item.successor_hypothesis_id for item in self.successor_hypotheses]
        if successor_ids != sorted(set(successor_ids)):
            raise ValueError("successor hypotheses must be canonical and unique")
        if self.successor_hypotheses and self.agreed_relation != "mixed":
            raise ValueError("only agreed mixed boundaries produce successors")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("boundary run artifact SHA mismatch")
        identity = dict(payload)
        identity.pop("boundary_run_id")
        if self.boundary_run_id != f"VIBR-{sha256_json(identity)[:20]}":
            raise ValueError("unstable boundary run id")
        return self


def _write_immutable(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json")
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != payload:
            raise ValueError(f"immutable artifact differs at {path}")
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _write_payload_immutable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != payload:
            raise ValueError(f"immutable artifact differs at {path}")
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _semantic_payload(value: IdentityBoundaryAssessment) -> dict[str, Any]:
    payload = value.model_dump(mode="json")
    payload.pop("rationale")
    return payload


def _boundary_call(
    *,
    stage: BoundaryStage,
    ordinal: Literal[1, 2],
    hypothesis_id: str,
    review_payload: Mapping[str, Any],
    packet_sha256: str,
    participant_claim_ids: list[str],
    reviewer: ReviewerAdapter,
    output_dir: Path,
) -> BoundaryReviewCallArtifact:
    fingerprint = sha256_json(
        {
            "engine_version": BOUNDARY_ENGINE_VERSION,
            "stage": stage,
            "hypothesis_id": hypothesis_id,
            "packet_sha256": packet_sha256,
            "model_id": reviewer.model_id,
            "prompt_sha256": reviewer.prompt_sha256,
        }
    )
    path = output_dir / f"{stage}.{fingerprint[:20]}.json"
    if path.exists():
        cached = BoundaryReviewCallArtifact.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        if (
            cached.generation_fingerprint_sha256 != fingerprint
            or cached.stage != stage
            or cached.semantic_call_ordinal != ordinal
            or cached.packet_sha256 != packet_sha256
            or cached.model_id != reviewer.model_id
            or cached.prompt_sha256 != reviewer.prompt_sha256
        ):
            raise ValueError("cached boundary call binding mismatch")
        return cached
    raw = reviewer.generate(
        {
            "hypothesis_id": hypothesis_id,
            "evidence_packet": dict(review_payload),
        }
    )
    try:
        assessment = IdentityBoundaryAssessment.model_validate(raw)
    except Exception as exc:
        failure_payload = {
            "schema_version": "wang_viewpoint_identity_boundary_call_failure_v1",
            "stage": stage,
            "semantic_call_ordinal": ordinal,
            "hypothesis_id": hypothesis_id,
            "packet_sha256": packet_sha256,
            "model_id": reviewer.model_id,
            "prompt_sha256": reviewer.prompt_sha256,
            "generation_fingerprint_sha256": fingerprint,
            "error": str(exc),
            "raw_response": raw,
        }
        failure_payload["artifact_sha256"] = sha256_json(failure_payload)
        _write_payload_immutable(
            output_dir / f"{stage}.{fingerprint[:20]}.failure.json",
            failure_payload,
        )
        raise
    expected_claim_ids = participant_claim_ids
    if assessment.hypothesis_id != hypothesis_id:
        raise ValueError(f"{stage}: boundary hypothesis id mismatch")
    if assessment.packet_sha256 != packet_sha256:
        raise ValueError(f"{stage}: boundary packet SHA mismatch")
    if assessment.participant_claim_ids != expected_claim_ids:
        raise ValueError(f"{stage}: boundary assessment must cover exact participants")
    payload = {
        "schema_version": BOUNDARY_CALL_VERSION,
        "stage": stage,
        "semantic_call_ordinal": ordinal,
        "packet_sha256": packet_sha256,
        "model_id": reviewer.model_id,
        "prompt_sha256": reviewer.prompt_sha256,
        "generation_fingerprint_sha256": fingerprint,
        "assessment": assessment.model_dump(mode="json"),
    }
    artifact = BoundaryReviewCallArtifact.model_validate(
        payload | {"artifact_sha256": sha256_json(payload)}
    )
    _write_immutable(path, artifact)
    return artifact


def run_identity_boundary_review(
    *,
    hypothesis_id: str,
    packet: Mapping[str, Any] | ViewpointIdentityReviewPacket,
    proposal_reviewer: ReviewerAdapter,
    blind_reviewer: ReviewerAdapter,
    output_dir: Path,
    context_packet: Mapping[str, Any] | None = None,
) -> IdentityBoundaryRunArtifact:
    """Run exactly two independent closed boundary classifications."""

    review_packet = (
        packet
        if isinstance(packet, ViewpointIdentityReviewPacket)
        else ViewpointIdentityReviewPacket.model_validate(packet)
    )
    if review_packet.deterministic_blockers:
        raise ValueError("blocked evidence packet cannot enter boundary review")
    review_payload: Mapping[str, Any] = review_packet.model_dump(mode="json")
    binding_packet_sha256 = review_packet.packet_sha256
    if context_packet is not None:
        if context_packet.get("parent_packet_sha256") != review_packet.packet_sha256:
            raise ValueError("context packet parent binding mismatch")
        if context_packet.get("hypothesis_id") != hypothesis_id:
            raise ValueError("context packet hypothesis binding mismatch")
        if context_packet.get("participant_claim_ids") != review_packet.candidate.candidate_claim_ids:
            raise ValueError("context packet participant binding mismatch")
        stated_sha = str(context_packet.get("packet_sha256") or "")
        sha_payload = dict(context_packet)
        sha_payload.pop("packet_sha256", None)
        if stated_sha != sha256_json(sha_payload):
            raise ValueError("context packet SHA mismatch")
        review_payload = context_packet
        binding_packet_sha256 = stated_sha
    if proposal_reviewer.model_id == blind_reviewer.model_id:
        raise ValueError("boundary reviewers require independent model identities")
    if proposal_reviewer.prompt_sha256 == blind_reviewer.prompt_sha256:
        raise ValueError("boundary reviewers require distinct prompt identities")
    proposal = _boundary_call(
        stage="proposal",
        ordinal=1,
        hypothesis_id=hypothesis_id,
        review_payload=review_payload,
        packet_sha256=binding_packet_sha256,
        participant_claim_ids=review_packet.candidate.candidate_claim_ids,
        reviewer=proposal_reviewer,
        output_dir=output_dir,
    )
    blind = _boundary_call(
        stage="blind_review",
        ordinal=2,
        hypothesis_id=hypothesis_id,
        review_payload=review_payload,
        packet_sha256=binding_packet_sha256,
        participant_claim_ids=review_packet.candidate.candidate_claim_ids,
        reviewer=blind_reviewer,
        output_dir=output_dir,
    )
    agreement = _semantic_payload(proposal.assessment) == _semantic_payload(
        blind.assessment
    )
    relation = proposal.assessment.whole_relation if agreement else None
    successors: list[BoundarySuccessorHypothesis] = []
    if relation == "mixed":
        for group in proposal.assessment.mixed_partition:
            identity = {
                "parent_hypothesis_id": hypothesis_id,
                "relation": group.relation,
                "participant_claim_ids": group.participant_claim_ids,
                "evidence_packet_sha256": binding_packet_sha256,
            }
            successors.append(
                BoundarySuccessorHypothesis(
                    successor_hypothesis_id=f"VIBH-{sha256_json(identity)[:20]}",
                    **identity,
                )
            )
        successors.sort(key=lambda item: item.successor_hypothesis_id)
    payload = {
        "schema_version": BOUNDARY_RUN_VERSION,
        "boundary_run_id": "pending",
        "hypothesis_id": hypothesis_id,
        "packet_sha256": binding_packet_sha256,
        "proposal_call_artifact_sha256": proposal.artifact_sha256,
        "blind_call_artifact_sha256": blind.artifact_sha256,
        "semantic_agreement": agreement,
        "agreed_relation": relation,
        "synthesis_eligible": agreement and relation == "equivalent_all",
        "successor_hypotheses": [item.model_dump(mode="json") for item in successors],
        "disposition": "agreed_boundary" if agreement else "boundary_disagreement",
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    identity = dict(payload)
    identity.pop("boundary_run_id")
    payload["boundary_run_id"] = f"VIBR-{sha256_json(identity)[:20]}"
    payload["artifact_sha256"] = sha256_json(payload)
    artifact = IdentityBoundaryRunArtifact.model_validate(payload)
    _write_immutable(output_dir / f"run.{artifact.boundary_run_id}.json", artifact)
    return artifact
