import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.api.canonical_repository.viewpoint_batch_resolution import (
    BatchResolutionError,
    CanonicalViewpointProposalResponse,
    CanonicalViewpointReconsiderationResponse,
    CanonicalViewpointReviewResponse,
    ProposedComponent,
    ProposedStructureFocal,
    ProposedViewpointRelation,
    ProposedViewpointStructure,
    ProposedSpan,
    build_batch_packet,
    build_cvp_batch_readback_receipt,
    build_route_resolution_job,
    canonicalize_review,
    coalesce_route_resolution_jobs,
    component_key,
    split_batches,
    validate_proposal,
    validate_review,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_route_queue import (
    FileRouteResolutionQueue,
)
from backend.api.canonical_repository.viewpoint_route_changeset import (
    compile_argument_route_package,
)
from backend.api.canonical_repository.viewpoint_batch_changeset import (
    CvpBatchChangeSetError,
    compile_cvp_batch_package,
)
from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
from backend.pipeline.viewpoint_batch_resolution_runner import _stable_decided_at, run_batch
from backend.pipeline.viewpoint_route_resolution import build_registry_route_packet

ROCK_STATEMENT = "磐石不是彼得这个人，而是彼得所承认的信仰"
MODAL_STATEMENT = "根基更可能是基督，而不是彼得个人"


def _evidence(
    claim_id: str,
    *,
    eligible: bool = True,
    scripture_refs: list[str] | None = None,
    source_id: str = "S1",
) -> dict[str, Any]:
    return {
        "evidence_step_id": f"{claim_id}-E1",
        "source_fragment_id": f"{claim_id}-F1",
        "source_id": source_id,
        "evidence_statement": "教授在该段落作出的推理步骤",
        "verbatim_excerpt": "逐字片段",
        "citation_id": "CIT-1",
        "citation_revision": 1,
        "citation_status": "approved" if eligible else "unresolved",
        "source_sha256": "source-sha",
        "support_eligibility": "eligible" if eligible else "eligible_candidate",
        "anchor_state": "source_version_bound",
        "valid_for_identity_review": eligible,
        "scripture_refs": scripture_refs or [],
    }


def _claim(
    claim_id: str,
    statement: str,
    *,
    eligible: bool = True,
    scripture_refs: list[str] | None = None,
    source_id: str = "S1",
) -> ReviewClaim:
    return ReviewClaim(
        claim_id=claim_id,
        pinned_claim_revision=1,
        claim_revision_sha256=f"sha-{claim_id}",
        source_id=source_id,
        statement=statement,
        review_status="approved",
        evidence=[
            _evidence(
                claim_id,
                eligible=eligible,
                scripture_refs=scripture_refs,
                source_id=source_id,
            )
        ],
    )


def _span(statement: str, text: str) -> dict[str, Any]:
    start = statement.index(text)
    return {"start_char": start, "end_char": start + len(text), "exact_text": text}


def _component(
    statement: str,
    text: str,
    disposition: str,
    claim_id: str = "C1",
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "spans": [_span(statement, text)],
        "disposition": disposition,
        "reason": "测试用理由",
        **extra,
    }
    if disposition in {
        "member_existing",
        "support_existing",
        "qualification_existing",
        "tension_existing",
        "new_viewpoint",
    }:
        payload.setdefault("evidence_step_ids", [f"{claim_id}-E1"])
        payload.setdefault("source_fragment_ids", [f"{claim_id}-F1"])
    return payload


def _candidate(local_key: str) -> dict[str, Any]:
    return {
        "local_key": local_key,
        "core_proposition": "太16:18 的磐石不指彼得本人",
        "subject": "太16:18 的磐石",
        "predicate": "指向",
        "object": "彼得本人",
        "polarity": "denied",
        "modality": "教授的释经判断",
        "scripture_scope": ["Matt.16.18"],
        "novelty_comparison": "现有 Registry 未收录该否定命题",
        "conditions": [],
        "population_scope": [],
    }


def _proposal(**overrides: Any) -> CanonicalViewpointProposalResponse:
    payload: dict[str, Any] = {
        "batch_id": "CVB-test-001",
        "claim_decisions": [
            {
                "claim_id": "C1",
                "components": [
                    _component(ROCK_STATEMENT, "磐石不是彼得这个人", "new_viewpoint", local_new_viewpoint_key="ROCK-NOT-PETER"),
                    _component(ROCK_STATEMENT, "而是彼得所承认的信仰", "support_existing", target_viewpoint_revision_id="CVR-1"),
                ],
            }
        ],
        "new_viewpoint_candidates": [_candidate("ROCK-NOT-PETER")],
    }
    payload.update(overrides)
    return CanonicalViewpointProposalResponse.model_validate(payload)


def test_split_batches_defaults_to_twenty_and_stays_ordered():
    claim_ids = [f"C{index:03d}" for index in range(45)]
    batches = split_batches(claim_ids)
    assert [len(batch) for batch in batches] == [20, 20, 5]
    assert [claim for batch in batches for claim in batch] == sorted(claim_ids)


def test_batch_decision_time_is_frozen_across_resume(tmp_path: Path):
    first = _stable_decided_at(tmp_path)
    second = _stable_decided_at(tmp_path)

    assert first == second
    artifact = json.loads((tmp_path / "decision-time.json").read_text())
    assert artifact["decided_at"] == first


def test_route_job_requires_exact_cvp_authority_readback():
    with pytest.raises(BatchResolutionError, match="observed CVR-old"):
        build_cvp_batch_readback_receipt(
            scope_label="matthew-16",
            scope_manifest_sha256="scope-sha",
            triggering_cvp_batch_id="CVB-matthew-16-001",
            cvp_changeset_id="KCS-1",
            cvp_changeset_sha256="changeset-sha",
            expected_current_revisions={"CV-1": "CVR-2"},
            observed_current_revisions={"CV-1": "CVR-old"},
        )


def test_verified_readback_builds_an_idempotent_route_job():
    receipt = build_cvp_batch_readback_receipt(
        scope_label="matthew-16",
        scope_manifest_sha256="scope-sha",
        triggering_cvp_batch_id="CVB-matthew-16-001",
        cvp_changeset_id="KCS-1",
        cvp_changeset_sha256="changeset-sha",
        expected_current_revisions={"CV-2": "CVR-A", "CV-1": "CVR-Z"},
        observed_current_revisions={"CV-1": "CVR-Z", "CV-2": "CVR-A"},
    )
    first = build_route_resolution_job(
        receipt=receipt,
        evidence_scope_sha256="evidence-scope-sha",
        route_policy_fingerprint_sha256="route-policy-sha",
    )
    second = build_route_resolution_job(
        receipt=receipt,
        evidence_scope_sha256="evidence-scope-sha",
        route_policy_fingerprint_sha256="route-policy-sha",
    )

    assert first == second
    assert first.logical_viewpoint_ids == ["CV-1", "CV-2"]
    # Revision ids stay positionally aligned to the sorted logical ids; they
    # must not be independently sorted or the queue would route the wrong CVP.
    assert first.enqueued_viewpoint_revision_ids == ["CVR-Z", "CVR-A"]
    assert first.cvp_readback_sha256 == receipt.artifact_sha256


def test_versioned_route_policy_fingerprint_binds_prompt_content():
    from backend.pipeline.viewpoint_route_policy import (
        DEFAULT_ROUTE_POLICY_PATH,
        load_route_policy,
        route_policy_fingerprint,
    )

    policy = load_route_policy(DEFAULT_ROUTE_POLICY_PATH)
    first = route_policy_fingerprint(policy, prompt_sha256s={"proposal": "prompt-a"})
    second = route_policy_fingerprint(policy, prompt_sha256s={"proposal": "prompt-a"})
    changed = route_policy_fingerprint(policy, prompt_sha256s={"proposal": "prompt-b"})

    assert first == second
    assert first != changed
    assert policy["review"]["targets_per_batch"] == 12


def test_route_policy_rejects_an_unimplemented_validator(tmp_path: Path):
    from backend.pipeline.viewpoint_route_policy import (
        DEFAULT_ROUTE_POLICY_PATH,
        load_route_policy,
    )

    policy = json.loads(DEFAULT_ROUTE_POLICY_PATH.read_text(encoding="utf-8"))
    policy["validator_version"] = "future-validator"
    path = tmp_path / "route-policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")

    with pytest.raises(ValueError, match="validator_version"):
        load_route_policy(path)


def test_route_apply_readback_compares_content_and_cvp_cut():
    from backend.pipeline.viewpoint_route_resolution_worker import (
        build_route_apply_readback_receipt,
        classify_route_apply_readback,
    )

    expected_record = {"argument_route_id": "AR-1", "route_status": "active"}
    verified, record_findings, cvp_findings = build_route_apply_readback_receipt(
        route_work_unit_sha256="work-sha",
        route_packet_sha256="packet-sha",
        route_proposal_sha256="proposal-sha",
        route_review_sha256="review-sha",
        changeset_fingerprint_sha256="changeset-sha",
        expected_current_viewpoint_revisions={"CV-1": "CVR-1"},
        observed_current_viewpoint_revisions={"CV-1": "CVR-1"},
        expected_records=[("argument_routes", "AR-1", expected_record)],
        observed_records={
            ("argument_routes", "AR-1"): expected_record | {"revision": 7}
        },
    )
    assert record_findings == []
    assert cvp_findings == []
    assert verified["readback_status"] == "verified"
    assert verified["approved_cvps_unchanged"] is True

    mismatch, record_findings, cvp_findings = build_route_apply_readback_receipt(
        route_work_unit_sha256="work-sha",
        route_packet_sha256="packet-sha",
        route_proposal_sha256="proposal-sha",
        route_review_sha256="review-sha",
        changeset_fingerprint_sha256="changeset-sha",
        expected_current_viewpoint_revisions={"CV-1": "CVR-1"},
        observed_current_viewpoint_revisions={"CV-1": "CVR-2"},
        expected_records=[("argument_routes", "AR-1", expected_record)],
        observed_records={
            ("argument_routes", "AR-1"): expected_record
            | {"route_status": "retired"}
        },
    )
    assert "argument_routes:AR-1" in record_findings
    assert any(item.startswith("canonical_viewpoints:CV-1") for item in cvp_findings)
    assert mismatch["readback_status"] == "mismatch"
    assert mismatch["approved_cvps_unchanged"] is False
    assert classify_route_apply_readback(
        record_mismatches=record_findings,
        cvp_cut_mismatches=cvp_findings,
    ) == "applied_with_readback_error"
    assert classify_route_apply_readback(
        record_mismatches=[],
        cvp_cut_mismatches=cvp_findings,
    ) == "applied_then_superseded"
    assert classify_route_apply_readback(
        record_mismatches=[], cvp_cut_mismatches=[]
    ) == "applied"


def test_route_queue_coalesces_to_current_revisions_and_marks_stale_jobs():
    old_receipt = build_cvp_batch_readback_receipt(
        scope_label="matthew-16",
        scope_manifest_sha256="scope-sha",
        triggering_cvp_batch_id="CVB-matthew-16-001",
        cvp_changeset_id="KCS-1",
        cvp_changeset_sha256="changeset-1-sha",
        expected_current_revisions={"CV-1": "CVR-1"},
        observed_current_revisions={"CV-1": "CVR-1"},
    )
    new_receipt = build_cvp_batch_readback_receipt(
        scope_label="matthew-16",
        scope_manifest_sha256="scope-sha",
        triggering_cvp_batch_id="CVB-matthew-16-002",
        cvp_changeset_id="KCS-2",
        cvp_changeset_sha256="changeset-2-sha",
        expected_current_revisions={"CV-1": "CVR-2", "CV-2": "CVR-3"},
        observed_current_revisions={"CV-1": "CVR-2", "CV-2": "CVR-3"},
    )
    jobs = [
        build_route_resolution_job(
            receipt=receipt,
            evidence_scope_sha256="evidence-scope-sha",
            route_policy_fingerprint_sha256="route-policy-sha",
        )
        for receipt in (old_receipt, new_receipt)
    ]

    work = coalesce_route_resolution_jobs(
        [jobs[0], jobs[0], jobs[1]],
        current_viewpoint_revisions={"CV-1": "CVR-2", "CV-2": "CVR-3"},
    )

    assert [
        (item.viewpoint_id, item.viewpoint_revision_id)
        for item in work.current_viewpoint_revisions
    ] == [("CV-1", "CVR-2"), ("CV-2", "CVR-3")]
    assert work.superseded_job_ids == [jobs[0].job_id]
    assert work.source_job_ids == sorted({item.job_id for item in jobs})
    assert work.evidence_scope_sha256 == "evidence-scope-sha"

    another_policy = build_route_resolution_job(
        receipt=new_receipt,
        evidence_scope_sha256="evidence-scope-sha",
        route_policy_fingerprint_sha256="route-policy-v2-sha",
    )
    with pytest.raises(ValueError, match="cannot cross scope manifests"):
        coalesce_route_resolution_jobs(
            [jobs[1], another_policy],
            current_viewpoint_revisions={"CV-1": "CVR-2", "CV-2": "CVR-3"},
        )


def test_file_route_queue_recovers_expired_lease_and_preserves_history(tmp_path: Path):
    old_receipt = build_cvp_batch_readback_receipt(
        scope_label="matthew-16",
        scope_manifest_sha256="scope-sha",
        triggering_cvp_batch_id="CVB-1",
        cvp_changeset_id="KCS-1",
        cvp_changeset_sha256="changeset-1",
        expected_current_revisions={"CV-1": "CVR-1"},
        observed_current_revisions={"CV-1": "CVR-1"},
    )
    new_receipt = build_cvp_batch_readback_receipt(
        scope_label="matthew-16",
        scope_manifest_sha256="scope-sha",
        triggering_cvp_batch_id="CVB-2",
        cvp_changeset_id="KCS-2",
        cvp_changeset_sha256="changeset-2",
        expected_current_revisions={"CV-1": "CVR-2", "CV-2": "CVR-3"},
        observed_current_revisions={"CV-1": "CVR-2", "CV-2": "CVR-3"},
    )
    jobs = [
        build_route_resolution_job(
            receipt=receipt,
            evidence_scope_sha256="evidence-scope-sha",
            route_policy_fingerprint_sha256="route-policy-sha",
        )
        for receipt in (old_receipt, new_receipt)
    ]
    queue = FileRouteResolutionQueue(tmp_path / "queue")
    for job in jobs:
        queue.enqueue(job, enqueued_at="2026-08-24T12:00:00+00:00")
    # Re-enqueue is idempotent and must not create another state event.
    queue.enqueue(jobs[1], enqueued_at="2026-08-24T12:01:00+00:00")
    started = datetime(2026, 8, 24, 13, 0, tzinfo=timezone.utc)
    work = queue.claim(
        worker_id="worker-a",
        current_viewpoint_revisions={"CV-1": "CVR-2", "CV-2": "CVR-3"},
        now=started,
        lease_seconds=60,
    )

    assert work is not None
    assert work.superseded_job_ids == [jobs[0].job_id]
    assert queue.claim(
        worker_id="worker-b",
        current_viewpoint_revisions={"CV-1": "CVR-2", "CV-2": "CVR-3"},
        now=started + timedelta(seconds=30),
    ) is None
    recovered = queue.claim(
        worker_id="worker-b",
        current_viewpoint_revisions={"CV-1": "CVR-2", "CV-2": "CVR-3"},
        now=started + timedelta(seconds=61),
    )
    assert recovered is not None
    with pytest.raises(ValueError, match="does not own"):
        queue.finish(recovered, worker_id="worker-a", status="resolved")
    queue.finish(recovered, worker_id="worker-b", status="resolved")

    old_state = json.loads(
        (tmp_path / "queue" / "states" / f"{jobs[0].job_id}.json").read_text()
    )
    new_state = json.loads(
        (tmp_path / "queue" / "states" / f"{jobs[1].job_id}.json").read_text()
    )
    assert old_state["status"] == "superseded"
    assert new_state["status"] == "resolved"
    assert new_state["attempt"] == 2


def test_file_route_queue_refuses_to_invent_missing_current_revision_job(tmp_path: Path):
    receipt = build_cvp_batch_readback_receipt(
        scope_label="matthew-16",
        scope_manifest_sha256="scope-sha",
        triggering_cvp_batch_id="CVB-1",
        cvp_changeset_id="KCS-1",
        cvp_changeset_sha256="changeset-1",
        expected_current_revisions={"CV-1": "CVR-old"},
        observed_current_revisions={"CV-1": "CVR-old"},
    )
    job = build_route_resolution_job(
        receipt=receipt,
        evidence_scope_sha256="evidence-scope-sha",
        route_policy_fingerprint_sha256="route-policy-sha",
    )
    queue = FileRouteResolutionQueue(tmp_path / "queue")
    queue.enqueue(job)

    with pytest.raises(ValueError, match="has no committed enqueue job"):
        queue.claim(
            worker_id="worker-a",
            current_viewpoint_revisions={"CV-1": "CVR-current"},
        )


def test_plan_only_route_worker_releases_its_lease(tmp_path: Path):
    receipt = build_cvp_batch_readback_receipt(
        scope_label="matthew-16",
        scope_manifest_sha256="scope-sha",
        triggering_cvp_batch_id="CVB-1",
        cvp_changeset_id="KCS-1",
        cvp_changeset_sha256="changeset-1",
        expected_current_revisions={"CV-1": "CVR-1"},
        observed_current_revisions={"CV-1": "CVR-1"},
    )
    job = build_route_resolution_job(
        receipt=receipt,
        evidence_scope_sha256="evidence-scope-sha",
        route_policy_fingerprint_sha256="route-policy-sha",
    )
    queue = FileRouteResolutionQueue(tmp_path / "queue")
    queue.enqueue(job)
    work = queue.claim(
        worker_id="worker-a",
        current_viewpoint_revisions={"CV-1": "CVR-1"},
    )
    assert work is not None

    queue.release(work, worker_id="worker-a", detail="plan-only")

    reclaimed = queue.claim(
        worker_id="worker-b",
        current_viewpoint_revisions={"CV-1": "CVR-1"},
    )
    assert reclaimed is not None


def test_route_queue_can_supersede_owned_work_immediately(tmp_path: Path):
    receipt = build_cvp_batch_readback_receipt(
        scope_label="matthew-16",
        scope_manifest_sha256="scope-sha",
        triggering_cvp_batch_id="CVB-1",
        cvp_changeset_id="KCS-1",
        cvp_changeset_sha256="changeset-1",
        expected_current_revisions={"CV-1": "CVR-1"},
        observed_current_revisions={"CV-1": "CVR-1"},
    )
    job = build_route_resolution_job(
        receipt=receipt,
        evidence_scope_sha256="evidence-scope-sha",
        route_policy_fingerprint_sha256="route-policy-sha",
    )
    queue = FileRouteResolutionQueue(tmp_path / "queue")
    queue.enqueue(job)
    work = queue.claim(
        worker_id="worker-a",
        current_viewpoint_revisions={"CV-1": "CVR-1"},
    )
    assert work is not None

    queue.supersede(work, worker_id="worker-a", detail="CVR-2 became current")

    state = json.loads(
        (tmp_path / "queue" / "states" / f"{job.job_id}.json").read_text()
    )
    assert state["status"] == "superseded"
    assert state["lease_expires_at"] is None


def test_route_queue_claim_filters_before_taking_ownership(tmp_path: Path):
    receipt = build_cvp_batch_readback_receipt(
        scope_label="matthew-16",
        scope_manifest_sha256="scope-sha",
        triggering_cvp_batch_id="CVB-1",
        cvp_changeset_id="KCS-1",
        cvp_changeset_sha256="changeset-1",
        expected_current_revisions={"CV-1": "CVR-1"},
        observed_current_revisions={"CV-1": "CVR-1"},
    )
    job = build_route_resolution_job(
        receipt=receipt,
        evidence_scope_sha256="packet-a",
        route_policy_fingerprint_sha256="policy-a",
    )
    queue = FileRouteResolutionQueue(tmp_path / "queue")
    queue.enqueue(job)

    assert queue.claim(
        worker_id="wrong-worker",
        current_viewpoint_revisions={"CV-1": "CVR-1"},
        scope_label="matthew-16",
        scope_manifest_sha256="scope-sha",
        evidence_scope_sha256="packet-b",
        route_policy_fingerprint_sha256="policy-a",
    ) is None
    state = json.loads(
        (tmp_path / "queue" / "states" / f"{job.job_id}.json").read_text()
    )
    assert state["status"] == "queued"


def test_route_queue_requires_an_exact_successor_before_superseding(tmp_path: Path):
    old_receipt = build_cvp_batch_readback_receipt(
        scope_label="matthew-16",
        scope_manifest_sha256="scope-sha",
        triggering_cvp_batch_id="CVB-old",
        cvp_changeset_id="KCS-old",
        cvp_changeset_sha256="changeset-old",
        expected_current_revisions={"CV-1": "CVR-1"},
        observed_current_revisions={"CV-1": "CVR-1"},
    )
    wrong_scope_receipt = build_cvp_batch_readback_receipt(
        scope_label="matthew-17",
        scope_manifest_sha256="scope-sha",
        triggering_cvp_batch_id="CVB-new",
        cvp_changeset_id="KCS-new",
        cvp_changeset_sha256="changeset-new",
        expected_current_revisions={"CV-1": "CVR-2"},
        observed_current_revisions={"CV-1": "CVR-2"},
    )
    old_job = build_route_resolution_job(
        receipt=old_receipt,
        evidence_scope_sha256="packet-a",
        route_policy_fingerprint_sha256="policy-a",
    )
    wrong_scope_job = build_route_resolution_job(
        receipt=wrong_scope_receipt,
        evidence_scope_sha256="packet-a",
        route_policy_fingerprint_sha256="policy-a",
    )
    queue = FileRouteResolutionQueue(tmp_path / "queue")
    queue.enqueue(old_job)
    work = queue.claim(
        worker_id="worker-a",
        current_viewpoint_revisions={"CV-1": "CVR-1"},
        scope_label="matthew-16",
    )
    assert work is not None
    queue.enqueue(wrong_scope_job)

    transition = queue.resolve_supersession(
        work,
        worker_id="worker-a",
        current_viewpoint_revisions={"CV-1": "CVR-2"},
        detail="CVP advanced",
    )

    assert transition["status"] == "exception"
    assert transition["missing_viewpoint_ids"] == ["CV-1"]
    state = json.loads(
        (tmp_path / "queue" / "states" / f"{old_job.job_id}.json").read_text()
    )
    assert state["status"] == "exception"


def test_route_queue_supersedes_when_exact_successor_is_durable(tmp_path: Path):
    def receipt(revision: str, suffix: str):
        return build_cvp_batch_readback_receipt(
            scope_label="matthew-16",
            scope_manifest_sha256="scope-sha",
            triggering_cvp_batch_id=f"CVB-{suffix}",
            cvp_changeset_id=f"KCS-{suffix}",
            cvp_changeset_sha256=f"changeset-{suffix}",
            expected_current_revisions={"CV-1": revision},
            observed_current_revisions={"CV-1": revision},
        )

    old_job = build_route_resolution_job(
        receipt=receipt("CVR-1", "old"),
        evidence_scope_sha256="packet-a",
        route_policy_fingerprint_sha256="policy-a",
    )
    successor_job = build_route_resolution_job(
        receipt=receipt("CVR-2", "new"),
        evidence_scope_sha256="packet-a",
        route_policy_fingerprint_sha256="policy-a",
    )
    queue = FileRouteResolutionQueue(tmp_path / "queue")
    queue.enqueue(old_job)
    work = queue.claim(
        worker_id="worker-a",
        current_viewpoint_revisions={"CV-1": "CVR-1"},
    )
    assert work is not None
    queue.enqueue(successor_job)

    transition = queue.resolve_supersession(
        work,
        worker_id="worker-a",
        current_viewpoint_revisions={"CV-1": "CVR-2"},
        detail="CVP advanced",
    )

    assert transition == {
        "status": "superseded",
        "missing_viewpoint_ids": [],
        "successor_job_ids": {"CV-1": successor_job.job_id},
    }


def test_cvp_re_review_exception_is_content_addressed_and_durable(tmp_path: Path):
    from backend.pipeline.viewpoint_route_resolution_worker import (
        _persist_cvp_re_review_exceptions,
    )

    receipt = build_cvp_batch_readback_receipt(
        scope_label="matthew-16",
        scope_manifest_sha256="scope-sha",
        triggering_cvp_batch_id="CVB-1",
        cvp_changeset_id="KCS-1",
        cvp_changeset_sha256="changeset-1",
        expected_current_revisions={"CV-1": "CVR-1"},
        observed_current_revisions={"CV-1": "CVR-1"},
    )
    job = build_route_resolution_job(
        receipt=receipt,
        evidence_scope_sha256="packet-a",
        route_policy_fingerprint_sha256="policy-a",
    )
    work = coalesce_route_resolution_jobs(
        [job], current_viewpoint_revisions={"CV-1": "CVR-1"}
    )
    report = {
        "route_review_sha256": "review-sha",
        "cvp_re_review_exceptions": [
            {
                "viewpoint_revision_id": "CVR-1",
                "finding_code": "identity_may_be_overmerged",
                "reason": "边界需要复核",
                "triggering_target_kind": "route",
                "triggering_target_key": "ROUTE-1",
                "evidence_claim_component_keys": ["CCK-1"],
            }
        ],
    }

    artifact_sha = _persist_cvp_re_review_exceptions(
        output_dir=tmp_path, work=work, report=report
    )

    artifact_path = tmp_path / "exceptions" / f"cvp-rereview-{artifact_sha}.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["status"] == "open"
    assert artifact["exceptions"] == report["cvp_re_review_exceptions"]


def test_passing_batch_compiles_component_bound_cvp_master_records():
    proposal = CanonicalViewpointProposalResponse.model_validate(
        {
            "batch_id": "CVB-test-001",
            "claim_decisions": [
                {
                    "claim_id": "C1",
                    "components": [
                        _component(
                            ROCK_STATEMENT,
                            "磐石不是彼得这个人",
                            "new_viewpoint",
                            local_new_viewpoint_key="ROCK-NOT-PETER",
                        )
                    ],
                }
            ],
            "new_viewpoint_candidates": [_candidate("ROCK-NOT-PETER")],
        }
    )
    proposal_sha = sha256_json(proposal.model_dump(mode="json"))
    review = CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": proposal_sha,
            "change_reviews": [
                {
                    "claim_id": "C1",
                    "component_index": 0,
                    "decision": "pass",
                    "reason": "语义与证据绑定均通过",
                }
            ],
            "novelty_review": {
                "status": "pass",
                "reason": "没有遗漏的新观点",
            },
        }
    )

    package = compile_cvp_batch_package(
        proposal=proposal,
        review=review,
        deterministic_validation_sha256="validation-sha",
        scope_manifest_sha256="scope-manifest-sha",
        claims=[_claim("C1", ROCK_STATEMENT)],
        registry_context=[],
        proposal_artifact_sha256="proposal-call-sha",
        review_artifact_sha256="review-call-sha",
        proposer_model_id="gpt-5.6-sol/high",
        reviewer_model_id="claude-opus-5/high",
        decided_at="2026-08-24T12:00:00Z",
    )

    assert len(package["canonical_viewpoints"]) == 1
    assert len(package["viewpoint_revisions"]) == 1
    assert len(package["viewpoint_identity_candidates"]) == 1
    assert len(package["viewpoint_identity_decisions"]) == 1
    link = package["viewpoint_claim_links"][0]
    assert link["link_type"] == "equivalent_component"
    assert link["component_locator"]["statement_component"] == "磐石不是彼得这个人"
    assert link["component_locator"]["canonical_spans"] == [
        _span(ROCK_STATEMENT, "磐石不是彼得这个人")
    ]
    assert link["evidence_bindings"] == [
        {"evidence_step_id": "C1-E1", "source_fragment_id": "C1-F1"}
    ]
    repeated = compile_cvp_batch_package(
        proposal=proposal,
        review=review,
        deterministic_validation_sha256="validation-sha",
        scope_manifest_sha256="scope-manifest-sha",
        claims=[_claim("C1", ROCK_STATEMENT)],
        registry_context=[],
        proposal_artifact_sha256="proposal-call-sha",
        review_artifact_sha256="review-call-sha",
        proposer_model_id="gpt-5.6-sol/high",
        reviewer_model_id="claude-opus-5/high",
        decided_at="2026-08-24T12:00:00Z",
    )
    assert repeated == package


def test_proposal_component_rejects_derived_and_conflicting_fields():
    # statement_component is the concatenation of the spans, so the schema must
    # not accept it from the model at all.
    with pytest.raises(ValueError):
        ProposedComponent.model_validate(
            {
                **_component(ROCK_STATEMENT, "磐石不是彼得这个人", "new_viewpoint", local_new_viewpoint_key="K"),
                "statement_component": "磐石不是彼得这个人",
            }
        )
    with pytest.raises(ValueError, match="requires a target viewpoint revision"):
        ProposedComponent.model_validate(
            _component(ROCK_STATEMENT, "磐石不是彼得这个人", "member_existing")
        )
    with pytest.raises(ValueError, match="may not carry an identity target"):
        ProposedComponent.model_validate(
            _component(
                ROCK_STATEMENT,
                "磐石不是彼得这个人",
                "no_registry_assertion",
                target_viewpoint_revision_id="CVR-1",
            )
        )


def test_span_length_and_overlap_are_rejected_before_any_model_call():
    with pytest.raises(ValueError, match="length does not match"):
        ProposedSpan(start_char=0, end_char=5, exact_text="磐石")
    with pytest.raises(ValueError, match="overlap"):
        ProposedComponent.model_validate(
            {
                "spans": [
                    _span(ROCK_STATEMENT, "磐石不是彼得这个人"),
                    _span(ROCK_STATEMENT, "彼得这个人"),
                ],
                "disposition": "new_viewpoint",
                "local_new_viewpoint_key": "K",
                "evidence_step_ids": ["C1-E1"],
                "source_fragment_ids": ["C1-F1"],
                "reason": "测试",
            }
        )


def test_validate_proposal_accepts_a_span_bound_batch():
    report = validate_proposal(
        proposal=_proposal(),
        batch_id="CVB-test-001",
        claims=[_claim("C1", ROCK_STATEMENT)],
        registry_revision_ids=["CVR-1"],
    )
    assert report["component_count"] == 2
    assert report["disposition_counts"]["new_viewpoint"] == 1
    assert report["disposition_counts"]["support_existing"] == 1
    assert len(report["member_component_keys"]) == 1


def test_shifted_span_fails_because_exact_text_disagrees():
    # An offset drifting by one character is exactly what exact_text exists to
    # catch; offsets alone would be checked against the slice they selected.
    proposal = _proposal()
    payload = proposal.model_dump(mode="json")
    span = payload["claim_decisions"][0]["components"][0]["spans"][0]
    span["start_char"] += 1
    span["end_char"] += 1
    with pytest.raises(BatchResolutionError) as excinfo:
        validate_proposal(
            proposal=CanonicalViewpointProposalResponse.model_validate(payload),
            batch_id="CVB-test-001",
            claims=[_claim("C1", ROCK_STATEMENT)],
            registry_revision_ids=["CVR-1"],
        )
    assert any("does not match the pinned statement" in item for item in excinfo.value.findings)


def test_validate_proposal_reports_every_coverage_and_reference_failure_at_once():
    proposal = _proposal(
        claim_decisions=[
            {
                "claim_id": "C1",
                "components": [
                    _component(
                        ROCK_STATEMENT,
                        "磐石不是彼得这个人",
                        "member_existing",
                        target_viewpoint_revision_id="CVR-absent",
                    )
                ],
            },
            {
                "claim_id": "C9",
                "components": [
                    _component(ROCK_STATEMENT, "而是彼得所承认的信仰", "no_registry_assertion")
                ],
            },
        ],
        new_viewpoint_candidates=[_candidate("ORPHAN")],
    )
    with pytest.raises(BatchResolutionError) as excinfo:
        validate_proposal(
            proposal=proposal,
            batch_id="CVB-test-001",
            claims=[_claim("C1", ROCK_STATEMENT), _claim("C2", MODAL_STATEMENT)],
            registry_revision_ids=["CVR-1"],
        )
    findings = "\n".join(excinfo.value.findings)
    assert "C2: Claim has no disposition" in findings
    assert "C9: Claim is not in this batch" in findings
    assert "CVR-absent" in findings
    assert "ORPHAN: new viewpoint candidate has no member component" in findings


def test_ineligible_evidence_blocks_an_identity_disposition():
    with pytest.raises(BatchResolutionError, match="identity-eligible"):
        validate_proposal(
            proposal=_proposal(),
            batch_id="CVB-test-001",
            claims=[_claim("C1", ROCK_STATEMENT, eligible=False)],
            registry_revision_ids=["CVR-1"],
        )


def test_same_component_cannot_be_claimed_by_two_members():
    text = "磐石不是彼得这个人"
    proposal = _proposal(
        claim_decisions=[
            {
                "claim_id": "C1",
                "components": [
                    _component(ROCK_STATEMENT, text, "new_viewpoint", local_new_viewpoint_key="A"),
                ],
            }
        ],
        new_viewpoint_candidates=[_candidate("A")],
    )
    claim = _claim("C1", ROCK_STATEMENT)
    key = component_key(claim, proposal.claim_decisions[0].components[0])
    assert key == component_key(claim, proposal.claim_decisions[0].components[0])

    duplicate = proposal.model_dump(mode="json")
    duplicate["claim_decisions"][0]["components"].append(
        _component(ROCK_STATEMENT, text, "member_existing", target_viewpoint_revision_id="CVR-1")
    )
    with pytest.raises(ValueError, match="same span to two components"):
        CanonicalViewpointProposalResponse.model_validate(duplicate)


def test_review_must_answer_every_component():
    proposal = _proposal()
    proposal_sha = "proposal-sha"
    partial = CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": proposal_sha,
            "change_reviews": [
                {
                    "claim_id": "C1",
                    "component_index": 0,
                    "decision": "pass",
                    "finding_codes": [],
                    "reason": "真值条件一致",
                }
            ],
            "novelty_review": {"status": "pass", "missed_claim_ids": [], "reason": "无漏项"},
        }
    )
    with pytest.raises(BatchResolutionError, match="C1#1: no review decision"):
        validate_review(review=partial, proposal=proposal, proposal_sha256=proposal_sha)


def test_review_canonicalization_sorts_and_deduplicates_nonsemantic_codes():
    raw = {
        "proposal_sha256": "proposal-sha",
        "change_reviews": [
            {
                "claim_id": "C1",
                "component_index": 0,
                "decision": "correct",
                "finding_codes": ["wrong_role", "modality_collapsed", "wrong_role"],
                "reason": "需要修正",
                "correction": "保留模态并修正角色",
            }
        ],
        "novelty_review": {
            "status": "missed_novelty",
            "missed_claim_ids": ["C2", "C1", "C2"],
            "reason": "有漏项",
        },
    }

    canonical, changed_paths = canonicalize_review(raw)

    assert canonical["change_reviews"][0]["finding_codes"] == [
        "modality_collapsed",
        "wrong_role",
    ]
    assert canonical["novelty_review"]["missed_claim_ids"] == ["C1", "C2"]
    assert changed_paths == [
        "/change_reviews/0/finding_codes",
        "/novelty_review/missed_claim_ids",
    ]
    CanonicalViewpointReviewResponse.model_validate(canonical)


def test_review_canonicalization_sorts_cvp_rereview_evidence_keys():
    raw = {
        "cvp_re_review_exceptions": [{
            "viewpoint_revision_id": "CVR-1",
            "finding_code": "possible_false_split",
            "reason": "需要回到 CVP 阶段复核",
            "triggering_target_kind": "route",
            "triggering_target_key": "R1",
            "evidence_claim_component_keys": ["CCK-b", "CCK-a", "CCK-b"],
        }]
    }

    canonical, changed_paths = canonicalize_review(raw)

    assert canonical["cvp_re_review_exceptions"][0][
        "evidence_claim_component_keys"
    ] == ["CCK-a", "CCK-b"]
    assert changed_paths == [
        "/cvp_re_review_exceptions/0/evidence_claim_component_keys"
    ]


def test_modality_finding_routes_the_batch_to_reconsideration():
    # The blind POC collapsed "更可能是基督……而不是彼得个人" into a categorical
    # member; the reviewer catching that must stop the batch, not soften it.
    proposal = _proposal()
    review = CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": "proposal-sha",
            "change_reviews": [
                {
                    "claim_id": "C1",
                    "component_index": 0,
                    "decision": "correct",
                    "finding_codes": ["modality_collapsed"],
                    "reason": "该成分带「更可能」，不是绝对断言",
                    "correction": "改为 support_existing，目标 revision 不变",
                },
                {
                    "claim_id": "C1",
                    "component_index": 1,
                    "decision": "pass",
                    "finding_codes": [],
                    "reason": "论据关系成立",
                },
            ],
            "novelty_review": {"status": "pass", "missed_claim_ids": [], "reason": "无漏项"},
        }
    )
    report = validate_review(review=review, proposal=proposal, proposal_sha256="proposal-sha")
    assert report["outcome"] == "findings"
    assert report["reconsideration_required"] is True
    assert report["decision_counts"] == {"pass": 1, "correct": 1, "reject": 0, "defer": 0}


def test_passing_review_needs_no_reconsideration():
    proposal = _proposal()
    review = CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": "proposal-sha",
            "change_reviews": [
                {
                    "claim_id": "C1",
                    "component_index": index,
                    "decision": "pass",
                    "finding_codes": [],
                    "reason": "判断成立",
                }
                for index in (0, 1)
            ],
            "novelty_review": {"status": "pass", "missed_claim_ids": [], "reason": "无漏项"},
        }
    )
    report = validate_review(review=review, proposal=proposal, proposal_sha256="proposal-sha")
    assert report["outcome"] == "pass"
    assert report["reconsideration_required"] is False


def test_missed_novelty_blocks_even_when_every_change_passes():
    proposal = _proposal()
    review = CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": "proposal-sha",
            "change_reviews": [
                {
                    "claim_id": "C1",
                    "component_index": index,
                    "decision": "pass",
                    "finding_codes": [],
                    "reason": "判断成立",
                }
                for index in (0, 1)
            ],
            "novelty_review": {
                "status": "missed_novelty",
                "missed_claim_ids": ["C1"],
                "reason": "钥匙授予对象未被提为独立观点",
            },
        }
    )
    report = validate_review(review=review, proposal=proposal, proposal_sha256="proposal-sha")
    assert report["outcome"] == "findings"


def test_packet_tells_the_proposer_the_registry_is_open():
    packet = build_batch_packet(
        batch_id="CVB-test-001",
        scope_label="matt16-13-20",
        claims=[_claim("C1", ROCK_STATEMENT)],
        registry_context=[{"viewpoint_revision_id": "CVR-1", "core_proposition": "..."}],
    )
    assert "开放参考集" in packet["registry_completeness_warning"]
    assert packet["packet_sha256"]


class _StubAdapter:
    def __init__(
        self,
        response: dict[str, Any],
        *,
        model_id: str = "stub-model",
        backend: str = "stub_subscription",
    ) -> None:
        self._response = response
        self.calls = 0
        self.model_id = model_id
        self.backend = backend
        self.prompt_sha256 = "prompt-sha"
        self.generation_config_sha256 = "config-sha"

    def generate(self, payload: Any) -> dict[str, Any]:
        self.calls += 1
        return self._response


def test_cached_call_is_bound_to_provider_and_model(tmp_path: Path):
    from backend.pipeline.viewpoint_batch_resolution_runner import _call

    cache = tmp_path / "raw.json"
    _call(
        _StubAdapter({"ok": True}, model_id="gpt-5.6-sol", backend="codex_subscription"),
        {"input": "same"},
        cache,
    )
    with pytest.raises(ValueError, match="model_id, backend"):
        _call(
            _StubAdapter({"ok": True}, model_id="claude-opus-5", backend="claude_subscription"),
            {"input": "same"},
            cache,
        )


def test_explicit_model_roles_use_separate_subscription_providers():
    from backend.pipeline.viewpoint_batch_resolution_runner import (
        build_proposer,
        build_reviewer,
    )

    proposer = build_proposer("gpt-5.6-sol", "high", provider="codex")
    reviewer = build_reviewer("claude-opus-5", "high", provider="claude")
    assert (proposer.backend, proposer.model_id) == (
        "codex_subscription",
        "gpt-5.6-sol",
    )
    assert (reviewer.backend, reviewer.model_id) == (
        "claude_subscription",
        "claude-opus-5",
    )


def test_run_batch_writes_artifacts_measures_time_and_resumes(tmp_path: Path):
    claims = [_claim("C1", ROCK_STATEMENT)]
    proposal_payload = _proposal().model_dump(mode="json")
    review_payload = {
        "schema_version": "wang_canonical_viewpoint_review_v1",
        "proposal_sha256": sha256_json(proposal_payload),
        "change_reviews": [
            {
                "claim_id": "C1",
                "component_index": index,
                "decision": "pass",
                "finding_codes": [],
                "reason": "判断成立",
            }
            for index in (0, 1)
        ],
        "novelty_review": {"status": "pass", "missed_claim_ids": [], "reason": "无漏项"},
    }
    proposer = _StubAdapter(proposal_payload)
    reviewer = _StubAdapter(review_payload)

    kwargs: dict[str, Any] = {
        "batch_id": "CVB-test-001",
        "scope_label": "matt16-13-20",
        "claims": claims,
        "registry_context": [{"viewpoint_revision_id": "CVR-1"}],
        "pending_candidates": [],
        "output_dir": tmp_path / "batch-001",
        "proposer": proposer,
        "reviewer": reviewer,
    }
    report = run_batch(**kwargs)

    assert report["outcome"] == "pass"
    assert report["claim_count"] == 1
    assert report["component_count"] == 2
    assert report["master_data_mutations"] == 0
    assert report["apply_allowed"] is False
    assert report["measurements"]["proposal_calls_executed"] == 1
    assert report["measurements"]["review_calls_executed"] == 1
    assert report["recorded_model_executions"]["calls_recorded_total"] == 2
    for name in (
        "batch-packet.json",
        "raw-proposal.json",
        "proposal.json",
        "raw-review.json",
        "review.json",
        "batch-run.json",
        "current-state.json",
    ):
        assert (tmp_path / "batch-001" / name).exists()

    # Rerunning reuses the cached calls, which is what makes a partly finished
    # scope resumable without paying for the batches already done. The semantic
    # result is byte-identical; only the execution log grows.
    again = run_batch(**kwargs)
    assert proposer.calls == 1
    assert reviewer.calls == 1
    assert again["measurements"]["proposal_calls_executed"] == 0
    assert again["recorded_model_executions"]["calls_recorded_total"] == 2
    assert again["artifact_sha256"] == report["artifact_sha256"]
    log = json.loads((tmp_path / "batch-001" / "measurements.json").read_text(encoding="utf-8"))
    assert len(log["executions"]) == 2
    current = json.loads(
        (tmp_path / "batch-001" / "current-state.json").read_text(encoding="utf-8")
    )
    assert current["status"] == "resolved"
    assert current["authoritative_artifact"] == "batch-run.json"


def test_successful_resume_marks_legacy_exception_as_superseded(tmp_path: Path):
    claims = [_claim("C1", ROCK_STATEMENT)]
    proposal_payload = _proposal().model_dump(mode="json")
    review_payload = {
        "schema_version": "wang_canonical_viewpoint_review_v1",
        "proposal_sha256": sha256_json(proposal_payload),
        "change_reviews": [
            {
                "claim_id": "C1",
                "component_index": index,
                "decision": "pass",
                "finding_codes": [],
                "reason": "判断成立",
            }
            for index in (0, 1)
        ],
        "novelty_review": {"status": "pass", "missed_claim_ids": [], "reason": "无漏项"},
    }
    batch_dir = tmp_path / "batch-001"
    batch_dir.mkdir(parents=True)
    (batch_dir / "exception.json").write_text(
        json.dumps({"historical": True}), encoding="utf-8"
    )

    run_batch(
        batch_id="CVB-test-001",
        scope_label="matt16-13-20",
        claims=claims,
        registry_context=[{"viewpoint_revision_id": "CVR-1"}],
        pending_candidates=[],
        output_dir=batch_dir,
        proposer=_StubAdapter(proposal_payload),
        reviewer=_StubAdapter(review_payload),
    )

    current = json.loads((batch_dir / "current-state.json").read_text(encoding="utf-8"))
    assert current["status"] == "resolved"
    assert current["superseded_artifacts"] == ["exception.json"]


def test_run_batch_refuses_a_proposal_that_skips_a_claim(tmp_path: Path):
    proposer = _StubAdapter(_proposal().model_dump(mode="json"))
    reviewer = _StubAdapter({})
    with pytest.raises(BatchResolutionError, match="C2: Claim has no disposition"):
        run_batch(
            batch_id="CVB-test-001",
            scope_label="matt16-13-20",
            claims=[_claim("C1", ROCK_STATEMENT), _claim("C2", MODAL_STATEMENT)],
            registry_context=[{"viewpoint_revision_id": "CVR-1"}],
            pending_candidates=[],
            output_dir=tmp_path / "batch-001",
            proposer=proposer,
            reviewer=reviewer,
        )
    assert reviewer.calls == 0
    assert not (tmp_path / "batch-001" / "review.json").exists()


def test_scope_selects_core_claims_without_a_target_proposition():
    from backend.pipeline.viewpoint_scope_packet_runner import scope_claim_ids

    scope = {
        "claims": [
            {"claim_id": "C1", "lane": "core", "passage_unit_ids": ["16:13-18"]},
            {"claim_id": "C2", "lane": "core", "passage_unit_ids": ["16:19"]},
            {"claim_id": "C3", "lane": "source_context_candidate", "passage_unit_ids": ["16:13-18"]},
            {"claim_id": "C4", "lane": "core", "passage_unit_ids": ["16:13-18", "16:19"]},
        ]
    }
    # A unit takes every core Claim assigned to it — the selection is the
    # passage, not a viewpoint someone named in advance.
    assert scope_claim_ids(scope, ["16:13-18"]) == ["C1", "C4"]
    # Cross-unit Claims appear in both units they were assigned to.
    assert scope_claim_ids(scope, ["16:19"]) == ["C2", "C4"]
    # No unit filter means the whole core lane; the context lane never enters.
    assert scope_claim_ids(scope, []) == ["C1", "C2", "C4"]


def test_registry_context_carries_boundaries_not_member_sets():
    from backend.pipeline.viewpoint_scope_packet_runner import registry_context

    revision = {
        "viewpoint_revision_id": "CVR-1",
        "viewpoint_id": "CV-1",
        "revision_number": 1,
        "revision": 1,
        "core_proposition": "太16:18 的磐石不指彼得本人",
        "proposition_signature": {
            "subject": "太16:18 的磐石",
            "predicate": "指向",
            "object": "彼得本人",
            "polarity": "denied",
            "modality": "教授的释经判断",
        },
        "scope": {"scripture_scope": ["Matt.16.18"]},
        "provenance": {"basis_identity_decision_ids": ["VID-1"], "review_artifact_sha256": "sha"},
        "review_status": "system_approved",
        "approved_by": "system",
        "approved_at": "2026-08-23T00:00:00Z",
    }
    viewpoints = [
        {
            "viewpoint_id": "CV-1",
            "current_revision_id": "CVR-1",
            "identity_status": "active",
            "created_from_candidate_id": "VIC-1",
            "review_status": "system_approved",
            "revision": 1,
        },
        {
            "viewpoint_id": "CV-2",
            "current_revision_id": "CVR-2",
            "identity_status": "retired",
            "created_from_candidate_id": "VIC-2",
            "review_status": "system_approved",
            "revision": 1,
        },
    ]
    context = registry_context(viewpoints, [revision])
    assert [item["viewpoint_id"] for item in context] == ["CV-1"]
    assert context[0]["core_proposition"] == "太16:18 的磐石不指彼得本人"
    # Member sets are deliberately absent: the proposer compares propositions.
    assert "members" not in context[0]
    assert "claim_ids" not in context[0]


def test_evidence_lists_are_independent_sets_not_positional_pairs():
    # One EvidenceStep legitimately binds several SourceFragments, so the two
    # lists have different lengths. Zipping them would invent pairs the model
    # never stated and silently drop the rest.
    claim = ReviewClaim(
        claim_id="C1",
        pinned_claim_revision=1,
        claim_revision_sha256="sha-C1",
        source_id="S1",
        statement=ROCK_STATEMENT,
        review_status="approved",
        evidence=[
            {**_evidence("C1"), "evidence_step_id": "E1", "source_fragment_id": "F1"},
            {**_evidence("C1"), "evidence_step_id": "E1", "source_fragment_id": "F2"},
            {**_evidence("C1"), "evidence_step_id": "E2", "source_fragment_id": "F3"},
        ],
    )
    proposal = _proposal(
        claim_decisions=[
            {
                "claim_id": "C1",
                "components": [
                    _component(
                        ROCK_STATEMENT,
                        "磐石不是彼得这个人",
                        "new_viewpoint",
                        local_new_viewpoint_key="ROCK-NOT-PETER",
                        evidence_step_ids=["E1", "E2"],
                        source_fragment_ids=["F1", "F2", "F3"],
                    )
                ],
            }
        ]
    )
    report = validate_proposal(
        proposal=proposal,
        batch_id="CVB-test-001",
        claims=[claim],
        registry_revision_ids=["CVR-1"],
    )
    assert report["component_count"] == 1


def test_referenced_evidence_must_form_a_real_pair():
    claim = ReviewClaim(
        claim_id="C1",
        pinned_claim_revision=1,
        claim_revision_sha256="sha-C1",
        source_id="S1",
        statement=ROCK_STATEMENT,
        review_status="approved",
        evidence=[
            {**_evidence("C1"), "evidence_step_id": "E1", "source_fragment_id": "F1"},
            {**_evidence("C1"), "evidence_step_id": "E2", "source_fragment_id": "F2"},
        ],
    )
    # E1 and F2 both belong to the Claim, but never together.
    proposal = _proposal(
        claim_decisions=[
            {
                "claim_id": "C1",
                "components": [
                    _component(
                        ROCK_STATEMENT,
                        "磐石不是彼得这个人",
                        "new_viewpoint",
                        local_new_viewpoint_key="ROCK-NOT-PETER",
                        evidence_step_ids=["E1"],
                        source_fragment_ids=["F2"],
                    )
                ],
            }
        ]
    )
    with pytest.raises(BatchResolutionError, match="forms no real"):
        validate_proposal(
            proposal=proposal,
            batch_id="CVB-test-001",
            claims=[claim],
            registry_revision_ids=["CVR-1"],
        )


def test_canonicalization_reorders_without_touching_meaning():
    from backend.api.canonical_repository.viewpoint_batch_resolution import canonicalize_proposal

    # A real proposer emits these in narrative order. Rejecting that would
    # throw away a ten-minute call over presentation.
    raw = {
        "batch_id": "CVB-test-001",
        "claim_decisions": [
            {"claim_id": "C2", "components": [{"evidence_step_id": "x", "evidence_step_ids": ["E2", "E1"]}]},
            {"claim_id": "C1", "components": []},
        ],
        "new_viewpoint_candidates": [
            {"local_key": "Z", "scripture_scope": ["Matt.16.19", "Matt.16.18"]},
            {"local_key": "A"},
        ],
    }
    canonical, changes = canonicalize_proposal(raw)
    assert [item["claim_id"] for item in canonical["claim_decisions"]] == ["C1", "C2"]
    assert [item["local_key"] for item in canonical["new_viewpoint_candidates"]] == ["A", "Z"]
    assert canonical["claim_decisions"][1]["components"][0]["evidence_step_ids"] == ["E1", "E2"]
    assert canonical["new_viewpoint_candidates"][1]["scripture_scope"] == ["Matt.16.18", "Matt.16.19"]
    assert "/claim_decisions" in changes
    # Text fields are never touched.
    assert canonical["claim_decisions"][1]["components"][0]["evidence_step_id"] == "x"


def test_span_offsets_are_reanchored_from_one_unique_exact_quote():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        anchor_proposal_spans,
    )

    raw = {
        "claim_decisions": [
            {
                "claim_id": "C1",
                "components": [
                    {
                        "spans": [
                            {
                                "start_char": 0,
                                "end_char": 4,
                                "exact_text": "磐石不是彼得",
                            }
                        ]
                    }
                ],
            }
        ]
    }
    anchored, changes = anchor_proposal_spans(
        raw, claim_statements={"C1": "太16说磐石不是彼得本人"}
    )
    span = anchored["claim_decisions"][0]["components"][0]["spans"][0]
    assert span == {"start_char": 4, "end_char": 10, "exact_text": "磐石不是彼得"}
    assert changes == ["/claim_decisions/0/components/0/spans/0/offsets"]
    assert raw["claim_decisions"][0]["components"][0]["spans"][0]["end_char"] == 4


def test_span_reanchoring_fails_closed_when_exact_quote_is_ambiguous():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        anchor_proposal_spans,
    )

    raw = {
        "claim_decisions": [
            {
                "claim_id": "C1",
                "components": [
                    {
                        "spans": [
                            {"start_char": 1, "end_char": 2, "exact_text": "彼得"}
                        ]
                    }
                ],
            }
        ]
    }
    with pytest.raises(BatchResolutionError, match="exact_text has 2 matches"):
        anchor_proposal_spans(raw, claim_statements={"C1": "彼得不是彼得"})


def test_grouping_must_cover_every_claim_exactly_once():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        ClaimGroupingResponse,
        batches_from_groups,
        validate_grouping,
    )

    grouping = ClaimGroupingResponse.model_validate(
        {
            "scope_label": "matt16-13-18",
            "groups": [
                {"group_key": "keys_authority", "claim_ids": ["C3"], "rationale": "钥匙"},
                {"group_key": "rock_referent", "claim_ids": ["C1", "C2"], "rationale": "磐石"},
            ],
        }
    )
    report = validate_grouping(grouping=grouping, scope_label="matt16-13-18", claim_ids=["C1", "C2", "C3"])
    assert report["group_count"] == 2

    with pytest.raises(BatchResolutionError, match="C4: Claim was not assigned"):
        validate_grouping(
            grouping=grouping, scope_label="matt16-13-18", claim_ids=["C1", "C2", "C3", "C4"]
        )

    # Grouping decides composition; the size ceiling stays the program's call.
    assert batches_from_groups(grouping, batch_size=20) == [["C1", "C2"], ["C3"]]
    assert batches_from_groups(grouping, batch_size=1) == [["C1"], ["C2"], ["C3"]]


def test_a_claim_in_two_groups_is_rejected():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        ClaimGroupingResponse,
        validate_grouping,
    )

    grouping = ClaimGroupingResponse.model_validate(
        {
            "scope_label": "s",
            "groups": [
                {"group_key": "a", "claim_ids": ["C1"], "rationale": "r"},
                {"group_key": "b", "claim_ids": ["C1"], "rationale": "r"},
            ],
        }
    )
    with pytest.raises(BatchResolutionError, match="in both group"):
        validate_grouping(grouping=grouping, scope_label="s", claim_ids=["C1"])


def test_grouping_coverage_is_repaired_not_thrown_away():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        ClaimGroupingResponse,
        RESIDUAL_GROUP_KEY,
        repair_grouping,
        validate_grouping,
    )

    # A real 190-Claim grouping put one Claim in two groups. Grouping is a
    # batching plan, so one slip must not cost the whole scope's call.
    grouping = ClaimGroupingResponse.model_validate(
        {
            "scope_label": "s",
            "groups": [
                {"group_key": "a_rock", "claim_ids": ["C1", "C2"], "rationale": "r"},
                {"group_key": "b_keys", "claim_ids": ["C2", "C3", "C9"], "rationale": "r"},
            ],
        }
    )
    repaired, repairs = repair_grouping(grouping=grouping, claim_ids=["C1", "C2", "C3", "C4"])
    assignments = [claim for group in repaired.groups for claim in group.claim_ids]

    assert sorted(assignments) == ["C1", "C2", "C3", "C4"]
    assert len(assignments) == 4, "exact-once coverage after repair"
    # First group in canonical order keeps the duplicate.
    assert dict((g.group_key, g.claim_ids) for g in repaired.groups)["a_rock"] == ["C1", "C2"]
    # Unassigned Claims land in a residual batch; out-of-scope ids are dropped.
    assert dict((g.group_key, g.claim_ids) for g in repaired.groups)[RESIDUAL_GROUP_KEY] == ["C4"]
    assert any("C2" in item and "already grouped" in item for item in repairs)
    assert any("C9" in item and "not in scope" in item for item in repairs)

    # The repaired plan is what gets validated, and it passes.
    validate_grouping(grouping=repaired, scope_label="s", claim_ids=["C1", "C2", "C3", "C4"])


def _review(*decisions: str) -> CanonicalViewpointReviewResponse:
    return CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": "proposal-sha",
            "change_reviews": [
                {
                    "claim_id": "C1",
                    "component_index": index,
                    "decision": decision,
                    "finding_codes": [] if decision == "pass" else ["modality_collapsed"],
                    "reason": "理由",
                    "correction": None if decision != "correct" else "改为 support_existing",
                }
                for index, decision in enumerate(decisions)
            ],
            "novelty_review": {"status": "pass", "missed_claim_ids": [], "reason": "无漏项"},
        }
    )


def _reconsideration(
    *dispositions: str,
    component_patches: list[dict[str, Any]] | None = None,
    candidate_patches: list[dict[str, Any]] | None = None,
    relation_patches: list[dict[str, Any]] | None = None,
    structure_patches: list[dict[str, Any]] | None = None,
) -> Any:
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        CanonicalViewpointReconsiderationResponse,
    )

    return CanonicalViewpointReconsiderationResponse.model_validate(
        {
            "proposal_sha256": "proposal-sha",
            "review_sha256": "review-sha",
            "finding_dispositions": [
                {
                    "claim_id": "C1",
                    "component_index": index,
                    "disposition": disposition,
                    "reason": "理由",
                }
                for index, disposition in enumerate(dispositions)
            ],
            "component_patches": component_patches or [],
            "candidate_patches": candidate_patches or [],
            "relation_patches": relation_patches or [],
            "structure_patches": structure_patches or [],
        }
    )


def _identity_patch() -> dict[str, Any]:
    """The no-op component patch an accepted finding must still carry."""

    component = _proposal().model_dump(mode="json")["claim_decisions"][0]["components"][0]
    return {"claim_id": "C1", "component_index": 0, "replacement_components": [component]}


def _boundary_relation(**overrides: Any) -> dict[str, Any]:
    relation = {
        "source_local_key": "ROCK-NOT-PETER",
        "target_viewpoint_revision_id": "CVR-1",
        "relation_type": "specializes",
        "reason": "把两者的边界写死，未来只讲其中一边的来源才知道匹配哪一条。",
    }
    relation.update(overrides)
    return relation


def test_relation_patch_records_a_boundary_the_reviewer_asked_for():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_reconsideration_patches,
    )

    effective = apply_reconsideration_patches(
        reconsideration=_reconsideration(
            "accepted",
            component_patches=[_identity_patch()],
            relation_patches=[{"action": "upsert", "relation": _boundary_relation()}],
        ),
        proposal=_proposal(),
        review=_review("correct", "pass"),
    )

    assert [item.relation_type for item in effective.viewpoint_relations] == ["specializes"]
    assert effective.viewpoint_relations[0].source_local_key == "ROCK-NOT-PETER"


def test_relation_patch_needs_only_one_end_in_the_finding():
    """The neighbour a boundary is drawn against is not itself under review.

    Requiring both endpoints would refuse exactly the edge the finding exists
    to obtain, which is what left the proposer with nothing to do but rebut.
    """

    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_reconsideration_patches,
    )

    effective = apply_reconsideration_patches(
        reconsideration=_reconsideration(
            "accepted",
            component_patches=[_identity_patch()],
            relation_patches=[
                {
                    "action": "upsert",
                    "relation": _boundary_relation(target_viewpoint_revision_id="CVR-99"),
                }
            ],
        ),
        proposal=_proposal(),
        review=_review("correct", "pass"),
    )

    assert effective.viewpoint_relations[0].target_viewpoint_revision_id == "CVR-99"


def test_relation_patch_unreachable_from_any_finding_is_refused():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_reconsideration_patches,
    )

    with pytest.raises(BatchResolutionError, match="not reachable from an accepted finding"):
        apply_reconsideration_patches(
            reconsideration=_reconsideration(
                "accepted",
                component_patches=[_identity_patch()],
                relation_patches=[
                    {
                        "action": "upsert",
                        "relation": _boundary_relation(
                            source_local_key=None,
                            source_viewpoint_revision_id="CVR-98",
                            target_viewpoint_revision_id="CVR-99",
                        ),
                    }
                ],
            ),
            proposal=_proposal(),
            review=_review("correct", "pass"),
        )


def test_relation_patch_deletes_an_edge_left_dangling_by_a_candidate_delete():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_reconsideration_patches,
    )

    proposal = _proposal(viewpoint_relations=[_boundary_relation()])
    replacement = proposal.model_dump(mode="json")["claim_decisions"][0]["components"][0]
    replacement["disposition"] = "support_existing"
    replacement["target_viewpoint_revision_id"] = "CVR-1"
    replacement["local_new_viewpoint_key"] = None

    effective = apply_reconsideration_patches(
        reconsideration=_reconsideration(
            "accepted",
            component_patches=[
                {"claim_id": "C1", "component_index": 0, "replacement_components": [replacement]}
            ],
            candidate_patches=[{"local_key": "ROCK-NOT-PETER", "action": "delete"}],
            relation_patches=[{"action": "delete", "relation": _boundary_relation()}],
        ),
        proposal=proposal,
        review=_review("correct", "pass"),
    )

    assert effective.viewpoint_relations == []
    assert effective.new_viewpoint_candidates == []


def test_correction_may_downgrade_a_component_onto_a_batch_local_candidate():
    """The contract #215 shipped: an argument for a new viewpoint has a home.

    The reconsideration prompt described the pre-#215 rule long after the code
    changed, so the proposer rebutted this correction as schema-invalid and
    failed the batch.  Pin the behaviour the prompt has to agree with.
    """

    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_reconsideration_patches,
    )

    replacement = _proposal().model_dump(mode="json")["claim_decisions"][0]["components"][0]
    replacement["disposition"] = "support_existing"
    replacement["target_viewpoint_revision_id"] = None
    replacement["local_new_viewpoint_key"] = "ROCK-NOT-PETER"

    effective = apply_reconsideration_patches(
        reconsideration=_reconsideration(
            "accepted",
            component_patches=[
                {"claim_id": "C1", "component_index": 0, "replacement_components": [replacement]}
            ],
        ),
        proposal=_proposal(),
        review=_review("correct", "pass"),
    )

    component = effective.claim_decisions[0].components[0]
    assert component.disposition == "support_existing"
    assert component.local_new_viewpoint_key == "ROCK-NOT-PETER"


def _structure(*local_keys: str, synthesis: str = "本批共同界定权柄的范围。") -> dict[str, Any]:
    return {
        "central_synthesis": synthesis,
        "focal": [
            {"local_key": key, "structure_role": "central_claim" if index == 0 else "application"}
            for index, key in enumerate(local_keys)
        ],
        "unresolved_items": [],
        "reason": "这批材料合起来在论证什么。",
    }


def test_structure_patch_rewrites_a_centre_a_candidate_delete_would_strand():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_reconsideration_patches,
    )

    proposal = _proposal(
        new_viewpoint_candidates=[_candidate("ROCK-NOT-PETER"), _candidate("SPARE")],
        structures=[_structure("ROCK-NOT-PETER", "SPARE")],
    )
    replacement = proposal.model_dump(mode="json")["claim_decisions"][0]["components"][0]
    replacement["disposition"] = "new_viewpoint"
    replacement["local_new_viewpoint_key"] = "SPARE"

    effective = apply_reconsideration_patches(
        reconsideration=_reconsideration(
            "accepted",
            component_patches=[
                {"claim_id": "C1", "component_index": 0, "replacement_components": [replacement]}
            ],
            candidate_patches=[{"local_key": "ROCK-NOT-PETER", "action": "delete"}],
            structure_patches=[
                {
                    "structure_index": 0,
                    "action": "upsert",
                    "structure": _structure("SPARE", synthesis="只剩下的那个中心。"),
                }
            ],
        ),
        proposal=proposal,
        review=_review("correct", "pass"),
    )

    assert [item.local_key for item in effective.structures[0].focal] == ["SPARE"]
    assert effective.structures[0].central_synthesis == "只剩下的那个中心。"


def test_structure_patch_untouched_by_the_finding_is_refused():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_reconsideration_patches,
    )

    proposal = _proposal(
        new_viewpoint_candidates=[_candidate("ROCK-NOT-PETER"), _candidate("SPARE")],
        structures=[_structure("ROCK-NOT-PETER"), _structure("SPARE")],
    )
    replacement = proposal.model_dump(mode="json")["claim_decisions"][0]["components"][0]

    with pytest.raises(BatchResolutionError, match="structures#1: structure patch is not reachable"):
        apply_reconsideration_patches(
            reconsideration=_reconsideration(
                "accepted",
                component_patches=[
                    {"claim_id": "C1", "component_index": 0, "replacement_components": [replacement]}
                ],
                structure_patches=[
                    {
                        "structure_index": 1,
                        "action": "upsert",
                        "structure": _structure("SPARE", synthesis="与本次 finding 无关的中心。"),
                    }
                ],
            ),
            proposal=proposal,
            review=_review("correct", "pass"),
        )


def test_structure_delete_does_not_shift_a_later_patch_target():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_reconsideration_patches,
    )

    proposal = _proposal(
        new_viewpoint_candidates=[_candidate("ROCK-NOT-PETER")],
        structures=[
            _structure("ROCK-NOT-PETER", synthesis="第一个中心。"),
            _structure("ROCK-NOT-PETER", synthesis="第二个中心。"),
        ],
    )
    replacement = proposal.model_dump(mode="json")["claim_decisions"][0]["components"][0]

    effective = apply_reconsideration_patches(
        reconsideration=_reconsideration(
            "accepted",
            component_patches=[
                {"claim_id": "C1", "component_index": 0, "replacement_components": [replacement]}
            ],
            structure_patches=[
                {"structure_index": 0, "action": "delete"},
                {
                    "structure_index": 1,
                    "action": "upsert",
                    "structure": _structure("ROCK-NOT-PETER", synthesis="改写过的第二个中心。"),
                },
            ],
        ),
        proposal=proposal,
        review=_review("correct", "pass"),
    )

    assert [item.central_synthesis for item in effective.structures] == ["改写过的第二个中心。"]


def test_unpatched_relations_are_copied_unchanged():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_reconsideration_patches,
    )

    untouched = _boundary_relation(relation_type="entails", reason="与本次 finding 无关的边。")
    effective = apply_reconsideration_patches(
        reconsideration=_reconsideration("accepted", component_patches=[_identity_patch()]),
        proposal=_proposal(viewpoint_relations=[untouched]),
        review=_review("correct", "pass"),
    )

    assert [item.relation_type for item in effective.viewpoint_relations] == ["entails"]
    assert effective.viewpoint_relations[0].reason == "与本次 finding 无关的边。"


def test_accepted_finding_resolves_the_batch():
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_reconsideration

    proposal = _proposal()
    replacement = proposal.model_dump(mode="json")["claim_decisions"][0]["components"][0]
    replacement["disposition"] = "support_existing"
    replacement["target_viewpoint_revision_id"] = "CVR-1"
    replacement["local_new_viewpoint_key"] = None

    report = validate_reconsideration(
        reconsideration=_reconsideration(
            "accepted",
            component_patches=[{
                "claim_id": "C1", "component_index": 0,
                "replacement_components": [replacement],
            }],
            candidate_patches=[{"local_key": "ROCK-NOT-PETER", "action": "delete"}],
        ),
        proposal=proposal,
        review=_review("correct", "pass"),
        proposal_sha256="proposal-sha",
        review_sha256="review-sha",
    )
    assert report["outcome"] == "resolved"
    assert report["escalations"] == []


def _missed_novelty_review() -> CanonicalViewpointReviewResponse:
    return CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": "proposal-sha",
            "change_reviews": [
                {
                    "claim_id": "C1",
                    "component_index": 0,
                    "decision": "correct",
                    "finding_codes": ["missed_novelty"],
                    "reason": "该成分是独立新观点",
                    "correction": "改为 new_viewpoint 并新增候选",
                },
                {
                    "claim_id": "C1",
                    "component_index": 1,
                    "decision": "pass",
                    "finding_codes": [],
                    "reason": "判断成立",
                },
            ],
            "novelty_review": {
                "status": "missed_novelty",
                "missed_claim_ids": ["C1"],
                "reason": "漏掉一个新观点",
            },
        }
    )


def test_accepted_novelty_correction_resolves_when_revised_claim_creates_viewpoint():
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_reconsideration

    revised = _proposal()
    original_payload = revised.model_dump(mode="json")
    original_payload["claim_decisions"][0]["components"][0]["disposition"] = "support_existing"
    original_payload["claim_decisions"][0]["components"][0]["target_viewpoint_revision_id"] = "CVR-1"
    original_payload["claim_decisions"][0]["components"][0]["local_new_viewpoint_key"] = None
    original_payload["new_viewpoint_candidates"] = []
    original = CanonicalViewpointProposalResponse.model_validate(original_payload)

    report = validate_reconsideration(
        reconsideration=_reconsideration(
            "accepted",
            component_patches=[{
                "claim_id": "C1", "component_index": 0,
                "replacement_components": [
                    revised.model_dump(mode="json")["claim_decisions"][0]["components"][0]
                ],
            }],
            candidate_patches=[{
                "local_key": "ROCK-NOT-PETER", "action": "upsert",
                "candidate": revised.model_dump(mode="json")["new_viewpoint_candidates"][0],
            }],
        ),
        proposal=original,
        review=_missed_novelty_review(),
        proposal_sha256="proposal-sha",
        review_sha256="review-sha",
    )
    assert report["outcome"] == "resolved"
    assert report["resolved_novelty_claim_ids"] == ["C1"]
    assert report["unresolved_novelty_claim_ids"] == []


def test_accepted_novelty_correction_must_actually_create_a_viewpoint():
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_reconsideration

    proposal = _proposal()
    payload = proposal.model_dump(mode="json")
    payload["claim_decisions"][0]["components"][0]["disposition"] = "support_existing"
    payload["claim_decisions"][0]["components"][0]["target_viewpoint_revision_id"] = "CVR-1"
    payload["claim_decisions"][0]["components"][0]["local_new_viewpoint_key"] = None
    payload["new_viewpoint_candidates"] = []
    original = CanonicalViewpointProposalResponse.model_validate(payload)

    with pytest.raises(BatchResolutionError, match="produced no new_viewpoint"):
        validate_reconsideration(
            reconsideration=_reconsideration(
                "accepted",
                component_patches=[{
                    "claim_id": "C1", "component_index": 0,
                    "replacement_components": [
                        original.model_dump(mode="json")["claim_decisions"][0]["components"][0]
                    ],
                }],
            ),
            proposal=original,
            review=_missed_novelty_review(),
            proposal_sha256="proposal-sha",
            review_sha256="review-sha",
        )


def test_rebutted_finding_goes_to_a_human_not_another_round():
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_reconsideration

    proposal = _proposal()
    report = validate_reconsideration(
        reconsideration=_reconsideration("rebutted"),
        proposal=proposal,
        review=_review("correct", "pass"),
        proposal_sha256="proposal-sha",
        review_sha256="review-sha",
    )
    assert report["outcome"] == "exception"
    assert report["escalations"] == ["C1#0:rebutted"]


def test_reconsideration_cannot_patch_a_component_the_reviewer_passed():
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_reconsideration

    proposal = _proposal()
    passed = proposal.model_dump(mode="json")["claim_decisions"][0]["components"][1]
    passed["reason"] = "偷偷改掉的理由"

    with pytest.raises(BatchResolutionError, match="patch is not an accepted finding"):
        validate_reconsideration(
            reconsideration=_reconsideration(
                "accepted",
                component_patches=[{
                    "claim_id": "C1", "component_index": 1,
                    "replacement_components": [passed],
                }],
            ),
            proposal=proposal,
            review=_review("correct", "pass"),
            proposal_sha256="proposal-sha",
            review_sha256="review-sha",
        )


def test_candidate_patch_cannot_change_a_candidate_used_by_an_unflagged_component():
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_reconsideration

    proposal_payload = _proposal().model_dump(mode="json")
    proposal_payload["claim_decisions"][0]["components"][1] = json.loads(
        json.dumps(proposal_payload["claim_decisions"][0]["components"][0])
    )
    proposal_payload["claim_decisions"][0]["components"][1]["spans"] = [
        _component(
            ROCK_STATEMENT,
            "而是彼得所承认的信仰",
            "new_viewpoint",
            local_new_viewpoint_key="ROCK-NOT-PETER",
        )["spans"][0]
    ]
    proposal = CanonicalViewpointProposalResponse.model_validate(proposal_payload)
    changed_candidate = proposal.new_viewpoint_candidates[0].model_dump(mode="json")
    changed_candidate["core_proposition"] = "偷偷影响 passed component"
    component = proposal.claim_decisions[0].components[0].model_dump(mode="json")

    with pytest.raises(BatchResolutionError, match="unflagged referrers C1#1"):
        validate_reconsideration(
            reconsideration=_reconsideration(
                "accepted",
                component_patches=[{
                    "claim_id": "C1", "component_index": 0,
                    "replacement_components": [component],
                }],
                candidate_patches=[{
                    "local_key": "ROCK-NOT-PETER", "action": "upsert",
                    "candidate": changed_candidate,
                }],
            ),
            proposal=proposal,
            review=_review("correct", "pass"),
            proposal_sha256="proposal-sha",
            review_sha256="review-sha",
        )


def test_reconsideration_can_merge_components_the_reviewer_flagged():
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_reconsideration

    proposal = _proposal()
    first = proposal.model_dump(mode="json")["claim_decisions"][0]["components"][0]

    report = validate_reconsideration(
        reconsideration=_reconsideration(
            "accepted", "accepted",
            component_patches=[
                {"claim_id": "C1", "component_index": 0, "replacement_components": [first]},
                {"claim_id": "C1", "component_index": 1, "replacement_components": []},
            ],
        ),
        proposal=proposal,
        review=_review("correct", "correct"),
        proposal_sha256="proposal-sha",
        review_sha256="review-sha",
    )
    assert report["outcome"] == "resolved"


def test_reconsideration_can_merge_flagged_span_into_unchanged_passed_component():
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_reconsideration

    proposal = _proposal()
    reconsideration_payload = _reconsideration(
        "accepted",
        component_patches=[{
            "claim_id": "C1", "component_index": 1,
            "merge_into_component_index": 0,
        }],
    ).model_dump(mode="json")
    reconsideration_payload["finding_dispositions"][0]["component_index"] = 1
    from backend.api.canonical_repository.viewpoint_batch_resolution import CanonicalViewpointReconsiderationResponse

    report = validate_reconsideration(
        reconsideration=CanonicalViewpointReconsiderationResponse.model_validate(
            reconsideration_payload
        ),
        proposal=proposal,
        review=_review("pass", "correct"),
        proposal_sha256="proposal-sha",
        review_sha256="review-sha",
    )

    assert report["outcome"] == "resolved"


def test_patch_merge_preserves_passed_component_metadata_byte_for_byte():
    from backend.api.canonical_repository.viewpoint_batch_resolution import apply_reconsideration_patches

    proposal = _proposal()
    reconsideration = _reconsideration(
        "accepted",
        component_patches=[{
            "claim_id": "C1", "component_index": 1,
            "merge_into_component_index": 0,
        }],
    ).model_copy(update={
        "finding_dispositions": [
            _reconsideration("accepted").finding_dispositions[0].model_copy(
                update={"component_index": 1}
            )
        ]
    })
    effective = apply_reconsideration_patches(
        reconsideration=reconsideration,
        proposal=proposal,
        review=_review("pass", "correct"),
    )
    before = proposal.claim_decisions[0].components[0].model_dump(mode="json")
    after = effective.claim_decisions[0].components[0].model_dump(mode="json")
    assert {k: v for k, v in after.items() if k != "spans"} == {
        k: v for k, v in before.items() if k != "spans"
    }


def test_patch_merge_can_update_the_candidate_owned_by_its_merge_target():
    from backend.api.canonical_repository.viewpoint_batch_resolution import apply_reconsideration_patches

    proposal = _proposal()
    changed_candidate = proposal.new_viewpoint_candidates[0].model_dump(mode="json")
    changed_candidate["core_proposition"] = "合并 span 后的完整观点"
    # Make component 0 the flagged member and component 1 the candidate-owning
    # merge target, mirroring the real CHRIST-MORE-LIKELY-ROCK correction.
    payload = proposal.model_dump(mode="json")
    payload["claim_decisions"][0]["components"].reverse()
    proposal = CanonicalViewpointProposalResponse.model_validate(payload)
    reconsideration = _reconsideration(
        "accepted",
        component_patches=[{
            "claim_id": "C1", "component_index": 0,
            "merge_into_component_index": 1,
        }],
        candidate_patches=[{
            "local_key": "ROCK-NOT-PETER", "action": "upsert",
            "candidate": changed_candidate,
        }],
    )
    effective = apply_reconsideration_patches(
        reconsideration=reconsideration,
        proposal=proposal,
        review=_review("correct", "pass"),
    )
    assert effective.new_viewpoint_candidates[0].core_proposition == "合并 span 后的完整观点"


def test_reconsideration_preserves_an_unflagged_component_after_index_shift():
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_reconsideration

    proposal = _proposal()
    report = validate_reconsideration(
        reconsideration=_reconsideration(
            "accepted",
            component_patches=[{
                "claim_id": "C1", "component_index": 0,
                "replacement_components": [],
            }],
        ),
        proposal=proposal,
        review=_review("correct", "pass"),
        proposal_sha256="proposal-sha",
        review_sha256="review-sha",
    )
    assert report["outcome"] == "resolved"


def test_every_finding_needs_a_disposition():
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_reconsideration

    proposal = _proposal()
    with pytest.raises(BatchResolutionError, match="C1#1: finding has no disposition"):
        validate_reconsideration(
            reconsideration=_reconsideration(
                "accepted",
                component_patches=[{
                    "claim_id": "C1", "component_index": 0,
                    "replacement_components": [
                        proposal.model_dump(mode="json")["claim_decisions"][0]["components"][0]
                    ],
                }],
            ),
            proposal=proposal,
            review=_review("correct", "correct"),
            proposal_sha256="proposal-sha",
            review_sha256="review-sha",
        )


def test_derived_summaries_survive_a_report_shape_change(tmp_path: Path):
    # batch-run.json is wholly derived from the immutable artifacts. Freezing it
    # meant that adding reconsideration fields to the report blocked a rerun of
    # a batch whose model calls were all cached.
    claims = [_claim("C1", ROCK_STATEMENT)]
    proposal_payload = _proposal().model_dump(mode="json")
    review_payload = {
        "schema_version": "wang_canonical_viewpoint_review_v1",
        "proposal_sha256": sha256_json(proposal_payload),
        "change_reviews": [
            {
                "claim_id": "C1",
                "component_index": index,
                "decision": "pass",
                "finding_codes": [],
                "reason": "判断成立",
            }
            for index in (0, 1)
        ],
        "novelty_review": {"status": "pass", "missed_claim_ids": [], "reason": "无漏项"},
    }
    kwargs: dict[str, Any] = {
        "batch_id": "CVB-test-001",
        "scope_label": "matt16-13-18",
        "claims": claims,
        "registry_context": [{"viewpoint_revision_id": "CVR-1"}],
        "pending_candidates": [],
        "output_dir": tmp_path / "batch-001",
        "proposer": _StubAdapter(proposal_payload),
        "reviewer": _StubAdapter(review_payload),
    }
    run_batch(**kwargs)

    # Simulate the report shape changing under a completed batch.
    path = tmp_path / "batch-001" / "batch-run.json"
    stale = json.loads(path.read_text(encoding="utf-8"))
    stale.pop("escalations", None)
    stale["removed_field"] = "from an older runner"
    path.write_text(json.dumps(stale, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    again = run_batch(**kwargs)
    assert again["outcome"] == "pass"
    assert "removed_field" not in json.loads(path.read_text(encoding="utf-8"))

    # The semantic artifacts stay immutable — those are the real record.
    proposal_path = tmp_path / "batch-001" / "raw-proposal.json"
    tampered = json.loads(proposal_path.read_text(encoding="utf-8"))
    tampered["response"]["batch_id"] = "CVB-somewhere-else"
    proposal_path.write_text(json.dumps(tampered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cached response SHA mismatch"):
        run_batch(**kwargs)


def test_batch_report_counts_the_effective_revised_proposal(tmp_path: Path):
    proposal_payload = _proposal().model_dump(mode="json")
    proposal_sha = sha256_json(proposal_payload)
    review_payload = {
        "schema_version": "wang_canonical_viewpoint_review_v1",
        "proposal_sha256": proposal_sha,
        "change_reviews": [
            {
                "claim_id": "C1",
                "component_index": index,
                "decision": "correct",
                "finding_codes": ["merge_components"],
                "reason": "两个成分需要合并",
                "correction": "合并为一个成分",
            }
            for index in (0, 1)
        ],
        "novelty_review": {"status": "pass", "missed_claim_ids": [], "reason": "无漏项"},
        "revision_reviews": [],
    }
    reconsideration_payload = {
        "schema_version": "wang_canonical_viewpoint_reconsideration_v3",
        "proposal_sha256": proposal_sha,
        "review_sha256": sha256_json(review_payload),
        "finding_dispositions": [
            {
                "claim_id": "C1",
                "component_index": index,
                "disposition": "accepted",
                "reason": "已按要求合并",
            }
            for index in (0, 1)
        ],
        "component_patches": [
            {
                "claim_id": "C1",
                "component_index": 0,
                "replacement_components": [
                    proposal_payload["claim_decisions"][0]["components"][0]
                ],
            },
            {
                "claim_id": "C1",
                "component_index": 1,
                "replacement_components": [],
            },
        ],
        "candidate_patches": [],
    }

    report = run_batch(
        batch_id="CVB-test-001",
        scope_label="matt16-13-18",
        claims=[_claim("C1", ROCK_STATEMENT)],
        registry_context=[{"viewpoint_revision_id": "CVR-1"}],
        pending_candidates=[],
        output_dir=tmp_path / "batch-001",
        proposer=_StubAdapter(proposal_payload),
        reviewer=_StubAdapter(review_payload),
        reconsiderer=_StubAdapter(reconsideration_payload),
    )
    assert report["component_count"] == 1
    assert report["disposition_counts"]["new_viewpoint"] == 1
    assert report["reconsideration_outcome"] == "resolved"


def _route(**overrides: Any) -> dict[str, Any]:
    return {
        "local_route_key": "ROUTE-GREEK",
        "conclusion_ref": {"target_viewpoint_revision_id": "CVR-1"},
        "proposed_action": "create_new",
        "route_label": "以 Petrus／petra 的性别差异论证磐石不指彼得本人",
        "inference_method_codes": ["morphology"],
        "ordered_inference_nodes": [
            {
                "route_step_key": "P1",
                "role": "observation",
                "normalized_proposition": "Petrus 是阳性、petra 是阴性",
                "required_for_full_attestation": True,
            },
            {
                "route_step_key": "C1",
                "role": "conclusion",
                "conclusion_ref": {"target_viewpoint_revision_id": "CVR-1"},
                "required_for_full_attestation": True,
            },
        ],
        "identity_comparison": "Registry 中无同一骨架的路线",
        **overrides,
    }


def _attestation(**overrides: Any) -> dict[str, Any]:
    binding = _route_component_binding()
    return {
        "local_attestation_key": "ATTEST-1",
        "route_ref": {"local_route_key": "ROUTE-GREEK"},
        "source_id": "S1",
        "source_revision_sha256": "source-sha",
        "claim_ids": ["C1"],
        "step_bindings": [
            {
                "route_step_key": "P1",
                "claim_component_keys": [binding.claim_component_key],
                "evidence_step_ids": ["C1-E1"],
                "source_fragment_ids": ["C1-F1"],
                "attestation_status": "attested",
            },
            {
                "route_step_key": "C1",
                "claim_component_keys": [binding.claim_component_key],
                "evidence_step_ids": ["C1-E1"],
                "source_fragment_ids": ["C1-F1"],
                "attestation_status": "attested",
            },
        ],
        "terminal_claim_component_key": binding.claim_component_key,
        "completeness": "full",
        "reason": "本篇给了前提也讲出结论",
        **overrides,
    }


def _routes(**overrides: Any) -> Any:
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        ArgumentRouteProposalResponse,
    )

    payload: dict[str, Any] = {
        "scope_label": "matt16-13-18",
        "approved_viewpoint_revision_ids": ["CVR-1"],
        "argument_route_candidates": [_route()],
        "source_route_attestations": [_attestation()],
        "viewpoints_with_no_route": [],
    }
    payload.update(overrides)
    return ArgumentRouteProposalResponse.model_validate(payload)


def _check_routes(routes: Any, claims: list[ReviewClaim]) -> dict[str, Any]:
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        validate_route_proposal,
    )

    return validate_route_proposal(
        routes=routes,
        scope_label="matt16-13-18",
        claims=claims,
        approved_viewpoint_revision_ids=["CVR-1"],
        known_route_revision_ids=[],
        component_bindings=[_route_component_binding()],
    )


def _route_component_binding() -> Any:
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        RouteComponentBinding,
    )

    claim = _claim("C1", ROCK_STATEMENT)
    component = ProposedComponent.model_validate(
        _component(
            ROCK_STATEMENT,
            "磐石不是彼得这个人",
            "member_existing",
            target_viewpoint_revision_id="CVR-1",
        )
    )
    return RouteComponentBinding(
        claim_component_key=component_key(claim, component),
        claim_id="C1",
        source_id="S1",
        disposition="member_existing",
        target_viewpoint_revision_id="CVR-1",
        statement_component=component.statement_component(),
        spans=component.spans,
        evidence_step_ids=component.evidence_step_ids,
        source_fragment_ids=component.source_fragment_ids,
    )


def _approved_viewpoint() -> dict[str, Any]:
    return {
        "viewpoint_id": "CV-1",
        "viewpoint_revision_id": "CVR-1",
        "core_proposition": "太16:18 的磐石不指彼得本人",
        "proposition_signature": {
            "subject": "太16:18 的磐石",
            "predicate": "指向",
            "object": "彼得本人",
            "polarity": "denied",
            "modality": "asserted",
            "conditions": [],
            "population_scope": [],
        },
        "scope": {"scripture_scope": ["Matt.16.18"]},
        "review_status": "system_approved",
    }


def _registry_link(claim: ReviewClaim, **overrides: Any) -> dict[str, Any]:
    span = _span(claim.statement, "磐石不是彼得这个人")
    payload = {
        "viewpoint_claim_link_id": "VCL-1",
        "viewpoint_id": "CV-1",
        "validated_against_viewpoint_revision_id": "CVR-1",
        "claim_id": claim.claim_id,
        "pinned_claim_revision": claim.pinned_claim_revision,
        "link_type": "equivalent_component",
        "component_locator": {
            "statement_component": span["exact_text"],
            "claim_sha256": claim.claim_revision_sha256,
            "canonical_spans": [span],
        },
        "evidence_bindings": [
            {"evidence_step_id": "C1-E1", "source_fragment_id": "C1-F1"}
        ],
        "occurrence_refs": ["OCC-1"],
        "effective_state": "active",
    }
    payload.update(overrides)
    return payload


def test_registry_route_packet_rebuilds_components_without_cvp_proposal():
    claim = _claim("C1", ROCK_STATEMENT)
    packet = build_registry_route_packet(
        scope_label="matt16-13-18",
        approved_viewpoints=[_approved_viewpoint()],
        claims=[claim],
        viewpoint_claim_links=[_registry_link(claim)],
        existing_routes=[],
    )

    assert packet["schema_version"] == "wang_argument_route_scope_packet_v2"
    assert packet["registry_handoff"] is True
    member = next(
        item for item in packet["claim_components"]
        if item["disposition"] == "member_existing"
    )
    assert member["statement_component"] == "磐石不是彼得这个人"
    assert member["target_viewpoint_revision_id"] == "CVR-1"
    assert member["evidence_step_ids"] == ["C1-E1"]
    assert member["source_fragment_ids"] == ["C1-F1"]


def test_registry_route_packet_keeps_unlinked_bridge_claim():
    member = _claim("C1", ROCK_STATEMENT)
    bridge = _claim("C2", MODAL_STATEMENT)
    packet = build_registry_route_packet(
        scope_label="matt16-13-18",
        approved_viewpoints=[_approved_viewpoint()],
        claims=[member, bridge],
        viewpoint_claim_links=[_registry_link(member)],
        existing_routes=[],
    )

    background = [
        item for item in packet["claim_components"]
        if item["claim_id"] == "C2"
    ]
    assert len(background) == 1
    assert background[0]["disposition"] == "no_registry_assertion"
    assert background[0]["statement_component"] == MODAL_STATEMENT


def test_registry_route_packet_accounts_for_members_it_cannot_attest():
    member = _claim("C1", ROCK_STATEMENT)
    external = _claim("C9", ROCK_STATEMENT)
    packet = build_registry_route_packet(
        scope_label="matt16-13-18",
        approved_viewpoints=[_approved_viewpoint()],
        claims=[member],
        viewpoint_claim_links=[
            _registry_link(member),
            _registry_link(
                external,
                viewpoint_claim_link_id="VCL-OUTSIDE",
            ),
            _registry_link(
                member,
                viewpoint_claim_link_id="VCL-NO-BINDINGS",
                evidence_bindings=[],
            ),
        ],
        existing_routes=[],
    )

    ledger = packet["membership_ledger"]
    assert [item["claim_id"] for item in ledger["out_of_scope_members"]] == ["C9"]
    assert [
        item["viewpoint_claim_link_id"]
        for item in ledger["unattestable_in_scope_members"]
    ] == ["VCL-NO-BINDINGS"]
    assert ledger["no_route_semantics"] == "no_attested_route_in_this_evidence_scope"


def test_no_route_reviewer_receives_the_membership_ledger():
    from backend.pipeline.viewpoint_route_resolution import build_route_review_batches

    member = _claim("C1", ROCK_STATEMENT)
    external = _claim("C9", ROCK_STATEMENT)
    packet = build_registry_route_packet(
        scope_label="matt16-13-18",
        approved_viewpoints=[_approved_viewpoint()],
        claims=[member],
        viewpoint_claim_links=[
            _registry_link(member),
            _registry_link(
                external,
                viewpoint_claim_link_id="VCL-OUTSIDE",
            ),
        ],
        existing_routes=[],
    )
    proposal = _routes(
        argument_route_candidates=[],
        source_route_attestations=[],
        viewpoints_with_no_route=[
            {
                "viewpoint_revision_id": "CVR-1",
                "reason_code": "evidence_insufficient",
                "reason": "本 scope 不能完整重建论证",
            }
        ],
    )

    batches = build_route_review_batches(
        proposal=proposal,
        route_packet=packet,
        route_proposal_sha256="proposal-sha",
    )

    ledger = batches[0]["route_evidence_context"]["membership_ledger"]
    assert [item["claim_id"] for item in ledger["out_of_scope_members"]] == ["C9"]
    assert ledger["no_route_semantics"] == "no_attested_route_in_this_evidence_scope"


def test_route_review_can_open_a_structured_cvp_re_review_exception():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        ArgumentRouteReviewResponse,
        validate_route_review,
    )

    binding = _route_component_binding()
    review = ArgumentRouteReviewResponse.model_validate(
        {
            "route_proposal_sha256": "proposal-sha",
            "route_evidence_packet_sha256": "packet-sha",
            "change_reviews": [
                {
                    "target_kind": "route",
                    "target_key": "ROUTE-GREEK",
                    "decision": "defer",
                    "finding_codes": ["cvp_identity_may_be_overmerged"],
                    "reason": "结论边界似乎合并了两个不同判断",
                },
                {
                    "target_kind": "attestation",
                    "target_key": "ATTEST-1",
                    "decision": "pass",
                    "finding_codes": [],
                    "reason": "来源绑定本身成立",
                },
            ],
            "cvp_re_review_exceptions": [
                {
                    "viewpoint_revision_id": "CVR-1",
                    "finding_code": "identity_may_be_overmerged",
                    "reason": "两组证据支持不同强度的结论",
                    "triggering_target_kind": "route",
                    "triggering_target_key": "ROUTE-GREEK",
                    "evidence_claim_component_keys": [binding.claim_component_key],
                }
            ],
            "cross_source_composition_found": False,
            "reason": "Route 不改写 CVP，只升级复核",
        }
    )

    report = validate_route_review(
        review=review,
        proposal=_routes(),
        route_proposal_sha256="proposal-sha",
        route_evidence_packet_sha256="packet-sha",
        allowed_claim_component_keys={binding.claim_component_key},
    )
    assert report["outcome"] == "findings"


def test_deterministic_route_target_isolation_removes_orphan_route():
    from backend.pipeline.viewpoint_route_resolution import (
        isolate_deterministically_invalid_route_targets,
    )

    proposal = _routes()
    filtered, exceptions = isolate_deterministically_invalid_route_targets(
        proposal,
        ["attestation ATTEST-1: terminal Claim component has no positive Registry link"],
    )

    assert filtered.argument_route_candidates == []
    assert filtered.source_route_attestations == []
    # Dropping the route leaves its conclusion with no coverage. Coverage
    # validation refuses that, so isolation has to record the loss or the scope
    # dead-ends on a viewpoint whose only route happened to be the invalid one.
    assert [
        (item.viewpoint_revision_id, item.reason_code)
        for item in filtered.viewpoints_with_no_route
    ] == [("CVR-1", "no_attested_route")]
    assert exceptions == [
        "deterministic_reject:attestation ATTEST-1: terminal Claim component has no positive Registry link",
        "route:ROUTE-GREEK:no_valid_attestation_after_deterministic_validation",
        "viewpoint:CVR-1:no_route_after_deterministic_isolation",
    ]


def test_deterministic_route_target_isolation_rejects_global_findings():
    from backend.pipeline.viewpoint_route_resolution import (
        isolate_deterministically_invalid_route_targets,
    )

    with pytest.raises(BatchResolutionError, match="approved viewpoint"):
        isolate_deterministically_invalid_route_targets(
            _routes(),
            ["CVR-1: approved viewpoint has no route or no-route disposition"],
        )


def test_registry_route_packet_rejects_stale_claim_link():
    claim = _claim("C1", ROCK_STATEMENT)
    with pytest.raises(ValueError, match="stale Claim revision"):
        build_registry_route_packet(
            scope_label="matt16-13-18",
            approved_viewpoints=[_approved_viewpoint()],
            claims=[claim],
            viewpoint_claim_links=[_registry_link(claim, pinned_claim_revision=2)],
            existing_routes=[],
        )


def test_route_changeset_compiles_reviewed_v2_master_records():
    claim = _claim("C1", ROCK_STATEMENT)
    packet = build_registry_route_packet(
        scope_label="matt16-13-18",
        approved_viewpoints=[_approved_viewpoint()],
        claims=[claim],
        viewpoint_claim_links=[_registry_link(claim)],
        existing_routes=[],
    )
    proposal = _routes()
    package = compile_argument_route_package(
        proposal=proposal,
        passing_route_keys=["ROUTE-GREEK"],
        passing_attestation_keys=["ATTEST-1"],
        route_packet=packet,
        existing_routes=[],
        claims=[claim],
        proposal_artifact_sha256="proposal-sha",
        review_artifact_sha256="review-sha",
        proposer_model_id="gpt-5.6-sol",
        reviewer_model_id="claude-opus-5",
        decided_at="2026-08-24T12:00:00Z",
    )

    assert len(package["argument_routes"]) == 1
    revision = package["argument_route_revisions"][0]
    assert revision["schema_version"] == "wang_argument_route_revision_v2"
    assert revision["route_signature"]["inference_method_codes"] == ["morphology"]
    assert revision["ordered_inference_nodes"][-1] == {
        "route_step_key": "C1",
        "role": "conclusion",
        "normalized_proposition": None,
        "conclusion_viewpoint_revision_id": "CVR-1",
        "required_for_full_attestation": True,
    }
    attestation = package["argument_route_attestations"][0]
    assert attestation["schema_version"] == "wang_argument_route_attestation_v2"
    assert attestation["terminal_claim_link_id"] == "VCL-1"
    assert attestation["claim_ids"] == ["C1"]
    assert attestation["step_bindings"][0]["claim_component_keys"][0].startswith(
        "CCK-"
    )


def test_route_changeset_derives_scripture_from_sibling_claim_evidence():
    member = _claim("C1", ROCK_STATEMENT, scripture_refs=["Matt.16.18"])
    sibling = _claim("C2", MODAL_STATEMENT, scripture_refs=["Eph.2.20"])
    packet = build_registry_route_packet(
        scope_label="matt16-13-18",
        approved_viewpoints=[_approved_viewpoint()],
        claims=[member, sibling],
        viewpoint_claim_links=[_registry_link(member)],
        existing_routes=[],
    )
    binding = _route_component_binding()
    sibling_step_bindings = [
        {
            "route_step_key": step_key,
            "claim_component_keys": [binding.claim_component_key],
            "evidence_step_ids": ["C2-E1"],
            "source_fragment_ids": ["C2-F1"],
            "attestation_status": "attested",
        }
        for step_key in ("P1", "C1")
    ]
    proposal = _routes(
        source_route_attestations=[
            _attestation(
                claim_ids=["C1"],
                step_bindings=sibling_step_bindings,
            )
        ]
    )

    package = compile_argument_route_package(
        proposal=proposal,
        passing_route_keys=["ROUTE-GREEK"],
        passing_attestation_keys=["ATTEST-1"],
        route_packet=packet,
        existing_routes=[],
        claims=[member, sibling],
        proposal_artifact_sha256="proposal-sha",
        review_artifact_sha256="review-sha",
        proposer_model_id="gpt-5.6-sol",
        reviewer_model_id="claude-opus-5",
        decided_at="2026-08-24T12:00:00Z",
    )

    assert package["argument_route_attestations"][0][
        "scripture_refs_derived"
    ] == ["Eph.2.20"]


def test_routes_validate_against_settled_conclusions():
    report = _check_routes(_routes(), [_claim("C1", ROCK_STATEMENT)])
    assert report["route_count"] == 1
    assert report["full_count"] == 1
    assert report["inference_method_codes"] == ["morphology"]


def test_route_review_batches_bound_targets_and_cover_them_exactly_once():
    from backend.pipeline.viewpoint_route_resolution import (
        build_route_review_batches,
    )

    claim = _claim("C1", ROCK_STATEMENT)
    packet = build_registry_route_packet(
        scope_label="matt16-13-18",
        approved_viewpoints=[_approved_viewpoint()],
        claims=[claim],
        viewpoint_claim_links=[_registry_link(claim)],
        existing_routes=[],
    )
    batches = build_route_review_batches(
        proposal=_routes(),
        route_packet=packet,
        route_proposal_sha256="proposal-sha",
        max_targets=1,
    )

    targets = [
        (item["target_kind"], item["target_key"])
        for batch in batches
        for item in batch["review_targets"]
    ]
    assert targets == [("route", "ROUTE-GREEK"), ("attestation", "ATTEST-1")]
    assert all(len(batch["review_targets"]) <= 1 for batch in batches)
    # The route-target batch sees its attestation as context but does not grant
    # the reviewer a second decision for it.
    assert len(batches[0]["route_proposal_context"]["source_route_attestations"]) == 1


def test_an_attestation_may_not_span_two_sermons():
    # The one error this layer exists to make impossible: a premise from one
    # sermon and a conclusion from another is an argument nobody delivered.
    routes = _routes(source_route_attestations=[_attestation(claim_ids=["C1", "C2"])])
    with pytest.raises(BatchResolutionError, match="an attestation is one source only"):
        _check_routes(
            routes,
            [
                _claim("C1", ROCK_STATEMENT),
                ReviewClaim(
                    claim_id="C2",
                    pinned_claim_revision=1,
                    claim_revision_sha256="sha-C2",
                    source_id="S2",
                    statement=MODAL_STATEMENT,
                    review_status="approved",
                    evidence=[{**_evidence("C2"), "source_id": "S2"}],
                ),
            ],
        )


def test_borrowed_evidence_is_caught_even_within_one_batch():
    routes = _routes(
        source_route_attestations=[
            _attestation(
                step_bindings=[
                    {
                        "route_step_key": "P1",
                        "claim_component_keys": [_route_component_binding().claim_component_key],
                        "evidence_step_ids": ["C9-E1"],
                        "source_fragment_ids": ["C1-F1"],
                        "attestation_status": "attested",
                    }
                ],
                completeness="partial",
                terminal_claim_component_key=None,
            )
        ]
    )
    with pytest.raises(BatchResolutionError, match="EvidenceStep C9-E1 is outside this source"):
        _check_routes(routes, [_claim("C1", ROCK_STATEMENT)])


def test_full_requires_every_required_node_attested():
    routes = _routes(
        source_route_attestations=[
            _attestation(
                step_bindings=[
                    {
                        "route_step_key": "P1",
                        "claim_component_keys": [_route_component_binding().claim_component_key],
                        "evidence_step_ids": ["C1-E1"],
                        "source_fragment_ids": ["C1-F1"],
                        "attestation_status": "missing",
                    },
                    {
                        "route_step_key": "C1",
                        "claim_component_keys": [_route_component_binding().claim_component_key],
                        "evidence_step_ids": ["C1-E1"],
                        "source_fragment_ids": ["C1-F1"],
                        "attestation_status": "attested",
                    },
                ]
            )
        ]
    )
    with pytest.raises(BatchResolutionError, match="required step P1"):
        _check_routes(routes, [_claim("C1", ROCK_STATEMENT)])


def test_node_roles_and_method_codes_come_from_the_policy_vocabulary():
    from backend.api.canonical_repository.viewpoint_batch_resolution import ArgumentRouteCandidate

    # A free-text slug is exactly what route identity must not turn on.
    with pytest.raises(ValueError, match="not a policy inference method code"):
        ArgumentRouteCandidate.model_validate(_route(inference_method_codes=["greek_morphology"]))
    with pytest.raises(ValueError):
        ArgumentRouteCandidate.model_validate(
            _route(
                ordered_inference_nodes=[
                    {
                        "route_step_key": "P1",
                        "role": "希臘文詞形論證",
                        "normalized_proposition": "x",
                        "required_for_full_attestation": True,
                    },
                    {
                        "route_step_key": "C1",
                        "role": "conclusion",
                        "conclusion_ref": {"target_viewpoint_revision_id": "CVR-1"},
                        "required_for_full_attestation": True,
                    },
                ]
            )
        )
    with pytest.raises(ValueError, match="other requires a reviewable note"):
        ArgumentRouteCandidate.model_validate(_route(inference_method_codes=["other"]))


def test_a_route_nobody_preached_is_rejected():
    with pytest.raises(BatchResolutionError, match="proposed with no source attestation"):
        _check_routes(_routes(source_route_attestations=[]), [_claim("C1", ROCK_STATEMENT)])


def test_cross_source_composition_can_never_be_a_pass():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        ArgumentRouteReviewResponse,
    )

    with pytest.raises(ValueError, match="never a passing review"):
        ArgumentRouteReviewResponse.model_validate(
            {
                "route_proposal_sha256": "proposal-sha",
                "route_evidence_packet_sha256": "packet-sha",
                "change_reviews": [
                    {
                        "target_kind": "route",
                        "target_key": "ROUTE-GREEK",
                        "decision": "pass",
                        "finding_codes": [],
                        "reason": "错误地放行",
                    }
                ],
                "cross_source_composition_found": True,
                "reason": "发现跨来源拼接",
            }
        )


def test_a_route_may_not_target_a_conclusion_the_batch_never_settled():
    routes = _routes(
        argument_route_candidates=[
            _route(
                conclusion_ref={"target_viewpoint_revision_id": "CVR-NEVER"},
                ordered_inference_nodes=[
                    _route()["ordered_inference_nodes"][0],
                    {
                        "route_step_key": "C1",
                        "role": "conclusion",
                        "conclusion_ref": {
                            "target_viewpoint_revision_id": "CVR-NEVER"
                        },
                        "required_for_full_attestation": True,
                    },
                ],
            )
        ],
        source_route_attestations=[],
    )
    with pytest.raises(BatchResolutionError, match="CVR-NEVER"):
        _check_routes(routes, [_claim("C1", ROCK_STATEMENT)])


def test_review_must_decide_every_route_and_attestation():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        ArgumentRouteReviewResponse,
        validate_route_review,
    )

    routes = _routes()
    review = ArgumentRouteReviewResponse.model_validate(
        {
            "route_proposal_sha256": "proposal-sha",
            "route_evidence_packet_sha256": "packet-sha",
            "change_reviews": [
                {
                    "target_kind": "attestation",
                    "target_key": "ATTEST-1",
                    "decision": "pass",
                    "finding_codes": [],
                    "reason": "判断成立",
                }
            ],
            "cross_source_composition_found": False,
            "reason": "无跨来源拼接",
        }
    )
    with pytest.raises(BatchResolutionError, match="route:ROUTE-GREEK"):
        validate_route_review(
            review=review,
            proposal=routes,
            route_proposal_sha256="proposal-sha",
            route_evidence_packet_sha256="packet-sha",
        )


def test_a_sibling_claim_in_the_same_sermon_is_not_cross_source():
    # The first real route run bound a step from another Claim in the same
    # sermon. That is the professor's own reasoning; the invariant is the source
    # revision, not the Claims the attestation happened to list.
    sibling = ReviewClaim(
        claim_id="C2",
        pinned_claim_revision=1,
        claim_revision_sha256="sha-C2",
        source_id="S1",
        statement=MODAL_STATEMENT,
        review_status="approved",
        evidence=[_evidence("C2")],
    )
    routes = _routes(
        source_route_attestations=[
            _attestation(
                claim_ids=["C1"],
                step_bindings=[
                    {
                        "route_step_key": "P1",
                        "claim_component_keys": [_route_component_binding().claim_component_key],
                        "evidence_step_ids": ["C1-E1", "C2-E1"],
                        "source_fragment_ids": ["C1-F1", "C2-F1"],
                        "attestation_status": "attested",
                    },
                    {
                        "route_step_key": "C1",
                        "claim_component_keys": [_route_component_binding().claim_component_key],
                        "evidence_step_ids": ["C1-E1"],
                        "source_fragment_ids": ["C1-F1"],
                        "attestation_status": "attested",
                    },
                ],
            )
        ]
    )
    report = _check_routes(routes, [_claim("C1", ROCK_STATEMENT), sibling])
    assert report["attestation_count"] == 1

    # A step from a different sermon still fails.
    other_sermon = ReviewClaim(
        claim_id="C3",
        pinned_claim_revision=1,
        claim_revision_sha256="sha-C3",
        source_id="S2",
        statement=MODAL_STATEMENT,
        review_status="approved",
        evidence=[{**_evidence("C3"), "source_id": "S2"}],
    )
    borrowed = _routes(
        source_route_attestations=[
            _attestation(
                step_bindings=[
                    {
                        "route_step_key": "P1",
                        "claim_component_keys": [_route_component_binding().claim_component_key],
                        "evidence_step_ids": ["C3-E1"],
                        "source_fragment_ids": ["C1-F1"],
                        "attestation_status": "attested",
                    },
                    {
                        "route_step_key": "C1",
                        "claim_component_keys": [_route_component_binding().claim_component_key],
                        "evidence_step_ids": ["C1-E1"],
                        "source_fragment_ids": ["C1-F1"],
                        "attestation_status": "attested",
                    },
                ]
            )
        ]
    )
    with pytest.raises(BatchResolutionError, match="EvidenceStep C3-E1 is outside this source"):
        _check_routes(borrowed, [_claim("C1", ROCK_STATEMENT), other_sermon])


def test_route_review_accepts_multiple_route_objects_without_component_key_collision():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        ArgumentRouteReviewResponse,
        validate_route_review,
    )

    proposal = _routes()
    review = ArgumentRouteReviewResponse.model_validate(
        {
            "route_proposal_sha256": "proposal-sha",
            "route_evidence_packet_sha256": "packet-sha",
            "change_reviews": [
                {
                    "target_kind": "route",
                    "target_key": "ROUTE-GREEK",
                    "decision": "pass",
                    "finding_codes": [],
                    "reason": "骨架成立",
                },
                {
                    "target_kind": "attestation",
                    "target_key": "ATTEST-1",
                    "decision": "pass",
                    "finding_codes": [],
                    "reason": "来源绑定成立",
                },
            ],
            "cross_source_composition_found": False,
            "reason": "逐项通过",
        }
    )
    report = validate_route_review(
        review=review,
        proposal=proposal,
        route_proposal_sha256="proposal-sha",
        route_evidence_packet_sha256="packet-sha",
    )
    assert report["outcome"] == "pass"
    assert report["reviewed_change_count"] == 2


def test_every_approved_viewpoint_gets_a_route_or_closed_no_route_disposition():
    empty = _routes(argument_route_candidates=[], source_route_attestations=[])
    with pytest.raises(BatchResolutionError, match="CVR-1: approved viewpoint has no route"):
        _check_routes(empty, [_claim("C1", ROCK_STATEMENT)])

    closed = _routes(
        argument_route_candidates=[],
        source_route_attestations=[],
        viewpoints_with_no_route=[
            {
                "viewpoint_revision_id": "CVR-1",
                "reason_code": "no_attested_route",
                "reason": "scope 中只有结论，没有可辨识的推理链",
            }
        ],
    )
    assert _check_routes(closed, [_claim("C1", ROCK_STATEMENT)])["route_count"] == 0


def test_attestation_component_keys_are_resolved_not_trusted():
    fake = "CCK-" + "0" * 64
    routes = _routes(
        source_route_attestations=[
            _attestation(
                step_bindings=[
                    {
                        "route_step_key": "P1",
                        "claim_component_keys": [fake],
                        "evidence_step_ids": ["C1-E1"],
                        "source_fragment_ids": ["C1-F1"],
                        "attestation_status": "attested",
                    },
                    {
                        "route_step_key": "C1",
                        "claim_component_keys": [fake],
                        "evidence_step_ids": ["C1-E1"],
                        "source_fragment_ids": ["C1-F1"],
                        "attestation_status": "attested",
                    },
                ],
                terminal_claim_component_key=fake,
            )
        ]
    )
    with pytest.raises(BatchResolutionError, match="is not in the route packet"):
        _check_routes(routes, [_claim("C1", ROCK_STATEMENT)])


def test_run_route_scope_is_independent_and_resumable(tmp_path: Path):
    from backend.pipeline.viewpoint_route_resolution import run_route_scope

    effective = _proposal(
        claim_decisions=[
            {
                "claim_id": "C1",
                "components": [
                    _component(
                        ROCK_STATEMENT,
                        "磐石不是彼得这个人",
                        "member_existing",
                        target_viewpoint_revision_id="CVR-1",
                    )
                ],
            }
        ],
        new_viewpoint_candidates=[],
    )
    approved = [
        {"viewpoint_revision_id": "CVR-1", "core_proposition": "磐石不指彼得本人"}
    ]
    claims = [_claim("C1", ROCK_STATEMENT)]
    packet = build_registry_route_packet(
        scope_label="matt16-13-18",
        approved_viewpoints=[_approved_viewpoint()],
        claims=claims,
        viewpoint_claim_links=[_registry_link(claims[0])],
        existing_routes=[],
    )
    route_payload = _routes().model_dump(mode="json")
    route_sha = sha256_json(route_payload)
    review_payload = {
        "schema_version": "wang_argument_route_review_v1",
        "route_proposal_sha256": route_sha,
        "route_evidence_packet_sha256": packet["packet_sha256"],
        "change_reviews": [
            {
                "target_kind": "route",
                "target_key": "ROUTE-GREEK",
                "decision": "pass",
                "finding_codes": [],
                "reason": "骨架成立",
            },
            {
                "target_kind": "attestation",
                "target_key": "ATTEST-1",
                "decision": "pass",
                "finding_codes": [],
                "reason": "来源绑定成立",
            },
        ],
        "cross_source_composition_found": False,
        "reason": "全部通过",
    }
    proposer = _StubAdapter(route_payload)
    reviewer = _StubAdapter(review_payload)
    kwargs = {
        "scope_label": "matt16-13-18",
        "claims": claims,
        "existing_routes": [],
        "route_packet": packet,
        "output_dir": tmp_path / "routes",
        "proposer": proposer,
        "reviewer": reviewer,
    }
    report = run_route_scope(**kwargs)
    assert report["passing_route_keys"] == ["ROUTE-GREEK"]
    assert report["passing_attestation_keys"] == ["ATTEST-1"]
    assert report["master_data_mutations"] == 0
    assert report["recorded_model_executions"]["calls_recorded_total"] == 2
    again = run_route_scope(**kwargs)
    assert proposer.calls == 1
    assert reviewer.calls == 1
    assert again["measurements"]["proposal_calls_executed"] == 0
    assert again["recorded_model_executions"]["calls_recorded_total"] == 2
    current = json.loads((tmp_path / "routes" / "current-state.json").read_text())
    assert current["status"] == "resolved"


def test_reject_and_defer_do_not_trigger_cvp_correction():
    proposal = _proposal()
    review = _review("reject", "pass")
    report = validate_review(
        review=review,
        proposal=proposal,
        proposal_sha256="proposal-sha",
    )
    assert report["reconsideration_required"] is True
    assert report["correction_required"] is False


def test_existing_route_must_reach_the_same_conclusion_viewpoint():
    routes = _routes(
        argument_route_candidates=[
            _route(
                proposed_action="match_existing",
                target_argument_route_revision_id="ARR-1",
            )
        ]
    )
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        validate_route_proposal,
    )

    with pytest.raises(BatchResolutionError, match="another conclusion viewpoint"):
        validate_route_proposal(
            routes=routes,
            scope_label="matt16-13-18",
            claims=[_claim("C1", ROCK_STATEMENT)],
            approved_viewpoint_revision_ids=["CVR-1"],
            known_route_revision_ids=["ARR-1"],
            known_route_conclusions={"ARR-1": "CVR-OTHER"},
            component_bindings=[_route_component_binding()],
        )


def test_route_correction_is_confined_to_flagged_objects():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        ArgumentRouteReconsiderationResponse,
        ArgumentRouteReviewResponse,
        validate_route_reconsideration,
    )

    proposal = _routes()
    proposal_sha = sha256_json(proposal.model_dump(mode="json"))
    review = ArgumentRouteReviewResponse.model_validate(
        {
            "route_proposal_sha256": proposal_sha,
            "route_evidence_packet_sha256": "packet-sha",
            "change_reviews": [
                {
                    "target_kind": "route",
                    "target_key": "ROUTE-GREEK",
                    "decision": "correct",
                    "finding_codes": ["route_label_overstates"],
                    "reason": "label 把形态观察说成绝对证明",
                    "correction": "将 label 改为较保守的形态差异论证",
                },
                {
                    "target_kind": "attestation",
                    "target_key": "ATTEST-1",
                    "decision": "pass",
                    "finding_codes": [],
                    "reason": "来源绑定成立",
                },
            ],
            "cross_source_composition_found": False,
            "reason": "只有 label 需要修正",
        }
    )
    review_sha = sha256_json(review.model_dump(mode="json"))
    revised = proposal.model_dump(mode="json")
    revised["argument_route_candidates"][0]["route_label"] = "以词形差异支持磐石不指彼得本人"
    reconsideration = ArgumentRouteReconsiderationResponse.model_validate(
        {
            "route_proposal_sha256": proposal_sha,
            "route_review_sha256": review_sha,
            "finding_dispositions": [
                {
                    "target_kind": "route",
                    "target_key": "ROUTE-GREEK",
                    "disposition": "accepted",
                    "reason": "已按标准收窄 label",
                }
            ],
            "revised_proposal": revised,
        }
    )
    report = validate_route_reconsideration(
        reconsideration=reconsideration,
        proposal=proposal,
        review=review,
        route_proposal_sha256=proposal_sha,
        route_review_sha256=review_sha,
    )
    assert report["outcome"] == "resolved"

    tampered = reconsideration.model_dump(mode="json")
    tampered["revised_proposal"]["source_route_attestations"][0]["reason"] = "偷偷改 attestation"
    with pytest.raises(BatchResolutionError, match="unflagged route object changed"):
        validate_route_reconsideration(
            reconsideration=ArgumentRouteReconsiderationResponse.model_validate(tampered),
            proposal=proposal,
            review=review,
            route_proposal_sha256=proposal_sha,
            route_review_sha256=review_sha,
        )


def test_failed_attestation_does_not_invalidate_approved_cvp(tmp_path: Path):
    from backend.pipeline.viewpoint_route_resolution import run_route_scope

    effective = _proposal(
        claim_decisions=[
            {
                "claim_id": "C1",
                "components": [
                    _component(
                        ROCK_STATEMENT,
                        "磐石不是彼得这个人",
                        "member_existing",
                        target_viewpoint_revision_id="CVR-1",
                    )
                ],
            }
        ],
        new_viewpoint_candidates=[],
    )
    approved = [
        {"viewpoint_revision_id": "CVR-1", "core_proposition": "磐石不指彼得本人"}
    ]
    claims = [_claim("C1", ROCK_STATEMENT)]
    packet = build_registry_route_packet(
        scope_label="matt16-13-18",
        approved_viewpoints=[_approved_viewpoint()],
        claims=claims,
        viewpoint_claim_links=[_registry_link(claims[0])],
        existing_routes=[],
    )
    route_payload = _routes().model_dump(mode="json")
    review_payload = {
        "schema_version": "wang_argument_route_review_v1",
        "route_proposal_sha256": sha256_json(route_payload),
        "route_evidence_packet_sha256": packet["packet_sha256"],
        "change_reviews": [
            {
                "target_kind": "route",
                "target_key": "ROUTE-GREEK",
                "decision": "pass",
                "finding_codes": [],
                "reason": "骨架成立",
            },
            {
                "target_kind": "attestation",
                "target_key": "ATTEST-1",
                "decision": "reject",
                "finding_codes": ["evidence_does_not_support_node"],
                "reason": "该证据不足以支持 premise",
            },
        ],
        "cross_source_composition_found": False,
        "reason": "路线可能成立，但本 occurrence 不成立",
    }
    report = run_route_scope(
        scope_label="matt16-13-18",
        claims=claims,
        existing_routes=[],
        route_packet=packet,
        output_dir=tmp_path / "routes",
        proposer=_StubAdapter(route_payload),
        reviewer=_StubAdapter(review_payload),
    )
    assert report["cvp_mutations_proposed"] == 0
    assert report["passing_route_keys"] == []
    assert "attestation:ATTEST-1:reject" in report["exceptions"]
    assert "route:ROUTE-GREEK:no_passing_attestation" in report["exceptions"]


def test_support_may_target_a_viewpoint_this_batch_creates() -> None:
    """An argument for a viewpoint the same batch is proposing has somewhere to go.

    Without this the only legal disposition for such a component is
    ``new_viewpoint``, which turns every supporting argument into its own CVP.
    """
    statement = "彼得與盤石的性別不同"
    component = ProposedComponent.model_validate(
        _component(
            statement,
            statement,
            "support_existing",
            local_new_viewpoint_key="ROCK-NOT-PETER",
        )
    )
    assert component.local_new_viewpoint_key == "ROCK-NOT-PETER"
    assert component.target_viewpoint_revision_id is None


def test_support_may_not_target_both_a_revision_and_a_local_key() -> None:
    statement = "彼得與盤石的性別不同"
    with pytest.raises(ValidationError, match="may not target both"):
        ProposedComponent.model_validate(
            _component(
                statement,
                statement,
                "support_existing",
                target_viewpoint_revision_id="CVR-1",
                local_new_viewpoint_key="ROCK-NOT-PETER",
            )
        )


def test_support_still_needs_some_target() -> None:
    statement = "彼得與盤石的性別不同"
    with pytest.raises(ValidationError, match="requires a target viewpoint revision or local key"):
        ProposedComponent.model_validate(
            _component(statement, statement, "support_existing")
        )


def test_member_existing_may_not_use_a_local_key() -> None:
    """Members of a new viewpoint are expressed by sharing its local key on
    ``new_viewpoint`` components, not by pointing ``member_existing`` at it."""
    statement = "彼得與盤石的性別不同"
    with pytest.raises(ValidationError, match="member_existing targets a committed revision"):
        ProposedComponent.model_validate(
            _component(
                statement,
                statement,
                "member_existing",
                local_new_viewpoint_key="ROCK-NOT-PETER",
            )
        )


def test_structure_focal_needs_exactly_one_endpoint() -> None:
    with pytest.raises(ValidationError, match="requires a revision id or a local key"):
        ProposedStructureFocal.model_validate({"structure_role": "central_claim"})
    with pytest.raises(ValidationError, match="may not carry both"):
        ProposedStructureFocal.model_validate(
            {
                "structure_role": "central_claim",
                "viewpoint_revision_id": "CVR-1",
                "local_key": "ROCK-NOT-PETER",
            }
        )


def test_a_viewpoint_holds_one_role_per_structure() -> None:
    with pytest.raises(ValidationError, match="only one role"):
        ProposedViewpointStructure.model_validate(
            {
                "central_synthesis": "磐石不是彼得本人",
                "reason": "测试用理由",
                "focal": [
                    {"local_key": "ROCK-NOT-PETER", "structure_role": "negative_boundary"},
                    {"local_key": "ROCK-NOT-PETER", "structure_role": "application"},
                ],
            }
        )


def test_relation_direction_is_source_first() -> None:
    """``source applies target`` means source is an application of target."""
    relation = ProposedViewpointRelation.model_validate(
        {
            "source_local_key": "PETER-NOT-FIRST-POPE",
            "target_local_key": "ROCK-NOT-PETER",
            "relation_type": "applies",
            "reason": "由磐石不是彼得本人推出彼得不是第一任教皇",
        }
    )
    assert relation.endpoints() == (("new", "PETER-NOT-FIRST-POPE"), ("new", "ROCK-NOT-PETER"))


def test_relation_endpoints_must_differ() -> None:
    with pytest.raises(ValidationError, match="endpoints must differ"):
        ProposedViewpointRelation.model_validate(
            {
                "source_local_key": "ROCK-NOT-PETER",
                "target_local_key": "ROCK-NOT-PETER",
                "relation_type": "applies",
                "reason": "测试用理由",
            }
        )


# --- revising committed wording ------------------------------------------------

REGISTRY_CONTEXT_ROCK = [
    {
        "viewpoint_id": "CV-1",
        "viewpoint_revision_id": "CVR-1",
        "core_proposition": "彼得不是第一任教皇。",
        "proposition_signature": {
            "subject": "彼得",
            "predicate": "具有",
            "object": "第一任教皇的身份",
            "polarity": "denied",
            "modality": "断言",
            "conditions": [],
            "population_scope": ["彼得"],
            "temporal_scope": [],
        },
        "scope": {"scripture_scope": ["馬太福音16:18"], "audience_scope": [], "historical_scope": []},
    }
]


def _revision(**overrides: Any) -> dict[str, Any]:
    payload = {
        "target_viewpoint_revision_id": "CVR-1",
        "core_proposition": "罗马天主教从马太福音16章推出的教皇制解经是错误的。",
        "subject": "罗马天主教对马太福音16章的教皇制解经",
        "predicate": "成立",
        "object": "",
        "polarity": "denied",
        "modality": "断言",
        "scripture_scope": ["馬太福音16:18", "馬太福音16:19"],
        "conditions": [],
        "population_scope": ["彼得", "历代教皇"],
        "revision_reason": "既有措辞只落在彼得的身份上，装不下本批对权柄传承的否定；两者是同一真值条件。",
    }
    payload.update(overrides)
    return payload


def _member_proposal(**overrides: Any) -> CanonicalViewpointProposalResponse:
    payload: dict[str, Any] = {
        "batch_id": "CVB-test-001",
        "claim_decisions": [
            {
                "claim_id": "C1",
                "components": [
                    _component(
                        ROCK_STATEMENT,
                        "磐石不是彼得这个人",
                        "member_existing",
                        target_viewpoint_revision_id="CVR-1",
                    )
                ],
            }
        ],
        "new_viewpoint_candidates": [],
    }
    payload.update(overrides)
    return CanonicalViewpointProposalResponse.model_validate(payload)


def _passing_review(proposal: CanonicalViewpointProposalResponse) -> CanonicalViewpointReviewResponse:
    return CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": sha256_json(proposal.model_dump(mode="json")),
            "change_reviews": [
                {"claim_id": "C1", "component_index": 0, "decision": "pass", "reason": "通过"}
            ],
            "novelty_review": {"status": "pass", "reason": "没有遗漏的新观点"},
            "revision_reviews": [
                {
                    "target_viewpoint_revision_id": item.target_viewpoint_revision_id,
                    "decision": "pass",
                    "reason": "同一真值条件，扩写后原有来源仍归得进去。",
                }
                for item in proposal.viewpoint_revisions
            ],
        }
    )


def _compile(proposal: CanonicalViewpointProposalResponse, review: CanonicalViewpointReviewResponse):
    return compile_cvp_batch_package(
        proposal=proposal,
        review=review,
        deterministic_validation_sha256="validation-sha",
        scope_manifest_sha256="scope-manifest-sha",
        claims=[_claim("C1", ROCK_STATEMENT)],
        registry_context=REGISTRY_CONTEXT_ROCK,
        proposal_artifact_sha256="proposal-call-sha",
        review_artifact_sha256="review-call-sha",
        proposer_model_id="gpt-5.6-sol/high",
        reviewer_model_id="claude-opus-5/high",
        decided_at="2026-08-24T12:00:00Z",
    )


def test_approved_revision_supersedes_the_committed_wording():
    proposal = _member_proposal(viewpoint_revisions=[_revision()])
    package = _compile(proposal, _passing_review(proposal))

    assert len(package["viewpoint_revisions"]) == 1
    revision = package["viewpoint_revisions"][0]
    assert revision["viewpoint_id"] == "CV-1"
    # Each revision is its own object, so the store writes it at 1 and the
    # record refuses anything else; the chain is what records the supersession.
    assert revision["revision_number"] == 1
    assert revision["supersedes_revision_id"] == "CVR-1"
    assert revision["core_proposition"] == "罗马天主教从马太福音16章推出的教皇制解经是错误的。"
    # The identity does not move; only the wording it currently points at does.
    assert [item["viewpoint_id"] for item in package["canonical_viewpoints"]] == ["CV-1"]
    assert package["canonical_viewpoints"][0]["current_revision_id"] == revision["viewpoint_revision_id"]
    # Everything this batch writes binds to the new revision, not the superseded one.
    assert package["viewpoint_claim_links"][0][
        "validated_against_viewpoint_revision_id"
    ] == revision["viewpoint_revision_id"]


def test_batch_without_a_revision_leaves_the_committed_wording_alone():
    proposal = _member_proposal()
    package = _compile(proposal, _passing_review(proposal))

    assert package["viewpoint_revisions"] == []
    assert package["canonical_viewpoints"] == []
    assert package["viewpoint_claim_links"][0]["validated_against_viewpoint_revision_id"] == "CVR-1"


def test_revision_the_reviewer_did_not_pass_is_not_written():
    proposal = _member_proposal(viewpoint_revisions=[_revision()])
    review = CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": sha256_json(proposal.model_dump(mode="json")),
            "change_reviews": [
                {"claim_id": "C1", "component_index": 0, "decision": "pass", "reason": "通过"}
            ],
            "novelty_review": {"status": "pass", "reason": "没有遗漏的新观点"},
            "revision_reviews": [
                {
                    "target_viewpoint_revision_id": "CVR-1",
                    "decision": "reject",
                    "finding_codes": ["revision_would_absorb_neighbour"],
                    "reason": "新措辞会把另一条 viewpoint 一并吞掉。",
                }
            ],
        }
    )
    with pytest.raises(CvpBatchChangeSetError):
        _compile(proposal, review)


def test_revision_must_target_a_viewpoint_the_batch_attaches_to():
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_proposal

    proposal = _proposal(
        viewpoint_revisions=[_revision(target_viewpoint_revision_id="CVR-untouched")]
    )
    with pytest.raises(BatchResolutionError, match="is not attached to by any component"):
        validate_proposal(
            proposal=proposal,
            batch_id="CVB-test-001",
            claims=[_claim("C1", ROCK_STATEMENT)],
            registry_revision_ids=["CVR-1", "CVR-untouched"],
        )


def test_review_must_decide_every_proposed_revision():
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_review

    proposal = _member_proposal(viewpoint_revisions=[_revision()])
    proposal_sha = sha256_json(proposal.model_dump(mode="json"))
    review = CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": proposal_sha,
            "change_reviews": [
                {"claim_id": "C1", "component_index": 0, "decision": "pass", "reason": "通过"}
            ],
            "novelty_review": {"status": "pass", "reason": "没有遗漏的新观点"},
        }
    )
    with pytest.raises(BatchResolutionError, match="proposed viewpoint revision has no review decision"):
        validate_review(review=review, proposal=proposal, proposal_sha256=proposal_sha)


def test_withdrawing_a_flagged_revision_resolves_the_batch():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_reconsideration_patches,
    )

    proposal = _member_proposal(viewpoint_revisions=[_revision()])
    review = CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": "proposal-sha",
            "change_reviews": [
                {"claim_id": "C1", "component_index": 0, "decision": "pass", "reason": "通过"}
            ],
            "novelty_review": {"status": "pass", "reason": "没有遗漏的新观点"},
            "revision_reviews": [
                {
                    "target_viewpoint_revision_id": "CVR-1",
                    "decision": "correct",
                    "finding_codes": ["revision_too_broad"],
                    "reason": "新措辞过宽。",
                    "correction": "撤回该修订，或改回只覆盖本批 Claim 的范围。",
                }
            ],
        }
    )
    reconsideration = CanonicalViewpointReconsiderationResponse.model_validate(
        {
            "proposal_sha256": "proposal-sha",
            "review_sha256": "review-sha",
            "finding_dispositions": [],
            "revision_dispositions": [
                {
                    "target_viewpoint_revision_id": "CVR-1",
                    "disposition": "accepted",
                    "reason": "同意过宽，撤回。",
                }
            ],
            "revision_patches": [
                {"target_viewpoint_revision_id": "CVR-1", "action": "withdraw"}
            ],
        }
    )
    effective = apply_reconsideration_patches(
        reconsideration=reconsideration, proposal=proposal, review=review
    )
    assert effective.viewpoint_revisions == []


# --- identity consolidation ----------------------------------------------------

def _consolidation(*verdicts: dict[str, Any]) -> Any:
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        IdentityConsolidationResponse,
    )

    return IdentityConsolidationResponse.model_validate({"verdicts": list(verdicts)})


def _merge_verdict(**overrides: Any) -> dict[str, Any]:
    payload = {
        "local_key": "ROCK-NOT-PETER",
        "verdict": "matches_but_wording_too_narrow",
        "target_viewpoint_revision_id": "CVR-1",
        "revised_core_proposition": "罗马天主教据太16章所立的教皇制解经是错误的。",
        "revised_subject": "罗马天主教对太16章的教皇制解经",
        "revised_predicate": "成立",
        "revised_object": "",
        "revised_polarity": "denied",
        "revised_modality": "断言",
        "revised_scripture_scope": ["馬太福音16:18"],
        "revised_conditions": [],
        "revised_population_scope": ["彼得", "历代教皇"],
        "reason": "讲员把整套主张当一个整体否定；既有 signature 只锁在彼得的身份上，装不下本候选。",
    }
    payload.update(overrides)
    return payload


def test_consolidation_folds_a_duplicate_into_the_registry_viewpoint():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_consolidation,
    )

    proposal = _proposal()
    folded = apply_consolidation(
        consolidation=_consolidation(_merge_verdict()), proposal=proposal
    )

    component = folded.claim_decisions[0].components[0]
    assert component.disposition == "member_existing"
    assert component.target_viewpoint_revision_id == "CVR-1"
    assert component.local_new_viewpoint_key is None
    assert folded.new_viewpoint_candidates == []
    # The wording that could not hold it becomes the revision the reviewer sees.
    assert [item.target_viewpoint_revision_id for item in folded.viewpoint_revisions] == ["CVR-1"]
    assert folded.viewpoint_revisions[0].revision_reason.startswith("讲员把整套主张")


def test_consolidation_keeping_the_wording_writes_no_revision():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_consolidation,
    )

    folded = apply_consolidation(
        consolidation=_consolidation(
            {
                "local_key": "ROCK-NOT-PETER",
                "verdict": "matches_existing",
                "target_viewpoint_revision_id": "CVR-1",
                "reason": "同一真值条件，既有措辞装得下。",
            }
        ),
        proposal=_proposal(),
    )

    assert folded.viewpoint_revisions == []
    assert folded.claim_decisions[0].components[0].disposition == "member_existing"


def test_consolidation_leaves_a_genuinely_new_viewpoint_alone():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_consolidation,
    )

    proposal = _proposal()
    folded = apply_consolidation(
        consolidation=_consolidation(
            {"local_key": "ROCK-NOT-PETER", "verdict": "new", "reason": "库里没有。"}
        ),
        proposal=proposal,
    )
    assert folded == proposal


def test_consolidation_retargets_a_relation_onto_the_registry_viewpoint():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_consolidation,
    )

    proposal = _proposal(
        new_viewpoint_candidates=[_candidate("ROCK-NOT-PETER"), _candidate("SPARE")],
        viewpoint_relations=[
            {
                "source_local_key": "SPARE",
                "target_local_key": "ROCK-NOT-PETER",
                "relation_type": "applies",
                "reason": "由前者推出后者。",
            }
        ],
    )
    folded = apply_consolidation(
        consolidation=_consolidation(
            _merge_verdict(),
            {"local_key": "SPARE", "verdict": "new", "reason": "库里没有。"},
        ),
        proposal=proposal,
    )
    relation = folded.viewpoint_relations[0]
    assert relation.source_local_key == "SPARE"
    assert relation.target_viewpoint_revision_id == "CVR-1"
    assert relation.target_local_key is None


def test_consolidation_drops_a_relation_whose_ends_became_one_viewpoint():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_consolidation,
    )

    proposal = _proposal(
        new_viewpoint_candidates=[_candidate("ROCK-NOT-PETER"), _candidate("SPARE")],
        viewpoint_relations=[
            {
                "source_local_key": "SPARE",
                "target_local_key": "ROCK-NOT-PETER",
                "relation_type": "applies",
                "reason": "由前者推出后者。",
            }
        ],
    )
    folded = apply_consolidation(
        consolidation=_consolidation(
            _merge_verdict(),
            _merge_verdict(local_key="SPARE", verdict="matches_existing",
                           revised_core_proposition=None, revised_subject=None,
                           revised_predicate=None, revised_object=None,
                           revised_polarity=None, revised_modality=None,
                           target_viewpoint_revision_id="CVR-2"),
        ),
        proposal=proposal,
    )
    assert len(folded.viewpoint_relations) == 1
    folded_same = apply_consolidation(
        consolidation=_consolidation(
            {"local_key": "ROCK-NOT-PETER", "verdict": "matches_existing",
             "target_viewpoint_revision_id": "CVR-1", "reason": "同一条。"},
            {"local_key": "SPARE", "verdict": "new", "reason": "库里没有。"},
        ),
        proposal=_proposal(
            new_viewpoint_candidates=[_candidate("ROCK-NOT-PETER")],
            viewpoint_relations=[
                {
                    "source_viewpoint_revision_id": "CVR-1",
                    "target_local_key": "ROCK-NOT-PETER",
                    "relation_type": "applies",
                    "reason": "两端合并后同一。",
                }
            ],
        ),
    )
    assert folded_same.viewpoint_relations == []


def test_two_candidates_may_not_claim_one_registry_viewpoint():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        validate_consolidation,
    )

    proposal = _proposal(
        new_viewpoint_candidates=[_candidate("ROCK-NOT-PETER"), _candidate("SPARE")]
    )
    with pytest.raises(BatchResolutionError, match="already claimed by"):
        validate_consolidation(
            consolidation=_consolidation(
                _merge_verdict(),
                _merge_verdict(local_key="SPARE"),
            ),
            proposal=proposal,
            registry_revision_ids=["CVR-1"],
        )


def test_consolidation_must_rule_on_every_candidate():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        validate_consolidation,
    )

    proposal = _proposal(
        new_viewpoint_candidates=[_candidate("ROCK-NOT-PETER"), _candidate("SPARE")]
    )
    with pytest.raises(BatchResolutionError, match="SPARE: candidate has no consolidation verdict"):
        validate_consolidation(
            consolidation=_consolidation(_merge_verdict()),
            proposal=proposal,
            registry_revision_ids=["CVR-1"],
        )


# --- what a revision strands ---------------------------------------------------

def _dependent_link() -> dict[str, Any]:
    return {
        "record_kind": "viewpoint_claim_link",
        "record": {
            "viewpoint_claim_link_id": "VCL-old",
            "viewpoint_id": "CV-1",
            "claim_id": "C-earlier",
            "link_type": "equivalent_full",
            "effective_state": "active",
            "review_status": "system_approved",
            "validated_against_viewpoint_revision_id": "CVR-1",
            "decision_id": "VID-earlier",
            "pinned_claim_revision": 1,
            "evidence_bindings": [],
            "occurrence_refs": [],
        },
    }


def test_revision_repoints_the_records_the_reviewer_confirmed():
    proposal = _member_proposal(viewpoint_revisions=[_revision()])
    review = _passing_review(proposal)
    review = CanonicalViewpointReviewResponse.model_validate(
        review.model_dump(mode="json")
        | {
            "revision_reviews": [
                review.revision_reviews[0].model_dump(mode="json")
                | {"confirmed_dependent_ids": ["VCL-old"]}
            ]
        }
    )
    package = compile_cvp_batch_package(
        proposal=proposal,
        review=review,
        deterministic_validation_sha256="validation-sha",
        scope_manifest_sha256="scope-manifest-sha",
        claims=[_claim("C1", ROCK_STATEMENT)],
        registry_context=REGISTRY_CONTEXT_ROCK,
        revision_dependents={"CVR-1": [_dependent_link()]},
        proposal_artifact_sha256="proposal-call-sha",
        review_artifact_sha256="review-call-sha",
        proposer_model_id="gpt-5.6-sol/high",
        reviewer_model_id="claude-opus-5/high",
        decided_at="2026-08-24T12:00:00Z",
    )
    new_revision = package["viewpoint_revisions"][0]["viewpoint_revision_id"]
    moved = [
        item for item in package["viewpoint_claim_links"]
        if item["viewpoint_claim_link_id"] == "VCL-old"
    ]
    assert len(moved) == 1
    assert moved[0]["validated_against_viewpoint_revision_id"] == new_revision


def test_revision_that_strands_an_unconfirmed_record_is_refused():
    proposal = _member_proposal(viewpoint_revisions=[_revision()])
    with pytest.raises(CvpBatchChangeSetError, match="strands unconfirmed records: VCL-old"):
        compile_cvp_batch_package(
            proposal=proposal,
            review=_passing_review(proposal),
            deterministic_validation_sha256="validation-sha",
            scope_manifest_sha256="scope-manifest-sha",
            claims=[_claim("C1", ROCK_STATEMENT)],
            registry_context=REGISTRY_CONTEXT_ROCK,
            revision_dependents={"CVR-1": [_dependent_link()]},
            proposal_artifact_sha256="proposal-call-sha",
            review_artifact_sha256="review-call-sha",
            proposer_model_id="gpt-5.6-sol/high",
            reviewer_model_id="claude-opus-5/high",
            decided_at="2026-08-24T12:00:00Z",
        )


def test_refused_merge_must_leave_the_two_viewpoints_related():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        validate_consolidation_fallback,
    )

    ruling = _consolidation(_merge_verdict())
    # The merge did not stick: the candidate is still its own viewpoint.
    with pytest.raises(BatchResolutionError, match="no relation connects it to a viewpoint this batch proposes"):
        validate_consolidation_fallback(consolidation=ruling, proposal=_proposal())

    related = _proposal(
        viewpoint_relations=[
            {
                "source_local_key": "ROCK-NOT-PETER",
                "target_viewpoint_revision_id": "CVR-1",
                "relation_type": "specializes",
                "reason": "候选是既有观点在更窄经文范围上的具体化。",
            }
        ]
    )
    report = validate_consolidation_fallback(consolidation=ruling, proposal=related)
    assert report["unmerged_matches"] == [
        {"matched_revision_id": "CVR-1", "ruled_local_key": "ROCK-NOT-PETER"}
    ]


def test_renaming_the_candidate_does_not_slip_the_fallback_rule():
    """The correction round may replace a candidate under a new local key.

    Keying the rule on that key let a rename carry the match out of scope
    silently, which is how a batch reported the check passing while the edge it
    requires had only been drawn because the prompt asked nicely.
    """

    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        validate_consolidation_fallback,
    )

    renamed = _proposal(
        claim_decisions=[
            {
                "claim_id": "C1",
                "components": [
                    _component(
                        ROCK_STATEMENT,
                        "磐石不是彼得这个人",
                        "new_viewpoint",
                        local_new_viewpoint_key="RENAMED-AFTER-CORRECTION",
                    )
                ],
            }
        ],
        new_viewpoint_candidates=[_candidate("RENAMED-AFTER-CORRECTION")],
    )
    with pytest.raises(BatchResolutionError, match="CVR-1: consolidation matched it"):
        validate_consolidation_fallback(
            consolidation=_consolidation(_merge_verdict()), proposal=renamed
        )


def test_a_merge_that_stuck_needs_no_fallback_relation():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_consolidation,
        validate_consolidation_fallback,
    )

    ruling = _consolidation(_merge_verdict())
    folded = apply_consolidation(consolidation=ruling, proposal=_proposal())
    report = validate_consolidation_fallback(consolidation=ruling, proposal=folded)
    assert report["unmerged_matches"] == []


def test_relation_may_point_at_a_committed_viewpoint_this_batch_does_not_touch():
    """Drawing a boundary against a neighbour is precisely this case.

    The neighbour is not under review and holds no Claim from this batch, so
    requiring a component before its endpoint resolves would refuse the edge
    the refused-merge rule exists to obtain.
    """

    proposal = _proposal(
        viewpoint_relations=[
            {
                "source_local_key": "ROCK-NOT-PETER",
                "target_viewpoint_revision_id": "CVR-untouched",
                "relation_type": "specializes",
                "reason": "候选是既有观点在更窄经文范围上的具体化。",
            }
        ]
    )
    registry = [
        *REGISTRY_CONTEXT_ROCK,
        {
            **REGISTRY_CONTEXT_ROCK[0],
            "viewpoint_id": "CV-2",
            "viewpoint_revision_id": "CVR-untouched",
            "core_proposition": "本批没有任何 component 指向它。",
        },
    ]
    review = CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": sha256_json(proposal.model_dump(mode="json")),
            "change_reviews": [
                {"claim_id": "C1", "component_index": index, "decision": "pass", "reason": "通过"}
                for index in range(2)
            ],
            "novelty_review": {"status": "pass", "reason": "没有遗漏的新观点"},
        }
    )
    package = compile_cvp_batch_package(
        proposal=proposal,
        review=review,
        deterministic_validation_sha256="validation-sha",
        scope_manifest_sha256="scope-manifest-sha",
        claims=[_claim("C1", ROCK_STATEMENT)],
        registry_context=registry,
        proposal_artifact_sha256="proposal-call-sha",
        review_artifact_sha256="review-call-sha",
        proposer_model_id="gpt-5.6-sol/high",
        reviewer_model_id="claude-opus-5/high",
        decided_at="2026-08-24T12:00:00Z",
    )
    relation = package["viewpoint_relations"][0]
    assert relation["target_viewpoint_id"] == "CV-2"
    assert relation["validated_target_viewpoint_revision_id"] == "CVR-untouched"


def test_written_revisions_carry_no_field_older_readers_reject():
    """Every stored viewpoint model forbids extras, in every deployed version.

    Adding an optional field to `ViewpointRevisionProvenance` put an explicit
    `"revision_reason": null` on four freshly written revisions -- pydantic
    serializes optional fields whether or not they carry anything -- and the
    production Registry views, running code without the field, refused to load
    the collection at all. Pin the provenance shape so the next addition has to
    be a deliberate migration rather than a side effect.
    """

    from backend.api.canonical_repository.knowledge_models import (
        ViewpointRevisionProvenance,
    )

    assert set(ViewpointRevisionProvenance.model_fields) == {
        "basis_identity_decision_ids",
        "review_artifact_sha256",
    }

    proposal = _member_proposal(viewpoint_revisions=[_revision()])
    review = _passing_review(proposal)
    review = CanonicalViewpointReviewResponse.model_validate(
        review.model_dump(mode="json")
        | {
            "revision_reviews": [
                review.revision_reviews[0].model_dump(mode="json")
                | {"confirmed_dependent_ids": []}
            ]
        }
    )
    package = _compile(proposal, review)
    for revision in package["viewpoint_revisions"]:
        assert set(revision["provenance"]) == {
            "basis_identity_decision_ids",
            "review_artifact_sha256",
        }


def test_route_packet_drops_claims_from_sources_holding_no_member():
    """The packet is bounded by the work, not by the corpus.

    An attestation must terminate in an active Claim link of the route's own
    conclusion viewpoint, so a source holding no member Claim cannot appear in
    a legal attestation and its Claims are unreachable cost. Carrying the whole
    scope tied packet size to how much has been transcribed: a 190-Claim
    Matthew 16 scope built 1.34M characters against a 1.05M limit, and the route
    job for that scope could not run at all.
    """

    member = _claim("C1", ROCK_STATEMENT, source_id="S1")
    same_source_bridge = _claim("C2", MODAL_STATEMENT, source_id="S1")
    other_source = _claim("C3", MODAL_STATEMENT, source_id="S2")

    packet = build_registry_route_packet(
        scope_label="matt16-13-18",
        approved_viewpoints=[_approved_viewpoint()],
        claims=[member, same_source_bridge, other_source],
        viewpoint_claim_links=[_registry_link(member)],
        existing_routes=[],
    )

    carried = {item["claim_id"] for item in packet["claim_components"]}
    assert "C1" in carried
    # Bridge and objection material from a source that does hold a member is
    # exactly what the packet exists to show, and stays.
    assert "C2" in carried
    assert "C3" not in carried
    assert {item["claim_id"] for item in packet["claims"]} == {"C1", "C2"}
    assert set(packet["source_revisions"]) == {"S1"}


def test_merged_focal_yields_to_a_focal_that_already_cited_the_viewpoint():
    """A proposal can name one viewpoint twice without noticing.

    Round 2's second batch cited the committed "教会执行天上已定的标准" as a
    focal and, in the same proposal, offered a candidate saying the same thing.
    Consolidation caught that they are one, and the structure was left holding
    the viewpoint in two roles. The direct citation was made against the
    committed viewpoint itself, so it keeps its role; the merged focal's role
    belonged to a viewpoint that turned out not to exist.
    """

    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_consolidation,
    )

    proposal = _proposal(
        new_viewpoint_candidates=[_candidate("ROCK-NOT-PETER")],
        structures=[
            {
                "central_synthesis": "本批共同界定的中心。",
                "focal": [
                    {"viewpoint_revision_id": "CVR-1", "structure_role": "positive_identification"},
                    {"local_key": "ROCK-NOT-PETER", "structure_role": "application"},
                ],
                "unresolved_items": [],
                "reason": "这批材料合起来在论证什么。",
            }
        ],
    )
    folded = apply_consolidation(
        consolidation=_consolidation(
            {
                "local_key": "ROCK-NOT-PETER",
                "verdict": "matches_existing",
                "target_viewpoint_revision_id": "CVR-1",
                "reason": "与既有观点同一真值条件。",
            }
        ),
        proposal=proposal,
    )

    focal = folded.structures[0].focal
    assert len(focal) == 1
    assert focal[0].viewpoint_revision_id == "CVR-1"
    assert focal[0].structure_role == "positive_identification"


def test_two_merged_focals_colliding_still_stops():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        apply_consolidation,
    )

    proposal = _proposal(
        new_viewpoint_candidates=[_candidate("ROCK-NOT-PETER"), _candidate("SPARE")],
        structures=[
            {
                "central_synthesis": "本批共同界定的中心。",
                "focal": [
                    {"local_key": "ROCK-NOT-PETER", "structure_role": "application"},
                    {"local_key": "SPARE", "structure_role": "qualification"},
                ],
                "unresolved_items": [],
                "reason": "这批材料合起来在论证什么。",
            }
        ],
    )
    with pytest.raises(BatchResolutionError, match="one viewpoint two focal roles"):
        apply_consolidation(
            consolidation=_consolidation(
                {"local_key": "ROCK-NOT-PETER", "verdict": "matches_existing",
                 "target_viewpoint_revision_id": "CVR-1", "reason": "同一条。"},
                {"local_key": "SPARE", "verdict": "matches_existing",
                 "target_viewpoint_revision_id": "CVR-1", "reason": "也是同一条。"},
            ),
            proposal=proposal,
        )
