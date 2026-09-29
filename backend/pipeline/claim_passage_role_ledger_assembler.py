"""Read-only #409 dual-review audit; write a ledger only at full coverage.

This joins the immutable original, Opus-resume, GPT-only, and Claude-only
artifacts by frozen batch/Claim ID. It never calls a model or mutates the DB.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.pipeline import claim_passage_role_audited_resume as audited
from backend.pipeline import claim_passage_role_independent_prefetch as independent_queue
from backend.pipeline import claim_passage_role_primary_prefetch as primary_queue
from backend.pipeline import claim_passage_role_runner as base


def artifact_paths(batch_id: int, *, original_root: Path, opus_root: Path,
                   primary_root: Path, independent_root: Path) -> tuple[Path, Path, str]:
    if batch_id < 1:
        raise ValueError("batch ID must be positive")
    if batch_id <= 69:
        primary = original_root / f"primary-{batch_id:05d}.json"
    elif batch_id <= 82:
        primary = opus_root / f"primary-{batch_id:05d}.json"
    else:
        lane = primary_queue._lane_for(batch_id)
        primary = primary_root / f"worker-{lane}" / f"primary-{batch_id:05d}.json"
    if batch_id <= 68:
        independent = original_root / f"independent-{batch_id:05d}.json"
        independent_model = audited.OLD_INDEPENDENT_MODEL
    elif batch_id <= 81:
        independent = opus_root / f"independent-{batch_id:05d}.json"
        independent_model = audited.NEW_INDEPENDENT_MODEL
    else:
        lane = independent_queue._lane_for(batch_id)
        independent = independent_root / f"worker-{lane}" / f"independent-{batch_id:05d}.json"
        independent_model = audited.NEW_INDEPENDENT_MODEL
    return primary, independent, independent_model


def _resolution_rows(path: Path, packet: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    artifact = base._read_json(path)
    base._check_artifact(artifact)
    if (artifact.get("schema_version") != "wang_claim_passage_role_disagreement_resolution_v1"
            or artifact.get("status") != "user_accepted_analyst_classification_partial_coverage"
            or artifact.get("packet_sha256") != packet["artifact_sha256"]
            or artifact.get("paired_batch_scope") != [1, 637]
            or artifact.get("counts_by_role") != {
                "other": 280, "passage_exegesis": 17, "unresolved": 5,
            }
            or artifact.get("counts_by_disposition") != {
                "needs_human": 2, "repair_required": 3, "resolved": 297,
            }):
        raise ValueError("disagreement resolution identity or approved scope differs")
    rows = artifact.get("decisions")
    if not isinstance(rows, list) or len(rows) != 302:
        raise ValueError("disagreement resolution denominator differs")
    keyed = {row["claim_id"]: row for row in rows}
    if len(keyed) != 302:
        raise ValueError("disagreement resolution Claim IDs repeat")
    packet_by_id = {row["claim_id"]: row for row in packet["claims"]}
    if not set(keyed).issubset(packet_by_id):
        raise ValueError("disagreement resolution references an unknown Claim")
    for claim_id, row in keyed.items():
        source = packet_by_id[claim_id]
        if (row["claim_content_sha256"] != source["claim_content_sha256"]
                or row["source_id"] != source["source_id"]
                or row["source_content_sha256"] != source["source_content_sha256"]):
            raise ValueError(f"disagreement resolution source binding differs: {claim_id}")
        role, disposition, keys = (row["role"], row["disposition"],
                                   row["interpreted_passage_keys"])
        if not ((role == "passage_exegesis" and disposition == "resolved" and keys)
                or (role == "other" and disposition == "resolved" and keys == [])
                or (role == "unresolved" and disposition in {"needs_human", "repair_required"}
                    and keys == [])):
            raise ValueError(f"disagreement resolution role/hold mismatch: {claim_id}")
    if (dict(sorted(Counter(row["role"] for row in rows).items()))
            != artifact["counts_by_role"]
            or dict(sorted(Counter(row["disposition"] for row in rows).items()))
            != artifact["counts_by_disposition"]):
        raise ValueError("disagreement resolution counts differ from rows")
    return artifact, keyed


def _apply_resolution(reconciled: list[dict[str, Any]], source_rows: list[dict[str, Any]],
                      primary_sha: str, independent_sha: str,
                      resolution: dict[str, dict[str, Any]], seen: set[str]) -> None:
    source_by_id = {row["claim_id"]: row for row in source_rows}
    for row in reconciled:
        claim_id = row["claim_id"]
        if claim_id not in resolution:
            continue
        approved = resolution[claim_id]
        if (claim_id in seen or row["role"] != "unresolved"
                or row["primary"]["role"] != "passage_exegesis"
                or row["independent"]["role"] != "other"
                or approved["primary_artifact_sha256"] != primary_sha
                or approved["independent_artifact_sha256"] != independent_sha):
            raise ValueError(f"disagreement resolution does not match model pair: {claim_id}")
        if approved["role"] == "passage_exegesis":
            expected_keys = base._passage_keys(row["primary"], source_by_id[claim_id])
            if approved["interpreted_passage_keys"] != expected_keys:
                raise ValueError(f"adjudicated passage keys differ from reviewed GPT reading: {claim_id}")
        row["role"] = approved["role"]
        row["interpreted_passage_keys"] = approved["interpreted_passage_keys"]
        row["passage_identity_status"] = (
            "adjudicated" if approved["role"] == "passage_exegesis"
            else "not_applicable" if approved["role"] == "other" else "disputed"
        )
        row["decision_basis"] = f"user_accepted_analyst_{approved['disposition']}"
        row["resolution_analysis_index"] = approved["analysis_index"]
        seen.add(claim_id)


def audit(original_root: Path, opus_root: Path, primary_root: Path,
          independent_root: Path, store: PostgresKnowledgeStore,
          *, output_path: Path | None = None,
          resolution_path: Path | None = None) -> dict[str, Any]:
    packet, opus_manifest = audited._check_resume(original_root, opus_root)
    primary_packet, primary_manifest = primary_queue._check_manifest(
        original_root, opus_root, primary_root, store,
    )
    independent_packet, independent_manifest = independent_queue._check_manifest(
        original_root, opus_root, independent_root, store,
    )
    if (primary_packet["artifact_sha256"] != packet["artifact_sha256"]
            or independent_packet["artifact_sha256"] != packet["artifact_sha256"]):
        raise ValueError("role queues do not share the frozen packet")
    resolution_artifact, resolution = (
        _resolution_rows(resolution_path, packet) if resolution_path else (None, {})
    )
    resolution_seen: set[str] = set()
    total_batches = math.ceil(len(packet["claims"]) / audited.BATCH_SIZE)
    missing: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    review_shas: dict[str, str] = {}
    contiguous_batches = 0
    for batch_id in range(1, total_batches + 1):
        primary_path, independent_path, independent_model = artifact_paths(
            batch_id, original_root=original_root, opus_root=opus_root,
            primary_root=primary_root, independent_root=independent_root,
        )
        absent = [role for role, path in (("primary", primary_path),
                                         ("independent", independent_path)) if not path.exists()]
        if absent:
            missing.append({"batch_id": batch_id, "roles": absent})
            continue
        rows = audited._rows_for_batch(packet, batch_id)
        primary, primary_sha = audited._checked_decisions(
            primary_path, packet, batch_id, "primary", audited.PRIMARY_MODEL,
        )
        independent, independent_sha = audited._checked_decisions(
            independent_path, packet, batch_id, "independent", independent_model,
        )
        reconciled = base.reconcile(primary, independent, rows)
        if [row["claim_id"] for row in reconciled] != sorted(row["claim_id"] for row in rows):
            raise ValueError("reconciled Claim ownership differs from frozen packet")
        _apply_resolution(reconciled, rows, primary_sha, independent_sha,
                          resolution, resolution_seen)
        decisions.extend(reconciled)
        review_shas[f"primary-{batch_id:05d}"] = primary_sha
        review_shas[f"independent-{batch_id:05d}"] = independent_sha
        if not missing:
            contiguous_batches = batch_id
    if resolution_seen != set(resolution):
        raise ValueError("disagreement resolution was not fully applied to its frozen paired scope")
    counts = dict(sorted(Counter(row["role"] for row in decisions).items()))
    result = {
        "packet_sha256": packet["artifact_sha256"],
        "total_claims": len(packet["claims"]),
        "total_batches": total_batches,
        "paired_claims": len(decisions),
        "paired_batches": len(review_shas) // 2,
        "contiguous_through_batch": contiguous_batches,
        "missing_batch_count": len(missing),
        "missing_role_artifact_count": sum(len(item["roles"]) for item in missing),
        "first_missing": missing[:5],
        "counts": counts,
    }
    if resolution_artifact:
        result["disagreement_resolution_sha256"] = resolution_artifact["artifact_sha256"]
        result["disagreement_resolution_applied_claims"] = len(resolution_seen)
    if output_path is None:
        return result
    if missing:
        raise ValueError(f"refusing incomplete role ledger: {result['missing_batch_count']} batches missing")
    decision_ids = [row["claim_id"] for row in decisions]
    packet_ids = [row["claim_id"] for row in packet["claims"]]
    if (len(decision_ids) != len(packet_ids)
            or len(set(decision_ids)) != len(decision_ids)
            or set(decision_ids) != set(packet_ids)):
        raise ValueError("final Claim denominator or uniqueness differs")
    audited._check_graph(packet, store)
    ledger_body = {
        "schema_version": "wang_claim_passage_role_ledger_v6",
        "status": "all_eligible_reviewed",
        "packet_sha256": packet["artifact_sha256"],
        "opus_resume_manifest_sha256": opus_manifest["artifact_sha256"],
        "primary_prefetch_manifest_sha256": primary_manifest["artifact_sha256"],
        "independent_prefetch_manifest_sha256": independent_manifest["artifact_sha256"],
        "assembler_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "review_artifact_shas": review_shas,
        "batch_size": audited.BATCH_SIZE,
        "decisions": decisions,
        "counts": counts,
    }
    if resolution_artifact:
        ledger_body["disagreement_resolution_sha256"] = resolution_artifact["artifact_sha256"]
    ledger = base._artifact(ledger_body)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    base._write_immutable(output_path, ledger)
    return result | {"ledger_sha256": ledger["artifact_sha256"], "output_path": str(output_path)}


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-root", type=Path, required=True)
    parser.add_argument("--opus-root", type=Path, required=True)
    parser.add_argument("--primary-root", type=Path, required=True)
    parser.add_argument("--independent-root", type=Path, required=True)
    parser.add_argument("--output-path", type=Path)
    parser.add_argument("--resolution-path", type=Path)
    args = parser.parse_args()
    result = audit(args.original_root, args.opus_root, args.primary_root,
                   args.independent_root, PostgresKnowledgeStore(),
                   output_path=args.output_path,
                   resolution_path=args.resolution_path)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
