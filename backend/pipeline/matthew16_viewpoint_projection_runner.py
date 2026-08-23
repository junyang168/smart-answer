"""Compile a standard downstream projection from one Matthew 16 pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.api.canonical_repository.matthew16_viewpoint_candidate import (
    Matthew16ViewpointPilotArtifact,
    build_pilot_composition_projection,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pilot = Matthew16ViewpointPilotArtifact.model_validate_json(
        args.pilot.read_text(encoding="utf-8")
    )
    projection = build_pilot_composition_projection(pilot)
    payload = projection.model_dump(mode="json")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        if existing != payload:
            raise ValueError(f"immutable projection differs at {args.output}")
    else:
        temporary = args.output.with_suffix(args.output.suffix + ".partial")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(args.output)
    print(json.dumps({
        "projection_sha256": projection.projection_sha256,
        "eligibility": projection.eligibility,
        "viewpoint_count": len(projection.viewpoints),
        "claim_count": len(projection.expanded_claims),
        "evidence_count": len(projection.expanded_evidence),
        "would_publish": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
