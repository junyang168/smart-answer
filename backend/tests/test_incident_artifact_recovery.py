from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.api.canonical_repository.postgres_store import sha256_json
from backend.pipeline.incident_artifact_recovery import (
    INPUT_SCHEMA_VERSION,
    IncidentArtifactRecoveryError,
    apply_restorations,
    build_report,
    load_manifest,
    validate_stage_state_proofs,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(tmp_path: Path) -> Path:
    current = tmp_path / "current.json"
    restore = tmp_path / "restore.json"
    current.write_text('{"generation":"withdrawn"}\n', encoding="utf-8")
    restore.write_text('{"generation":"authoritative"}\n', encoding="utf-8")
    payload = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "withdrawn_archive_dir": str(tmp_path / "withdrawn"),
        "expected_counts": {
            "sources": 1,
            "artifacts": 1,
            "stage:reviewed": 1,
        },
        "restorations": [
            {
                "source_key": "A",
                "stage": "reviewed",
                "current_path": str(current),
                "current_sha256": _sha(current),
                "restore_path": str(restore),
                "restore_sha256": _sha(restore),
            }
        ],
    }
    path = tmp_path / "input.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_apply_archives_withdrawn_bytes_before_restoring(tmp_path: Path) -> None:
    manifest = load_manifest(_manifest(tmp_path))
    report = apply_restorations(build_report(manifest))

    current = tmp_path / "current.json"
    assert current.read_text(encoding="utf-8") == '{"generation":"authoritative"}\n'
    archived = json.loads(
        (tmp_path / "withdrawn" / "withdrawn-artifacts.json").read_text()
    )
    assert archived["artifacts"][0]["sha256"] == hashlib.sha256(
        b'{"generation":"withdrawn"}\n'
    ).hexdigest()
    assert report["status"] == "applied"


def test_current_state_guard_refuses_a_file_changed_after_planning(tmp_path: Path) -> None:
    manifest = load_manifest(_manifest(tmp_path))
    (tmp_path / "current.json").write_text("changed", encoding="utf-8")

    with pytest.raises(IncidentArtifactRecoveryError, match="changed after planning"):
        build_report(manifest)


def test_apply_refuses_to_mix_with_an_existing_withdrawal_archive(tmp_path: Path) -> None:
    manifest = load_manifest(_manifest(tmp_path))
    report = build_report(manifest)
    (tmp_path / "withdrawn").mkdir()

    with pytest.raises(IncidentArtifactRecoveryError, match="already exists"):
        apply_restorations(report)


def test_stage_state_proof_preserves_declared_freshness(tmp_path: Path) -> None:
    artifacts = {
        "cross_section": {
            "cross_section_relations": {
                "schema_version": "wang_cross_section_relation_v2"
            }
        },
        "independent_review": {
            "source": {},
            "reviewer": {"fingerprint_sha256": "review-fingerprint"},
        },
        "adjudication": {
            "adjudicator": {
                "review_fingerprint": "review-fingerprint",
                "fingerprint_sha256": "adjudication-fingerprint",
            }
        },
        "consensus_override": {
            "adjudication_fingerprint": {
                "fingerprint_sha256": "adjudication-fingerprint"
            }
        },
        "reviewed": {
            "consensus_application": {
                "adjudication_fingerprint": "adjudication-fingerprint"
            }
        },
    }
    rows = []
    for stage, payload in artifacts.items():
        path = tmp_path / f"{stage}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        rows.append(
            {"source_key": "A", "stage": stage, "restore_path": str(path)}
        )
    cross_path = tmp_path / "cross_section.json"
    artifacts["independent_review"]["source"]["package_sha256"] = _sha(cross_path)
    (tmp_path / "independent_review.json").write_text(
        json.dumps(artifacts["independent_review"]), encoding="utf-8"
    )
    proof = {
        "source_key": "A",
        "restored_freshness": {
            "review_reads_restored_cross_section": True,
            "adjudication_reads_restored_review": True,
            "override_reads_restored_adjudication": True,
            "reviewed_reads_restored_override": True,
        },
        "expected_stale_stages": [],
        "historical_reviewed_canonical_sha256": sha256_json(artifacts["reviewed"]),
    }

    result = validate_stage_state_proofs(rows, [proof])

    assert result["status"] == "verified"
    assert result["stale_stage_counts"] == {}
