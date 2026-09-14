from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

import backend.pipeline.claim_evidence_reciprocity_source_queue_runner as source_queue_module
from backend.api.canonical_repository.postgres_store import (
    ChangeOperation,
    ChangeSetPlan,
    _build_claim_evidence_human_authority_snapshot,
    _validate_claim_evidence_source_queue_apply,
    record_content_sha,
    sha256_json,
)
from backend.api.canonical_repository.reviewed_candidate_contract import (
    reseal_after_relation_id_migration,
    reviewed_candidate_artifact_sha256,
)
from backend.pipeline.claim_evidence_reciprocity_authority import (
    AUTHORITY_MANIFEST_SCHEMA_VERSION,
    AUTHORITY_VALIDATION_SCHEMA_VERSION,
    QUEUE_EXACT_REPLAY,
    QUEUE_MANUAL,
    QUEUE_SOURCE_RERUN,
    SOURCE_QUEUE_SCHEMA_VERSION,
    seal_authority_artifact,
    validate_authority_manifest,
)
from backend.pipeline.claim_evidence_reciprocity_source_queue_runner import (
    ClaimEvidenceReciprocitySourceQueueError,
    REASON_HUMAN_IMPACT,
    REASON_MANUAL_QUEUE,
    WORK_MANUAL,
    WKP364_EXACT_REPLAY_SOURCE_KIND,
    assess_current_human_authority_impact,
    build_current_human_authority_snapshot,
    build_source_queue_execution_plan,
    build_source_queue_result,
    consume_source_work_unit,
    main as source_queue_main,
    record_source_rerun_candidate,
    validate_current_human_authority_snapshot,
    validate_source_queue_execution_plan,
    validate_source_queue_result,
)
from backend.pipeline.claim_evidence_reciprocity_repair import (
    ClaimEvidenceReciprocityRepairError,
    apply_sealed_plan,
    build_reciprocity_audit,
    build_repair_plan,
    seal_artifact,
)
from backend.pipeline.relation_id_namespace import (
    migrate_legacy_cross_section_relation_ids,
    source_namespace,
)


TRANSCRIPT_ID = "SERMON-ONE"
NAMESPACE = source_namespace(TRANSCRIPT_ID)
CLAIM_ID = f"{NAMESPACE}-CL1"
EVIDENCE_ID = f"{NAMESPACE}-E1"
PAIR_ID = f"PAIR-{sha256_json([CLAIM_ID, EVIDENCE_ID])[:24]}"
SOURCE_ID = "SRC-ONE"


def _reviewed_package(
    *,
    transcript_id: str = TRANSCRIPT_ID,
    source_id: str = SOURCE_ID,
    claim_id: str = CLAIM_ID,
    evidence_id: str = EVIDENCE_ID,
) -> dict[str, Any]:
    namespace = source_namespace(transcript_id)
    body_sha = hashlib.sha256(f"body:{transcript_id}".encode()).hexdigest()
    package: dict[str, Any] = {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": f"PKG-{transcript_id}",
        "complete": True,
        "source_documents": [
            {
                "source_id": source_id,
                "source_type": "sermon_transcript",
                "transcript_id": transcript_id,
                "source_sha256": body_sha,
                "source_body_sha256": body_sha,
                "locator_space": "spoken_body_v1",
                "extraction_record_namespace": namespace,
            }
        ],
        "source_fragments": [
            {
                "fragment_id": f"FR-{namespace}-1",
                "source_id": source_id,
                "verbatim_excerpt": "source words",
                "source_sha256": body_sha,
            }
        ],
        "evidence_steps": [
            {
                "evidence_step_id": evidence_id,
                "source_fragment_ids": [f"FR-{namespace}-1"],
                "source_document_ids": [source_id],
                "statement": "evidence one",
                "produced_claim_ids": [claim_id],
            }
        ],
        "claims": [
            {
                "claim_id": claim_id,
                "source_document_ids": [source_id],
                "statement": "claim one",
                "claim_type": "explicit_claim",
                "evidence_step_ids": [evidence_id],
                "review_status": "ai_consensus_reviewed",
                "reviewed_by": "reviewer",
                "reviewed_at": "2026-09-13T00:00:00+00:00",
                "review_note": "independent review passed",
            }
        ],
        "knowledge_relations": [],
        "claim_relations": [],
        "extraction": {
            "fingerprint_sha256": "e" * 64,
            "record_namespace": namespace,
        },
        "consensus_application": {
            "schema_version": "wang_ai_consensus_application_v2",
            "scope_kind": "source_scoped",
            "review_completion": "complete",
            "review_artifact_sha256": "a" * 64,
            "review_fingerprint": "review-fingerprint",
            "adjudication_artifact_sha256": "b" * 64,
            "adjudication_fingerprint": "adjudication-fingerprint",
            "overrides_artifact_sha256": "c" * 64,
            "approval_status": "not_human_approved",
            "applied_claim_ids": [],
            "merged_claim_ids": {},
            "final_review_status_counts": {"ai_consensus_reviewed": 1},
            "review_resolutions": [
                {
                    "schema_version": "wang_claim_ai_review_provenance_v1",
                    "claim_id": claim_id,
                    "independent_review_decision": "pass",
                    "adjudication_status": "not_required",
                    "target_review_status": "ai_consensus_reviewed",
                    "reviewer_id": "reviewer",
                    "reason": "independent review passed",
                    "approval_status": "not_human_approved",
                }
            ],
        },
    }
    package["consensus_application"]["artifact_sha256"] = (
        reviewed_candidate_artifact_sha256(package)
    )
    return package


def _active_source(package: dict[str, Any], *, revision: int = 7) -> dict[str, Any]:
    payload = deepcopy(package["source_documents"][0])
    return {
        "collection": "source_documents",
        "object_id": payload["source_id"],
        "revision": revision,
        "content_sha256": record_content_sha(payload),
        "payload": payload,
    }


def _eligible_authority(
    tmp_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    package = _reviewed_package()
    path = tmp_path / "reviewed.json"
    path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    raw = path.read_bytes()
    migrated, migration = migrate_legacy_cross_section_relation_ids(package)
    effective = reseal_after_relation_id_migration(package, migrated, migration)
    effective_sha = sha256_json(effective)
    source = _active_source(package)
    historical = {
        "change_set_id": "KCS-HISTORICAL",
        "fingerprint_sha256": "f" * 64,
        "source_kind": "knowledge_package",
        "source_sha256": effective_sha,
        "status": "applied",
    }
    ledger = {
        historical["change_set_id"]: {
            **historical,
            "applied_at": "2026-09-12T00:00:00+00:00",
            "metadata": {
                "upstream_reviewed_candidate_artifact_sha256": package[
                    "consensus_application"
                ]["artifact_sha256"],
                "effective_reviewed_candidate_artifact_sha256": effective[
                    "consensus_application"
                ]["artifact_sha256"],
                "relation_id_namespace_migration": migration,
            },
        }
    }
    document = package["source_documents"][0]
    manifest = seal_authority_artifact(
        {
            "schema_version": AUTHORITY_MANIFEST_SCHEMA_VERSION,
            "packages": [
                {
                    "authority_unit_id": "AUTH-ONE",
                    "path": str(path),
                    "raw_sha256": hashlib.sha256(raw).hexdigest(),
                    "input_canonical_sha256": sha256_json(package),
                    "effective_canonical_sha256": effective_sha,
                    "upstream_reviewed_candidate_artifact_sha256": package[
                        "consensus_application"
                    ]["artifact_sha256"],
                    "effective_reviewed_candidate_artifact_sha256": effective[
                        "consensus_application"
                    ]["artifact_sha256"],
                    "relation_id_namespace_migration": migration,
                    "historical_change_set": historical,
                    "source_generations": [
                        {
                            "source_type": document["source_type"],
                            "row_key": document["transcript_id"],
                            "active_source_document_id": SOURCE_ID,
                            "expected_revision": source["revision"],
                            "expected_content_sha256": source["content_sha256"],
                            "source_body_sha256": document["source_body_sha256"],
                            "extraction_record_namespace": document[
                                "extraction_record_namespace"
                            ],
                        }
                    ],
                }
            ],
        }
    )
    authority = validate_authority_manifest(
        manifest,
        historical_change_sets=ledger,
        active_source_documents=[source],
    )
    return authority, package, [source], manifest


def _empty_authority() -> dict[str, Any]:
    return seal_authority_artifact(
        {
            "schema_version": AUTHORITY_VALIDATION_SCHEMA_VERSION,
            "status": "eligible",
            "authority_manifest_sha256": "a" * 64,
            "packages": [],
            "counts": {
                "packages": 0,
                "replay_eligible": 0,
                "blocked": 0,
                "sources": 0,
            },
        }
    )


def _queue(
    *,
    action: str,
    authority: dict[str, Any],
    pair_id: str = PAIR_ID,
    sources: list[dict[str, str]] | None = None,
    authority_ids: list[str] | None = None,
) -> dict[str, Any]:
    source_rows = sources or [
        {"source_type": "sermon_transcript", "row_key": TRANSCRIPT_ID}
    ]
    if len(source_rows) == 1:
        group_key = f"source:{sha256_json([source_rows[0]['source_type'], source_rows[0]['row_key']])}"
    else:
        group_key = f"aggregate:{(authority_ids or ['AUTH-ONE'])[0]}"
    queue_id = f"SRCQ-{sha256_json(group_key)[:24]}"
    task = {
        "queue_id": queue_id,
        "group_key": group_key,
        "source_identities": source_rows,
        "action": action,
        "authority_unit_ids": authority_ids or [],
        "pair_ids": [pair_id],
        "mismatch_counts": {"claim_only": 1},
        "reason_codes": ["test"],
    }
    return seal_authority_artifact(
        {
            "schema_version": SOURCE_QUEUE_SCHEMA_VERSION,
            "freeze_artifact_sha256": "1" * 64,
            "audit_artifact_sha256": "2" * 64,
            "pair_action_manifest_sha256": "3" * 64,
            "authority_validation_sha256": authority["artifact_sha256"],
            "source_tasks": [task],
            "pair_assignments": [{"pair_id": pair_id, "queue_id": queue_id}],
            "counts": {
                "blocking_pairs": 1,
                "source_tasks": 1,
                "exact_replay": int(action == QUEUE_EXACT_REPLAY),
                "source_rerun": int(action == QUEUE_SOURCE_RERUN),
                "manual": int(action == QUEUE_MANUAL),
            },
        }
    )


def _empty_queue(authority: dict[str, Any]) -> dict[str, Any]:
    return seal_authority_artifact(
        {
            "schema_version": SOURCE_QUEUE_SCHEMA_VERSION,
            "freeze_artifact_sha256": "1" * 64,
            "audit_artifact_sha256": "2" * 64,
            "pair_action_manifest_sha256": sha256_json([]),
            "authority_validation_sha256": authority["artifact_sha256"],
            "source_tasks": [],
            "pair_assignments": [],
            "counts": {
                "blocking_pairs": 0,
                "source_tasks": 0,
                "exact_replay": 0,
                "source_rerun": 0,
                "manual": 0,
            },
        }
    )


def _repair_endpoint(
    collection: str, object_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    revision = 1
    value = {**payload, "revision": revision}
    content_sha = record_content_sha(value)
    change_set_id = f"KCS-{collection}-{object_id}"
    return {
        "collection": collection,
        "object_id": object_id,
        "revision": revision,
        "content_sha256": content_sha,
        "payload": value,
        "object_version": {
            "revision": revision,
            "content_sha256": content_sha,
            "payload": value,
            "change_set_id": change_set_id,
        },
        "producer_change_set": {
            "change_set_id": change_set_id,
            "fingerprint_sha256": hashlib.sha256(change_set_id.encode()).hexdigest(),
            "package_id": f"PKG-{object_id}",
            "source_kind": "knowledge_package",
            "source_sha256": "c" * 64,
            "status": "applied",
        },
        "producer_operation": {
            "change_set_id": change_set_id,
            "operation_index": 0,
            "operation": "create",
            "collection": collection,
            "object_id": object_id,
            "before_sha256": None,
            "after_sha256": content_sha,
            "before_revision": None,
            "after_revision": revision,
            "details": {},
        },
        "review_events": [],
        "source_lineage": {},
    }


def _human_snapshot() -> dict[str, Any]:
    ledger = {
        "schema_version": "wang_review_event_ledger_snapshot_v1",
        "count": 0,
        "rows_sha256": sha256_json([]),
    }
    ledger["snapshot_sha256"] = sha256_json(ledger)
    return build_current_human_authority_snapshot(
        [],
        review_event_ledger_count=0,
        review_event_ledger_snapshot=ledger,
    )


def _fixture_review_ledger(count: int) -> dict[str, Any]:
    ledger = {
        "schema_version": "wang_review_event_ledger_snapshot_v1",
        "count": count,
        "rows_sha256": sha256_json({"fixture_review_event_count": count}),
    }
    ledger["snapshot_sha256"] = sha256_json(ledger)
    return ledger


def _operation(
    operation: str,
    collection: str,
    object_id: str,
    payload: dict[str, Any],
    *,
    before_revision: int | None = None,
) -> ChangeOperation:
    after_revision = 1 if before_revision is None else before_revision + 1
    return ChangeOperation(
        operation=operation,
        collection=collection,
        object_id=object_id,
        before_sha256=None if before_revision is None else "0" * 64,
        after_sha256=record_content_sha(payload),
        before_revision=before_revision,
        after_revision=after_revision,
        payload=deepcopy(payload),
    )


def _change_set(
    *operations: ChangeOperation,
    source_kind: str = "knowledge_package",
    source_sha256: str = "9" * 64,
    package_id: str = "PKG-PLAN",
) -> ChangeSetPlan:
    rows = [
        {
            "operation": row.operation,
            "collection": row.collection,
            "object_id": row.object_id,
            "before_revision": row.before_revision,
            "after_revision": row.after_revision,
            "after_sha256": row.after_sha256,
        }
        for row in operations
    ]
    return ChangeSetPlan(
        change_set_id=f"KCS-{sha256_json(rows)[:24]}",
        fingerprint_sha256=sha256_json(rows),
        package_id=package_id,
        source_kind=source_kind,
        source_sha256=source_sha256,
        operations=tuple(operations),
        unchanged=0,
        ignored_keys=(),
    )


def _guarded_exact_change_set(
    authority: dict[str, Any], *operations: ChangeOperation
) -> ChangeSetPlan:
    unit = authority["packages"][0]
    return _change_set(
        *operations,
        source_kind=WKP364_EXACT_REPLAY_SOURCE_KIND,
        source_sha256=unit["effective_canonical_sha256"],
        package_id=unit["package_id"],
    )


class _Store:
    def __init__(self, generations: list[dict[str, Any]], *, guarded: bool = True):
        self.generations = deepcopy(generations)
        self.apply_calls = 0
        self.guarded = guarded

    def read_claim_evidence_source_generations(
        self, expected: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return deepcopy(self.generations)

    def apply_claim_evidence_source_queue_plan(self, change_set: Any, **kwargs: Any):
        if not self.guarded:
            raise AssertionError("unexpected apply")
        self.apply_calls += 1
        assert kwargs["expected_source_generations"] == self.generations
        assert kwargs["metadata"]["claim_evidence_reciprocity_source_queue"]
        assert kwargs["expected_human_authority_snapshot"]["artifact_sha256"]
        _validate_claim_evidence_source_queue_apply(
            change_set,
            kwargs["metadata"],
            kwargs["expected_source_generations"],
            kwargs["expected_human_authority_snapshot"],
        )
        return {"status": "applied", "change_set_id": change_set.change_set_id}


def _readback(plan: dict[str, Any], work: dict[str, Any]) -> dict[str, Any]:
    return seal_authority_artifact(
        {
            "schema_version": "wang_claim_evidence_reciprocity_source_work_readback_v1",
            "execution_plan_sha256": plan["artifact_sha256"],
            "source_queue_sha256": plan["source_queue_sha256"],
            "work_unit_id": work["work_unit_id"],
            "work_unit_sha256": work["artifact_sha256"],
            "post_authority_bridge_schema_version": (
                "wang_claim_evidence_reciprocity_authority_bridge_v1"
            ),
            "post_authority_bridge_sha256": "4" * 64,
            "post_frozen_input_sha256": "5" * 64,
            "post_audit_sha256": "6" * 64,
            "post_pair_actions_sha256": "7" * 64,
            "resolutions": [
                {"pair_id": pair_id, "resolution": "reciprocal"}
                for pair_id in work["pair_ids"]
            ],
            "unresolved_pair_ids": [],
            "status": "verified",
        }
    )


def test_exact_replay_execution_is_deterministic_and_binds_full_authority(
    tmp_path: Path,
) -> None:
    authority, _package, active, _manifest = _eligible_authority(tmp_path)
    queue = _queue(
        action=QUEUE_EXACT_REPLAY,
        authority=authority,
        authority_ids=["AUTH-ONE"],
    )

    first = build_source_queue_execution_plan(
        queue, authority, active_source_documents=active
    )
    second = build_source_queue_execution_plan(
        queue, authority, active_source_documents=active
    )

    assert first == second
    work = first["work_units"][0]
    assert work["package_binding"]["authority_unit_id"] == "AUTH-ONE"
    assert work["package_binding"]["effective_canonical_sha256"] == authority[
        "packages"
    ][0]["effective_canonical_sha256"]
    assert work["package_binding"]["path"] == authority["packages"][0]["path"]
    assert work["source_generations"][0]["expected_revision"] == 7
    assert validate_source_queue_execution_plan(
        first, source_queue=queue, authority_validation=authority
    ) == first


def test_execution_plan_rejects_cross_artifact_or_nested_tampering(
    tmp_path: Path,
) -> None:
    authority, _package, active, _manifest = _eligible_authority(tmp_path)
    queue = _queue(
        action=QUEUE_EXACT_REPLAY,
        authority=authority,
        authority_ids=["AUTH-ONE"],
    )
    plan = build_source_queue_execution_plan(
        queue, authority, active_source_documents=active
    )
    tampered = deepcopy(plan)
    tampered["work_units"][0]["pair_ids"] = ["PAIR-TAMPERED"]

    with pytest.raises(ClaimEvidenceReciprocitySourceQueueError, match="seal"):
        validate_source_queue_execution_plan(tampered)

    foreign_queue = deepcopy(queue)
    foreign_queue["freeze_artifact_sha256"] = "8" * 64
    foreign_queue = seal_authority_artifact(foreign_queue)
    with pytest.raises(
        ClaimEvidenceReciprocitySourceQueueError, match="another source queue"
    ):
        validate_source_queue_execution_plan(plan, source_queue=foreign_queue)

    coordinated = deepcopy(plan)
    coordinated["work_units"] = []
    coordinated["work_unit_manifest_sha256"] = sha256_json([])
    coordinated["counts"] = {
        "work_units": 0,
        "exact_replay": 0,
        "source_rerun": 0,
        "manual": 0,
        "dispatch_binding_required": 0,
    }
    coordinated = seal_authority_artifact(coordinated)
    with pytest.raises(
        ClaimEvidenceReciprocitySourceQueueError,
        match="differs from its deterministic source queue derivation",
    ):
        validate_source_queue_execution_plan(coordinated)


def test_exact_replay_rereads_and_rejects_changed_package_bytes(
    tmp_path: Path,
) -> None:
    authority, _package, active, _manifest = _eligible_authority(tmp_path)
    queue = _queue(
        action=QUEUE_EXACT_REPLAY,
        authority=authority,
        authority_ids=["AUTH-ONE"],
    )
    plan = build_source_queue_execution_plan(
        queue, authority, active_source_documents=active
    )
    work = plan["work_units"][0]
    Path(work["package_binding"]["path"]).write_text("{}", encoding="utf-8")

    with pytest.raises(
        ClaimEvidenceReciprocitySourceQueueError, match="raw SHA changed"
    ):
        consume_source_work_unit(
            plan,
            work["work_unit_id"],
            authority_validation=authority,
            store=_Store(work["source_generations"]),
            human_authority_snapshot=_human_snapshot(),
        )


def test_aggregate_authority_cannot_be_split_into_one_source() -> None:
    pair = {"claim_id": CLAIM_ID, "evidence_step_id": EVIDENCE_ID}
    second_package = _reviewed_package(
        transcript_id="SERMON-TWO",
        source_id="SRC-TWO",
        claim_id=f"{source_namespace('SERMON-TWO')}-CL1",
        evidence_id=f"{source_namespace('SERMON-TWO')}-E1",
    )
    active = [_active_source(_reviewed_package())]
    active.append(_active_source(second_package, revision=2))
    source_generations = []
    for row in active:
        payload = row["payload"]
        source_generations.append(
            {
                "source_type": payload["source_type"],
                "row_key": payload["transcript_id"],
                "active_source_document_id": payload["source_id"],
                "expected_revision": row["revision"],
                "expected_content_sha256": row["content_sha256"],
                "source_body_sha256": payload["source_body_sha256"],
                "extraction_record_namespace": payload[
                    "extraction_record_namespace"
                ],
            }
        )
    authority = seal_authority_artifact(
        {
            "schema_version": AUTHORITY_VALIDATION_SCHEMA_VERSION,
            "status": "eligible",
            "authority_manifest_sha256": "a" * 64,
            "packages": [
                {
                    "authority_unit_id": "AUTH-AGGREGATE",
                    "replay_eligible": True,
                    "source_identities": [
                        {"source_type": row["source_type"], "row_key": row["row_key"]}
                        for row in source_generations
                    ],
                    "source_generations": source_generations,
                    "claim_evidence_pairs": [pair],
                    "claim_evidence_pairs_sha256": sha256_json([pair]),
                    "scope_kind": "research_batch_aggregate",
                    "package_id": "PKG-AGG",
                    "path": "/tmp/aggregate.json",
                    "raw_sha256": "b" * 64,
                    "input_canonical_sha256": "c" * 64,
                    "effective_canonical_sha256": "d" * 64,
                    "upstream_reviewed_candidate_artifact_sha256": "e" * 64,
                    "effective_reviewed_candidate_artifact_sha256": "f" * 64,
                    "relation_id_namespace_migration": {},
                    "historical_change_set": {
                        "change_set_id": "KCS-AGG",
                        "source_kind": "knowledge_package",
                    },
                }
            ],
            "counts": {
                "packages": 1,
                "replay_eligible": 1,
                "blocked": 0,
                "sources": 2,
            },
        }
    )
    queue = _queue(
        action=QUEUE_EXACT_REPLAY,
        authority=authority,
        authority_ids=["AUTH-AGGREGATE"],
    )

    with pytest.raises(
        ClaimEvidenceReciprocitySourceQueueError, match="splits or changes"
    ):
        build_source_queue_execution_plan(
            queue, authority, active_source_documents=active
        )


def test_rerun_dispatch_never_applies_and_candidate_receipt_binds_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _reviewed_package()
    active = [_active_source(package)]
    authority = _empty_authority()
    queue = _queue(action=QUEUE_SOURCE_RERUN, authority=authority)
    batch_path = tmp_path / "batch.json"
    batch_path.write_text("{}", encoding="utf-8")
    output_root = tmp_path / "output"
    plan = build_source_queue_execution_plan(
        queue,
        authority,
        active_source_documents=active,
        rerun_bindings={
            TRANSCRIPT_ID: {
                "batch_path": str(batch_path),
                "batch_sha256": hashlib.sha256(batch_path.read_bytes()).hexdigest(),
                "output_root": str(output_root),
            }
        },
    )
    work = plan["work_units"][0]
    assert "--apply" not in work["dispatch"]["command"]
    execution_path = tmp_path / "execution.json"
    dispatch_preview_path = tmp_path / "dispatch-preview.json"
    execution_path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setattr(
        "backend.pipeline.claim_evidence_reciprocity_source_queue_runner.subprocess.run",
        lambda *args, **kwargs: pytest.fail(
            "dispatch preview must not start a subprocess"
        ),
    )
    assert source_queue_main(
        [
            "dispatch",
            "--execution-plan",
            str(execution_path),
            "--work-unit",
            work["work_unit_id"],
            "--output",
            str(dispatch_preview_path),
        ]
    ) == 0
    dispatch_preview = json.loads(
        dispatch_preview_path.read_text(encoding="utf-8")
    )
    assert dispatch_preview["status"] == "preview"
    assert dispatch_preview["ingest_apply"] is False
    assert "--apply" not in dispatch_preview["command"]
    candidate_path = Path(work["dispatch"]["reviewed_candidate_path"])
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_text(json.dumps(package), encoding="utf-8")
    manifest_path = output_root / "run-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "status": "partial_selection",
                "selected_stage": "all",
                "output_root": str(output_root),
                "ingest_applies": False,
                "members": [{"source": TRANSCRIPT_ID, "status": "completed"}],
            }
        ),
        encoding="utf-8",
    )

    receipt = record_source_rerun_candidate(
        plan, work["work_unit_id"], run_manifest_path=manifest_path
    )

    assert receipt["status"] == "candidate_ready"
    assert receipt["work_unit_sha256"] == work["artifact_sha256"]
    candidate_path.write_text("{}", encoding="utf-8")
    with pytest.raises(
        ClaimEvidenceReciprocitySourceQueueError,
        match="reviewed candidate bytes changed",
    ):
        consume_source_work_unit(
            plan,
            work["work_unit_id"],
            authority_validation=authority,
            store=_Store(work["source_generations"]),
            human_authority_snapshot=_human_snapshot(),
            rerun_candidate_receipt=receipt,
        )


def test_human_claim_projection_and_id_replacement_are_manual() -> None:
    claim = {
        "claim_id": CLAIM_ID,
        "review_status": "human_approved",
        "evidence_step_ids": [EVIDENCE_ID],
        "revision": 4,
    }
    event = {
        "review_event_id": "REV-HUMAN-1",
        "collection": "claims",
        "object_id": CLAIM_ID,
        "object_revision": 4,
        "reviewer_kind": "human",
        "decision": "human_approved",
        "artifact": {"change_set_id": "KCS-HUMAN"},
    }
    snapshot = build_current_human_authority_snapshot(
        [
            {
                "collection": "claims",
                "object_id": CLAIM_ID,
                "revision": 4,
                "content_sha256": record_content_sha(claim),
                "payload": claim,
                "producer_change_set_id": "KCS-HUMAN",
                "review_events": [event],
            }
        ],
        review_event_ledger_count=1,
        review_event_ledger_snapshot=_fixture_review_ledger(1),
    )
    changed = {**claim, "evidence_step_ids": []}
    update = _change_set(
        _operation("update", "claims", CLAIM_ID, changed, before_revision=4)
    )
    update_impact = assess_current_human_authority_impact(update, snapshot)
    assert update_impact["status"] == "blocked"
    assert update_impact["blockers"][0]["reason_code"] == (
        "human_settled_claim_evidence_projection_change"
    )

    created = {
        "claim_id": f"{CLAIM_ID}-REPLACEMENT",
        "review_status": "ai_consensus_reviewed",
        "evidence_step_ids": [],
    }
    replacement = _change_set(
        _operation("retire", "claims", CLAIM_ID, claim, before_revision=4),
        _operation("create", "claims", created["claim_id"], created),
    )
    replacement_impact = assess_current_human_authority_impact(
        replacement, snapshot
    )
    assert replacement_impact["status"] == "blocked"
    assert replacement_impact["blockers"][0]["reason_code"] == (
        "human_settled_record_id_replacement"
    )
    assert replacement_impact["blockers"][0]["replacement_create_ids"] == [
        created["claim_id"]
    ]


def test_current_human_evidence_event_is_protected_without_payload_status() -> None:
    evidence = {
        "evidence_step_id": EVIDENCE_ID,
        "produced_claim_ids": [CLAIM_ID],
        "statement": "human reviewed evidence",
    }
    snapshot = build_current_human_authority_snapshot(
        [
            {
                "collection": "evidence_steps",
                "object_id": EVIDENCE_ID,
                "revision": 2,
                "content_sha256": record_content_sha(evidence),
                "payload": evidence,
                "producer_change_set_id": "KCS-HUMAN-EVIDENCE",
                "review_events": [
                    {
                        "review_event_id": "REV-HUMAN-EVIDENCE",
                        "collection": "evidence_steps",
                        "object_id": EVIDENCE_ID,
                        "object_revision": 2,
                        "reviewer_kind": "human",
                        "decision": "approved",
                        "artifact": {"change_set_id": "KCS-HUMAN-EVIDENCE"},
                    }
                ],
            }
        ],
        review_event_ledger_count=1,
    )
    plan = _change_set(
        _operation(
            "retire",
            "evidence_steps",
            EVIDENCE_ID,
            evidence,
            before_revision=2,
        )
    )

    impact = assess_current_human_authority_impact(plan, snapshot)

    assert impact["status"] == "blocked"
    assert impact["blockers"][0]["collection"] == "evidence_steps"
    assert snapshot["protected_records"][0]["human_decision"] == "approved"


def test_bare_superseded_status_is_not_inferred_as_human_authority() -> None:
    claim = {
        "claim_id": CLAIM_ID,
        "review_status": "superseded",
        "evidence_step_ids": [],
    }

    snapshot = build_current_human_authority_snapshot(
        [
            {
                "collection": "claims",
                "object_id": CLAIM_ID,
                "revision": 4,
                "content_sha256": record_content_sha(claim),
                "payload": claim,
                "producer_change_set_id": "KCS-AI-SUPERSEDE",
                "review_events": [],
                "retired": True,
            }
        ],
        review_event_ledger_count=0,
    )

    assert snapshot["protected_records"] == []
    assert snapshot["scanned_record_count"] == 1


def test_guarded_apply_requires_store_owned_review_event_root() -> None:
    preview_fixture = build_current_human_authority_snapshot(
        [], review_event_ledger_count=0
    )

    assert validate_current_human_authority_snapshot(preview_fixture) == (
        preview_fixture
    )
    with pytest.raises(
        ClaimEvidenceReciprocitySourceQueueError,
        match="full store-owned review-event ledger root",
    ):
        validate_current_human_authority_snapshot(
            preview_fixture, require_store_roots=True
        )

    store_snapshot = _build_claim_evidence_human_authority_snapshot([], [], [])
    assert validate_current_human_authority_snapshot(
        store_snapshot, require_store_roots=True
    ) == store_snapshot


def test_consumer_checks_source_cas_and_human_impact_before_apply(
    tmp_path: Path,
) -> None:
    authority, _package, active, _manifest = _eligible_authority(tmp_path)
    queue = _queue(
        action=QUEUE_EXACT_REPLAY,
        authority=authority,
        authority_ids=["AUTH-ONE"],
    )
    plan = build_source_queue_execution_plan(
        queue, authority, active_source_documents=active
    )
    work = plan["work_units"][0]
    drifted = deepcopy(work["source_generations"])
    drifted[0]["expected_revision"] += 1
    store = _Store(drifted)

    with pytest.raises(
        ClaimEvidenceReciprocitySourceQueueError, match="generation changed"
    ):
        consume_source_work_unit(
            plan,
            work["work_unit_id"],
            authority_validation=authority,
            store=store,
            human_authority_snapshot=_human_snapshot(),
            apply=True,
            supersede_planner=lambda *args, **kwargs: pytest.fail(
                "planner must not run after CAS drift"
            ),
        )


def test_guarded_apply_rejects_execution_not_derived_from_authority_bridge(
    tmp_path: Path,
) -> None:
    authority, _package, active, _manifest = _eligible_authority(tmp_path)
    queue = _queue(
        action=QUEUE_EXACT_REPLAY,
        authority=authority,
        authority_ids=["AUTH-ONE"],
    )
    plan = build_source_queue_execution_plan(
        queue, authority, active_source_documents=active
    )
    work = plan["work_units"][0]
    payload = {"fragment_id": "FR-NEW", "statement": "new"}
    change_set = _guarded_exact_change_set(
        authority,
        _operation("create", "source_fragments", "FR-NEW", payload)
    )
    store = _Store(work["source_generations"])
    planner_calls = 0

    def planner(*args: Any, **kwargs: Any) -> tuple[Any, ...]:
        nonlocal planner_calls
        planner_calls += 1
        assert kwargs["source_kind"] == WKP364_EXACT_REPLAY_SOURCE_KIND
        return (change_set, {}, [], [], None, None, None, None)

    with pytest.raises(
        ClaimEvidenceReciprocitySourceQueueError,
        match="execution derived from an authority bridge",
    ):
        consume_source_work_unit(
            plan,
            work["work_unit_id"],
            authority_validation=authority,
            store=store,
            human_authority_snapshot=_human_snapshot(),
            apply=True,
            supersede_planner=planner,
            completion_readback=lambda: _readback(plan, work),
        )

    assert planner_calls == 1
    assert store.apply_calls == 0


def test_apply_fails_closed_without_dedicated_locked_store_method(
    tmp_path: Path,
) -> None:
    authority, _package, active, _manifest = _eligible_authority(tmp_path)
    queue = _queue(
        action=QUEUE_EXACT_REPLAY,
        authority=authority,
        authority_ids=["AUTH-ONE"],
    )
    plan = build_source_queue_execution_plan(
        queue, authority, active_source_documents=active
    )
    work = plan["work_units"][0]
    change_set = _guarded_exact_change_set(
        authority,
        _operation(
            "create",
            "source_fragments",
            "FR-LOCK-REQUIRED",
            {"fragment_id": "FR-LOCK-REQUIRED", "statement": "new"},
        ),
    )

    class PreviewOnlyStore:
        def read_claim_evidence_source_generations(
            self, expected: list[dict[str, Any]]
        ) -> list[dict[str, Any]]:
            return deepcopy(expected)

    with pytest.raises(
        ClaimEvidenceReciprocitySourceQueueError,
        match="locked apply_claim_evidence_source_queue_plan",
    ):
        consume_source_work_unit(
            plan,
            work["work_unit_id"],
            authority_validation=authority,
            store=PreviewOnlyStore(),
            human_authority_snapshot=_human_snapshot(),
            apply=True,
            supersede_planner=lambda *args, **kwargs: (
                change_set,
                {},
                [],
                [],
                None,
                None,
                None,
                None,
            ),
            completion_readback=lambda: _readback(plan, work),
        )


def test_exact_replay_rejects_planner_returning_generic_source_kind(
    tmp_path: Path,
) -> None:
    authority, package, active, _manifest = _eligible_authority(tmp_path)
    queue = _queue(
        action=QUEUE_EXACT_REPLAY,
        authority=authority,
        authority_ids=["AUTH-ONE"],
    )
    execution = build_source_queue_execution_plan(
        queue, authority, active_source_documents=active
    )
    work = execution["work_units"][0]
    generic = _change_set(
        _operation(
            "create",
            "source_fragments",
            "FR-GENERIC",
            {"fragment_id": "FR-GENERIC", "statement": "unsafe generic plan"},
        ),
        source_kind="knowledge_package",
        source_sha256=authority["packages"][0]["effective_canonical_sha256"],
        package_id=package["package_id"],
    )

    with pytest.raises(
        ClaimEvidenceReciprocitySourceQueueError,
        match="ChangeSet identity differs",
    ):
        consume_source_work_unit(
            execution,
            work["work_unit_id"],
            authority_validation=authority,
            store=_Store(work["source_generations"]),
            human_authority_snapshot=_human_snapshot(),
            supersede_planner=lambda *args, **kwargs: (
                generic,
                {},
                [],
                [],
                None,
                None,
                None,
                None,
            ),
        )


def test_manual_work_never_executes_or_unblocks(tmp_path: Path) -> None:
    authority = _empty_authority()
    package = _reviewed_package()
    queue = _queue(action=QUEUE_MANUAL, authority=authority)
    plan = build_source_queue_execution_plan(
        queue, authority, active_source_documents=[_active_source(package)]
    )
    work = plan["work_units"][0]

    receipt = consume_source_work_unit(
        plan,
        work["work_unit_id"],
        authority_validation=authority,
        store=object(),
        human_authority_snapshot=_human_snapshot(),
        apply=True,
        supersede_planner=lambda *args, **kwargs: pytest.fail(
            "manual work must never call supersede"
        ),
    )

    assert receipt["status"] == WORK_MANUAL
    assert receipt["reason_code"] == REASON_MANUAL_QUEUE
    assert receipt["completion_verified"] is False
    result = build_source_queue_result(plan, [receipt])
    assert result["status"] == "blocked"
    assert result["source_queue_complete"] is False
    assert result["old_repair_plan_apply_allowed"] is False
    execution_path = tmp_path / "manual-execution.json"
    receipt_path = tmp_path / "manual-receipt.json"
    result_path = tmp_path / "manual-result.json"
    execution_path.write_text(json.dumps(plan), encoding="utf-8")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    assert source_queue_main(
        [
            "result",
            "--execution-plan",
            str(execution_path),
            "--receipt",
            str(receipt_path),
            "--output",
            str(result_path),
        ]
    ) == 2
    cli_result = json.loads(result_path.read_text(encoding="utf-8"))
    assert cli_result["status"] == "blocked"
    assert cli_result["old_repair_plan_apply_allowed"] is False


def test_consumer_turns_human_mutating_supersede_plan_into_manual(
    tmp_path: Path,
) -> None:
    authority, _package, active, _manifest = _eligible_authority(tmp_path)
    queue = _queue(
        action=QUEUE_EXACT_REPLAY,
        authority=authority,
        authority_ids=["AUTH-ONE"],
    )
    plan = build_source_queue_execution_plan(
        queue, authority, active_source_documents=active
    )
    work = plan["work_units"][0]
    claim = {
        "claim_id": CLAIM_ID,
        "review_status": "approved",
        "evidence_step_ids": [EVIDENCE_ID],
        "revision": 3,
    }
    snapshot = build_current_human_authority_snapshot(
        [
            {
                "collection": "claims",
                "object_id": CLAIM_ID,
                "revision": 3,
                "content_sha256": record_content_sha(claim),
                "payload": claim,
                "producer_change_set_id": "KCS-HUMAN",
                "review_events": [
                    {
                        "review_event_id": "REV-HUMAN",
                        "collection": "claims",
                        "object_id": CLAIM_ID,
                        "object_revision": 3,
                        "reviewer_kind": "human",
                        "decision": "approved",
                        "artifact": {"change_set_id": "KCS-HUMAN"},
                    }
                ],
            }
        ],
        review_event_ledger_count=1,
        review_event_ledger_snapshot=_fixture_review_ledger(1),
    )
    changed = {**claim, "evidence_step_ids": []}
    change_set = _guarded_exact_change_set(
        authority,
        _operation("update", "claims", CLAIM_ID, changed, before_revision=3)
    )
    store = _Store(work["source_generations"], guarded=False)

    receipt = consume_source_work_unit(
        plan,
        work["work_unit_id"],
        authority_validation=authority,
        store=store,
        human_authority_snapshot=snapshot,
        apply=True,
        supersede_planner=lambda *args, **kwargs: (
            change_set,
            {},
            [],
            [],
            None,
            None,
            None,
            None,
        ),
    )

    assert receipt["status"] == WORK_MANUAL
    assert receipt["reason_code"] == REASON_HUMAN_IMPACT
    assert store.apply_calls == 0


def test_result_rejects_receipt_from_another_artifact(
    tmp_path: Path,
) -> None:
    authority, _package, active, _manifest = _eligible_authority(tmp_path)
    queue = _queue(
        action=QUEUE_EXACT_REPLAY,
        authority=authority,
        authority_ids=["AUTH-ONE"],
    )
    plan = build_source_queue_execution_plan(
        queue, authority, active_source_documents=active
    )
    work = plan["work_units"][0]
    foreign = seal_authority_artifact(
        {
            "schema_version": "wang_claim_evidence_reciprocity_source_work_receipt_v1",
            "execution_plan_sha256": "0" * 64,
            "source_queue_sha256": plan["source_queue_sha256"],
            "authority_validation_sha256": plan["authority_validation_sha256"],
            "work_unit_id": work["work_unit_id"],
            "work_unit_sha256": work["artifact_sha256"],
            "queue_id": work["queue_id"],
            "action": work["action"],
            "pair_ids": work["pair_ids"],
            "source_generations_sha256": sha256_json(work["source_generations"]),
            "status": "applied",
            "completion_verified": True,
            "reason_code": None,
            "details": {},
        }
    )

    with pytest.raises(
        ClaimEvidenceReciprocitySourceQueueError, match="cross-artifact roots"
    ):
        build_source_queue_result(plan, [foreign])


def test_verified_source_result_cannot_unlock_an_old_blocked_repair_plan(
    tmp_path: Path,
) -> None:
    authority = _empty_authority()
    source_result = build_source_queue_result(
        build_source_queue_execution_plan(
            _empty_queue(authority),
            authority,
            active_source_documents=[_active_source(_reviewed_package())],
        ),
        [],
    )
    records = [
        _repair_endpoint(
            "claims",
            "CL-OLD",
            {
                "claim_id": "CL-OLD",
                "statement": "old claim",
                "claim_type": "teaching",
                "evidence_step_ids": ["EV-OLD"],
                "review_status": "candidate",
            },
        ),
        _repair_endpoint(
            "evidence_steps",
            "EV-OLD",
            {
                "evidence_step_id": "EV-OLD",
                "statement": "old evidence",
                "produced_claim_ids": [],
            },
        ),
    ]
    old_plan = build_repair_plan(build_reciprocity_audit(records), records)
    old_plan_before = deepcopy(old_plan)

    assert source_result["status"] == "verified"
    assert source_result["old_repair_plan_apply_allowed"] is False
    assert old_plan == old_plan_before
    assert old_plan["apply_allowed"] is False
    with pytest.raises(
        ClaimEvidenceReciprocityRepairError,
        match="blocked repair plan cannot be applied",
    ):
        apply_sealed_plan(
            old_plan,
            store=object(),
            backup_dump=tmp_path / "must-not-be-read.dump",
        )


def test_plan_cli_consumes_the_exact_frozen_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority, _package, active, _manifest = _eligible_authority(tmp_path)
    frozen = seal_artifact(
        {
            "schema_version": "wang_claim_evidence_reciprocity_audit_input_v1",
            "active_source_documents": active,
        }
    )
    queue = _queue(
        action=QUEUE_EXACT_REPLAY,
        authority=authority,
        authority_ids=["AUTH-ONE"],
    )
    queue["freeze_artifact_sha256"] = frozen["artifact_sha256"]
    queue = seal_authority_artifact(queue)
    paths = {
        "queue": tmp_path / "queue.json",
        "authority": tmp_path / "authority.json",
        "frozen": tmp_path / "frozen.json",
        "output": tmp_path / "execution.json",
    }
    paths["queue"].write_text(json.dumps(queue), encoding="utf-8")
    paths["authority"].write_text(json.dumps(authority), encoding="utf-8")
    paths["frozen"].write_text(json.dumps(frozen), encoding="utf-8")

    dotenv_calls = 0

    def load_dotenv() -> None:
        nonlocal dotenv_calls
        dotenv_calls += 1

    monkeypatch.setattr(source_queue_module, "load_dotenv", load_dotenv)
    assert source_queue_main(
        [
            "plan",
            "--queue",
            str(paths["queue"]),
            "--authority-validation",
            str(paths["authority"]),
            "--frozen-input",
            str(paths["frozen"]),
            "--output",
            str(paths["output"]),
        ]
    ) == 0
    assert dotenv_calls == 1
    output = json.loads(paths["output"].read_text(encoding="utf-8"))
    assert output["freeze_artifact_sha256"] == frozen["artifact_sha256"]
    assert output["work_units"][0]["package_binding"]["authority_unit_id"] == (
        "AUTH-ONE"
    )
