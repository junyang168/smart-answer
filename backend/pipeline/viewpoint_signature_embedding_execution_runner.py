"""Execute a pinned signature embedding plan and compile the final recall graph."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.canonical_repository.viewpoint_candidate_recall import (
    ViewpointCandidateRecallArtifact,
)
from backend.api.canonical_repository.viewpoint_claim_signature import (
    ClaimSignatureIndexArtifact,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_signature_recall import (
    build_final_candidate_graph,
    build_signature_recall,
)
from backend.api.semantic_index.embeddings import (
    EmbeddingGenerationPlan,
    EmbeddingProjectionManifest,
    GoogleGeminiEmbeddingProvider,
)
from backend.pipeline.viewpoint_embedding_execution_runner import execute_embedding_plan
from backend.pipeline.viewpoint_signature_embedding_plan_runner import (
    SignatureEmbeddingBudgetReport,
    build_signature_embedding_budget,
)


class SignatureEmbeddingExecutionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["wang_claim_signature_embedding_execution_v1"] = (
        "wang_claim_signature_embedding_execution_v1"
    )
    signature_index_sha256: str
    projection_manifest_sha256: str
    plan_sha256: str
    embedding_index_sha256: str
    signature_recall_artifact_sha256: str
    candidate_recall_artifact_sha256: str
    final_candidate_graph_sha256: str
    completed_batch_ids: list[str]
    provider_calls_executed_this_run: int = Field(ge=0)
    reused_batch_count: int = Field(ge=0)
    signature_count: int = Field(ge=0)
    semantic_atom_count: int = Field(ge=0)
    final_candidate_pair_count: int = Field(ge=0)
    master_data_mutations: Literal[0] = 0
    recall_only: Literal[True] = True
    identity_evidence: Literal[False] = False
    apply_allowed: Literal[False] = False
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_report(self) -> "SignatureEmbeddingExecutionReport":
        if self.completed_batch_ids != sorted(set(self.completed_batch_ids)):
            raise ValueError("signature embedding batch ids must be canonical")
        if self.provider_calls_executed_this_run + self.reused_batch_count != len(
            self.completed_batch_ids
        ):
            raise ValueError("signature embedding execution/reuse counts mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("signature embedding execution report SHA mismatch")
        return self


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


def _assert_exact(expected: BaseModel, actual: BaseModel, label: str) -> None:
    if expected.model_dump(mode="json") != actual.model_dump(mode="json"):
        raise ValueError(f"{label} changed since the authorized dry run")


def run_signature_embedding_execution(
    *,
    signature_index: ClaimSignatureIndexArtifact,
    candidate_recall: ViewpointCandidateRecallArtifact,
    authorized_projection: EmbeddingProjectionManifest,
    authorized_plan: EmbeddingGenerationPlan,
    authorized_budget: SignatureEmbeddingBudgetReport,
    output_dir: Path,
    top_k: int = 12,
) -> SignatureEmbeddingExecutionReport:
    provider_config = authorized_plan.provider
    rebuilt = build_signature_embedding_budget(
        signature_index=signature_index,
        model=provider_config.model,
        dimensions=provider_config.dimensions,
        batch_size=authorized_plan.max_batch_size,
        transport_mode=provider_config.transport_mode,
        endpoint_location=provider_config.endpoint_location,
    )
    _assert_exact(rebuilt["projection_manifest"], authorized_projection, "projection manifest")
    _assert_exact(rebuilt["plan"], authorized_plan, "embedding plan")
    _assert_exact(rebuilt["summary"], authorized_budget, "embedding budget")
    provider = GoogleGeminiEmbeddingProvider(
        model=provider_config.model,
        dimensions=provider_config.dimensions,
        batch_size=authorized_plan.max_batch_size,
        transport_mode=provider_config.transport_mode,
        endpoint_location=provider_config.endpoint_location,
    )
    embedding_index, executed, reused = execute_embedding_plan(
        projection_manifest=authorized_projection,
        plan=authorized_plan,
        provider=provider,
        batch_dir=output_dir / "batches",
    )
    postflight = build_signature_embedding_budget(
        signature_index=signature_index,
        model=provider_config.model,
        dimensions=provider_config.dimensions,
        batch_size=authorized_plan.max_batch_size,
        transport_mode=provider_config.transport_mode,
        endpoint_location=provider_config.endpoint_location,
    )
    _assert_exact(postflight["projection_manifest"], authorized_projection, "postflight manifest")
    _assert_exact(postflight["plan"], authorized_plan, "postflight plan")
    signature_recall = build_signature_recall(
        signature_index=signature_index,
        embedding_index=embedding_index,
        top_k=top_k,
    )
    final_graph = build_final_candidate_graph(
        candidate_recall=candidate_recall,
        signature_recall=signature_recall,
        signature_index=signature_index,
    )
    _write_new(output_dir / "signature-embedding-index.json", embedding_index)
    _write_new(output_dir / "signature-recall-report.json", signature_recall)
    _write_new(output_dir / "final-candidate-graph.json", final_graph)
    payload = {
        "schema_version": "wang_claim_signature_embedding_execution_v1",
        "signature_index_sha256": signature_index.artifact_sha256,
        "projection_manifest_sha256": authorized_projection.artifact_sha256,
        "plan_sha256": authorized_plan.plan_sha256,
        "embedding_index_sha256": embedding_index.artifact_sha256,
        "signature_recall_artifact_sha256": signature_recall.artifact_sha256,
        "candidate_recall_artifact_sha256": candidate_recall.artifact_sha256,
        "final_candidate_graph_sha256": final_graph.artifact_sha256,
        "completed_batch_ids": sorted(batch.batch_id for batch in authorized_plan.batches),
        "provider_calls_executed_this_run": executed,
        "reused_batch_count": reused,
        "signature_count": len(signature_index.signatures),
        "semantic_atom_count": signature_index.statistics["semantic_atom_count"],
        "final_candidate_pair_count": final_graph.statistics["union_unique_pair_count"],
        "master_data_mutations": 0,
        "recall_only": True,
        "identity_evidence": False,
        "apply_allowed": False,
    }
    report = SignatureEmbeddingExecutionReport(
        **payload, artifact_sha256=sha256_json(payload)
    )
    _write_new(
        output_dir / "execution-reports" / f"{report.artifact_sha256}.json",
        report,
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signature-index", type=Path, required=True)
    parser.add_argument("--candidate-recall", type=Path, required=True)
    parser.add_argument("--authorized-plan-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=12)
    args = parser.parse_args()
    report = run_signature_embedding_execution(
        signature_index=ClaimSignatureIndexArtifact.model_validate(
            _read(args.signature_index)
        ),
        candidate_recall=ViewpointCandidateRecallArtifact.model_validate(
            _read(args.candidate_recall)
        ),
        authorized_projection=EmbeddingProjectionManifest.model_validate(
            _read(args.authorized_plan_dir / "signature-embedding-projection-manifest.json")
        ),
        authorized_plan=EmbeddingGenerationPlan.model_validate(
            _read(args.authorized_plan_dir / "signature-embedding-plan.json")
        ),
        authorized_budget=SignatureEmbeddingBudgetReport.model_validate(
            _read(args.authorized_plan_dir / "signature-embedding-budget.json")
        ),
        output_dir=args.output_dir,
        top_k=args.top_k,
    )
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
