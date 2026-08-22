"""Fuse recall channels without turning retrieval signals into identity evidence."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .viewpoint_embedding_recall import ViewpointEmbeddingRecallArtifact
from .viewpoint_foundation import sha256_json
from .viewpoint_recall_blocking import ViewpointRecallBlockingArtifact


CANDIDATE_RECALL_VERSION = "wang_viewpoint_candidate_recall_v1"


class StrictCandidateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuleRecallSignal(StrictCandidateModel):
    rank: int = Field(ge=1)
    score: int = Field(ge=1)
    signals: list[str]
    shared_topic_terms: list[str]
    shared_scripture_chapters: list[str]


class EmbeddingRecallSignal(StrictCandidateModel):
    rank: int = Field(ge=1)
    cosine_similarity: float = Field(ge=-1, le=1)
    projection_sha256: str


class CandidateRecallNeighbor(StrictCandidateModel):
    claim_id: str
    channels: list[Literal["embedding", "rule"]]
    rule: RuleRecallSignal | None = None
    embedding: EmbeddingRecallSignal | None = None
    identity_evidence: Literal[False] = False

    @model_validator(mode="after")
    def validate_channels(self) -> "CandidateRecallNeighbor":
        expected = sorted(
            channel
            for channel, value in (("rule", self.rule), ("embedding", self.embedding))
            if value is not None
        )
        if self.channels != expected or not expected:
            raise ValueError("candidate channels must exactly describe their signals")
        return self


class CandidateRecallNeighborhood(StrictCandidateModel):
    focal_claim_id: str
    neighbors: list[CandidateRecallNeighbor]

    @model_validator(mode="after")
    def validate_neighbors(self) -> "CandidateRecallNeighborhood":
        ids = [item.claim_id for item in self.neighbors]
        if ids != sorted(set(ids)) or self.focal_claim_id in ids:
            raise ValueError("candidate neighbors must use canonical unique order")
        return self


class CandidateKnownPositiveRecall(StrictCandidateModel):
    eligible_pair_count: int = Field(ge=0)
    union_found_pair_count: int | None = Field(default=None, ge=0)
    union_recall: float | None = Field(default=None, ge=0, le=1)
    measurement_status: Literal[
        "no_scoped_positive_pairs", "gold_pair_ids_not_available"
    ]

    @model_validator(mode="after")
    def validate_measurement(self) -> "CandidateKnownPositiveRecall":
        if self.eligible_pair_count == 0:
            if (
                self.union_found_pair_count != 0
                or self.union_recall is not None
                or self.measurement_status != "no_scoped_positive_pairs"
            ):
                raise ValueError("empty gold scope must report recall=null")
        elif (
            self.union_found_pair_count is not None
            or self.union_recall is not None
            or self.measurement_status != "gold_pair_ids_not_available"
        ):
            raise ValueError("union recall requires explicit gold pair ids")
        return self


class ViewpointCandidateRecallArtifact(StrictCandidateModel):
    schema_version: Literal["wang_viewpoint_candidate_recall_v1"] = (
        CANDIDATE_RECALL_VERSION
    )
    claim_manifest_sha256: str
    rule_artifact_sha256: str
    embedding_artifact_sha256: str
    neighborhoods: list[CandidateRecallNeighborhood]
    uncovered_claim_ids: list[str]
    source_ineligible_claim_ids: list[str]
    known_positive_recall: CandidateKnownPositiveRecall
    statistics: dict[str, int]
    recall_only: Literal[True] = True
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "ViewpointCandidateRecallArtifact":
        focal_ids = [item.focal_claim_id for item in self.neighborhoods]
        if focal_ids != sorted(set(focal_ids)):
            raise ValueError("candidate recall focal Claims must use canonical order")
        expected_uncovered = sorted(
            item.focal_claim_id for item in self.neighborhoods if not item.neighbors
        )
        if self.uncovered_claim_ids != expected_uncovered:
            raise ValueError("candidate recall uncovered Claims mismatch")
        if self.source_ineligible_claim_ids != sorted(
            set(self.source_ineligible_claim_ids)
        ) or set(focal_ids) & set(self.source_ineligible_claim_ids):
            raise ValueError("invalid source-ineligible Claim disposition")
        directed = [
            (item.focal_claim_id, neighbor)
            for item in self.neighborhoods
            for neighbor in item.neighbors
        ]
        rule_directed = sum("rule" in neighbor.channels for _, neighbor in directed)
        embedding_directed = sum(
            "embedding" in neighbor.channels for _, neighbor in directed
        )
        overlap_directed = sum(len(neighbor.channels) == 2 for _, neighbor in directed)
        pairs_by_channel = {
            channel: {
                tuple(sorted((focal_id, neighbor.claim_id)))
                for focal_id, neighbor in directed
                if channel in neighbor.channels
            }
            for channel in ("rule", "embedding")
        }
        union_pairs = pairs_by_channel["rule"] | pairs_by_channel["embedding"]
        expected = {
            "input_claim_count": len(focal_ids) + len(self.source_ineligible_claim_ids),
            "eligible_claim_count": len(focal_ids),
            "source_ineligible_claim_count": len(self.source_ineligible_claim_ids),
            "covered_claim_count": len(focal_ids) - len(expected_uncovered),
            "uncovered_claim_count": len(expected_uncovered),
            "rule_directed_neighbor_count": rule_directed,
            "embedding_directed_neighbor_count": embedding_directed,
            "overlap_directed_neighbor_count": overlap_directed,
            "union_directed_neighbor_count": len(directed),
            "rule_unique_candidate_pair_count": len(pairs_by_channel["rule"]),
            "embedding_unique_candidate_pair_count": len(pairs_by_channel["embedding"]),
            "overlap_unique_candidate_pair_count": len(
                pairs_by_channel["rule"] & pairs_by_channel["embedding"]
            ),
            "union_unique_candidate_pair_count": len(union_pairs),
        }
        if self.statistics != expected:
            raise ValueError("candidate recall statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("candidate recall artifact SHA mismatch")
        return self


def build_viewpoint_candidate_recall(
    *,
    rule_recall: ViewpointRecallBlockingArtifact,
    embedding_recall: ViewpointEmbeddingRecallArtifact,
) -> ViewpointCandidateRecallArtifact:
    """Return the lossless rule/embedding union with channel provenance."""

    if rule_recall.claim_manifest_sha256 != embedding_recall.claim_manifest_sha256:
        raise ValueError("recall channels belong to different Claim manifests")
    if rule_recall.source_ineligible_claim_ids != embedding_recall.source_ineligible_claim_ids:
        raise ValueError("recall channels disagree on source-ineligible Claims")
    rule_by_id = {item.focal_claim_id: item for item in rule_recall.neighborhoods}
    embedding_by_id = {
        item.focal_claim_id: item for item in embedding_recall.neighborhoods
    }
    if sorted(rule_by_id) != sorted(embedding_by_id):
        raise ValueError("recall channels must cover the same eligible Claims")

    neighborhoods: list[CandidateRecallNeighborhood] = []
    for focal_id in sorted(rule_by_id):
        rule_neighbors = rule_by_id[focal_id].neighbors
        embedding_neighbors = embedding_by_id[focal_id].neighbors
        rule_rank = {
            item.claim_id: rank for rank, item in enumerate(
                sorted(rule_neighbors, key=lambda item: (-item.score, item.claim_id)), 1
            )
        }
        rule_index = {item.claim_id: item for item in rule_neighbors}
        embedding_index = {item.claim_id: item for item in embedding_neighbors}
        neighbors: list[CandidateRecallNeighbor] = []
        for claim_id in sorted(set(rule_index) | set(embedding_index)):
            rule = rule_index.get(claim_id)
            embedding = embedding_index.get(claim_id)
            neighbors.append(
                CandidateRecallNeighbor(
                    claim_id=claim_id,
                    channels=sorted(
                        channel
                        for channel, value in (("rule", rule), ("embedding", embedding))
                        if value is not None
                    ),
                    rule=(
                        RuleRecallSignal(
                            rank=rule_rank[claim_id],
                            score=rule.score,
                            signals=rule.signals,
                            shared_topic_terms=rule.shared_topic_terms,
                            shared_scripture_chapters=rule.shared_scripture_chapters,
                        )
                        if rule else None
                    ),
                    embedding=(
                        EmbeddingRecallSignal(
                            rank=embedding.rank,
                            cosine_similarity=embedding.cosine_similarity,
                            projection_sha256=embedding.projection_sha256,
                        )
                        if embedding else None
                    ),
                )
            )
        neighborhoods.append(
            CandidateRecallNeighborhood(focal_claim_id=focal_id, neighbors=neighbors)
        )

    source_ineligible = rule_recall.source_ineligible_claim_ids
    directed = [
        (item.focal_claim_id, neighbor)
        for item in neighborhoods for neighbor in item.neighbors
    ]
    pairs = {
        channel: {
            tuple(sorted((focal, neighbor.claim_id)))
            for focal, neighbor in directed if channel in neighbor.channels
        }
        for channel in ("rule", "embedding")
    }
    uncovered = sorted(item.focal_claim_id for item in neighborhoods if not item.neighbors)
    statistics = {
        "input_claim_count": len(neighborhoods) + len(source_ineligible),
        "eligible_claim_count": len(neighborhoods),
        "source_ineligible_claim_count": len(source_ineligible),
        "covered_claim_count": len(neighborhoods) - len(uncovered),
        "uncovered_claim_count": len(uncovered),
        "rule_directed_neighbor_count": sum("rule" in n.channels for _, n in directed),
        "embedding_directed_neighbor_count": sum("embedding" in n.channels for _, n in directed),
        "overlap_directed_neighbor_count": sum(len(n.channels) == 2 for _, n in directed),
        "union_directed_neighbor_count": len(directed),
        "rule_unique_candidate_pair_count": len(pairs["rule"]),
        "embedding_unique_candidate_pair_count": len(pairs["embedding"]),
        "overlap_unique_candidate_pair_count": len(pairs["rule"] & pairs["embedding"]),
        "union_unique_candidate_pair_count": len(pairs["rule"] | pairs["embedding"]),
    }
    payload: dict[str, Any] = {
        "schema_version": CANDIDATE_RECALL_VERSION,
        "claim_manifest_sha256": rule_recall.claim_manifest_sha256,
        "rule_artifact_sha256": rule_recall.artifact_sha256,
        "embedding_artifact_sha256": embedding_recall.artifact_sha256,
        "neighborhoods": [item.model_dump(mode="json") for item in neighborhoods],
        "uncovered_claim_ids": uncovered,
        "source_ineligible_claim_ids": source_ineligible,
        "known_positive_recall": {
            "eligible_pair_count": rule_recall.known_positive_recall.eligible_pair_count,
            "union_found_pair_count": (
                0 if rule_recall.known_positive_recall.eligible_pair_count == 0 else None
            ),
            "union_recall": None,
            "measurement_status": (
                "no_scoped_positive_pairs"
                if rule_recall.known_positive_recall.eligible_pair_count == 0
                else "gold_pair_ids_not_available"
            ),
        },
        "statistics": statistics,
        "recall_only": True,
    }
    return ViewpointCandidateRecallArtifact(
        **payload, artifact_sha256=sha256_json(payload)
    )
