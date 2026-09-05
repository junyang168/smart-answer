"""Restore canonical staging paths after a withdrawn model rerun.

Historical generation artifacts are immutable.  This runner first copies every
withdrawn current artifact into a SHA-bound recovery archive, stages every
authoritative replacement beside its target, and then swaps the canonical
paths.  If a swap fails, completed swaps are restored from that archive.

Planning is read-only.  Applying changes staging artifacts only; it does not
write the knowledge registry or the reader-visible Wang repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.api.canonical_repository.postgres_store import sha256_json


INPUT_SCHEMA_VERSION = "wang_incident_artifact_recovery_input_v1"
REPORT_SCHEMA_VERSION = "wang_incident_artifact_recovery_report_v1"


class IncidentArtifactRecoveryError(RuntimeError):
    """Raised before an artifact restoration can overwrite a canonical path."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expand(value: str) -> Path:
    expanded = os.path.expandvars(value)
    if "$" in expanded:
        raise IncidentArtifactRecoveryError(f"unresolved path variable: {value}")
    return Path(expanded).expanduser().resolve()


def load_manifest(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise IncidentArtifactRecoveryError(
            f"schema_version must be {INPUT_SCHEMA_VERSION}"
        )
    rows = payload.get("restorations") or []
    if not rows:
        raise IncidentArtifactRecoveryError("restorations cannot be empty")
    targets: set[Path] = set()
    for index, row in enumerate(rows):
        required = {
            "source_key",
            "stage",
            "current_path",
            "current_sha256",
            "restore_path",
            "restore_sha256",
        }
        missing = sorted(required - set(row))
        if missing:
            raise IncidentArtifactRecoveryError(
                f"restorations[{index}] is missing: {', '.join(missing)}"
            )
        target = _expand(str(row["current_path"]))
        if target in targets:
            raise IncidentArtifactRecoveryError(f"duplicate restoration target: {target}")
        targets.add(target)
    archive_dir = payload.get("withdrawn_archive_dir")
    if not archive_dir:
        raise IncidentArtifactRecoveryError("withdrawn_archive_dir is required")
    return {
        **payload,
        "manifest_path": str(path.resolve()),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }


def validate_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    planned: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        current = _expand(str(row["current_path"]))
        restore = _expand(str(row["restore_path"]))
        if not current.is_file():
            raise IncidentArtifactRecoveryError(f"current artifact is missing: {current}")
        if not restore.is_file():
            raise IncidentArtifactRecoveryError(f"restore artifact is missing: {restore}")
        current_sha = sha256_file(current)
        restore_sha = sha256_file(restore)
        if current_sha != str(row["current_sha256"]):
            raise IncidentArtifactRecoveryError(
                f"current artifact changed after planning: {current}"
            )
        if restore_sha != str(row["restore_sha256"]):
            raise IncidentArtifactRecoveryError(
                f"restore artifact changed after planning: {restore}"
            )
        if current_sha == restore_sha:
            raise IncidentArtifactRecoveryError(
                f"restoration is not a state change: {current}"
            )
        planned.append(
            {
                **dict(row),
                "operation_index": index,
                "current_path": str(current),
                "restore_path": str(restore),
            }
        )
    return planned


def validate_stage_state_proofs(
    rows: Sequence[Mapping[str, Any]], proofs: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Verify the selected files preserve the actual pre-incident stage state.

    Freshness is not repaired here.  Three sources were already stale before
    the incident, so forcing every stage into one apparently coherent chain
    would manufacture a state that never existed.
    """

    if not proofs:
        return {"status": "not_declared"}
    row_by_key = {
        (str(row["source_key"]), str(row["stage"])): row for row in rows
    }
    source_keys = {key[0] for key in row_by_key}
    proof_by_source = {str(row.get("source_key") or ""): row for row in proofs}
    if "" in proof_by_source or len(proof_by_source) != len(proofs):
        raise IncidentArtifactRecoveryError(
            "stage_state_proofs requires one unique, non-empty source_key per proof"
        )
    if set(proof_by_source) != source_keys:
        raise IncidentArtifactRecoveryError(
            "stage_state_proofs source coverage differs from restorations"
        )

    stages = {
        "cross_section",
        "independent_review",
        "adjudication",
        "consensus_override",
        "reviewed",
    }
    stale_counts: dict[str, int] = {}
    for source_key in sorted(source_keys):
        missing = sorted(
            stage for stage in stages if (source_key, stage) not in row_by_key
        )
        if missing:
            raise IncidentArtifactRecoveryError(
                f"stage-state proof for {source_key} is missing: {', '.join(missing)}"
            )
        artifacts = {
            stage: json.loads(
                Path(str(row_by_key[(source_key, stage)]["restore_path"])).read_text(
                    encoding="utf-8"
                )
            )
            for stage in stages
        }
        cross_path = Path(
            str(row_by_key[(source_key, "cross_section")]["restore_path"])
        )
        if (
            artifacts["cross_section"]
            .get("cross_section_relations", {})
            .get("schema_version")
            != "wang_cross_section_relation_v2"
        ):
            raise IncidentArtifactRecoveryError(
                f"restored cross-section artifact is not v2 for {source_key}"
            )
        actual_freshness = {
            "review_reads_restored_cross_section": (
                artifacts["independent_review"].get("source", {}).get(
                    "package_sha256"
                )
                == sha256_file(cross_path)
            ),
            "adjudication_reads_restored_review": (
                artifacts["adjudication"].get("adjudicator", {}).get(
                    "review_fingerprint"
                )
                == artifacts["independent_review"].get("reviewer", {}).get(
                    "fingerprint_sha256"
                )
            ),
            "override_reads_restored_adjudication": (
                artifacts["consensus_override"]
                .get("adjudication_fingerprint", {})
                .get("fingerprint_sha256")
                == artifacts["adjudication"].get("adjudicator", {}).get(
                    "fingerprint_sha256"
                )
            ),
            "reviewed_reads_restored_override": (
                artifacts["reviewed"].get("consensus_application", {}).get(
                    "adjudication_fingerprint"
                )
                == artifacts["consensus_override"]
                .get("adjudication_fingerprint", {})
                .get("fingerprint_sha256")
            ),
        }
        proof = proof_by_source[source_key]
        if dict(proof.get("restored_freshness") or {}) != actual_freshness:
            raise IncidentArtifactRecoveryError(
                f"declared stage freshness differs from selected artifacts for {source_key}"
            )
        stale_stage_by_binding = {
            "review_reads_restored_cross_section": "independent_review",
            "adjudication_reads_restored_review": "adjudication",
            "override_reads_restored_adjudication": "consensus_override",
            "reviewed_reads_restored_override": "reviewed",
        }
        actual_stale = sorted(
            stale_stage_by_binding[key]
            for key, fresh in actual_freshness.items()
            if not fresh
        )
        expected_stale = sorted(
            str(value) for value in proof.get("expected_stale_stages") or []
        )
        if expected_stale != actual_stale:
            raise IncidentArtifactRecoveryError(
                f"declared stale stages differ from selected artifacts for {source_key}"
            )
        reviewed_sha = sha256_json(artifacts["reviewed"])
        if reviewed_sha != str(proof.get("historical_reviewed_canonical_sha256") or ""):
            raise IncidentArtifactRecoveryError(
                f"historical reviewed canonical SHA differs for {source_key}"
            )
        for stage in actual_stale:
            stale_counts[stage] = stale_counts.get(stage, 0) + 1
    return {
        "status": "verified",
        "sources_checked": len(source_keys),
        "cross_section_v2_artifacts_checked": len(source_keys),
        "stale_stage_counts": dict(sorted(stale_counts.items())),
        "undeclared_stage_state_differences": 0,
    }


def build_report(manifest: Mapping[str, Any]) -> dict[str, Any]:
    rows = validate_rows(manifest["restorations"])
    stage_state_proofs = list(manifest.get("stage_state_proofs") or [])
    stage_state_validation = validate_stage_state_proofs(rows, stage_state_proofs)
    by_stage: dict[str, int] = {}
    for row in rows:
        stage = str(row["stage"])
        by_stage[stage] = by_stage.get(stage, 0) + 1
    source_keys = {str(row["source_key"]) for row in rows}
    expected = manifest.get("expected_counts") or {}
    actual = {
        "sources": len(source_keys),
        "artifacts": len(rows),
        **{f"stage:{key}": value for key, value in sorted(by_stage.items())},
    }
    if expected and {str(k): int(v) for k, v in expected.items()} != actual:
        raise IncidentArtifactRecoveryError(
            f"artifact recovery count contract differs: expected {expected}, found {actual}"
        )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "planned",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_manifest_path": manifest["manifest_path"],
        "input_manifest_sha256": manifest["manifest_sha256"],
        "withdrawn_archive_dir": str(_expand(str(manifest["withdrawn_archive_dir"]))),
        "count_contract": {"expected": expected, "actual": actual, "matches": True},
        "restorations": rows,
        "stage_state_proofs": stage_state_proofs,
        "stage_state_validation": stage_state_validation,
    }


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def apply_restorations(report: Mapping[str, Any]) -> dict[str, Any]:
    rows = validate_rows(report["restorations"])
    archive = Path(str(report["withdrawn_archive_dir"]))
    if archive.exists():
        raise IncidentArtifactRecoveryError(
            f"withdrawn archive already exists; refusing to mix runs: {archive}"
        )
    archive.mkdir(parents=True)
    archived: dict[str, Path] = {}
    staged: dict[str, Path] = {}
    completed: list[dict[str, Any]] = []
    try:
        for row in rows:
            target = Path(str(row["current_path"]))
            backup = archive / f"{int(row['operation_index']):03d}-{target.name}"
            shutil.copy2(target, backup)
            if sha256_file(backup) != row["current_sha256"]:
                raise IncidentArtifactRecoveryError(f"withdrawn archive copy failed: {target}")
            archived[str(target)] = backup
        _write(
            archive / "withdrawn-artifacts.json",
            {
                "schema_version": "wang_withdrawn_incident_artifacts_v1",
                "status": "archived_before_restore",
                "input_manifest_sha256": report["input_manifest_sha256"],
                "artifacts": [
                    {
                        "source_key": row["source_key"],
                        "stage": row["stage"],
                        "original_path": row["current_path"],
                        "sha256": row["current_sha256"],
                        "archive_path": str(archived[row["current_path"]]),
                        "withdrawal_reason": (
                            "superseded semantic rerun caused by ID-only schema bump"
                        ),
                    }
                    for row in rows
                ],
            },
        )
        for row in rows:
            target = Path(str(row["current_path"]))
            restore = Path(str(row["restore_path"]))
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{target.name}.restore-", dir=target.parent
            )
            os.close(descriptor)
            temporary_path = Path(temporary)
            shutil.copy2(restore, temporary_path)
            if sha256_file(temporary_path) != row["restore_sha256"]:
                raise IncidentArtifactRecoveryError(f"staged restore SHA differs: {target}")
            staged[str(target)] = temporary_path
        for row in rows:
            target = Path(str(row["current_path"]))
            os.replace(staged[str(target)], target)
            staged.pop(str(target), None)
            completed.append(row)
        for row in rows:
            target = Path(str(row["current_path"]))
            if sha256_file(target) != row["restore_sha256"]:
                raise IncidentArtifactRecoveryError(f"restored artifact SHA differs: {target}")
    except BaseException:
        for row in reversed(completed):
            target = Path(str(row["current_path"]))
            shutil.copy2(archived[str(target)], target)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
    return {
        **dict(report),
        "status": "applied",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "restored_artifact_count": len(rows),
        "withdrawn_artifact_manifest": str(archive / "withdrawn-artifacts.json"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-manifest", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    manifest = load_manifest(args.input_manifest)
    report = build_report(manifest)
    if args.apply:
        report = apply_restorations(report)
    _write(args.report, report)
    print(json.dumps({
        "status": report["status"],
        "sources": report["count_contract"]["actual"]["sources"],
        "artifacts": report["count_contract"]["actual"]["artifacts"],
        "report": str(args.report),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
