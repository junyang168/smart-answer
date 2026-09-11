"""Revalidate SHA-bound source lineage closures without model calls or writes.

The reconciliation artifact selects a successful canonical lineage from an
append-only attempt history.  This validator distrusts the artifact's earlier
verdict: it reopens every named source and evidence artifact, rereads the run
ledger and ChangeSet rows, and verifies the object-version denominator again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


EXPECTED_STAGES = (
    "extraction",
    "cross_section",
    "review",
    "adjudication",
    "merge",
    "ingest",
)


class ReconciliationValidationError(ValueError):
    """The reconciliation document cannot be validated as requested."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_database_evidence(
    connection: Any,
    *,
    run_ids: Iterable[str],
    change_set_ids: Iterable[str],
    object_keys: Iterable[tuple[str, str]],
) -> dict[str, Any]:
    """Read all mutable authority state in one read-only transaction."""

    run_ids = sorted(set(run_ids))
    change_set_ids = sorted(set(change_set_ids))
    object_keys = sorted(set(object_keys))
    runs: dict[str, Any] = {}
    change_sets: dict[str, Any] = {}
    readbacks: dict[str, Any] = {}
    objects: dict[str, Any] = {}
    with connection.cursor() as cursor:
        if run_ids:
            cursor.execute(
                """SELECT run_id, stage, status, input_sha256, output_paths, metadata
                   FROM wang_knowledge.pipeline_runs WHERE run_id = ANY(%s)""",
                (run_ids,),
            )
            for row in cursor.fetchall():
                runs[str(row[0])] = {
                    "stage": str(row[1]),
                    "status": str(row[2]),
                    "input_sha256": dict(row[3] or {}),
                    "output_paths": list(row[4] or []),
                    "metadata": dict(row[5] or {}),
                }
        if change_set_ids:
            cursor.execute(
                """SELECT change_set_id, fingerprint_sha256, package_id, source_sha256,
                          status, summary, metadata
                   FROM wang_knowledge.change_sets WHERE change_set_id = ANY(%s)""",
                (change_set_ids,),
            )
            for row in cursor.fetchall():
                change_sets[str(row[0])] = {
                    "fingerprint_sha256": str(row[1]),
                    "package_id": str(row[2]),
                    "source_sha256": str(row[3]),
                    "status": str(row[4]),
                    "summary": dict(row[5] or {}),
                    "metadata": dict(row[6] or {}),
                }
            cursor.execute(
                """SELECT co.change_set_id, count(*),
                          count(*) FILTER (WHERE ov.change_set_id = co.change_set_id),
                          count(*) FILTER (WHERE ov.content_sha256 = co.after_sha256)
                   FROM wang_knowledge.change_operations co
                   LEFT JOIN wang_knowledge.object_versions ov
                     ON ov.collection = co.collection
                    AND ov.object_id = co.object_id
                    AND ov.revision = co.after_revision
                   WHERE co.change_set_id = ANY(%s)
                   GROUP BY co.change_set_id""",
                (change_set_ids,),
            )
            for row in cursor.fetchall():
                readbacks[str(row[0])] = {
                    "operation_count": int(row[1]),
                    "change_set_id_match_count": int(row[2]),
                    "after_sha256_match_count": int(row[3]),
                }
        for collection, object_id in object_keys:
            cursor.execute(
                """SELECT revision, content_sha256, retired_at
                   FROM wang_knowledge.objects
                   WHERE collection = %s AND object_id = %s""",
                (collection, object_id),
            )
            row = cursor.fetchone()
            if row:
                objects[f"{collection}/{object_id}"] = {
                    "revision": int(row[0]),
                    "content_sha256": str(row[1]),
                    "active": row[2] is None,
                }
    return {
        "runs": runs,
        "change_sets": change_sets,
        "readbacks": readbacks,
        "objects": objects,
    }


class _Checks:
    def __init__(self) -> None:
        self.total = 0
        self.failures: list[dict[str, Any]] = []

    def equal(self, name: str, actual: Any, expected: Any) -> None:
        self.total += 1
        if actual != expected:
            self.failures.append({"check": name, "actual": actual, "expected": expected})

    def file_sha(self, name: str, path_value: Any, expected: Any) -> None:
        path = Path(str(path_value or ""))
        self.equal(f"{name}.exists", path.is_file(), True)
        if path.is_file():
            self.equal(f"{name}.sha256", sha256_file(path), expected)


def _validate_closure(closure: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    source_id = str(closure.get("source_id") or "")
    checks = _Checks()
    checks.equal("disposition", closure.get("disposition"), "lineage_closed")
    checks.equal("reason_code_present", bool(closure.get("reason_code")), True)
    checks.file_sha(
        "current_source",
        closure.get("current_source_path"),
        closure.get("current_source_sha256"),
    )

    lineage = closure.get("selected_canonical_lineage") or []
    checks.equal("canonical_stage_sequence", [row.get("stage") for row in lineage], list(EXPECTED_STAGES))
    artifacts: dict[str, str] = {}
    ingest_row: dict[str, Any] | None = None
    for row in lineage:
        stage = str(row.get("stage") or "")
        run_id = str(row.get("run_id") or "")
        run = evidence["runs"].get(run_id)
        checks.equal(f"{stage}.run_present", run is not None, True)
        if run:
            checks.equal(f"{stage}.run_stage", run["stage"], stage)
            checks.equal(f"{stage}.run_status", run["status"], row.get("status"))
            for key, expected in (row.get("input_sha256") or {}).items():
                checks.equal(f"{stage}.input_sha256.{key}", run["input_sha256"].get(key), expected)
        artifact_path = row.get("evidence_artifact_path")
        if artifact_path:
            checks.file_sha(f"{stage}.artifact", artifact_path, row.get("evidence_artifact_sha256"))
            path = Path(str(artifact_path))
            if path.is_file():
                artifacts[stage] = sha256_file(path)
        if stage == "ingest":
            ingest_row = row

    if "cross_section" in artifacts:
        review_inputs = next(
            (row.get("input_sha256") or {} for row in lineage if row.get("stage") == "review"),
            {},
        )
        checks.equal(
            "continuity.cross_section_to_review",
            review_inputs.get("package_sha256"),
            artifacts["cross_section"],
        )

    if ingest_row:
        expected = ingest_row.get("change_set") or {}
        change_set_id = str(expected.get("change_set_id") or "")
        change_set = evidence["change_sets"].get(change_set_id)
        checks.equal("ingest.change_set_present", change_set is not None, True)
        if change_set:
            for key in ("fingerprint_sha256", "package_id", "status", "summary"):
                checks.equal(f"ingest.change_set.{key}", change_set.get(key), expected.get(key))
            checks.equal(
                "ingest.change_set.source_sha256",
                change_set.get("source_sha256"),
                expected.get("reviewed_package_canonical_sha256"),
            )
        if "merge" in artifacts:
            checks.equal(
                "continuity.merge_to_ingest",
                expected.get("reviewed_package_byte_sha256"),
                artifacts["merge"],
            )
        readback = evidence["readbacks"].get(change_set_id)
        checks.equal("ingest.readback_present", readback is not None, True)
        if readback:
            for key in (
                "operation_count",
                "change_set_id_match_count",
                "after_sha256_match_count",
            ):
                checks.equal(f"ingest.{key}", readback.get(key), expected.get(key))
            checks.equal(
                "ingest.object_version_readback_count",
                readback.get("operation_count"),
                expected.get("object_version_readback_count"),
            )

    obsolete = closure.get("obsolete_object_state")
    if obsolete:
        key = f"{obsolete.get('collection')}/{obsolete.get('object_id')}"
        held = evidence["objects"].get(key)
        checks.equal("obsolete_object.present", held is not None, True)
        if held:
            for field in ("revision", "content_sha256", "active"):
                checks.equal(f"obsolete_object.{field}", held.get(field), obsolete.get(field))

    for row in closure.get("rejected_attempts") or []:
        run_id = str(row.get("run_id") or "")
        run = evidence["runs"].get(run_id)
        checks.equal(f"rejected_attempt.{run_id}.present", run is not None, True)
        if run:
            checks.equal(f"rejected_attempt.{run_id}.status", run["status"], row.get("status"))
            checks.equal(f"rejected_attempt.{run_id}.output_count", len(run["output_paths"]), 0)
            checks.equal(
                f"rejected_attempt.{run_id}.change_set_absent",
                "change_set_id" not in run["metadata"],
                True,
            )

    duplicate = closure.get("later_duplicate_extraction")
    if duplicate:
        run_id = str(duplicate.get("run_id") or "")
        run = evidence["runs"].get(run_id)
        checks.equal("later_duplicate.present", run is not None, True)
        if run:
            checks.equal("later_duplicate.status", run["status"], duplicate.get("status"))
            checks.equal("later_duplicate.output_count", len(run["output_paths"]), 0)
            for key, expected in (duplicate.get("input_sha256") or {}).items():
                checks.equal(f"later_duplicate.input_sha256.{key}", run["input_sha256"].get(key), expected)

    incident = closure.get("incident_recovery_receipt")
    if incident:
        checks.file_sha("incident_recovery_receipt", incident.get("path"), incident.get("sha256"))

    return {
        "source_id": source_id,
        "disposition": closure.get("disposition"),
        "reason_code": closure.get("reason_code"),
        "checks": checks.total,
        "passed": checks.total - len(checks.failures),
        "failures": checks.failures,
        "status": "passed" if not checks.failures else "failed",
    }


def validate_reconciliation(
    reconciliation: dict[str, Any],
    evidence: dict[str, Any],
    *,
    source_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    if reconciliation.get("schema_version") != "source-state-reconciliation.v1":
        raise ReconciliationValidationError("unsupported reconciliation schema_version")
    closures = reconciliation.get("closures")
    if not isinstance(closures, list):
        raise ReconciliationValidationError("reconciliation closures must be a list")
    by_source = {str(row.get("source_id") or ""): row for row in closures}
    if len(by_source) != len(closures):
        raise ReconciliationValidationError("reconciliation closure source IDs are not unique")
    selected = list(source_ids or by_source)
    missing = sorted(set(selected).difference(by_source))
    if missing:
        raise ReconciliationValidationError("closure sources not found: " + ", ".join(missing))
    return [_validate_closure(by_source[source_id], evidence) for source_id in selected]


def evidence_keys(closures: Iterable[dict[str, Any]]) -> tuple[set[str], set[str], set[tuple[str, str]]]:
    run_ids: set[str] = set()
    change_set_ids: set[str] = set()
    object_keys: set[tuple[str, str]] = set()
    for closure in closures:
        for row in closure.get("selected_canonical_lineage") or []:
            if row.get("run_id"):
                run_ids.add(str(row["run_id"]))
            if row.get("stage") == "ingest" and (row.get("change_set") or {}).get("change_set_id"):
                change_set_ids.add(str(row["change_set"]["change_set_id"]))
        for row in closure.get("rejected_attempts") or []:
            if row.get("run_id"):
                run_ids.add(str(row["run_id"]))
        duplicate = closure.get("later_duplicate_extraction") or {}
        if duplicate.get("run_id"):
            run_ids.add(str(duplicate["run_id"]))
        obsolete = closure.get("obsolete_object_state") or {}
        if obsolete.get("collection") and obsolete.get("object_id"):
            object_keys.add((str(obsolete["collection"]), str(obsolete["object_id"])))
    return run_ids, change_set_ids, object_keys


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reconciliation", type=Path)
    parser.add_argument("--source-id", action="append", dest="source_ids")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    raw = args.reconciliation.read_bytes()
    reconciliation = json.loads(raw)
    closures = reconciliation.get("closures") or []
    selected = set(args.source_ids or [str(row.get("source_id") or "") for row in closures])
    selected_closures = [row for row in closures if str(row.get("source_id") or "") in selected]
    missing = sorted(selected.difference(str(row.get("source_id") or "") for row in selected_closures))
    if missing:
        parser.error("closure sources not found: " + ", ".join(missing))
    database_url = os.getenv("KNOWLEDGE_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("KNOWLEDGE_DATABASE_URL is required")

    import psycopg

    run_ids, change_set_ids, object_keys = evidence_keys(selected_closures)
    with psycopg.connect(database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        evidence = load_database_evidence(
            connection,
            run_ids=run_ids,
            change_set_ids=change_set_ids,
            object_keys=object_keys,
        )
    results = validate_reconciliation(
        reconciliation,
        evidence,
        source_ids=args.source_ids,
    )
    passed = all(row["status"] == "passed" for row in results)
    receipt = {
        "schema_version": "source-state-reconciliation-validation.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reconciliation_path": str(args.reconciliation.resolve()),
        "reconciliation_sha256": hashlib.sha256(raw).hexdigest(),
        "source_ids": [row["source_id"] for row in results],
        "status": "passed" if passed else "failed",
        "model_calls": 0,
        "database_writes": 0,
        "results": results,
    }
    if args.output:
        _atomic_write(args.output, receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
