"""Build a zero-call identity-hypothesis index from completed group discovery."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.viewpoint_group_discovery import (
    GroupDiscoveryPlan,
    validate_group_discovery_response,
)
from backend.api.canonical_repository.viewpoint_identity_hypotheses import (
    build_identity_hypothesis_index,
)
from backend.pipeline.viewpoint_group_discovery_runner import (
    GroupDiscoveryCallArtifact,
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read(path) != payload:
            raise ValueError(f"immutable artifact differs at {path}")
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_plan(*, plan_path: Path, calls_dir: Path, output_path: Path) -> dict[str, Any]:
    plan = GroupDiscoveryPlan.model_validate(_read(plan_path))
    packets = {item.packet_id: item for item in plan.packets}
    calls: dict[str, GroupDiscoveryCallArtifact] = {}
    for path in sorted(calls_dir.glob("group.*.json")):
        artifact = GroupDiscoveryCallArtifact.model_validate(_read(path))
        if artifact.plan_artifact_sha256 != plan.artifact_sha256:
            continue
        packet = packets.get(artifact.packet_id)
        if packet is None or artifact.packet_sha256 != packet.packet_sha256:
            raise ValueError(f"{path}: group-discovery call binding mismatch")
        validate_group_discovery_response(packet, artifact.response)
        if artifact.packet_id in calls:
            raise ValueError(f"duplicate completed call for {artifact.packet_id}")
        calls[artifact.packet_id] = artifact
    if set(calls) != set(packets):
        missing = sorted(set(packets) - set(calls))
        raise ValueError(
            "identity hypothesis planning requires complete group discovery: "
            + ", ".join(missing)
        )
    index = build_identity_hypothesis_index(
        plan=plan,
        responses_by_packet_id={
            packet_id: artifact.response.model_dump(mode="json")
            for packet_id, artifact in calls.items()
        },
        call_artifact_sha_by_packet_id={
            packet_id: artifact.artifact_sha256
            for packet_id, artifact in calls.items()
        },
    )
    payload = index.model_dump(mode="json")
    _write_immutable(output_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-plan", type=Path, required=True)
    parser.add_argument("--calls-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_plan(
        plan_path=args.group_plan,
        calls_dir=args.calls_dir,
        output_path=args.output,
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "artifact_sha256": payload["artifact_sha256"],
                **payload["statistics"],
                "model_calls_executed": 0,
                "master_data_mutations": 0,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
