"""Signature-aware recall and lossless union with the baseline Claim graph."""

from __future__ import annotations

import heapq
from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..semantic_index.embeddings import EmbeddingIndexArtifact, EmbeddingProviderDescriptor
from .viewpoint_candidate_recall import (
    CandidateRecallNeighbor,
    ViewpointCandidateRecallArtifact,
)
from .viewpoint_claim_signature import ClaimSignatureIndexArtifact
from .viewpoint_foundation import sha256_json


DEFAULT_SIGNATURE_TOP_K = 12
MAX_EXACT_SIGNATURE_RECORDS = 2_000
EXACT_MATRIX_BLOCK = 128


class StrictSignatureRecallModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignatureRecallNeighbor(StrictSignatureRecallModel):
    claim_id: str
    rank: int = Field(ge=1)
    cosine_similarity: float = Field(ge=-1, le=1)
    projection_sha256: str
    identity_evidence: Literal[False] = False


class SignatureRecallNeighborhood(StrictSignatureRecallModel):
    focal_claim_id: str
    focal_projection_sha256: str
    neighbors: list[SignatureRecallNeighbor]

    @model_validator(mode="after")
    def validate_neighbors(self) -> "SignatureRecallNeighborhood":
        ids = [item.claim_id for item in self.neighbors]
        if self.focal_claim_id in ids or len(ids) != len(set(ids)):
            raise ValueError("signature recall neighbors must be unique and exclude self")
        if [item.rank for item in self.neighbors] != list(range(1, len(ids) + 1)):
            raise ValueError("signature recall ranks must be contiguous")
        if self.neighbors != sorted(
            self.neighbors, key=lambda item: (-item.cosine_similarity, item.claim_id)
        ):
            raise ValueError("signature recall neighbors must use score/id order")
        return self


class ViewpointSignatureRecallArtifact(StrictSignatureRecallModel):
    schema_version: Literal["wang_viewpoint_signature_recall_v1"] = (
        "wang_viewpoint_signature_recall_v1"
    )
    signature_index_sha256: str
    retrieval_method: Literal["embedding"]
    embedding_index_sha256: str
    provider: EmbeddingProviderDescriptor
    retrieval_config_sha256: str
    top_k: int = Field(ge=1)
    neighborhoods: list[SignatureRecallNeighborhood]
    source_ineligible_claim_ids: list[str]
    statistics: dict[str, int]
    recall_only: Literal[True] = True
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "ViewpointSignatureRecallArtifact":
        ids = [item.focal_claim_id for item in self.neighborhoods]
        if ids != sorted(set(ids)):
            raise ValueError("signature recall focal Claims must use canonical order")
        if self.source_ineligible_claim_ids != sorted(set(self.source_ineligible_claim_ids)):
            raise ValueError("signature recall ineligible Claims must be canonical")
        pairs = {
            tuple(sorted((item.focal_claim_id, neighbor.claim_id)))
            for item in self.neighborhoods for neighbor in item.neighbors
        }
        expected = {
            "eligible_claim_count": len(ids),
            "source_ineligible_claim_count": len(self.source_ineligible_claim_ids),
            "directed_neighbor_count": sum(len(item.neighbors) for item in self.neighborhoods),
            "unique_candidate_pair_count": len(pairs),
        }
        if self.statistics != expected:
            raise ValueError("signature recall statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("signature recall artifact SHA mismatch")
        return self


class BaselineDirectionSignal(StrictSignatureRecallModel):
    focal_claim_id: str
    neighbor: CandidateRecallNeighbor


class SignatureDirectionSignal(StrictSignatureRecallModel):
    focal_claim_id: str
    neighbor: SignatureRecallNeighbor


class FinalCandidateEdge(StrictSignatureRecallModel):
    claim_ids: list[str] = Field(min_length=2, max_length=2)
    channels: list[Literal["embedding", "rule", "signature"]]
    baseline_directions: list[BaselineDirectionSignal]
    signature_directions: list[SignatureDirectionSignal]
    identity_evidence: Literal[False] = False

    @model_validator(mode="after")
    def validate_edge(self) -> "FinalCandidateEdge":
        if self.claim_ids != sorted(set(self.claim_ids)):
            raise ValueError("final candidate edge Claim ids must be canonical")
        expected = set()
        for direction in self.baseline_directions:
            expected.update(direction.neighbor.channels)
        if self.signature_directions:
            expected.add("signature")
        if self.channels != sorted(expected) or not expected:
            raise ValueError("final candidate edge channels do not match provenance")
        for focal, neighbor_id in [
            (item.focal_claim_id, item.neighbor.claim_id)
            for item in self.baseline_directions
        ] + [
            (item.focal_claim_id, item.neighbor.claim_id)
            for item in self.signature_directions
        ]:
            if sorted((focal, neighbor_id)) != self.claim_ids:
                raise ValueError("candidate edge provenance belongs to another pair")
        return self


class ViewpointFinalCandidateGraphArtifact(StrictSignatureRecallModel):
    schema_version: Literal["wang_viewpoint_final_candidate_graph_v1"] = (
        "wang_viewpoint_final_candidate_graph_v1"
    )
    candidate_recall_artifact_sha256: str
    signature_recall_artifact_sha256: str
    signature_index_sha256: str
    edges: list[FinalCandidateEdge]
    source_ineligible_claim_ids: list[str]
    statistics: dict[str, int]
    recall_only: Literal[True] = True
    identity_evidence: Literal[False] = False
    apply_allowed: Literal[False] = False
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_graph(self) -> "ViewpointFinalCandidateGraphArtifact":
        pairs = [edge.claim_ids for edge in self.edges]
        if pairs != sorted(pairs) or len({tuple(pair) for pair in pairs}) != len(pairs):
            raise ValueError("final candidate graph edges must be canonical and unique")
        baseline = sum(bool(edge.baseline_directions) for edge in self.edges)
        signature = sum(bool(edge.signature_directions) for edge in self.edges)
        overlap = sum(
            bool(edge.baseline_directions) and bool(edge.signature_directions)
            for edge in self.edges
        )
        expected = {
            "baseline_unique_pair_count": baseline,
            "signature_unique_pair_count": signature,
            "overlap_unique_pair_count": overlap,
            "signature_added_pair_count": signature - overlap,
            "union_unique_pair_count": len(self.edges),
        }
        if self.statistics != expected:
            raise ValueError("final candidate graph statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("final candidate graph SHA mismatch")
        return self


def build_signature_recall(
    *,
    signature_index: ClaimSignatureIndexArtifact,
    embedding_index: EmbeddingIndexArtifact,
    top_k: int = DEFAULT_SIGNATURE_TOP_K,
) -> ViewpointSignatureRecallArtifact:
    if top_k < 1:
        raise ValueError("signature top_k must be positive")
    signatures = {item.claim_id: item for item in signature_index.signatures}
    eligible_ids = sorted(signatures)
    records = embedding_index.records
    if embedding_index.object_kind != "claim_signature":
        raise ValueError("signature recall requires claim_signature embeddings")
    if [record.object_id for record in records] != eligible_ids:
        raise ValueError("signature embedding index must exactly cover signatures")
    if len(records) > MAX_EXACT_SIGNATURE_RECORDS:
        raise ValueError("exact signature recall exceeds bounded corpus limit")
    matrix = np.asarray([record.vector for record in records], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(norms == 0) or not np.all(np.isfinite(norms)):
        raise ValueError("signature recall requires finite non-zero vectors")
    normalized = matrix / norms[:, None]
    record_by_id = {record.object_id: record for record in records}
    neighborhoods: list[SignatureRecallNeighborhood] = []
    for start in range(0, len(eligible_ids), EXACT_MATRIX_BLOCK):
        end = min(start + EXACT_MATRIX_BLOCK, len(eligible_ids))
        scores = np.clip(normalized[start:end] @ normalized.T, -1.0, 1.0)
        for offset, row in enumerate(scores):
            focal_index = start + offset
            focal_id = eligible_ids[focal_index]
            candidates = [index for index in range(len(eligible_ids)) if index != focal_index]
            selected = heapq.nsmallest(
                top_k,
                candidates,
                key=lambda index: (-float(row[index]), eligible_ids[index]),
            )
            neighbors = [
                SignatureRecallNeighbor(
                    claim_id=eligible_ids[index],
                    rank=rank,
                    cosine_similarity=float(row[index]),
                    projection_sha256=record_by_id[eligible_ids[index]].projection_sha256,
                )
                for rank, index in enumerate(selected, 1)
            ]
            neighborhoods.append(
                SignatureRecallNeighborhood(
                    focal_claim_id=focal_id,
                    focal_projection_sha256=record_by_id[focal_id].projection_sha256,
                    neighbors=neighbors,
                )
            )
    pairs = {
        tuple(sorted((item.focal_claim_id, neighbor.claim_id)))
        for item in neighborhoods for neighbor in item.neighbors
    }
    statistics = {
        "eligible_claim_count": len(eligible_ids),
        "source_ineligible_claim_count": len(signature_index.source_ineligible_claim_ids),
        "directed_neighbor_count": sum(len(item.neighbors) for item in neighborhoods),
        "unique_candidate_pair_count": len(pairs),
    }
    payload: dict[str, Any] = {
        "schema_version": "wang_viewpoint_signature_recall_v1",
        "signature_index_sha256": signature_index.artifact_sha256,
        "retrieval_method": "embedding",
        "embedding_index_sha256": embedding_index.artifact_sha256,
        "provider": embedding_index.provider.model_dump(mode="json"),
        "retrieval_config_sha256": sha256_json({
            "method": "embedding", "top_k": top_k,
            "embedding_index_sha256": embedding_index.artifact_sha256,
        }),
        "top_k": top_k,
        "neighborhoods": [item.model_dump(mode="json") for item in neighborhoods],
        "source_ineligible_claim_ids": signature_index.source_ineligible_claim_ids,
        "statistics": statistics,
        "recall_only": True,
    }
    return ViewpointSignatureRecallArtifact(
        **payload, artifact_sha256=sha256_json(payload)
    )


def build_final_candidate_graph(
    *,
    candidate_recall: ViewpointCandidateRecallArtifact,
    signature_recall: ViewpointSignatureRecallArtifact,
    signature_index: ClaimSignatureIndexArtifact,
) -> ViewpointFinalCandidateGraphArtifact:
    eligible = sorted(signature.claim_id for signature in signature_index.signatures)
    if [item.focal_claim_id for item in candidate_recall.neighborhoods] != eligible:
        raise ValueError("baseline recall and signature index Claim coverage differ")
    if [item.focal_claim_id for item in signature_recall.neighborhoods] != eligible:
        raise ValueError("signature recall and signature index Claim coverage differ")
    if candidate_recall.source_ineligible_claim_ids != signature_index.source_ineligible_claim_ids:
        raise ValueError("baseline recall and signature index dispositions differ")
    if signature_recall.signature_index_sha256 != signature_index.artifact_sha256:
        raise ValueError("signature recall belongs to another signature index")

    baseline: dict[tuple[str, str], list[BaselineDirectionSignal]] = {}
    for item in candidate_recall.neighborhoods:
        for neighbor in item.neighbors:
            pair = tuple(sorted((item.focal_claim_id, neighbor.claim_id)))
            baseline.setdefault(pair, []).append(
                BaselineDirectionSignal(focal_claim_id=item.focal_claim_id, neighbor=neighbor)
            )
    signature: dict[tuple[str, str], list[SignatureDirectionSignal]] = {}
    for item in signature_recall.neighborhoods:
        for neighbor in item.neighbors:
            pair = tuple(sorted((item.focal_claim_id, neighbor.claim_id)))
            signature.setdefault(pair, []).append(
                SignatureDirectionSignal(focal_claim_id=item.focal_claim_id, neighbor=neighbor)
            )
    edges = []
    for pair in sorted(set(baseline) | set(signature)):
        baseline_directions = sorted(
            baseline.get(pair, []), key=lambda item: item.focal_claim_id
        )
        signature_directions = sorted(
            signature.get(pair, []), key=lambda item: item.focal_claim_id
        )
        channels = {
            channel
            for direction in baseline_directions
            for channel in direction.neighbor.channels
        }
        if signature_directions:
            channels.add("signature")
        edges.append(
            FinalCandidateEdge(
                claim_ids=list(pair),
                channels=sorted(channels),
                baseline_directions=baseline_directions,
                signature_directions=signature_directions,
            )
        )
    overlap = len(set(baseline) & set(signature))
    statistics = {
        "baseline_unique_pair_count": len(baseline),
        "signature_unique_pair_count": len(signature),
        "overlap_unique_pair_count": overlap,
        "signature_added_pair_count": len(signature) - overlap,
        "union_unique_pair_count": len(edges),
    }
    payload: dict[str, Any] = {
        "schema_version": "wang_viewpoint_final_candidate_graph_v1",
        "candidate_recall_artifact_sha256": candidate_recall.artifact_sha256,
        "signature_recall_artifact_sha256": signature_recall.artifact_sha256,
        "signature_index_sha256": signature_index.artifact_sha256,
        "edges": [edge.model_dump(mode="json") for edge in edges],
        "source_ineligible_claim_ids": signature_index.source_ineligible_claim_ids,
        "statistics": statistics,
        "recall_only": True,
        "identity_evidence": False,
        "apply_allowed": False,
    }
    return ViewpointFinalCandidateGraphArtifact(
        **payload, artifact_sha256=sha256_json(payload)
    )
