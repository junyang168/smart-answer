"""Compile the post-637 #409 analyst adjudication as a bounded role overlay.

No model output, Claim, source, or Registry record is modified.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from backend.pipeline import claim_passage_role_audited_resume as audited
from backend.pipeline import claim_passage_role_new_disagreement_analysis as analysis
from backend.pipeline import claim_passage_role_runner as base


def compile_resolution(analysis_path: Path, inventory_path: Path,
                       original_root: Path, opus_root: Path, primary_root: Path,
                       independent_root: Path) -> dict:
    supplied = base._read_json(analysis_path)
    base._check_artifact(supplied)
    expected = analysis.build_analysis(inventory_path, original_root, opus_root,
                                       primary_root, independent_root)
    if supplied != expected:
        raise ValueError("new disagreement analysis differs from frozen inputs")
    packet, _ = audited._check_resume(original_root, opus_root)
    claim_by_id = {row["claim_id"]: row for row in packet["claims"]}
    source_inventory = base._read_json(inventory_path)
    by_index = {row["index"]: row for row in source_inventory["entries"]}
    decisions = []
    for recommendation in supplied["decisions"]:
        original = by_index[recommendation["index"]]
        claim = claim_by_id[recommendation["claim_id"]]
        role = recommendation["role"]
        if role == "passage_exegesis":
            reviewer = (original["primary"] if original["primary"]["role"] == role
                        else original["independent"])
            if reviewer["role"] != role:
                raise ValueError("selected exegesis has no reviewed passage reading")
            passage_keys = base._passage_keys(reviewer, claim)
            if not passage_keys:
                raise ValueError(f"selected exegesis lacks passage keys: {claim['claim_id']}")
        else:
            passage_keys = []
        decisions.append(recommendation | {
            "interpreted_passage_keys": passage_keys,
        })
    if len(decisions) != 177 or len({row["claim_id"] for row in decisions}) != 177:
        raise ValueError("new disagreement resolution denominator changed")
    return base._artifact({
        "schema_version": "wang_claim_passage_role_new_disagreement_resolution_v1",
        "status": "analyst_adjudicated_under_user_instruction_partial_coverage",
        "instruction": supplied["instruction"],
        "packet_sha256": packet["artifact_sha256"],
        "inventory_sha256": source_inventory["artifact_sha256"],
        "analysis_sha256": supplied["artifact_sha256"],
        "paired_batch_scope": [638, 798],
        "counts_by_disposition": dict(sorted(Counter(row["disposition"] for row in decisions).items())),
        "counts_by_role": dict(sorted(Counter(row["role"] for row in decisions).items())),
        "decisions": decisions,
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-path", type=Path, required=True)
    parser.add_argument("--inventory-path", type=Path, required=True)
    parser.add_argument("--original-root", type=Path, required=True)
    parser.add_argument("--opus-root", type=Path, required=True)
    parser.add_argument("--primary-root", type=Path, required=True)
    parser.add_argument("--independent-root", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    args = parser.parse_args()
    artifact = compile_resolution(args.analysis_path, args.inventory_path,
                                  args.original_root, args.opus_root,
                                  args.primary_root, args.independent_root)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    base._write_immutable(args.output_path, artifact)
    print(json.dumps({"artifact_sha256": artifact["artifact_sha256"],
                      "counts_by_role": artifact["counts_by_role"],
                      "counts_by_disposition": artifact["counts_by_disposition"],
                      "output_path": str(args.output_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
