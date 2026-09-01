from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.postgres_store import (
    ChangeSetConflict,
    SOURCE_KEYS,
    PostgresKnowledgeStore,
    build_active_snapshot,
    build_change_set_plan,
    normalize_package,
    record_content_sha,
    reviewed_relations_package,
    stored_operation_payload,
)


def _package() -> dict:
    return {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": "PKG-1",
        "source_documents": [
            {"source_id": "SRC-1", "source_type": "sermon_transcript", "title": "讲道"}
        ],
        "source_fragments": [
            {"fragment_id": "FR-1", "source_id": "SRC-1", "verbatim_excerpt": "原话"}
        ],
        "evidence_steps": [
            {
                "evidence_step_id": "E-1",
                "source_fragment_id": "FR-1",
                "statement": "证据",
                "support_eligibility": "withheld_unreviewed",
            }
        ],
        "claims": [
            {
                "claim_id": "CL-1",
                "statement": "教授的主张",
                "claim_type": "explicit_claim",
                "evidence_step_ids": ["E-1"],
            }
        ],
    }


def test_plan_is_stable_and_revision_is_not_semantic_content() -> None:
    package = _package()
    first = build_change_set_plan(package, {})
    assert first.as_dict()["summary"] == {
        "created": 4,
        "updated": 0,
        "retired": 0,
        "revived": 0,
        "unchanged": 0,
        "operations": 4,
        "fields_removed": 0,
        "removals": [],
    }
    claim = normalize_package(package)["claims"]["CL-1"]
    existing_claim = dict(claim, revision=8)
    existing = {
        ("claims", "CL-1"): {
            "revision": 8,
            "content_sha256": record_content_sha(existing_claim),
            "payload": existing_claim,
        }
    }
    partial = build_change_set_plan(package, existing)
    assert not any(item.object_id == "CL-1" for item in partial.operations)


def test_human_review_fields_survive_ai_reimport() -> None:
    package = _package()
    reviewed = normalize_package(package)["claims"]["CL-1"]
    reviewed.update(
        {
            "review_status": "approved",
            "review_note": "同工已核对",
            "reviewed_by": "reviewer-1",
            "revision": 4,
        }
    )
    existing = {
        ("claims", "CL-1"): {
            "revision": 4,
            "content_sha256": record_content_sha(reviewed),
            "payload": reviewed,
        }
    }
    changed = _package()
    changed["claims"][0]["statement"] = "更新后的候选文字"
    plan = build_change_set_plan(changed, existing)
    operation = next(item for item in plan.operations if item.object_id == "CL-1")
    assert operation.payload["review_status"] == "approved"
    assert operation.payload["review_note"] == "同工已核对"
    assert operation.payload["reviewed_by"] == "reviewer-1"


def test_explicit_human_ruling_promotes_existing_system_review() -> None:
    package = _package()
    system_reviewed = normalize_package(package)["claims"]["CL-1"]
    system_reviewed.update(
        {
            "review_status": "system_approved",
            "review_note": "旧模型审核",
            "reviewed_by": "review-model",
            "revision": 3,
        }
    )
    existing = {
        ("claims", "CL-1"): {
            "revision": 3,
            "content_sha256": record_content_sha(system_reviewed),
            "payload": system_reviewed,
        }
    }
    ruled = _package()
    ruled["claims"][0].update(
        {
            "review_status": "human_approved",
            "review_note": "owner ruling",
            "reviewed_by": "junyang",
        }
    )

    plan = build_change_set_plan(ruled, existing)
    operation = next(item for item in plan.operations if item.object_id == "CL-1")
    assert operation.payload["review_status"] == "human_approved"
    assert operation.payload["review_note"] == "owner ruling"
    assert operation.payload["reviewed_by"] == "junyang"
    assert stored_operation_payload(operation)["revision"] == 4


def test_reviewed_relation_artifact_becomes_edges_and_negative_constraints() -> None:
    artifact = {
        "result": {
            "reviewed_relations": [
                {
                    "candidate_id": "XSR-1",
                    "source_claim_id": "CL-1",
                    "target_claim_id": "CL-2",
                    "relation_type": "supports",
                    "reason": "跨讲支持",
                    "review_status": "ai_consensus",
                },
            ],
            "negative_comparisons": [
                {
                    "candidate_id": "XSR-2",
                    "source_claim_id": "CL-1",
                    "target_claim_id": "CL-3",
                    "relation_type": "unrelated",
                    "reason": "不能合并",
                    "review_status": "ai_consensus",
                }
            ],
        }
    }
    package = reviewed_relations_package(artifact)
    assert package["claim_relations"][0]["relation_type"] == "supports"
    assert package["claim_relation_constraints"][0]["bidirectional"] is True
    assert "duplicate" in package["claim_relation_constraints"][0]["forbidden_relation_types"]


def test_reviewed_relation_package_preserves_human_approval() -> None:
    artifact = {
        "result": {
            "reviewed_relations": [
                {
                    "candidate_id": "XSR-APPROVED",
                    "source_claim_id": "CL-1",
                    "target_claim_id": "CL-2",
                    "relation_type": "supports",
                    "review_status": "approved",
                }
            ]
        }
    }
    package = reviewed_relations_package(artifact)
    assert package["claim_relations"][0]["review_status"] == "approved"


def test_topic_identity_reconciliation_is_a_persisted_knowledge_record() -> None:
    package = {
        "schema_version": "topic_identity_test_v1",
        "package_id": "PKG-TOPIC-IDENTITY",
        "topic_identity_reconciliations": [{
            "reconciliation_id": "TIR-1",
            "candidate_topic_id": "TCAND-1",
            "label": "候选母题：约与关系",
            "topic_level": "family",
            "claim_ids": ["CL-1"],
            "status": "pending_match",
            "candidate_matches": [{"existing_topic_id": "covenant"}],
            "origin_batch_id": "RB-ONE",
        }],
    }
    normalized = normalize_package(package)
    record = normalized["topic_identity_reconciliations"]["TIR-1"]
    assert record["candidate_topic_id"] == "TCAND-1"
    assert record["status"] == "pending_match"
    assert record["candidate_matches"][0]["existing_topic_id"] == "covenant"
    assert SOURCE_KEYS["topic_identity_reconciliations"] == "topic_identity_reconciliations"


def test_current_shared_package_can_be_normalized() -> None:
    path = Path(__file__).parent / "fixtures/wang_knowledge_platform/shared-knowledge-pilot.json"
    normalized = normalize_package(json.loads(path.read_text(encoding="utf-8")))
    assert normalized["claims"]
    assert normalized["source_documents"]


def test_migration_defines_transactional_authoring_tables() -> None:
    sql = Path(
        "backend/api/canonical_repository/migrations/001_postgres_authoring_store.sql"
    ).read_text(encoding="utf-8")
    for table in (
        "wang_knowledge.objects",
        "wang_knowledge.object_versions",
        "wang_knowledge.change_sets",
        "wang_knowledge.change_operations",
        "wang_knowledge.edges",
        "wang_knowledge.review_events",
    ):
        assert table in sql


def test_active_snapshot_contains_only_approved_claims_with_bound_evidence() -> None:
    package = _package()
    package["source_documents"][0]["source_sha256"] = "source-hash"
    package["source_fragments"][0].update(
        {
            "anchor_state": "source_version_bound",
            "paragraph_text_sha256": "paragraph-hash",
            "verbatim_excerpt_sha256": "excerpt-hash",
        }
    )
    package["evidence_steps"][0]["support_eligibility"] = "eligible"
    package["claims"][0]["review_status"] = "approved"
    package["claims"].append(
        {
            "claim_id": "CL-2",
            "statement": "尚未批准的主张",
            "claim_type": "explicit_claim",
            "evidence_step_ids": ["E-1"],
            "review_status": "candidate",
        }
    )

    snapshot, findings = build_active_snapshot(package, build_id="ACTIVE-TEST")

    assert snapshot["build_id"] == "ACTIVE-TEST"
    assert [item["claim_id"] for item in snapshot["claims"]] == ["CL-1"]
    assert [item["evidence_step_id"] for item in snapshot["evidence_steps"]] == ["E-1"]
    assert [item["fragment_id"] for item in snapshot["source_fragments"]] == ["FR-1"]
    assert not [item for item in findings if item["severity"] == "error"]


def test_active_snapshot_rejects_approved_claim_with_unbound_source() -> None:
    package = _package()
    package["claims"][0]["review_status"] = "approved"
    package["evidence_steps"][0]["support_eligibility"] = "eligible"

    snapshot, findings = build_active_snapshot(package, build_id="ACTIVE-TEST")

    assert snapshot["claims"] == []
    codes = {item["code"] for item in findings if item["severity"] == "error"}
    assert "approved_claim_has_unbound_evidence" in codes
    assert "approved_claim_without_publishable_evidence" in codes


class _RecordStore:
    """Enough of the store for `get_plan_document`, which only reads records."""

    get_plan_document = PostgresKnowledgeStore.get_plan_document

    def __init__(self, records: dict) -> None:
        self._records = records

    def get_record(self, collection: str, object_id: str):
        return self._records.get(collection, {}).get(object_id)


def test_plan_document_inlines_its_decisions_and_survives_a_package_round_trip() -> None:
    """`export-plan` writes this shape and `ingest-plan` wraps it back up, so
    a plan can leave the store for the composition review and return revised.
    """

    store = _RecordStore(
        {
            "composition_plans": {
                "CP-1": {
                    "plan_id": "CP-1",
                    "title": "測試編排計劃",
                    "product_type": "scripture_exposition",
                    "decision_ids": ["CD-1", "CD-2"],
                }
            },
            "composition_decisions": {
                "CD-1": {
                    "decision_id": "CD-1", "plan_id": "CP-1", "claim_ids": [],
                    "decision_type": "coverage_gap", "decision": "只引經文。",
                },
                "CD-2": {
                    "decision_id": "CD-2", "plan_id": "CP-1", "claim_ids": ["CL-1"],
                    "decision_type": "main_section", "decision": "展開論證。",
                },
            },
        }
    )
    document = store.get_plan_document("CP-1")
    assert [item["decision_id"] for item in document["decisions"]] == ["CD-1", "CD-2"]
    assert store.get_plan_document("CP-missing") is None

    # The wrapper `ingest-plan` builds: the importer splits an inlined
    # `product_plans` entry back into a plan plus its decisions.
    normalized = normalize_package(
        {
            "schema_version": "wang_shared_knowledge_v1.3",
            "package_id": "PLAN-CP-1",
            "product_plans": [document],
        }
    )
    assert set(normalized["composition_plans"]) == {"CP-1"}
    assert set(normalized["composition_decisions"]) == {"CD-1", "CD-2"}
    assert normalized["composition_plans"]["CP-1"]["decision_ids"] == ["CD-1", "CD-2"]


def test_plan_document_refuses_a_decision_the_store_does_not_have() -> None:
    store = _RecordStore(
        {
            "composition_plans": {
                "CP-1": {"plan_id": "CP-1", "title": "t", "product_type": "x", "decision_ids": ["CD-gone"]}
            },
            "composition_decisions": {},
        }
    )
    with pytest.raises(KeyError, match="CD-gone"):
        store.get_plan_document("CP-1")


def _stored(collection: str, object_id: str, payload: dict) -> dict:
    return {
        (collection, object_id): {
            "revision": payload.get("revision", 1),
            "content_sha256": record_content_sha(payload),
            "payload": payload,
        }
    }


def test_a_reextraction_keeps_the_provenance_it_never_carried() -> None:
    """WKP-F01.16: the manuscript that dropped out of every scripture view.

    `project_id`, `lineage` and `source_url` are written by the import that
    brought the manuscript in. An extraction package has no reason to carry
    them and did not, and the update erased all three, so the manuscript could
    no longer find its `meta.json` and stopped being grouped under 太16.
    """

    stored = normalize_package(_package())["source_documents"]["SRC-1"]
    stored.update(
        {
            "project_id": "16_章_-_彌賽亞，捨己",
            "source_url": "/resources/notes_to_manuscript_series/d5c55bdf/16_章",
            "lineage": {"upstream_kind": "professor_notes"},
        }
    )

    package = _package()
    package["source_documents"][0]["title"] = "母本 v2"
    plan = build_change_set_plan(package, _stored("source_documents", "SRC-1", stored))

    operation = next(item for item in plan.operations if item.object_id == "SRC-1")
    assert operation.operation == "update"
    assert operation.payload["title"] == "母本 v2"
    assert operation.payload["project_id"] == "16_章_-_彌賽亞，捨己"
    assert operation.payload["lineage"] == {"upstream_kind": "professor_notes"}
    assert operation.payload["source_url"].startswith("/resources/")
    assert operation.removed_fields == ()


def test_a_repackage_cannot_take_a_relation_endpoint_away() -> None:
    """The nine `DK-f0eac41a4244-CR0*` relations lost both of their endpoints.

    A re-extraction of one lecture has no opinion about a cross-lecture edge,
    which is exactly why it said nothing about it.
    """

    stored = {
        "claim_relation_id": "CR-1",
        "from_id": "CL-1",
        "to_id": "CL-2",
        "source_id": "CL-1",
        "target_id": "CL-2",
        "relation_type": "supports",
        "reason": "跨讲支持",
        "relation_review": {"reviewer": "同工"},
        "schema_version": 1,
        "review_status": "candidate",
        "visibility": "internal",
        "revision": 1,
    }
    package = {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": "PKG-REL",
        "claim_relations": [
            {
                "claim_relation_id": "CR-1",
                "from_id": "CL-1",
                "to_id": "CL-2",
                "relation_type": "extends",
            }
        ],
    }
    plan = build_change_set_plan(package, _stored("claim_relations", "CR-1", stored))

    operation = plan.operations[0]
    assert operation.payload["relation_type"] == "extends"
    assert operation.payload["source_id"] == "CL-1"
    assert operation.payload["target_id"] == "CL-2"
    assert operation.payload["relation_review"] == {"reviewer": "同工"}
    assert operation.payload["reason"] == "跨讲支持"
    assert operation.removed_fields == ()


def test_a_removal_has_to_be_stated_and_the_plan_names_it() -> None:
    """Deleting a field is still allowed -- by saying so, and it is reported.

    Nothing in the store could answer "what did that ingest remove": the
    operation rows carry two hashes and no field names, which is why 511
    erased values went unnoticed.
    """

    stored = normalize_package(_package())["source_documents"]["SRC-1"]
    stored.update({"project_id": "16_章", "lineage": {"upstream_kind": "professor_notes"}})

    package = _package()
    package["source_documents"][0]["project_id"] = None
    plan = build_change_set_plan(package, _stored("source_documents", "SRC-1", stored))

    operation = next(item for item in plan.operations if item.object_id == "SRC-1")
    assert operation.payload["project_id"] is None
    assert operation.payload["lineage"] == {"upstream_kind": "professor_notes"}
    assert operation.removed_fields == ("project_id",)
    summary = plan.as_dict()["summary"]
    assert summary["fields_removed"] == 1
    assert summary["removals"] == [
        {"collection": "source_documents", "object_id": "SRC-1", "fields": ["project_id"]}
    ]


def test_a_plan_sent_without_its_decisions_keeps_them() -> None:
    """`AUTHORING-CONTRACT-MIGRATION-01` emptied `decision_ids` on all three
    Matthew 16 plans and was undone 98 seconds later. The migration package
    carried the contract, not the decisions, and never said to drop them."""

    stored = {
        "plan_id": "CP-1",
        "title": "太16",
        "product_type": "exposition",
        "description": "",
        "decision_ids": ["CD-1", "CD-2"],
        "schema_version": 1,
        "review_status": "candidate",
        "visibility": "internal",
        "revision": 1,
    }
    package = {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": "AUTHORING-CONTRACT-MIGRATION-01",
        "product_plans": [
            {"plan_id": "CP-1", "title": "太16", "product_type": "exposition",
             "contract_id": "CT-1"}
        ],
    }
    plan = build_change_set_plan(package, _stored("composition_plans", "CP-1", stored))

    operation = next(item for item in plan.operations if item.object_id == "CP-1")
    assert operation.payload["decision_ids"] == ["CD-1", "CD-2"]
    assert operation.payload["contract_id"] == "CT-1"
    assert operation.removed_fields == ()


class _RecordingCursor:
    """Just enough cursor to watch what `apply_plan` writes."""

    def __init__(self, locked_row: tuple) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self._locked_row = locked_row
        self._last = ""

    def __enter__(self) -> "_RecordingCursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.statements.append((sql, params))
        self._last = sql

    def fetchone(self):
        if "FOR UPDATE" in self._last:
            return self._locked_row
        return None

    def fetchall(self):
        return []


class _RecordingConnection:
    def __init__(self, cursor: _RecordingCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> "_RecordingConnection":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def cursor(self) -> _RecordingCursor:
        return self._cursor


def test_dependency_invalidation_matches_any_pinned_manifest_record() -> None:
    cursor = _RecordingCursor(())
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)

    count = store._invalidate_dependencies(
        cursor,
        SimpleNamespace(change_set_id="CS-VIEWPOINT"),
        [("viewpoint_revisions", "CVR-PETER-1", 1, 2)],
        0,
    )

    select = next(
        (sql, params) for sql, params in cursor.statements
        if "collection='product_dependencies'" in sql and "SELECT object_id" in sql
    )
    assert count == 0
    assert "dependency_manifest" in select[0]
    assert select[1] == (
        "viewpoint_revisions",
        "CVR-PETER-1",
        '[{"collection":"viewpoint_revisions","record_id":"CVR-PETER-1"}]',
    )


def test_apply_records_which_fields_an_update_removed() -> None:
    """The removal outlives the session that caused it.

    `change_operations.details` is where the invalidation path already puts
    what a hash cannot say; a removal belongs there for the same reason.
    """

    stored = normalize_package(_package())["source_documents"]["SRC-1"]
    stored["project_id"] = "16_章"
    package = {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": "PKG-REMOVE",
        "source_documents": [
            {"source_id": "SRC-1", "source_type": "sermon_transcript",
             "title": "讲道", "project_id": None}
        ],
    }
    plan = build_change_set_plan(package, _stored("source_documents", "SRC-1", stored))

    cursor = _RecordingCursor((1, record_content_sha(stored), None))
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: _RecordingConnection(cursor)  # type: ignore[method-assign]

    result = store.apply_plan(plan)

    written = [
        params
        for sql, params in cursor.statements
        if "INSERT INTO wang_knowledge.change_operations" in sql
    ]
    details = [json.loads(params[-1]) for params in written]
    assert {"removed_fields": ["project_id"]} in details
    assert result["summary"]["removals"] == [
        {"collection": "source_documents", "object_id": "SRC-1", "fields": ["project_id"]}
    ]


def test_route_apply_cas_rejects_a_stale_conclusion_revision() -> None:
    cursor = _RecordingCursor(("CVR-NEW",))

    with pytest.raises(ChangeSetConflict, match="expected current revision CVR-OLD"):
        PostgresKnowledgeStore._assert_current_viewpoint_revisions(
            cursor, {"CV-1": "CVR-OLD"}
        )

    assert any("FOR UPDATE" in sql for sql, _ in cursor.statements)


def test_a_preserved_review_field_is_not_reported_as_removed() -> None:
    """Removal is read off the settled payload, not off the incoming package.

    A package that blanks `review_note` on an approved record has it put back;
    reporting a removal that did not happen teaches whoever reads these
    reports to stop reading them.
    """

    reviewed = normalize_package(_package())["claims"]["CL-1"]
    reviewed.update(
        {"review_status": "approved", "review_note": "同工已核对", "revision": 4}
    )
    package = _package()
    package["claims"][0].update({"statement": "更新后的候选文字", "review_note": None})
    plan = build_change_set_plan(package, _stored("claims", "CL-1", reviewed))

    operation = next(item for item in plan.operations if item.object_id == "CL-1")
    assert operation.payload["review_note"] == "同工已核对"
    assert operation.removed_fields == ()
