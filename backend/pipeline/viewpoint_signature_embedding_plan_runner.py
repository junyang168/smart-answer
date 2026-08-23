"""Build a zero-call embedding plan for Claim semantic signatures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.canonical_repository.viewpoint_claim_signature import (
    ClaimSignatureIndexArtifact,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.semantic_index.embeddings import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_DIMENSIONS,
    DEFAULT_GEMINI_MODEL,
    GoogleGeminiEmbeddingProvider,
    build_embedding_generation_plan,
    build_embedding_projection_manifest,
)
from backend.api.semantic_index.projections import (
    build_claim_signature_embedding_projection,
)


class SignatureEmbeddingBudgetReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["wang_claim_signature_embedding_budget_v1"] = (
        "wang_claim_signature_embedding_budget_v1"
    )
    signature_index_sha256: str
    projection_manifest_sha256: str
    plan_sha256: str
    model_calls_executed: Literal[0] = 0
    estimated_provider_call_count: int = Field(ge=0)
    signature_count: int = Field(ge=0)
    semantic_atom_count: int = Field(ge=0)
    batch_count: int = Field(ge=0)
    input_bytes: int = Field(ge=0)
    estimated_input_tokens: int = Field(ge=0)
    model: str
    dimensions: int = Field(ge=1)
    retrieval_only: Literal[True] = True
    identity_evidence: Literal[False] = False
    apply_allowed: Literal[False] = False
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_report(self) -> "SignatureEmbeddingBudgetReport":
        if self.estimated_provider_call_count != self.batch_count:
            raise ValueError("signature embedding provider call estimate mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("signature embedding budget SHA mismatch")
        return self


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_new(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError(f"refusing to overwrite immutable artifact {path}")
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(value.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_signature_embedding_budget(
    *,
    signature_index: ClaimSignatureIndexArtifact,
    model: str = DEFAULT_GEMINI_MODEL,
    dimensions: int = DEFAULT_DIMENSIONS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    transport_mode: Literal[
        "gemini_developer_multi_content", "vertex_single_content"
    ] = "gemini_developer_multi_content",
    endpoint_location: str = "global",
) -> dict[str, BaseModel]:
    if transport_mode == "vertex_single_content" and batch_size != 1:
        raise ValueError("Vertex sync signature embedding plans require batch_size=1")
    projections = [
        build_claim_signature_embedding_projection(
            signature, signature_index_sha256=signature_index.artifact_sha256
        )
        for signature in signature_index.signatures
    ]
    manifest = build_embedding_projection_manifest(projections)
    provider = GoogleGeminiEmbeddingProvider(
        model=model,
        dimensions=dimensions,
        batch_size=batch_size,
        transport_mode=transport_mode,
        endpoint_location=endpoint_location,
    )
    plan = build_embedding_generation_plan(
        projections=projections,
        provider=provider,
        use_case="candidate_recall",
        max_batch_size=batch_size,
    )
    if plan.projection_manifest_sha256 != manifest.artifact_sha256:
        raise ValueError("signature embedding plan does not bind projection manifest")
    if len(projections) != signature_index.statistics["signature_count"]:
        raise ValueError("signature embedding projections do not cover index exactly once")
    payload = {
        "schema_version": "wang_claim_signature_embedding_budget_v1",
        "signature_index_sha256": signature_index.artifact_sha256,
        "projection_manifest_sha256": manifest.artifact_sha256,
        "plan_sha256": plan.plan_sha256,
        "model_calls_executed": 0,
        "estimated_provider_call_count": plan.statistics["batch_count"],
        "signature_count": len(projections),
        "semantic_atom_count": signature_index.statistics["semantic_atom_count"],
        "batch_count": plan.statistics["batch_count"],
        "input_bytes": plan.statistics["input_bytes"],
        "estimated_input_tokens": plan.statistics["estimated_input_tokens"],
        "model": plan.provider.model,
        "dimensions": plan.provider.dimensions,
        "retrieval_only": True,
        "identity_evidence": False,
        "apply_allowed": False,
    }
    budget = SignatureEmbeddingBudgetReport(
        **payload, artifact_sha256=sha256_json(payload)
    )
    return {"projection_manifest": manifest, "plan": plan, "summary": budget}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signature-index", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_GEMINI_MODEL)
    parser.add_argument("--dimensions", type=int, default=DEFAULT_DIMENSIONS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--transport-mode",
        choices=("gemini_developer_multi_content", "vertex_single_content"),
        default="gemini_developer_multi_content",
    )
    parser.add_argument("--endpoint-location", default="global")
    args = parser.parse_args()
    signature_index = ClaimSignatureIndexArtifact.model_validate(
        _read(args.signature_index)
    )
    artifacts = build_signature_embedding_budget(
        signature_index=signature_index,
        model=args.model,
        dimensions=args.dimensions,
        batch_size=args.batch_size,
        transport_mode=args.transport_mode,
        endpoint_location=args.endpoint_location,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_new(
        args.output_dir / "signature-embedding-projection-manifest.json",
        artifacts["projection_manifest"],
    )
    _write_new(args.output_dir / "signature-embedding-plan.json", artifacts["plan"])
    _write_new(
        args.output_dir / "signature-embedding-budget.json", artifacts["summary"]
    )
    print(
        json.dumps(
            artifacts["summary"].model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
