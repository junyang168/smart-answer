"""Build a zero-call boundary holdout plan disjoint from prior calibration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.api.canonical_repository.viewpoint_identity_boundary import (
    build_identity_boundary_calibration_plan,
)
from backend.api.canonical_repository.viewpoint_identity_hypotheses import (
    IdentityCalibrationPlan,
    IdentityEvidenceReviewPlan,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-plan", type=Path, required=True)
    parser.add_argument("--exclude-plan", type=Path, action="append", required=True)
    parser.add_argument("--sample-size", type=int, default=12)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence_plan = IdentityEvidenceReviewPlan.model_validate_json(
        args.evidence_plan.read_text(encoding="utf-8")
    )
    excluded_ids: set[str] = set()
    exclusion_shas: list[str] = []
    for path in args.exclude_plan:
        plan = IdentityCalibrationPlan.model_validate_json(path.read_text(encoding="utf-8"))
        excluded_ids.update(plan.selected_hypothesis_ids)
        exclusion_shas.append(plan.artifact_sha256)
    result = build_identity_boundary_calibration_plan(
        evidence_plan=evidence_plan,
        sample_size=args.sample_size,
        excluded_hypothesis_ids=excluded_ids,
        exclusion_plan_sha256s=exclusion_shas,
    )
    payload = result.model_dump(mode="json")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        if json.loads(args.output.read_text(encoding="utf-8")) != payload:
            raise ValueError(f"immutable holdout plan differs at {args.output}")
    else:
        temporary = args.output.with_suffix(args.output.suffix + ".partial")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(args.output)
    print(json.dumps({
        "output": str(args.output),
        "artifact_sha256": result.artifact_sha256,
        "strata": result.strata,
        **result.statistics,
        "master_data_mutations": 0,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
