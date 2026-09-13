from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import pytest

from backend.api.canonical_repository.postgres_store import (
    CLAIM_EVIDENCE_BACKUP_REQUIRED_TABLES,
    CLAIM_EVIDENCE_RECIPROCITY_REPAIR_METADATA_KEY,
    CLAIM_EVIDENCE_RECIPROCITY_REPAIR_SOURCE_KIND,
    ChangeOperation,
    ChangeSetConflict,
    ChangeSetPlan,
    PostgresKnowledgeStore,
    PostgresKnowledgeStoreError,
    build_claim_evidence_active_snapshot,
    build_claim_evidence_reciprocity_guard,
    build_product_dependency_active_snapshot,
    build_review_event_ledger_snapshot,
    build_source_lineage_identity_snapshot,
    record_content_sha,
    sha256_json,
)


def _claim(evidence_ids: Sequence[str], *, claim_id: str = "CL-1", revision: int = 1):
    return {
        "claim_id": claim_id,
        "statement": f"claim {claim_id}",
        "claim_type": "explicit_claim",
        "evidence_step_ids": list(evidence_ids),
        "review_status": "approved",
        "revision": revision,
    }


def _evidence(claim_ids: Sequence[str], *, evidence_id: str = "E-1", revision: int = 1):
    return {
        "evidence_step_id": evidence_id,
        "statement": f"evidence {evidence_id}",
        "produced_claim_ids": list(claim_ids),
        "revision": revision,
    }


def _row(collection: str, object_id: str, revision: int, payload: Mapping[str, Any]):
    return (collection, object_id, revision, record_content_sha(payload), dict(payload))


def _rows(
    claim: Mapping[str, Any], evidence: Mapping[str, Any]
) -> list[tuple[str, str, int, str, dict[str, Any]]]:
    return [
        _row("claims", str(claim["claim_id"]), int(claim["revision"]), claim),
        _row(
            "evidence_steps",
            str(evidence["evidence_step_id"]),
            int(evidence["revision"]),
            evidence,
        ),
    ]


def _plan(
    operation: ChangeOperation,
    *, source_kind: str = CLAIM_EVIDENCE_RECIPROCITY_REPAIR_SOURCE_KIND,
) -> ChangeSetPlan:
    fingerprint = sha256_json(
        {
            "test": "claim-evidence-reciprocity",
            "collection": operation.collection,
            "object_id": operation.object_id,
            "before_revision": operation.before_revision,
            "after_revision": operation.after_revision,
            "after_sha256": operation.after_sha256,
        }
    )
    return ChangeSetPlan(
        change_set_id=f"KCS-{fingerprint[:20]}",
        fingerprint_sha256=fingerprint,
        package_id="WKP364-TEST",
        source_kind=source_kind,
        source_sha256=fingerprint,
        operations=(operation,),
        unchanged=0,
        ignored_keys=(),
    )


def _zero_plan() -> ChangeSetPlan:
    fingerprint = sha256_json({"test": "claim-evidence-reciprocity-zero"})
    return ChangeSetPlan(
        change_set_id=f"KCS-{fingerprint[:20]}",
        fingerprint_sha256=fingerprint,
        package_id="WKP364-TEST-ZERO",
        source_kind=CLAIM_EVIDENCE_RECIPROCITY_REPAIR_SOURCE_KIND,
        source_sha256=fingerprint,
        operations=(),
        unchanged=2,
        ignored_keys=(),
    )


def _evidence_update(before: Mapping[str, Any], after: Mapping[str, Any]) -> ChangeOperation:
    return ChangeOperation(
        operation="update",
        collection="evidence_steps",
        object_id=str(before["evidence_step_id"]),
        before_sha256=record_content_sha(before),
        after_sha256=record_content_sha(after),
        before_revision=int(before["revision"]),
        after_revision=int(before["revision"]) + 1,
        payload=dict(after),
    )


class _SnapshotCursor:
    def __init__(
        self,
        rows: Sequence[tuple[str, str, int, str, dict[str, Any]]],
        *,
        review_events: Sequence[tuple[Any, ...]] | None = None,
        producers: Sequence[tuple[Any, ...]] | None = None,
        product_dependencies: Sequence[tuple[Any, ...]] = (),
        written_rows: Sequence[tuple[str, str, int, str, dict[str, Any]]] | None = None,
        source_fragments: Sequence[tuple[Any, ...]] = (),
        source_documents: Sequence[tuple[Any, ...]] = (),
    ):
        self.rows = list(rows)
        claims = [row for row in self.rows if row[0] == "claims"]
        self.review_events = list(
            review_events
            if review_events is not None
            else [
                (
                    f"REV-{object_id}-{revision}",
                    "claims",
                    object_id,
                    revision,
                    "human",
                    "approved",
                    {"change_set_id": f"KCS-REVIEW-{object_id}"},
                )
                for _collection, object_id, revision, _sha, _payload in claims
            ]
        )
        self.producers = list(
            producers
            if producers is not None
            else [
                (
                    object_id,
                    revision,
                    content_sha,
                    f"KCS-REVIEW-{object_id}",
                    "update",
                    revision,
                    content_sha,
                    "applied",
                    "review_decision",
                )
                for _collection, object_id, revision, content_sha, _payload in claims
            ]
        )
        self.product_dependencies = list(product_dependencies)
        self.written_rows = list(written_rows) if written_rows is not None else None
        self.source_fragments = list(source_fragments)
        self.source_documents = list(source_documents)
        self.snapshot_reads = 0
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.last_sql = ""

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.last_sql = sql
        self.statements.append((sql, params))

    def fetchall(self):
        if "FROM wang_knowledge.review_events" in self.last_sql:
            if "reviewer_id" in self.last_sql:
                return [
                    (
                        event_id,
                        collection,
                        object_id,
                        revision,
                        reviewer_kind,
                        "editor",
                        decision,
                        "review reason",
                        artifact,
                        datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc),
                    )
                    for (
                        event_id,
                        collection,
                        object_id,
                        revision,
                        reviewer_kind,
                        decision,
                        artifact,
                    ) in self.review_events
                ]
            return list(self.review_events)
        if "collection='source_fragments'" in self.last_sql:
            return list(self.source_fragments)
        if "collection='source_documents'" in self.last_sql:
            return list(self.source_documents)
        if "FROM wang_knowledge.object_versions ov" in self.last_sql:
            return list(self.producers)
        if "collection='product_dependencies'" in self.last_sql:
            return list(self.product_dependencies)
        if (
            "FROM wang_knowledge.objects" in self.last_sql
            and "collection = ANY" in self.last_sql
        ):
            self.snapshot_reads += 1
            if self.snapshot_reads > 1 and self.written_rows is not None:
                return list(self.written_rows)
        return list(self.rows)


def _guard(
    rows: Sequence[tuple[str, str, int, str, dict[str, Any]]],
    plan: ChangeSetPlan,
    *,
    product_dependencies: Sequence[tuple[Any, ...]] = (),
    review_events: Sequence[tuple[Any, ...]] | None = None,
    source_lineage: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    compact_events = list(
        review_events
        if review_events is not None
        else _SnapshotCursor(rows).review_events
    )
    expanded_events = [
        (
            event_id,
            collection,
            object_id,
            revision,
            reviewer_kind,
            "editor",
            decision,
            "review reason",
            artifact,
            datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc),
        )
        for (
            event_id,
            collection,
            object_id,
            revision,
            reviewer_kind,
            decision,
            artifact,
        ) in compact_events
    ]
    freeze_binding = {
        "schema_version": "wang_claim_evidence_reciprocity_freeze_binding_v1",
        "frozen_input_artifact_sha256": "1" * 64,
        "frozen_at": "2026-09-13T12:00:00+00:00",
        "database_identity": {
            "database_name": "wkp364_test",
            "server_version_num": "140017",
        },
    }
    freeze_binding["binding_sha256"] = sha256_json(freeze_binding)
    return build_claim_evidence_reciprocity_guard(
        plan,
        build_claim_evidence_active_snapshot(rows),
        build_product_dependency_active_snapshot(product_dependencies),
        build_review_event_ledger_snapshot(expanded_events),
        freeze_binding,
        build_source_lineage_identity_snapshot(source_lineage),
    )


def _apply_metadata(
    plan: ChangeSetPlan,
    guard: Mapping[str, Any],
    *,
    backup_sha256: str = "b" * 64,
) -> dict[str, Any]:
    required_table_coverage = [
        {
            "table_name": table_name,
            "has_table_definition": True,
            "has_table_data": True,
        }
        for table_name in CLAIM_EVIDENCE_BACKUP_REQUIRED_TABLES
    ]
    backup = {
        "schema_version": "wang_claim_evidence_reciprocity_backup_verification_v1",
        "path": "/tmp/wkp364-test.dump",
        "sha256": backup_sha256,
        "size_bytes": 123,
        "pg_restore_list_sha256": "c" * 64,
        "pg_restore_entry_count": 2,
        "archive_created_at": "2026-09-13T12:00:01+00:00",
        "archive_database_name": "wkp364_test",
        "contains_wang_knowledge_schema": True,
        "contains_required_table_data": True,
        "required_table_coverage": required_table_coverage,
        "required_table_coverage_sha256": sha256_json(
            required_table_coverage
        ),
        "timestamp_precision": "second",
        "frozen_input_artifact_sha256": "1" * 64,
        "frozen_at": "2026-09-13T12:00:00+00:00",
        "database_identity": {
            "database_name": "wkp364_test",
            "server_version_num": "140017",
        },
    }
    backup["artifact_sha256"] = sha256_json(backup)
    return {
        CLAIM_EVIDENCE_RECIPROCITY_REPAIR_METADATA_KEY: {
            "audit_artifact_sha256": "d" * 64,
            "plan_artifact_sha256": "e" * 64,
            "action_manifest_sha256": "f" * 64,
            "operation_manifest_sha256": sha256_json(
                [
                    {
                        "operation": operation.operation,
                        "collection": operation.collection,
                        "object_id": operation.object_id,
                        "before_sha256": operation.before_sha256,
                        "after_sha256": operation.after_sha256,
                        "before_revision": operation.before_revision,
                        "after_revision": operation.after_revision,
                    }
                    for operation in plan.operations
                ]
            ),
            "store_guard": guard,
            "backup": backup,
        }
    }


def test_guard_rejects_snapshot_drift_from_an_untouched_new_row() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    expected_rows = _rows(claim, evidence)
    after = _evidence(["CL-1"])
    plan = _plan(_evidence_update(evidence, after))
    current_rows = [
        *expected_rows,
        _row("claims", "CL-2", 1, _claim([], claim_id="CL-2")),
    ]

    with pytest.raises(ChangeSetConflict, match="snapshot ID drift.*CL-2"):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            _SnapshotCursor(current_rows), plan, _guard(expected_rows, plan)
        )


def test_guard_rejects_same_sha_revision_drift() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    expected_rows = _rows(claim, evidence)
    after = _evidence(["CL-1"])
    plan = _plan(_evidence_update(evidence, after))
    revised_evidence = _evidence([], revision=2)
    current_rows = _rows(claim, revised_evidence)
    assert current_rows[1][3] == expected_rows[1][3]

    with pytest.raises(ChangeSetConflict, match="snapshot revision drift.*E-1"):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            _SnapshotCursor(current_rows), plan, _guard(expected_rows, plan)
        )


def test_snapshot_uses_row_revision_for_a_revived_legacy_payload() -> None:
    claim = _claim(["E-1"])
    evidence_payload = _evidence(["CL-1"], revision=1)
    rows = [
        _row("claims", "CL-1", 1, claim),
        (
            "evidence_steps",
            "E-1",
            3,
            record_content_sha(evidence_payload),
            evidence_payload,
        ),
    ]

    snapshot = build_claim_evidence_active_snapshot(rows)

    evidence_record = next(
        row for row in snapshot["records"] if row["collection"] == "evidence_steps"
    )
    assert evidence_record["revision"] == 3


@pytest.mark.parametrize(
    ("after_claim_ids", "message"),
    [
        ([], "not reciprocal"),
        (["CL-1", "CL-1"], "duplicate array references"),
        (["CL-1", "CL-MISSING"], "dangling endpoints"),
    ],
)
def test_guard_rejects_invalid_simulated_final_graph(
    after_claim_ids: list[str], message: str
) -> None:
    claim = _claim(["E-1"])
    evidence = _evidence(["CL-1"])
    rows = _rows(claim, evidence)
    after = _evidence(after_claim_ids)
    plan = _plan(
        _evidence_update(evidence, after),
        source_kind="test_opt_in_claim_evidence_guard",
    )

    with pytest.raises(ChangeSetConflict, match=message):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            _SnapshotCursor(rows), plan, _guard(rows, plan)
        )


def test_guard_accepts_a_plan_that_repairs_the_complete_locked_graph() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    after = _evidence(["CL-1"])
    plan = _plan(_evidence_update(evidence, after))

    final_snapshot = PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
        _SnapshotCursor(rows), plan, _guard(rows, plan)
    )

    assert final_snapshot["counts"]["reciprocal_pairs"] == 1
    assert final_snapshot["counts"]["claim_only_pairs"] == 0
    assert final_snapshot["counts"]["evidence_only_pairs"] == 0
    assert final_snapshot["counts"]["dangling_endpoints"] == 0


def test_guard_rejects_full_product_dependency_snapshot_drift() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    plan = _plan(_evidence_update(evidence, _evidence(["CL-1"])))
    dependency = {
        "dependency_id": "DEP-1",
        "status": "current",
        "dependency_manifest": [],
    }
    expected_dependencies = [
        ("DEP-1", 1, record_content_sha(dependency), dependency)
    ]
    changed_dependency = {**dependency, "status": "invalidated"}
    current_dependencies = [
        (
            "DEP-1",
            2,
            record_content_sha(changed_dependency),
            changed_dependency,
        )
    ]

    with pytest.raises(ChangeSetConflict, match="ProductDependency snapshot drift"):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            _SnapshotCursor(rows, product_dependencies=current_dependencies),
            plan,
            _guard(rows, plan, product_dependencies=expected_dependencies),
        )


def test_dedicated_guard_rejects_non_evidence_and_non_update_operations() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    claim_operation = ChangeOperation(
        operation="update",
        collection="claims",
        object_id="CL-1",
        before_sha256=record_content_sha(claim),
        after_sha256=record_content_sha(claim),
        before_revision=1,
        after_revision=2,
        payload=dict(claim),
    )
    plan = _plan(claim_operation)

    with pytest.raises(ChangeSetConflict, match="only active EvidenceStep updates"):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            _SnapshotCursor(rows), plan, _guard(rows, plan)
        )

    update = _evidence_update(evidence, _evidence(["CL-1"]))
    create = replace(update, operation="create", before_revision=None, before_sha256=None)
    create_plan = _plan(create)
    with pytest.raises(ChangeSetConflict, match="only active EvidenceStep updates"):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            _SnapshotCursor(rows), create_plan, _guard(rows, create_plan)
        )


def test_dedicated_guard_rejects_non_adjacent_revision_and_content_mutation() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    update = _evidence_update(evidence, _evidence(["CL-1"]))
    skipped = replace(update, after_revision=3)
    skipped_plan = _plan(skipped)
    with pytest.raises(ChangeSetConflict, match="locked adjacent revision"):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            _SnapshotCursor(rows), skipped_plan, _guard(rows, skipped_plan)
        )

    changed_payload = _evidence(["CL-1"])
    changed_payload["statement"] = "silently rewritten evidence"
    changed = _evidence_update(evidence, changed_payload)
    changed_plan = _plan(changed)
    with pytest.raises(ChangeSetConflict, match="outside produced_claim_ids"):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            _SnapshotCursor(rows), changed_plan, _guard(rows, changed_plan)
        )


def test_dedicated_guard_rejects_removal_and_invented_claim_binding() -> None:
    claim = _claim(["E-1"])
    reciprocal_evidence = _evidence(["CL-1"])
    reciprocal_rows = _rows(claim, reciprocal_evidence)
    removal = _plan(_evidence_update(reciprocal_evidence, _evidence([])))
    with pytest.raises(ChangeSetConflict, match="may not remove or reorder"):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            _SnapshotCursor(reciprocal_rows),
            removal,
            _guard(reciprocal_rows, removal),
        )

    unbound_claim = _claim([])
    evidence = _evidence([])
    rows = _rows(unbound_claim, evidence)
    invented = _plan(_evidence_update(evidence, _evidence(["CL-1"])))
    with pytest.raises(ChangeSetConflict, match="cannot invent a binding"):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            _SnapshotCursor(rows), invented, _guard(rows, invented)
        )


def test_dedicated_guard_rejects_current_human_evidence() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    evidence["review_status"] = "approved"
    rows = _rows(claim, evidence)
    plan = _plan(_evidence_update(evidence, {**evidence, "produced_claim_ids": ["CL-1"]}))
    claim_event = _SnapshotCursor(rows).review_events[0]
    evidence_event = (
        "REV-E-1-1",
        "evidence_steps",
        "E-1",
        1,
        "human",
        "approved",
        {"change_set_id": "KCS-REVIEW-E-1"},
    )

    with pytest.raises(ChangeSetConflict, match="human-settled EvidenceStep"):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            _SnapshotCursor(rows, review_events=[claim_event, evidence_event]),
            plan,
            _guard(rows, plan),
        )


@pytest.mark.parametrize(
    ("producer_patch", "message"),
    [
        ({"change_set_status": "planned"}, "not bound"),
        ({"source_kind": "knowledge_package"}, "not bound"),
        ({"operation": "retire"}, "not bound"),
        ({"after_revision": 2}, "not bound"),
    ],
)
def test_dedicated_guard_rejects_unproved_claim_producer(
    producer_patch: dict[str, Any], message: str
) -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    plan = _plan(_evidence_update(evidence, _evidence(["CL-1"])))
    producer = list(_SnapshotCursor(rows).producers[0])
    positions = {
        "operation": 4,
        "after_revision": 5,
        "change_set_status": 7,
        "source_kind": 8,
    }
    for field, value in producer_patch.items():
        producer[positions[field]] = value

    with pytest.raises(ChangeSetConflict, match=message):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            _SnapshotCursor(rows, producers=[tuple(producer)]),
            plan,
            _guard(rows, plan),
        )


def test_dedicated_guard_rejects_human_event_for_another_producer() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    plan = _plan(_evidence_update(evidence, _evidence(["CL-1"])))
    event = list(_SnapshotCursor(rows).review_events[0])
    event[6] = {"change_set_id": "KCS-FORGED"}

    with pytest.raises(ChangeSetConflict, match="not bound"):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            _SnapshotCursor(rows, review_events=[tuple(event)]),
            plan,
            _guard(rows, plan),
        )


def test_dedicated_source_kind_cannot_omit_the_store_guard() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    after = _evidence(["CL-1"])
    plan = _plan(_evidence_update(evidence, after))
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: pytest.fail("missing guard must fail before DB access")

    with pytest.raises(PostgresKnowledgeStoreError, match="requires a sealed store guard"):
        store.apply_plan(plan)


def test_dedicated_guard_cannot_omit_product_dependency_denominator() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    plan = _plan(_evidence_update(evidence, _evidence(["CL-1"])))

    with pytest.raises(
        PostgresKnowledgeStoreError,
        match="requires a full ProductDependency snapshot",
    ):
        build_claim_evidence_reciprocity_guard(
            plan, build_claim_evidence_active_snapshot(rows)
        )


def test_dedicated_nonzero_apply_requires_verified_operational_metadata() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    plan = _plan(_evidence_update(evidence, _evidence(["CL-1"])))
    guard = _guard(rows, plan)
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: pytest.fail("metadata must fail before DB access")

    with pytest.raises(PostgresKnowledgeStoreError, match="metadata lacks"):
        store.apply_plan(
            plan,
            metadata={
                CLAIM_EVIDENCE_RECIPROCITY_REPAIR_METADATA_KEY: {
                    "store_guard": guard
                }
            },
            expected_claim_evidence_guard=guard,
        )


def test_dedicated_apply_rejects_resealed_partial_backup_coverage() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    plan = _plan(_evidence_update(evidence, _evidence(["CL-1"])))
    guard = _guard(rows, plan)
    metadata = _apply_metadata(plan, guard)
    backup = metadata[CLAIM_EVIDENCE_RECIPROCITY_REPAIR_METADATA_KEY]["backup"]
    backup["required_table_coverage"][0]["has_table_data"] = False
    backup["required_table_coverage_sha256"] = sha256_json(
        backup["required_table_coverage"]
    )
    sealed = dict(backup)
    sealed.pop("artifact_sha256")
    backup["artifact_sha256"] = sha256_json(sealed)
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: pytest.fail("backup must fail before DB access")

    with pytest.raises(PostgresKnowledgeStoreError, match="backup verification"):
        store.apply_plan(
            plan,
            metadata=metadata,
            expected_claim_evidence_guard=guard,
        )


def test_store_guard_is_bound_to_the_exact_plan_fingerprint() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    first = _plan(_evidence_update(evidence, _evidence(["CL-1"])))
    second = _plan(_evidence_update(evidence, _evidence(["CL-1", "CL-2"])))

    with pytest.raises(PostgresKnowledgeStoreError, match="another ChangeSet plan"):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            _SnapshotCursor(rows), second, _guard(rows, first)
        )


class _ApplyCursor(_SnapshotCursor):
    def __init__(
        self,
        rows: Sequence[tuple[str, str, int, str, dict[str, Any]]],
        locked: tuple[int, str, None],
        *,
        written_rows: Sequence[tuple[str, str, int, str, dict[str, Any]]],
        plan: ChangeSetPlan | None = None,
        metadata: Mapping[str, Any] | None = None,
        omit_object_versions: bool = False,
        change_set_status: str = "applied",
    ):
        super().__init__(rows, written_rows=written_rows)
        self.locked = locked
        self.plan = plan
        self.metadata = dict(metadata or {})
        self.omit_object_versions = omit_object_versions
        self.change_set_status = change_set_status

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def fetchone(self):
        if "WHERE fingerprint_sha256" in self.last_sql:
            return None
        if "SELECT count(*) FROM wang_knowledge.review_events" in self.last_sql:
            return (0,)
        if (
            "SELECT fingerprint_sha256, package_id, source_kind" in self.last_sql
            and self.plan is not None
        ):
            summary = self.plan.as_dict()["summary"]
            summary["invalidated_dependencies"] = 0
            return (
                self.plan.fingerprint_sha256,
                self.plan.package_id,
                self.plan.source_kind,
                self.plan.source_sha256,
                self.change_set_status,
                summary,
                self.metadata,
                datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 9, 13, 12, 0, 1, tzinfo=timezone.utc),
            )
        if "WHERE collection=%s AND object_id=%s FOR UPDATE" in self.last_sql:
            return self.locked
        return None

    def fetchall(self):
        if (
            "FROM wang_knowledge.change_operations" in self.last_sql
            and self.plan is not None
        ):
            return [
                (
                    index,
                    operation.operation,
                    operation.collection,
                    operation.object_id,
                    operation.before_sha256,
                    operation.after_sha256,
                    operation.before_revision,
                    operation.after_revision,
                    {},
                )
                for index, operation in enumerate(self.plan.operations)
            ]
        if (
            "FROM wang_knowledge.object_versions" in self.last_sql
            and "WHERE change_set_id" in self.last_sql
            and self.plan is not None
        ):
            if self.omit_object_versions:
                return []
            return sorted(
                (
                    operation.collection,
                    operation.object_id,
                    operation.after_revision,
                    operation.after_sha256,
                    {**operation.payload, "revision": operation.after_revision},
                    self.plan.change_set_id,
                )
                for operation in self.plan.operations
            )
        return super().fetchall()


class _RetryCursor(_SnapshotCursor):
    def __init__(
        self,
        rows: Sequence[tuple[str, str, int, str, dict[str, Any]]],
        *,
        plan: ChangeSetPlan,
        persisted_metadata: Mapping[str, Any],
        before_payload: Mapping[str, Any],
        review_events: Sequence[tuple[Any, ...]] | None = None,
    ):
        super().__init__(rows, review_events=review_events)
        self.plan = plan
        self.persisted_metadata = dict(persisted_metadata)
        self.before_payload = dict(before_payload)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def fetchone(self):
        if "WHERE fingerprint_sha256" in self.last_sql:
            summary = self.plan.as_dict()["summary"]
            summary["invalidated_dependencies"] = 0
            return (
                self.plan.change_set_id,
                "applied",
                summary,
                self.persisted_metadata,
            )
        if "SELECT fingerprint_sha256, package_id, source_kind" in self.last_sql:
            summary = self.plan.as_dict()["summary"]
            summary["invalidated_dependencies"] = 0
            return (
                self.plan.fingerprint_sha256,
                self.plan.package_id,
                self.plan.source_kind,
                self.plan.source_sha256,
                "applied",
                summary,
                self.persisted_metadata,
                datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 9, 13, 12, 0, 1, tzinfo=timezone.utc),
            )
        if "FROM wang_knowledge.object_versions" in self.last_sql:
            return (self.before_payload,)
        return None

    def fetchall(self):
        if "FROM wang_knowledge.change_operations" in self.last_sql:
            return [
                (
                    index,
                    operation.operation,
                    operation.collection,
                    operation.object_id,
                    operation.before_sha256,
                    operation.after_sha256,
                    operation.before_revision,
                    operation.after_revision,
                    {},
                )
                for index, operation in enumerate(self.plan.operations)
            ]
        if (
            "FROM wang_knowledge.object_versions" in self.last_sql
            and "WHERE change_set_id" in self.last_sql
        ):
            return sorted(
                (
                    operation.collection,
                    operation.object_id,
                    operation.after_revision,
                    operation.after_sha256,
                    {**operation.payload, "revision": operation.after_revision},
                    self.plan.change_set_id,
                )
                for operation in self.plan.operations
            )
        return super().fetchall()


class _Connection:
    def __init__(self, cursor: _ApplyCursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def cursor(self):
        return self._cursor


def test_source_resume_guard_reads_current_graph_and_preserves_existing_bindings() -> None:
    claim = _claim(["E-1"])
    claim["review_status"] = "ai_consensus_reviewed"
    evidence = _evidence(["CL-1"])
    rows = _rows(claim, evidence)
    after = {**evidence, "statement": "freshly reviewed evidence"}
    plan = _plan(
        _evidence_update(evidence, after),
        source_kind="knowledge_package",
    )
    cursor = _ApplyCursor(rows, (1, "unused", None), written_rows=rows)
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: _Connection(cursor)

    guard = store.read_claim_evidence_reciprocity_guard(plan)

    assert guard["expected_active_snapshot"]["counts"] == {
        "active_claims": 1,
        "active_evidence_steps": 1,
        "claim_evidence_pairs": 1,
        "evidence_claim_pairs": 1,
        "reciprocal_pairs": 1,
        "claim_only_pairs": 0,
        "evidence_only_pairs": 0,
        "duplicate_array_references": 0,
        "dangling_claim_evidence_refs": 0,
        "dangling_evidence_claim_refs": 0,
        "dangling_endpoints": 0,
    }
    assert "pg_advisory_xact_lock" in cursor.statements[0][0]


def test_source_resume_guard_rejects_an_old_package_binding_projection() -> None:
    claim = _claim(["E-1"])
    claim["review_status"] = "ai_consensus_reviewed"
    evidence = _evidence(["CL-1"])
    rows = _rows(claim, evidence)
    plan = _plan(
        _evidence_update(evidence, _evidence([])),
        source_kind="knowledge_package",
    )
    cursor = _ApplyCursor(rows, (1, "unused", None), written_rows=rows)
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: _Connection(cursor)

    with pytest.raises(
        PostgresKnowledgeStoreError,
        match="source resume would replace current produced_claim_ids",
    ):
        store.read_claim_evidence_reciprocity_guard(plan)


def test_apply_runs_metadata_guard_after_advisory_lock_and_before_writes() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    after = _evidence(["CL-1"])
    plan = _plan(_evidence_update(evidence, after))
    guard = _guard(rows, plan)
    metadata = _apply_metadata(plan, guard)
    written_evidence = {**after, "revision": 2}
    written_rows = _rows(claim, written_evidence)
    cursor = _ApplyCursor(
        rows,
        (1, record_content_sha(evidence), None),
        written_rows=written_rows,
        plan=plan,
        metadata=metadata,
    )
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: _Connection(cursor)
    for name in (
        "_assert_obsolete_candidate_retirement",
        "_assert_stale_pending_topic_identity_retirement",
        "_assert_stale_candidate_projection_retirement",
        "_assert_stale_ai_cross_sermon_constraint_retirement",
        "_assert_global_id_uniqueness",
        "_assert_source_identity_uniqueness",
        "_assert_edge_integrity",
        "_assert_no_dangling_package_references",
        "_assert_no_uncoordinated_semantic_references",
        "_assert_current_viewpoint_revisions",
    ):
        setattr(store, name, lambda *_args, **_kwargs: None)
    store._invalidate_dependencies = lambda *_args, **_kwargs: 0
    result = store.apply_plan(
        plan,
        metadata=metadata,
        expected_claim_evidence_guard=guard,
    )

    sql = [statement for statement, _params in cursor.statements]
    lock_index = next(i for i, statement in enumerate(sql) if "pg_advisory_xact_lock" in statement)
    snapshot_index = next(
        i
        for i, statement in enumerate(sql)
        if "collection = ANY" in statement and "ORDER BY collection, object_id" in statement
    )
    write_index = next(
        i
        for i, statement in enumerate(sql)
        if "INSERT INTO wang_knowledge.change_sets" in statement
    )
    assert lock_index < snapshot_index < write_index
    assert result["status"] == "applied"


def test_dedicated_zero_operation_plan_still_locks_and_checks_clean_graph() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence(["CL-1"])
    rows = _rows(claim, evidence)
    plan = _zero_plan()
    guard = _guard(rows, plan)
    cursor = _ApplyCursor(rows, (1, "unused", None), written_rows=rows)
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: _Connection(cursor)

    result = store.apply_plan(plan, expected_claim_evidence_guard=guard)

    sql = [statement for statement, _params in cursor.statements]
    assert any("pg_advisory_xact_lock" in statement for statement in sql)
    assert any("collection = ANY" in statement for statement in sql)
    assert not any("INSERT INTO wang_knowledge.change_sets" in statement for statement in sql)
    assert result["status"] == "unchanged"


def test_dedicated_zero_operation_plan_rejects_a_dirty_final_graph() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    plan = _zero_plan()
    guard = _guard(rows, plan)
    cursor = _ApplyCursor(rows, (1, "unused", None), written_rows=rows)
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: _Connection(cursor)

    with pytest.raises(ChangeSetConflict, match="not reciprocal"):
        store.apply_plan(plan, expected_claim_evidence_guard=guard)


def test_apply_rolls_back_if_written_graph_differs_from_locked_simulation() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    after = _evidence(["CL-1"])
    plan = _plan(_evidence_update(evidence, after))
    guard = _guard(rows, plan)
    metadata = _apply_metadata(plan, guard)
    cursor = _ApplyCursor(
        rows,
        (1, record_content_sha(evidence), None),
        written_rows=rows,
        plan=plan,
        metadata=metadata,
    )
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: _Connection(cursor)
    for name in (
        "_assert_obsolete_candidate_retirement",
        "_assert_stale_pending_topic_identity_retirement",
        "_assert_stale_candidate_projection_retirement",
        "_assert_stale_ai_cross_sermon_constraint_retirement",
        "_assert_global_id_uniqueness",
        "_assert_source_identity_uniqueness",
        "_assert_edge_integrity",
        "_assert_no_dangling_package_references",
        "_assert_no_uncoordinated_semantic_references",
        "_assert_current_viewpoint_revisions",
    ):
        setattr(store, name, lambda *_args, **_kwargs: None)
    store._invalidate_dependencies = lambda *_args, **_kwargs: 0

    with pytest.raises(ChangeSetConflict, match="differs from its locked final"):
        store.apply_plan(
            plan,
            metadata=metadata,
            expected_claim_evidence_guard=guard,
        )


def test_dedicated_apply_rejects_any_dynamic_dependency_invalidation() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    after = _evidence(["CL-1"])
    plan = _plan(_evidence_update(evidence, after))
    guard = _guard(rows, plan)
    metadata = _apply_metadata(plan, guard)
    written_evidence = {**after, "revision": 2}
    cursor = _ApplyCursor(
        rows,
        (1, record_content_sha(evidence), None),
        written_rows=_rows(claim, written_evidence),
        plan=plan,
        metadata=metadata,
    )
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: _Connection(cursor)
    for name in (
        "_assert_obsolete_candidate_retirement",
        "_assert_stale_pending_topic_identity_retirement",
        "_assert_stale_candidate_projection_retirement",
        "_assert_stale_ai_cross_sermon_constraint_retirement",
        "_assert_global_id_uniqueness",
        "_assert_source_identity_uniqueness",
        "_assert_edge_integrity",
        "_assert_no_dangling_package_references",
        "_assert_no_uncoordinated_semantic_references",
        "_assert_current_viewpoint_revisions",
    ):
        setattr(store, name, lambda *_args, **_kwargs: None)
    store._invalidate_dependencies = lambda *_args, **_kwargs: 1

    with pytest.raises(ChangeSetConflict, match="uncoordinated ProductDependency"):
        store.apply_plan(
            plan,
            metadata=metadata,
            expected_claim_evidence_guard=guard,
        )


@pytest.mark.parametrize(
    ("omit_versions", "change_set_status", "message"),
    [
        (True, "applied", "ObjectVersion ledger differs"),
        (False, "planned", "ChangeSet ledger write is incomplete"),
    ],
)
def test_dedicated_apply_rejects_silent_ledger_write_loss_before_commit(
    omit_versions: bool, change_set_status: str, message: str
) -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    after = _evidence(["CL-1"])
    plan = _plan(_evidence_update(evidence, after))
    guard = _guard(rows, plan)
    metadata = _apply_metadata(plan, guard)
    cursor = _ApplyCursor(
        rows,
        (1, record_content_sha(evidence), None),
        written_rows=_rows(claim, {**after, "revision": 2}),
        plan=plan,
        metadata=metadata,
        omit_object_versions=omit_versions,
        change_set_status=change_set_status,
    )
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: _Connection(cursor)
    for name in (
        "_assert_obsolete_candidate_retirement",
        "_assert_stale_pending_topic_identity_retirement",
        "_assert_stale_candidate_projection_retirement",
        "_assert_stale_ai_cross_sermon_constraint_retirement",
        "_assert_global_id_uniqueness",
        "_assert_source_identity_uniqueness",
        "_assert_edge_integrity",
        "_assert_no_dangling_package_references",
        "_assert_no_uncoordinated_semantic_references",
        "_assert_current_viewpoint_revisions",
    ):
        setattr(store, name, lambda *_args, **_kwargs: None)
    store._invalidate_dependencies = lambda *_args, **_kwargs: 0

    with pytest.raises(ChangeSetConflict, match=message):
        store.apply_plan(
            plan,
            metadata=metadata,
            expected_claim_evidence_guard=guard,
        )


def test_retry_rechecks_original_backup_and_current_human_authority() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    after = _evidence(["CL-1"], revision=2)
    plan = _plan(_evidence_update(evidence, after))
    guard = _guard(rows, plan)
    metadata = _apply_metadata(plan, guard)
    current_rows = _rows(claim, after)
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    valid_cursor = _RetryCursor(
        current_rows,
        plan=plan,
        persisted_metadata=metadata,
        before_payload=evidence,
    )
    store.connect = lambda: _Connection(valid_cursor)

    assert store.apply_plan(
        plan,
        metadata=metadata,
        expected_claim_evidence_guard=guard,
    )["status"] == "already_applied"

    different_backup = _apply_metadata(plan, guard, backup_sha256="a" * 64)
    backup_cursor = _RetryCursor(
        current_rows,
        plan=plan,
        persisted_metadata=metadata,
        before_payload=evidence,
    )
    store.connect = lambda: _Connection(backup_cursor)
    with pytest.raises(ChangeSetConflict, match="metadata or backup"):
        store.apply_plan(
            plan,
            metadata=different_backup,
            expected_claim_evidence_guard=guard,
        )

    drift_cursor = _RetryCursor(
        rows,
        plan=plan,
        persisted_metadata=metadata,
        before_payload=evidence,
    )
    store.connect = lambda: _Connection(drift_cursor)
    with pytest.raises(ChangeSetConflict, match="final snapshot drifted"):
        store.apply_plan(
            plan,
            metadata=metadata,
            expected_claim_evidence_guard=guard,
        )

    claim_event = list(_SnapshotCursor(current_rows).review_events[0])
    claim_event[4] = "system"
    authority_cursor = _RetryCursor(
        current_rows,
        plan=plan,
        persisted_metadata=metadata,
        before_payload=evidence,
        review_events=[tuple(claim_event)],
    )
    store.connect = lambda: _Connection(authority_cursor)
    with pytest.raises(ChangeSetConflict, match="lacks one current human Claim authority"):
        store.apply_plan(
            plan,
            metadata=metadata,
            expected_claim_evidence_guard=guard,
        )

    forged_event = list(_SnapshotCursor(current_rows).review_events[0])
    forged_event[6] = {"change_set_id": "KCS-FORGED"}
    artifact_cursor = _RetryCursor(
        current_rows,
        plan=plan,
        persisted_metadata=metadata,
        before_payload=evidence,
        review_events=[tuple(forged_event)],
    )
    store.connect = lambda: _Connection(artifact_cursor)
    with pytest.raises(ChangeSetConflict, match="not bound to"):
        store.apply_plan(
            plan,
            metadata=metadata,
            expected_claim_evidence_guard=guard,
        )


def test_guard_rejects_unrelated_review_ledger_row_drift() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    plan = _plan(_evidence_update(evidence, _evidence(["CL-1"])))
    extra_event = (
        "REV-UNRELATED",
        "claims",
        "CL-UNRELATED",
        1,
        "system",
        "candidate",
        {"change_set_id": "KCS-UNRELATED"},
    )
    cursor = _SnapshotCursor(
        rows,
        review_events=[*_SnapshotCursor(rows).review_events, extra_event],
    )

    with pytest.raises(ChangeSetConflict, match="Review-event ledger drifted"):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            cursor, plan, _guard(rows, plan)
        )


def test_guard_review_ledger_root_includes_created_at() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    rows = _rows(claim, evidence)
    plan = _plan(_evidence_update(evidence, _evidence(["CL-1"])))

    class TimestampDriftCursor(_SnapshotCursor):
        def fetchall(self):
            result = super().fetchall()
            if (
                "FROM wang_knowledge.review_events" in self.last_sql
                and "ORDER BY review_event_id" in self.last_sql
            ):
                changed = list(result[0])
                changed[9] = datetime(
                    2026, 9, 13, 12, 0, 1, tzinfo=timezone.utc
                )
                return [tuple(changed)]
            return result

    with pytest.raises(ChangeSetConflict, match="Review-event ledger drifted"):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            TimestampDriftCursor(rows), plan, _guard(rows, plan)
        )


def test_guard_locks_and_rejects_referenced_source_lineage_drift() -> None:
    claim = _claim(["E-1"])
    evidence = {**_evidence([]), "source_fragment_id": "FR-1"}
    after = {**evidence, "produced_claim_ids": ["CL-1"]}
    rows = _rows(claim, evidence)
    plan = _plan(_evidence_update(evidence, after))
    fragment = {"fragment_id": "FR-1", "source_id": "SRC-1"}
    source = {"source_id": "SRC-1", "source_type": "transcript"}
    expected_source_rows = [
        {
            "collection": "source_fragments",
            "object_id": "FR-1",
            "revision": 1,
            "content_sha256": record_content_sha(fragment),
            "retired": False,
        },
        {
            "collection": "source_documents",
            "object_id": "SRC-1",
            "revision": 1,
            "content_sha256": record_content_sha(source),
            "retired": False,
        },
    ]
    cursor = _SnapshotCursor(
        rows,
        source_fragments=[
            (
                "source_fragments",
                "FR-1",
                2,
                record_content_sha(fragment),
                fragment,
                None,
            )
        ],
        source_documents=[
            (
                "source_documents",
                "SRC-1",
                1,
                record_content_sha(source),
                source,
                None,
            )
        ],
    )

    with pytest.raises(ChangeSetConflict, match="source-lineage snapshot drifted"):
        PostgresKnowledgeStore._assert_claim_evidence_reciprocity_guard(
            cursor,
            plan,
            _guard(rows, plan, source_lineage=expected_source_rows),
        )


def _store_with_apply_cursor(cursor: _ApplyCursor) -> PostgresKnowledgeStore:
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    store.connect = lambda: _Connection(cursor)
    for name in (
        "_assert_obsolete_candidate_retirement",
        "_assert_stale_pending_topic_identity_retirement",
        "_assert_stale_candidate_projection_retirement",
        "_assert_stale_ai_cross_sermon_constraint_retirement",
        "_assert_global_id_uniqueness",
        "_assert_source_identity_uniqueness",
        "_assert_edge_integrity",
        "_assert_no_dangling_package_references",
        "_assert_no_uncoordinated_semantic_references",
        "_assert_current_viewpoint_revisions",
    ):
        setattr(store, name, lambda *_args, **_kwargs: None)
    store._invalidate_dependencies = lambda *_args, **_kwargs: 0
    return store


def test_apply_rolls_back_if_review_ledger_changes_after_precheck() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    after = _evidence(["CL-1"])
    rows = _rows(claim, evidence)
    plan = _plan(_evidence_update(evidence, after))
    guard = _guard(rows, plan)
    metadata = _apply_metadata(plan, guard)

    class LateReviewDriftCursor(_ApplyCursor):
        ledger_reads = 0

        def fetchall(self):
            result = super().fetchall()
            if (
                "FROM wang_knowledge.review_events" in self.last_sql
                and "ORDER BY review_event_id" in self.last_sql
            ):
                self.ledger_reads += 1
                if self.ledger_reads >= 3:
                    changed = list(result[0])
                    changed[9] = datetime(
                        2026, 9, 13, 12, 0, 1, tzinfo=timezone.utc
                    )
                    return [tuple(changed)]
            return result

    cursor = LateReviewDriftCursor(
        rows,
        (1, record_content_sha(evidence), None),
        written_rows=_rows(claim, {**after, "revision": 2}),
        plan=plan,
        metadata=metadata,
    )

    with pytest.raises(ChangeSetConflict, match="unexpectedly changed review events"):
        _store_with_apply_cursor(cursor).apply_plan(
            plan,
            metadata=metadata,
            expected_claim_evidence_guard=guard,
        )


def test_apply_rolls_back_if_applied_changeset_timestamp_is_missing() -> None:
    claim = _claim(["E-1"])
    evidence = _evidence([])
    after = _evidence(["CL-1"])
    rows = _rows(claim, evidence)
    plan = _plan(_evidence_update(evidence, after))
    guard = _guard(rows, plan)
    metadata = _apply_metadata(plan, guard)

    class MissingAppliedAtCursor(_ApplyCursor):
        def fetchone(self):
            result = super().fetchone()
            if (
                result is not None
                and "SELECT fingerprint_sha256, package_id, source_kind"
                in self.last_sql
            ):
                changed = list(result)
                changed[8] = None
                return tuple(changed)
            return result

    cursor = MissingAppliedAtCursor(
        rows,
        (1, record_content_sha(evidence), None),
        written_rows=_rows(claim, {**after, "revision": 2}),
        plan=plan,
        metadata=metadata,
    )

    with pytest.raises(ChangeSetConflict, match="ChangeSet ledger write is incomplete"):
        _store_with_apply_cursor(cursor).apply_plan(
            plan,
            metadata=metadata,
            expected_claim_evidence_guard=guard,
        )
