"""Build a no-call embedding projection and budget plan for a Claim cohort."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.canonical_repository.knowledge_models import ClaimRecord
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import (
    semantic_record_sha,
    sha256_json,
)
from backend.api.canonical_repository.viewpoint_recall_blocking import (
    INELIGIBLE_REVIEW_STATUSES,
)
from backend.api.semantic_index.embeddings import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_DIMENSIONS,
    DEFAULT_GEMINI_MODEL,
    GoogleGeminiEmbeddingProvider,
    build_embedding_generation_plan,
    build_embedding_projection_manifest,
)
from backend.api.semantic_index.projections import build_claim_embedding_projection


class ClaimEmbeddingBudgetReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["wang_viewpoint_claim_embedding_budget_v1"] = (
        "wang_viewpoint_claim_embedding_budget_v1"
    )
    claim_manifest_sha256: str
    projection_manifest_sha256: str
    plan_sha256: str
    model_calls_executed: Literal[0] = 0
    estimated_provider_call_count: int = Field(ge=0)
    apply_allowed: Literal[False] = False
    projection_count: int = Field(ge=0)
    input_claim_count: int = Field(ge=0)
    source_ineligible_claim_count: int = Field(ge=0)
    source_ineligible_claim_ids: list[str]
    batch_count: int = Field(ge=0)
    input_bytes: int = Field(ge=0)
    estimated_input_tokens: int = Field(ge=0)
    model: str
    dimensions: int = Field(ge=1)
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_report(self) -> "ClaimEmbeddingBudgetReport":
        if self.source_ineligible_claim_ids != sorted(
            set(self.source_ineligible_claim_ids)
        ):
            raise ValueError("source-ineligible Claim ids must be sorted and unique")
        if self.source_ineligible_claim_count != len(self.source_ineligible_claim_ids):
            raise ValueError("source-ineligible Claim count mismatch")
        if self.projection_count + self.source_ineligible_claim_count != self.input_claim_count:
            raise ValueError("embedding budget Claim denominator mismatch")
        if self.estimated_provider_call_count != self.batch_count:
            raise ValueError("embedding budget provider call estimate mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("embedding budget artifact SHA mismatch")
        return self


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def build_claim_embedding_budget(
    *,
    claim_manifest: Mapping[str, Any],
    claims: Sequence[Mapping[str, Any] | ClaimRecord],
    model: str = DEFAULT_GEMINI_MODEL,
    dimensions: int = DEFAULT_DIMENSIONS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    transport_mode: Literal[
        "gemini_developer_multi_content", "vertex_single_content"
    ] = "gemini_developer_multi_content",
    endpoint_location: str = "global",
) -> dict[str, Any]:
    if transport_mode == "vertex_single_content" and batch_size != 1:
        raise ValueError("Vertex sync embedding plans require batch_size=1 for safe resume")
    unsigned_manifest = {
        key: value for key, value in claim_manifest.items() if key != "manifest_sha256"
    }
    if claim_manifest.get("manifest_sha256") != sha256_json(unsigned_manifest):
        raise ValueError("Claim manifest SHA mismatch")
    claim_index = {
        claim.claim_id: claim
        for value in claims
        for claim in [
            value if isinstance(value, ClaimRecord) else ClaimRecord.model_validate(value)
        ]
    }
    rows = sorted(claim_manifest.get("claims") or [], key=lambda item: item["claim_id"])
    row_ids = [str(item["claim_id"]) for item in rows]
    if row_ids != sorted(set(row_ids)):
        raise ValueError("Claim manifest ids must be sorted and unique")
    selected: list[ClaimRecord] = []
    for row in rows:
        claim_id = str(row["claim_id"])
        claim = claim_index.get(claim_id)
        if claim is None:
            raise ValueError(f"{claim_id}: Claim missing from authoring store")
        if claim.revision != int(row["pinned_claim_revision"]):
            raise ValueError(f"{claim_id}: Claim revision changed")
        if semantic_record_sha(claim) != row["claim_revision_sha256"]:
            raise ValueError(f"{claim_id}: Claim SHA changed")
        selected.append(claim)
    eligible = [
        claim for claim in selected if claim.review_status not in INELIGIBLE_REVIEW_STATUSES
    ]
    source_ineligible_ids = sorted(
        claim.claim_id
        for claim in selected
        if claim.review_status in INELIGIBLE_REVIEW_STATUSES
    )
    projections = [build_claim_embedding_projection(claim) for claim in eligible]
    projection_manifest = build_embedding_projection_manifest(projections)
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
    if plan.projection_manifest_sha256 != projection_manifest.artifact_sha256:
        raise ValueError("embedding plan does not bind projection manifest")
    budget_payload = {
        "schema_version": "wang_viewpoint_claim_embedding_budget_v1",
        "claim_manifest_sha256": claim_manifest["manifest_sha256"],
        "projection_manifest_sha256": projection_manifest.artifact_sha256,
        "plan_sha256": plan.plan_sha256,
        "model_calls_executed": 0,
        "estimated_provider_call_count": plan.statistics["batch_count"],
        "apply_allowed": False,
        "projection_count": plan.statistics["projection_count"],
        "input_claim_count": len(selected),
        "source_ineligible_claim_count": len(source_ineligible_ids),
        "source_ineligible_claim_ids": source_ineligible_ids,
        "batch_count": plan.statistics["batch_count"],
        "input_bytes": plan.statistics["input_bytes"],
        "estimated_input_tokens": plan.statistics["estimated_input_tokens"],
        "model": plan.provider.model,
        "dimensions": plan.provider.dimensions,
    }
    budget = ClaimEmbeddingBudgetReport(
        **budget_payload, artifact_sha256=sha256_json(budget_payload)
    )
    return {
        "projection_manifest": projection_manifest,
        "plan": plan,
        "summary": budget,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claim-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--database-url")
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

    store = PostgresKnowledgeStore(args.database_url)
    artifacts = build_claim_embedding_budget(
        claim_manifest=_read(args.claim_manifest),
        claims=store.list_records("claims"),
        model=args.model,
        dimensions=args.dimensions,
        batch_size=args.batch_size,
        transport_mode=args.transport_mode,
        endpoint_location=args.endpoint_location,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write(
        args.output_dir / "claim-embedding-projection-manifest.json",
        artifacts["projection_manifest"],
    )
    _write(args.output_dir / "claim-embedding-plan.json", artifacts["plan"])
    _write(args.output_dir / "claim-embedding-budget.json", artifacts["summary"])
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
