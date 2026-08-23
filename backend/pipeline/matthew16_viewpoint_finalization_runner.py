"""Compile the formal atomic gate and apply-ready package for one viewpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.api.canonical_repository.matthew16_viewpoint_candidate import (
    Matthew16ViewpointPilotArtifact,
)
from backend.api.canonical_repository.matthew16_viewpoint_finalization import (
    build_matthew16_viewpoint_finalization_bundle,
)
from backend.api.canonical_repository.matthew16_viewpoint_promotion import (
    Matthew16ViewpointPromotionProposal,
)
from backend.api.canonical_repository.viewpoint_runtime_projection import (
    ViewpointKnowledgeProjection,
)


def _write_immutable(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != payload:
            raise ValueError(f"immutable artifact differs at {path}")
        return
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    partial.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promotion-proposal", type=Path, required=True)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--consumer-projection", type=Path, required=True)
    parser.add_argument("--decided-at", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    proposal = Matthew16ViewpointPromotionProposal.model_validate_json(
        args.promotion_proposal.read_text(encoding="utf-8")
    )
    pilot = Matthew16ViewpointPilotArtifact.model_validate_json(
        args.pilot.read_text(encoding="utf-8")
    )
    projection = ViewpointKnowledgeProjection.model_validate_json(
        args.consumer_projection.read_text(encoding="utf-8")
    )
    bundle = build_matthew16_viewpoint_finalization_bundle(
        proposal=proposal,
        pilot=pilot,
        projection=projection,
        decided_at=args.decided_at,
    )
    _write_immutable(
        args.output_dir / "finalization-bundle.json",
        bundle.model_dump(mode="json"),
    )
    _write_immutable(args.output_dir / "knowledge-package.json", bundle.knowledge_package)
    print(
        json.dumps(
            {
                "viewpoint_id": bundle.canonical_viewpoint.viewpoint_id,
                "atomic_coverage_snapshot_id": (
                    bundle.atomic_coverage_snapshot.atomic_coverage_snapshot_id
                ),
                "atomic_resolution_ledger_id": (
                    bundle.atomic_resolution_ledger.atomic_resolution_ledger_id
                ),
                "atomic_quality_report_id": (
                    bundle.atomic_quality_report.atomic_quality_report_id
                ),
                "automated_promotion_decision_id": (
                    bundle.automated_promotion_decision.automated_promotion_decision_id
                ),
                "quality_decision": bundle.atomic_quality_report.eligibility_decision,
                "human_approval": bundle.automated_promotion_decision.human_approval,
                "master_data_mutation_count": bundle.master_data_mutation_count,
                "apply_allowed": bundle.apply_allowed,
                "artifact_sha256": bundle.artifact_sha256,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
