from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

import pytest

from backend.api.canonical_repository.postgres_store import (
    CLAIM_EVIDENCE_HUMAN_AUTHORITY_SNAPSHOT_SCHEMA_VERSION,
    CLAIM_EVIDENCE_SOURCE_QUEUE_METADATA_KEY,
    CLAIM_EVIDENCE_SOURCE_REPLAY_SOURCE_KIND,
    ChangeOperation,
    ChangeSetConflict,
    ChangeSetPlan,
    PostgresKnowledgeStore,
    PostgresKnowledgeStoreError,
    _build_claim_evidence_human_authority_snapshot,
    _sealed_artifact,
    record_content_sha,
    sha256_json,
)


NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def _source_payload() -> dict[str, Any]:
    return {
        "source_id": "SRC-ONE",
        "source_type": "sermon_transcript",
        "transcript_id": "SERMON-ONE",
        "source_body_sha256": "a" * 64,
        "extraction_record_namespace": "SERMON-ONE",
        "revision": 1,
    }


def _expected_generation() -> list[dict[str, Any]]:
    payload = _source_payload()
    return [
        {
            "source_type": "sermon_transcript",
            "row_key": "SERMON-ONE",
            "active_source_document_id": "SRC-ONE",
            "expected_revision": 1,
            "expected_content_sha256": record_content_sha(payload),
            "source_body_sha256": "a" * 64,
            "extraction_record_namespace": "SERMON-ONE",
        }
    ]


def _evidence_payload(*, revision: int, statement: str = "before") -> dict[str, Any]:
    return {
        "evidence_step_id": "E-ONE",
        "statement": statement,
        "produced_claim_ids": [],
        "review_status": "candidate",
        "revision": revision,
    }


def _head_row(
    *,
    payload: Mapping[str, Any],
    collection: str = "evidence_steps",
    object_id: str = "E-ONE",
    producer_id: str = "KCS-BASE",
    producer_kind: str = "knowledge_package",
    operation: str = "create",
    retired_at: Any = None,
) -> tuple[Any, ...]:
    revision = int(payload["revision"])
    content_sha = record_content_sha(payload)
    return (
        collection,
        object_id,
        revision,
        content_sha,
        dict(payload),
        retired_at,
        producer_id,
        revision,
        content_sha,
        dict(payload),
        "applied",
        producer_kind,
        operation,
        revision,
        content_sha,
    )


def _human_snapshot(heads: list[tuple[Any, ...]]) -> dict[str, Any]:
    return _build_claim_evidence_human_authority_snapshot(heads, [], [])


def _plan() -> ChangeSetPlan:
    before = _evidence_payload(revision=1)
    after = _evidence_payload(revision=2, statement="after")
    operation = ChangeOperation(
        operation="update",
        collection="evidence_steps",
        object_id="E-ONE",
        before_sha256=record_content_sha(before),
        after_sha256=record_content_sha(after),
        before_revision=1,
        after_revision=2,
        payload=after,
    )
    return ChangeSetPlan(
        change_set_id="KCS-SOURCE-QUEUE-ONE",
        fingerprint_sha256=sha256_json({"source-queue-test": 1}),
        package_id="PKG-SOURCE-QUEUE-ONE",
        source_kind=CLAIM_EVIDENCE_SOURCE_REPLAY_SOURCE_KIND,
        source_sha256="e" * 64,
        operations=(operation,),
        unchanged=0,
        ignored_keys=(),
    )


def _metadata(
    plan: ChangeSetPlan,
    generations: list[dict[str, Any]],
    human_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        CLAIM_EVIDENCE_SOURCE_QUEUE_METADATA_KEY: {
            "execution_plan_sha256": "1" * 64,
            "work_unit_sha256": "2" * 64,
            "source_queue_sha256": "3" * 64,
            "authority_validation_sha256": "4" * 64,
            "package_proof": {
                "kind": "historical_exact_replay",
                "authority_unit_id": "AUTH-ONE",
                "effective_canonical_sha256": plan.source_sha256,
                "historical_source_kind": "knowledge_package",
                "guarded_apply_source_kind": plan.source_kind,
            },
            "expected_source_generations_sha256": sha256_json(generations),
            "expected_human_authority_snapshot_sha256": human_snapshot[
                "artifact_sha256"
            ],
            "human_impact_sha256": "5" * 64,
        }
    }


class _QueueCursor:
    def __init__(
        self,
        *,
        extra_head: bool = False,
        omit_object_write: bool = False,
        omit_object_version: bool = False,
        omit_applied_status: bool = False,
    ):
        before = _evidence_payload(revision=1)
        self.objects: dict[tuple[str, str], dict[str, Any]] = {
            ("evidence_steps", "E-ONE"): {
                "revision": 1,
                "content_sha256": record_content_sha(before),
                "payload": before,
                "retired_at": None,
                "producer_id": "KCS-BASE",
                "producer_kind": "knowledge_package",
                "producer_operation": "create",
            }
        }
        if extra_head:
            extra = {
                "evidence_step_id": "E-DRIFT",
                "statement": "untouched drift",
                "produced_claim_ids": [],
                "review_status": "candidate",
                "revision": 1,
            }
            self.objects[("evidence_steps", "E-DRIFT")] = {
                "revision": 1,
                "content_sha256": record_content_sha(extra),
                "payload": extra,
                "retired_at": None,
                "producer_id": "KCS-DRIFT",
                "producer_kind": "knowledge_package",
                "producer_operation": "create",
            }
        self.source = _source_payload()
        self.review_rows: list[tuple[Any, ...]] = []
        self.operations: list[tuple[Any, ...]] = []
        self.versions: list[tuple[Any, ...]] = []
        self.change_set: dict[str, Any] | None = None
        self.omit_object_write = omit_object_write
        self.omit_object_version = omit_object_version
        self.omit_applied_status = omit_applied_status
        self.last_sql = ""
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.last_sql = " ".join(sql.split())
        self.statements.append(self.last_sql)
        if self.last_sql.startswith("INSERT INTO wang_knowledge.change_sets"):
            self.change_set = {
                "id": params[0],
                "fingerprint": params[1],
                "package_id": params[2],
                "source_kind": params[3],
                "source_sha": params[4],
                "status": "planned",
                "summary": json.loads(params[5]),
                "metadata": json.loads(params[6]),
            }
        elif self.last_sql.startswith("INSERT INTO wang_knowledge.objects"):
            if not self.omit_object_write:
                collection, object_id, revision = str(params[0]), str(params[1]), int(params[2])
                self.objects[(collection, object_id)] = {
                    "revision": revision,
                    "content_sha256": str(params[5]),
                    "payload": json.loads(params[7]),
                    "retired_at": None,
                    "producer_id": self.change_set["id"],
                    "producer_kind": self.change_set["source_kind"],
                    "producer_operation": "update",
                }
        elif self.last_sql.startswith("INSERT INTO wang_knowledge.object_versions"):
            if not self.omit_object_version:
                self.versions.append(
                    (
                        str(params[0]),
                        str(params[1]),
                        int(params[2]),
                        str(params[3]),
                        json.loads(params[4]),
                        str(params[5]),
                    )
                )
        elif self.last_sql.startswith("INSERT INTO wang_knowledge.change_operations"):
            self.operations.append(
                (
                    int(params[1]),
                    str(params[2]),
                    str(params[3]),
                    str(params[4]),
                    params[5],
                    params[6],
                    params[7],
                    params[8],
                    json.loads(params[9]),
                )
            )
        elif self.last_sql.startswith("UPDATE wang_knowledge.change_sets"):
            if not self.omit_applied_status:
                assert self.change_set is not None
                self.change_set["status"] = "applied"
                self.change_set["summary"] = json.loads(params[0])

    def _heads(self) -> list[tuple[Any, ...]]:
        result = []
        for (collection, object_id), row in sorted(self.objects.items()):
            result.append(
                _head_row(
                    payload=row["payload"],
                    collection=collection,
                    object_id=object_id,
                    producer_id=row["producer_id"],
                    producer_kind=row["producer_kind"],
                    operation=row["producer_operation"],
                    retired_at=row["retired_at"],
                )
            )
        return result

    def fetchall(self):
        if "collection='source_documents'" in self.last_sql:
            return [
                (
                    "SRC-ONE",
                    1,
                    record_content_sha(self.source),
                    self.source,
                )
            ]
        if "FROM wang_knowledge.objects o" in self.last_sql:
            return self._heads()
        if "FROM wang_knowledge.review_events re" in self.last_sql:
            return []
        if "FROM wang_knowledge.review_events" in self.last_sql:
            return list(self.review_rows)
        if "FROM wang_knowledge.change_operations" in self.last_sql:
            return list(self.operations)
        if "FROM wang_knowledge.object_versions" in self.last_sql:
            return sorted(self.versions)
        return []

    def fetchone(self):
        if "WHERE fingerprint_sha256" in self.last_sql:
            if self.change_set and self.change_set["status"] == "applied":
                return (
                    self.change_set["id"],
                    "applied",
                    self.change_set["summary"],
                    self.change_set["metadata"],
                )
            return None
        if "SELECT fingerprint_sha256, package_id, source_kind" in self.last_sql:
            if not self.change_set:
                return None
            return (
                self.change_set["fingerprint"],
                self.change_set["package_id"],
                self.change_set["source_kind"],
                self.change_set["source_sha"],
                self.change_set["status"],
                self.change_set["summary"],
                self.change_set["metadata"],
                NOW,
                NOW,
            )
        if "SELECT revision, content_sha256, retired_at" in self.last_sql:
            row = self.objects.get((str(self._last_params[0]), str(self._last_params[1])))
            return (
                (row["revision"], row["content_sha256"], row["retired_at"])
                if row
                else None
            )
        if "SELECT revision, content_sha256, payload, retired_at" in self.last_sql:
            row = self.objects.get((str(self._last_params[0]), str(self._last_params[1])))
            return (
                (
                    row["revision"],
                    row["content_sha256"],
                    row["payload"],
                    row["retired_at"],
                )
                if row
                else None
            )
        return None

    @property
    def _last_params(self) -> tuple[Any, ...]:
        return self.__last_params

    @_last_params.setter
    def _last_params(self, value: tuple[Any, ...]) -> None:
        self.__last_params = value


class _Connection:
    def __init__(self, cursor: _QueueCursor):
        self.cursor_value = cursor
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_exc):
        self.rolled_back = exc_type is not None
        return False

    def cursor(self) -> _QueueCursor:
        return self.cursor_value


class _QueueStore(PostgresKnowledgeStore):
    def __init__(self, cursor: _QueueCursor):
        self.connection = _Connection(cursor)

    def connect(self) -> _Connection:
        return self.connection

    _assert_obsolete_candidate_retirement = staticmethod(lambda *_args: None)
    _assert_stale_pending_topic_identity_retirement = staticmethod(lambda *_args: None)
    _assert_stale_candidate_projection_retirement = staticmethod(lambda *_args: None)
    _assert_stale_ai_cross_sermon_constraint_retirement = staticmethod(lambda *_args: None)
    _assert_global_id_uniqueness = staticmethod(lambda *_args: None)
    _assert_source_identity_uniqueness = staticmethod(lambda *_args: None)
    _assert_edge_integrity = staticmethod(lambda *_args: None)
    _assert_no_dangling_package_references = staticmethod(lambda *_args: None)
    _assert_no_uncoordinated_semantic_references = staticmethod(lambda *_args: None)
    _assert_current_viewpoint_revisions = staticmethod(lambda *_args: None)
    _invalidate_dependencies = staticmethod(lambda *_args: 0)


# Preserve params after dispatch; keeping this outside execute makes the mutation
# branches above easy to read while still supporting SELECT fetches.
_execute = _QueueCursor.execute


def _execute_with_params(self: _QueueCursor, sql: str, params: tuple[Any, ...] = ()) -> None:
    self._last_params = params
    _execute(self, sql, params)


_QueueCursor.execute = _execute_with_params


def _apply_fixture(**cursor_options: Any):
    cursor = _QueueCursor(**cursor_options)
    store = _QueueStore(cursor)
    plan = _plan()
    generations = _expected_generation()
    snapshot = _human_snapshot(cursor._heads())
    metadata = _metadata(plan, generations, snapshot)
    return store, cursor, plan, generations, snapshot, metadata


def test_dedicated_source_kind_cannot_use_generic_apply_plan() -> None:
    store, _cursor, plan, _generations, _snapshot, metadata = _apply_fixture()

    with pytest.raises(PostgresKnowledgeStoreError, match="must use"):
        store.apply_plan(plan, metadata=metadata)


def test_source_apply_requires_store_owned_review_ledger_root() -> None:
    store, _cursor, plan, generations, snapshot, metadata = _apply_fixture()
    weakened = dict(snapshot)
    weakened.pop("review_event_ledger_snapshot")
    weakened = _sealed_artifact(weakened)
    metadata = _metadata(plan, generations, weakened)

    with pytest.raises(PostgresKnowledgeStoreError, match="full review-event ledger"):
        store.apply_claim_evidence_source_queue_plan(
            plan,
            metadata=metadata,
            expected_source_generations=generations,
            expected_human_authority_snapshot=weakened,
        )


def test_source_apply_rejects_drift_in_an_untouched_head() -> None:
    cursor = _QueueCursor(extra_head=True)
    store = _QueueStore(cursor)
    plan = _plan()
    generations = _expected_generation()
    baseline = _human_snapshot([_head_row(payload=_evidence_payload(revision=1))])
    metadata = _metadata(plan, generations, baseline)

    with pytest.raises(ChangeSetConflict, match="human-authority snapshot drifted"):
        store.apply_claim_evidence_source_queue_plan(
            plan,
            metadata=metadata,
            expected_source_generations=generations,
            expected_human_authority_snapshot=baseline,
        )
    assert store.connection.rolled_back is True


def test_source_apply_rejects_physical_or_semantic_source_generation_drift() -> None:
    store, cursor, plan, generations, snapshot, metadata = _apply_fixture()
    cursor.source = {**cursor.source, "source_body_sha256": "b" * 64}

    with pytest.raises(ChangeSetConflict, match="SourceDocument generation drifted"):
        store.apply_claim_evidence_source_queue_plan(
            plan,
            metadata=metadata,
            expected_source_generations=generations,
            expected_human_authority_snapshot=snapshot,
        )


def test_source_apply_rejects_full_review_ledger_drift() -> None:
    store, cursor, plan, generations, snapshot, metadata = _apply_fixture()
    cursor.review_rows.append(
        (
            "REV-UNRELATED",
            "questions",
            "Q-ONE",
            1,
            "system",
            "fixture",
            "recorded",
            "unrelated ledger drift",
            {},
            NOW,
        )
    )

    with pytest.raises(ChangeSetConflict, match="human-authority snapshot drifted"):
        store.apply_claim_evidence_source_queue_plan(
            plan,
            metadata=metadata,
            expected_source_generations=generations,
            expected_human_authority_snapshot=snapshot,
        )


@pytest.mark.parametrize(
    ("option", "message"),
    [
        ("omit_object_write", "object readback"),
        ("omit_object_version", "ObjectVersion ledger"),
        ("omit_applied_status", "ChangeSet ledger"),
    ],
)
def test_source_apply_rolls_back_silent_ledger_or_object_writes(
    option: str, message: str
) -> None:
    store, _cursor, plan, generations, snapshot, metadata = _apply_fixture(
        **{option: True}
    )

    with pytest.raises(ChangeSetConflict, match=message):
        store.apply_claim_evidence_source_queue_plan(
            plan,
            metadata=metadata,
            expected_source_generations=generations,
            expected_human_authority_snapshot=snapshot,
        )
    assert store.connection.rolled_back is True


def test_source_apply_and_retry_verify_the_exact_final_state() -> None:
    store, cursor, plan, generations, snapshot, metadata = _apply_fixture()

    first = store.apply_claim_evidence_source_queue_plan(
        plan,
        metadata=metadata,
        expected_source_generations=generations,
        expected_human_authority_snapshot=snapshot,
    )
    second = store.apply_claim_evidence_source_queue_plan(
        plan,
        metadata=metadata,
        expected_source_generations=generations,
        expected_human_authority_snapshot=snapshot,
    )

    assert first["status"] == "applied"
    assert second["status"] == "already_applied"
    assert cursor.objects[("evidence_steps", "E-ONE")]["revision"] == 2
    assert cursor.statements[0].startswith("SELECT pg_advisory_xact_lock")


def test_source_retry_rejects_different_execution_metadata() -> None:
    store, _cursor, plan, generations, snapshot, metadata = _apply_fixture()
    store.apply_claim_evidence_source_queue_plan(
        plan,
        metadata=metadata,
        expected_source_generations=generations,
        expected_human_authority_snapshot=snapshot,
    )
    changed = json.loads(json.dumps(metadata))
    changed[CLAIM_EVIDENCE_SOURCE_QUEUE_METADATA_KEY][
        "execution_plan_sha256"
    ] = "f" * 64

    with pytest.raises(ChangeSetConflict, match="metadata differs"):
        store.apply_claim_evidence_source_queue_plan(
            plan,
            metadata=changed,
            expected_source_generations=generations,
            expected_human_authority_snapshot=snapshot,
        )


def test_bare_superseded_head_is_not_human_authority() -> None:
    payload = {
        "claim_id": "CL-AI-SUPERSEDED",
        "statement": "AI merge",
        "evidence_step_ids": [],
        "review_status": "superseded",
        "revision": 4,
    }

    snapshot = _build_claim_evidence_human_authority_snapshot(
        [
            _head_row(
                payload=payload,
                collection="claims",
                object_id="CL-AI-SUPERSEDED",
            )
        ],
        [],
        [],
    )

    assert snapshot["schema_version"] == (
        CLAIM_EVIDENCE_HUMAN_AUTHORITY_SNAPSHOT_SCHEMA_VERSION
    )
    assert snapshot["protected_records"] == []
    assert snapshot["scanned_record_count"] == 1


def test_approved_head_without_ledger_proof_fails_closed() -> None:
    payload = {
        "claim_id": "CL-APPROVED",
        "statement": "human claim",
        "evidence_step_ids": [],
        "review_status": "approved",
        "revision": 1,
    }

    with pytest.raises(PostgresKnowledgeStoreError, match="lacks ledger-proven"):
        _build_claim_evidence_human_authority_snapshot(
            [
                _head_row(
                    payload=payload,
                    collection="claims",
                    object_id="CL-APPROVED",
                )
            ],
            [],
            [],
        )


def test_retired_head_preserves_only_ledger_proven_historical_human_authority() -> None:
    payload = {
        "claim_id": "CL-HUMAN-RETIRED",
        "statement": "human claim",
        "evidence_step_ids": [],
        "review_status": "approved",
        "revision": 1,
    }
    content_sha = record_content_sha(payload)
    event_artifact = {"change_set_id": "KCS-REVIEW"}
    event = (
        "REV-HUMAN",
        "claims",
        "CL-HUMAN-RETIRED",
        1,
        "human",
        "editor",
        "approved",
        "human ruling",
        event_artifact,
        NOW,
    )
    current_version = {**payload, "revision": 2}
    head = (
        "claims",
        "CL-HUMAN-RETIRED",
        2,
        content_sha,
        payload,
        NOW,
        "KCS-RETIRE",
        2,
        content_sha,
        current_version,
        "applied",
        "retirement",
        "retire",
        2,
        content_sha,
    )
    proof = (
        "REV-HUMAN",
        "claims",
        "CL-HUMAN-RETIRED",
        1,
        "human",
        "approved",
        event_artifact,
        1,
        content_sha,
        payload,
        "KCS-REVIEW",
        "applied",
        "review_decision",
        "update",
        1,
        content_sha,
    )

    snapshot = _build_claim_evidence_human_authority_snapshot(
        [head], [event], [proof]
    )

    assert snapshot["protected_records"][0]["retired"] is True
    assert snapshot["protected_records"][0]["revision"] == 2
    assert snapshot["protected_records"][0]["reviewed_revision"] == 1
    assert snapshot["protected_records"][0]["producer_change_set_id"] == (
        "KCS-REVIEW"
    )


def test_source_plan_may_not_mutate_a_protected_human_key() -> None:
    payload = {
        "claim_id": "CL-HUMAN",
        "statement": "human claim",
        "evidence_step_ids": [],
        "review_status": "approved",
        "revision": 1,
    }
    content_sha = record_content_sha(payload)
    event_artifact = {"change_set_id": "KCS-REVIEW"}
    event = (
        "REV-HUMAN",
        "claims",
        "CL-HUMAN",
        1,
        "human",
        "editor",
        "approved",
        "human ruling",
        event_artifact,
        NOW,
    )
    proof = (
        "REV-HUMAN",
        "claims",
        "CL-HUMAN",
        1,
        "human",
        "approved",
        event_artifact,
        1,
        content_sha,
        payload,
        "KCS-REVIEW",
        "applied",
        "review_decision",
        "update",
        1,
        content_sha,
    )
    snapshot = _build_claim_evidence_human_authority_snapshot(
        [
            _head_row(
                payload=payload,
                collection="claims",
                object_id="CL-HUMAN",
                producer_id="KCS-REVIEW",
                producer_kind="review_decision",
                operation="update",
            )
        ],
        [event],
        [proof],
    )
    after = {**payload, "revision": 2}
    operation = ChangeOperation(
        operation="update",
        collection="claims",
        object_id="CL-HUMAN",
        before_sha256=content_sha,
        after_sha256=content_sha,
        before_revision=1,
        after_revision=2,
        payload=after,
    )
    plan = ChangeSetPlan(
        change_set_id="KCS-PROTECTED",
        fingerprint_sha256="d" * 64,
        package_id="PKG-PROTECTED",
        source_kind=CLAIM_EVIDENCE_SOURCE_REPLAY_SOURCE_KIND,
        source_sha256="e" * 64,
        operations=(operation,),
        unchanged=0,
        ignored_keys=(),
    )

    with pytest.raises(ChangeSetConflict, match="cannot mutate human-settled"):
        PostgresKnowledgeStore._assert_claim_evidence_source_queue_plan_authority(
            plan, snapshot
        )
