from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
from typing import Any, Iterator
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402
from psycopg.conninfo import make_conninfo  # noqa: E402

from backend.api.canonical_repository.postgres_store import (  # noqa: E402
    ChangeSetConflict,
    POSTGRES_APPLY_ADVISORY_LOCK_KEY,
    PostgresKnowledgeStore,
    sha256_json,
)
from backend.pipeline.claim_evidence_reciprocity_repair import (  # noqa: E402
    BACKUP_VERIFICATION_SCHEMA_VERSION,
    COMMITTED_RECEIPT_SCHEMA_VERSION,
    MISMATCH_CLAIM_ONLY,
    PREREQUISITES_MANIFEST_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    _build_freeze_binding,
    _change_set_from_dict,
    _read_post_apply_ledger,
    apply_sealed_plan,
    build_reciprocity_audit,
    build_repair_plan,
    deserialize_repair_plan,
    freeze_claim_evidence_reciprocity_input,
    seal_artifact,
    validate_sealed_artifact,
    verify_postgres_backup_dump,
)
from backend.pipeline.claim_evidence_pair_adjudication import (  # noqa: E402
    FINAL_DECISIONS_SCHEMA_VERSION,
    apply_pair_repair_plan,
    build_pair_repair_plan,
    build_relation_packets,
    packet_source_keys,
    read_packet_source_records,
)


_POSTGRES_PORT = 5432


@dataclass(frozen=True)
class _TemporaryPostgres:
    root: Path
    data_dir: Path
    socket_dir: Path
    pg_ctl: str

    def dsn(self, database: str) -> str:
        return make_conninfo(
            dbname=database,
            user="postgres",
            host=str(self.socket_dir),
            port=_POSTGRES_PORT,
        )


@dataclass(frozen=True)
class _SeededStore:
    store: PostgresKnowledgeStore
    prerequisites_manifest: dict[str, Any]


def _run_postgres(command: list[str], *, purpose: str) -> None:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"temporary PostgreSQL {purpose} failed: {details}")


@pytest.fixture(scope="session")
def _temporary_postgres(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[_TemporaryPostgres]:
    """Start a socket-only cluster whose DSNs cannot resolve to production."""

    initdb = shutil.which("initdb")
    pg_ctl = shutil.which("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.skip("PostgreSQL integration tests require initdb and pg_ctl")

    root = tmp_path_factory.mktemp("wkp364-postgres-")
    data_dir = root / "data"
    # PostgreSQL caps Unix-socket paths at roughly 100 bytes.  macOS pytest
    # roots are longer than that before a filename is added, so keep only the
    # socket in a separate, still-unique short-lived directory.
    socket_dir = Path(tempfile.mkdtemp(prefix="wkp364-pg-sock-", dir="/tmp"))
    log_path = root / "postgres.log"
    started = False
    try:
        _run_postgres(
            [
                initdb,
                "-D",
                str(data_dir),
                "-A",
                "trust",
                "--no-locale",
                "--encoding=UTF8",
                "-U",
                "postgres",
            ],
            purpose="initdb",
        )
        _run_postgres(
            [
                pg_ctl,
                "-D",
                str(data_dir),
                "-l",
                str(log_path),
                "-o",
                (
                    f"-F -h '' -k {socket_dir} -p {_POSTGRES_PORT} "
                    "-c synchronous_commit=off -c full_page_writes=off"
                ),
                "-w",
                "start",
            ],
            purpose="start",
        )
        started = True
        cluster = _TemporaryPostgres(
            root=root,
            data_dir=data_dir,
            socket_dir=socket_dir,
            pg_ctl=pg_ctl,
        )
        with psycopg.connect(cluster.dsn("postgres")) as connection:
            assert connection.execute("SELECT current_database()").fetchone() == (
                "postgres",
            )
        yield cluster
    finally:
        if started:
            _run_postgres(
                [
                    pg_ctl,
                    "-D",
                    str(data_dir),
                    "-m",
                    "immediate",
                    "-w",
                    "stop",
                ],
                purpose="stop",
            )
        if root.exists():
            shutil.rmtree(root)
        if socket_dir.exists():
            shutil.rmtree(socket_dir)


@pytest.fixture
def postgres_store(
    _temporary_postgres: _TemporaryPostgres,
) -> Iterator[PostgresKnowledgeStore]:
    database = f"wkp364_{uuid.uuid4().hex}"
    admin_dsn = _temporary_postgres.dsn("postgres")
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database))
        )

    database_url = _temporary_postgres.dsn(database)
    store = PostgresKnowledgeStore(database_url)
    assert store.database_url == database_url
    store.migrate()
    try:
        yield store
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                """SELECT pg_terminate_backend(pid)
                   FROM pg_stat_activity
                   WHERE datname=%s AND pid <> pg_backend_pid()""",
                (database,),
            )
            connection.execute(
                sql.SQL("DROP DATABASE {}").format(sql.Identifier(database))
            )


def _seed_pairs(
    store: PostgresKnowledgeStore,
    *,
    pair_count: int,
    reciprocal: bool,
    reviewer_kind: str | None = None,
) -> _SeededStore:
    claim_ids = [f"CL-{index}" for index in range(1, pair_count + 1)]
    evidence_ids = [f"EV-{index}" for index in range(1, pair_count + 1)]
    package = {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": "WKP364-INTEGRATION-SEED",
        "source_documents": [
            {
                "source_id": "SRC-WKP364-FIXTURE",
                "source_type": "sermon_transcript",
                "transcript_id": "WKP364-FIXTURE",
                "title": "WKP364 integration fixture",
            }
        ],
        "source_fragments": [
            {
                "fragment_id": "FR-WKP364-FIXTURE",
                "source_id": "SRC-WKP364-FIXTURE",
                "verbatim_excerpt": "integration fixture source text",
            }
        ],
        "claims": [
            {
                "claim_id": claim_id,
                "statement": f"integration fixture claim {index}",
                "claim_type": "explicit_claim",
                "evidence_step_ids": [evidence_ids[index - 1]],
            }
            for index, claim_id in enumerate(claim_ids, start=1)
        ],
        "evidence_steps": [
            {
                "evidence_step_id": evidence_id,
                "source_fragment_id": "FR-WKP364-FIXTURE",
                "statement": f"integration fixture evidence {index}",
                "support_eligibility": "withheld_unreviewed",
                "produced_claim_ids": (
                    [claim_ids[index - 1]] if reciprocal else []
                ),
            }
            for index, evidence_id in enumerate(evidence_ids, start=1)
        ],
    }
    seed_plan = store.plan_package(package, source_kind="integration_fixture")
    assert store.apply_plan(seed_plan)["status"] == "applied"
    if reviewer_kind is not None:
        for claim_id in claim_ids:
            review = store.record_review(
                "claims",
                claim_id,
                decision="approved",
                reason="integration fixture ruling",
                reviewer_id=f"integration-{reviewer_kind}",
                reviewer_kind=reviewer_kind,
                expected_revision=1,
            )
            assert review["revision"] == 2

    prerequisites_manifest = seal_artifact(
        {
            "schema_version": PREREQUISITES_MANIFEST_SCHEMA_VERSION,
            "change_sets": [
                {
                    "change_set_id": seed_plan.change_set_id,
                    "fingerprint_sha256": seed_plan.fingerprint_sha256,
                }
            ],
        }
    )
    return _SeededStore(
        store=store,
        prerequisites_manifest=prerequisites_manifest,
    )


def _freeze(seeded: _SeededStore) -> dict[str, Any]:
    frozen = freeze_claim_evidence_reciprocity_input(
        seeded.store,
        prerequisites_manifest=seeded.prerequisites_manifest,
    )
    assert frozen["freeze_transaction"] == {
        "isolation": "repeatable_read",
        "read_only": True,
        "advisory_lock_key": "wang_knowledge.apply_plan.v1",
    }
    return frozen


def _repair_from_frozen(
    frozen: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    audit = build_reciprocity_audit(
        frozen["active_records"],
        prerequisites=frozen["prerequisites"],
        authority_records=frozen["authority_records"],
        source_lineage_findings=frozen["source_lineage_findings"],
        review_event_ledger_count=frozen["review_event_ledger_count"],
        review_event_ledger_snapshot=frozen["review_event_ledger_snapshot"],
        freeze_binding=_build_freeze_binding(
            frozen_input_artifact_sha256=frozen["artifact_sha256"],
            frozen_at=frozen["frozen_at"],
            database_identity=frozen["database_identity"],
        ),
        source_lineage_identity_snapshot=frozen[
            "source_lineage_identity_snapshot"
        ],
    )
    artifact = build_repair_plan(
        audit,
        frozen["active_records"],
        product_dependencies=frozen["product_dependencies"],
        product_dependency_records=frozen["product_dependency_records"],
    )
    value, plan = deserialize_repair_plan(artifact)
    return audit, value, plan


def _apply_repair(
    store: PostgresKnowledgeStore,
    value: dict[str, Any],
    plan: Any,
    *,
    backup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    guard = value["store_guard"]
    backup = backup or _backup_verification(
        freeze_binding=value["freeze_binding"]
    )
    return store.apply_plan(
        plan,
        metadata={
            "claim_evidence_reciprocity_repair": {
                "audit_artifact_sha256": value["audit_artifact_sha256"],
                "plan_artifact_sha256": value["artifact_sha256"],
                "action_manifest_sha256": value["action_manifest_sha256"],
                "operation_manifest_sha256": value["operation_manifest_sha256"],
                "store_guard": guard,
                "backup": backup,
            }
        },
        expected_claim_evidence_guard=guard,
    )


def _backup_verification(
    *,
    path: str = "/tmp/wkp364-integration-fixture.dump",
    sha_character: str = "a",
    freeze_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    freeze_binding = freeze_binding or {
        "frozen_input_artifact_sha256": "1" * 64,
        "frozen_at": "2026-09-13T12:00:00+00:00",
        "database_identity": {
            "database_name": "wkp364_fixture",
            "server_version_num": "140017",
        },
    }
    required_table_coverage = [
        {
            "table_name": table_name,
            "has_table_definition": True,
            "has_table_data": True,
        }
        for table_name in (
            "change_operations",
            "change_sets",
            "object_versions",
            "objects",
            "review_events",
        )
    ]
    return seal_artifact(
        {
            "schema_version": BACKUP_VERIFICATION_SCHEMA_VERSION,
            "path": path,
            "sha256": sha_character * 64,
            "size_bytes": 1,
            "pg_restore_list_sha256": "b" * 64,
            "pg_restore_entry_count": 1,
            "archive_created_at": str(freeze_binding["frozen_at"]),
            "archive_database_name": str(
                freeze_binding["database_identity"]["database_name"]
            ),
            "contains_wang_knowledge_schema": True,
            "contains_required_table_data": True,
            "required_table_coverage": required_table_coverage,
            "required_table_coverage_sha256": sha256_json(
                required_table_coverage
            ),
            "timestamp_precision": "second",
            "frozen_input_artifact_sha256": str(
                freeze_binding["frozen_input_artifact_sha256"]
            ),
            "frozen_at": str(freeze_binding["frozen_at"]),
            "database_identity": deepcopy(freeze_binding["database_identity"]),
        }
    )


def _database_state(store: PostgresKnowledgeStore) -> dict[str, list[tuple[Any, ...]]]:
    queries = {
        "change_sets": """SELECT change_set_id, fingerprint_sha256, package_id,
                                  source_kind, source_sha256, status, summary, metadata,
                                  created_at, applied_at
                           FROM wang_knowledge.change_sets
                           ORDER BY change_set_id""",
        "objects": """SELECT collection, object_id, revision, review_status,
                               visibility, content_sha256, payload, retired_at
                        FROM wang_knowledge.objects
                        ORDER BY collection, object_id""",
        "object_versions": """SELECT collection, object_id, revision,
                                      content_sha256, payload, change_set_id, recorded_at
                               FROM wang_knowledge.object_versions
                               ORDER BY collection, object_id, revision""",
        "change_operations": """SELECT change_set_id, operation_index, operation,
                                        collection, object_id, before_sha256,
                                        after_sha256, before_revision, after_revision,
                                        details
                                 FROM wang_knowledge.change_operations
                                 ORDER BY change_set_id, operation_index""",
        "review_events": """SELECT review_event_id, collection, object_id,
                                    object_revision, reviewer_kind, reviewer_id,
                                    decision, reason, artifact, created_at
                             FROM wang_knowledge.review_events
                             ORDER BY review_event_id""",
    }
    with store.connect() as connection, connection.cursor() as cursor:
        return {
            name: list(cursor.execute(query).fetchall())
            for name, query in queries.items()
        }


def _count(store: PostgresKnowledgeStore, query: str, params: tuple[Any, ...] = ()) -> int:
    with store.connect() as connection:
        row = connection.execute(query, params).fetchone()
    assert row is not None
    return int(row[0])


def _object_row(
    store: PostgresKnowledgeStore, collection: str, object_id: str
) -> tuple[int, dict[str, Any]]:
    with store.connect() as connection:
        row = connection.execute(
            """SELECT revision, payload
               FROM wang_knowledge.objects
               WHERE collection=%s AND object_id=%s AND retired_at IS NULL""",
            (collection, object_id),
        ).fetchone()
    assert row is not None
    return int(row[0]), dict(row[1])


@pytest.mark.parametrize("reader_kind", ["freeze", "post_apply_readback"])
def test_locked_read_snapshot_is_established_only_after_waiting_for_apply_lock(
    postgres_store: PostgresKnowledgeStore,
    reader_kind: str,
) -> None:
    seeded = _seed_pairs(
        postgres_store,
        pair_count=1,
        reciprocal=False,
    )
    writer = psycopg.connect(postgres_store.database_url)
    try:
        writer.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (POSTGRES_APPLY_ADVISORY_LOCK_KEY,),
        )
        writer.execute(
            """INSERT INTO wang_knowledge.review_events
               (review_event_id, collection, object_id, object_revision,
                reviewer_kind, reviewer_id, decision, reason, artifact)
               VALUES ('REV-WKP364-LOCK-ORDER', 'claims', 'CL-1', 1,
                       'system', 'lock-order-test', 'candidate',
                       'must become visible after lock wait', '{}'::jsonb)"""
        )

        def read() -> dict[str, Any]:
            if reader_kind == "freeze":
                return _freeze(seeded)
            return _read_post_apply_ledger(
                postgres_store,
                SimpleNamespace(change_set_id="KCS-NOT-PRESENT"),
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(read)
            with pytest.raises(FutureTimeoutError):
                future.result(timeout=0.2)
            writer.commit()
            observed = future.result(timeout=10)
    finally:
        writer.close()

    assert observed["review_event_ledger_snapshot"]["count"] == 1
    assert observed["review_event_ledger_count"] == 1
    if reader_kind == "freeze":
        assert observed["database_identity"]["system_identifier"].isdigit()


def test_late_operation_failure_rolls_back_the_whole_repair(
    postgres_store: PostgresKnowledgeStore,
) -> None:
    seeded = _seed_pairs(
        postgres_store,
        pair_count=2,
        reciprocal=False,
        reviewer_kind="human",
    )
    frozen = _freeze(seeded)
    audit, value, plan = _repair_from_frozen(frozen)
    assert audit["counts"]["claim_only"] == 2
    assert len(plan.operations) == 2

    with postgres_store.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """CREATE FUNCTION fail_wkp364_second_operation()
               RETURNS trigger LANGUAGE plpgsql AS $$
               BEGIN
                   IF NEW.change_set_id = TG_ARGV[0]
                      AND NEW.operation_index = 1 THEN
                       RAISE EXCEPTION 'forced integration rollback';
                   END IF;
                   RETURN NEW;
               END
               $$"""
        )
        cursor.execute(
            sql.SQL(
                """CREATE TRIGGER fail_wkp364_second_operation
                   BEFORE INSERT ON wang_knowledge.change_operations
                   FOR EACH ROW EXECUTE FUNCTION
                   fail_wkp364_second_operation({})"""
            ).format(sql.Literal(plan.change_set_id))
        )

    state_before = _database_state(postgres_store)
    with pytest.raises(psycopg.errors.RaiseException, match="forced integration rollback"):
        _apply_repair(postgres_store, value, plan)

    assert _database_state(postgres_store) == state_before
    assert _count(
        postgres_store,
        "SELECT count(*) FROM wang_knowledge.change_sets WHERE change_set_id=%s",
        (plan.change_set_id,),
    ) == 0


def test_apply_precheck_rejects_global_review_ledger_drift(
    postgres_store: PostgresKnowledgeStore,
) -> None:
    seeded = _seed_pairs(
        postgres_store,
        pair_count=1,
        reciprocal=False,
        reviewer_kind="human",
    )
    _, value, plan = _repair_from_frozen(_freeze(seeded))
    with postgres_store.connect() as connection:
        connection.execute(
            """INSERT INTO wang_knowledge.review_events
               (review_event_id, collection, object_id, object_revision,
                reviewer_kind, reviewer_id, decision, reason, artifact)
               VALUES ('REV-WKP364-UNRELATED-DRIFT', 'evidence_steps', 'EV-1', 1,
                       'system', 'drift-test', 'candidate',
                       'unrelated ledger drift', '{}'::jsonb)"""
        )
    state_after_drift = _database_state(postgres_store)

    with pytest.raises(ChangeSetConflict, match="Review-event ledger drifted"):
        _apply_repair(postgres_store, value, plan)

    assert _database_state(postgres_store) == state_after_drift
    assert _count(
        postgres_store,
        "SELECT count(*) FROM wang_knowledge.change_sets WHERE change_set_id=%s",
        (plan.change_set_id,),
    ) == 0


def test_apply_precheck_rejects_referenced_source_lineage_drift(
    postgres_store: PostgresKnowledgeStore,
) -> None:
    seeded = _seed_pairs(
        postgres_store,
        pair_count=1,
        reciprocal=False,
        reviewer_kind="human",
    )
    _, value, plan = _repair_from_frozen(_freeze(seeded))
    with postgres_store.connect() as connection:
        result = connection.execute(
            """UPDATE wang_knowledge.objects
               SET revision=revision + 1
               WHERE collection='source_fragments'
                 AND object_id='FR-WKP364-FIXTURE'"""
        )
        assert result.rowcount == 1
    state_after_drift = _database_state(postgres_store)

    with pytest.raises(ChangeSetConflict, match="source-lineage snapshot drifted"):
        _apply_repair(postgres_store, value, plan)

    assert _database_state(postgres_store) == state_after_drift
    assert _count(
        postgres_store,
        "SELECT count(*) FROM wang_knowledge.change_sets WHERE change_set_id=%s",
        (plan.change_set_id,),
    ) == 0
    assert _count(
        postgres_store,
        "SELECT count(*) FROM wang_knowledge.object_versions WHERE change_set_id=%s",
        (plan.change_set_id,),
    ) == 0
    assert _count(
        postgres_store,
        "SELECT count(*) FROM wang_knowledge.change_operations WHERE change_set_id=%s",
        (plan.change_set_id,),
    ) == 0


def test_silently_skipped_object_version_rolls_back_the_whole_repair(
    postgres_store: PostgresKnowledgeStore,
) -> None:
    seeded = _seed_pairs(
        postgres_store,
        pair_count=1,
        reciprocal=False,
        reviewer_kind="human",
    )
    _, value, plan = _repair_from_frozen(_freeze(seeded))

    with postgres_store.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """CREATE FUNCTION skip_wkp364_object_version()
               RETURNS trigger LANGUAGE plpgsql AS $$
               BEGIN
                   IF NEW.change_set_id = TG_ARGV[0] THEN
                       RETURN NULL;
                   END IF;
                   RETURN NEW;
               END
               $$"""
        )
        cursor.execute(
            sql.SQL(
                """CREATE TRIGGER skip_wkp364_object_version
                   BEFORE INSERT ON wang_knowledge.object_versions
                   FOR EACH ROW EXECUTE FUNCTION
                   skip_wkp364_object_version({})"""
            ).format(sql.Literal(plan.change_set_id))
        )

    state_before = _database_state(postgres_store)
    with pytest.raises(ChangeSetConflict, match="ObjectVersion ledger differs"):
        _apply_repair(postgres_store, value, plan)

    assert _database_state(postgres_store) == state_before
    assert _count(
        postgres_store,
        "SELECT count(*) FROM wang_knowledge.change_sets WHERE change_set_id=%s",
        (plan.change_set_id,),
    ) == 0
    assert _count(
        postgres_store,
        "SELECT count(*) FROM wang_knowledge.object_versions WHERE change_set_id=%s",
        (plan.change_set_id,),
    ) == 0
    assert _count(
        postgres_store,
        "SELECT count(*) FROM wang_knowledge.change_operations WHERE change_set_id=%s",
        (plan.change_set_id,),
    ) == 0


def test_silently_skipped_change_set_status_update_rolls_back_the_whole_repair(
    postgres_store: PostgresKnowledgeStore,
) -> None:
    seeded = _seed_pairs(
        postgres_store,
        pair_count=1,
        reciprocal=False,
        reviewer_kind="human",
    )
    _, value, plan = _repair_from_frozen(_freeze(seeded))

    with postgres_store.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """CREATE FUNCTION skip_wkp364_change_set_status()
               RETURNS trigger LANGUAGE plpgsql AS $$
               BEGIN
                   IF NEW.change_set_id = TG_ARGV[0]
                      AND NEW.status = 'applied' THEN
                       RETURN NULL;
                   END IF;
                   RETURN NEW;
               END
               $$"""
        )
        cursor.execute(
            sql.SQL(
                """CREATE TRIGGER skip_wkp364_change_set_status
                   BEFORE UPDATE ON wang_knowledge.change_sets
                   FOR EACH ROW EXECUTE FUNCTION
                   skip_wkp364_change_set_status({})"""
            ).format(sql.Literal(plan.change_set_id))
        )

    state_before = _database_state(postgres_store)
    with pytest.raises(ChangeSetConflict, match="ChangeSet ledger write is incomplete"):
        _apply_repair(postgres_store, value, plan)

    assert _database_state(postgres_store) == state_before
    assert _count(
        postgres_store,
        "SELECT count(*) FROM wang_knowledge.change_sets WHERE change_set_id=%s",
        (plan.change_set_id,),
    ) == 0


def test_missing_applied_at_rolls_back_the_whole_repair(
    postgres_store: PostgresKnowledgeStore,
) -> None:
    seeded = _seed_pairs(
        postgres_store,
        pair_count=1,
        reciprocal=False,
        reviewer_kind="human",
    )
    _, value, plan = _repair_from_frozen(_freeze(seeded))

    with postgres_store.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """CREATE FUNCTION clear_wkp364_applied_at()
               RETURNS trigger LANGUAGE plpgsql AS $$
               BEGIN
                   IF NEW.change_set_id = TG_ARGV[0]
                      AND NEW.status = 'applied' THEN
                       NEW.applied_at := NULL;
                   END IF;
                   RETURN NEW;
               END
               $$"""
        )
        cursor.execute(
            sql.SQL(
                """CREATE TRIGGER clear_wkp364_applied_at
                   BEFORE UPDATE ON wang_knowledge.change_sets
                   FOR EACH ROW EXECUTE FUNCTION
                   clear_wkp364_applied_at({})"""
            ).format(sql.Literal(plan.change_set_id))
        )

    state_before = _database_state(postgres_store)
    with pytest.raises(ChangeSetConflict, match="ChangeSet ledger write is incomplete"):
        _apply_repair(postgres_store, value, plan)

    assert _database_state(postgres_store) == state_before
    assert _count(
        postgres_store,
        "SELECT count(*) FROM wang_knowledge.change_sets WHERE change_set_id=%s",
        (plan.change_set_id,),
    ) == 0
    assert _count(
        postgres_store,
        "SELECT count(*) FROM wang_knowledge.object_versions WHERE change_set_id=%s",
        (plan.change_set_id,),
    ) == 0
    assert _count(
        postgres_store,
        "SELECT count(*) FROM wang_knowledge.change_operations WHERE change_set_id=%s",
        (plan.change_set_id,),
    ) == 0


def test_successful_retry_is_exact_once_and_fresh_preview_is_zero_op(
    postgres_store: PostgresKnowledgeStore,
) -> None:
    seeded = _seed_pairs(
        postgres_store,
        pair_count=1,
        reciprocal=False,
        reviewer_kind="human",
    )
    evidence_revision_before, _ = _object_row(
        postgres_store, "evidence_steps", "EV-1"
    )
    review_events_before = _count(
        postgres_store, "SELECT count(*) FROM wang_knowledge.review_events"
    )
    audit, value, plan = _repair_from_frozen(_freeze(seeded))
    assert audit["pairs"][0]["mismatch_type"] == MISMATCH_CLAIM_ONLY
    assert len(plan.operations) == 1

    assert _apply_repair(postgres_store, value, plan)["status"] == "applied"
    state_after_first_apply = _database_state(postgres_store)
    retry = _apply_repair(postgres_store, value, plan)
    assert retry["status"] == "already_applied"
    assert _database_state(postgres_store) == state_after_first_apply

    evidence_revision, evidence_payload = _object_row(
        postgres_store, "evidence_steps", "EV-1"
    )
    assert evidence_revision == evidence_revision_before + 1
    assert evidence_payload["produced_claim_ids"] == ["CL-1"]
    assert _count(
        postgres_store,
        "SELECT count(*) FROM wang_knowledge.object_versions "
        "WHERE collection='evidence_steps' AND object_id='EV-1'",
    ) == 2
    assert _count(
        postgres_store, "SELECT count(*) FROM wang_knowledge.review_events"
    ) == review_events_before

    fresh_audit, fresh_value, fresh_plan = _repair_from_frozen(_freeze(seeded))
    assert fresh_audit["status"] == "clean"
    assert fresh_audit["counts"]["claim_only"] == 0
    assert fresh_audit["counts"]["evidence_only"] == 0
    assert fresh_plan.operations == ()
    assert _apply_repair(postgres_store, fresh_value, fresh_plan) == {
        "status": "unchanged",
        "change_set_id": None,
        "summary": fresh_plan.as_dict()["summary"],
    }
    assert _database_state(postgres_store) == state_after_first_apply


def test_official_apply_with_real_dump_seals_readback_and_fresh_zero_plan(
    postgres_store: PostgresKnowledgeStore,
    tmp_path: Path,
) -> None:
    pg_dump = shutil.which("pg_dump")
    pg_restore = shutil.which("pg_restore")
    if not pg_dump or not pg_restore:
        pytest.skip("official apply integration requires pg_dump and pg_restore")

    seeded = _seed_pairs(
        postgres_store,
        pair_count=1,
        reciprocal=False,
        reviewer_kind="human",
    )
    _, value, plan = _repair_from_frozen(_freeze(seeded))
    backup_dump = tmp_path / "wkp364-pre-apply.dump"
    _run_postgres(
        [
            pg_dump,
            "--format=custom",
            f"--file={backup_dump}",
            f"--dbname={postgres_store.database_url}",
        ],
        purpose="pg_dump",
    )

    result = apply_sealed_plan(
        value,
        store=postgres_store,
        backup_dump=backup_dump,
    )
    validated = validate_sealed_artifact(
        result,
        expected_schema_version=RESULT_SCHEMA_VERSION,
    )

    assert validated["apply_result"]["status"] == "applied"
    assert validated["change_set_id"] == plan.change_set_id
    assert validated["applied_change_set"]["change_set_id"] == plan.change_set_id
    assert len(validated["applied_operations"]) == len(plan.operations) == 1
    assert validated["fresh_plan_operations"] == 0
    expected_counts = {
        "active_claims": 1,
        "active_evidence_steps": 1,
        "claim_evidence_pairs": 1,
        "evidence_claim_pairs": 1,
        "reciprocal_pairs": 1,
        "claim_only_pairs": 0,
        "evidence_only_pairs": 0,
        "dangling_endpoints": 0,
        "duplicate_array_references": 0,
    }
    counts = validated["post_apply_active_snapshot"]["counts"]
    assert {key: counts[key] for key in expected_counts} == expected_counts
    assert validated["backup"]["path"] == str(backup_dump.resolve())
    assert validated["backup"]["contains_wang_knowledge_schema"] is True
    assert (
        validated["backup"]["archive_database_name"]
        == value["freeze_binding"]["database_identity"]["database_name"]
    )
    assert validated["post_apply_review_event_ledger_snapshot"] == value[
        "review_event_ledger_snapshot"
    ]


@pytest.mark.parametrize(
    "dump_scope",
    ["schema_only", "missing_object_versions_data"],
)
def test_real_backup_verifier_rejects_schema_only_and_partial_dumps(
    postgres_store: PostgresKnowledgeStore,
    tmp_path: Path,
    dump_scope: str,
) -> None:
    pg_dump = shutil.which("pg_dump")
    pg_restore = shutil.which("pg_restore")
    if not pg_dump or not pg_restore:
        pytest.skip("backup coverage integration requires pg_dump and pg_restore")

    seeded = _seed_pairs(
        postgres_store,
        pair_count=1,
        reciprocal=False,
        reviewer_kind="human",
    )
    frozen = _freeze(seeded)
    _, sealed_plan, _ = _repair_from_frozen(frozen)
    backup_dump = tmp_path / f"wkp364-{dump_scope}.dump"
    command = [
        pg_dump,
        "--format=custom",
        f"--file={backup_dump}",
        f"--dbname={postgres_store.database_url}",
    ]
    if dump_scope == "schema_only":
        command.append("--schema-only")
    else:
        command.append("--exclude-table-data=wang_knowledge.object_versions")
    _run_postgres(command, purpose=f"pg_dump {dump_scope}")

    with pytest.raises(
        ValueError, match="lacks required wang_knowledge table"
    ):
        verify_postgres_backup_dump(
            backup_dump,
            freeze_binding=sealed_plan["freeze_binding"],
        )


def test_post_commit_result_failure_leaves_sealed_recovery_receipt(
    postgres_store: PostgresKnowledgeStore,
    tmp_path: Path,
) -> None:
    pg_dump = shutil.which("pg_dump")
    pg_restore = shutil.which("pg_restore")
    if not pg_dump or not pg_restore:
        pytest.skip("recovery receipt integration requires pg_dump and pg_restore")

    seeded = _seed_pairs(
        postgres_store,
        pair_count=1,
        reciprocal=False,
        reviewer_kind="human",
    )
    _, value, plan = _repair_from_frozen(_freeze(seeded))
    backup_dump = tmp_path / "wkp364-recovery-pre-apply.dump"
    _run_postgres(
        [
            pg_dump,
            "--format=custom",
            f"--file={backup_dump}",
            f"--dbname={postgres_store.database_url}",
        ],
        purpose="pg_dump",
    )
    receipt_path = tmp_path / "wkp364-committed-receipt.json"

    def fail_readback(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("forced result artifact failure")

    with pytest.raises(RuntimeError, match="forced result artifact failure"):
        apply_sealed_plan(
            value,
            store=postgres_store,
            backup_dump=backup_dump,
            result_builder=fail_readback,
            committed_receipt_path=receipt_path,
        )

    receipt = validate_sealed_artifact(
        json.loads(receipt_path.read_text(encoding="utf-8")),
        expected_schema_version=COMMITTED_RECEIPT_SCHEMA_VERSION,
    )
    assert receipt["apply_result"]["status"] == "applied"
    assert receipt["change_set_id"] == plan.change_set_id
    assert _count(
        postgres_store,
        """SELECT count(*) FROM wang_knowledge.change_sets
           WHERE change_set_id=%s AND status='applied'""",
        (plan.change_set_id,),
    ) == 1
    _, evidence = _object_row(postgres_store, "evidence_steps", "EV-1")
    assert evidence["produced_claim_ids"] == ["CL-1"]


@pytest.mark.parametrize(
    ("tamper", "expected_message"),
    [
        ("reviewer_kind", "lacks one current human Claim authority"),
        ("artifact", "human event is not bound to the current ObjectVersion producer"),
    ],
)
def test_retry_rejects_same_count_tampered_human_authority_event(
    postgres_store: PostgresKnowledgeStore,
    tamper: str,
    expected_message: str,
) -> None:
    seeded = _seed_pairs(
        postgres_store,
        pair_count=1,
        reciprocal=False,
        reviewer_kind="human",
    )
    _, value, plan = _repair_from_frozen(_freeze(seeded))
    assert _apply_repair(postgres_store, value, plan)["status"] == "applied"
    event_count = _count(
        postgres_store, "SELECT count(*) FROM wang_knowledge.review_events"
    )

    with postgres_store.connect() as connection, connection.cursor() as cursor:
        if tamper == "reviewer_kind":
            cursor.execute(
                """UPDATE wang_knowledge.review_events
                   SET reviewer_kind='system'
                   WHERE collection='claims' AND object_id='CL-1'"""
            )
        else:
            cursor.execute(
                """UPDATE wang_knowledge.review_events
                   SET artifact=%s::jsonb
                   WHERE collection='claims' AND object_id='CL-1'""",
                ('{"change_set_id":"KCS-WRONG-WKP364"}',),
            )
        assert cursor.rowcount == 1

    assert _count(
        postgres_store, "SELECT count(*) FROM wang_knowledge.review_events"
    ) == event_count
    state_after_tamper = _database_state(postgres_store)
    with pytest.raises(ChangeSetConflict, match=expected_message):
        _apply_repair(postgres_store, value, plan)

    assert _database_state(postgres_store) == state_after_tamper


def test_retry_rejects_different_sealed_backup_metadata(
    postgres_store: PostgresKnowledgeStore,
) -> None:
    seeded = _seed_pairs(
        postgres_store,
        pair_count=1,
        reciprocal=False,
        reviewer_kind="human",
    )
    _, value, plan = _repair_from_frozen(_freeze(seeded))
    original_backup = _backup_verification(
        freeze_binding=value["freeze_binding"]
    )
    assert _apply_repair(
        postgres_store,
        value,
        plan,
        backup=original_backup,
    )["status"] == "applied"
    state_after_apply = _database_state(postgres_store)
    different_backup = _backup_verification(
        path="/tmp/wkp364-integration-different.dump",
        sha_character="c",
        freeze_binding=value["freeze_binding"],
    )

    with pytest.raises(
        ChangeSetConflict,
        match="metadata or backup differs from this retry",
    ):
        _apply_repair(
            postgres_store,
            value,
            plan,
            backup=different_backup,
        )

    assert _database_state(postgres_store) == state_after_apply


def test_stale_zero_operation_preview_is_rejected_by_the_full_snapshot_guard(
    postgres_store: PostgresKnowledgeStore,
) -> None:
    seeded = _seed_pairs(
        postgres_store,
        pair_count=1,
        reciprocal=True,
    )
    clean_audit, clean_value, zero_plan = _repair_from_frozen(_freeze(seeded))
    assert clean_audit["status"] == "clean"
    assert zero_plan.operations == ()

    drift_plan = postgres_store.plan_package(
        {
            "schema_version": "wang_shared_knowledge_v1.3",
            "package_id": "WKP364-INTEGRATION-DRIFT",
            "evidence_steps": [
                {
                    "evidence_step_id": "EV-1",
                    "produced_claim_ids": [],
                }
            ],
        },
        source_kind="integration_fixture_drift",
    )
    assert len(drift_plan.operations) == 1
    assert postgres_store.apply_plan(drift_plan)["status"] == "applied"
    state_after_drift = _database_state(postgres_store)

    with pytest.raises(ChangeSetConflict, match="drift after preview"):
        _apply_repair(postgres_store, clean_value, zero_plan)

    assert _database_state(postgres_store) == state_after_drift
    assert _count(
        postgres_store,
        "SELECT count(*) FROM wang_knowledge.change_sets WHERE change_set_id=%s",
        (zero_plan.change_set_id,),
    ) == 0


def test_forged_human_input_is_rejected_against_the_review_ledger(
    postgres_store: PostgresKnowledgeStore,
) -> None:
    seeded = _seed_pairs(
        postgres_store,
        pair_count=1,
        reciprocal=False,
        reviewer_kind="system",
    )
    frozen = _freeze(seeded)
    claim = next(
        row
        for row in frozen["active_records"]
        if row["collection"] == "claims" and row["object_id"] == "CL-1"
    )
    assert claim["review_events"][0]["reviewer_kind"] == "system"

    forged_records = deepcopy(frozen["active_records"])
    forged_claim = next(
        row
        for row in forged_records
        if row["collection"] == "claims" and row["object_id"] == "CL-1"
    )
    forged_claim["review_events"] = [
        {
            **forged_claim["review_events"][0],
            "review_event_id": "REV-FORGED-HUMAN-WKP364",
            "reviewer_kind": "human",
            "reviewer_id": "forged-editor",
        }
    ]
    forged_audit = build_reciprocity_audit(
        forged_records,
        prerequisites=frozen["prerequisites"],
        review_event_ledger_snapshot=frozen["review_event_ledger_snapshot"],
        freeze_binding=_build_freeze_binding(
            frozen_input_artifact_sha256=frozen["artifact_sha256"],
            frozen_at=frozen["frozen_at"],
            database_identity=frozen["database_identity"],
        ),
        source_lineage_identity_snapshot=frozen[
            "source_lineage_identity_snapshot"
        ],
    )
    assert forged_audit["pairs"][0]["mismatch_type"] == MISMATCH_CLAIM_ONLY
    forged_artifact = build_repair_plan(
        forged_audit,
        forged_records,
        product_dependencies=frozen["product_dependencies"],
        product_dependency_records=frozen["product_dependency_records"],
    )
    forged_value, forged_plan = deserialize_repair_plan(forged_artifact)
    assert len(forged_plan.operations) == 1
    state_before = _database_state(postgres_store)

    with pytest.raises(ChangeSetConflict, match="lacks one current human Claim authority"):
        _apply_repair(postgres_store, forged_value, forged_plan)

    assert _database_state(postgres_store) == state_before
    assert _count(
        postgres_store,
        "SELECT count(*) FROM wang_knowledge.change_sets WHERE change_set_id=%s",
        (forged_plan.change_set_id,),
    ) == 0


def test_pair_adjudication_apply_is_atomic_clean_and_exactly_once(
    postgres_store: PostgresKnowledgeStore,
    tmp_path: Path,
) -> None:
    seeded = _seed_pairs(
        postgres_store,
        pair_count=1,
        reciprocal=True,
    )
    _, drifted_claim = _object_row(postgres_store, "claims", "CL-1")
    drifted_claim["evidence_step_ids"] = []
    drift = postgres_store.plan_package(
        {
            "schema_version": "wang_shared_knowledge_v1.3",
            "package_id": "WKP364-PAIR-ADJUDICATION-DRIFT",
            "claims": [drifted_claim],
        },
        source_kind="integration_fixture_drift",
    )
    assert postgres_store.apply_plan(drift)["status"] == "applied"
    frozen = _freeze(seeded)
    audit = build_reciprocity_audit(
        frozen["active_records"],
        prerequisites=frozen["prerequisites"],
        authority_records=frozen["authority_records"],
        source_lineage_findings=frozen["source_lineage_findings"],
        review_event_ledger_count=frozen["review_event_ledger_count"],
        review_event_ledger_snapshot=frozen["review_event_ledger_snapshot"],
        freeze_binding=_build_freeze_binding(
            frozen_input_artifact_sha256=frozen["artifact_sha256"],
            frozen_at=frozen["frozen_at"],
            database_identity=frozen["database_identity"],
        ),
        source_lineage_identity_snapshot=frozen[
            "source_lineage_identity_snapshot"
        ],
    )
    assert audit["counts"]["evidence_only"] == 1
    packets = build_relation_packets(
        audit_artifact=audit,
        frozen_input=frozen,
        source_records=read_packet_source_records(
            postgres_store, required=packet_source_keys(audit)
        ),
    )
    final = seal_artifact(
        {
            "schema_version": FINAL_DECISIONS_SCHEMA_VERSION,
            "packet_artifact_sha256": packets["artifact_sha256"],
            "counts": {"include": 1, "exclude": 0, "needs_human": 0},
            "decisions": [
                {
                    "pair_id": packets["pair_ids"][0],
                    "decision": "include",
                    "reason_code": "independent_model_consensus",
                }
            ],
        }
    )
    preview = build_pair_repair_plan(
        audit_artifact=audit,
        frozen_input=frozen,
        packet_artifact=packets,
        final_decisions=final,
    )
    pg_dump = shutil.which("pg_dump")
    if not pg_dump or not shutil.which("pg_restore"):
        pytest.skip("pair repair integration requires pg_dump and pg_restore")
    backup_dump = tmp_path / "wkp364-pair-repair.dump"
    _run_postgres(
        [
            pg_dump,
            "--format=custom",
            f"--file={backup_dump}",
            f"--dbname={postgres_store.database_url}",
        ],
        purpose="pg_dump",
    )
    receipt_path = tmp_path / "committed-receipt.json"
    applied = apply_pair_repair_plan(
        preview,
        audit_artifact=audit,
        frozen_input=frozen,
        packet_artifact=packets,
        final_decisions=final,
        prerequisites_manifest=seeded.prerequisites_manifest,
        backup_dump=backup_dump,
        store=postgres_store,
        committed_receipt_path=receipt_path,
    )
    assert applied["status"] == "verified"
    assert applied["apply_result"]["status"] == "applied"
    assert applied["fresh_counts"]["claim_only_pairs"] == 0
    assert applied["fresh_counts"]["evidence_only_pairs"] == 0
    assert applied["fresh_preview_operations"] == 0
    assert receipt_path.is_file()
    first_state = _database_state(postgres_store)
    retry = apply_pair_repair_plan(
        preview,
        audit_artifact=audit,
        frozen_input=frozen,
        packet_artifact=packets,
        final_decisions=final,
        prerequisites_manifest=seeded.prerequisites_manifest,
        backup_dump=backup_dump,
        store=postgres_store,
        committed_receipt_path=receipt_path,
    )
    assert retry["apply_result"]["status"] == "already_applied"
    assert _database_state(postgres_store) == first_state
    claim_revision, claim_payload = _object_row(postgres_store, "claims", "CL-1")
    assert claim_revision == 3
    assert claim_payload["evidence_step_ids"] == ["EV-1"]
