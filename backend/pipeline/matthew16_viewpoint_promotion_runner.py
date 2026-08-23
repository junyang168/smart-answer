"""Compile a fail-closed master promotion proposal for the first Matthew 16 viewpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.api.canonical_repository.matthew16_viewpoint_candidate import (
    Matthew16ViewpointPilotArtifact,
)
from backend.api.canonical_repository.matthew16_viewpoint_promotion import (
    build_matthew16_viewpoint_promotion_proposal,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_proposition_units import (
    ClaimAtomicDecompositionArtifact,
)
from backend.api.canonical_repository.viewpoint_resolution import (
    ViewpointIdentityReviewPacket,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--boundary-run", type=Path, required=True)
    parser.add_argument("--evidence-packet", type=Path, required=True)
    parser.add_argument("--decomposition-dir", type=Path, required=True)
    parser.add_argument("--proposed-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    pilot = Matthew16ViewpointPilotArtifact.model_validate_json(
        args.pilot.read_text(encoding="utf-8")
    )
    boundary = json.loads(args.boundary_run.read_text(encoding="utf-8"))
    evidence_packet = ViewpointIdentityReviewPacket.model_validate_json(
        args.evidence_packet.read_text(encoding="utf-8")
    )
    boundary_body = {key: value for key, value in boundary.items() if key != "artifact_sha256"}
    if boundary.get("artifact_sha256") != sha256_json(boundary_body):
        raise ValueError("boundary run artifact SHA mismatch")
    decompositions = [
        ClaimAtomicDecompositionArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(args.decomposition_dir.glob("*.json"))
    ]
    proposal = build_matthew16_viewpoint_promotion_proposal(
        pilot=pilot,
        boundary_run=boundary,
        evidence_packet=evidence_packet,
        decompositions=decompositions,
        proposed_at=args.proposed_at,
    )
    payload = proposal.model_dump(mode="json")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        if existing != payload:
            raise ValueError(f"immutable promotion proposal differs at {args.output}")
    else:
        partial = args.output.with_suffix(args.output.suffix + ".partial")
        partial.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        partial.replace(args.output)
    print(json.dumps({
        "viewpoint_id": proposal.canonical_viewpoint.viewpoint_id,
        "proposition_unit_count": len(proposal.proposition_units),
        "member_unit_link_count": len(proposal.proposition_unit_links),
        "excluded_unit_count": len(proposal.excluded_proposition_unit_ids),
        "claim_membership_link_count": proposal.claim_membership_link_count,
        "quality_check_count": len(proposal.quality_checks),
        "blockers": proposal.blockers,
        "apply_allowed": proposal.apply_allowed,
        "artifact_sha256": proposal.artifact_sha256,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
