"""#409 GPT-only queue; independent review may consume these immutable artifacts later.

Each worker owns disjoint frozen 16-Claim batches. No Claude call, reconciliation,
ledger, or database write occurs here. The existing Opus root is read-only.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.pipeline import claim_passage_role_audited_resume as audited
from backend.pipeline import claim_passage_role_runner as base
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient


FIRST_BATCH = 83
LANES = ("a", "b")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _checkpoint(packet: dict[str, Any], opus_root: Path) -> dict[str, str]:
    """Pin all earlier GPT work, and reject competing future artifacts."""
    expected = {f"primary-{batch_id:05d}.json" for batch_id in range(70, FIRST_BATCH)}
    expected |= {f"independent-{batch_id:05d}.json" for batch_id in range(69, FIRST_BATCH - 1)}
    actual = {p.name for p in opus_root.glob("primary-*.json")}
    actual |= {p.name for p in opus_root.glob("independent-*.json")}
    if actual != expected:
        raise ValueError("Opus checkpoint changed or future output overlaps GPT queue")
    shas = {}
    for name in sorted(expected):
        role, number = name.removesuffix(".json").split("-")
        model = audited.PRIMARY_MODEL if role == "primary" else audited.NEW_INDEPENDENT_MODEL
        _, shas[name] = audited._checked_decisions(opus_root / name, packet, int(number), role, model)
    return shas


def preflight(original_root: Path, opus_root: Path, store: PostgresKnowledgeStore) -> dict[str, Any]:
    packet, opus_manifest = audited._check_resume(original_root, opus_root)
    audited._check_graph(packet, store)
    checkpoint = _checkpoint(packet, opus_root)
    total = math.ceil(len(packet["claims"]) / audited.BATCH_SIZE)
    if FIRST_BATCH > total:
        raise ValueError("nothing left to prefetch")
    return {
        "packet_sha256": packet["artifact_sha256"],
        "opus_manifest_sha256": opus_manifest["artifact_sha256"],
        "opus_checkpoint_shas": checkpoint,
        "original_root": str(original_root.resolve()),
        "opus_root": str(opus_root.resolve()),
        "code_sha256": _sha(Path(__file__)),
        "prompt_sha256": packet["prompt_sha256"],
        "first_batch": FIRST_BATCH,
        "total_batches": total,
        "batch_size": audited.BATCH_SIZE,
        "primary_model": audited.PRIMARY_MODEL,
        "lanes": list(LANES),
    }


def prepare(original_root: Path, opus_root: Path, output_root: Path,
            store: PostgresKnowledgeStore) -> dict[str, Any]:
    if output_root.exists():
        raise ValueError("new GPT-only output root already exists")
    plan = preflight(original_root, opus_root, store)
    manifest = base._artifact({"schema_version": "wang_claim_passage_role_gpt_prefetch_v1"} | plan)
    output_root.mkdir(parents=True, exist_ok=False)
    base._write_immutable(output_root / "prefetch-manifest.json", manifest)
    for lane in LANES:
        (output_root / f"worker-{lane}").mkdir(exist_ok=False)
    return {"output_root": str(output_root), "manifest_sha256": manifest["artifact_sha256"]}


def _check_manifest(original_root: Path, opus_root: Path, output_root: Path,
                    store: PostgresKnowledgeStore) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = preflight(original_root, opus_root, store)
    manifest = base._read_json(output_root / "prefetch-manifest.json")
    base._check_artifact(manifest)
    if manifest != base._artifact({"schema_version": "wang_claim_passage_role_gpt_prefetch_v1"} | plan):
        raise ValueError("GPT-only manifest or frozen checkpoint changed")
    packet = audited._check_packet(original_root)
    return packet, manifest


def _lane_for(batch_id: int) -> str:
    return LANES[(batch_id - FIRST_BATCH) % len(LANES)]


def _run_one(batch_id: int, *, packet: dict[str, Any], manifest: dict[str, Any],
             output_root: Path, client: Any) -> dict[str, Any]:
    lane = _lane_for(batch_id)
    lane_root = output_root / f"worker-{lane}"
    rows = audited._rows_for_batch(packet, batch_id)
    path = lane_root / f"primary-{batch_id:05d}.json"
    if path.exists():
        decisions, sha = audited._checked_decisions(path, packet, batch_id,
                                                     "primary", audited.PRIMARY_MODEL)
    else:
        decisions = audited._response_for_batch(
            "primary", rows, client=client, packet_sha=packet["artifact_sha256"],
            manifest_sha=manifest["artifact_sha256"], batch_id=batch_id,
            output_root=lane_root,
        )
        _, sha = audited._checked_decisions(path, packet, batch_id,
                                            "primary", audited.PRIMARY_MODEL)
    return {"batch_id": batch_id, "lane": lane, "claims": len(decisions), "artifact_sha256": sha}


def run(original_root: Path, opus_root: Path, output_root: Path,
        store: PostgresKnowledgeStore, *, last_batch: int,
        client_factory: Any = None) -> dict[str, Any]:
    packet, manifest = _check_manifest(original_root, opus_root, output_root, store)
    if not FIRST_BATCH <= last_batch <= manifest["total_batches"]:
        raise ValueError("last batch outside GPT-only queue")
    with (output_root / ".prefetch.lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # A batch has exactly one owner; each client stays in its worker thread.
        def worker(lane: str) -> list[dict[str, Any]]:
            client = ((client_factory or (lambda: CodexSubscriptionClient(
                model=audited.PRIMARY_MODEL, reasoning_effort="high")))())
            done = []
            for batch_id in range(FIRST_BATCH, last_batch + 1):
                if _lane_for(batch_id) == lane:
                    result = _run_one(batch_id, packet=packet, manifest=manifest,
                                      output_root=output_root, client=client)
                    print(json.dumps(result, sort_keys=True), flush=True)
                    done.append(result)
            return done

        results = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker, lane) for lane in LANES]
            for future in as_completed(futures):
                results.extend(future.result())
        audited._check_graph(packet, store)
        if sorted(row["batch_id"] for row in results) != list(range(FIRST_BATCH, last_batch + 1)):
            raise ValueError("GPT-only queue has a batch gap or overlap")
        return {"completed_batches": len(results), "through_batch": last_batch,
                "claims": sum(row["claims"] for row in results),
                "manifest_sha256": manifest["artifact_sha256"]}


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-root", required=True, type=Path)
    parser.add_argument("--opus-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--last-batch", type=int)
    args = parser.parse_args()
    store = PostgresKnowledgeStore()
    if args.preflight:
        result = preflight(args.original_root, args.opus_root, store)
    elif args.prepare:
        result = prepare(args.original_root, args.opus_root, args.output_root, store)
    else:
        if args.last_batch is None:
            parser.error("--last-batch is required for bounded GPT-only execution")
        result = run(args.original_root, args.opus_root, args.output_root,
                     store, last_batch=args.last_batch)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
