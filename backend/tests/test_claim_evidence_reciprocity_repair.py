from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import backend.pipeline.claim_evidence_reciprocity_repair as repair_module
from backend.api.canonical_repository.postgres_store import (
    CLAIM_EVIDENCE_BACKUP_REQUIRED_TABLES,
    build_product_dependency_active_snapshot,
    record_content_sha,
    sha256_json,
)
from backend.pipeline.claim_evidence_reciprocity_repair import (
    AUTHORITY_SEALED_REVIEWED_SOURCE,
    ClaimEvidenceReciprocityRepairError,
    DISPOSITION_ADD_REVERSE,
    DISPOSITION_EXACT_REPLAY,
    DISPOSITION_SOURCE_RERUN,
    MISMATCH_CLAIM_ONLY,
    MISMATCH_EVIDENCE_ONLY,
    PREREQUISITES_MANIFEST_SCHEMA_VERSION,
    apply_sealed_plan,
    build_reciprocity_audit,
    build_post_apply_result,
    build_repair_plan,
    deserialize_repair_plan,
    freeze_claim_evidence_reciprocity_input,
    seal_artifact,
    verify_postgres_backup_dump,
)


def _record(
    collection: str,
    object_id: str,
    payload: dict[str, Any],
    *,
    revision: int = 3,
    human_review: bool = False,
) -> dict[str, Any]:
    value = {**payload, "revision": revision}
    content_sha = record_content_sha(value)
    change_set_id = f"KCS-{collection}-{object_id}-{revision}"
    events = []
    if human_review:
        events.append(
            {
                "review_event_id": f"REV-{object_id}-{revision}",
                "collection": collection,
                "object_id": object_id,
                "object_revision": revision,
                "reviewer_kind": "human",
                "reviewer_id": "editor",
                "decision": value["review_status"],
                "reason": "current human ruling",
                "artifact": {"change_set_id": change_set_id},
            }
        )
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
            "fingerprint_sha256": "a" * 64,
            "source_kind": "review_decision" if human_review else "knowledge_package",
            "source_sha256": "b" * 64,
            "status": "applied",
        },
        "producer_operation": {
            "change_set_id": change_set_id,
            "operation_index": 0,
            "operation": "update",
            "collection": collection,
            "object_id": object_id,
            "before_sha256": "d" * 64,
            "after_sha256": content_sha,
            "before_revision": revision - 1,
            "after_revision": revision,
            "details": {},
        },
        "review_events": events,
        "source_lineage": {
            "source_document_ids": ["SRC-1"],
            "source_package_sha256": "c" * 64,
        },
    }


def _claim(
    claim_id: str,
    evidence_ids: list[str],
    *,
    status: str = "approved",
    human_review: bool = True,
) -> dict[str, Any]:
    return _record(
        "claims",
        claim_id,
        {
            "claim_id": claim_id,
            "statement": f"claim {claim_id}",
            "claim_type": "teaching",
            "evidence_step_ids": evidence_ids,
            "review_status": status,
            "visibility": "internal",
        },
        human_review=human_review,
    )


def _evidence(
    evidence_id: str,
    claim_ids: list[str],
    *,
    status: str = "system_verified",
    human_review: bool = False,
) -> dict[str, Any]:
    return _record(
        "evidence_steps",
        evidence_id,
        {
            "evidence_step_id": evidence_id,
            "statement": f"evidence {evidence_id}",
            "produced_claim_ids": claim_ids,
            "support_eligibility": "eligible",
            "review_status": status,
            "visibility": "internal",
            "source_fragment_ids": ["FRAG-1"],
        },
        human_review=human_review,
    )


def test_human_claim_only_projects_only_the_evidence_reverse_index() -> None:
    records = [_claim("CL-1", ["EV-1"]), _evidence("EV-1", [])]

    audit = build_reciprocity_audit(records)

    pair = audit["pairs"][0]
    assert pair["mismatch_type"] == MISMATCH_CLAIM_ONLY
    assert pair["disposition"] == DISPOSITION_ADD_REVERSE
    assert pair["blocks_apply"] is False
    plan_artifact = build_repair_plan(audit, records)
    loaded, change_set = deserialize_repair_plan(plan_artifact)
    assert loaded["apply_allowed"] is True
    assert len(change_set.operations) == 1
    operation = change_set.operations[0]
    assert operation.collection == "evidence_steps"
    assert operation.object_id == "EV-1"
    assert operation.payload["produced_claim_ids"] == ["CL-1"]
    assert operation.payload["statement"] == "evidence EV-1"
    assert not any(row.collection == "claims" for row in change_set.operations)


def test_producer_operation_must_match_current_object_version() -> None:
    claim = _claim("CL-1", ["EV-1"])
    claim["producer_operation"]["after_sha256"] = "f" * 64

    with pytest.raises(
        ClaimEvidenceReciprocityRepairError,
        match="producer operation does not prove current ObjectVersion",
    ):
        build_reciprocity_audit([claim, _evidence("EV-1", [])])


def test_revived_row_accepts_object_version_compatibility_revision_difference() -> None:
    claim = _claim("CL-1", ["EV-1"])
    evidence = _evidence("EV-1", ["CL-1"])
    evidence["payload"]["revision"] = 1
    evidence["content_sha256"] = record_content_sha(evidence["payload"])

    audit = build_reciprocity_audit([claim, evidence])

    assert audit["status"] == "clean"


def test_human_evidence_only_defaults_to_manual_and_never_deletes() -> None:
    records = [_claim("CL-1", []), _evidence("EV-1", ["CL-1"])]

    audit = build_reciprocity_audit(records)
    assert audit["pairs"][0]["mismatch_type"] == MISMATCH_EVIDENCE_ONLY
    assert audit["pairs"][0]["disposition"] == "manual_adjudication"

    plan, change_set = deserialize_repair_plan(build_repair_plan(audit, records))
    assert plan["apply_allowed"] is False
    assert len(plan["queues"]["manual_adjudication"]) == 1
    assert change_set.operations == ()


def test_superseded_claim_and_human_evidence_do_not_authorize_pair_repair() -> None:
    superseded = [
        _claim("CL-1", ["EV-1"], status="superseded", human_review=True),
        _evidence("EV-1", []),
    ]
    superseded_audit = build_reciprocity_audit(superseded)
    assert superseded_audit["pairs"][0]["disposition"] == "manual_adjudication"

    evidence_human = [
        _claim("CL-1", []),
        _evidence("EV-1", ["CL-1"], status="approved", human_review=True),
    ]
    evidence_audit = build_reciprocity_audit(evidence_human)
    assert evidence_audit["pairs"][0]["disposition"] == "manual_adjudication"
    assert (
        evidence_audit["pairs"][0]["reason_code"]
        == "current_human_evidence_requires_adjudication"
    )


def test_multiple_human_pair_fixes_coalesce_to_one_evidence_update() -> None:
    records = [
        _claim("CL-2", ["EV-1"]),
        _claim("CL-1", ["EV-1"]),
        _claim("CL-0", ["EV-1"]),
        _evidence("EV-1", ["CL-0"]),
    ]

    artifact = build_repair_plan(build_reciprocity_audit(records), records)
    _, change_set = deserialize_repair_plan(artifact)

    assert len(change_set.operations) == 1
    assert change_set.operations[0].payload["produced_claim_ids"] == [
        "CL-0",
        "CL-1",
        "CL-2",
    ]
    assert artifact["direct_projection_states"] == [
        {
            "evidence_step_id": "EV-1",
            "expected_revision": 3,
            "expected_content_sha256": records[-1]["content_sha256"],
            "before_produced_claim_ids": ["CL-0"],
            "after_produced_claim_ids": ["CL-0", "CL-1", "CL-2"],
        }
    ]


def test_candidate_and_stale_human_review_never_authorize_direct_repair() -> None:
    candidate = _claim(
        "CL-CANDIDATE", ["EV-1"], status="candidate", human_review=False
    )
    stale = _claim("CL-STALE", ["EV-1"])
    stale["review_events"][0]["object_revision"] = 2
    records = [candidate, stale, _evidence("EV-1", [])]

    audit = build_reciprocity_audit(records)
    dispositions = {
        row["claim_id"]: row["disposition"]
        for row in audit["pairs"]
        if row["mismatch_type"] == MISMATCH_CLAIM_ONLY
    }
    assert dispositions == {
        "CL-CANDIDATE": DISPOSITION_SOURCE_RERUN,
        "CL-STALE": "manual_adjudication",
    }
    plan = build_repair_plan(audit, records)
    assert plan["apply_allowed"] is False
    assert len(plan["queues"]["authoritative_source_rerun"]) == 1
    assert len(plan["queues"]["manual_adjudication"]) == 1
    assert plan["change_set"]["summary"]["operations"] == 0


def test_human_review_required_and_current_human_evidence_are_manual() -> None:
    needs_human = [
        _claim(
            "CL-1",
            ["EV-1"],
            status="human_review_required",
            human_review=False,
        ),
        _evidence("EV-1", []),
    ]
    needs_human_audit = build_reciprocity_audit(needs_human)
    assert needs_human_audit["pairs"][0]["disposition"] == "manual_adjudication"

    human_evidence = [
        _claim("CL-1", ["EV-1"], status="candidate", human_review=False),
        _evidence("EV-1", [], status="approved", human_review=True),
    ]
    evidence_audit = build_reciprocity_audit(human_evidence)
    assert evidence_audit["pairs"][0]["disposition"] == "manual_adjudication"
    assert evidence_audit["pairs"][0]["reason_code"] == (
        "current_human_evidence_requires_adjudication"
    )


def test_human_event_must_name_the_current_object_version_change_set() -> None:
    claim = _claim("CL-1", ["EV-1"])
    claim["review_events"][0]["artifact"]["change_set_id"] = "KCS-FORGED"
    records = [claim, _evidence("EV-1", [])]

    audit = build_reciprocity_audit(records)

    assert audit["pairs"][0]["disposition"] == "manual_adjudication"
    assert audit["pairs"][0]["blocks_apply"] is True
    assert build_repair_plan(audit, records)["apply_allowed"] is False


def test_human_event_requires_a_review_decision_object_version_producer() -> None:
    claim = _claim("CL-1", ["EV-1"])
    claim["producer_change_set"]["source_kind"] = "knowledge_package"
    records = [claim, _evidence("EV-1", [])]

    audit = build_reciprocity_audit(records)

    assert audit["pairs"][0]["disposition"] == "manual_adjudication"
    assert audit["pairs"][0]["blocks_apply"] is True
    plan = build_repair_plan(audit, records)
    assert plan["apply_allowed"] is False
    assert plan["change_set"]["summary"]["operations"] == 0


def test_sealed_reviewed_source_is_an_explicit_replay_queue_not_a_patch() -> None:
    records = [
        _claim("CL-1", ["EV-1"], status="ai_consensus_reviewed", human_review=False),
        _evidence("EV-1", []),
    ]
    authority = [
        {
            "claim_id": "CL-1",
            "evidence_step_id": "EV-1",
            "authority_class": AUTHORITY_SEALED_REVIEWED_SOURCE,
            "package_id": "PACKAGE-1",
            "package_sha256": "1" * 64,
            "reviewed_artifact_sha256": "2" * 64,
            "historical_change_set_id": "KCS-PACKAGE-1",
            "review_completion": "complete",
        }
    ]

    audit = build_reciprocity_audit(records, authority_records=authority)
    assert audit["pairs"][0]["disposition"] == DISPOSITION_EXACT_REPLAY
    plan = build_repair_plan(audit, records)
    assert plan["apply_allowed"] is False
    assert len(plan["queues"]["exact_source_replay"]) == 1
    assert plan["change_set"]["summary"]["operations"] == 0


def test_duplicate_reference_is_not_silently_collapsed() -> None:
    records = [_claim("CL-1", ["EV-1", "EV-1"]), _evidence("EV-1", [])]

    audit = build_reciprocity_audit(records)

    assert audit["status"] == "blocked"
    assert audit["counts"]["claim_references"] == 2
    assert audit["counts"]["duplicate_array_references"] == 1
    assert audit["duplicate_references"] == [
        {
            "collection": "claims",
            "object_id": "CL-1",
            "field": "evidence_step_ids",
            "referenced_id": "EV-1",
            "occurrences": 2,
        }
    ]
    plan = build_repair_plan(audit, records)
    assert plan["apply_allowed"] is False
    assert plan["change_set"]["summary"]["operations"] == 0
    assert len(plan["queues"]["manual_adjudication"]) == 1


def test_duplicate_produced_claim_target_yields_a_blocked_plan_not_a_crash() -> None:
    records = [
        _claim("CL-1", ["EV-1"]),
        _claim("CL-2", ["EV-1"]),
        _evidence("EV-1", ["CL-1", "CL-1"]),
    ]

    audit = build_reciprocity_audit(records)
    plan = build_repair_plan(audit, records)
    loaded, _ = deserialize_repair_plan(plan)

    assert audit["status"] == "blocked"
    assert audit["counts"]["duplicate_array_references"] == 1
    assert plan["status"] == "blocked"
    assert loaded["apply_allowed"] is False
    assert len(plan["queues"]["manual_adjudication"]) == 1


def test_clean_snapshot_produces_a_stable_zero_operation_plan() -> None:
    records = [_claim("CL-1", ["EV-1"]), _evidence("EV-1", ["CL-1"])]

    first_audit = build_reciprocity_audit(records)
    second_audit = build_reciprocity_audit(list(reversed(records)))
    assert first_audit == second_audit
    assert first_audit["status"] == "clean"
    assert first_audit["pair_detail_scope"] == "active_mismatches_only"
    assert first_audit["pairs"] == []
    assert first_audit["counts"]["reciprocal_pairs"] == 1

    first = build_repair_plan(first_audit, records)
    second = build_repair_plan(second_audit, list(reversed(records)))
    assert first == second
    loaded, change_set = deserialize_repair_plan(first)
    assert loaded["apply_allowed"] is True
    assert change_set.operations == ()
    assert loaded["change_set"]["summary"]["operations"] == 0


def test_resealed_nested_change_set_tamper_still_fails_exact_deserialization() -> None:
    records = [_claim("CL-1", ["EV-1"]), _evidence("EV-1", [])]
    plan = build_repair_plan(build_reciprocity_audit(records), records)
    tampered = deepcopy(plan)
    tampered["change_set"]["operations"][0]["payload"]["produced_claim_ids"] = []
    tampered = seal_artifact(tampered)

    with pytest.raises(
        ClaimEvidenceReciprocityRepairError, match="payload integrity failed"
    ):
        deserialize_repair_plan(tampered)


def test_resealed_audit_cannot_turn_candidate_into_direct_projection() -> None:
    records = [
        _claim("CL-1", ["EV-1"], status="candidate", human_review=False),
        _evidence("EV-1", []),
    ]
    audit = build_reciprocity_audit(records)
    tampered = deepcopy(audit)
    tampered["pairs"][0]["disposition"] = DISPOSITION_ADD_REVERSE
    tampered["pairs"][0]["blocks_apply"] = False
    tampered = seal_artifact(tampered)

    with pytest.raises(
        ClaimEvidenceReciprocityRepairError, match="deterministic pair classification"
    ):
        build_repair_plan(tampered, records)


def test_active_snapshot_drift_after_audit_is_rejected() -> None:
    records = [_claim("CL-1", ["EV-1"]), _evidence("EV-1", [])]
    audit = build_reciprocity_audit(records)
    changed = deepcopy(records)
    changed[1]["revision"] = 4
    changed[1]["payload"]["revision"] = 4
    changed[1]["content_sha256"] = record_content_sha(changed[1]["payload"])
    changed[1]["object_version"]["revision"] = 4
    changed[1]["object_version"]["content_sha256"] = changed[1]["content_sha256"]
    changed[1]["object_version"]["payload"] = changed[1]["payload"]
    changed[1]["producer_operation"]["after_revision"] = 4
    changed[1]["producer_operation"]["after_sha256"] = changed[1]["content_sha256"]

    with pytest.raises(ClaimEvidenceReciprocityRepairError, match="snapshot changed"):
        build_repair_plan(audit, changed)


def test_impact_preview_uses_changed_evidence_dependency_manifest() -> None:
    records = [_claim("CL-1", ["EV-1"]), _evidence("EV-1", [])]
    dependencies = {
        "DEP-1": {
            "dependency_id": "DEP-1",
            "consumer_kind": "article",
            "consumer_id": "ARTICLE-1",
            "status": "current",
            "dependency_manifest": [
                {"collection": "evidence_steps", "record_id": "EV-1"}
            ],
        },
        "DEP-OLD": {
            "dependency_id": "DEP-OLD",
            "consumer_kind": "article",
            "consumer_id": "ARTICLE-OLD",
            "status": "invalidated",
            "dependency_manifest": [
                {"collection": "evidence_steps", "record_id": "EV-1"}
            ],
        },
    }

    plan = build_repair_plan(
        build_reciprocity_audit(records),
        records,
        product_dependencies=dependencies,
        product_dependency_records=[
            {
                "object_id": dependency_id,
                "revision": index + 1,
                "content_sha256": record_content_sha(payload),
                "payload": payload,
            }
            for index, (dependency_id, payload) in enumerate(
                sorted(dependencies.items())
            )
        ],
    )
    assert plan["products_to_rebuild"] == [
        {
            "consumer_kind": "article",
            "consumer_id": "ARTICLE-1",
            "affected_dependency_ids": ["DEP-1"],
            "changed_records": [
                {"collection": "evidence_steps", "record_id": "EV-1"}
            ],
        }
    ]
    assert plan["apply_allowed"] is False
    assert len(plan["queues"]["dependency_coordination"]) == 1


def _active_db_row(record: dict[str, Any]) -> tuple[Any, ...]:
    version = record["object_version"]
    producer = record["producer_change_set"]
    operation = record["producer_operation"]
    timestamp = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    return (
        record["collection"],
        record["object_id"],
        record["revision"],
        record["content_sha256"],
        record["payload"],
        version["revision"],
        version["content_sha256"],
        version["payload"],
        version["change_set_id"],
        timestamp,
        producer["fingerprint_sha256"],
        producer.get("package_id", f"PACKAGE-{record['object_id']}"),
        producer["source_kind"],
        producer["source_sha256"],
        producer["status"],
        {},
        {},
        timestamp,
        timestamp,
        operation["operation_index"],
        operation["operation"],
        operation["collection"],
        operation["object_id"],
        operation["before_sha256"],
        operation["after_sha256"],
        operation["before_revision"],
        operation["after_revision"],
        operation["details"],
    )


class _FreezeCursor:
    def __init__(
        self,
        records: list[dict[str, Any]],
        *,
        prerequisite_status: str = "applied",
    ):
        self.records = records
        self.prerequisite_status = prerequisite_status
        self.last_sql = ""
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> None:
        self.last_sql = sql
        self.statements.append(sql)

    def fetchone(self):
        if "current_database()" in self.last_sql:
            return ("wkp364_fixture", "140017")
        if "transaction_timestamp" in self.last_sql:
            return (datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc),)
        if "count(*) FROM wang_knowledge.review_events" in self.last_sql:
            return (sum(len(record["review_events"]) for record in self.records),)
        return None

    def fetchall(self):
        timestamp = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
        if "FROM wang_knowledge.change_sets" in self.last_sql:
            return [
                (
                    "KCS-358-FINAL",
                    "9" * 64,
                    "PACKAGE-358",
                    "research_batch",
                    "8" * 64,
                    self.prerequisite_status,
                    {},
                    {},
                    timestamp,
                    timestamp if self.prerequisite_status == "applied" else None,
                )
            ]
        if "FROM wang_knowledge.objects o" in self.last_sql:
            return [_active_db_row(record) for record in self.records]
        if "FROM wang_knowledge.review_events r" in self.last_sql:
            return [
                (
                    event["review_event_id"],
                    event["collection"],
                    event["object_id"],
                    event["object_revision"],
                    event["reviewer_kind"],
                    event["reviewer_id"],
                    event["decision"],
                    event["reason"],
                    event["artifact"],
                    timestamp,
                )
                for record in self.records
                for event in record["review_events"]
            ]
        if "collection='product_dependencies'" in self.last_sql:
            dependency = {
                "dependency_id": "DEP-1",
                "status": "current",
                "dependency_manifest": [
                    {"collection": "evidence_steps", "record_id": "EV-1"}
                ],
            }
            return [("DEP-1", 2, record_content_sha(dependency), dependency)]
        if "collection='source_fragments'" in self.last_sql:
            fragment = {
                "fragment_id": "FRAG-1",
                "source_id": "SRC-1",
                "verbatim_excerpt": "source words",
            }
            return [
                ("FRAG-1", 1, record_content_sha(fragment), fragment, None)
            ]
        if "collection='source_documents'" in self.last_sql:
            source = {"source_id": "SRC-1", "source_type": "transcript"}
            return [("SRC-1", 1, record_content_sha(source), source, None)]
        return []


class _FreezeConnection:
    def __init__(self, cursor: _FreezeCursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def cursor(self):
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _prerequisites_manifest() -> dict[str, Any]:
    return seal_artifact(
        {
            "schema_version": PREREQUISITES_MANIFEST_SCHEMA_VERSION,
            "change_sets": [
                {
                    "change_set_id": "KCS-358-FINAL",
                    "fingerprint_sha256": "9" * 64,
                }
            ],
        }
    )


def test_freeze_uses_repeatable_read_apply_lock_and_exports_ledger_lineage() -> None:
    records = [_claim("CL-1", ["EV-1"]), _evidence("EV-1", [])]
    cursor = _FreezeCursor(records)
    connection = _FreezeConnection(cursor)
    store = SimpleNamespace(connect=lambda: connection)

    frozen = freeze_claim_evidence_reciprocity_input(
        store, prerequisites_manifest=_prerequisites_manifest()
    )

    assert "pg_advisory_lock(" in cursor.statements[0]
    assert cursor.statements[1] == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    )
    assert "pg_advisory_unlock(" in cursor.statements[-1]
    assert connection.commits == 3
    assert connection.rollbacks == 0
    assert frozen["prerequisites"][0]["status"] == "applied"
    assert frozen["active_records"][0]["producer_operation"]["after_sha256"]
    assert frozen["active_records"][0]["object_version"]["payload"]
    evidence = next(
        row
        for row in frozen["active_records"]
        if row["collection"] == "evidence_steps"
    )
    assert evidence["source_lineage"]["source_fragments"][0]["object_id"] == "FRAG-1"
    assert evidence["source_lineage"]["source_documents"][0]["object_id"] == "SRC-1"
    assert frozen["source_lineage_findings"] == []
    assert frozen["product_dependency_records"][0]["revision"] == 2
    assert frozen["review_event_ledger_count"] == 1
    assert frozen["authority_records"] == []
    audit = build_reciprocity_audit(
        frozen["active_records"], prerequisites=frozen["prerequisites"]
    )
    assert audit["pairs"][0]["disposition"] == DISPOSITION_ADD_REVERSE


def test_freeze_rejects_caller_supplied_authority_records() -> None:
    cursor = _FreezeCursor(
        [_claim("CL-1", ["EV-1"]), _evidence("EV-1", [])]
    )
    store = SimpleNamespace(connect=lambda: _FreezeConnection(cursor))

    with pytest.raises(TypeError, match="authority_records"):
        freeze_claim_evidence_reciprocity_input(
            store,
            prerequisites_manifest=_prerequisites_manifest(),
            authority_records=[{"untrusted": True}],
        )


def test_freeze_rejects_prerequisite_that_is_not_applied() -> None:
    cursor = _FreezeCursor(
        [_claim("CL-1", ["EV-1"]), _evidence("EV-1", [])],
        prerequisite_status="planned",
    )
    store = SimpleNamespace(connect=lambda: _FreezeConnection(cursor))

    with pytest.raises(ClaimEvidenceReciprocityRepairError, match="exact applied KCS"):
        freeze_claim_evidence_reciprocity_input(
            store, prerequisites_manifest=_prerequisites_manifest()
        )


def test_freeze_rejects_missing_current_producer_operation() -> None:
    records = [_claim("CL-1", ["EV-1"]), _evidence("EV-1", [])]
    records[0]["producer_operation"]["operation_index"] = None
    cursor = _FreezeCursor(records)
    store = SimpleNamespace(connect=lambda: _FreezeConnection(cursor))

    with pytest.raises(ClaimEvidenceReciprocityRepairError, match="lacks producer operation"):
        freeze_claim_evidence_reciprocity_input(
            store, prerequisites_manifest=_prerequisites_manifest()
        )


def test_missing_fragment_is_sealed_as_an_explicit_apply_blocker() -> None:
    class MissingFragmentCursor(_FreezeCursor):
        def fetchall(self):
            if "collection='source_fragments'" in self.last_sql:
                return []
            return super().fetchall()

    records = [_claim("CL-1", ["EV-1"]), _evidence("EV-1", [])]
    cursor = MissingFragmentCursor(records)
    store = SimpleNamespace(connect=lambda: _FreezeConnection(cursor))

    frozen = freeze_claim_evidence_reciprocity_input(
        store, prerequisites_manifest=_prerequisites_manifest()
    )
    assert frozen["source_lineage_findings"] == [
        {
            "code": "source_fragment_missing",
            "collection": "evidence_steps",
            "object_id": "EV-1",
            "referenced_id": "FRAG-1",
        }
    ]
    audit = build_reciprocity_audit(
        frozen["active_records"],
        prerequisites=frozen["prerequisites"],
        source_lineage_findings=frozen["source_lineage_findings"],
    )
    plan = build_repair_plan(
        audit,
        frozen["active_records"],
        product_dependencies=frozen["product_dependencies"],
        product_dependency_records=frozen["product_dependency_records"],
    )
    assert audit["status"] == "blocked"
    assert audit["blocking_source_lineage_findings"] == frozen[
        "source_lineage_findings"
    ]
    assert plan["apply_allowed"] is False


def test_unrelated_source_lineage_finding_does_not_block_a_clean_pair_graph() -> None:
    records = [_claim("CL-1", ["EV-1"]), _evidence("EV-1", ["CL-1"])]
    finding = {
        "code": "source_fragment_retired",
        "collection": "evidence_steps",
        "object_id": "EV-UNRELATED",
        "referenced_id": "FRAG-OLD",
    }

    audit = build_reciprocity_audit(records, source_lineage_findings=[finding])

    assert audit["status"] == "clean"
    assert audit["source_lineage_findings"] == [finding]
    assert audit["blocking_source_lineage_findings"] == []


def test_backup_verification_requires_a_pg_restore_toc(tmp_path: Path) -> None:
    backup = tmp_path / "repository.dump"
    backup.write_bytes(b"real-looking-pg-archive-bytes")
    calls: list[list[str]] = []

    def valid_run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        calls.append(command)
        toc_rows = ["1; 2615 12345 SCHEMA - wang_knowledge postgres"]
        for index, table_name in enumerate(
            CLAIM_EVIDENCE_BACKUP_REQUIRED_TABLES, start=2
        ):
            toc_rows.extend(
                [
                    f"{index}; 1259 {12000 + index} TABLE wang_knowledge "
                    f"{table_name} postgres",
                    f"{index + 100}; 0 {12000 + index} TABLE DATA "
                    f"wang_knowledge {table_name} postgres",
                ]
            )
        return SimpleNamespace(
            returncode=0,
            stdout="; Archive created at 2026-09-13\n" + "\n".join(toc_rows) + "\n",
            stderr="",
        )

    verified = verify_postgres_backup_dump(backup, run=valid_run)
    assert calls == [["pg_restore", "--list", str(backup.resolve())]]
    assert verified["pg_restore_entry_count"] == 11
    assert verified["size_bytes"] == backup.stat().st_size

    def invalid_run(_command: list[str], **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="not an archive")

    with pytest.raises(ClaimEvidenceReciprocityRepairError, match="rejected backup"):
        verify_postgres_backup_dump(backup, run=invalid_run)


def test_post_apply_result_requires_exact_ledger_and_fresh_zero_replan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prerequisites = [
        {
            "change_set_id": "KCS-358-FINAL",
            "fingerprint_sha256": "9" * 64,
            "status": "applied",
        }
    ]
    records = [_claim("CL-1", ["EV-1"]), _evidence("EV-1", [])]
    audit = build_reciprocity_audit(
        records, prerequisites=prerequisites, review_event_ledger_count=1
    )
    plan = build_repair_plan(audit, records)
    _, change_set = deserialize_repair_plan(plan)
    operation = change_set.operations[0]

    fresh_records = deepcopy(records)
    fresh_evidence = fresh_records[1]
    fresh_evidence["revision"] = operation.after_revision
    fresh_evidence["payload"] = {
        **operation.payload,
        "revision": operation.after_revision,
    }
    fresh_evidence["content_sha256"] = operation.after_sha256
    fresh_evidence["object_version"] = {
        "revision": operation.after_revision,
        "content_sha256": operation.after_sha256,
        "payload": fresh_evidence["payload"],
        "change_set_id": change_set.change_set_id,
    }
    fresh_evidence["producer_change_set"] = {
        "change_set_id": change_set.change_set_id,
        "fingerprint_sha256": change_set.fingerprint_sha256,
        "source_kind": change_set.source_kind,
        "source_sha256": change_set.source_sha256,
        "status": "applied",
    }
    fresh_evidence["producer_operation"] = {
        "change_set_id": change_set.change_set_id,
        "operation_index": 0,
        "operation": "update",
        "collection": "evidence_steps",
        "object_id": "EV-1",
        "before_sha256": operation.before_sha256,
        "after_sha256": operation.after_sha256,
        "before_revision": operation.before_revision,
        "after_revision": operation.after_revision,
        "details": {},
    }
    fresh_evidence["review_events"] = []
    fresh_audit = build_reciprocity_audit(
        fresh_records, prerequisites=prerequisites, review_event_ledger_count=1
    )
    fresh_input = seal_artifact(
        {
            "schema_version": "wang_claim_evidence_reciprocity_audit_input_v1",
            "frozen_at": "2026-09-13T12:01:00+00:00",
            "database_identity": {
                "database_name": "wkp364_fixture",
                "server_version_num": "140017",
            },
            "active_records": fresh_records,
            "active_snapshot": fresh_audit["store_snapshot"],
            "prerequisites": prerequisites,
            "authority_records": [],
            "source_lineage_findings": [],
            "source_lineage_identity_snapshot": fresh_audit[
                "source_lineage_identity_snapshot"
            ],
            "product_dependencies": {},
            "product_dependency_records": [],
            "review_event_ledger_count": 1,
            "review_event_ledger_snapshot": fresh_audit[
                "review_event_ledger_snapshot"
            ],
        }
    )
    ledger = {
        "active_snapshot": fresh_audit["store_snapshot"],
        "product_dependency_snapshot": build_product_dependency_active_snapshot(()),
        "review_event_ledger_count": 1,
        "review_event_ledger_snapshot": fresh_audit[
            "review_event_ledger_snapshot"
        ],
        "change_set": {
            "change_set_id": change_set.change_set_id,
            "fingerprint_sha256": change_set.fingerprint_sha256,
            "package_id": change_set.package_id,
            "source_kind": change_set.source_kind,
            "source_sha256": change_set.source_sha256,
            "status": "applied",
        },
        "operations": [
            {
                "operation_index": 0,
                "operation": operation.operation,
                "collection": operation.collection,
                "object_id": operation.object_id,
                "before_sha256": operation.before_sha256,
                "after_sha256": operation.after_sha256,
                "before_revision": operation.before_revision,
                "after_revision": operation.after_revision,
                "details": {},
                "object_version": {
                    "revision": operation.after_revision,
                    "content_sha256": operation.after_sha256,
                    "payload": fresh_evidence["payload"],
                    "change_set_id": change_set.change_set_id,
                },
            }
        ],
    }

    def freeze_after_apply(
        _store: object,
        *,
        prerequisites_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        assert prerequisites_manifest["schema_version"] == (
            PREREQUISITES_MANIFEST_SCHEMA_VERSION
        )
        return fresh_input

    monkeypatch.setattr(
        repair_module,
        "freeze_claim_evidence_reciprocity_input",
        freeze_after_apply,
    )
    monkeypatch.setattr(
        repair_module, "_read_post_apply_ledger", lambda *_args, **_kwargs: ledger
    )

    result = build_post_apply_result(
        store=object(),
        plan_artifact=plan,
        change_set=change_set,
        apply_result={"status": "applied"},
        backup={"artifact_sha256": "b" * 64},
    )

    assert result["fresh_plan_operations"] == 0
    assert result["post_apply_active_snapshot"]["counts"]["claim_only_pairs"] == 0
    assert result["applied_operations"][0]["object_version"]["change_set_id"] == (
        change_set.change_set_id
    )


def test_post_apply_result_rejects_review_event_increment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [_claim("CL-1", ["EV-1"]), _evidence("EV-1", ["CL-1"])]
    audit = build_reciprocity_audit(records, review_event_ledger_count=1)
    plan = build_repair_plan(audit, records)
    _, change_set = deserialize_repair_plan(plan)
    fresh = seal_artifact(
        {
            "schema_version": "wang_claim_evidence_reciprocity_audit_input_v1",
            "active_records": records,
            "active_snapshot": audit["store_snapshot"],
            "prerequisites": [],
            "authority_records": [],
            "source_lineage_findings": [],
            "product_dependencies": {},
            "product_dependency_records": [],
            "review_event_ledger_count": 2,
        }
    )
    monkeypatch.setattr(
        repair_module,
        "freeze_claim_evidence_reciprocity_input",
        lambda *_args, **_kwargs: fresh,
    )
    monkeypatch.setattr(
        repair_module,
        "_read_post_apply_ledger",
        lambda *_args, **_kwargs: {
            "active_snapshot": audit["store_snapshot"],
            "product_dependency_snapshot": build_product_dependency_active_snapshot(
                ()
            ),
            "review_event_ledger_count": 2,
            "change_set": None,
            "operations": [],
        },
    )

    with pytest.raises(ClaimEvidenceReciprocityRepairError, match="review-event ledger"):
        build_post_apply_result(
            store=object(),
            plan_artifact=plan,
            change_set=change_set,
            apply_result={"status": "applied"},
            backup={},
        )


def test_apply_requires_backup_and_passes_exact_guard_to_store(tmp_path: Path) -> None:
    records = [_claim("CL-1", ["EV-1"]), _evidence("EV-1", [])]
    freeze_binding = repair_module._build_freeze_binding(
        frozen_input_artifact_sha256="f" * 64,
        frozen_at="2026-09-13T12:00:00+00:00",
        database_identity={
            "database_name": "wkp364_fixture",
            "server_version_num": "140017",
        },
    )
    plan = build_repair_plan(
        build_reciprocity_audit(records, freeze_binding=freeze_binding),
        records,
    )

    class Store:
        received: tuple[Any, ...] | None = None

        def apply_plan(self, change_set: Any, **kwargs: Any) -> dict[str, Any]:
            self.received = (change_set, kwargs)
            return {"status": "applied"}

    store = Store()
    missing = tmp_path / "missing.dump"
    with pytest.raises(ClaimEvidenceReciprocityRepairError, match="backup dump"):
        apply_sealed_plan(plan, store=store, backup_dump=missing)

    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"backup")
    required_table_coverage = [
        {
            "table_name": table_name,
            "has_table_definition": True,
            "has_table_data": True,
        }
        for table_name in CLAIM_EVIDENCE_BACKUP_REQUIRED_TABLES
    ]
    verified_backup = seal_artifact(
        {
            "schema_version": (
                "wang_claim_evidence_reciprocity_backup_verification_v1"
            ),
            "path": str(backup.resolve()),
            "sha256": "1" * 64,
            "size_bytes": backup.stat().st_size,
            "pg_restore_list_sha256": "2" * 64,
            "pg_restore_entry_count": 1,
            "archive_created_at": "2026-09-13T12:00:01+00:00",
            "archive_database_name": "wkp364_fixture",
            "contains_wang_knowledge_schema": True,
            "contains_required_table_data": True,
            "required_table_coverage": required_table_coverage,
            "required_table_coverage_sha256": sha256_json(
                required_table_coverage
            ),
            "timestamp_precision": "second",
            "frozen_input_artifact_sha256": freeze_binding[
                "frozen_input_artifact_sha256"
            ],
            "frozen_at": freeze_binding["frozen_at"],
            "database_identity": freeze_binding["database_identity"],
        }
    )
    assert apply_sealed_plan(
        plan,
        store=store,
        backup_dump=backup,
        backup_verifier=lambda _path, **_kwargs: verified_backup,
        result_builder=lambda **_kwargs: {"status": "verified"},
    ) == {
        "status": "verified"
    }
    assert store.received is not None
    _, kwargs = store.received
    assert kwargs["expected_claim_evidence_guard"] == plan["store_guard"]
    assert (
        kwargs["metadata"]["claim_evidence_reciprocity_repair"]["store_guard"]
        == plan["store_guard"]
    )
