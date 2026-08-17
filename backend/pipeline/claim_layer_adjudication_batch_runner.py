"""Combine claim-layer adjudication batches without additional model calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.pipeline.claim_layer_adjudication_batch import merge_adjudication_artifacts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--adjudication", required=True, action="append", type=Path)
    parser.add_argument(
        "--override", required=True, action="append", type=Path,
        help="Application-ready override artifact paired with each adjudication batch.",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--overrides-output", required=True, type=Path)
    args = parser.parse_args()

    review = json.loads(args.review.read_text(encoding="utf-8"))
    actionable_ids = [
        row["claim_id"]
        for row in review.get("claim_reviews") or []
        if row.get("decision") != "pass"
    ]
    artifacts = [
        json.loads(path.read_text(encoding="utf-8")) for path in args.adjudication
    ]
    override_artifacts = [
        json.loads(path.read_text(encoding="utf-8")) for path in args.override
    ]
    combined = merge_adjudication_artifacts(
        artifacts,
        expected_actionable_claim_ids=actionable_ids,
        override_artifacts=override_artifacts,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.overrides_output.write_text(
        json.dumps(
            {
                "schema_version": "wang_claim_statement_overrides_batched_v1",
                "adjudication_fingerprint": combined["fingerprint_sha256"],
                "claims": combined["claim_overrides"],
                "approval_status": "not_human_approved",
                "note": combined["note"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "created",
                "output": str(args.output),
                "overrides_output": str(args.overrides_output),
                "summary": combined["summary"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
