"""Reconcile a known unrecorded pipeline chain from SHA-bound artifacts.

This is deliberately narrower than :mod:`run_ledger_backfill`.  A repair
manifest names the exact artifacts, their hashes, and the already-recorded
merge/ingest consumers.  Nothing is inferred from whichever files happen to
be newest in a directory, and no model is called.

    python -m backend.pipeline.run_ledger_reconciliation \
      --manifest backend/pipeline/run_reconciliations/<repair>.json --dry-run
    python -m backend.pipeline.run_ledger_reconciliation \
      --manifest backend/pipeline/run_reconciliations/<repair>.json --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from backend.config.wang_platform_paths import wang_platform_paths
from backend.pipeline.detailed_knowledge_extraction_runner import _coverage_quality
from backend.pipeline.model_prices import price_usage
from backend.pipeline.run_ledger_backfill import _artifact_stage, _generated_at, _subject


SCHEMA_VERSION = "wang_pipeline_run_reconciliation_v1"
RECONCILABLE_STAGES = ("extraction", "cross_section", "review", "adjudication")


class RunLedgerReconciliationError(ValueError):
    """The proposed historical record cannot be proved by its artifacts."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunLedgerReconciliationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RunLedgerReconciliationError(f"{path}: expected a JSON object")
    return payload


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise RunLedgerReconciliationError(f"cannot read {path}: {exc}") from exc


def _safe_child(root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise RunLedgerReconciliationError(f"artifact path must be relative: {relative!r}")
    root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise RunLedgerReconciliationError(f"artifact escapes its declared root: {relative}")
    return path


def _stage_inputs(stage: str, payload: dict[str, Any]) -> dict[str, Any]:
    if stage == "extraction":
        block = payload.get("extraction") or {}
        return {
            "source_sha256": block.get("source_body_sha256") or block.get("source_sha256"),
            "prompt_sha256": block.get("prompt_sha256"),
            "fingerprint_sha256": block.get("fingerprint_sha256"),
        }
    if stage == "cross_section":
        block = payload.get("cross_section_relations") or {}
        return {
            "package_sha256": block.get("package_sha256"),
            "prompt_sha256": block.get("prompt_sha256"),
            "fingerprint_sha256": block.get("fingerprint_sha256"),
        }
    if stage == "review":
        source = payload.get("source") or {}
        reviewer = payload.get("reviewer") or {}
        return {
            "package_sha256": source.get("package_sha256"),
            "fingerprint_sha256": reviewer.get("fingerprint_sha256"),
        }
    block = payload.get("adjudicator") or {}
    return {
        "package_sha256": block.get("source_package_sha256"),
        "review_sha256": block.get("review_artifact_sha256"),
        "fingerprint_sha256": block.get("fingerprint_sha256"),
    }


def _stage_quality(stage: str, payload: dict[str, Any]) -> dict[str, Any]:
    if stage == "extraction":
        return _coverage_quality(dict(payload.get("coverage") or {}))
    if stage == "cross_section":
        block = payload.get("cross_section_relations") or {}
        return {
            "evidence_relations_added": block.get("evidence_relations_added", 0),
            "claim_relations_added": block.get("claim_relations_added", 0),
        }
    if stage == "review":
        return dict(payload.get("routing_summary") or {})
    return dict(payload.get("summary") or {})


def _deterministic_run_id(reconciliation_id: str, stage: str, artifact_sha256: str) -> str:
    digest = hashlib.sha256(
        f"{reconciliation_id}\0{stage}\0{artifact_sha256}".encode("utf-8")
    ).hexdigest()[:26]
    return f"RUN-{digest}"


def load_reconciliation(manifest_path: Path, staging_root: Path) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise RunLedgerReconciliationError(
            f"{manifest_path}: expected schema_version {SCHEMA_VERSION}"
        )
    reconciliation_id = str(manifest.get("reconciliation_id") or "").strip()
    source_id = str(manifest.get("source_id") or "").strip()
    triggered_by = str(manifest.get("triggered_by") or "").strip() or None
    if not reconciliation_id or not source_id:
        raise RunLedgerReconciliationError("reconciliation_id and source_id are required")

    artifact_root = _safe_child(staging_root, str(manifest.get("artifact_root") or ""))
    entries = manifest.get("artifacts")
    if not isinstance(entries, list) or len(entries) != len(RECONCILABLE_STAGES):
        raise RunLedgerReconciliationError(
            f"artifacts must contain exactly {len(RECONCILABLE_STAGES)} entries"
        )

    payload_by_stage: dict[str, dict[str, Any]] = {}
    sha_by_stage: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    source_cache: dict[Path, str | None] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise RunLedgerReconciliationError("each artifact entry must be an object")
        stage = str(entry.get("stage") or "")
        if stage not in RECONCILABLE_STAGES or stage in payload_by_stage:
            raise RunLedgerReconciliationError(f"invalid or duplicate stage: {stage!r}")
        path = _safe_child(artifact_root, str(entry.get("path") or ""))
        expected_sha = str(entry.get("sha256") or "")
        actual_sha = _sha256(path)
        if actual_sha != expected_sha:
            raise RunLedgerReconciliationError(
                f"{stage}: artifact SHA mismatch: expected {expected_sha}, got {actual_sha}"
            )
        if _artifact_stage(path) != stage:
            raise RunLedgerReconciliationError(f"{path}: filename does not identify {stage}")
        payload = _read_json(path)
        resolved_source = _subject(payload, path, source_cache)
        if resolved_source != source_id:
            raise RunLedgerReconciliationError(
                f"{stage}: expected source {source_id!r}, got {resolved_source!r}"
            )
        generated_at, time_source = _generated_at(stage, payload, path)
        model_id = str(entry.get("model_id") or "").strip() or None
        usage = list(payload.get("usage") or [])
        cost = price_usage(usage, model_id, when=generated_at)
        row = {
            "run_id": _deterministic_run_id(reconciliation_id, stage, actual_sha),
            "reconciliation_id": reconciliation_id,
            "source_id": source_id,
            "triggered_by": triggered_by,
            "stage": stage,
            "path": path,
            "sha256": actual_sha,
            "generated_at": generated_at,
            "time_source": time_source,
            "model_id": model_id,
            "usage": usage,
            "cost_usd": cost.cost_usd,
            "price_version": cost.price_version,
            "quality": _stage_quality(stage, payload),
            "inputs": _stage_inputs(stage, payload),
            "output_paths": [str(path)],
        }
        payload_by_stage[stage] = payload
        sha_by_stage[stage] = actual_sha
        rows.append(row)

    missing = set(RECONCILABLE_STAGES) - set(payload_by_stage)
    if missing:
        raise RunLedgerReconciliationError(f"missing stages: {sorted(missing)}")

    cross = payload_by_stage["cross_section"].get("cross_section_relations") or {}
    review_source = payload_by_stage["review"].get("source") or {}
    adjudicator = payload_by_stage["adjudication"].get("adjudicator") or {}
    expected_links = {
        "cross_section.package_sha256": (cross.get("package_sha256"), sha_by_stage["extraction"]),
        "review.package_sha256": (review_source.get("package_sha256"), sha_by_stage["cross_section"]),
        "adjudication.package_sha256": (
            adjudicator.get("source_package_sha256"), sha_by_stage["cross_section"]
        ),
        "adjudication.review_sha256": (
            adjudicator.get("review_artifact_sha256"), sha_by_stage["review"]
        ),
    }
    for label, (actual, expected) in expected_links.items():
        if actual != expected:
            raise RunLedgerReconciliationError(
                f"{label} does not bind the declared upstream artifact: "
                f"expected {expected}, got {actual}"
            )

    companion = manifest.get("adjudication_companion") or {}
    companion_path = _safe_child(artifact_root, str(companion.get("path") or ""))
    companion_sha = _sha256(companion_path)
    if companion_sha != companion.get("sha256"):
        raise RunLedgerReconciliationError("adjudication companion SHA mismatch")
    next(row for row in rows if row["stage"] == "adjudication")["output_paths"].append(
        str(companion_path)
    )

    consumer = manifest.get("consumer") or {}
    merged_output = _safe_child(artifact_root, str(consumer.get("merged_output") or ""))
    merged_output_sha = _sha256(merged_output)
    expected_merged_sha = str(consumer.get("merged_output_sha256") or "")
    if merged_output_sha != expected_merged_sha:
        raise RunLedgerReconciliationError("merged output SHA mismatch")

    ordered_rows = sorted(rows, key=lambda row: RECONCILABLE_STAGES.index(row["stage"]))
    for earlier, later in zip(ordered_rows, ordered_rows[1:]):
        if earlier["generated_at"] > later["generated_at"]:
            raise RunLedgerReconciliationError(
                f"artifact times reverse the pipeline: {earlier['stage']} at "
                f"{earlier['generated_at'].isoformat()} is after {later['stage']} at "
                f"{later['generated_at'].isoformat()}"
            )

    return {
        "reconciliation_id": reconciliation_id,
        "source_id": source_id,
        "rows": ordered_rows,
        "consumer": {
            "merge_run_id": str(consumer.get("merge_run_id") or ""),
            "ingest_run_id": str(consumer.get("ingest_run_id") or ""),
            "merged_output": str(merged_output),
            "merged_output_sha256": merged_output_sha,
            "expected_merge_inputs": {
                "package_sha256": sha_by_stage["cross_section"],
                "review_sha256": sha_by_stage["review"],
                "adjudication_sha256": sha_by_stage["adjudication"],
                "overrides_sha256": companion_sha,
            },
        },
    }


def _validate_consumer(cursor: Any, plan: dict[str, Any]) -> None:
    consumer = plan["consumer"]
    source_id = plan["source_id"]
    for stage, run_id in (
        ("merge", consumer["merge_run_id"]),
        ("ingest", consumer["ingest_run_id"]),
    ):
        if not run_id:
            raise RunLedgerReconciliationError(f"consumer {stage}_run_id is required")
        cursor.execute(
            """SELECT subject_id, source_ids, stage, status, input_sha256, output_paths
                 FROM wang_knowledge.pipeline_runs WHERE run_id=%s""",
            (run_id,),
        )
        found = cursor.fetchone()
        if not found:
            raise RunLedgerReconciliationError(f"consumer run {run_id} does not exist")
        subject, source_ids, actual_stage, status, inputs, outputs = found
        if (
            subject != source_id
            or source_id not in (source_ids or [])
            or actual_stage != stage
            or status != "succeeded"
            or consumer["merged_output"] not in (outputs or [])
        ):
            raise RunLedgerReconciliationError(f"consumer run {run_id} does not match the plan")
        if stage == "merge" and inputs != consumer["expected_merge_inputs"]:
            raise RunLedgerReconciliationError(
                f"merge run {run_id} does not bind the declared artifacts"
            )


def _matching_existing(found: tuple[Any, ...], row: dict[str, Any]) -> bool:
    subject, stage, status, inputs, outputs, metadata = found
    return (
        subject == row["source_id"]
        and stage == row["stage"]
        and status == "succeeded"
        and inputs == row["inputs"]
        and list(outputs or []) == row["output_paths"]
        and (metadata or {}).get("reconciliation_id") == row["reconciliation_id"]
        and (metadata or {}).get("backfilled_sha256") == row["sha256"]
    )


def preflight_reconciliation(plan: dict[str, Any], database_url: str) -> dict[str, int]:
    """Validate the recorded consumers and prove the write will be idempotent."""

    import psycopg

    counts = {"would_insert": 0, "already_present": 0}
    with psycopg.connect(database_url) as conn, conn.cursor() as cursor:
        _validate_consumer(cursor, plan)
        for row in plan["rows"]:
            cursor.execute(
                """SELECT subject_id, stage, status, input_sha256, output_paths, metadata
                     FROM wang_knowledge.pipeline_runs
                    WHERE metadata->>'backfilled_sha256'=%s OR run_id=%s""",
                (row["sha256"], row["run_id"]),
            )
            found = cursor.fetchall()
            if len(found) > 1:
                raise RunLedgerReconciliationError(
                    f"artifact {row['sha256']} already has duplicate ledger rows"
                )
            if found:
                if not _matching_existing(found[0], row):
                    raise RunLedgerReconciliationError(
                        f"run {row['run_id']} conflicts with the reconciliation"
                    )
                counts["already_present"] += 1
            else:
                counts["would_insert"] += 1
    return counts


def apply_reconciliation(plan: dict[str, Any], database_url: str) -> dict[str, int]:
    import psycopg

    counts = {"inserted": 0, "already_present": 0}
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            _validate_consumer(cursor, plan)
            for row in plan["rows"]:
                metadata = {
                    "backfilled": True,
                    "backfilled_sha256": row["sha256"],
                    "backfilled_from": str(row["path"]),
                    "time_source": row["time_source"],
                    "reconciliation_id": row["reconciliation_id"],
                    "note": "Reconciled from a SHA-bound artifact; no model was rerun.",
                }
                cursor.execute(
                    """SELECT subject_id, stage, status, input_sha256, output_paths, metadata
                         FROM wang_knowledge.pipeline_runs
                        WHERE metadata->>'backfilled_sha256'=%s""",
                    (row["sha256"],),
                )
                same_artifact = cursor.fetchall()
                if len(same_artifact) > 1:
                    raise RunLedgerReconciliationError(
                        f"artifact {row['sha256']} already has duplicate ledger rows"
                    )
                if same_artifact:
                    if not _matching_existing(same_artifact[0], row):
                        raise RunLedgerReconciliationError(
                            f"artifact {row['sha256']} has a conflicting ledger row"
                        )
                    counts["already_present"] += 1
                    continue
                cursor.execute(
                    """INSERT INTO wang_knowledge.pipeline_runs
                        (run_id, batch_id, subject_kind, subject_id, source_ids, stage,
                         trigger, triggered_by, status, started_at, finished_at,
                         heartbeat_at, model_id, usage, cost_usd, price_version,
                         quality, input_sha256, output_paths, command, metadata)
                       VALUES (%s,%s,'source',%s,%s,%s,'cli',%s,'succeeded',%s,%s,%s,
                               %s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (run_id) DO NOTHING""",
                    (
                        row["run_id"], row["reconciliation_id"], row["source_id"],
                        [row["source_id"]], row["stage"], row["triggered_by"],
                        row["generated_at"], row["generated_at"], row["generated_at"],
                        row["model_id"], json.dumps(row["usage"], ensure_ascii=False),
                        row["cost_usd"], row["price_version"],
                        json.dumps(row["quality"], ensure_ascii=False),
                        json.dumps(row["inputs"], ensure_ascii=False), row["output_paths"],
                        f"reconcile:{row['reconciliation_id']}",
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )
                inserted = cursor.rowcount == 1
                cursor.execute(
                    """SELECT subject_id, stage, status, input_sha256, output_paths, metadata
                         FROM wang_knowledge.pipeline_runs WHERE run_id=%s""",
                    (row["run_id"],),
                )
                found = cursor.fetchone()
                if not found or not _matching_existing(found, row):
                    raise RunLedgerReconciliationError(
                        f"existing run {row['run_id']} conflicts with the reconciliation"
                    )
                counts["inserted" if inserted else "already_present"] += 1
    return counts


def _summary(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "reconciliation_id": plan["reconciliation_id"],
        "source_id": plan["source_id"],
        "runs": [
            {
                "run_id": row["run_id"],
                "stage": row["stage"],
                "generated_at": row["generated_at"].isoformat(),
                "artifact_sha256": row["sha256"],
            }
            for row in plan["rows"]
        ],
        "consumer": plan["consumer"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--database-url")
    args = parser.parse_args()

    plan = load_reconciliation(args.manifest, wang_platform_paths().staging)
    summary = _summary(plan)
    url = args.database_url or os.getenv("KNOWLEDGE_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        parser.error("KNOWLEDGE_DATABASE_URL is required")
    summary.update(preflight_reconciliation(plan, url))
    summary["applied"] = False
    if args.apply:
        summary.update(apply_reconciliation(plan, url))
        summary["applied"] = True
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
