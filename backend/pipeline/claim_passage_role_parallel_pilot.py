"""Two disjoint 16-Claim workers, with independent model calls in parallel.

This #409 experiment only re-reviews already completed batches. Each worker
has a separate output root. The frozen production roots are read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.pipeline import claim_passage_role_audited_resume as audited
from backend.pipeline import claim_passage_role_runner as base
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient


def _source_batch(packet: dict[str, Any], baseline_root: Path,
                  batch_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    if batch_id < audited.CUTOVER_BATCH:
        raise ValueError("pilot requires completed Opus-era batches")
    rows = audited._rows_for_batch(packet, batch_id)
    old_primary, primary_sha = audited._checked_decisions(
        baseline_root / f"primary-{batch_id:05d}.json", packet, batch_id,
        "primary", audited.PRIMARY_MODEL,
    )
    old_independent, independent_sha = audited._checked_decisions(
        baseline_root / f"independent-{batch_id:05d}.json", packet, batch_id,
        "independent", audited.NEW_INDEPENDENT_MODEL,
    )
    return rows, base.reconcile(old_primary, old_independent, rows), {
        "primary": primary_sha, "independent": independent_sha,
    }


def preflight(original_root: Path, baseline_root: Path,
              batch_ids: tuple[int, int], store: PostgresKnowledgeStore) -> dict[str, Any]:
    if len(set(batch_ids)) != 2:
        raise ValueError("two workers need disjoint batch IDs")
    packet, _ = audited._check_resume(original_root, baseline_root)
    audited._check_graph(packet, store)
    baselines = {}
    for batch_id in batch_ids:
        rows, decisions, shas = _source_batch(packet, baseline_root, batch_id)
        if len(rows) != audited.BATCH_SIZE:
            raise ValueError("pilot batch must contain exactly 16 Claims")
        baselines[str(batch_id)] = {
            "claim_ids": [row["claim_id"] for row in rows],
            "review_artifact_shas": shas,
            "roles": dict(sorted(Counter(row["role"] for row in decisions).items())),
        }
    first_ids = set(baselines[str(batch_ids[0])]["claim_ids"])
    second_ids = set(baselines[str(batch_ids[1])]["claim_ids"])
    if first_ids & second_ids:
        raise ValueError("worker Claim ownership overlaps")
    return {
        "packet_sha256": packet["artifact_sha256"],
        "baseline_manifest_sha256": base._read_json(baseline_root / "resume-manifest.json")["artifact_sha256"],
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "batch_ids": list(batch_ids),
        "workers": baselines,
        "primary_model": audited.PRIMARY_MODEL,
        "independent_model": audited.NEW_INDEPENDENT_MODEL,
    }


def review_pair_parallel(*, rows: list[dict[str, Any]], packet_sha: str,
                         manifest_sha: str, batch_id: int, output_root: Path,
                         primary_client: Any, independent_client: Any) -> tuple[list[dict[str, Any]], float]:
    """Two calls see only the same source packet, never each other's answer."""

    tasks = {
        "primary": primary_client,
        "independent": independent_client,
    }
    started = time.monotonic()
    results: dict[str, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(
                audited._response_for_batch, role, rows, client=client,
                packet_sha=packet_sha, manifest_sha=manifest_sha,
                batch_id=batch_id, output_root=output_root,
            ): role
            for role, client in tasks.items()
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return base.reconcile(results["primary"], results["independent"], rows), time.monotonic() - started


def _worker(batch_id: int, *, packet: dict[str, Any], baseline_root: Path,
            experiment_root: Path, experiment_manifest_sha: str) -> dict[str, Any]:
    rows, baseline, baseline_shas = _source_batch(packet, baseline_root, batch_id)
    output_root = experiment_root / f"worker-batch-{batch_id:05d}"
    output_root.mkdir(exist_ok=False)
    worker_manifest = base._artifact({
        "schema_version": "wang_claim_passage_role_parallel_worker_pilot_v1",
        "experiment_manifest_sha256": experiment_manifest_sha,
        "packet_sha256": packet["artifact_sha256"],
        "batch_id": batch_id,
        "claim_ids": [row["claim_id"] for row in rows],
        "baseline_artifact_shas": baseline_shas,
        "primary_model": audited.PRIMARY_MODEL,
        "independent_model": audited.NEW_INDEPENDENT_MODEL,
    })
    base._write_immutable(output_root / "worker-manifest.json", worker_manifest)
    primary = CodexSubscriptionClient(model=audited.PRIMARY_MODEL, reasoning_effort="high")
    independent = ClaudeSubscriptionClient(model=audited.NEW_INDEPENDENT_MODEL,
                                            reasoning_effort="high")
    reviewed, elapsed = review_pair_parallel(
        rows=rows, packet_sha=packet["artifact_sha256"],
        manifest_sha=worker_manifest["artifact_sha256"], batch_id=batch_id,
        output_root=output_root, primary_client=primary, independent_client=independent,
    )
    old_by_id = {row["claim_id"]: row for row in baseline}
    changes = [
        {"claim_id": row["claim_id"],
         "baseline_role": old_by_id[row["claim_id"]]["role"],
         "pilot_role": row["role"],
         "baseline_passage_identity": old_by_id[row["claim_id"]]["passage_identity_status"],
         "pilot_passage_identity": row["passage_identity_status"],
         "baseline_passage_keys": old_by_id[row["claim_id"]]["interpreted_passage_keys"],
         "pilot_passage_keys": row["interpreted_passage_keys"]}
        for row in reviewed
        if (row["role"], row["passage_identity_status"], row["interpreted_passage_keys"])
        != (old_by_id[row["claim_id"]]["role"],
            old_by_id[row["claim_id"]]["passage_identity_status"],
            old_by_id[row["claim_id"]]["interpreted_passage_keys"])
    ]
    result = base._artifact({
        "schema_version": "wang_claim_passage_role_parallel_worker_report_v1",
        "worker_manifest_sha256": worker_manifest["artifact_sha256"],
        "batch_id": batch_id, "elapsed_seconds": round(elapsed, 3),
        "baseline_roles": dict(sorted(Counter(row["role"] for row in baseline).items())),
        "pilot_roles": dict(sorted(Counter(row["role"] for row in reviewed).items())),
        "changes": changes,
    })
    base._write_immutable(output_root / "worker-report.json", result)
    print(json.dumps({"batch_id": batch_id, "elapsed_seconds": round(elapsed, 3),
                      "changed_claim_count": len(changes)}), flush=True)
    return result


def run(original_root: Path, baseline_root: Path, experiment_root: Path,
        batch_ids: tuple[int, int], store: PostgresKnowledgeStore) -> dict[str, Any]:
    if experiment_root.exists():
        raise ValueError("parallel pilot output root already exists")
    plan = preflight(original_root, baseline_root, batch_ids, store)
    packet = audited._check_packet(original_root)
    experiment_root.mkdir(parents=True, exist_ok=False)
    manifest = base._artifact({"schema_version": "wang_claim_passage_role_parallel_pilot_v1"} | plan)
    base._write_immutable(experiment_root / "pilot-manifest.json", manifest)
    started = time.monotonic()
    results: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(
                _worker, batch_id, packet=packet, baseline_root=baseline_root,
                experiment_root=experiment_root,
                experiment_manifest_sha=manifest["artifact_sha256"],
            ): batch_id
            for batch_id in batch_ids
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    audited._check_graph(packet, store)
    summary = base._artifact({
        "schema_version": "wang_claim_passage_role_parallel_pilot_summary_v1",
        "pilot_manifest_sha256": manifest["artifact_sha256"],
        "wall_seconds": round(time.monotonic() - started, 3),
        "workers": {str(batch_id): results[batch_id]["artifact_sha256"]
                    for batch_id in batch_ids},
        "changed_claim_count": sum(len(results[batch_id]["changes"])
                                   for batch_id in batch_ids),
    })
    base._write_immutable(experiment_root / "summary.json", summary)
    return summary


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-root", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--experiment-root", type=Path)
    parser.add_argument("--batch-id", type=int, action="append", required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if len(args.batch_id) != 2:
        parser.error("specify exactly two --batch-id values")
    batch_ids = (args.batch_id[0], args.batch_id[1])
    store = PostgresKnowledgeStore()
    if args.preflight:
        result = preflight(args.original_root, args.baseline_root, batch_ids, store)
    else:
        if args.experiment_root is None:
            parser.error("--experiment-root is required to run model calls")
        result = run(args.original_root, args.baseline_root, args.experiment_root,
                     batch_ids, store)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
