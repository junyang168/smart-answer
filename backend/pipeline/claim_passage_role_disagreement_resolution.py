"""Compile the user-accepted #409 disagreement analysis into a bounded overlay.

The overlay is not the all-Claim role ledger and cannot mutate Claim/Registry.
Unresolved and source-data problems remain withheld, not silently coerced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from backend.pipeline import claim_passage_role_audited_resume as audited
from backend.pipeline import claim_passage_role_disagreement_report as analysis
from backend.pipeline import claim_passage_role_runner as base
from backend.pipeline.claim_passage_role_ledger_assembler import artifact_paths


EXPECTED_COUNTS = {
    "data_issue": 3,
    "recommend_exegesis": 17,
    "recommend_other": 280,
    "unresolved": 2,
}


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compile_resolution(report_path: Path, original_root: Path, opus_root: Path,
                       primary_root: Path, independent_root: Path) -> dict[str, Any]:
    report = base._read_json(report_path)
    expected = analysis.build_report(original_root, opus_root,
                                     primary_root, independent_root)
    if report != expected:
        raise ValueError("analysis report differs from its frozen inputs and code")
    if report["counts"] != EXPECTED_COUNTS or len(report["entries"]) != 302:
        raise ValueError("approved disagreement scope changed")
    packet, _ = audited._check_resume(original_root, opus_root)
    if report["frozen_packet_sha256"] != packet["artifact_sha256"]:
        raise ValueError("report/packet SHA mismatch")
    claim_by_id = {row["claim_id"]: row for row in packet["claims"]}
    primary_by_batch: dict[int, dict[str, dict[str, Any]]] = {}
    decisions = []
    for entry in report["entries"]:
        claim_id = entry["claim_id"]
        claim = claim_by_id[claim_id]
        triage = entry["triage"]
        if triage == "recommend_exegesis":
            batch_id = entry["batch_id"]
            if batch_id not in primary_by_batch:
                primary_path, _, _ = artifact_paths(
                    batch_id, original_root=original_root, opus_root=opus_root,
                    primary_root=primary_root, independent_root=independent_root,
                )
                primary, _ = audited._checked_decisions(
                    primary_path, packet, batch_id, "primary", audited.PRIMARY_MODEL,
                )
                primary_by_batch[batch_id] = {row["claim_id"]: row for row in primary}
            model_decision = primary_by_batch[batch_id][claim_id]
            if model_decision["role"] != "passage_exegesis":
                raise ValueError(f"selected exegesis lacks a GPT passage reading: {claim_id}")
            passage_keys = base._passage_keys(model_decision, claim)
            if not passage_keys:
                raise ValueError(f"selected exegesis lacks an interpreted passage: {claim_id}")
            role, disposition = "passage_exegesis", "resolved"
        elif triage == "recommend_other":
            role, disposition, passage_keys = "other", "resolved", []
        elif triage == "unresolved":
            role, disposition, passage_keys = "unresolved", "needs_human", []
        elif triage == "data_issue":
            role, disposition, passage_keys = "unresolved", "repair_required", []
        else:
            raise ValueError(f"unknown triage: {triage}")
        decisions.append({
            "claim_id": claim_id,
            "claim_content_sha256": claim["claim_content_sha256"],
            "source_id": claim["source_id"],
            "source_content_sha256": claim["source_content_sha256"],
            "role": role,
            "interpreted_passage_keys": passage_keys,
            "disposition": disposition,
            "analysis_index": entry["index"],
            "analysis_note": entry["analyst_note"],
            "primary_artifact_sha256": entry["primary_artifact_sha256"],
            "independent_artifact_sha256": entry["independent_artifact_sha256"],
        })
    ids = [row["claim_id"] for row in decisions]
    if len(ids) != len(set(ids)) or len(ids) != 302:
        raise ValueError("resolution Claim denominator is not exact")
    if dict(sorted(Counter(row["role"] for row in decisions).items())) != {
            "other": 280, "passage_exegesis": 17, "unresolved": 5}:
        raise ValueError("resolution role totals differ")
    return base._artifact({
        "schema_version": "wang_claim_passage_role_disagreement_resolution_v1",
        "status": "user_accepted_analyst_classification_partial_coverage",
        "approval_scope": "2026-09-29 user instruction: 按照你的决定分类",
        "packet_sha256": packet["artifact_sha256"],
        "analysis_file_sha256": _file_sha(report_path),
        "analysis_code_sha256": _file_sha(Path(analysis.__file__)),
        "resolution_code_sha256": _file_sha(Path(__file__)),
        "paired_batch_scope": [1, 637],
        "counts_by_disposition": dict(sorted(Counter(row["disposition"] for row in decisions).items())),
        "counts_by_role": dict(sorted(Counter(row["role"] for row in decisions).items())),
        "decisions": decisions,
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-path", type=Path, required=True)
    parser.add_argument("--original-root", type=Path, required=True)
    parser.add_argument("--opus-root", type=Path, required=True)
    parser.add_argument("--primary-root", type=Path, required=True)
    parser.add_argument("--independent-root", type=Path, required=True)
    parser.add_argument("--output-path", type=Path)
    args = parser.parse_args()
    artifact = compile_resolution(args.report_path, args.original_root,
                                  args.opus_root, args.primary_root,
                                  args.independent_root)
    if args.output_path:
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        base._write_immutable(args.output_path, artifact)
    print(json.dumps({
        "artifact_sha256": artifact["artifact_sha256"],
        "counts_by_disposition": artifact["counts_by_disposition"],
        "counts_by_role": artifact["counts_by_role"],
        "output_path": str(args.output_path) if args.output_path else None,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
