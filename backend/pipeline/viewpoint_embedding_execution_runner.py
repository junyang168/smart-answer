"""Execute a pinned Claim embedding plan with fail-closed resume semantics."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_candidate_recall import (
    build_viewpoint_candidate_recall,
)
from backend.api.canonical_repository.viewpoint_embedding_recall import (
    DEFAULT_TOP_K,
    build_viewpoint_embedding_recall,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_recall_blocking import (
    ViewpointRecallBlockingArtifact,
)
from backend.api.semantic_index.embeddings import (
    EmbeddingGenerationPlan,
    EmbeddingIndexArtifact,
    EmbeddingProjection,
    EmbeddingProjectionManifest,
    EmbeddingProvider,
    EmbeddingVectorRecord,
    GoogleGeminiEmbeddingProvider,
    build_embedding_index_artifact,
)
from backend.pipeline.viewpoint_embedding_plan_runner import (
    ClaimEmbeddingBudgetReport,
    build_claim_embedding_budget,
)


BATCH_RESULT_VERSION = "wang_semantic_embedding_batch_result_v1"
EXECUTION_REPORT_VERSION = "wang_viewpoint_claim_embedding_execution_v1"


class StrictExecutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmbeddingBatchResult(StrictExecutionModel):
    schema_version: Literal["wang_semantic_embedding_batch_result_v1"] = (
        BATCH_RESULT_VERSION
    )
    plan_sha256: str
    batch_id: str
    batch_fingerprint_sha256: str
    provider: dict[str, Any]
    records: list[EmbeddingVectorRecord]
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "EmbeddingBatchResult":
        ids = [item.object_id for item in self.records]
        if ids != sorted(set(ids)):
            raise ValueError("embedding batch records must use canonical order")
        dimensions = int(self.provider.get("dimensions") or 0)
        if not dimensions or any(len(item.vector) != dimensions for item in self.records):
            raise ValueError("embedding batch vector dimensions mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("embedding batch result SHA mismatch")
        return self


class EmbeddingExecutionReport(StrictExecutionModel):
    schema_version: Literal["wang_viewpoint_claim_embedding_execution_v1"] = (
        EXECUTION_REPORT_VERSION
    )
    claim_manifest_sha256: str
    projection_manifest_sha256: str
    plan_sha256: str
    embedding_index_sha256: str
    rule_recall_artifact_sha256: str
    embedding_recall_artifact_sha256: str
    candidate_recall_artifact_sha256: str
    completed_batch_ids: list[str]
    provider_calls_executed_this_run: int = Field(ge=0)
    reused_batch_count: int = Field(ge=0)
    total_provider_calls_for_index: int = Field(ge=0)
    input_claim_count: int = Field(ge=0)
    embedded_claim_count: int = Field(ge=0)
    source_ineligible_claim_count: int = Field(ge=0)
    master_data_mutations: Literal[0] = 0
    recall_only: Literal[True] = True
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_report(self) -> "EmbeddingExecutionReport":
        if self.completed_batch_ids != sorted(set(self.completed_batch_ids)):
            raise ValueError("completed batch ids must use canonical order")
        if self.total_provider_calls_for_index != len(self.completed_batch_ids):
            raise ValueError("completed provider call count mismatch")
        if self.provider_calls_executed_this_run + self.reused_batch_count != len(
            self.completed_batch_ids
        ):
            raise ValueError("execution/reuse batch count mismatch")
        if self.embedded_claim_count + self.source_ineligible_claim_count != self.input_claim_count:
            raise ValueError("embedding execution Claim denominator mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("embedding execution report SHA mismatch")
        return self


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _assert_exact_artifact(expected: BaseModel, actual: BaseModel, label: str) -> None:
    if expected.model_dump(mode="json") != actual.model_dump(mode="json"):
        raise ValueError(f"{label} changed since the authorized dry run")


def execute_embedding_plan(
    *,
    projection_manifest: EmbeddingProjectionManifest,
    plan: EmbeddingGenerationPlan,
    provider: EmbeddingProvider,
    batch_dir: Path,
) -> tuple[EmbeddingIndexArtifact, int, int]:
    """Execute missing batches and reuse only fully validated immutable results."""

    if plan.projection_manifest_sha256 != projection_manifest.artifact_sha256:
        raise ValueError("embedding plan does not bind the supplied projection manifest")
    if provider.descriptor != plan.provider:
        raise ValueError("embedding provider descriptor differs from the pinned plan")
    projection_index = {
        item.object_id: item for item in projection_manifest.projections
    }
    plan_items = {item.object_id: item for item in plan.items}
    vectors: dict[str, Sequence[float]] = {}
    executed = 0
    reused = 0
    for batch in plan.batches:
        path = batch_dir / f"{batch.batch_id}.json"
        result: EmbeddingBatchResult | None = None
        if path.exists():
            result = EmbeddingBatchResult.model_validate(_read(path))
            expected_ids = batch.item_ids
            if (
                result.plan_sha256 != plan.plan_sha256
                or result.batch_id != batch.batch_id
                or result.batch_fingerprint_sha256 != batch.batch_fingerprint_sha256
                or result.provider != plan.provider.model_dump(mode="json")
                or [item.object_id for item in result.records] != expected_ids
                or [item.projection_sha256 for item in result.records]
                != [plan_items[item_id].projection_sha256 for item_id in expected_ids]
            ):
                raise ValueError(f"{batch.batch_id}: existing batch result mismatch")
            reused += 1
        else:
            projections = [projection_index[item_id] for item_id in batch.item_ids]
            batch_vectors = provider.embed_documents(projections, plan.use_case)
            if len(batch_vectors) != len(projections):
                raise ValueError(f"{batch.batch_id}: provider result count mismatch")
            records = [
                EmbeddingVectorRecord(
                    object_kind=projection.object_kind,
                    object_id=projection.object_id,
                    projection_sha256=projection.projection_sha256,
                    vector=list(vector),
                    vector_sha256=sha256_json(list(vector)),
                )
                for projection, vector in zip(projections, batch_vectors, strict=True)
            ]
            payload = {
                "schema_version": BATCH_RESULT_VERSION,
                "plan_sha256": plan.plan_sha256,
                "batch_id": batch.batch_id,
                "batch_fingerprint_sha256": batch.batch_fingerprint_sha256,
                "provider": plan.provider.model_dump(mode="json"),
                "records": [item.model_dump(mode="json") for item in records],
            }
            result = EmbeddingBatchResult(
                **payload, artifact_sha256=sha256_json(payload)
            )
            _write(path, result)
            executed += 1
        for record in result.records:
            if record.object_id in vectors:
                raise ValueError("embedding batch results overlap")
            vectors[record.object_id] = record.vector
    return (
        build_embedding_index_artifact(
            plan=plan,
            projections=projection_manifest.projections,
            vectors_by_object_id=vectors,
        ),
        executed,
        reused,
    )


def run_execution(
    *,
    claim_manifest_path: Path,
    authorized_plan_dir: Path,
    rule_recall_path: Path,
    output_dir: Path,
    database_url: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> EmbeddingExecutionReport:
    claim_manifest = _read(claim_manifest_path)
    authorized_projection = EmbeddingProjectionManifest.model_validate(
        _read(authorized_plan_dir / "claim-embedding-projection-manifest.json")
    )
    authorized_plan = EmbeddingGenerationPlan.model_validate(
        _read(authorized_plan_dir / "claim-embedding-plan.json")
    )
    authorized_budget = ClaimEmbeddingBudgetReport.model_validate(
        _read(authorized_plan_dir / "claim-embedding-budget.json")
    )
    store = PostgresKnowledgeStore(database_url)
    rebuilt = build_claim_embedding_budget(
        claim_manifest=claim_manifest,
        claims=store.list_records("claims"),
        model=authorized_plan.provider.model,
        dimensions=authorized_plan.provider.dimensions,
        batch_size=authorized_plan.max_batch_size,
        transport_mode=authorized_plan.provider.transport_mode,
        endpoint_location=authorized_plan.provider.endpoint_location,
    )
    _assert_exact_artifact(rebuilt["projection_manifest"], authorized_projection, "projection manifest")
    _assert_exact_artifact(rebuilt["plan"], authorized_plan, "embedding plan")
    _assert_exact_artifact(rebuilt["summary"], authorized_budget, "embedding budget")

    provider = GoogleGeminiEmbeddingProvider(
        model=authorized_plan.provider.model,
        dimensions=authorized_plan.provider.dimensions,
        batch_size=authorized_plan.max_batch_size,
        transport_mode=authorized_plan.provider.transport_mode,
        endpoint_location=authorized_plan.provider.endpoint_location,
    )
    index, executed, reused = execute_embedding_plan(
        projection_manifest=authorized_projection,
        plan=authorized_plan,
        provider=provider,
        batch_dir=output_dir / "batches",
    )

    # Rebuild after external calls so a mid-run source mutation cannot be published.
    postflight = build_claim_embedding_budget(
        claim_manifest=claim_manifest,
        claims=store.list_records("claims"),
        model=authorized_plan.provider.model,
        dimensions=authorized_plan.provider.dimensions,
        batch_size=authorized_plan.max_batch_size,
        transport_mode=authorized_plan.provider.transport_mode,
        endpoint_location=authorized_plan.provider.endpoint_location,
    )
    _assert_exact_artifact(postflight["projection_manifest"], authorized_projection, "postflight projection manifest")
    _assert_exact_artifact(postflight["plan"], authorized_plan, "postflight embedding plan")

    rule_recall = ViewpointRecallBlockingArtifact.model_validate(_read(rule_recall_path))
    embedding_recall = build_viewpoint_embedding_recall(
        claim_manifest=claim_manifest,
        embedding_index=index,
        source_ineligible_claim_ids=authorized_budget.source_ineligible_claim_ids,
        top_k=top_k,
    )
    candidate_recall = build_viewpoint_candidate_recall(
        rule_recall=rule_recall, embedding_recall=embedding_recall
    )
    _write(output_dir / "claim-embedding-index.json", index)
    _write(output_dir / "embedding-recall-report.json", embedding_recall)
    _write(output_dir / "candidate-recall-report.json", candidate_recall)
    payload = {
        "schema_version": EXECUTION_REPORT_VERSION,
        "claim_manifest_sha256": claim_manifest["manifest_sha256"],
        "projection_manifest_sha256": authorized_projection.artifact_sha256,
        "plan_sha256": authorized_plan.plan_sha256,
        "embedding_index_sha256": index.artifact_sha256,
        "rule_recall_artifact_sha256": rule_recall.artifact_sha256,
        "embedding_recall_artifact_sha256": embedding_recall.artifact_sha256,
        "candidate_recall_artifact_sha256": candidate_recall.artifact_sha256,
        "completed_batch_ids": sorted(batch.batch_id for batch in authorized_plan.batches),
        "provider_calls_executed_this_run": executed,
        "reused_batch_count": reused,
        "total_provider_calls_for_index": len(authorized_plan.batches),
        "input_claim_count": authorized_budget.input_claim_count,
        "embedded_claim_count": len(index.records),
        "source_ineligible_claim_count": authorized_budget.source_ineligible_claim_count,
        "master_data_mutations": 0,
        "recall_only": True,
    }
    report = EmbeddingExecutionReport(**payload, artifact_sha256=sha256_json(payload))
    _write(
        output_dir / "execution-reports" / f"{report.artifact_sha256}.json",
        report,
    )
    _write(output_dir / "embedding-execution-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claim-manifest", type=Path, required=True)
    parser.add_argument("--authorized-plan-dir", type=Path, required=True)
    parser.add_argument("--rule-recall", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    args = parser.parse_args()
    report = run_execution(
        claim_manifest_path=args.claim_manifest,
        authorized_plan_dir=args.authorized_plan_dir,
        rule_recall_path=args.rule_recall,
        output_dir=args.output_dir,
        database_url=args.database_url,
        top_k=args.top_k,
    )
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
