from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.api.canonical_repository.postgres_store import record_content_sha, sha256_json
from backend.pipeline.relation_id_incident_recovery import (
    RelationIdIncidentRecoveryError,
    RepositoryState,
    assert_incident_still_current,
    build_recovery_plan,
    desired_recovery_states,
    load_authoritative_relations,
    simulate_active_states,
    validate_desired_current,
    validate_simulated_graph,
)


def _state(
    collection: str,
    object_id: str,
    payload: dict,
    *,
    revision: int = 1,
    retired: bool = False,
) -> RepositoryState:
    return RepositoryState(
        collection=collection,
        object_id=object_id,
        revision=revision,
        content_sha256=record_content_sha(payload),
        payload={**payload, "revision": revision},
        retired=retired,
    )


def _operation(
    operation: str,
    collection: str,
    object_id: str,
    before: dict | None,
    after: dict,
) -> dict:
    return {
        "change_set_id": "KCS-INCIDENT",
        "operation_index": 0,
        "operation": operation,
        "collection": collection,
        "object_id": object_id,
        "before_sha256": record_content_sha(before) if before is not None else None,
        "after_sha256": record_content_sha(after),
        "before_revision": 1 if before is not None else None,
        "after_revision": 2 if before is not None else 1,
    }


def test_recovery_composes_inverse_namespace_and_bare_retirement_once() -> None:
    old_claim = {"claim_id": "CL-A", "statement": "old"}
    wrong_claim = {"claim_id": "CL-A", "statement": "wrong"}
    created = {"observation_id": "OBS-BAD", "statement": "wrong"}
    retired_evidence = {"evidence_step_id": "E-OLD", "statement": "kept"}
    bare = {
        "relation_id": "XER001",
        "from_id": "OBS-GOOD",
        "to_id": "E-OLD",
        "relation_type": "supports",
    }
    current = {
        ("claims", "CL-A"): _state("claims", "CL-A", wrong_claim, revision=2),
        ("observations", "OBS-BAD"): _state("observations", "OBS-BAD", created),
        ("observations", "OBS-GOOD"): _state(
            "observations", "OBS-GOOD", {"observation_id": "OBS-GOOD", "statement": "good"}
        ),
        ("evidence_steps", "E-OLD"): _state(
            "evidence_steps", "E-OLD", retired_evidence, revision=2, retired=True
        ),
        ("knowledge_relations", "XER001"): _state(
            "knowledge_relations", "XER001", bare
        ),
    }
    operations = [
        _operation("update", "claims", "CL-A", old_claim, wrong_claim),
        _operation("create", "observations", "OBS-BAD", None, created),
        _operation("retire", "evidence_steps", "E-OLD", retired_evidence, retired_evidence),
        _operation("retire", "knowledge_relations", "XER001", bare, bare),
    ]
    before = {
        ("claims", "CL-A"): {
            "revision": 1,
            "content_sha256": record_content_sha(old_claim),
            "payload": old_claim,
        },
        ("evidence_steps", "E-OLD"): {
            "revision": 1,
            "content_sha256": record_content_sha(retired_evidence),
            "payload": retired_evidence,
        },
        ("knowledge_relations", "XER001"): {
            "revision": 1,
            "content_sha256": record_content_sha(bare),
            "payload": bare,
        },
    }
    namespaced = {
        "relation_id": "DK-source-XER001",
        "from_id": "OBS-GOOD",
        "to_id": "E-OLD",
        "relation_type": "supports",
    }
    desired = desired_recovery_states(
        operations,
        before,
        current,
        {("knowledge_relations", "DK-source-XER001"): namespaced},
    )
    plan = build_recovery_plan(desired=desired, current=current, authority={"proof": "x"})
    by_key = {(row.collection, row.object_id): row for row in plan.operations}

    assert by_key[("claims", "CL-A")].operation == "update"
    assert by_key[("observations", "OBS-BAD")].operation == "retire"
    assert by_key[("evidence_steps", "E-OLD")].operation == "revive"
    assert by_key[("knowledge_relations", "XER001")].operation == "retire"
    assert by_key[("knowledge_relations", "DK-source-XER001")].operation == "create"
    assert len(by_key) == len(plan.operations) == 5

    active = simulate_active_states(current, desired)
    graph = validate_simulated_graph(active)
    assert graph["active_bare_relation_id_count"] == 0
    assert graph["unresolved_relation_endpoint_count"] == 0


def test_incident_guard_checks_revision_even_when_sha_is_unchanged() -> None:
    payload = {"claim_id": "CL-A", "statement": "same"}
    operation = _operation("update", "claims", "CL-A", payload, payload)
    current = {("claims", "CL-A"): _state("claims", "CL-A", payload, revision=3)}

    try:
        assert_incident_still_current([operation], current)
    except RelationIdIncidentRecoveryError as exc:
        assert "revision 3 != 2" in str(exc)
    else:
        raise AssertionError("revision drift was accepted")


def test_desired_state_verifier_distinguishes_active_and_retired() -> None:
    payload = {"claim_id": "CL-A", "statement": "old"}
    current = {("claims", "CL-A"): _state("claims", "CL-A", payload)}
    assert validate_desired_current(current, {("claims", "CL-A"): payload})[
        "desired_active_states_verified"
    ] == 1

    try:
        validate_desired_current(current, {("claims", "CL-A"): None})
    except RelationIdIncidentRecoveryError as exc:
        assert "should be inactive" in str(exc)
    else:
        raise AssertionError("active state was accepted as absent")


def test_authoritative_loader_migrates_only_legacy_cross_section_rows(
    tmp_path: Path,
) -> None:
    package = {
        "source_documents": [
            {
                "source_id": "SRC-A",
                "source_type": "sermon_transcript",
                "transcript_id": "A",
                "title": "A",
            }
        ],
        "knowledge_relations": [
            {
                "relation_id": "DK-existing-ER001",
                "from_id": "E-1",
                "to_id": "E-2",
                "relation_type": "supports",
                "reason": "base",
            },
            {
                "relation_id": "XER001",
                "from_id": "E-1",
                "to_id": "E-2",
                "relation_type": "supports",
                "reason": "cross section",
            },
        ],
        "claim_relations": [],
    }
    path = tmp_path / "reviewed.json"
    path.write_text(json.dumps(package), encoding="utf-8")
    relations, lineage = load_authoritative_relations(
        [
            {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "canonical_sha256": sha256_json(package),
                "historical_change_set_id": "KCS-OLD",
            }
        ]
    )

    assert len(relations) == 1
    assert next(iter(relations))[1].endswith("-XER001")
    assert lineage[0]["relation_count"] == 1
