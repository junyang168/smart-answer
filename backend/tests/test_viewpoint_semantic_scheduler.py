from __future__ import annotations

import pytest

from backend.api.canonical_repository.knowledge_models import ClaimRecord
from backend.api.canonical_repository.viewpoint_semantic_scheduler import (
    SemanticBundleSchedule,
    build_semantic_bundle_schedule,
)
from backend.api.canonical_repository.viewpoint_foundation import (
    semantic_record_sha,
    sha256_json,
)
from backend.api.canonical_repository.viewpoint_recall_blocking import (
    build_viewpoint_recall_blocking,
)


def _candidate(candidate_id: str, *claim_ids: str, blockers: list[str] | None = None) -> dict:
    return {
        "identity_candidate_id": candidate_id,
        "candidate_claim_ids": sorted(claim_ids),
        "candidate_viewpoint_ids": [],
        "seed_relation_ids": [],
        "proposed_action": "defer" if blockers else "create_new",
        "coverage_snapshot_id": "CVS-1",
        "blocker_codes": sorted(blockers or []),
        "generation_fingerprint": "generation-1",
    }


def _claim(claim_id: str, *, revision: int = 1, topic: str = "TOPIC-A") -> dict:
    suffix = claim_id.removeprefix("CL-")
    return {
        "claim_id": claim_id,
        "statement": f"命题 {claim_id}",
        "claim_type": "explicit_claim",
        "topic_ids": [topic],
        "scripture_refs": ["Matt.16.18"],
        "evidence_step_ids": [f"EV-{suffix}"],
        "revision": revision,
    }


def _evidence(claim_id: str) -> dict:
    suffix = claim_id.removeprefix("CL-")
    return {
        "evidence_step_id": f"EV-{suffix}",
        "source_fragment_ids": [f"FR-{suffix}"],
        "statement": f"证据 {claim_id}",
    }


def _fragment(claim_id: str) -> dict:
    suffix = claim_id.removeprefix("CL-")
    return {
        "fragment_id": f"FR-{suffix}",
        "source_id": f"SRC-{suffix}",
        "source_sha256": f"source-sha-{suffix}",
        "verbatim_excerpt": f"逐字依据 {claim_id}",
        "anchor_state": "source_version_bound",
    }


def _schedule(candidates: list[dict], claims: list[dict], **kwargs) -> SemanticBundleSchedule:
    include_recall = kwargs.pop("include_recall", False)
    claim_ids = [row["claim_id"] for row in claims]
    manifest = {
        "schema_version": "test_claim_manifest_v1",
        "claims": [
            {
                "claim_id": claim.claim_id,
                "pinned_claim_revision": claim.revision,
                "claim_revision_sha256": semantic_record_sha(claim),
            }
            for claim in (ClaimRecord.model_validate(row) for row in claims)
        ],
    }
    manifest["manifest_sha256"] = sha256_json(manifest)
    recall = (
        build_viewpoint_recall_blocking(claim_manifest=manifest, claims=claims)
        if include_recall
        else None
    )
    return build_semantic_bundle_schedule(
        preflight_packet_sha256="preflight-sha",
        resolution_queue_sha256="queue-sha",
        claim_manifest=manifest,
        candidates=candidates,
        claims=claims,
        evidence_steps=[_evidence(value) for value in claim_ids],
        source_fragments=[_fragment(value) for value in claim_ids],
        recall_blocking=recall,
        **kwargs,
    )


def test_independent_singletons_share_transport_bundles_without_becoming_a_cluster() -> None:
    candidates = [_candidate(f"VIC-{value}", f"CL-{value}") for value in ("A", "B", "C")]
    schedule = _schedule(
        candidates,
        [_claim(f"CL-{value}") for value in ("A", "B", "C")],
        max_bundle_items=2,
        max_bundle_bytes=100_000,
    )

    assert schedule.statistics == {
        "input_candidate_count": 3,
        "scheduled_candidate_count": 3,
        "bundle_count": 2,
        "reused_candidate_count": 0,
        "exception_candidate_count": 0,
        "recall_neighbor_reference_count": 0,
    }
    assert all(item.independent_candidate_outputs_required for item in schedule.bundles)
    assert all(item.priority_lane == "singleton_discovery" for item in schedule.bundles)
    assert all(len(work.claim_ids) == 1 for work in schedule.work_items)
    SemanticBundleSchedule.model_validate(schedule.model_dump(mode="json"))


def test_recall_neighborhood_is_bound_into_semantic_input_and_reuse_key() -> None:
    claims = [
        {**_claim("CL-A"), "topic_terms": ["圣灵"]},
        {**_claim("CL-B"), "topic_terms": ["聖靈"]},
    ]
    without_recall = _schedule(
        [_candidate("VIC-A", "CL-A"), _candidate("VIC-B", "CL-B")], claims
    )
    with_recall = _schedule(
        [_candidate("VIC-A", "CL-A"), _candidate("VIC-B", "CL-B")],
        claims,
        include_recall=True,
    )

    by_candidate = {
        item.identity_candidate_id: item for item in with_recall.work_items
    }
    assert by_candidate["VIC-A"].recall_neighbor_claim_ids == ["CL-B"]
    assert by_candidate["VIC-A"].semantic_input["recall_neighborhoods"][0][
        "neighbors"
    ][0]["statement"] == "命题 CL-B"
    assert with_recall.statistics["recall_neighbor_reference_count"] == 2
    assert {
        item.reuse_key_sha256 for item in with_recall.work_items
    }.isdisjoint({item.reuse_key_sha256 for item in without_recall.work_items})


def test_blocked_candidate_is_exception_and_completed_fingerprint_is_reused() -> None:
    candidates = [
        _candidate("VIC-A", "CL-A"),
        _candidate("VIC-B", "CL-B", blockers=["reviewed_material_relation"]),
    ]
    claims = [_claim("CL-A"), _claim("CL-B")]
    first = _schedule(candidates, claims)
    reusable = next(item for item in first.work_items if item.identity_candidate_id == "VIC-A")

    second = _schedule(
        candidates,
        claims,
        completed_results_by_reuse_key={reusable.reuse_key_sha256: "result-sha"},
    )

    assert second.statistics["scheduled_candidate_count"] == 0
    assert second.statistics["reused_candidate_count"] == 1
    assert second.statistics["exception_candidate_count"] == 1
    assert second.exceptions[0].identity_candidate_id == "VIC-B"
    assert second.reused[0].result_artifact_sha256 == "result-sha"


def test_claim_revision_change_invalidates_reuse_and_duplicate_input_is_rejected() -> None:
    candidate = _candidate("VIC-A", "CL-A")
    first = _schedule([candidate], [_claim("CL-A")])
    reuse_key = first.work_items[0].reuse_key_sha256
    changed = _schedule(
        [candidate],
        [_claim("CL-A", revision=2)],
        completed_results_by_reuse_key={reuse_key: "old-result"},
    )

    assert changed.statistics["reused_candidate_count"] == 0
    assert changed.statistics["scheduled_candidate_count"] == 1

    with pytest.raises(ValueError, match="multiple candidates"):
        _schedule(
            [_candidate("VIC-A", "CL-A"), _candidate("VIC-B", "CL-A")],
            [_claim("CL-A")],
        )


def test_oversized_candidate_is_explicit_exception() -> None:
    schedule = _schedule(
        [_candidate("VIC-A", "CL-A")],
        [{**_claim("CL-A"), "statement": "很长" * 200}],
        max_bundle_bytes=100,
    )

    assert schedule.bundles == []
    assert schedule.exceptions[0].reason_code == "oversized_work_item"


def test_superseded_claim_stays_in_denominator_but_skips_semantic_call() -> None:
    schedule = _schedule(
        [_candidate("VIC-A", "CL-A")],
        [{**_claim("CL-A"), "review_status": "superseded"}],
    )

    assert schedule.statistics["input_candidate_count"] == 1
    assert schedule.statistics["scheduled_candidate_count"] == 0
    assert schedule.exceptions[0].reason_code == "source_ineligible"
    assert schedule.exceptions[0].blocker_codes == ["insufficient_source_maturity"]


def test_schedule_rejects_rebound_candidate_even_with_recomputed_outer_sha() -> None:
    schedule = _schedule([_candidate("VIC-A", "CL-A")], [_claim("CL-A")])
    tampered = schedule.model_dump(mode="json")
    tampered["bundles"][0]["candidate_ids"] = ["VIC-SUBSTITUTED"]
    tampered["input_candidate_ids"] = ["VIC-SUBSTITUTED"]
    tampered["artifact_sha256"] = sha256_json(
        {key: value for key, value in tampered.items() if key != "artifact_sha256"}
    )

    with pytest.raises(ValueError, match="candidate ids do not match"):
        SemanticBundleSchedule.model_validate(tampered)
