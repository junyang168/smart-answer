"""Execute resumable screening-only group discovery via Codex Subscription."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_group_discovery import (
    GroupDiscoveryPacket,
    GroupDiscoveryPlan,
    GroupDiscoveryResponse,
    validate_group_discovery_response,
)
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient


PROMPT_PATH = Path(__file__).with_name("prompts") / "viewpoint_group_discovery.md"


class StrictRunnerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GroupDiscoveryCallArtifact(StrictRunnerModel):
    schema_version: str = "wang_viewpoint_group_discovery_call_v1"
    plan_artifact_sha256: str
    packet_id: str
    packet_sha256: str
    model_id: str
    backend: str
    prompt_sha256: str
    generation_config_sha256: str
    generation_fingerprint_sha256: str
    raw_response_sha256: str
    response: GroupDiscoveryResponse
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "GroupDiscoveryCallArtifact":
        if self.response.packet_sha256 != self.packet_sha256:
            raise ValueError("group-discovery response packet mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("group-discovery call artifact SHA mismatch")
        return self


class GroupDiscoveryFailureArtifact(StrictRunnerModel):
    schema_version: str = "wang_viewpoint_group_discovery_failure_v1"
    plan_artifact_sha256: str
    packet_id: str
    packet_sha256: str
    generation_fingerprint_sha256: str
    error_type: str
    error_message: str
    raw_response_sha256: str | None = None
    raw_response: dict[str, Any] | None = None
    master_data_mutations: int = 0
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_failure(self) -> "GroupDiscoveryFailureArtifact":
        if self.master_data_mutations != 0:
            raise ValueError("group-discovery failure cannot mutate master data")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("group-discovery failure artifact SHA mismatch")
        return self


class GroupDiscoveryExecutionReport(StrictRunnerModel):
    schema_version: str = "wang_viewpoint_group_discovery_execution_v1"
    plan_artifact_sha256: str
    completed_packet_ids: list[str]
    calls_executed_this_run: int = Field(ge=0)
    reused_packet_count: int = Field(ge=0)
    proposal_count: int = Field(ge=0)
    possible_equivalent_count: int = Field(ge=0)
    component_count: int = Field(ge=0)
    tension_count: int = Field(ge=0)
    complete: bool
    master_data_mutations: int = 0
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_report(self) -> "GroupDiscoveryExecutionReport":
        if self.completed_packet_ids != sorted(set(self.completed_packet_ids)):
            raise ValueError("group-discovery completed packet ids must be canonical")
        if self.calls_executed_this_run + self.reused_packet_count != len(
            self.completed_packet_ids
        ):
            raise ValueError("group-discovery execution/reuse counts mismatch")
        if self.master_data_mutations != 0:
            raise ValueError("group discovery cannot mutate master data")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("group-discovery execution report SHA mismatch")
        return self


class GroupAdapter(Protocol):
    model_id: str
    backend: str
    prompt_sha256: str
    generation_config_sha256: str
    def generate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_new(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = type(value).model_validate(_read(path))
        if existing.model_dump(mode="json") != value.model_dump(mode="json"):
            raise ValueError(f"immutable artifact differs at {path}")
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(value.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _fingerprint(plan: GroupDiscoveryPlan, packet: GroupDiscoveryPacket, adapter: GroupAdapter) -> str:
    return sha256_json({
        "stage": "viewpoint_group_discovery",
        "plan_artifact_sha256": plan.artifact_sha256,
        "packet_sha256": packet.packet_sha256,
        "model_id": adapter.model_id,
        "backend": adapter.backend,
        "prompt_sha256": adapter.prompt_sha256,
        "generation_config_sha256": adapter.generation_config_sha256,
    })


def _run_one(
    *, plan: GroupDiscoveryPlan, packet: GroupDiscoveryPacket,
    adapter: GroupAdapter, output_dir: Path,
) -> tuple[GroupDiscoveryCallArtifact, bool]:
    fingerprint = _fingerprint(plan, packet, adapter)
    path = output_dir / "calls" / f"group.{fingerprint[:20]}.json"
    if path.exists():
        artifact = GroupDiscoveryCallArtifact.model_validate(_read(path))
        if (
            artifact.plan_artifact_sha256 != plan.artifact_sha256
            or artifact.packet_id != packet.packet_id
            or artifact.packet_sha256 != packet.packet_sha256
            or artifact.model_id != adapter.model_id
            or artifact.backend != adapter.backend
            or artifact.prompt_sha256 != adapter.prompt_sha256
            or artifact.generation_config_sha256 != adapter.generation_config_sha256
            or artifact.generation_fingerprint_sha256 != fingerprint
        ):
            raise ValueError("cached group-discovery artifact binding mismatch")
        validate_group_discovery_response(packet, artifact.response)
        return artifact, True
    raw: Mapping[str, Any] | None = None
    for failure_path in sorted(
        (output_dir / "failures").glob(f"group.{fingerprint[:20]}.*.json"),
        reverse=True,
    ):
        try:
            failure = GroupDiscoveryFailureArtifact.model_validate(_read(failure_path))
        except Exception:
            continue
        if (
            failure.generation_fingerprint_sha256 != fingerprint
            or failure.raw_response is None
        ):
            continue
        try:
            response = validate_group_discovery_response(packet, failure.raw_response)
        except Exception:
            continue
        raw = failure.raw_response
        break
    try:
        if raw is None:
            raw = adapter.generate(packet.model_dump(mode="json"))
            response = validate_group_discovery_response(packet, dict(raw))
    except Exception as exc:
        failure_payload = {
            "schema_version": "wang_viewpoint_group_discovery_failure_v1",
            "plan_artifact_sha256": plan.artifact_sha256,
            "packet_id": packet.packet_id,
            "packet_sha256": packet.packet_sha256,
            "generation_fingerprint_sha256": fingerprint,
            "error_type": type(exc).__name__,
            "error_message": str(exc)[-2000:],
            "raw_response_sha256": sha256_json(raw) if raw is not None else None,
            "raw_response": dict(raw) if raw is not None else None,
            "master_data_mutations": 0,
        }
        failure = GroupDiscoveryFailureArtifact(
            **failure_payload, artifact_sha256=sha256_json(failure_payload)
        )
        _write_new(
            output_dir / "failures" /
            f"group.{fingerprint[:20]}.{failure.artifact_sha256[:12]}.json",
            failure,
        )
        raise
    payload = {
        "schema_version": "wang_viewpoint_group_discovery_call_v1",
        "plan_artifact_sha256": plan.artifact_sha256,
        "packet_id": packet.packet_id,
        "packet_sha256": packet.packet_sha256,
        "model_id": adapter.model_id,
        "backend": adapter.backend,
        "prompt_sha256": adapter.prompt_sha256,
        "generation_config_sha256": adapter.generation_config_sha256,
        "generation_fingerprint_sha256": fingerprint,
        "raw_response_sha256": sha256_json(raw),
        "response": response.model_dump(mode="json"),
    }
    artifact = GroupDiscoveryCallArtifact(**payload, artifact_sha256=sha256_json(payload))
    _write_new(path, artifact)
    return artifact, False


def run_group_discovery(
    *, plan: GroupDiscoveryPlan, adapter: GroupAdapter, output_dir: Path,
    workers: int = 4, max_new_calls: int | None = None,
) -> GroupDiscoveryExecutionReport:
    if (adapter.model_id, adapter.backend, adapter.prompt_sha256) != (
        plan.model_id, plan.backend, plan.prompt_sha256
    ):
        raise ValueError("group-discovery adapter differs from authorized plan")
    expected_config = sha256_json({
        "reasoning_effort": plan.reasoning_effort,
        "max_output_tokens": plan.max_output_tokens,
        "temperature": 0.0,
    })
    if adapter.generation_config_sha256 != expected_config:
        raise ValueError("group-discovery generation config differs from plan")
    if not 1 <= workers <= 8:
        raise ValueError("group-discovery workers must be between 1 and 8")
    selected = []
    new_budget = max_new_calls
    for packet in plan.packets:
        path = output_dir / "calls" / f"group.{_fingerprint(plan, packet, adapter)[:20]}.json"
        if path.exists():
            selected.append(packet)
        elif new_budget is None or new_budget > 0:
            selected.append(packet)
            if new_budget is not None:
                new_budget -= 1
    results: list[tuple[GroupDiscoveryCallArtifact, bool]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        packet_iter = iter(selected)
        futures: dict[Future[tuple[GroupDiscoveryCallArtifact, bool]], str] = {}
        def submit_next() -> bool:
            try:
                packet = next(packet_iter)
            except StopIteration:
                return False
            future = executor.submit(
                _run_one, plan=plan, packet=packet, adapter=adapter, output_dir=output_dir
            )
            futures[future] = packet.packet_id
            return True
        for _ in range(workers):
            if not submit_next():
                break
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                futures.pop(future)
                results.append(future.result())
                submit_next()
    results.sort(key=lambda row: row[0].packet_id)
    proposals = [
        proposal for artifact, _ in results for proposal in artifact.response.proposals
    ]
    counts = {kind: sum(p.relation_kind == kind for p in proposals) for kind in (
        "possible_equivalent", "component", "tension"
    )}
    payload = {
        "schema_version": "wang_viewpoint_group_discovery_execution_v1",
        "plan_artifact_sha256": plan.artifact_sha256,
        "completed_packet_ids": sorted(artifact.packet_id for artifact, _ in results),
        "calls_executed_this_run": sum(not cached for _, cached in results),
        "reused_packet_count": sum(cached for _, cached in results),
        "proposal_count": len(proposals),
        "possible_equivalent_count": counts["possible_equivalent"],
        "component_count": counts["component"],
        "tension_count": counts["tension"],
        "complete": len(results) == len(plan.packets),
        "master_data_mutations": 0,
    }
    report = GroupDiscoveryExecutionReport(**payload, artifact_sha256=sha256_json(payload))
    _write_new(output_dir / "execution-reports" / f"{report.artifact_sha256}.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-new-calls", type=int)
    args = parser.parse_args()
    plan = GroupDiscoveryPlan.model_validate(_read(args.plan))
    client = CodexSubscriptionClient(
        model=plan.model_id,
        reasoning_effort=plan.reasoning_effort,
        timeout_seconds=900,
        max_output_tokens=plan.max_output_tokens,
    )
    adapter = StructuredJsonReviewerAdapter(
        client=client,
        prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        response_model=GroupDiscoveryResponse,
        schema_name="wang_viewpoint_group_discovery_response_v1",
    )
    report = run_group_discovery(
        plan=plan, adapter=adapter, output_dir=args.output_dir,
        workers=args.workers, max_new_calls=args.max_new_calls,
    )
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
