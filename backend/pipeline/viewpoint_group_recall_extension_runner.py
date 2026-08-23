"""Compile complete group-discovery calls into an immutable recall overlay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.viewpoint_group_discovery import GroupDiscoveryPlan
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_group_recall_extension import (
    build_group_recall_extension,
)
from backend.api.canonical_repository.viewpoint_signature_recall import (
    ViewpointFinalCandidateGraphArtifact,
)
from backend.api.semantic_index.embeddings import EmbeddingIndexArtifact
from backend.pipeline.viewpoint_group_discovery_runner import GroupDiscoveryCallArtifact


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--calls-dir", type=Path, required=True)
    parser.add_argument("--final-candidate-graph", type=Path, required=True)
    parser.add_argument("--signature-embedding-index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = GroupDiscoveryPlan.model_validate(_read(args.plan))
    expected_config_sha = sha256_json({
        "reasoning_effort": plan.reasoning_effort,
        "max_output_tokens": plan.max_output_tokens,
        "temperature": 0.0,
    })
    calls = {}
    for path in sorted(args.calls_dir.glob("*.json")):
        call = GroupDiscoveryCallArtifact.model_validate(_read(path))
        if call.plan_artifact_sha256 != plan.artifact_sha256:
            raise ValueError(f"{path}: group call belongs to another plan")
        packet = next(
            (packet for packet in plan.packets if packet.packet_id == call.packet_id),
            None,
        )
        if packet is None or call.packet_sha256 != packet.packet_sha256:
            raise ValueError(f"{path}: group call packet differs from plan")
        if call.model_id != plan.model_id or call.backend != plan.backend:
            raise ValueError(f"{path}: group call model/backend differs from plan")
        if call.prompt_sha256 != plan.prompt_sha256:
            raise ValueError(f"{path}: group call prompt differs from plan")
        if call.generation_config_sha256 != expected_config_sha:
            raise ValueError(f"{path}: group call generation config differs from plan")
        if call.packet_id in calls:
            raise ValueError(f"duplicate group call for {call.packet_id}")
        calls[call.packet_id] = call
    artifact = build_group_recall_extension(
        plan=plan,
        final_graph=ViewpointFinalCandidateGraphArtifact.model_validate(
            _read(args.final_candidate_graph)
        ),
        signature_embedding_index=EmbeddingIndexArtifact.model_validate(
            _read(args.signature_embedding_index)
        ),
        responses_by_packet_id={packet_id: call.response for packet_id, call in calls.items()},
        call_artifact_sha_by_packet_id={
            packet_id: call.artifact_sha256 for packet_id, call in calls.items()
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise ValueError(f"refusing to overwrite immutable extension {args.output}")
    temporary = args.output.with_suffix(args.output.suffix + ".partial")
    temporary.write_text(
        json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(json.dumps(artifact.statistics | {"artifact_sha256": artifact.artifact_sha256}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
