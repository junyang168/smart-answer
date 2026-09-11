from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.api.canonical_repository.postgres_store import (
    ChangeSetConflict,
    EXTRACTION_RECORD_COLLECTIONS,
    NON_LIVE_EXTRACTION_REFERENCE_COLLECTIONS,
    SEMANTIC_REFERENCE_COLLECTIONS,
    SOURCE_KEYS,
    PostgresKnowledgeStore,
    PostgresKnowledgeStoreError,
    build_active_snapshot,
    build_change_set_plan,
    normalize_package,
    record_content_sha,
    reviewed_relations_package,
    stored_operation_payload,
    sha256_json,
    uncoordinated_semantic_reference_blockers,
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


def test_source_fragment_transport_section_survives_store_normalization() -> None:
    package = _package()
    package["source_fragments"][0]["extraction_section_index"] = 7

    fragment = normalize_package(package)["source_fragments"]["FR-1"]

    assert fragment["extraction_section_index"] == 7


def test_change_set_fingerprint_is_bound_to_the_exact_before_snapshot() -> None:
    package = _package()
    absent = build_change_set_plan(package, {})
    prior = normalize_package(_package())["claims"]["CL-1"]
    prior["title"] = "older claim"
    existing = build_change_set_plan(
        package,
        {
            ("claims", "CL-1"): {
                "revision": 7,
                "content_sha256": record_content_sha(prior),
                "payload": prior,
            }
        },
    )

    assert absent.fingerprint_sha256 != existing.fingerprint_sha256
    assert absent.change_set_id != existing.change_set_id


def test_a_source_document_id_cannot_be_reused_for_another_transcript() -> None:
    package = _package()
    package["source_documents"][0]["transcript_id"] = "SERMON-NEW"
    current = normalize_package(_package())["source_documents"]["SRC-1"]
    current["transcript_id"] = "SERMON-OLD"

    with pytest.raises(PostgresKnowledgeStoreError, match="cannot change identity"):
        build_change_set_plan(
            package,
            {
                ("source_documents", "SRC-1"): {
                    "revision": 1,
                    "content_sha256": record_content_sha(current),
                    "payload": current,
                }
            },
        )


def test_package_cannot_declare_two_source_ids_for_one_transcript_identity() -> None:
    package = _package()
    package["source_documents"] = [
        {
            "source_id": "SRC-ONE",
            "source_type": "sermon_transcript",
            "transcript_id": "SERMON-1",
        },
        {
            "source_id": "SRC-TWO",
            "source_type": "sermon_transcript",
            "transcript_id": "SERMON-1",
        },
    ]

    with pytest.raises(PostgresKnowledgeStoreError, match="declared by both"):
        normalize_package(package)


def test_legacy_source_type_can_be_completed_without_changing_transcript_identity() -> None:
    package = _package()
    package["source_documents"][0]["transcript_id"] = "SERMON-1"
    legacy = normalize_package(package)["source_documents"]["SRC-1"]
    legacy["source_type"] = ""

    plan = build_change_set_plan(
        package,
        {
            ("source_documents", "SRC-1"): {
                "revision": 1,
                "content_sha256": record_content_sha(legacy),
                "payload": legacy,
            }
        },
    )

    assert any(row.object_id == "SRC-1" for row in plan.operations)


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


def test_package_rejects_an_id_reused_by_another_collection() -> None:
    package = _package()
    package["claims"][0]["claim_id"] = "E-1"

    with pytest.raises(PostgresKnowledgeStoreError, match="globally unique"):
        normalize_package(package)


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


def test_migration_enforces_global_record_identity() -> None:
    sql = Path(
        "backend/api/canonical_repository/migrations/005_global_object_id_uniqueness.sql"
    ).read_text(encoding="utf-8")
    assert "UNIQUE INDEX" in sql
    assert "wang_knowledge.objects (object_id)" in sql
    assert "source_documents_current_transcript_identity_unique_idx" in sql


def test_applying_an_empty_plan_is_a_database_no_op() -> None:
    plan = build_change_set_plan(
        {"schema_version": "wang_shared_knowledge_v1.3", "package_id": "EMPTY"},
        {},
    )
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: pytest.fail("empty plan must not open a transaction")  # type: ignore[method-assign]

    assert store.apply_plan(plan)["status"] == "unchanged"


def test_apply_rechecks_global_id_ownership_under_the_transaction_lock() -> None:
    operation = SimpleNamespace(
        collection="claims",
        object_id="GLOBAL-1",
        operation="create",
    )

    class ConflictingOwnerCursor(_RecordingCursor):
        def fetchall(self):
            if "WHERE object_id = ANY" in self._last:
                return [("evidence_steps", "GLOBAL-1")]
            return []

    with pytest.raises(ChangeSetConflict, match="already belongs to evidence_steps"):
        PostgresKnowledgeStore._assert_global_id_uniqueness(
            ConflictingOwnerCursor(None), SimpleNamespace(operations=(operation,))
        )


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


def test_one_dependency_keeps_every_change_reason_but_invalidates_once() -> None:
    class DependencyCursor(_RecordingCursor):
        def fetchall(self):
            if (
                "collection='product_dependencies'" in self._last
                and "SELECT object_id" in self._last
            ):
                return [(
                    "PD-1",
                    3,
                    {
                        "dependency_id": "PD-1",
                        "consumer_kind": "matthew_draft",
                        "consumer_id": "DRAFT-1",
                        "claim_id": "CL-1",
                        "status": "current",
                        "revision": 3,
                    },
                )]
            return []

    cursor = DependencyCursor(())
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    count = store._invalidate_dependencies(
        cursor,
        SimpleNamespace(change_set_id="CS-MULTI"),
        [
            ("claims", "CL-1", 1, 2),
            ("source_fragments", "SF-1", 1, 2),
        ],
        7,
    )

    updates = [
        params
        for sql, params in cursor.statements
        if "UPDATE wang_knowledge.objects SET revision" in sql
        and "product_dependencies" in sql
    ]
    assert count == 1
    assert len(updates) == 1
    updated = json.loads(updates[0][4])
    assert updated["invalidation_event_ids"] == [
        "IMPACT-CS-MULTI-claims-CL-1",
        "IMPACT-CS-MULTI-source_fragments-SF-1",
    ]
    impact_inserts = [
        params
        for sql, params in cursor.statements
        if "VALUES ('impact_events'" in sql and "wang_knowledge.objects" in sql
    ]
    assert [params[0] for params in impact_inserts] == [
        "IMPACT-CS-MULTI-claims-CL-1",
        "IMPACT-CS-MULTI-source_fragments-SF-1",
    ]


def test_generated_impact_event_cannot_collide_with_another_collection() -> None:
    class CollisionCursor(_RecordingCursor):
        def fetchall(self):
            if "WHERE object_id=%s FOR UPDATE" in self._last:
                return [("claims",)]
            if (
                "collection='product_dependencies'" in self._last
                and "SELECT object_id" in self._last
            ):
                return [(
                    "PD-1",
                    1,
                    {
                        "dependency_id": "PD-1",
                        "consumer_kind": "matthew_draft",
                        "consumer_id": "DRAFT-1",
                        "claim_id": "CL-1",
                        "status": "current",
                        "revision": 1,
                    },
                )]
            return []

    cursor = CollisionCursor(())
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)

    with pytest.raises(ChangeSetConflict, match="generated impact event"):
        store._invalidate_dependencies(
            cursor,
            SimpleNamespace(change_set_id="CS-COLLISION"),
            [("claims", "CL-1", 1, 2)],
            0,
        )

    assert not any(
        "VALUES ('impact_events'" in sql for sql, _ in cursor.statements
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


def test_apply_rejects_revision_drift_even_when_semantic_sha_is_unchanged() -> None:
    """A retire/revive can change revision without changing semantic bytes."""

    stored = normalize_package(_package())["source_documents"]["SRC-1"]
    changed = {
        "schema_version": "wang_shared_knowledge_v1.3",
        "source_documents": [
            {"source_id": "SRC-1", "source_type": "sermon_transcript", "title": "新标题"}
        ],
    }
    plan = build_change_set_plan(
        changed, _stored("source_documents", "SRC-1", stored)
    )
    cursor = _RecordingCursor((2, record_content_sha(stored), None))
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: _RecordingConnection(cursor)  # type: ignore[method-assign]

    with pytest.raises(ChangeSetConflict, match="expected revision 1, found 2"):
        store.apply_plan(plan)


def test_reextraction_cannot_leave_current_semantic_master_data_on_old_ids() -> None:
    from backend.api.canonical_repository.postgres_store import build_retirement_plan

    claim = normalize_package(_package())["claims"]["CL-1"]
    plan = build_retirement_plan(
        [("claims", "CL-1")],
        _stored("claims", "CL-1", claim),
        reason="new extraction generation",
        package_id="PKG-REEXTRACT",
    )

    class SemanticReferenceCursor(_RecordingCursor):
        def fetchall(self):
            if "SELECT collection, object_id, payload" in self._last:
                return [(
                    "viewpoint_claim_links",
                    "VCL-1",
                    {"link_id": "VCL-1", "claim_id": "CL-1"},
                )]
            return []

    cursor = SemanticReferenceCursor((1, record_content_sha(claim), None))
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: _RecordingConnection(cursor)  # type: ignore[method-assign]

    with pytest.raises(ChangeSetConflict, match="coordinated CVR update"):
        store.apply_plan(plan)

    assert uncoordinated_semantic_reference_blockers(
        plan,
        [
            (
                "viewpoint_claim_links",
                "VCL-1",
                {"link_id": "VCL-1", "claim_id": "CL-1"},
            )
        ],
    ) == ["viewpoint_claim_links/VCL-1 -> CL-1"]


def _obsolete_retirement_audit() -> dict[str, Any]:
    audit = {
        "schema_version": "wang_obsolete_candidate_batch_retirement_v1",
        "batch_id": "RB-OLD",
        "reason_code": "composition_plan_candidate_retired_by_draft_first",
        "selection_policy": "test",
        "status": "planned",
        "known_plan_ids": ["CP-OLD-S-abcdef123456"],
        "summary": {"total": 1},
        "records": [
            {
                "collection": "knowledge_routes",
                "object_id": "KR-OLD",
                "expected_revision": 1,
                "expected_content_sha256": "old-sha",
            }
        ],
    }
    audit["scope_sha256"] = sha256_json(audit)
    return audit


def _obsolete_retirement_plan(*extra: SimpleNamespace) -> SimpleNamespace:
    retired = SimpleNamespace(
        collection="knowledge_routes",
        object_id="KR-OLD",
        operation="retire",
        before_revision=1,
        before_sha256="old-sha",
    )
    return SimpleNamespace(operations=(retired, *extra))


class _ObsoleteRetirementCursor(_RecordingCursor):
    def __init__(self, rows: list[tuple[str, str, dict[str, Any]]]):
        super().__init__(None)
        self.rows = rows

    def fetchall(self):
        if "WHERE retired_at IS NULL FOR UPDATE" in self._last:
            return self.rows
        return []


def test_obsolete_candidate_retirement_is_rechecked_under_apply_lock() -> None:
    candidate = {
        "route_id": "KR-OLD",
        "target_id": "CP-OLD-S-abcdef123456",
        "review_status": "candidate",
        "visibility": "internal",
    }
    cursor = _ObsoleteRetirementCursor(
        [("knowledge_routes", "KR-OLD", candidate)]
    )

    PostgresKnowledgeStore._assert_obsolete_candidate_retirement(
        cursor, _obsolete_retirement_plan(), _obsolete_retirement_audit()
    )

    with pytest.raises(ChangeSetConflict, match="no longer pending candidate"):
        PostgresKnowledgeStore._assert_obsolete_candidate_retirement(
            _ObsoleteRetirementCursor(
                [
                    (
                        "knowledge_routes",
                        "KR-OLD",
                        {**candidate, "review_status": "approved"},
                    )
                ]
            ),
            _obsolete_retirement_plan(),
            _obsolete_retirement_audit(),
        )

    with pytest.raises(ChangeSetConflict, match="omitted obsolete candidate"):
        PostgresKnowledgeStore._assert_obsolete_candidate_retirement(
            _ObsoleteRetirementCursor([
                ("knowledge_routes", "KR-OLD", candidate),
                (
                    "composition_plans",
                    "CP-OLD-T-fedcba654321",
                    {
                        "plan_id": "CP-OLD-T-fedcba654321",
                        "review_status": "candidate",
                        "visibility": "internal",
                    },
                ),
            ]),
            _obsolete_retirement_plan(),
            _obsolete_retirement_audit(),
        )

    with pytest.raises(ChangeSetConflict, match="no longer pending candidate"):
        PostgresKnowledgeStore._assert_obsolete_candidate_retirement(
            _ObsoleteRetirementCursor([
                (
                    "knowledge_routes",
                    "KR-OLD",
                    {**candidate, "target_id": "CP-ANOTHER-S-abcdef123456"},
                )
            ]),
            _obsolete_retirement_plan(),
            _obsolete_retirement_audit(),
        )


def test_obsolete_candidate_apply_guard_blocks_external_and_arriving_refs() -> None:
    candidate = {
        "route_id": "KR-OLD",
        "target_id": "CP-OLD-S-abcdef123456",
        "review_status": "candidate",
        "visibility": "internal",
    }
    with pytest.raises(ChangeSetConflict, match="PD-OUTSIDE"):
        PostgresKnowledgeStore._assert_obsolete_candidate_retirement(
            _ObsoleteRetirementCursor(
                [
                    ("knowledge_routes", "KR-OLD", candidate),
                    ("product_dependencies", "PD-OUTSIDE", {"route_ids": ["KR-OLD"]}),
                ]
            ),
            _obsolete_retirement_plan(),
            _obsolete_retirement_audit(),
        )

    arriving = SimpleNamespace(
        collection="product_dependencies",
        object_id="PD-NEW",
        operation="create",
        before_revision=None,
        before_sha256=None,
        after_revision=1,
        payload={"dependency_id": "PD-NEW", "route_ids": ["KR-OLD"]},
    )
    with pytest.raises(ChangeSetConflict, match="planned product_dependencies/PD-NEW"):
        PostgresKnowledgeStore._assert_obsolete_candidate_retirement(
            _ObsoleteRetirementCursor(
                [("knowledge_routes", "KR-OLD", candidate)]
            ),
            _obsolete_retirement_plan(arriving),
            _obsolete_retirement_audit(),
        )


def test_obsolete_candidate_apply_guard_rejects_tampered_audit() -> None:
    audit = _obsolete_retirement_audit()
    audit["batch_id"] = "RB-TAMPERED"
    with pytest.raises(ChangeSetConflict, match="scope SHA"):
        PostgresKnowledgeStore._assert_obsolete_candidate_retirement(
            _ObsoleteRetirementCursor([]),
            _obsolete_retirement_plan(),
            audit,
        )


def test_already_retired_candidate_audit_allows_a_changed_package_arrival() -> None:
    audit = {
        "schema_version": "wang_obsolete_candidate_batch_retirement_v1",
        "batch_id": "RB-OLD",
        "reason_code": "composition_plan_candidate_retired_by_draft_first",
        "selection_policy": "test",
        "status": "already_retired",
        "known_plan_ids": ["CP-OLD-S-abcdef123456"],
        "summary": {"total": 0},
        "records": [],
    }
    audit["scope_sha256"] = sha256_json(audit)
    arrival = SimpleNamespace(
        collection="claims",
        object_id="CL-NEW",
        operation="create",
    )

    PostgresKnowledgeStore._assert_obsolete_candidate_retirement(
        _ObsoleteRetirementCursor([]),
        SimpleNamespace(operations=(arrival,)),
        audit,
    )
    with pytest.raises(ChangeSetConflict, match="has live rows again"):
        PostgresKnowledgeStore._assert_obsolete_candidate_retirement(
            _ObsoleteRetirementCursor([
                (
                    "composition_plans",
                    "CP-OLD-S-abcdef123456",
                    {"plan_id": "CP-OLD-S-abcdef123456"},
                )
            ]),
            SimpleNamespace(operations=(arrival,)),
            audit,
        )


def _stale_topic_identity_audit(*, status: str = "planned") -> dict[str, Any]:
    records = [] if status == "not_needed" else [{
        "collection": "topic_identity_reconciliations",
        "object_id": "TIR-STALE",
        "expected_revision": 1,
        "expected_content_sha256": "tir-sha",
        "stale_claim_ids": ["CL-OLD"],
    }]
    audit = {
        "schema_version": "wang_stale_pending_topic_identity_retirement_v1",
        "batch_id": "RB-TOPIC",
        "reason_code": "pending_topic_identity_invalidated_by_extraction_supersession",
        "selection_policy": "test",
        "status": status,
        "retired_extraction_ids_sha256": sha256_json(["CL-OLD"]),
        "summary": {
            "topic_identity_reconciliations": len(records),
            "total": len(records),
        },
        "records": records,
    }
    audit["scope_sha256"] = sha256_json(audit)
    return audit


def _stale_topic_identity_plan(*extra: SimpleNamespace) -> SimpleNamespace:
    extraction = SimpleNamespace(
        collection="claims",
        object_id="CL-OLD",
        operation="retire",
        before_revision=1,
        before_sha256="claim-sha",
    )
    identity = SimpleNamespace(
        collection="topic_identity_reconciliations",
        object_id="TIR-STALE",
        operation="retire",
        before_revision=1,
        before_sha256="tir-sha",
    )
    return SimpleNamespace(operations=(extraction, identity, *extra))


def test_stale_topic_identity_retirement_is_rechecked_under_apply_lock() -> None:
    stale = {
        "reconciliation_id": "TIR-STALE",
        "origin_batch_id": "RB-TOPIC",
        "claim_ids": ["CL-OLD", "CL-KEPT"],
        "status": "pending_new",
        "review_status": "candidate",
        "visibility": "internal",
    }
    PostgresKnowledgeStore._assert_stale_pending_topic_identity_retirement(
        _ObsoleteRetirementCursor([
            ("topic_identity_reconciliations", "TIR-STALE", stale)
        ]),
        _stale_topic_identity_plan(),
        _stale_topic_identity_audit(),
    )

    with pytest.raises(ChangeSetConflict, match="no longer the audited"):
        PostgresKnowledgeStore._assert_stale_pending_topic_identity_retirement(
            _ObsoleteRetirementCursor([
                (
                    "topic_identity_reconciliations",
                    "TIR-STALE",
                    {**stale, "status": "resolved"},
                )
            ]),
            _stale_topic_identity_plan(),
            _stale_topic_identity_audit(),
        )


def test_stale_topic_identity_guard_detects_omission_reappearance_and_refs() -> None:
    stale = {
        "reconciliation_id": "TIR-OTHER",
        "origin_batch_id": "RB-TOPIC",
        "claim_ids": ["CL-OLD"],
        "status": "pending_match",
        "review_status": "candidate",
        "visibility": "internal",
    }
    no_identity_plan = SimpleNamespace(
        operations=(_stale_topic_identity_plan().operations[0],)
    )
    with pytest.raises(ChangeSetConflict, match="omitted stale identity"):
        PostgresKnowledgeStore._assert_stale_pending_topic_identity_retirement(
            _ObsoleteRetirementCursor([
                ("topic_identity_reconciliations", "TIR-OTHER", stale)
            ]),
            no_identity_plan,
            _stale_topic_identity_audit(status="not_needed"),
        )

    audited = {
        **stale,
        "reconciliation_id": "TIR-STALE",
    }
    with pytest.raises(ChangeSetConflict, match="PD-OUTSIDE"):
        PostgresKnowledgeStore._assert_stale_pending_topic_identity_retirement(
            _ObsoleteRetirementCursor([
                ("topic_identity_reconciliations", "TIR-STALE", audited),
                ("product_dependencies", "PD-OUTSIDE", {"ids": ["TIR-STALE"]}),
            ]),
            _stale_topic_identity_plan(),
            _stale_topic_identity_audit(),
        )


def test_retiring_a_source_alias_cannot_leave_semantic_master_data_on_it() -> None:
    from backend.api.canonical_repository.postgres_store import build_retirement_plan

    source = normalize_package(_package())["source_documents"]["SRC-1"]
    plan = build_retirement_plan(
        [("source_documents", "SRC-1")],
        _stored("source_documents", "SRC-1", source),
        reason="replace source alias",
        package_id="PKG-REEXTRACT",
    )

    class SemanticReferenceCursor(_RecordingCursor):
        def fetchall(self):
            if "SELECT collection, object_id, payload" in self._last:
                return [(
                    "argument_route_attestations",
                    "ARA-1",
                    {"attestation_id": "ARA-1", "source_id": "SRC-1"},
                )]
            return []

    cursor = SemanticReferenceCursor((1, record_content_sha(source), None))
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: _RecordingConnection(cursor)  # type: ignore[method-assign]

    with pytest.raises(ChangeSetConflict, match="coordinated CVR update"):
        store.apply_plan(plan)


def test_updating_a_stable_source_document_does_not_invalidate_its_source_id() -> None:
    current = normalize_package(_package())["source_documents"]["SRC-1"]
    package = _package()
    package["source_documents"][0]["source_sha256"] = "new-body"
    plan = build_change_set_plan(
        package, _stored("source_documents", "SRC-1", current)
    )
    cursor = _RecordingCursor(None)

    PostgresKnowledgeStore._assert_no_uncoordinated_semantic_references(cursor, plan)

    assert cursor.statements == []


def test_updating_an_extraction_record_preserves_live_semantic_references() -> None:
    extraction = SimpleNamespace(
        collection="claims",
        object_id="CL-STABLE",
        operation="update",
        payload={"claim_id": "CL-STABLE", "review_status": "reviewed"},
        after_revision=2,
    )
    cursor = _RecordingCursor(None)

    PostgresKnowledgeStore._assert_no_uncoordinated_semantic_references(
        cursor, SimpleNamespace(operations=(extraction,))
    )

    assert cursor.statements == []


def test_a_planned_semantic_update_must_remove_the_predecessor_reference() -> None:
    extraction = SimpleNamespace(
        collection="claims",
        object_id="CL-OLD",
        operation="retire",
        payload={"claim_id": "CL-OLD"},
        after_revision=2,
    )
    semantic = SimpleNamespace(
        collection="viewpoint_claim_links",
        object_id="VCL-1",
        operation="update",
        payload={"link_id": "VCL-1", "claim_id": "CL-OLD"},
        after_revision=2,
    )

    class PlannedSemanticCursor(_RecordingCursor):
        def fetchall(self):
            if "SELECT collection, object_id, payload" in self._last:
                return [(
                    "viewpoint_claim_links",
                    "VCL-1",
                    {"link_id": "VCL-1", "claim_id": "CL-OLD"},
                )]
            return []

    with pytest.raises(ChangeSetConflict, match="planned viewpoint_claim_links/VCL-1"):
        PostgresKnowledgeStore._assert_no_uncoordinated_semantic_references(
            PlannedSemanticCursor(None),
            SimpleNamespace(operations=(extraction, semantic)),
        )


@pytest.mark.parametrize(
    ("collection", "payload"),
    [
        (
            "viewpoint_claim_links",
            {
                "viewpoint_claim_link_id": "VCL-1",
                "claim_id": "CL-CURRENT",
                "evidence_bindings": [{
                    "evidence_step_id": "E-OLD",
                    "source_fragment_id": "FR-CURRENT",
                }],
            },
        ),
        (
            "viewpoint_proposition_units",
            {
                "proposition_unit_id": "VPU-1",
                "parent_claim_id": "CL-CURRENT",
                "source_id": "SRC-CURRENT",
                "evidence_bindings": [{
                    "evidence_step_id": "E-OLD",
                    "source_fragment_id": "FR-CURRENT",
                }],
            },
        ),
    ],
)
def test_nested_live_evidence_bindings_block_retirement(
    collection: str, payload: dict[str, Any]
) -> None:
    extraction = SimpleNamespace(
        collection="evidence_steps",
        object_id="E-OLD",
        operation="retire",
        payload={"evidence_step_id": "E-OLD"},
        after_revision=2,
    )

    class NestedReferenceCursor(_RecordingCursor):
        def fetchall(self):
            if "SELECT collection, object_id, payload" in self._last:
                return [(collection, "SEM-1", payload)]
            return []

    with pytest.raises(ChangeSetConflict, match="E-OLD"):
        PostgresKnowledgeStore._assert_no_uncoordinated_semantic_references(
            NestedReferenceCursor(None),
            SimpleNamespace(operations=(extraction,)),
        )


def test_retired_composition_workflow_is_not_live_cvr_master_data() -> None:
    extraction = SimpleNamespace(
        collection="claims",
        object_id="CL-OLD",
        operation="retire",
        payload={"claim_id": "CL-OLD"},
        after_revision=2,
    )
    cursor = _RecordingCursor(None)

    PostgresKnowledgeStore._assert_no_uncoordinated_semantic_references(
        cursor, SimpleNamespace(operations=(extraction,))
    )

    selected_collections = cursor.statements[0][1][0]
    assert "composition_plans" not in selected_collections
    assert "composition_decisions" not in selected_collections


def test_semantic_lineage_and_transcript_metadata_are_not_live_id_references() -> None:
    extraction = SimpleNamespace(
        collection="claims",
        object_id="CL-OLD",
        operation="retire",
        payload={"claim_id": "CL-OLD"},
        after_revision=2,
    )
    semantic = SimpleNamespace(
        collection="viewpoint_claim_links",
        object_id="VCL-1",
        operation="update",
        payload={
            "viewpoint_claim_link_id": "VCL-1",
            "claim_id": "CL-NEW",
            "previous_claim_id": "CL-OLD",
        },
        after_revision=2,
    )

    class LineageCursor(_RecordingCursor):
        def fetchall(self):
            if "SELECT collection, object_id, payload" in self._last:
                return [(
                    "viewpoint_claim_links",
                    "VCL-1",
                    {
                        "viewpoint_claim_link_id": "VCL-1",
                        "claim_id": "CL-OLD",
                        "transcript_id": "CL-OLD",
                    },
                )]
            return []

    PostgresKnowledgeStore._assert_no_uncoordinated_semantic_references(
        LineageCursor(None),
        SimpleNamespace(operations=(extraction, semantic)),
    )


def test_an_unclassified_future_semantic_reference_field_fails_closed() -> None:
    extraction = SimpleNamespace(
        collection="evidence_steps",
        object_id="E-OLD",
        operation="retire",
        payload={"evidence_step_id": "E-OLD"},
        after_revision=2,
    )

    class FutureFieldCursor(_RecordingCursor):
        def fetchall(self):
            if "SELECT collection, object_id, payload" in self._last:
                return [(
                    "viewpoint_claim_links",
                    "VCL-1",
                    {
                        "viewpoint_claim_link_id": "VCL-1",
                        "claim_id": "CL-CURRENT",
                        "future_grounding_ids": ["E-OLD"],
                    },
                )]
            return []

    with pytest.raises(ChangeSetConflict, match="unclassified id field"):
        PostgresKnowledgeStore._assert_no_uncoordinated_semantic_references(
            FutureFieldCursor(None),
            SimpleNamespace(operations=(extraction,)),
        )


def test_an_unclassified_mapping_key_reference_fails_closed() -> None:
    extraction = SimpleNamespace(
        collection="evidence_steps",
        object_id="E-OLD",
        operation="retire",
        payload={"evidence_step_id": "E-OLD"},
        after_revision=2,
    )

    class KeyedReferenceCursor(_RecordingCursor):
        def fetchall(self):
            if "SELECT collection, object_id, payload" in self._last:
                return [(
                    "viewpoint_claim_links",
                    "VCL-1",
                    {
                        "viewpoint_claim_link_id": "VCL-1",
                        "claim_id": "CL-CURRENT",
                        "future_evidence_weights": {"E-OLD": 0.7},
                    },
                )]
            return []

    with pytest.raises(ChangeSetConflict, match="<key>=E-OLD"):
        PostgresKnowledgeStore._assert_no_uncoordinated_semantic_references(
            KeyedReferenceCursor(None),
            SimpleNamespace(operations=(extraction,)),
        )


def test_every_registered_collection_classifies_extraction_reference_semantics() -> None:
    from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS

    classified = (
        EXTRACTION_RECORD_COLLECTIONS
        | {"source_documents"}
        | SEMANTIC_REFERENCE_COLLECTIONS
        | NON_LIVE_EXTRACTION_REFERENCE_COLLECTIONS
    )
    assert set(KNOWLEDGE_COLLECTIONS) == classified


def test_retired_alias_text_in_transcript_metadata_does_not_block_repair() -> None:
    alias = SimpleNamespace(
        collection="source_documents",
        object_id="SERMON-1",
        operation="retire",
        payload={"source_id": "SERMON-1"},
        after_revision=2,
    )

    class MetadataCursor(_RecordingCursor):
        def fetchall(self):
            if "SELECT collection, object_id, payload" in self._last:
                return [(
                    "argument_route_attestations",
                    "ARA-1",
                    {
                        "argument_route_attestation_id": "ARA-1",
                        "source_id": "SRC-NEW",
                        "transcript_id": "SERMON-1",
                    },
                )]
            return []

    PostgresKnowledgeStore._assert_no_uncoordinated_semantic_references(
        MetadataCursor(None), SimpleNamespace(operations=(alias,))
    )


def test_untyped_current_source_document_blocks_a_new_source_identity() -> None:
    package = _package()
    package["source_documents"][0]["transcript_id"] = "SERMON-1"
    plan = build_change_set_plan(package, {})

    class UntypedSourceCursor(_RecordingCursor):
        def fetchall(self):
            if "btrim(COALESCE(payload->>'source_type',''))" in self._last:
                return [("SRC-LEGACY", "", "SERMON-1")]
            return []

    cursor = UntypedSourceCursor(None)
    with pytest.raises(ChangeSetConflict, match="has no source_type"):
        PostgresKnowledgeStore._assert_source_identity_uniqueness(cursor, plan)


def test_apply_rejects_two_incoming_source_ids_for_one_transcript_identity() -> None:
    first = SimpleNamespace(
        collection="source_documents",
        object_id="SRC-ONE",
        operation="create",
        payload={
            "source_id": "SRC-ONE",
            "source_type": "sermon_transcript",
            "transcript_id": "SERMON-1",
        },
        after_revision=1,
    )
    second = SimpleNamespace(
        collection="source_documents",
        object_id="SRC-TWO",
        operation="create",
        payload={
            "source_id": "SRC-TWO",
            "source_type": "sermon_transcript",
            "transcript_id": "SERMON-1",
        },
        after_revision=1,
    )

    with pytest.raises(ChangeSetConflict, match="Multiple incoming SourceDocuments"):
        PostgresKnowledgeStore._assert_source_identity_uniqueness(
            _RecordingCursor(None), SimpleNamespace(operations=(first, second))
        )


def _edge_operation(
    edge_id: str, from_id: str, to_id: str, *, operation: str = "create"
) -> SimpleNamespace:
    return SimpleNamespace(
        collection="claim_relations",
        object_id=edge_id,
        operation=operation,
        payload={
            "claim_relation_id": edge_id,
            "from_id": from_id,
            "to_id": to_id,
            "relation_type": "supports",
        },
        after_revision=1,
    )


def test_apply_rejects_an_incoming_self_edge() -> None:
    with pytest.raises(ChangeSetConflict, match="points to itself"):
        PostgresKnowledgeStore._assert_edge_integrity(
            _RecordingCursor(None),
            SimpleNamespace(operations=(_edge_operation("CR-1", "CL-1", "CL-1"),)),
        )


def test_apply_rejects_retiring_an_endpoint_without_its_current_edge() -> None:
    retiring_claim = SimpleNamespace(
        collection="claims",
        object_id="CL-1",
        operation="retire",
        payload={"claim_id": "CL-1"},
        after_revision=2,
    )

    class SurvivingEdgeCursor(_RecordingCursor):
        def fetchall(self):
            if "AND (from_id = ANY" in self._last:
                return [("claim_relations", "CR-1", "CL-1", "CL-2")]
            return []

    with pytest.raises(ChangeSetConflict, match="would leave current edge"):
        PostgresKnowledgeStore._assert_edge_integrity(
            SurvivingEdgeCursor(None),
            SimpleNamespace(operations=(retiring_claim,)),
        )


def test_apply_allows_an_edge_update_that_removes_its_retiring_endpoint() -> None:
    retiring_claim = SimpleNamespace(
        collection="claims",
        object_id="CL-1",
        operation="retire",
        payload={"claim_id": "CL-1"},
        after_revision=2,
    )
    updated_edge = _edge_operation(
        "CR-1", "CL-3", "CL-2", operation="update"
    )

    class RepointedEdgeCursor(_RecordingCursor):
        def fetchall(self):
            if "AND (from_id = ANY" in self._last:
                return [("claim_relations", "CR-1", "CL-1", "CL-2")]
            if "SELECT collection, object_id FROM wang_knowledge.objects" in self._last:
                return [("claims", "CL-2"), ("claims", "CL-3")]
            if "FROM wang_knowledge.edges" in self._last:
                return [("claim_relations", "CR-1", "CL-1", "CL-2", "supports")]
            return []

    PostgresKnowledgeStore._assert_edge_integrity(
        RepointedEdgeCursor(None),
        SimpleNamespace(operations=(retiring_claim, updated_edge)),
    )


def test_apply_rejects_retiring_a_fragment_still_cited_by_a_live_step() -> None:
    retiring_fragment = SimpleNamespace(
        collection="source_fragments",
        object_id="FR-OLD",
        operation="retire",
        payload={"fragment_id": "FR-OLD"},
        after_revision=2,
    )

    class SurvivingStepCursor(_RecordingCursor):
        def fetchall(self):
            if "WHERE collection = ANY" in self._last:
                return [(
                    "evidence_steps",
                    "E-1",
                    {"evidence_step_id": "E-1", "source_fragment_id": "FR-OLD"},
                )]
            return []

    with pytest.raises(ChangeSetConflict, match="E-1 -> FR-OLD"):
        PostgresKnowledgeStore._assert_no_dangling_package_references(
            SurvivingStepCursor(None),
            SimpleNamespace(operations=(retiring_fragment,)),
        )


def test_apply_rejects_duplicate_semantic_edges_in_one_change_set() -> None:
    plan = SimpleNamespace(
        operations=(
            _edge_operation("CR-1", "CL-1", "CL-2"),
            _edge_operation("CR-2", "CL-1", "CL-2"),
        )
    )

    with pytest.raises(ChangeSetConflict, match="Duplicate semantic edge"):
        PostgresKnowledgeStore._assert_edge_integrity(_RecordingCursor(None), plan)


def test_apply_rejects_a_dangling_incoming_edge_endpoint() -> None:
    class EndpointCursor(_RecordingCursor):
        def fetchall(self):
            if "SELECT collection, object_id FROM wang_knowledge.objects" in self._last:
                return [("claims", "CL-1")]
            return []

    with pytest.raises(ChangeSetConflict, match="non-current endpoints: CL-2"):
        PostgresKnowledgeStore._assert_edge_integrity(
            EndpointCursor(None),
            SimpleNamespace(operations=(_edge_operation("CR-1", "CL-1", "CL-2"),)),
        )


def test_apply_rejects_an_existing_semantic_edge_under_another_id() -> None:
    class ExistingEdgeCursor(_RecordingCursor):
        def fetchall(self):
            if "SELECT collection, object_id FROM wang_knowledge.objects" in self._last:
                return [("claims", "CL-1"), ("claims", "CL-2")]
            if "FROM wang_knowledge.edges" in self._last:
                return [("claim_relations", "CR-OLD", "CL-1", "CL-2", "supports")]
            return []

    with pytest.raises(ChangeSetConflict, match="already has another current ID"):
        PostgresKnowledgeStore._assert_edge_integrity(
            ExistingEdgeCursor(None),
            SimpleNamespace(operations=(_edge_operation("CR-NEW", "CL-1", "CL-2"),)),
        )


def test_edge_duplicate_check_uses_the_planned_signature_for_an_update() -> None:
    updated = _edge_operation("CR-1", "CL-1", "CL-3", operation="update")
    created = _edge_operation("CR-2", "CL-1", "CL-2")

    class UpdatedSignatureCursor(_RecordingCursor):
        def fetchall(self):
            if "SELECT collection, object_id FROM wang_knowledge.objects" in self._last:
                return [
                    ("claims", "CL-1"),
                    ("claims", "CL-2"),
                    ("claims", "CL-3"),
                ]
            if "FROM wang_knowledge.edges" in self._last:
                return [("claim_relations", "CR-1", "CL-1", "CL-2", "supports")]
            return []

    PostgresKnowledgeStore._assert_edge_integrity(
        UpdatedSignatureCursor(None),
        SimpleNamespace(operations=(updated, created)),
    )


def test_apply_rejects_an_endpoint_from_the_wrong_collection() -> None:
    class WrongTypeCursor(_RecordingCursor):
        def fetchall(self):
            if "SELECT collection, object_id FROM wang_knowledge.objects" in self._last:
                return [("evidence_steps", "CL-1"), ("claims", "CL-2")]
            return []

    with pytest.raises(ChangeSetConflict, match="belongs to evidence_steps"):
        PostgresKnowledgeStore._assert_edge_integrity(
            WrongTypeCursor(None),
            SimpleNamespace(operations=(_edge_operation("CR-1", "CL-1", "CL-2"),)),
        )


def test_apply_rejects_an_endpoint_with_ambiguous_global_ownership() -> None:
    class AmbiguousOwnerCursor(_RecordingCursor):
        def fetchall(self):
            if "SELECT collection, object_id FROM wang_knowledge.objects" in self._last:
                return [
                    ("claims", "CL-1"),
                    ("evidence_steps", "CL-1"),
                    ("claims", "CL-2"),
                ]
            return []

    with pytest.raises(ChangeSetConflict, match="ambiguous global ownership"):
        PostgresKnowledgeStore._assert_edge_integrity(
            AmbiguousOwnerCursor(None),
            SimpleNamespace(operations=(_edge_operation("CR-1", "CL-1", "CL-2"),)),
        )


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
