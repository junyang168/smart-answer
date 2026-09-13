from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import backend.pipeline.claim_evidence_reciprocity_repair as repair
from backend.api.canonical_repository.postgres_store import (
    CLAIM_EVIDENCE_FREEZE_BINDING_SCHEMA_VERSION,
    ChangeSetPlan,
    sha256_json,
)


def _freeze_binding(
    *,
    frozen_at: str = "2026-09-13T16:59:59.900000+00:00",
    database_name: str = "wkp364_test",
) -> dict[str, Any]:
    value = {
        "schema_version": CLAIM_EVIDENCE_FREEZE_BINDING_SCHEMA_VERSION,
        "frozen_input_artifact_sha256": "a" * 64,
        "frozen_at": frozen_at,
        "database_identity": {
            "database_name": database_name,
            "server_version_num": "140017",
        },
    }
    value["binding_sha256"] = sha256_json(value)
    return value


class _Cursor:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False

    def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> None:
        self.statements.append(" ".join(sql.split()))


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor
        self.events: list[str] = []

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False

    def cursor(self) -> _Cursor:
        return self._cursor

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")


def test_locked_reader_acquires_session_lock_before_repeatable_read_snapshot() -> None:
    cursor = _Cursor()
    connection = _Connection(cursor)
    store = SimpleNamespace(connect=lambda: connection)

    with repair._locked_repeatable_read_cursor(store) as locked:
        locked.execute("SELECT semantic_snapshot")

    assert "pg_advisory_lock" in cursor.statements[0]
    assert "pg_advisory_xact_lock" not in cursor.statements[0]
    assert cursor.statements[1] == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    )
    assert cursor.statements[2] == "SELECT semantic_snapshot"
    assert "pg_advisory_unlock" in cursor.statements[3]
    assert connection.events == ["commit", "commit", "commit"]


def _toc(
    *,
    created_at: str,
    database: str,
    include_schema: bool = True,
    include_table_data: bool = True,
    missing_table: str | None = None,
) -> str:
    rows = [
        ";",
        f"; Archive created at {created_at}",
        f";     dbname: {database}",
        "; Selected TOC Entries:",
    ]
    if include_schema:
        rows.append("5; 2615 12345 SCHEMA - wang_knowledge postgres")
    for index, table_name in enumerate(
        repair.CLAIM_EVIDENCE_BACKUP_REQUIRED_TABLES, start=6
    ):
        if table_name == missing_table:
            continue
        rows.append(
            f"{index}; 1259 {12340 + index} TABLE wang_knowledge "
            f"{table_name} postgres"
        )
        if include_table_data:
            rows.append(
                f"{index + 100}; 0 {12340 + index} TABLE DATA wang_knowledge "
                f"{table_name} postgres"
            )
    return "\n".join(rows) + "\n"


def _run_for(listing: str):
    def run(_command: list[str], **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=listing, stderr="")

    return run


def test_backup_verifier_binds_toc_time_schema_and_database(
    tmp_path: Path,
) -> None:
    dump = tmp_path / "repository.dump"
    dump.write_bytes(b"archive")
    binding = _freeze_binding()

    verified = repair.verify_postgres_backup_dump(
        dump,
        freeze_binding=binding,
        run=_run_for(
            _toc(
                created_at="2026-09-13 12:00:00 CDT",
                database="wkp364_test",
            )
        ),
    )

    assert verified["archive_created_at"] == "2026-09-13T17:00:00+00:00"
    assert verified["frozen_input_artifact_sha256"] == "a" * 64
    assert verified["contains_wang_knowledge_schema"] is True
    assert verified["contains_required_table_data"] is True
    assert all(
        row["has_table_definition"] and row["has_table_data"]
        for row in verified["required_table_coverage"]
    )
    assert verified["archive_database_name"] == "wkp364_test"


@pytest.mark.parametrize(
    ("created_at", "database", "include_schema", "message"),
    [
        (
            "2026-09-13 11:59:58 CDT",
            "wkp364_test",
            True,
            "predates",
        ),
        (
            "2026-09-13 12:00:00 CDT",
            "another_database",
            True,
            "database does not match",
        ),
        (
            "2026-09-13 12:00:00 CDT",
            "wkp364_test",
            False,
            "does not contain",
        ),
    ],
)
def test_backup_verifier_rejects_unbound_toc(
    tmp_path: Path,
    created_at: str,
    database: str,
    include_schema: bool,
    message: str,
) -> None:
    dump = tmp_path / "repository.dump"
    dump.write_bytes(b"archive")

    with pytest.raises(repair.ClaimEvidenceReciprocityRepairError, match=message):
        repair.verify_postgres_backup_dump(
            dump,
            freeze_binding=_freeze_binding(),
            run=_run_for(
                _toc(
                    created_at=created_at,
                    database=database,
                    include_schema=include_schema,
                )
            ),
        )


@pytest.mark.parametrize(
    ("include_table_data", "missing_table"),
    [(False, None), (True, "object_versions")],
)
def test_backup_verifier_rejects_schema_only_or_partial_archives(
    tmp_path: Path,
    include_table_data: bool,
    missing_table: str | None,
) -> None:
    dump = tmp_path / "partial.dump"
    dump.write_bytes(b"archive")

    with pytest.raises(
        repair.ClaimEvidenceReciprocityRepairError,
        match="lacks required wang_knowledge table",
    ):
        repair.verify_postgres_backup_dump(
            dump,
            freeze_binding=_freeze_binding(),
            run=_run_for(
                _toc(
                    created_at="2026-09-13 12:00:00 CDT",
                    database="wkp364_test",
                    include_table_data=include_table_data,
                    missing_table=missing_table,
                )
            ),
        )


def test_apply_persists_committed_receipt_before_result_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _freeze_binding()
    fingerprint = "b" * 64
    change_set = ChangeSetPlan(
        change_set_id="KCS-WKP364-RECEIPT",
        fingerprint_sha256=fingerprint,
        package_id="WKP364-RECEIPT",
        source_kind="wkp364_claim_evidence_reciprocity_repair",
        source_sha256="c" * 64,
        operations=(),
        unchanged=2,
        ignored_keys=(),
    )
    plan_artifact = {
        "artifact_sha256": "d" * 64,
        "audit_artifact_sha256": "e" * 64,
        "apply_allowed": True,
        "freeze_binding": binding,
        "store_guard": {},
        "action_manifest_sha256": "f" * 64,
        "operation_manifest_sha256": sha256_json([]),
    }
    monkeypatch.setattr(
        repair,
        "deserialize_repair_plan",
        lambda _artifact: (plan_artifact, change_set),
    )

    required_table_coverage = [
        {
            "table_name": table_name,
            "has_table_definition": True,
            "has_table_data": True,
        }
        for table_name in repair.CLAIM_EVIDENCE_BACKUP_REQUIRED_TABLES
    ]
    backup = repair.seal_artifact(
        {
            "schema_version": repair.BACKUP_VERIFICATION_SCHEMA_VERSION,
            "path": str(tmp_path / "repository.dump"),
            "sha256": "1" * 64,
            "size_bytes": 1,
            "pg_restore_list_sha256": "2" * 64,
            "pg_restore_entry_count": 1,
            "archive_created_at": "2026-09-13T17:00:00+00:00",
            "archive_database_name": "wkp364_test",
            "contains_wang_knowledge_schema": True,
            "contains_required_table_data": True,
            "required_table_coverage": required_table_coverage,
            "required_table_coverage_sha256": repair.sha256_json(
                required_table_coverage
            ),
            "timestamp_precision": "second",
            "frozen_input_artifact_sha256": "a" * 64,
            "frozen_at": binding["frozen_at"],
            "database_identity": binding["database_identity"],
        }
    )

    class Store:
        def apply_plan(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"status": "unchanged", "change_set_id": None}

    receipt_path = tmp_path / "committed-receipt.json"

    def fail_result(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("post-commit readback failed")

    with pytest.raises(RuntimeError, match="post-commit readback failed"):
        repair.apply_sealed_plan(
            {},
            store=Store(),
            backup_dump=tmp_path / "repository.dump",
            backup_verifier=lambda _path, **_kwargs: backup,
            result_builder=fail_result,
            committed_receipt_path=receipt_path,
        )

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    validated = repair.validate_sealed_artifact(
        receipt,
        expected_schema_version=repair.COMMITTED_RECEIPT_SCHEMA_VERSION,
    )
    assert validated["apply_result"]["status"] == "unchanged"
    assert validated["plan_artifact_sha256"] == "d" * 64
    assert validated["status"].endswith("verification_pending")
