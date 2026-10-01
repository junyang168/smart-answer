from __future__ import annotations

import hashlib
from pathlib import Path

from backend.pipeline.source_state_reconciliation_validator import (
    evidence_keys,
    validate_reconciliation,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[dict, dict]:
    source = tmp_path / "source.json"
    source.write_text("source", encoding="utf-8")
    artifacts = {}
    lineage = []
    for stage in ("extraction", "cross_section", "review", "adjudication", "merge"):
        path = tmp_path / f"{stage}.json"
        path.write_text(stage, encoding="utf-8")
        artifacts[stage] = path
        inputs = {"package_sha256": _sha(artifacts["cross_section"])} if stage == "review" else {}
        lineage.append({
            "run_id": f"RUN-{stage}",
            "stage": stage,
            "status": "succeeded",
            "input_sha256": inputs,
            "evidence_artifact_path": str(path),
            "evidence_artifact_sha256": _sha(path),
        })
    change_set = {
        "change_set_id": "KCS-test",
        "fingerprint_sha256": "f" * 64,
        "package_id": "DETAILED-test",
        "reviewed_package_byte_sha256": _sha(artifacts["merge"]),
        "reviewed_package_canonical_sha256": "c" * 64,
        "status": "applied",
        "summary": {"operations": 1},
        "operation_count": 1,
        "object_version_readback_count": 1,
        "after_sha256_match_count": 1,
        "change_set_id_match_count": 1,
    }
    lineage.append({
        "run_id": "RUN-ingest",
        "stage": "ingest",
        "status": "succeeded",
        "input_sha256": {},
        "change_set": change_set,
    })
    closure = {
        "source_id": "S test",
        "disposition": "lineage_closed",
        "reason_code": "DUPLICATE_ATTEMPT_SUPERSEDED",
        "current_source_path": str(source),
        "current_source_sha256": _sha(source),
        "selected_canonical_lineage": lineage,
        "later_duplicate_extraction": {
            "run_id": "RUN-duplicate",
            "status": "cancelled",
            "input_sha256": {"source_sha256": _sha(source)},
        },
    }
    reconciliation = {
        "schema_version": "source-state-reconciliation.v1",
        "closures": [closure],
    }
    runs = {
        row["run_id"]: {
            "stage": row["stage"], "status": "succeeded",
            "input_sha256": row["input_sha256"], "output_paths": [], "metadata": {},
        }
        for row in lineage
    }
    runs["RUN-duplicate"] = {
        "stage": "extraction", "status": "cancelled",
        "input_sha256": closure["later_duplicate_extraction"]["input_sha256"],
        "output_paths": [], "metadata": {},
    }
    evidence = {
        "runs": runs,
        "change_sets": {
            "KCS-test": {
                "fingerprint_sha256": "f" * 64,
                "package_id": "DETAILED-test",
                "source_sha256": "c" * 64,
                "status": "applied",
                "summary": {"operations": 1},
                "metadata": {},
            }
        },
        "readbacks": {
            "KCS-test": {
                "operation_count": 1,
                "change_set_id_match_count": 1,
                "after_sha256_match_count": 1,
            }
        },
        "objects": {},
    }
    return reconciliation, evidence


def test_revalidates_a_complete_lineage(tmp_path: Path) -> None:
    reconciliation, evidence = _fixture(tmp_path)

    result = validate_reconciliation(reconciliation, evidence)

    assert result[0]["status"] == "passed"
    assert result[0]["failures"] == []


def test_detects_source_drift_and_database_readback_mismatch(tmp_path: Path) -> None:
    reconciliation, evidence = _fixture(tmp_path)
    Path(reconciliation["closures"][0]["current_source_path"]).write_text(
        "changed", encoding="utf-8"
    )
    evidence["readbacks"]["KCS-test"]["after_sha256_match_count"] = 0

    result = validate_reconciliation(reconciliation, evidence)
    failed = {row["check"] for row in result[0]["failures"]}

    assert result[0]["status"] == "failed"
    assert "current_source.sha256" in failed
    assert "ingest.after_sha256_match_count" in failed


def test_collects_all_authority_keys() -> None:
    closure = {
        "selected_canonical_lineage": [
            {"run_id": "RUN-a", "stage": "ingest", "change_set": {"change_set_id": "KCS-a"}}
        ],
        "rejected_attempts": [{"run_id": "RUN-b"}],
        "later_duplicate_extraction": {"run_id": "RUN-c"},
        "obsolete_object_state": {"collection": "claims", "object_id": "CL-1"},
    }

    assert evidence_keys([closure]) == (
        {"RUN-a", "RUN-b", "RUN-c"}, {"KCS-a"}, {("claims", "CL-1")}
    )
