"""Compile validated per-packet Claim signatures into one immutable index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.viewpoint_claim_signature import (
    ClaimSignaturePlan, build_claim_signature_index,
)
from backend.pipeline.viewpoint_claim_signature_runner import ClaimSignatureCallArtifact


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--calls-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = ClaimSignaturePlan.model_validate(_read(args.plan))
    calls: dict[str, ClaimSignatureCallArtifact] = {}
    for path in sorted(args.calls_dir.glob("*.json")):
        call = ClaimSignatureCallArtifact.model_validate(_read(path))
        if call.plan_artifact_sha256 != plan.artifact_sha256:
            raise ValueError(f"{path}: signature call belongs to another plan")
        if call.model_id != plan.model_id:
            raise ValueError(f"{path}: signature call model differs from plan")
        if call.backend != plan.backend:
            raise ValueError(f"{path}: signature call backend differs from plan")
        if call.prompt_sha256 != plan.prompt_sha256:
            raise ValueError(f"{path}: signature call prompt differs from plan")
        packet = next(
            (packet for packet in plan.packets if packet.packet_id == call.packet_id),
            None,
        )
        if packet is None or call.packet_sha256 != packet.packet_sha256:
            raise ValueError(f"{path}: signature call packet differs from plan")
        if call.packet_id in calls:
            raise ValueError(f"duplicate signature call for {call.packet_id}")
        calls[call.packet_id] = call
    config_shas = {call.generation_config_sha256 for call in calls.values()}
    if len(config_shas) != 1:
        raise ValueError("signature calls do not share one generation config")
    index = build_claim_signature_index(
        plan=plan,
        responses_by_packet_id={packet_id: call.response for packet_id, call in calls.items()},
        call_artifact_sha_by_packet_id={packet_id: call.artifact_sha256 for packet_id, call in calls.items()},
        generation_config_sha256=next(iter(config_shas)),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise ValueError(f"refusing to overwrite immutable index {args.output}")
    temporary = args.output.with_suffix(args.output.suffix + ".partial")
    temporary.write_text(json.dumps(index.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps(index.statistics | {"artifact_sha256": index.artifact_sha256}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
