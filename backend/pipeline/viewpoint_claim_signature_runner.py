"""Execute resumable Claim semantic-signature extraction via Codex Subscription."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.canonical_repository.viewpoint_claim_signature import (
    ClaimSignaturePacket, ClaimSignaturePlan, ClaimSignatureResponse,
    validate_signature_response,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient

PROMPT_PATH = Path(__file__).with_name("prompts") / "viewpoint_claim_signature.md"


class StrictRunnerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaimSignatureCallArtifact(StrictRunnerModel):
    schema_version: str = "wang_claim_semantic_signature_call_v1"
    plan_artifact_sha256: str
    packet_id: str
    packet_sha256: str
    model_id: str
    backend: str
    prompt_sha256: str
    generation_config_sha256: str
    generation_fingerprint_sha256: str
    raw_response_sha256: str
    response: ClaimSignatureResponse
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "ClaimSignatureCallArtifact":
        if self.response.packet_sha256 != self.packet_sha256:
            raise ValueError("signature response packet SHA mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("signature call artifact SHA mismatch")
        return self


class ClaimSignatureFailureArtifact(StrictRunnerModel):
    schema_version: str = "wang_claim_semantic_signature_failure_v1"
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
    def validate_failure(self) -> "ClaimSignatureFailureArtifact":
        if self.master_data_mutations != 0:
            raise ValueError("signature failure artifact cannot mutate master data")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("signature failure artifact SHA mismatch")
        return self


class ClaimSignatureExecutionReport(StrictRunnerModel):
    schema_version: str = "wang_claim_semantic_signature_execution_v1"
    plan_artifact_sha256: str
    model_id: str
    backend: str
    prompt_sha256: str
    generation_config_sha256: str
    completed_packet_ids: list[str]
    calls_executed_this_run: int = Field(ge=0)
    reused_packet_count: int = Field(ge=0)
    signature_count: int = Field(ge=0)
    semantic_atom_count: int = Field(ge=0)
    insufficient_evidence_count: int = Field(ge=0)
    master_data_mutations: int = 0
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_report(self) -> "ClaimSignatureExecutionReport":
        if self.completed_packet_ids != sorted(set(self.completed_packet_ids)):
            raise ValueError("completed signature packet ids must be canonical")
        if self.calls_executed_this_run + self.reused_packet_count != len(self.completed_packet_ids):
            raise ValueError("signature execution/reuse counts mismatch")
        if self.master_data_mutations != 0:
            raise ValueError("signature extraction cannot mutate master data")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("signature execution report SHA mismatch")
        return self


class SignatureAdapter(Protocol):
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
        raise ValueError(f"refusing to overwrite immutable artifact {path}")
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _fingerprint(plan: ClaimSignaturePlan, packet: ClaimSignaturePacket, adapter: SignatureAdapter) -> str:
    return sha256_json({
        "stage": "claim_semantic_signature", "plan_artifact_sha256": plan.artifact_sha256,
        "packet_sha256": packet.packet_sha256, "model_id": adapter.model_id,
        "backend": adapter.backend, "prompt_sha256": adapter.prompt_sha256,
        "generation_config_sha256": adapter.generation_config_sha256,
    })


def _run_one(
    *, plan: ClaimSignaturePlan, packet: ClaimSignaturePacket,
    adapter: SignatureAdapter, output_dir: Path,
) -> tuple[ClaimSignatureCallArtifact, bool]:
    fingerprint = _fingerprint(plan, packet, adapter)
    path = output_dir / "calls" / f"signature.{fingerprint[:20]}.json"
    if path.exists():
        artifact = ClaimSignatureCallArtifact.model_validate(_read(path))
        if artifact.generation_fingerprint_sha256 != fingerprint:
            raise ValueError("cached signature artifact binding mismatch")
        validate_signature_response(packet, artifact.response)
        return artifact, True
    raw: Mapping[str, Any] | None = None
    recovered_from_failure = False
    failure_pattern = f"signature.{fingerprint[:20]}.*.json"
    for failure_path in sorted((output_dir / "failures").glob(failure_pattern), reverse=True):
        try:
            failure = ClaimSignatureFailureArtifact.model_validate(_read(failure_path))
        except Exception:
            continue
        if failure.generation_fingerprint_sha256 != fingerprint or failure.raw_response is None:
            continue
        try:
            response = validate_signature_response(packet, failure.raw_response)
        except Exception:
            continue
        raw = failure.raw_response
        recovered_from_failure = True
        break
    try:
        if raw is None:
            raw = adapter.generate(packet.model_dump(mode="json"))
            response = validate_signature_response(packet, raw)
    except Exception as exc:
        failure_payload = {
            "schema_version": "wang_claim_semantic_signature_failure_v1",
            "plan_artifact_sha256": plan.artifact_sha256, "packet_id": packet.packet_id,
            "packet_sha256": packet.packet_sha256,
            "generation_fingerprint_sha256": fingerprint,
            "error_type": type(exc).__name__, "error_message": str(exc)[-2000:],
            "raw_response_sha256": sha256_json(raw) if raw is not None else None,
            "raw_response": dict(raw) if raw is not None else None,
            "master_data_mutations": 0,
        }
        failure = ClaimSignatureFailureArtifact(
            **failure_payload, artifact_sha256=sha256_json(failure_payload)
        )
        _write_new(
            output_dir / "failures" / f"signature.{fingerprint[:20]}.{failure.artifact_sha256[:12]}.json",
            failure,
        )
        raise
    payload = {
        "schema_version": "wang_claim_semantic_signature_call_v1",
        "plan_artifact_sha256": plan.artifact_sha256, "packet_id": packet.packet_id,
        "packet_sha256": packet.packet_sha256, "model_id": adapter.model_id,
        "backend": adapter.backend, "prompt_sha256": adapter.prompt_sha256,
        "generation_config_sha256": adapter.generation_config_sha256,
        "generation_fingerprint_sha256": fingerprint,
        "raw_response_sha256": sha256_json(raw),
        "response": response.model_dump(mode="json"),
    }
    artifact = ClaimSignatureCallArtifact(**payload, artifact_sha256=sha256_json(payload))
    _write_new(path, artifact)
    return artifact, recovered_from_failure


def run_claim_signatures(
    *, plan: ClaimSignaturePlan, adapter: SignatureAdapter,
    output_dir: Path, workers: int = 4,
) -> ClaimSignatureExecutionReport:
    if adapter.model_id != plan.model_id or adapter.backend != plan.backend:
        raise ValueError("signature adapter differs from the authorized plan")
    if adapter.prompt_sha256 != plan.prompt_sha256:
        raise ValueError("signature prompt differs from the authorized plan")
    expected_config = sha256_json({
        "reasoning_effort": plan.reasoning_effort,
        "max_output_tokens": plan.max_output_tokens,
        "temperature": 0.0,
    })
    if adapter.generation_config_sha256 != expected_config:
        raise ValueError("signature generation config differs from the authorized plan")
    if not 1 <= workers <= 8:
        raise ValueError("signature workers must be between 1 and 8")
    results: list[tuple[ClaimSignatureCallArtifact, bool]] = []
    packets = iter(plan.packets)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures: dict[Future[tuple[ClaimSignatureCallArtifact, bool]], str] = {}
        def submit_next() -> bool:
            try:
                packet = next(packets)
            except StopIteration:
                return False
            future = executor.submit(_run_one, plan=plan, packet=packet, adapter=adapter, output_dir=output_dir)
            futures[future] = packet.packet_id
            return True
        for _ in range(workers):
            if not submit_next():
                break
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                futures.pop(future)
                try:
                    results.append(future.result())
                except Exception:
                    for pending in futures:
                        pending.cancel()
                    raise
                submit_next()
    results.sort(key=lambda row: row[0].packet_id)
    signatures = [signature for artifact, _ in results for signature in artifact.response.signatures]
    payload = {
        "schema_version": "wang_claim_semantic_signature_execution_v1",
        "plan_artifact_sha256": plan.artifact_sha256, "model_id": adapter.model_id,
        "backend": adapter.backend, "prompt_sha256": adapter.prompt_sha256,
        "generation_config_sha256": adapter.generation_config_sha256,
        "completed_packet_ids": sorted(artifact.packet_id for artifact, _ in results),
        "calls_executed_this_run": sum(not cached for _, cached in results),
        "reused_packet_count": sum(cached for _, cached in results),
        "signature_count": len(signatures),
        "semantic_atom_count": sum(len(signature.semantic_atoms) for signature in signatures),
        "insufficient_evidence_count": sum(not signature.evidence_sufficient for signature in signatures),
        "master_data_mutations": 0,
    }
    report = ClaimSignatureExecutionReport(**payload, artifact_sha256=sha256_json(payload))
    _write_new(output_dir / "execution-reports" / f"{report.artifact_sha256}.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    plan = ClaimSignaturePlan.model_validate(_read(args.plan))
    client = CodexSubscriptionClient(
        model=plan.model_id, reasoning_effort=plan.reasoning_effort,
        timeout_seconds=900, max_output_tokens=plan.max_output_tokens,
    )
    adapter = StructuredJsonReviewerAdapter(
        client=client, prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        response_model=ClaimSignatureResponse,
        schema_name="wang_claim_semantic_signature_response_v2",
    )
    report = run_claim_signatures(plan=plan, adapter=adapter, output_dir=args.output_dir, workers=args.workers)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
