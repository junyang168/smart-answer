"""Merge detailed knowledge packages into one mechanically verified review batch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.pipeline.knowledge_package_merge import merge_packages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, required=True)
    parser.add_argument("--package-id", required=True)
    parser.add_argument(
        "--neutral-batch-id",
        help=(
            "Declare a neutral comparison batch. Input selection does not imply "
            "that the selected claims are equivalent."
        ),
    )
    parser.add_argument("--purpose", help="Human-readable reason for the neutral comparison")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if bool(args.neutral_batch_id) != bool(args.purpose):
        parser.error("--neutral-batch-id and --purpose must be supplied together")
    batch = None
    if args.neutral_batch_id:
        batch = {
            "batch_id": args.neutral_batch_id,
            "purpose": args.purpose,
            "semantic_assumption": "none",
            "selection_is_not_classification": True,
        }
    package = merge_packages(args.input, package_id=args.package_id, batch=batch)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(package, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output), **package["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
