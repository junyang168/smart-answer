"""Evaluate a boundary calibration execution against rollout policy."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.viewpoint_foundation import sha256_json


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_report(execution_report: dict[str, Any], execution_dir: Path) -> dict[str, Any]:
    cross_tab: Counter[str] = Counter()
    exact_agreement = 0
    label_agreement = 0
    equivalent_all_agreement = 0
    for row in execution_report["results"]:
        run_dir = execution_dir / "runs" / row["hypothesis_id"]
        assessments = {}
        for stage in ("proposal", "blind_review"):
            paths = sorted(run_dir.glob(f"{stage}.*.json"))
            if len(paths) != 1:
                raise ValueError(
                    f"{row['hypothesis_id']}: expected one {stage} artifact, got {len(paths)}"
                )
            assessments[stage] = _read(paths[0])["assessment"]
        proposal = assessments["proposal"]
        blind = assessments["blind_review"]
        proposal_relation = proposal["whole_relation"]
        blind_relation = blind["whole_relation"]
        cross_tab[f"{proposal_relation}|{blind_relation}"] += 1
        labels_same = proposal_relation == blind_relation
        label_agreement += labels_same
        semantic_proposal = dict(proposal)
        semantic_blind = dict(blind)
        semantic_proposal.pop("rationale")
        semantic_blind.pop("rationale")
        exact_same = semantic_proposal == semantic_blind
        exact_agreement += exact_same
        equivalent_all_agreement += exact_same and proposal_relation == "equivalent_all"
    completed = len(execution_report["results"])
    exact_rate = exact_agreement / completed if completed else 0.0
    policy = {
        "policy_version": "viewpoint_identity_boundary_rollout_policy_v1",
        "minimum_exact_agreement_rate": 0.9,
        "minimum_equivalent_all_agreements": 2,
        "requires_complete_execution": True,
        "requires_zero_master_data_mutations": True,
    }
    recommended = bool(
        execution_report["complete"]
        and execution_report["master_data_mutations"] == 0
        and exact_rate >= policy["minimum_exact_agreement_rate"]
        and equivalent_all_agreement >= policy["minimum_equivalent_all_agreements"]
    )
    payload = {
        "schema_version": "wang_viewpoint_identity_boundary_calibration_report_v1",
        "execution_report_sha256": execution_report["artifact_sha256"],
        "rollout_policy": policy,
        "statistics": {
            "completed_hypothesis_count": completed,
            "exact_boundary_agreement_count": exact_agreement,
            "exact_boundary_agreement_rate": round(exact_rate, 6),
            "relation_label_agreement_count": label_agreement,
            "relation_label_agreement_rate": round(
                label_agreement / completed if completed else 0.0, 6
            ),
            "equivalent_all_agreement_count": equivalent_all_agreement,
            "boundary_exception_count": completed - exact_agreement,
        },
        "relation_cross_tab": dict(sorted(cross_tab.items())),
        "full_rollout_recommended": recommended,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    payload["artifact_sha256"] = sha256_json(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-report", type=Path, required=True)
    parser.add_argument("--execution-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(_read(args.execution_report), args.execution_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        if _read(args.output) != report:
            raise ValueError(f"immutable boundary report differs at {args.output}")
    else:
        temporary = args.output.with_suffix(args.output.suffix + ".partial")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(args.output)
    print(json.dumps({
        "output": str(args.output),
        "artifact_sha256": report["artifact_sha256"],
        **report["statistics"],
        "full_rollout_recommended": report["full_rollout_recommended"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
