"""Compare completed Sonnet pilot artifacts with frozen GPT and Opus answers."""

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
from backend.pipeline import claim_passage_role_sonnet_pilot as pilot


def compare(root: Path, output_root: Path, store: PostgresKnowledgeStore) -> dict:
    packet, manifest = pilot._checked(root, output_root, store)
    original, opus_root, primary_root, independent_root = pilot._paths(root)
    counts = {
        "claims": 0, "sonnet_opus_role_agree": 0, "sonnet_gpt_role_agree": 0,
        "sonnet_opus_passage_agree": 0, "sonnet_gpt_passage_agree": 0,
        "three_way_role_agree": 0, "gpt_opus_role_agree": 0,
    }
    differences = []
    for batch_id in pilot.BATCHES:
        rows = audited._rows_for_batch(packet, batch_id)
        by_id = {row["claim_id"]: row for row in rows}
        primary_path, independent_path, reviewer_model = ledger.artifact_paths(
            batch_id, original_root=original, opus_root=opus_root,
            primary_root=primary_root, independent_root=independent_root,
        )
        gpt, _ = audited._checked_decisions(
            primary_path, packet, batch_id, "primary", audited.PRIMARY_MODEL,
        )
        opus, _ = audited._checked_decisions(
            independent_path, packet, batch_id, "independent", reviewer_model,
        )
        sonnet, _ = audited._checked_decisions(
            output_root / f"independent-{batch_id:05d}.json", packet,
            batch_id, "independent", pilot.MODEL,
        )
        for g, o, s in zip(gpt, opus, sonnet, strict=True):
            if g["claim_id"] != o["claim_id"] or g["claim_id"] != s["claim_id"]:
                raise ValueError("reviewer Claim order differs")
            row = by_id[g["claim_id"]]
            g_refs = base._passage_keys(g, row)
            o_refs = base._passage_keys(o, row)
            s_refs = base._passage_keys(s, row)
            counts["claims"] += 1
            counts["sonnet_opus_role_agree"] += s["role"] == o["role"]
            counts["sonnet_gpt_role_agree"] += s["role"] == g["role"]
            counts["sonnet_opus_passage_agree"] += s_refs == o_refs
            counts["sonnet_gpt_passage_agree"] += s_refs == g_refs
            counts["three_way_role_agree"] += s["role"] == o["role"] == g["role"]
            counts["gpt_opus_role_agree"] += g["role"] == o["role"]
            if s["role"] != o["role"] or s_refs != o_refs or g["role"] != o["role"]:
                differences.append({
                    "batch_id": batch_id, "claim_id": row["claim_id"],
                    "gpt_role": g["role"], "opus_role": o["role"], "sonnet_role": s["role"],
                    "gpt_refs": g_refs, "opus_refs": o_refs, "sonnet_refs": s_refs,
                    "gpt_reason": g["reason"], "opus_reason": o["reason"],
                    "sonnet_reason": s["reason"],
                })
    body = {
        "schema_version": "wang_claim_passage_role_sonnet_pilot_comparison_v2",
        "pilot_manifest_sha256": manifest["artifact_sha256"],
        "comparison_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "counts": counts, "differences": differences,
    }
    result = base._artifact(body)
    base._write_immutable(output_root / "comparison-v2.json", result)
    return {"counts": counts, "difference_count": len(differences),
            "comparison_sha256": result["artifact_sha256"]}


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.root, args.output_root, PostgresKnowledgeStore())
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
