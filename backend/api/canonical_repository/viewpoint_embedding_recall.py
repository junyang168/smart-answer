"""Bounded embedding recall for CanonicalViewpoint bootstrap.

This artifact only says which Claim pairs deserve semantic comparison.  It is
not identity evidence and cannot create a ClaimRelation or viewpoint member.
"""

from __future__ import annotations

import heapq
from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..semantic_index.embeddings import (
    EmbeddingIndexArtifact,
    EmbeddingProviderDescriptor,
    EmbeddingVectorRecord,
)
from .viewpoint_foundation import sha256_json


EMBEDDING_RECALL_VERSION = "wang_viewpoint_embedding_recall_v1"
DEFAULT_TOP_K = 12
MAX_EXACT_RECALL_RECORDS = 2_000
EXACT_RECALL_MATRIX_BLOCK = 128


class StrictRecallModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmbeddingRecallNeighbor(StrictRecallModel):
    claim_id: str
    rank: int = Field(ge=1)
    cosine_similarity: float = Field(ge=-1, le=1)
    projection_sha256: str
    signal_kind: Literal["embedding_neighbor"] = "embedding_neighbor"
    identity_evidence: Literal[False] = False


class EmbeddingRecallNeighborhood(StrictRecallModel):
    focal_claim_id: str
    focal_projection_sha256: str
    neighbors: list[EmbeddingRecallNeighbor]

    @model_validator(mode="after")
    def validate_neighbors(self) -> "EmbeddingRecallNeighborhood":
        ids = [item.claim_id for item in self.neighbors]
        if self.focal_claim_id in ids or len(ids) != len(set(ids)):
            raise ValueError("embedding recall neighbors must be unique and exclude self")
        if [item.rank for item in self.neighbors] != list(
            range(1, len(self.neighbors) + 1)
        ):
            raise ValueError("embedding recall ranks must be contiguous")
        expected_order = sorted(
            self.neighbors,
            key=lambda item: (-item.cosine_similarity, item.claim_id),
        )
        if self.neighbors != expected_order:
            raise ValueError("embedding recall neighbors must use score/id order")
        return self


class ViewpointEmbeddingRecallArtifact(StrictRecallModel):
    schema_version: Literal["wang_viewpoint_embedding_recall_v1"] = (
        EMBEDDING_RECALL_VERSION
    )
    claim_manifest_sha256: str
    embedding_index_sha256: str
    provider: EmbeddingProviderDescriptor
    top_k: int = Field(ge=1)
    minimum_similarity: float | None = Field(default=None, ge=-1, le=1)
    neighborhoods: list[EmbeddingRecallNeighborhood]
    uncovered_claim_ids: list[str]
    source_ineligible_claim_ids: list[str]
    statistics: dict[str, int]
    recall_only: Literal[True] = True
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "ViewpointEmbeddingRecallArtifact":
        focal_ids = [item.focal_claim_id for item in self.neighborhoods]
        if focal_ids != sorted(set(focal_ids)):
            raise ValueError("embedding recall focal Claims must use canonical order")
        expected_uncovered = sorted(
            item.focal_claim_id for item in self.neighborhoods if not item.neighbors
        )
        if self.uncovered_claim_ids != expected_uncovered:
            raise ValueError("embedding recall uncovered Claims mismatch")
        if self.source_ineligible_claim_ids != sorted(
            set(self.source_ineligible_claim_ids)
        ):
            raise ValueError("source-ineligible Claim ids must be sorted and unique")
        if set(focal_ids) & set(self.source_ineligible_claim_ids):
            raise ValueError("Claim cannot be both embedding-eligible and source-ineligible")
        unique_pairs = {
            tuple(sorted((item.focal_claim_id, neighbor.claim_id)))
            for item in self.neighborhoods
            for neighbor in item.neighbors
        }
        expected = {
            "input_claim_count": len(self.neighborhoods)
            + len(self.source_ineligible_claim_ids),
            "eligible_claim_count": len(self.neighborhoods),
            "source_ineligible_claim_count": len(self.source_ineligible_claim_ids),
            "covered_claim_count": len(self.neighborhoods) - len(expected_uncovered),
            "uncovered_claim_count": len(expected_uncovered),
            "directed_neighbor_count": sum(
                len(item.neighbors) for item in self.neighborhoods
            ),
            "unique_candidate_pair_count": len(unique_pairs),
        }
        if self.statistics != expected:
            raise ValueError("embedding recall statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("embedding recall artifact SHA mismatch")
        return self


def build_viewpoint_embedding_recall(
    *,
    claim_manifest: Mapping[str, Any],
    embedding_index: EmbeddingIndexArtifact,
    source_ineligible_claim_ids: list[str] | None = None,
    top_k: int = DEFAULT_TOP_K,
    minimum_similarity: float | None = None,
) -> ViewpointEmbeddingRecallArtifact:
    """Return top-K Claim neighbors from one manifest-bound embedding index."""

    if top_k < 1:
        raise ValueError("top_k must be positive")
    if minimum_similarity is not None and not -1 <= minimum_similarity <= 1:
        raise ValueError("minimum_similarity must be between -1 and 1")
    manifest_sha = str(claim_manifest.get("manifest_sha256") or "")
    expected_manifest_sha = sha256_json(
        {key: value for key, value in claim_manifest.items() if key != "manifest_sha256"}
    )
    if not manifest_sha or manifest_sha != expected_manifest_sha:
        raise ValueError("Claim manifest SHA mismatch")
    manifest_ids = sorted(str(item["claim_id"]) for item in claim_manifest.get("claims") or [])
    ineligible_ids = sorted(set(source_ineligible_claim_ids or []))
    if not set(ineligible_ids) <= set(manifest_ids):
        raise ValueError("source-ineligible Claim ids must belong to the Claim manifest")
    eligible_ids = sorted(set(manifest_ids) - set(ineligible_ids))
    if len(eligible_ids) > MAX_EXACT_RECALL_RECORDS:
        raise ValueError(
            "exact embedding recall is limited to 2000 records; use a bounded ANN index"
        )
    records = embedding_index.records
    record_ids = [item.object_id for item in records]
    if record_ids != eligible_ids:
        raise ValueError(
            "embedding index plus source-ineligible disposition must exactly cover "
            "the Claim manifest"
        )
    if any(item.object_kind != "claim" for item in records):
        raise ValueError("viewpoint embedding recall accepts only Claim embeddings")
    index: dict[str, EmbeddingVectorRecord] = {item.object_id: item for item in records}
    matrix = np.asarray([index[claim_id].vector for claim_id in eligible_ids], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(norms == 0) or not np.all(np.isfinite(norms)):
        raise ValueError("embedding recall requires finite non-zero vectors")
    normalized = matrix / norms[:, None]
    neighborhoods: list[EmbeddingRecallNeighborhood] = []
    for block_start in range(0, len(eligible_ids), EXACT_RECALL_MATRIX_BLOCK):
        block_end = min(block_start + EXACT_RECALL_MATRIX_BLOCK, len(eligible_ids))
        score_block = normalized[block_start:block_end] @ normalized.T
        score_block = np.clip(score_block, -1.0, 1.0)
        for offset, scores in enumerate(score_block):
            focal_index = block_start + offset
            focal_id = eligible_ids[focal_index]
            focal = index[focal_id]
            candidate_indices = [
                candidate_index
                for candidate_index, score in enumerate(scores)
                if candidate_index != focal_index
                and (minimum_similarity is None or float(score) >= minimum_similarity)
            ]
            selected = heapq.nsmallest(
                top_k,
                candidate_indices,
                key=lambda candidate_index: (
                    -float(scores[candidate_index]),
                    eligible_ids[candidate_index],
                ),
            )
            neighbors = [
                EmbeddingRecallNeighbor(
                    claim_id=eligible_ids[candidate_index],
                    rank=rank,
                    cosine_similarity=float(scores[candidate_index]),
                    projection_sha256=index[
                        eligible_ids[candidate_index]
                    ].projection_sha256,
                )
                for rank, candidate_index in enumerate(selected, start=1)
            ]
            neighborhoods.append(
                EmbeddingRecallNeighborhood(
                    focal_claim_id=focal_id,
                    focal_projection_sha256=focal.projection_sha256,
                    neighbors=neighbors,
                )
            )
    uncovered = sorted(
        item.focal_claim_id for item in neighborhoods if not item.neighbors
    )
    unique_pairs = {
        tuple(sorted((item.focal_claim_id, neighbor.claim_id)))
        for item in neighborhoods
        for neighbor in item.neighbors
    }
    statistics = {
        "input_claim_count": len(neighborhoods) + len(ineligible_ids),
        "eligible_claim_count": len(neighborhoods),
        "source_ineligible_claim_count": len(ineligible_ids),
        "covered_claim_count": len(neighborhoods) - len(uncovered),
        "uncovered_claim_count": len(uncovered),
        "directed_neighbor_count": sum(len(item.neighbors) for item in neighborhoods),
        "unique_candidate_pair_count": len(unique_pairs),
    }
    payload: dict[str, Any] = {
        "schema_version": EMBEDDING_RECALL_VERSION,
        "claim_manifest_sha256": manifest_sha,
        "embedding_index_sha256": embedding_index.artifact_sha256,
        "provider": embedding_index.provider.model_dump(mode="json"),
        "top_k": top_k,
        "minimum_similarity": minimum_similarity,
        "neighborhoods": [item.model_dump(mode="json") for item in neighborhoods],
        "uncovered_claim_ids": uncovered,
        "source_ineligible_claim_ids": ineligible_ids,
        "statistics": statistics,
        "recall_only": True,
    }
    return ViewpointEmbeddingRecallArtifact(
        **payload,
        artifact_sha256=sha256_json(payload),
    )
