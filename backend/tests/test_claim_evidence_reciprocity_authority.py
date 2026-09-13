from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from backend.api.canonical_repository.postgres_store import (
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
    ClaimEvidenceReciprocityAuthorityError,
    QUEUE_EXACT_REPLAY,
    QUEUE_MANUAL,
    QUEUE_SOURCE_RERUN,
    REASON_CANONICAL_SHA,
    REASON_CURRENT_CONTRACT,
    REASON_LEDGER_MISMATCH,
    REASON_LEDGER_METADATA,
    REASON_RAW_SHA,
    REASON_REVIEW_SEAL,
    REASON_SOURCE_GENERATION,
    SOURCE_QUEUE_SCHEMA_VERSION,
    _derive_effective_package,
    build_source_work_queues,
    seal_authority_artifact,
    validate_authority_manifest,
    validate_source_work_queue,
)
from backend.pipeline.relation_id_namespace import (
    migrate_legacy_cross_section_relation_ids,
    source_namespace,
)


def _reviewed_package(*, reciprocal: bool = True, legacy_relation: bool = False) -> dict[str, Any]:
    transcript_id = "SERMON-ONE"
    namespace = source_namespace(transcript_id)
    body_sha = "d" * 64
    claim_evidence = [f"{namespace}-E1"] if reciprocal else []
    package: dict[str, Any] = {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": "PKG-SERMON-ONE",
        "complete": True,
        "source_documents": [
            {
                "source_id": "SRC-ONE",
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
                "source_id": "SRC-ONE",
                "verbatim_excerpt": "source words",
                "source_sha256": body_sha,
            }
        ],
        "evidence_steps": [
            {
                "evidence_step_id": f"{namespace}-E1",
                "source_fragment_ids": [f"FR-{namespace}-1"],
                "statement": "evidence one",
                "produced_claim_ids": [f"{namespace}-CL1"],
            }
        ],
        "claims": [
            {
                "claim_id": f"{namespace}-CL1",
                "statement": "claim one",
                "claim_type": "explicit_claim",
                "evidence_step_ids": claim_evidence,
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
                    "claim_id": f"{namespace}-CL1",
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
    if legacy_relation:
        package["evidence_steps"].append(
            {
                "evidence_step_id": f"{namespace}-E2",
                "source_fragment_ids": [f"FR-{namespace}-1"],
                "statement": "evidence two",
                "produced_claim_ids": [],
            }
        )
        package["knowledge_relations"] = [
            {
                "relation_id": "XER001",
                "from_id": f"{namespace}-E1",
                "to_id": f"{namespace}-E2",
                "relation_type": "supports",
            }
        ]
    package["consensus_application"]["artifact_sha256"] = (
        reviewed_candidate_artifact_sha256(package)
    )
    return package


def _context(
    tmp_path: Path,
    package: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    path = tmp_path / "reviewed.json"
    path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    raw = path.read_bytes()
    effective_unsealed, migration = migrate_legacy_cross_section_relation_ids(package)
    effective = reseal_after_relation_id_migration(
        package, effective_unsealed, migration
    )
    upstream_sha = package["consensus_application"]["artifact_sha256"]
    effective_artifact_sha = effective["consensus_application"]["artifact_sha256"]
    effective_sha = sha256_json(effective)

    source = deepcopy(package["source_documents"][0])
    source["revision"] = 7
    active = [
        {
            "collection": "source_documents",
            "object_id": source["source_id"],
            "revision": 7,
            "content_sha256": record_content_sha(source),
            "payload": source,
        }
    ]
    historical = {
        "change_set_id": "KCS-HISTORICAL",
        "fingerprint_sha256": "f" * 64,
        "source_kind": "knowledge_package",
        "source_sha256": effective_sha,
        "status": "applied",
    }
    ledger = {
        "KCS-HISTORICAL": {
            **historical,
            "applied_at": "2026-09-12T00:00:00+00:00",
            "metadata": {
                "upstream_reviewed_candidate_artifact_sha256": upstream_sha,
                "effective_reviewed_candidate_artifact_sha256": effective_artifact_sha,
                "relation_id_namespace_migration": migration,
            },
        }
    }
    document = package["source_documents"][0]
    spec = {
        "authority_unit_id": "AUTH-SERMON-ONE",
        "path": str(path),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "input_canonical_sha256": sha256_json(package),
        "effective_canonical_sha256": effective_sha,
        "upstream_reviewed_candidate_artifact_sha256": upstream_sha,
        "effective_reviewed_candidate_artifact_sha256": effective_artifact_sha,
        "relation_id_namespace_migration": migration,
        "historical_change_set": historical,
        "source_generations": [
            {
                "source_type": document["source_type"],
                "row_key": document["transcript_id"],
                "active_source_document_id": document["source_id"],
                "expected_revision": 7,
                "expected_content_sha256": record_content_sha(source),
                "source_body_sha256": document["source_body_sha256"],
                "extraction_record_namespace": document[
                    "extraction_record_namespace"
                ],
            }
        ],
    }
    manifest = seal_authority_artifact(
        {
            "schema_version": AUTHORITY_MANIFEST_SCHEMA_VERSION,
            "packages": [spec],
        }
    )
    return manifest, ledger, active


def test_valid_package_binds_bytes_review_migration_ledger_and_current_source(
    tmp_path: Path,
) -> None:
    manifest, ledger, active = _context(
        tmp_path, _reviewed_package(legacy_relation=True)
    )

    result = validate_authority_manifest(
        manifest,
        historical_change_sets=ledger,
        active_source_documents=active,
    )

    assert result["status"] == "eligible"
    row = result["packages"][0]
    assert row["replay_eligible"] is True
    assert row["relation_id_namespace_migration"]["status"] == "applied"
    assert row["relation_id_namespace_migration"]["id_map"] == {
        "XER001": f"{source_namespace('SERMON-ONE')}-XER001"
    }
    assert row["historical_change_set"]["source_sha256"] == row[
        "effective_canonical_sha256"
    ]
    assert row["claim_evidence_pairs"] == [
        {
            "claim_id": f"{source_namespace('SERMON-ONE')}-CL1",
            "evidence_step_id": f"{source_namespace('SERMON-ONE')}-E1",
        }
    ]


def test_empty_manifest_is_a_sealed_zero_replay_authority_declaration() -> None:
    manifest = seal_authority_artifact(
        {
            "schema_version": AUTHORITY_MANIFEST_SCHEMA_VERSION,
            "packages": [],
        }
    )

    result = validate_authority_manifest(
        manifest,
        historical_change_sets={},
        active_source_documents=[],
    )

    assert result["status"] == "eligible"
    assert result["packages"] == []
    assert result["counts"] == {
        "packages": 0,
        "replay_eligible": 0,
        "blocked": 0,
        "sources": 0,
    }


def test_raw_sha_mismatch_blocks_before_package_authority(tmp_path: Path) -> None:
    manifest, ledger, active = _context(tmp_path, _reviewed_package())
    changed = deepcopy(manifest)
    changed["packages"][0]["raw_sha256"] = "0" * 64
    changed = seal_authority_artifact(changed)

    result = validate_authority_manifest(
        changed,
        historical_change_sets=ledger,
        active_source_documents=active,
    )

    assert result["packages"][0]["reason_code"] == REASON_RAW_SHA
    assert result["packages"][0]["failed_stage"] == "raw"


def test_canonical_sha_and_review_seal_are_independent_gates(tmp_path: Path) -> None:
    manifest, ledger, active = _context(tmp_path, _reviewed_package())
    wrong_canonical = deepcopy(manifest)
    wrong_canonical["packages"][0]["input_canonical_sha256"] = "0" * 64
    wrong_canonical = seal_authority_artifact(wrong_canonical)
    result = validate_authority_manifest(
        wrong_canonical,
        historical_change_sets=ledger,
        active_source_documents=active,
    )
    assert result["packages"][0]["reason_code"] == REASON_CANONICAL_SHA

    tampered = deepcopy(manifest)
    path = Path(tampered["packages"][0]["path"])
    package = json.loads(path.read_text(encoding="utf-8"))
    package["claims"][0]["statement"] = "modified after review"
    path.write_text(json.dumps(package), encoding="utf-8")
    raw = path.read_bytes()
    tampered["packages"][0]["raw_sha256"] = hashlib.sha256(raw).hexdigest()
    tampered["packages"][0]["input_canonical_sha256"] = sha256_json(package)
    tampered = seal_authority_artifact(tampered)
    result = validate_authority_manifest(
        tampered,
        historical_change_sets=ledger,
        active_source_documents=active,
    )
    assert result["packages"][0]["reason_code"] == REASON_REVIEW_SEAL
    assert result["packages"][0]["failed_stage"] == "review_seal"


def test_historical_seal_that_fails_current_reciprocity_gate_requires_rerun(
    tmp_path: Path,
) -> None:
    # The package seal authenticates this historical one-sided graph.  That is
    # not permission to replay it through today's stronger graph contract.
    package = _reviewed_package(reciprocal=False)
    manifest, ledger, active = _context(tmp_path, package)

    result = validate_authority_manifest(
        manifest,
        historical_change_sets=ledger,
        active_source_documents=active,
    )

    row = result["packages"][0]
    assert row["replay_eligible"] is False
    assert row["reason_code"] == REASON_CURRENT_CONTRACT
    assert row["failed_stage"] == "current_contract"
    assert row["required_action"] == QUEUE_SOURCE_RERUN


def test_historical_kcs_effective_metadata_must_match(tmp_path: Path) -> None:
    manifest, ledger, active = _context(tmp_path, _reviewed_package())
    ledger["KCS-HISTORICAL"]["metadata"][
        "effective_reviewed_candidate_artifact_sha256"
    ] = "0" * 64

    result = validate_authority_manifest(
        manifest,
        historical_change_sets=ledger,
        active_source_documents=active,
    )

    assert result["packages"][0]["reason_code"] == REASON_LEDGER_METADATA
    assert result["packages"][0]["failed_stage"] == "historical_change_set"


def test_historical_kcs_source_sha_must_equal_effective_package(tmp_path: Path) -> None:
    manifest, ledger, active = _context(tmp_path, _reviewed_package())
    ledger["KCS-HISTORICAL"]["source_sha256"] = "0" * 64

    result = validate_authority_manifest(
        manifest,
        historical_change_sets=ledger,
        active_source_documents=active,
    )

    row = result["packages"][0]
    assert row["reason_code"] == REASON_LEDGER_MISMATCH
    assert "must equal effective package" in row["detail"]
    assert "original package" in row["detail"]
    assert "is not accepted" in row["detail"]


def test_historical_kcs_identity_fields_cannot_be_omitted(tmp_path: Path) -> None:
    manifest, ledger, active = _context(tmp_path, _reviewed_package())
    manifest = deepcopy(manifest)
    manifest["packages"][0]["historical_change_set"].pop("fingerprint_sha256")
    manifest = seal_authority_artifact(manifest)

    result = validate_authority_manifest(
        manifest,
        historical_change_sets=ledger,
        active_source_documents=active,
    )

    row = result["packages"][0]
    assert row["reason_code"] == REASON_LEDGER_MISMATCH
    assert row["failed_stage"] == "historical_change_set"
    assert "fingerprint_sha256 is required" in row["detail"]


def test_active_source_generation_drift_blocks_replay(tmp_path: Path) -> None:
    manifest, ledger, active = _context(tmp_path, _reviewed_package())
    active[0]["payload"]["extraction_record_namespace"] = "DK-000000000000"
    active[0]["content_sha256"] = record_content_sha(active[0]["payload"])
    manifest = deepcopy(manifest)
    manifest["packages"][0]["source_generations"][0][
        "expected_content_sha256"
    ] = active[0]["content_sha256"]
    manifest = seal_authority_artifact(manifest)

    result = validate_authority_manifest(
        manifest,
        historical_change_sets=ledger,
        active_source_documents=active,
    )

    assert result["packages"][0]["reason_code"] == REASON_SOURCE_GENERATION
    assert result["packages"][0]["failed_stage"] == "source_generation"


def test_aggregate_cannot_skip_unsupported_legacy_relation_migration() -> None:
    aggregate = {
        "consensus_application": {"scope_kind": "research_batch_aggregate"},
        "knowledge_relations": [{"relation_id": "XER001"}],
        "claim_relations": [],
    }

    with pytest.raises(
        ClaimEvidenceReciprocityAuthorityError, match="legacy relation IDs"
    ):
        _derive_effective_package(aggregate)

    aggregate["knowledge_relations"][0]["relation_id"] = "DK-global-XER001"
    aggregate["unsupported_legacy_reference"] = "XCR009"
    with pytest.raises(
        ClaimEvidenceReciprocityAuthorityError,
        match=r"unsupported_legacy_reference=XCR009",
    ):
        _derive_effective_package(aggregate)

    aggregate.pop("unsupported_legacy_reference")
    effective, migration = _derive_effective_package(aggregate)
    assert effective == aggregate
    assert migration["status"] == "not_required"
    assert migration["legacy_relation_ids_verified_absent"] is True


def _authority_validation(*packages: dict[str, Any]) -> dict[str, Any]:
    return seal_authority_artifact(
        {
            "schema_version": AUTHORITY_VALIDATION_SCHEMA_VERSION,
            "status": "eligible",
            "authority_manifest_sha256": "m" * 64,
            "packages": list(packages),
            "counts": {},
        }
    )


def _unit(unit_id: str, *sources: tuple[str, str]) -> dict[str, Any]:
    return {
        "authority_unit_id": unit_id,
        "replay_eligible": True,
        "source_identities": [
            {"source_type": source_type, "row_key": row_key}
            for source_type, row_key in sources
        ],
    }


def _action(
    pair_id: str,
    disposition: str,
    *,
    authority_unit_id: str | None = None,
) -> dict[str, Any]:
    result = {
        "pair_id": pair_id,
        "mismatch_type": "claim_only",
        "reason_code": "test_reason",
        "disposition": disposition,
        "blocks_apply": True,
    }
    if authority_unit_id:
        result["authority_unit_id"] = authority_unit_id
    return result


SOURCE_ONE = [{"source_type": "sermon_transcript", "row_key": "SERMON-ONE"}]


def test_many_pair_findings_for_one_source_become_one_deterministic_task() -> None:
    authority = _authority_validation()
    actions = [
        _action("PAIR-2", QUEUE_SOURCE_RERUN),
        _action("PAIR-1", QUEUE_SOURCE_RERUN),
    ]
    sources = {"PAIR-1": SOURCE_ONE, "PAIR-2": SOURCE_ONE}

    first = build_source_work_queues(
        actions,
        freeze_artifact_sha256="f" * 64,
        audit_artifact_sha256="a" * 64,
        authority_validation=authority,
        pair_sources=sources,
    )
    second = build_source_work_queues(
        list(reversed(actions)),
        freeze_artifact_sha256="f" * 64,
        audit_artifact_sha256="a" * 64,
        authority_validation=authority,
        pair_sources=sources,
    )

    assert first == second
    assert first["counts"]["source_tasks"] == 1
    assert first["source_tasks"][0]["pair_ids"] == ["PAIR-1", "PAIR-2"]
    assert len(first["pair_assignments"]) == 2


def test_source_rerun_escalates_replay_for_the_same_source() -> None:
    unit = _unit("AUTH-1", ("sermon_transcript", "SERMON-ONE"))
    authority = _authority_validation(unit)
    actions = [
        _action("PAIR-1", QUEUE_EXACT_REPLAY, authority_unit_id="AUTH-1"),
        _action("PAIR-2", QUEUE_SOURCE_RERUN),
    ]
    sources = {"PAIR-1": SOURCE_ONE, "PAIR-2": SOURCE_ONE}

    result = build_source_work_queues(
        actions,
        freeze_artifact_sha256="f" * 64,
        audit_artifact_sha256="a" * 64,
        authority_validation=authority,
        pair_sources=sources,
    )

    assert len(result["source_tasks"]) == 1
    assert result["source_tasks"][0]["action"] == QUEUE_SOURCE_RERUN


def test_conflicting_replay_generations_for_one_source_require_manual_work() -> None:
    authority = _authority_validation(
        _unit("AUTH-1", ("sermon_transcript", "SERMON-ONE")),
        _unit("AUTH-2", ("sermon_transcript", "SERMON-ONE")),
    )
    actions = [
        _action("PAIR-1", QUEUE_EXACT_REPLAY, authority_unit_id="AUTH-1"),
        _action("PAIR-2", QUEUE_EXACT_REPLAY, authority_unit_id="AUTH-2"),
    ]
    sources = {"PAIR-1": SOURCE_ONE, "PAIR-2": SOURCE_ONE}

    result = build_source_work_queues(
        actions,
        freeze_artifact_sha256="f" * 64,
        audit_artifact_sha256="a" * 64,
        authority_validation=authority,
        pair_sources=sources,
    )

    assert result["source_tasks"][0]["action"] == QUEUE_MANUAL
    assert result["source_tasks"][0]["authority_unit_ids"] == ["AUTH-1", "AUTH-2"]


def test_aggregate_reviewed_package_stays_one_atomic_replay_unit() -> None:
    unit = _unit(
        "AUTH-BATCH",
        ("sermon_transcript", "SERMON-ONE"),
        ("sermon_transcript", "SERMON-TWO"),
    )
    authority = _authority_validation(unit)
    actions = [
        _action("PAIR-1", QUEUE_EXACT_REPLAY, authority_unit_id="AUTH-BATCH"),
        _action("PAIR-2", QUEUE_EXACT_REPLAY, authority_unit_id="AUTH-BATCH"),
    ]
    sources = {
        "PAIR-1": SOURCE_ONE,
        "PAIR-2": [{"source_type": "sermon_transcript", "row_key": "SERMON-TWO"}],
    }

    result = build_source_work_queues(
        actions,
        freeze_artifact_sha256="f" * 64,
        audit_artifact_sha256="a" * 64,
        authority_validation=authority,
        pair_sources=sources,
    )

    assert result["counts"]["source_tasks"] == 1
    assert result["source_tasks"][0]["action"] == QUEUE_EXACT_REPLAY
    assert result["source_tasks"][0]["authority_unit_ids"] == ["AUTH-BATCH"]
    assert result["source_tasks"][0]["pair_ids"] == ["PAIR-1", "PAIR-2"]


@pytest.mark.parametrize(
    "observed_sources",
    [
        [],
        [{"source_type": "sermon_transcript", "row_key": "SERMON-OTHER"}],
    ],
)
def test_aggregate_replay_requires_pair_source_coverage(
    observed_sources: list[dict[str, str]],
) -> None:
    authority = _authority_validation(
        _unit(
            "AUTH-BATCH",
            ("sermon_transcript", "SERMON-ONE"),
            ("sermon_transcript", "SERMON-TWO"),
        )
    )
    action = _action(
        "PAIR-1", QUEUE_EXACT_REPLAY, authority_unit_id="AUTH-BATCH"
    )

    result = build_source_work_queues(
        [action],
        freeze_artifact_sha256="f" * 64,
        audit_artifact_sha256="a" * 64,
        authority_validation=authority,
        pair_sources={"PAIR-1": observed_sources},
    )

    assert result["source_tasks"][0]["action"] == QUEUE_MANUAL


def test_queue_rejects_duplicate_authority_units_and_source_identities() -> None:
    repeated_unit = _unit("AUTH-1", ("sermon_transcript", "SERMON-ONE"))
    authority = _authority_validation(repeated_unit, repeated_unit)
    with pytest.raises(ClaimEvidenceReciprocityAuthorityError, match="repeats unit"):
        build_source_work_queues(
            [],
            freeze_artifact_sha256="f" * 64,
            audit_artifact_sha256="a" * 64,
            authority_validation=authority,
            pair_sources={},
        )

    repeated_source = _unit(
        "AUTH-1",
        ("sermon_transcript", "SERMON-ONE"),
        ("sermon_transcript", "SERMON-ONE"),
    )
    authority = _authority_validation(repeated_source)
    with pytest.raises(ClaimEvidenceReciprocityAuthorityError, match="repeats source"):
        build_source_work_queues(
            [],
            freeze_artifact_sha256="f" * 64,
            audit_artifact_sha256="a" * 64,
            authority_validation=authority,
            pair_sources={},
        )


def test_direct_pair_is_excluded_and_duplicate_blocked_pair_is_rejected() -> None:
    authority = _authority_validation()
    direct = {
        **_action("PAIR-DIRECT", "project_human_binding_to_evidence"),
        "blocks_apply": False,
    }
    result = build_source_work_queues(
        [direct],
        freeze_artifact_sha256="f" * 64,
        audit_artifact_sha256="a" * 64,
        authority_validation=authority,
        pair_sources={},
    )
    assert result["counts"]["blocking_pairs"] == 0
    assert result["source_tasks"] == []

    duplicate = _action("PAIR-1", QUEUE_SOURCE_RERUN)
    with pytest.raises(ClaimEvidenceReciprocityAuthorityError, match="repeat"):
        build_source_work_queues(
            [duplicate, duplicate],
            freeze_artifact_sha256="f" * 64,
            audit_artifact_sha256="a" * 64,
            authority_validation=authority,
            pair_sources={"PAIR-1": SOURCE_ONE},
        )

    inconsistent = _action("PAIR-2", QUEUE_MANUAL)
    inconsistent["blocks_apply"] = False
    with pytest.raises(ClaimEvidenceReciprocityAuthorityError, match="must block"):
        build_source_work_queues(
            [inconsistent],
            freeze_artifact_sha256="f" * 64,
            audit_artifact_sha256="a" * 64,
            authority_validation=authority,
            pair_sources={"PAIR-2": SOURCE_ONE},
        )


def test_queue_tamper_and_cross_list_coverage_are_rejected() -> None:
    authority = _authority_validation()
    action = _action("PAIR-1", QUEUE_SOURCE_RERUN)
    queue = build_source_work_queues(
        [action],
        freeze_artifact_sha256="f" * 64,
        audit_artifact_sha256="a" * 64,
        authority_validation=authority,
        pair_sources={"PAIR-1": SOURCE_ONE},
    )
    assert queue["schema_version"] == SOURCE_QUEUE_SCHEMA_VERSION
    assert queue["audit_artifact_sha256"] == "a" * 64
    assert queue["pair_action_manifest_sha256"] == sha256_json([action])
    validate_source_work_queue(
        queue,
        expected_freeze_artifact_sha256="f" * 64,
        expected_audit_artifact_sha256="a" * 64,
        expected_pair_action_manifest_sha256=sha256_json([action]),
        expected_authority_validation_sha256=authority["artifact_sha256"],
    )
    with pytest.raises(ClaimEvidenceReciprocityAuthorityError, match="another reciprocity audit"):
        validate_source_work_queue(
            queue, expected_audit_artifact_sha256="b" * 64
        )
    tampered = deepcopy(queue)
    tampered["source_tasks"][0]["pair_ids"] = []
    with pytest.raises(ClaimEvidenceReciprocityAuthorityError, match="seal"):
        validate_source_work_queue(tampered)

    resealed = seal_authority_artifact(tampered)
    with pytest.raises(ClaimEvidenceReciprocityAuthorityError, match="coverage"):
        validate_source_work_queue(resealed)

    tampered_counts = deepcopy(queue)
    tampered_counts["counts"]["blocking_pairs"] = 2
    resealed_counts = seal_authority_artifact(tampered_counts)
    with pytest.raises(ClaimEvidenceReciprocityAuthorityError, match="counts"):
        validate_source_work_queue(resealed_counts)
