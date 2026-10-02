"""Inventory the post-637 #409 role disagreements without adjudicating them."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from backend.pipeline import claim_passage_role_audited_resume as audited
from backend.pipeline import claim_passage_role_runner as base
from backend.pipeline.claim_passage_role_ledger_assembler import artifact_paths


FIRST_BATCH = 638
LAST_BATCH = 798


def build_inventory(original_root: Path, opus_root: Path, primary_root: Path,
                    independent_root: Path) -> dict:
    packet, _ = audited._check_resume(original_root, opus_root)
    claims = {row["claim_id"]: row for row in packet["claims"]}
    entries = []
    both_unresolved = []
    for batch_id in range(FIRST_BATCH, LAST_BATCH + 1):
        primary_path, independent_path, independent_model = artifact_paths(
            batch_id, original_root=original_root, opus_root=opus_root,
            primary_root=primary_root, independent_root=independent_root,
        )
        primary, primary_sha = audited._checked_decisions(
            primary_path, packet, batch_id, "primary", audited.PRIMARY_MODEL,
        )
        independent, independent_sha = audited._checked_decisions(
            independent_path, packet, batch_id, "independent", independent_model,
        )
        for gpt, opus in zip(primary, independent, strict=True):
            if gpt["claim_id"] != opus["claim_id"]:
                raise ValueError(f"batch {batch_id} Claim order differs")
            claim = claims[gpt["claim_id"]]
            if gpt["role"] == opus["role"]:
                if gpt["role"] == "unresolved":
                    both_unresolved.append(gpt["claim_id"])
                continue
            entries.append({
                "index": len(entries) + 1,
                "batch_id": batch_id,
                "claim_id": claim["claim_id"],
                "claim_content_sha256": claim["claim_content_sha256"],
                "source_id": claim["source_id"],
                "source_content_sha256": claim["source_content_sha256"],
                "statement": claim["statement"],
                "scripture_refs": claim["scripture_refs"],
                "evidence_steps": claim["evidence_steps"],
                "primary": gpt,
                "independent": opus,
                "primary_artifact_sha256": primary_sha,
                "independent_artifact_sha256": independent_sha,
            })
    if len(entries) != 177 or len(both_unresolved) != 44:
        raise ValueError("post-637 role disagreement denominator changed")
    return base._artifact({
        "schema_version": "wang_claim_passage_role_new_disagreement_inventory_v1",
        "status": "inventory_not_adjudication",
        "batch_scope": [FIRST_BATCH, LAST_BATCH],
        "packet_sha256": packet["artifact_sha256"],
        "packet_file_sha256": hashlib.sha256(
            (original_root / "role-packet.json").read_bytes()).hexdigest(),
        "counts_by_pair": dict(sorted(Counter(
            f"{row['primary']['role']}|{row['independent']['role']}" for row in entries
        ).items())),
        "both_unresolved_claim_ids": both_unresolved,
        "entries": entries,
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-root", type=Path, required=True)
    parser.add_argument("--opus-root", type=Path, required=True)
    parser.add_argument("--primary-root", type=Path, required=True)
    parser.add_argument("--independent-root", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    args = parser.parse_args()
    artifact = build_inventory(args.original_root, args.opus_root,
                               args.primary_root, args.independent_root)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    base._write_immutable(args.output_path, artifact)
    print(json.dumps({
        "output_path": str(args.output_path),
        "artifact_sha256": artifact["artifact_sha256"],
        "counts_by_pair": artifact["counts_by_pair"],
        "both_unresolved": len(artifact["both_unresolved_claim_ids"]),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
