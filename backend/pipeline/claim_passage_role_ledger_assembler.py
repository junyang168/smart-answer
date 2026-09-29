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


def audit(original_root: Path, opus_root: Path, primary_root: Path,
          independent_root: Path, store: PostgresKnowledgeStore,
          *, output_path: Path | None = None) -> dict[str, Any]:
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
        decisions.extend(reconciled)
        review_shas[f"primary-{batch_id:05d}"] = primary_sha
        review_shas[f"independent-{batch_id:05d}"] = independent_sha
        if not missing:
            contiguous_batches = batch_id
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
    ledger = base._artifact({
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
    })
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
    args = parser.parse_args()
    result = audit(args.original_root, args.opus_root, args.primary_root,
                   args.independent_root, PostgresKnowledgeStore(),
                   output_path=args.output_path)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
