"""Build an immutable zero-call group-discovery plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.viewpoint_claim_signature import (
    ClaimSignatureIndexArtifact,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_group_discovery import (
    build_group_discovery_plan,
)
from backend.api.canonical_repository.viewpoint_signature_recall import (
    ViewpointFinalCandidateGraphArtifact,
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signature-index", type=Path, required=True)
    parser.add_argument("--final-candidate-graph", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--max-output-tokens", type=int, default=32000)
    args = parser.parse_args()
    prompt_sha = sha256_json({"prompt": args.prompt.read_text(encoding="utf-8")})
    plan = build_group_discovery_plan(
        signature_index=ClaimSignatureIndexArtifact.model_validate(
            _read(args.signature_index)
        ),
        final_graph=ViewpointFinalCandidateGraphArtifact.model_validate(
            _read(args.final_candidate_graph)
        ),
        model_id=args.model,
        reasoning_effort=args.reasoning_effort,
        max_output_tokens=args.max_output_tokens,
        prompt_sha256=prompt_sha,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise ValueError(f"refusing to overwrite immutable plan {args.output}")
    temporary = args.output.with_suffix(args.output.suffix + ".partial")
    temporary.write_text(
        json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(json.dumps(plan.statistics | {"artifact_sha256": plan.artifact_sha256}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
