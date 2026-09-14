from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from backend.api.canonical_repository.postgres_store import (
    build_claim_evidence_active_snapshot,
    build_review_event_ledger_snapshot,
    build_source_lineage_identity_snapshot,
    record_content_sha,
    sha256_json,
)
from backend.api.canonical_repository.reviewed_candidate_contract import (
    reseal_after_relation_id_migration,
    reviewed_candidate_artifact_sha256,
)
from backend.pipeline.claim_evidence_reciprocity_authority import (
    AUTHORITY_MANIFEST_SCHEMA_VERSION,
    QUEUE_EXACT_REPLAY,
    QUEUE_MANUAL,
    QUEUE_SOURCE_RERUN,
    UNRESOLVED_SOURCE_TYPE,
    seal_authority_artifact,
)
from backend.pipeline.claim_evidence_reciprocity_authority_bridge import (
    AUTHORITY_BRIDGE_SCHEMA_VERSION,
    AUTHORITY_CONFLICT,
    AUTHORITY_NO_PACKAGE_PAIR,
    AUTHORITY_PROVED,
    ClaimEvidenceReciprocityAuthorityBridgeError,
    build_authority_bound_audit_and_queue,
    validate_authority_bound_audit_and_queue,
)
from backend.pipeline.claim_evidence_reciprocity_repair import (
    AUDIT_INPUT_SCHEMA_VERSION,
    AUTHORITY_SEALED_REVIEWED_SOURCE,
    DISPOSITION_EXACT_REPLAY,
    DISPOSITION_SOURCE_RERUN,
    build_repair_plan,
    deserialize_repair_plan,
    main as repair_main,
    seal_artifact,
)
from backend.pipeline.claim_evidence_reciprocity_source_queue_runner import (
    main as source_queue_main,
)
from backend.pipeline.relation_id_namespace import (
    migrate_legacy_cross_section_relation_ids,
    source_namespace,
)


TRANSCRIPT_ID = "SERMON-ONE"
NAMESPACE = source_namespace(TRANSCRIPT_ID)
CLAIM_ID = f"{NAMESPACE}-CL1"
EVIDENCE_ID = f"{NAMESPACE}-E1"
SOURCE_ID = "SRC-ONE"


def _reviewed_package(
    package_id: str,
    *,
    claim_id: str = CLAIM_ID,
    evidence_id: str = EVIDENCE_ID,
) -> dict[str, Any]:
    body_sha = "d" * 64
    package: dict[str, Any] = {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": package_id,
        "complete": True,
        "source_documents": [
            {
                "source_id": SOURCE_ID,
                "source_type": "sermon_transcript",
                "transcript_id": TRANSCRIPT_ID,
                "source_sha256": body_sha,
                "source_body_sha256": body_sha,
                "locator_space": "spoken_body_v1",
                "extraction_record_namespace": NAMESPACE,
            }
        ],
        "source_fragments": [
            {
                "fragment_id": f"FR-{NAMESPACE}-1",
                "source_id": SOURCE_ID,
                "verbatim_excerpt": "source words",
                "source_sha256": body_sha,
            }
        ],
        "evidence_steps": [
            {
                "evidence_step_id": evidence_id,
                "source_fragment_ids": [f"FR-{NAMESPACE}-1"],
                "source_document_ids": [SOURCE_ID],
                "statement": "evidence one",
                "produced_claim_ids": [claim_id],
            }
        ],
        "claims": [
            {
                "claim_id": claim_id,
                "source_document_ids": [SOURCE_ID],
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
            "record_namespace": NAMESPACE,
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


def _authority_unit(
    tmp_path: Path,
    name: str,
    package: dict[str, Any],
    source_record: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    raw = path.read_bytes()
    migrated, migration = migrate_legacy_cross_section_relation_ids(package)
    effective = reseal_after_relation_id_migration(package, migrated, migration)
    upstream_sha = package["consensus_application"]["artifact_sha256"]
    effective_artifact_sha = effective["consensus_application"]["artifact_sha256"]
    effective_sha = sha256_json(effective)
    change_set_id = f"KCS-{name}"
    producer = {
        "change_set_id": change_set_id,
        "fingerprint_sha256": hashlib.sha256(name.encode("utf-8")).hexdigest(),
        "package_id": package["package_id"],
        "source_kind": "knowledge_package",
        "source_sha256": effective_sha,
        "status": "applied",
        "summary": {"operations": 2},
        "metadata": {
            "upstream_reviewed_candidate_artifact_sha256": upstream_sha,
            "effective_reviewed_candidate_artifact_sha256": effective_artifact_sha,
            "relation_id_namespace_migration": migration,
        },
        "created_at": "2026-09-12T00:00:00+00:00",
        "applied_at": "2026-09-12T00:01:00+00:00",
    }
    source = package["source_documents"][0]
    spec = {
        "authority_unit_id": f"AUTH-{name}",
        "path": str(path),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "input_canonical_sha256": sha256_json(package),
        "effective_canonical_sha256": effective_sha,
        "upstream_reviewed_candidate_artifact_sha256": upstream_sha,
        "effective_reviewed_candidate_artifact_sha256": effective_artifact_sha,
        "relation_id_namespace_migration": migration,
        "historical_change_set": {
            key: producer[key]
            for key in (
                "change_set_id",
                "fingerprint_sha256",
                "source_kind",
                "source_sha256",
                "status",
            )
        },
        "source_generations": [
            {
                "source_type": source["source_type"],
                "row_key": source["transcript_id"],
                "active_source_document_id": SOURCE_ID,
                "expected_revision": source_record["revision"],
                "expected_content_sha256": source_record["content_sha256"],
                "source_body_sha256": source["source_body_sha256"],
                "extraction_record_namespace": source[
                    "extraction_record_namespace"
                ],
            }
        ],
    }
    return spec, producer


def _other_producer(name: str) -> dict[str, Any]:
    return {
        "change_set_id": f"KCS-{name}",
        "fingerprint_sha256": hashlib.sha256(f"fp:{name}".encode()).hexdigest(),
        "package_id": f"PKG-{name}",
        "source_kind": "knowledge_package",
        "source_sha256": hashlib.sha256(f"source:{name}".encode()).hexdigest(),
        "status": "applied",
        "summary": {"operations": 1},
        "metadata": {},
        "created_at": "2026-09-12T00:00:00+00:00",
        "applied_at": "2026-09-12T00:01:00+00:00",
    }


def _source_record(package: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(package["source_documents"][0])
    return {
        "object_id": SOURCE_ID,
        "revision": 7,
        "content_sha256": record_content_sha(payload),
        "payload": payload,
        "retired": False,
        "retired_at": None,
    }


def _source_lineage(
    producer: dict[str, Any], source_record: dict[str, Any]
) -> dict[str, Any]:
    fragments: list[dict[str, Any]] = []
    documents = [
        {
            "object_id": SOURCE_ID,
            "revision": source_record["revision"],
            "content_sha256": source_record["content_sha256"],
            "retired": False,
            "retired_at": None,
        }
    ]
    return {
        "producer_change_set_id": producer["change_set_id"],
        "producer_package_id": producer["package_id"],
        "producer_source_kind": producer["source_kind"],
        "producer_source_sha256": producer["source_sha256"],
        "source_document_ids": [SOURCE_ID],
        "source_fragment_ids": [],
        "extraction_fingerprints": [],
        "producer_metadata": deepcopy(producer["metadata"]),
        "source_fragments": fragments,
        "source_documents": documents,
        "chain_sha256": sha256_json(
            {"source_fragments": fragments, "source_documents": documents}
        ),
    }


def _active_record(
    collection: str,
    object_id: str,
    payload: dict[str, Any],
    producer: dict[str, Any],
    source_record: dict[str, Any],
) -> dict[str, Any]:
    revision = 3
    content_sha = record_content_sha(payload)
    return {
        "collection": collection,
        "object_id": object_id,
        "revision": revision,
        "content_sha256": content_sha,
        "payload": deepcopy(payload),
        "object_version": {
            "revision": revision,
            "content_sha256": content_sha,
            "payload": deepcopy(payload),
            "change_set_id": producer["change_set_id"],
            "recorded_at": "2026-09-12T00:01:00+00:00",
        },
        "producer_change_set": deepcopy(producer),
        "producer_operation": {
            "change_set_id": producer["change_set_id"],
            "operation_index": 0 if collection == "claims" else 1,
            "operation": "update",
            "collection": collection,
            "object_id": object_id,
            "before_sha256": "0" * 64,
            "after_sha256": content_sha,
            "before_revision": revision - 1,
            "after_revision": revision,
            "details": {},
        },
        "review_events": [],
        "source_lineage": _source_lineage(producer, source_record),
    }


def _frozen_input(
    package: dict[str, Any],
    source_record: dict[str, Any],
    claim_producer: dict[str, Any],
    evidence_producer: dict[str, Any],
    *,
    active_source_documents: list[dict[str, Any]] | None = None,
    review_event_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    claim_payload = deepcopy(package["claims"][0])
    evidence_payload = deepcopy(package["evidence_steps"][0])
    evidence_payload["produced_claim_ids"] = []
    records = [
        _active_record(
            "claims", CLAIM_ID, claim_payload, claim_producer, source_record
        ),
        _active_record(
            "evidence_steps",
            EVIDENCE_ID,
            evidence_payload,
            evidence_producer,
            source_record,
        ),
    ]
    records.sort(key=lambda row: (row["collection"], row["object_id"]))
    snapshot = build_claim_evidence_active_snapshot(
        [
            (
                row["collection"],
                row["object_id"],
                row["revision"],
                row["content_sha256"],
                row["payload"],
            )
            for row in records
        ]
    )
    producers = {
        producer["change_set_id"]: deepcopy(producer)
        for producer in (claim_producer, evidence_producer)
    }
    lineage_rows = [
        {
            "collection": row["collection"],
            "object_id": row["object_id"],
            "source_lineage": row["source_lineage"],
        }
        for row in records
    ]
    review_event_ledger_snapshot = build_review_event_ledger_snapshot(
        review_event_rows or []
    )
    source_lineage_identity_snapshot = build_source_lineage_identity_snapshot(
        [
            {
                "collection": "source_documents",
                "object_id": source_record["object_id"],
                "revision": source_record["revision"],
                "content_sha256": source_record["content_sha256"],
                "retired": source_record["retired"],
            }
        ]
    )
    return seal_artifact(
        {
            "schema_version": AUDIT_INPUT_SCHEMA_VERSION,
            "frozen_at": "2026-09-13T00:00:00+00:00",
            "database_identity": {
                "database_name": "wkp364_fixture",
                "server_version_num": "140017",
            },
            "freeze_transaction": {
                "isolation": "repeatable_read",
                "read_only": True,
                "advisory_lock_key": "wang_knowledge.apply_plan",
            },
            "prerequisites": [],
            "active_records": records,
            "active_snapshot": snapshot,
            "producer_change_sets": [producers[key] for key in sorted(producers)],
            "authority_records": [],
            "product_dependencies": {},
            "product_dependency_records": [],
            "active_source_documents": (
                [deepcopy(source_record)]
                if active_source_documents is None
                else active_source_documents
            ),
            "source_lineage_findings": [],
            "source_lineage_snapshot_sha256": sha256_json(lineage_rows),
            "source_lineage_identity_snapshot": source_lineage_identity_snapshot,
            "review_event_ledger_snapshot": review_event_ledger_snapshot,
            "review_event_ledger_count": review_event_ledger_snapshot["count"],
        }
    )


def _manifest(*specs: dict[str, Any]) -> dict[str, Any]:
    return seal_authority_artifact(
        {
            "schema_version": AUTHORITY_MANIFEST_SCHEMA_VERSION,
            "packages": list(specs),
        }
    )


def test_bridge_builds_authority_bound_audit_queue_and_plan_input(
    tmp_path: Path,
) -> None:
    package = _reviewed_package("PKG-A")
    source_record = _source_record(package)
    spec, producer = _authority_unit(tmp_path, "A", package, source_record)
    frozen = _frozen_input(
        package,
        source_record,
        producer,
        _other_producer("Z"),
    )

    first = build_authority_bound_audit_and_queue(frozen, _manifest(spec))
    second = build_authority_bound_audit_and_queue(frozen, _manifest(spec))

    assert first == second
    assert first["schema_version"] == AUTHORITY_BRIDGE_SCHEMA_VERSION
    assert first["pair_authority_decisions"][0]["reason_code"] == AUTHORITY_PROVED
    assert len(first["authority_records"]) == 1
    assert (
        first["audit"]["pairs"][0]["authority_class"]
        == AUTHORITY_SEALED_REVIEWED_SOURCE
    )
    assert first["audit"]["pairs"][0]["disposition"] == DISPOSITION_EXACT_REPLAY
    assert first["source_work_queue"]["source_tasks"][0]["action"] == (
        QUEUE_EXACT_REPLAY
    )
    assert first["source_work_queue"]["freeze_artifact_sha256"] == (
        frozen["artifact_sha256"]
    )
    assert first["source_work_queue"]["audit_artifact_sha256"] == (
        first["audit"]["artifact_sha256"]
    )
    assert first["roots"]["authority_validation_sha256"] == (
        first["authority_validation"]["artifact_sha256"]
    )
    validate_authority_bound_audit_and_queue(
        first,
        expected_frozen_input_sha256=frozen["artifact_sha256"],
        expected_authority_manifest_sha256=_manifest(spec)["artifact_sha256"],
    )

    plan = build_repair_plan(
        first["audit"],
        frozen["active_records"],
        product_dependencies={},
        product_dependency_records=[],
        authority_bridge=first,
    )
    assert plan["audit_artifact_sha256"] == first["audit"]["artifact_sha256"]
    assert plan["authority_binding"]["authority_bridge_artifact_sha256"] == (
        first["artifact_sha256"]
    )
    assert plan["source_work_queue"] == first["source_work_queue"]
    assert len(plan["queues"]["exact_source_replay"]) == 1
    loaded, change_set = deserialize_repair_plan(plan)
    assert loaded == plan
    assert change_set.operations == ()


def test_bridge_preserves_nonzero_review_root_and_freeze_binding_in_plan(
    tmp_path: Path,
) -> None:
    package = _reviewed_package("PKG-LEDGER")
    source_record = _source_record(package)
    spec, producer = _authority_unit(tmp_path, "LEDGER", package, source_record)
    review_event = {
        "review_event_id": "REV-HISTORICAL",
        "collection": "claims",
        "object_id": "CL-HISTORICAL",
        "object_revision": 1,
        "reviewer_kind": "system",
        "reviewer_id": "historical-fixture",
        "decision": "candidate",
        "reason": "unrelated historical ledger row",
        "artifact": {"fixture": True},
        "created_at": "2026-09-12T00:00:00+00:00",
    }
    frozen = _frozen_input(
        package,
        source_record,
        producer,
        _other_producer("LEDGER-OTHER"),
        review_event_rows=[review_event],
    )

    bridge = build_authority_bound_audit_and_queue(frozen, _manifest(spec))
    plan = build_repair_plan(
        bridge["audit"],
        frozen["active_records"],
        product_dependencies={},
        product_dependency_records=[],
        authority_bridge=bridge,
    )

    assert bridge["audit"]["review_event_ledger_count"] == 1
    assert bridge["audit"]["review_event_ledger_snapshot"] == (
        frozen["review_event_ledger_snapshot"]
    )
    assert bridge["audit"]["source_lineage_identity_snapshot"] == (
        frozen["source_lineage_identity_snapshot"]
    )
    assert bridge["audit"]["freeze_binding"][
        "frozen_input_artifact_sha256"
    ] == frozen["artifact_sha256"]
    assert plan["freeze_binding"] == bridge["audit"]["freeze_binding"]
    assert plan["store_guard"]["expected_review_event_ledger_snapshot"] == (
        frozen["review_event_ledger_snapshot"]
    )
    assert plan["store_guard"]["expected_source_lineage_snapshot"] == (
        frozen["source_lineage_identity_snapshot"]
    )
    loaded, change_set = deserialize_repair_plan(plan)
    assert loaded == plan
    assert change_set.operations == ()


def test_cli_binds_authority_and_carries_the_queue_into_plan(
    tmp_path: Path,
) -> None:
    package = _reviewed_package("PKG-CLI")
    source_record = _source_record(package)
    spec, producer = _authority_unit(tmp_path, "CLI", package, source_record)
    frozen = _frozen_input(
        package,
        source_record,
        producer,
        _other_producer("CLI-OTHER"),
    )
    frozen_path = tmp_path / "freeze.json"
    manifest_path = tmp_path / "authority-manifest.json"
    bridge_path = tmp_path / "authority-bridge.json"
    plan_path = tmp_path / "plan.json"
    frozen_path.write_text(json.dumps(frozen), encoding="utf-8")
    manifest_path.write_text(json.dumps(_manifest(spec)), encoding="utf-8")

    assert repair_main(
        [
            "bind-authority",
            "--input",
            str(frozen_path),
            "--manifest",
            str(manifest_path),
            "--output",
            str(bridge_path),
        ]
    ) == 0
    assert repair_main(
        [
            "plan",
            "--input",
            str(frozen_path),
            "--authority-bridge",
            str(bridge_path),
            "--output",
            str(plan_path),
        ]
    ) == 2
    bridge = json.loads(bridge_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["authority_binding"]["authority_bridge_artifact_sha256"] == (
        bridge["artifact_sha256"]
    )
    assert plan["source_work_queue"] == bridge["source_work_queue"]


def test_bridge_does_not_upgrade_a_package_without_pair_coverage(
    tmp_path: Path,
) -> None:
    current_package = _reviewed_package("PKG-CURRENT")
    unrelated_package = _reviewed_package(
        "PKG-UNRELATED",
        claim_id=f"{NAMESPACE}-CL2",
        evidence_id=f"{NAMESPACE}-E2",
    )
    source_record = _source_record(current_package)
    spec, producer = _authority_unit(
        tmp_path, "UNRELATED", unrelated_package, source_record
    )
    frozen = _frozen_input(
        current_package,
        source_record,
        producer,
        _other_producer("Z"),
    )

    result = build_authority_bound_audit_and_queue(frozen, _manifest(spec))

    assert result["authority_records"] == []
    assert result["pair_authority_decisions"][0]["reason_code"] == (
        AUTHORITY_NO_PACKAGE_PAIR
    )
    assert result["audit"]["pairs"][0]["disposition"] == DISPOSITION_SOURCE_RERUN
    assert result["source_work_queue"]["source_tasks"][0]["action"] == (
        QUEUE_SOURCE_RERUN
    )


def test_bridge_accepts_sealed_zero_replay_authority_manifest() -> None:
    package = _reviewed_package("PKG-CURRENT")
    source_record = _source_record(package)
    frozen = _frozen_input(
        package,
        source_record,
        _other_producer("A"),
        _other_producer("Z"),
    )

    result = build_authority_bound_audit_and_queue(frozen, _manifest())

    assert result["authority_validation"]["status"] == "eligible"
    assert result["authority_validation"]["counts"]["packages"] == 0
    assert result["authority_records"] == []
    assert result["audit"]["pairs"][0]["disposition"] == DISPOSITION_SOURCE_RERUN
    assert result["source_work_queue"]["source_tasks"][0]["action"] == (
        QUEUE_SOURCE_RERUN
    )


def test_conflicting_endpoint_package_proofs_stay_manual(
    tmp_path: Path,
) -> None:
    package_a = _reviewed_package("PKG-A")
    package_b = _reviewed_package("PKG-B")
    source_record = _source_record(package_a)
    spec_a, producer_a = _authority_unit(tmp_path, "A", package_a, source_record)
    spec_b, producer_b = _authority_unit(tmp_path, "B", package_b, source_record)
    frozen = _frozen_input(
        package_a,
        source_record,
        producer_a,
        producer_b,
    )

    result = build_authority_bound_audit_and_queue(
        frozen, _manifest(spec_a, spec_b)
    )

    assert result["authority_records"] == []
    decision = result["pair_authority_decisions"][0]
    assert decision["reason_code"] == AUTHORITY_CONFLICT
    assert decision["qualified_authority_unit_ids"] == ["AUTH-A", "AUTH-B"]
    assert result["source_work_queue"]["source_tasks"][0]["action"] == QUEUE_MANUAL
    assert result["pair_actions"][0]["audit_disposition"] == (
        DISPOSITION_SOURCE_RERUN
    )


def test_missing_frozen_source_generation_is_explicit_manual_work(
    tmp_path: Path,
) -> None:
    package = _reviewed_package("PKG-A")
    source_record = _source_record(package)
    spec, producer = _authority_unit(tmp_path, "A", package, source_record)
    frozen = _frozen_input(
        package,
        source_record,
        producer,
        _other_producer("Z"),
        active_source_documents=[],
    )

    result = build_authority_bound_audit_and_queue(frozen, _manifest(spec))

    assert result["authority_validation"]["status"] == "blocked"
    assert result["authority_records"] == []
    sources = result["pair_sources"][result["audit"]["pairs"][0]["pair_id"]]
    assert sources[0]["source_type"] == UNRESOLVED_SOURCE_TYPE
    assert result["source_work_queue"]["source_tasks"][0]["action"] == QUEUE_MANUAL


def test_bridge_rejects_cross_freeze_and_internal_producer_drift(
    tmp_path: Path,
) -> None:
    package = _reviewed_package("PKG-A")
    source_record = _source_record(package)
    spec, producer = _authority_unit(tmp_path, "A", package, source_record)
    frozen = _frozen_input(
        package,
        source_record,
        producer,
        _other_producer("Z"),
    )
    result = build_authority_bound_audit_and_queue(frozen, _manifest(spec))

    with pytest.raises(
        ClaimEvidenceReciprocityAuthorityBridgeError,
        match="another frozen input",
    ):
        validate_authority_bound_audit_and_queue(
            result, expected_frozen_input_sha256="0" * 64
        )

    drifted = deepcopy(frozen)
    drifted["producer_change_sets"][0]["summary"] = {"operations": 999}
    drifted = seal_artifact(drifted)
    with pytest.raises(
        ClaimEvidenceReciprocityAuthorityBridgeError,
        match="exact active-endpoint producer set",
    ):
        build_authority_bound_audit_and_queue(drifted, _manifest(spec))


def test_bridge_rejects_coordinated_resealed_derived_authority(
    tmp_path: Path,
) -> None:
    package = _reviewed_package("PKG-DERIVATION")
    source_record = _source_record(package)
    spec, producer = _authority_unit(
        tmp_path, "DERIVATION", package, source_record
    )
    frozen = _frozen_input(
        package,
        source_record,
        producer,
        _other_producer("DERIVATION-OTHER"),
    )
    bridge = build_authority_bound_audit_and_queue(frozen, _manifest(spec))

    forged = deepcopy(bridge)
    forged_authority = deepcopy(forged["authority_validation"])
    forged_authority["packages"][0]["reason_code"] = "forged_eligible_reason"
    forged_authority = seal_authority_artifact(forged_authority)
    forged_queue = deepcopy(forged["source_work_queue"])
    forged_queue["authority_validation_sha256"] = forged_authority[
        "artifact_sha256"
    ]
    forged_queue = seal_authority_artifact(forged_queue)
    forged["authority_validation"] = forged_authority
    forged["source_work_queue"] = forged_queue
    forged["roots"]["authority_validation_sha256"] = forged_authority[
        "artifact_sha256"
    ]
    forged["roots"]["source_work_queue_sha256"] = forged_queue[
        "artifact_sha256"
    ]
    forged = seal_artifact(forged)

    with pytest.raises(
        ClaimEvidenceReciprocityAuthorityBridgeError,
        match="differs from its deterministic frozen-input derivation",
    ):
        validate_authority_bound_audit_and_queue(forged)


def test_source_queue_plan_cli_prefers_the_sealed_authority_bridge(
    tmp_path: Path,
) -> None:
    package = _reviewed_package("PKG-A")
    source_record = _source_record(package)
    spec, producer = _authority_unit(tmp_path, "A", package, source_record)
    frozen = _frozen_input(
        package,
        source_record,
        producer,
        _other_producer("Z"),
    )
    bridge = build_authority_bound_audit_and_queue(frozen, _manifest(spec))
    bridge_path = tmp_path / "authority-bridge.json"
    frozen_path = tmp_path / "frozen-input.json"
    output_path = tmp_path / "source-execution.json"
    bridge_path.write_text(json.dumps(bridge), encoding="utf-8")
    frozen_path.write_text(json.dumps(frozen), encoding="utf-8")

    assert source_queue_main(
        [
            "plan",
            "--authority-bridge",
            str(bridge_path),
            "--frozen-input",
            str(frozen_path),
            "--output",
            str(output_path),
        ]
    ) == 0

    execution = json.loads(output_path.read_text(encoding="utf-8"))
    assert execution["source_queue_sha256"] == bridge[
        "source_work_queue"
    ]["artifact_sha256"]
    assert execution["authority_validation_sha256"] == bridge[
        "authority_validation"
    ]["artifact_sha256"]
