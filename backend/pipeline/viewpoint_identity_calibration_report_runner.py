"""Compile deterministic metrics from a completed identity calibration."""

from __future__ import annotations

import argparse
import glob
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_identity_hypotheses import (
    IdentityCalibrationPlan,
    IdentityEvidenceReviewPlan,
)
from backend.api.canonical_repository.viewpoint_resolution import (
    ReviewCallArtifact,
    ViewpointResolutionRunArtifact,
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _one(pattern: str) -> Path:
    paths = [Path(value) for value in glob.glob(pattern) if ".failure." not in value]
    if len(paths) != 1:
        raise ValueError(f"expected exactly one artifact for {pattern}; found {len(paths)}")
    return paths[0]


def build_report(
    *, calibration_plan: IdentityCalibrationPlan,
    evidence_plan: IdentityEvidenceReviewPlan, runs_dir: Path,
) -> dict[str, Any]:
    evidence = {item.hypothesis_id: item for item in evidence_plan.items}
    path_counts: Counter[str] = Counter()
    action_pairs: Counter[str] = Counter()
    kind_rows: dict[str, Counter[str]] = defaultdict(Counter)
    result_rows = []
    remaining_finding_count = 0
    call_count = 0
    for hypothesis_id in calibration_plan.selected_hypothesis_ids:
        item = evidence[hypothesis_id]
        directory = runs_dir / hypothesis_id
        proposal = ReviewCallArtifact.model_validate(
            _read(_one(str(directory / "proposal.*.json")))
        ).assessment
        blind = ReviewCallArtifact.model_validate(
            _read(_one(str(directory / "blind_review.*.json")))
        ).assessment
        run = ViewpointResolutionRunArtifact.model_validate(
            _read(_one(str(directory / "run.*.json")))
        )
        proposal_roles = [
            (member.claim_id, member.member_role) for member in proposal.members
        ]
        blind_roles = [
            (member.claim_id, member.member_role) for member in blind.members
        ]
        role_agreement = proposal_roles == blind_roles
        action_agreement = proposal.proposed_action == blind.proposed_action
        boundary_agreement = role_agreement and action_agreement
        both_all_full = all(
            member.member_role == "equivalent_full" for member in proposal.members
        ) and all(
            member.member_role == "equivalent_full" for member in blind.members
        )
        kind = item.relation_kind
        summary = kind_rows[kind]
        summary["count"] += 1
        summary["member_role_agreement_count"] += role_agreement
        summary["action_agreement_count"] += action_agreement
        summary["boundary_agreement_count"] += boundary_agreement
        summary["both_all_full_count"] += both_all_full
        action_pairs[f"{proposal.proposed_action}|{blind.proposed_action}"] += 1
        path_counts.update(delta.field_path for delta in run.semantic_deltas)
        call_count += len(run.call_ledger)
        remaining = (
            run.exception_bundle.remaining_findings if run.exception_bundle else []
        )
        remaining_finding_count += len(remaining)
        result_rows.append({
            "hypothesis_id": hypothesis_id,
            "relation_kind": kind,
            "member_role_agreement": role_agreement,
            "action_agreement": action_agreement,
            "boundary_agreement": boundary_agreement,
            "both_all_full": both_all_full,
            "semantic_delta_count": len(run.semantic_deltas),
            "remaining_finding_count": len(remaining),
            "disposition": run.disposition,
            "resolution_run_artifact_sha256": run.artifact_sha256,
        })
    selected_count = len(result_rows)
    boundary_agreement_count = sum(row["boundary_agreement"] for row in result_rows)
    payload = {
        "schema_version": "wang_viewpoint_identity_calibration_report_v1",
        "calibration_plan_sha256": calibration_plan.artifact_sha256,
        "evidence_plan_sha256": evidence_plan.artifact_sha256,
        "results": result_rows,
        "by_relation_kind": {
            kind: dict(sorted(values.items()))
            for kind, values in sorted(kind_rows.items())
        },
        "action_pairs": dict(sorted(action_pairs.items())),
        "delta_field_counts": dict(sorted(path_counts.items())),
        "statistics": {
            "selected_hypothesis_count": selected_count,
            "completed_hypothesis_count": selected_count,
            "semantic_call_count": call_count,
            "semantic_delta_count": sum(row["semantic_delta_count"] for row in result_rows),
            "delta_hypothesis_count": sum(bool(row["semantic_delta_count"]) for row in result_rows),
            "member_role_agreement_count": sum(row["member_role_agreement"] for row in result_rows),
            "action_agreement_count": sum(row["action_agreement"] for row in result_rows),
            "boundary_agreement_count": boundary_agreement_count,
            "both_all_full_count": sum(row["both_all_full"] for row in result_rows),
            "remaining_finding_count": remaining_finding_count,
            "system_approved_count": sum(row["disposition"] == "system_approved" for row in result_rows),
            "human_exception_count": sum(row["disposition"] == "human_exception" for row in result_rows),
        },
        "full_rollout_recommended": False,
        "rollout_blockers": [
            "combined boundary-and-canonical-synthesis schema produced delta for every calibration hypothesis",
            "no calibration hypothesis received dual-review all-member equivalent_full agreement",
            "identity boundary agreement is below a safe automation threshold",
            "delta adjudication retained unresolved findings",
        ],
        "required_redesign": (
            "separate closed whole-hypothesis boundary classification from later "
            "canonical wording/signature synthesis; do not allow reviewers to select "
            "different subsets while assessing one hypothesis"
        ),
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    payload["artifact_sha256"] = sha256_json(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration-plan", type=Path, required=True)
    parser.add_argument("--evidence-plan", type=Path, required=True)
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(
        calibration_plan=IdentityCalibrationPlan.model_validate(_read(args.calibration_plan)),
        evidence_plan=IdentityEvidenceReviewPlan.model_validate(_read(args.evidence_plan)),
        runs_dir=args.runs_dir,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists() and _read(args.output) != report:
        raise ValueError(f"immutable artifact differs at {args.output}")
    if not args.output.exists():
        temporary = args.output.with_suffix(args.output.suffix + ".partial")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(args.output)
    print(json.dumps({
        "output": str(args.output), "artifact_sha256": report["artifact_sha256"],
        **report["statistics"],
        "full_rollout_recommended": report["full_rollout_recommended"],
        "master_data_mutations": 0,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
