"""Create a deterministic, zero-call identity-review calibration sample."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.api.canonical_repository.viewpoint_identity_hypotheses import (
    IdentityEvidenceReviewPlan,
    build_identity_calibration_plan,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=24)
    parser.add_argument("--proposal-model", default="gpt-5.6-sol")
    parser.add_argument("--blind-model", default="claude-sonnet-5")
    args = parser.parse_args()
    plan = IdentityEvidenceReviewPlan.model_validate_json(
        args.evidence_plan.read_text(encoding="utf-8")
    )
    calibration = build_identity_calibration_plan(
        plan=plan,
        sample_size=args.sample_size,
        proposal_model_id=args.proposal_model,
        blind_model_id=args.blind_model,
    )
    payload = calibration.model_dump(mode="json")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        if existing != payload:
            raise ValueError(f"immutable artifact differs at {args.output}")
    else:
        temporary = args.output.with_suffix(args.output.suffix + ".partial")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(args.output)
    print(json.dumps({
        "output": str(args.output),
        "artifact_sha256": payload["artifact_sha256"],
        "strata": payload["strata"],
        **payload["statistics"],
        "model_calls_executed": 0,
        "master_data_mutations": 0,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
