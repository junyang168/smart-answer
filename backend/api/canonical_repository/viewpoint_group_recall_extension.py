"""Compile model-discovered group bridges into a traceable recall overlay."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..semantic_index.embeddings import EmbeddingIndexArtifact
from .viewpoint_foundation import sha256_json
from .viewpoint_group_discovery import (
    GroupDiscoveryPlan,
    GroupDiscoveryResponse,
    ScreeningGroupProposal,
    validate_group_discovery_response,
)
from .viewpoint_signature_recall import ViewpointFinalCandidateGraphArtifact


class StrictExtensionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GroupDiscoveryProvenance(StrictExtensionModel):
    call_artifact_sha256: str
    packet_id: str
    local_group_id: str
    proposal_sha256: str
    relation_kind: Literal["possible_equivalent", "component", "tension"]


class GroupRecallExtensionEdge(StrictExtensionModel):
    claim_ids: list[str] = Field(min_length=2, max_length=2)
    signature_cosine_similarity: float = Field(ge=-1, le=1)
    provenances: list[GroupDiscoveryProvenance]
    channel: Literal["group_model_discovery"] = "group_model_discovery"
    identity_evidence: Literal[False] = False

    @model_validator(mode="after")
    def validate_edge(self) -> "GroupRecallExtensionEdge":
        if self.claim_ids != sorted(set(self.claim_ids)):
            raise ValueError("group recall extension pair must be canonical")
        keys = [
            (item.packet_id, item.local_group_id, item.call_artifact_sha256)
            for item in self.provenances
        ]
        if keys != sorted(set(keys)):
            raise ValueError("group recall extension provenance must be canonical")
        return self


class GroupRecallExtensionArtifact(StrictExtensionModel):
    schema_version: Literal["wang_viewpoint_group_recall_extension_v1"] = (
        "wang_viewpoint_group_recall_extension_v1"
    )
    base_final_candidate_graph_sha256: str
    group_discovery_plan_sha256: str
    signature_embedding_index_sha256: str
    call_artifact_sha256s: list[str]
    base_unique_pair_count: int = Field(ge=0)
    packet_extension_flag_count: int = Field(ge=0)
    edges: list[GroupRecallExtensionEdge]
    statistics: dict[str, int]
    recall_only: Literal[True] = True
    identity_evidence: Literal[False] = False
    apply_allowed: Literal[False] = False
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "GroupRecallExtensionArtifact":
        if self.call_artifact_sha256s != sorted(set(self.call_artifact_sha256s)):
            raise ValueError("group extension call SHAs must be canonical")
        pairs = [edge.claim_ids for edge in self.edges]
        if pairs != sorted(pairs) or len({tuple(pair) for pair in pairs}) != len(pairs):
            raise ValueError("group extension edges must be canonical and unique")
        extension_proposals = {
            (item.packet_id, item.local_group_id, item.call_artifact_sha256)
            for edge in self.edges for item in edge.provenances
        }
        expected = {
            "completed_call_count": len(self.call_artifact_sha256s),
            "packet_extension_flag_count": self.packet_extension_flag_count,
            "recall_extension_proposal_count": len(extension_proposals),
            "base_unique_pair_count": self.base_unique_pair_count,
            "added_unique_pair_count": len(self.edges),
            "overlay_union_unique_pair_count": self.base_unique_pair_count + len(self.edges),
        }
        if self.statistics != expected:
            raise ValueError("group recall extension statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("group recall extension artifact SHA mismatch")
        return self


def _components(participants: set[str], edges: set[tuple[str, str]]) -> list[set[str]]:
    remaining = set(participants)
    result = []
    while remaining:
        component = {min(remaining)}
        while True:
            expanded = component | {
                claim_id
                for edge in edges
                if set(edge) <= participants and set(edge) & component
                for claim_id in edge
            }
            if expanded == component:
                break
            component = expanded
        remaining -= component
        result.append(component)
    return sorted(result, key=lambda item: min(item))


def _proposal_bridges(
    *, proposal: ScreeningGroupProposal,
    base_edges: set[tuple[str, str]],
    normalized_vectors: Mapping[str, np.ndarray[Any, Any]],
) -> list[tuple[tuple[str, str], float]]:
    participants = {item.claim_id for item in proposal.participants}
    components = _components(participants, base_edges)
    bridges = []
    while len(components) > 1:
        candidates = []
        for left_index, left in enumerate(components):
            for right_index in range(left_index + 1, len(components)):
                right = components[right_index]
                for left_id in left:
                    for right_id in right:
                        pair = tuple(sorted((left_id, right_id)))
                        score = float(
                            normalized_vectors[left_id] @ normalized_vectors[right_id]
                        )
                        candidates.append((-score, pair, left_index, right_index))
        negative_score, pair, left_index, right_index = min(candidates)
        bridges.append((pair, -negative_score))
        merged = components[left_index] | components[right_index]
        components = [
            component for index, component in enumerate(components)
            if index not in {left_index, right_index}
        ] + [merged]
        components.sort(key=lambda item: min(item))
    return bridges


def build_group_recall_extension(
    *,
    plan: GroupDiscoveryPlan,
    final_graph: ViewpointFinalCandidateGraphArtifact,
    signature_embedding_index: EmbeddingIndexArtifact,
    responses_by_packet_id: Mapping[str, GroupDiscoveryResponse],
    call_artifact_sha_by_packet_id: Mapping[str, str],
) -> GroupRecallExtensionArtifact:
    expected_packets = {packet.packet_id for packet in plan.packets}
    if set(responses_by_packet_id) != expected_packets:
        raise ValueError("group extension requires complete packet responses")
    if set(call_artifact_sha_by_packet_id) != expected_packets:
        raise ValueError("group extension requires complete call provenance")
    if plan.final_candidate_graph_sha256 != final_graph.artifact_sha256:
        raise ValueError("group extension plan belongs to another candidate graph")
    if signature_embedding_index.object_kind != "claim_signature":
        raise ValueError("group extension requires Claim signature embeddings")
    vectors = {
        record.object_id: np.asarray(record.vector, dtype=np.float32)
        for record in signature_embedding_index.records
    }
    if set(vectors) != {
        claim.claim_id for packet in plan.packets for claim in packet.claims
    }:
        raise ValueError("group extension embedding coverage differs from plan")
    normalized = {}
    for claim_id, vector in vectors.items():
        norm = float(np.linalg.norm(vector))
        if not norm or not np.isfinite(norm):
            raise ValueError("group extension requires finite non-zero vectors")
        normalized[claim_id] = vector / norm
    base_edges = {tuple(edge.claim_ids) for edge in final_graph.edges}
    edge_rows: dict[tuple[str, str], dict[str, Any]] = {}
    packet_extension_flag_count = 0
    extension_proposal_count = 0
    for packet in plan.packets:
        response = validate_group_discovery_response(
            packet, responses_by_packet_id[packet.packet_id]
        )
        call_sha = call_artifact_sha_by_packet_id[packet.packet_id]
        for proposal in response.proposals:
            if not proposal.requires_recall_extension:
                continue
            packet_extension_flag_count += 1
            provenance = GroupDiscoveryProvenance(
                call_artifact_sha256=call_sha,
                packet_id=packet.packet_id,
                local_group_id=proposal.local_group_id,
                proposal_sha256=sha256_json(proposal.model_dump(mode="json")),
                relation_kind=proposal.relation_kind,
            )
            bridges = _proposal_bridges(
                proposal=proposal,
                base_edges=base_edges,
                normalized_vectors=normalized,
            )
            if bridges:
                extension_proposal_count += 1
            for pair, score in bridges:
                row = edge_rows.setdefault(pair, {"scores": [], "provenances": []})
                row["scores"].append(score)
                row["provenances"].append(provenance)
    edges = [
        GroupRecallExtensionEdge(
            claim_ids=list(pair),
            signature_cosine_similarity=max(row["scores"]),
            provenances=sorted(
                row["provenances"],
                key=lambda item: (
                    item.packet_id, item.local_group_id, item.call_artifact_sha256
                ),
            ),
        )
        for pair, row in sorted(edge_rows.items())
    ]
    provenance_count = len({
        (item.packet_id, item.local_group_id, item.call_artifact_sha256)
        for edge in edges for item in edge.provenances
    })
    if provenance_count != extension_proposal_count:
        raise ValueError("a recall-extension proposal produced no traceable bridge")
    statistics = {
        "completed_call_count": len(call_artifact_sha_by_packet_id),
        "packet_extension_flag_count": packet_extension_flag_count,
        "recall_extension_proposal_count": extension_proposal_count,
        "base_unique_pair_count": len(base_edges),
        "added_unique_pair_count": len(edges),
        "overlay_union_unique_pair_count": len(base_edges) + len(edges),
    }
    payload: dict[str, Any] = {
        "schema_version": "wang_viewpoint_group_recall_extension_v1",
        "base_final_candidate_graph_sha256": final_graph.artifact_sha256,
        "group_discovery_plan_sha256": plan.artifact_sha256,
        "signature_embedding_index_sha256": signature_embedding_index.artifact_sha256,
        "call_artifact_sha256s": sorted(call_artifact_sha_by_packet_id.values()),
        "base_unique_pair_count": len(base_edges),
        "packet_extension_flag_count": packet_extension_flag_count,
        "edges": [edge.model_dump(mode="json") for edge in edges],
        "statistics": statistics,
        "recall_only": True,
        "identity_evidence": False,
        "apply_allowed": False,
    }
    return GroupRecallExtensionArtifact(
        **payload, artifact_sha256=sha256_json(payload)
    )
