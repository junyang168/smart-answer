"""Deterministic transport scheduler for independent Viewpoint semantic reviews."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .knowledge_models import (
    ClaimRecord,
    EvidenceStepRecord,
    SourceFragmentRecord,
    ViewpointIdentityCandidateRecord,
    evidence_fragment_ids,
)
from .viewpoint_foundation import canonical_json, semantic_record_sha, sha256_json


SCHEDULER_VERSION = "viewpoint_semantic_bundle_scheduler_v1"
SCHEDULE_VERSION = "wang_viewpoint_semantic_bundle_schedule_v1"
TRANSPORT_VERSION = "wang_viewpoint_semantic_transport_bundle_v1"
DEFAULT_MAX_BUNDLE_ITEMS = 8
DEFAULT_MAX_BUNDLE_BYTES = 96 * 1024


class StrictScheduleArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SemanticCandidateWorkItem(StrictScheduleArtifact):
    work_item_id: str
    identity_candidate_id: str
    candidate_kind: Literal["match_existing", "duplicate_component", "singleton_discovery"]
    grouping_key: str
    claim_ids: list[str]
    source_ids: list[str]
    topic_ids: list[str]
    scripture_refs: list[str]
    candidate_artifact_sha256: str
    semantic_input: dict[str, Any]
    estimated_input_bytes: int = Field(gt=0)
    reuse_key_sha256: str

    @model_validator(mode="after")
    def validate_item(self) -> "SemanticCandidateWorkItem":
        for name in ("claim_ids", "source_ids", "topic_ids", "scripture_refs"):
            values = getattr(self, name)
            if values != sorted(set(values)):
                raise ValueError(f"{name} must be sorted and unique")
        actual_bytes = len(canonical_json(self.semantic_input).encode("utf-8"))
        if self.estimated_input_bytes != actual_bytes:
            raise ValueError("estimated_input_bytes does not match semantic input")
        if self.work_item_id != f"VSW-{self.reuse_key_sha256[:20]}":
            raise ValueError("work item id does not match reuse key")
        if self.semantic_input.get("identity_candidate_id") != self.identity_candidate_id:
            raise ValueError("semantic input candidate id mismatch")
        if self.semantic_input.get("candidate_claim_ids") != self.claim_ids:
            raise ValueError("semantic input Claim ids mismatch")
        return self


class SemanticReviewBundle(StrictScheduleArtifact):
    bundle_id: str
    priority_lane: Literal["match_existing", "duplicate_component", "singleton_discovery"]
    grouping_keys: list[str]
    work_item_ids: list[str]
    candidate_ids: list[str]
    estimated_input_bytes: int = Field(gt=0)
    independent_candidate_outputs_required: Literal[True] = True
    bundle_fingerprint_sha256: str

    @model_validator(mode="after")
    def validate_bundle(self) -> "SemanticReviewBundle":
        for name in ("grouping_keys", "work_item_ids", "candidate_ids"):
            values = getattr(self, name)
            if values != sorted(set(values)):
                raise ValueError(f"{name} must be sorted and unique")
        if len(self.work_item_ids) != len(self.candidate_ids):
            raise ValueError("bundle work items and candidates must be one-to-one")
        if self.bundle_id != f"VSB-{self.bundle_fingerprint_sha256[:20]}":
            raise ValueError("bundle id does not match fingerprint")
        return self


class SemanticScheduleException(StrictScheduleArtifact):
    identity_candidate_id: str
    claim_ids: list[str] = Field(min_length=1)
    reason_code: Literal[
        "deterministic_blocker", "source_ineligible", "oversized_work_item"
    ]
    blocker_codes: list[str] = Field(default_factory=list)
    estimated_input_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_exception(self) -> "SemanticScheduleException":
        if self.claim_ids != sorted(set(self.claim_ids)):
            raise ValueError("exception claim_ids must be sorted and unique")
        if self.blocker_codes != sorted(set(self.blocker_codes)):
            raise ValueError("exception blocker_codes must be sorted and unique")
        return self


class SemanticReuseBinding(StrictScheduleArtifact):
    identity_candidate_id: str
    reuse_key_sha256: str
    result_artifact_sha256: str


class SemanticBundleSchedule(StrictScheduleArtifact):
    schema_version: Literal["wang_viewpoint_semantic_bundle_schedule_v1"] = SCHEDULE_VERSION
    scheduler_version: Literal["viewpoint_semantic_bundle_scheduler_v1"] = SCHEDULER_VERSION
    preflight_packet_sha256: str
    resolution_queue_sha256: str
    max_bundle_items: int = Field(gt=0)
    max_bundle_bytes: int = Field(gt=0)
    input_candidate_ids: list[str]
    work_items: list[SemanticCandidateWorkItem]
    bundles: list[SemanticReviewBundle]
    reused: list[SemanticReuseBinding]
    exceptions: list[SemanticScheduleException]
    statistics: dict[str, int]
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_schedule(self) -> "SemanticBundleSchedule":
        if self.input_candidate_ids != sorted(set(self.input_candidate_ids)):
            raise ValueError("input_candidate_ids must be sorted and unique")
        scheduled = {value for bundle in self.bundles for value in bundle.candidate_ids}
        reused = {item.identity_candidate_id for item in self.reused}
        exceptions = {item.identity_candidate_id for item in self.exceptions}
        scheduled_rows = [value for bundle in self.bundles for value in bundle.candidate_ids]
        if len(scheduled_rows) != len(scheduled):
            raise ValueError("candidate appears in multiple bundles")
        if len(reused) != len(self.reused) or len(exceptions) != len(self.exceptions):
            raise ValueError("duplicate reuse or exception disposition")
        if scheduled & reused or scheduled & exceptions or reused & exceptions:
            raise ValueError("candidate appears in multiple schedule dispositions")
        if scheduled | reused | exceptions != set(self.input_candidate_ids):
            raise ValueError("schedule does not address every input candidate exactly once")
        item_index = {item.work_item_id: item for item in self.work_items}
        if len(item_index) != len(self.work_items):
            raise ValueError("duplicate work item id")
        used_work_rows = [value for bundle in self.bundles for value in bundle.work_item_ids]
        used_work_ids = set(used_work_rows)
        if len(used_work_rows) != len(used_work_ids):
            raise ValueError("work item appears in multiple bundles")
        if used_work_ids != set(item_index):
            raise ValueError("work items must appear in exactly one bundle")
        for item in self.work_items:
            expected_reuse_key = sha256_json(
                {
                    "scheduler_version": self.scheduler_version,
                    "preflight_packet_sha256": self.preflight_packet_sha256,
                    "resolution_queue_sha256": self.resolution_queue_sha256,
                    "candidate_artifact_sha256": item.candidate_artifact_sha256,
                    "semantic_input": item.semantic_input,
                }
            )
            if item.reuse_key_sha256 != expected_reuse_key:
                raise ValueError("work item reuse key mismatch")
        for bundle in self.bundles:
            items = [item_index.get(value) for value in bundle.work_item_ids]
            if any(item is None for item in items):
                raise ValueError("bundle references missing work item")
            if (
                sorted(item.identity_candidate_id for item in items if item)
                != bundle.candidate_ids
            ):
                raise ValueError("bundle candidate ids do not match work items")
            if len(items) > self.max_bundle_items:
                raise ValueError("bundle exceeds item budget")
            if (
                _bundle_input_bytes([item for item in items if item])
                != bundle.estimated_input_bytes
            ):
                raise ValueError("bundle byte total mismatch")
            if bundle.estimated_input_bytes > self.max_bundle_bytes:
                raise ValueError("bundle exceeds byte budget")
            expected_fingerprint = sha256_json(
                {
                    "scheduler_version": self.scheduler_version,
                    "max_bundle_items": self.max_bundle_items,
                    "max_bundle_bytes": self.max_bundle_bytes,
                    "work_item_ids": bundle.work_item_ids,
                    "candidate_ids": bundle.candidate_ids,
                    "independent_candidate_outputs_required": True,
                }
            )
            if bundle.bundle_fingerprint_sha256 != expected_fingerprint:
                raise ValueError("bundle fingerprint mismatch")
        expected = {
            "input_candidate_count": len(self.input_candidate_ids),
            "scheduled_candidate_count": len(scheduled),
            "bundle_count": len(self.bundles),
            "reused_candidate_count": len(reused),
            "exception_candidate_count": len(exceptions),
        }
        if self.statistics != expected:
            raise ValueError("schedule statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("schedule artifact SHA mismatch")
        return self


def _as_model(value: Mapping[str, Any] | Any, model: type[Any]) -> Any:
    return value if isinstance(value, model) else model.model_validate(value)


def _transport_input_bytes(inputs: Sequence[Mapping[str, Any]]) -> int:
    payload = {
        "schema_version": TRANSPORT_VERSION,
        "independent_candidate_outputs_required": True,
        "items": list(inputs),
    }
    return len(canonical_json(payload).encode("utf-8"))


def _bundle_input_bytes(items: Sequence[SemanticCandidateWorkItem]) -> int:
    return _transport_input_bytes([item.semantic_input for item in items])


def _claim_semantic_input(
    claim: ClaimRecord,
    evidence_index: Mapping[str, EvidenceStepRecord],
    fragment_index: Mapping[str, SourceFragmentRecord],
) -> tuple[dict[str, Any], set[str]]:
    source_ids: set[str] = set()
    evidence_rows: list[dict[str, Any]] = []
    for evidence_id in claim.evidence_step_ids:
        step = evidence_index.get(evidence_id)
        if not step:
            raise ValueError(f"{claim.claim_id}: missing EvidenceStep {evidence_id}")
        fragments: list[dict[str, Any]] = []
        fragment_ids = evidence_fragment_ids(step)
        if not fragment_ids:
            raise ValueError(f"{claim.claim_id}: EvidenceStep {evidence_id} has no fragment")
        for fragment_id in fragment_ids:
            fragment = fragment_index.get(fragment_id)
            if not fragment:
                raise ValueError(
                    f"{claim.claim_id}: EvidenceStep {evidence_id} is missing "
                    f"fragment {fragment_id}"
                )
            source_ids.add(fragment.source_id)
            fragments.append(
                {
                    "fragment_id": fragment.fragment_id,
                    "source_id": fragment.source_id,
                    "source_sha256": fragment.source_sha256,
                    "paragraph_key": fragment.paragraph_key,
                    "media_time": fragment.media_time,
                    "verbatim_excerpt": fragment.verbatim_excerpt,
                    "anchor_state": fragment.anchor_state,
                }
            )
        evidence_rows.append(
            {
                "evidence_step_id": step.evidence_step_id,
                "statement": step.statement,
                "support_eligibility": step.support_eligibility,
                "citation_ids": sorted(set(step.citation_ids)),
                "fragments": sorted(fragments, key=lambda item: item["fragment_id"]),
            }
        )
    if len(source_ids) != 1:
        raise ValueError(f"{claim.claim_id}: semantic scheduling requires source-local evidence")
    return (
        {
            "claim_id": claim.claim_id,
            "pinned_claim_revision": claim.revision,
            "claim_revision_sha256": semantic_record_sha(claim),
            "statement": claim.statement,
            "claim_type": claim.claim_type,
            "attribution": claim.attribution,
            "maturity": claim.maturity,
            "review_status": claim.review_status,
            "topic_ids": sorted(set(claim.topic_ids)),
            "scripture_refs": sorted(
                {
                    value if isinstance(value, str) else canonical_json(value)
                    for value in claim.scripture_refs
                }
            ),
            "evidence": sorted(evidence_rows, key=lambda item: item["evidence_step_id"]),
        },
        source_ids,
    )


def build_semantic_bundle_schedule(
    *,
    preflight_packet_sha256: str,
    resolution_queue_sha256: str,
    claim_manifest: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any] | ViewpointIdentityCandidateRecord],
    claims: Sequence[Mapping[str, Any] | ClaimRecord],
    evidence_steps: Sequence[Mapping[str, Any] | EvidenceStepRecord],
    source_fragments: Sequence[Mapping[str, Any] | SourceFragmentRecord],
    completed_results_by_reuse_key: Mapping[str, str] | None = None,
    max_bundle_items: int = DEFAULT_MAX_BUNDLE_ITEMS,
    max_bundle_bytes: int = DEFAULT_MAX_BUNDLE_BYTES,
) -> SemanticBundleSchedule:
    """Schedule independent candidate assessments; co-bundling never merges identity."""

    if max_bundle_items < 1 or max_bundle_bytes < 1:
        raise ValueError("bundle budgets must be positive")
    candidate_rows = sorted(
        (_as_model(value, ViewpointIdentityCandidateRecord) for value in candidates),
        key=lambda item: item.identity_candidate_id,
    )
    candidate_ids = [item.identity_candidate_id for item in candidate_rows]
    if candidate_ids != sorted(set(candidate_ids)):
        raise ValueError("input candidates must be unique")
    claim_owners: dict[str, str] = {}
    for candidate in candidate_rows:
        for claim_id in candidate.candidate_claim_ids:
            prior = claim_owners.get(claim_id)
            if prior:
                raise ValueError(
                    f"Claim {claim_id} appears in multiple candidates: "
                    f"{prior}, {candidate.identity_candidate_id}"
                )
            claim_owners[claim_id] = candidate.identity_candidate_id
    claim_index = {
        item.claim_id: item for item in (_as_model(value, ClaimRecord) for value in claims)
    }
    manifest_claims = {
        str(row["claim_id"]): row for row in claim_manifest.get("claims") or []
    }
    manifest_payload = dict(claim_manifest)
    stated_manifest_sha = str(manifest_payload.pop("manifest_sha256", ""))
    if not stated_manifest_sha or stated_manifest_sha != sha256_json(manifest_payload):
        raise ValueError("Claim manifest SHA mismatch")
    evidence_index = {
        item.evidence_step_id: item
        for item in (_as_model(value, EvidenceStepRecord) for value in evidence_steps)
    }
    fragment_index = {
        item.fragment_id: item
        for item in (_as_model(value, SourceFragmentRecord) for value in source_fragments)
    }
    completed = dict(completed_results_by_reuse_key or {})
    work_items: list[SemanticCandidateWorkItem] = []
    reused: list[SemanticReuseBinding] = []
    exceptions: list[SemanticScheduleException] = []

    for candidate in candidate_rows:
        claim_rows: list[dict[str, Any]] = []
        source_ids: set[str] = set()
        for claim_id in candidate.candidate_claim_ids:
            claim = claim_index.get(claim_id)
            if not claim:
                raise ValueError(f"{candidate.identity_candidate_id}: missing Claim {claim_id}")
            pinned = manifest_claims.get(claim_id)
            if (
                not pinned
                or int(pinned.get("pinned_claim_revision") or 0) != claim.revision
                or pinned.get("claim_revision_sha256") != semantic_record_sha(claim)
            ):
                raise ValueError(
                    f"{candidate.identity_candidate_id}: Claim {claim_id} is outside or stale "
                    "against the pinned Claim manifest"
                )
            semantic_claim, claim_sources = _claim_semantic_input(
                claim, evidence_index, fragment_index
            )
            claim_rows.append(semantic_claim)
            source_ids.update(claim_sources)
        topic_ids = sorted({value for row in claim_rows for value in row["topic_ids"]})
        scripture_refs = sorted(
            {value for row in claim_rows for value in row["scripture_refs"]}
        )
        if candidate.candidate_viewpoint_ids:
            candidate_kind = "match_existing"
        elif len(candidate.candidate_claim_ids) > 1:
            candidate_kind = "duplicate_component"
        else:
            candidate_kind = "singleton_discovery"
        grouping_key = (
            f"topic:{topic_ids[0]}"
            if topic_ids
            else f"scripture:{scripture_refs[0]}"
            if scripture_refs
            else f"source:{min(source_ids)}"
            if source_ids
            else "unscoped"
        )
        semantic_input = {
            "identity_candidate_id": candidate.identity_candidate_id,
            "candidate_kind": candidate_kind,
            "candidate_claim_ids": candidate.candidate_claim_ids,
            "candidate_viewpoint_ids": candidate.candidate_viewpoint_ids,
            "seed_relation_ids": candidate.seed_relation_ids,
            "proposed_action": candidate.proposed_action,
            "blocker_codes": candidate.blocker_codes,
            "claims": claim_rows,
        }
        candidate_sha = semantic_record_sha(candidate)
        reuse_payload = {
            "scheduler_version": SCHEDULER_VERSION,
            "preflight_packet_sha256": preflight_packet_sha256,
            "resolution_queue_sha256": resolution_queue_sha256,
            "candidate_artifact_sha256": candidate_sha,
            "semantic_input": semantic_input,
        }
        reuse_key = sha256_json(reuse_payload)
        estimated_bytes = len(canonical_json(semantic_input).encode("utf-8"))
        ineligible_claim_ids = sorted(
            row["claim_id"]
            for row in claim_rows
            if row["review_status"] in {"superseded", "rejected", "retired", "withdrawn"}
        )
        if ineligible_claim_ids:
            exceptions.append(
                SemanticScheduleException(
                    identity_candidate_id=candidate.identity_candidate_id,
                    claim_ids=candidate.candidate_claim_ids,
                    reason_code="source_ineligible",
                    blocker_codes=["insufficient_source_maturity"],
                    estimated_input_bytes=estimated_bytes,
                )
            )
            continue
        if candidate.blocker_codes:
            exceptions.append(
                SemanticScheduleException(
                    identity_candidate_id=candidate.identity_candidate_id,
                    claim_ids=candidate.candidate_claim_ids,
                    reason_code="deterministic_blocker",
                    blocker_codes=candidate.blocker_codes,
                    estimated_input_bytes=estimated_bytes,
                )
            )
            continue
        single_transport_bytes = _transport_input_bytes([semantic_input])
        if single_transport_bytes > max_bundle_bytes:
            exceptions.append(
                SemanticScheduleException(
                    identity_candidate_id=candidate.identity_candidate_id,
                    claim_ids=candidate.candidate_claim_ids,
                    reason_code="oversized_work_item",
                    estimated_input_bytes=single_transport_bytes,
                )
            )
            continue
        if reuse_key in completed:
            reused.append(
                SemanticReuseBinding(
                    identity_candidate_id=candidate.identity_candidate_id,
                    reuse_key_sha256=reuse_key,
                    result_artifact_sha256=completed[reuse_key],
                )
            )
            continue
        work_items.append(
            SemanticCandidateWorkItem(
                work_item_id=f"VSW-{reuse_key[:20]}",
                identity_candidate_id=candidate.identity_candidate_id,
                candidate_kind=candidate_kind,
                grouping_key=grouping_key,
                claim_ids=candidate.candidate_claim_ids,
                source_ids=sorted(source_ids),
                topic_ids=topic_ids,
                scripture_refs=scripture_refs,
                candidate_artifact_sha256=candidate_sha,
                semantic_input=semantic_input,
                estimated_input_bytes=estimated_bytes,
                reuse_key_sha256=reuse_key,
            )
        )

    lane_order = {"match_existing": 0, "duplicate_component": 1, "singleton_discovery": 2}
    work_items.sort(
        key=lambda item: (
            lane_order[item.candidate_kind],
            item.grouping_key,
            item.identity_candidate_id,
        )
    )
    bundle_groups: list[list[SemanticCandidateWorkItem]] = []
    for item in work_items:
        current = bundle_groups[-1] if bundle_groups else []
        if (
            not current
            or current[0].candidate_kind != item.candidate_kind
            or len(current) >= max_bundle_items
            or _bundle_input_bytes([*current, item]) > max_bundle_bytes
        ):
            current = []
            bundle_groups.append(current)
        current.append(item)

    bundles: list[SemanticReviewBundle] = []
    for items in bundle_groups:
        work_ids = sorted(item.work_item_id for item in items)
        candidate_bundle_ids = sorted(item.identity_candidate_id for item in items)
        grouping_keys = sorted({item.grouping_key for item in items})
        total_bytes = _bundle_input_bytes(items)
        fingerprint = sha256_json(
            {
                "scheduler_version": SCHEDULER_VERSION,
                "max_bundle_items": max_bundle_items,
                "max_bundle_bytes": max_bundle_bytes,
                "work_item_ids": work_ids,
                "candidate_ids": candidate_bundle_ids,
                "independent_candidate_outputs_required": True,
            }
        )
        bundles.append(
            SemanticReviewBundle(
                bundle_id=f"VSB-{fingerprint[:20]}",
                priority_lane=items[0].candidate_kind,
                grouping_keys=grouping_keys,
                work_item_ids=work_ids,
                candidate_ids=candidate_bundle_ids,
                estimated_input_bytes=total_bytes,
                bundle_fingerprint_sha256=fingerprint,
            )
        )
    bundles.sort(
        key=lambda item: (
            lane_order[item.priority_lane],
            item.grouping_keys,
            item.bundle_id,
        )
    )
    reused.sort(key=lambda item: item.identity_candidate_id)
    exceptions.sort(key=lambda item: item.identity_candidate_id)
    statistics = {
        "input_candidate_count": len(candidate_ids),
        "scheduled_candidate_count": sum(len(item.candidate_ids) for item in bundles),
        "bundle_count": len(bundles),
        "reused_candidate_count": len(reused),
        "exception_candidate_count": len(exceptions),
    }
    payload = {
        "schema_version": SCHEDULE_VERSION,
        "scheduler_version": SCHEDULER_VERSION,
        "preflight_packet_sha256": preflight_packet_sha256,
        "resolution_queue_sha256": resolution_queue_sha256,
        "max_bundle_items": max_bundle_items,
        "max_bundle_bytes": max_bundle_bytes,
        "input_candidate_ids": candidate_ids,
        "work_items": [item.model_dump(mode="json") for item in work_items],
        "bundles": [item.model_dump(mode="json") for item in bundles],
        "reused": [item.model_dump(mode="json") for item in reused],
        "exceptions": [item.model_dump(mode="json") for item in exceptions],
        "statistics": statistics,
    }
    payload["artifact_sha256"] = sha256_json(payload)
    return SemanticBundleSchedule.model_validate(payload)
