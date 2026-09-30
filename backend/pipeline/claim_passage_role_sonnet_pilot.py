"""Bounded, blind Sonnet 5.5 comparison against completed #409 reviews.

This is a read-only database workflow. It writes only immutable pilot artifacts
under a new output root and cannot advance the production review queue.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.pipeline import claim_passage_role_audited_resume as audited
from backend.pipeline import claim_passage_role_ledger_assembler as ledger
from backend.pipeline import claim_passage_role_runner as base
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient


MODEL = "claude-sonnet-5-5"
BATCHES = (102, 131, 266, 297)
MANIFEST_VERSION = "wang_claim_passage_role_sonnet_pilot_v1"


def _paths(root: Path) -> tuple[Path, Path, Path, Path]:
    return (
        root / "full-freeze-20260928-v5",
        root / "opus55-from-00069-v1",
        root / "primary-prefetch-from-00083-v1",
        root / "independent-prefetch-from-00082-v1",
    )


def _plan(root: Path, store: PostgresKnowledgeStore) -> dict:
    original, opus_root, primary, independent = _paths(root)
    packet, _ = audited._check_resume(original, opus_root)
    audited._check_graph(packet, store)
    baselines = {}
    for batch_id in BATCHES:
        primary_path, independent_path, reviewer_model = ledger.artifact_paths(
            batch_id, original_root=original, opus_root=opus_root,
            primary_root=primary, independent_root=independent,
        )
        for role, path, model in (
            ("primary", primary_path, audited.PRIMARY_MODEL),
            ("independent", independent_path, reviewer_model),
        ):
            _, sha = audited._checked_decisions(path, packet, batch_id, role, model)
            baselines[f"{role}-{batch_id:05d}"] = sha
    return {
        "schema_version": MANIFEST_VERSION,
        "packet_sha256": packet["artifact_sha256"],
        "prompt_sha256": packet["prompt_sha256"],
        "pilot_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "model": MODEL,
        "reasoning_effort": "high",
        "batch_ids": list(BATCHES),
        "baseline_artifact_shas": baselines,
        "claim_count": sum(len(audited._rows_for_batch(packet, n)) for n in BATCHES),
    }


def prepare(root: Path, output_root: Path, store: PostgresKnowledgeStore) -> dict:
    if output_root.exists():
        raise ValueError("pilot output root already exists")
    manifest = base._artifact(_plan(root, store))
    output_root.mkdir(parents=True, exist_ok=False)
    base._write_immutable(output_root / "pilot-manifest.json", manifest)
    return {"output_root": str(output_root), "manifest_sha256": manifest["artifact_sha256"]}


def _checked(root: Path, output_root: Path, store: PostgresKnowledgeStore) -> tuple[dict, dict]:
    original, _, _, _ = _paths(root)
    packet = audited._check_packet(original)
    manifest = base._read_json(output_root / "pilot-manifest.json")
    base._check_artifact(manifest)
    if manifest != base._artifact(_plan(root, store)):
        raise ValueError("pilot source graph, baseline, prompt, code, or manifest changed")
    return packet, manifest


def run(root: Path, output_root: Path, store: PostgresKnowledgeStore) -> dict:
    packet, manifest = _checked(root, output_root, store)
    client = ClaudeSubscriptionClient(model=MODEL, reasoning_effort="high")
    done = []
    for batch_id in BATCHES:
        path = output_root / f"independent-{batch_id:05d}.json"
        if path.exists():
            decisions, sha = audited._checked_decisions(path, packet, batch_id, "independent", MODEL)
        else:
            decisions = audited._response_for_batch(
                "independent", audited._rows_for_batch(packet, batch_id),
                client=client, packet_sha=packet["artifact_sha256"],
                manifest_sha=manifest["artifact_sha256"], batch_id=batch_id,
                output_root=output_root,
            )
            _, sha = audited._checked_decisions(path, packet, batch_id, "independent", MODEL)
        row = {"batch_id": batch_id, "claims": len(decisions), "artifact_sha256": sha}
        print(json.dumps(row, sort_keys=True), flush=True)
        done.append(row)
    audited._check_graph(packet, store)
    return {"completed_batches": len(done), "claims": sum(row["claims"] for row in done)}


def report(root: Path, output_root: Path, store: PostgresKnowledgeStore) -> dict:
    packet, manifest = _checked(root, output_root, store)
    original, opus_root, primary, independent = _paths(root)
    counts = {"claims": 0, "sonnet_opus_role_agree": 0, "sonnet_gpt_role_agree": 0,
              "sonnet_opus_passage_agree": 0, "sonnet_gpt_passage_agree": 0}
    differences = []
    for batch_id in BATCHES:
        rows = audited._rows_for_batch(packet, batch_id)
        primary_path, independent_path, reviewer_model = ledger.artifact_paths(
            batch_id, original_root=original, opus_root=opus_root,
            primary_root=primary, independent_root=independent,
        )
        gpt, _ = audited._checked_decisions(primary_path, packet, batch_id, "primary", audited.PRIMARY_MODEL)
        opus_decisions, _ = audited._checked_decisions(
            independent_path, packet, batch_id, "independent", reviewer_model,
        )
        sonnet, _ = audited._checked_decisions(
            output_root / f"independent-{batch_id:05d}.json", packet, batch_id, "independent", MODEL,
        )
        for source, a, b, c in zip(rows, gpt, opus_decisions, sonnet, strict=True):
            counts["claims"] += 1
            for label, comparison in (("opus", b), ("gpt", a)):
                counts[f"sonnet_{label}_role_agree"] += c["role"] == comparison["role"]
                counts[f"sonnet_{label}_passage_agree"] += (
                    base._passage_keys(c, source) == base._passage_keys(comparison, source)
                )
            if not (a["role"] == b["role"] == c["role"]
                    and base._passage_keys(a, source) == base._passage_keys(b, source)
                    == base._passage_keys(c, source)):
                differences.append({
                    "batch_id": batch_id, "claim_id": source["claim_id"],
                    "gpt_role": a["role"], "opus_role": b["role"], "sonnet_role": c["role"],
                    "gpt_refs": base._passage_keys(a, source),
                    "opus_refs": base._passage_keys(b, source),
                    "sonnet_refs": base._passage_keys(c, source),
                    "gpt_reason": a["reason"], "opus_reason": b["reason"],
                    "sonnet_reason": c["reason"],
                })
    result = base._artifact({
        "schema_version": "wang_claim_passage_role_sonnet_pilot_comparison_v1",
        "pilot_manifest_sha256": manifest["artifact_sha256"],
        "counts": counts, "differences": differences,
    })
    base._write_immutable(output_root / "comparison.json", result)
    return {"counts": counts, "difference_count": len(differences),
            "comparison_sha256": result["artifact_sha256"]}


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("prepare", "run", "report"))
    args = parser.parse_args()
    store = PostgresKnowledgeStore()
    result = (prepare(args.root, args.output_root, store) if args.mode == "prepare" else
              run(args.root, args.output_root, store) if args.mode == "run" else
              report(args.root, args.output_root, store))
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
