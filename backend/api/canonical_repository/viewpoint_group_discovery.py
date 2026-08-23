"""Bounded group-discovery packets over the final candidate graph."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .viewpoint_claim_signature import (
    ClaimSemanticSignatureCandidate,
    ClaimSignatureIndexArtifact,
)
from .viewpoint_foundation import sha256_json
from .viewpoint_signature_recall import (
    FinalCandidateEdge,
    ViewpointFinalCandidateGraphArtifact,
)


GROUP_DISCOVERY_PLAN_VERSION = "wang_viewpoint_group_discovery_plan_v1"
MAX_GROUP_PACKET_CLAIMS = 48


class StrictGroupModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GroupDiscoveryEdge(StrictGroupModel):
    claim_ids: list[str] = Field(min_length=2, max_length=2)
    channels: list[Literal["embedding", "rule", "signature"]]
    signature_similarity_max: float = Field(ge=-1, le=1)
    signature_similarity_min: float = Field(ge=-1, le=1)
    signature_mutual: bool
    identity_evidence: Literal[False] = False

    @model_validator(mode="after")
    def validate_edge(self) -> "GroupDiscoveryEdge":
        if self.claim_ids != sorted(set(self.claim_ids)):
            raise ValueError("group-discovery edge Claim ids must be canonical")
        if "signature" not in self.channels:
            raise ValueError("group-discovery edge must come from signature recall")
        if self.signature_similarity_min > self.signature_similarity_max:
            raise ValueError("group-discovery similarity bounds are reversed")
        return self


class GroupDiscoveryPacket(StrictGroupModel):
    packet_id: str
    signature_index_sha256: str
    final_candidate_graph_sha256: str
    claims: list[ClaimSemanticSignatureCandidate] = Field(
        min_length=2, max_length=MAX_GROUP_PACKET_CLAIMS
    )
    candidate_edges: list[GroupDiscoveryEdge] = Field(min_length=1)
    context_edges: list[GroupDiscoveryEdge]
    packet_sha256: str

    @model_validator(mode="after")
    def validate_packet(self) -> "GroupDiscoveryPacket":
        claim_ids = [claim.claim_id for claim in self.claims]
        if claim_ids != sorted(set(claim_ids)):
            raise ValueError("group-discovery packet Claims must be canonical")
        edge_pairs = [edge.claim_ids for edge in self.candidate_edges]
        if edge_pairs != sorted(edge_pairs) or len({tuple(pair) for pair in edge_pairs}) != len(edge_pairs):
            raise ValueError("group-discovery packet edges must be canonical and unique")
        context_pairs = [edge.claim_ids for edge in self.context_edges]
        if context_pairs != sorted(context_pairs) or len(
            {tuple(pair) for pair in context_pairs}
        ) != len(context_pairs):
            raise ValueError("group-discovery context edges must be canonical and unique")
        if set(map(tuple, edge_pairs)) & set(map(tuple, context_pairs)):
            raise ValueError("review and context edges must not overlap")
        if any(
            not set(edge.claim_ids) <= set(claim_ids)
            for edge in self.candidate_edges + self.context_edges
        ):
            raise ValueError("group-discovery edge endpoint missing from packet")
        payload = self.model_dump(mode="json", exclude={"packet_sha256"})
        if self.packet_sha256 != sha256_json(payload):
            raise ValueError("group-discovery packet SHA mismatch")
        identity = dict(payload)
        identity.pop("packet_id")
        if self.packet_id != f"VGDP-{sha256_json(identity)[:20]}":
            raise ValueError("group-discovery packet id mismatch")
        return self


class GroupDiscoveryPlan(StrictGroupModel):
    schema_version: Literal["wang_viewpoint_group_discovery_plan_v1"] = (
        GROUP_DISCOVERY_PLAN_VERSION
    )
    signature_index_sha256: str
    final_candidate_graph_sha256: str
    model_id: str
    backend: Literal["codex_subscription"]
    reasoning_effort: Literal["low", "medium", "high", "xhigh"]
    max_output_tokens: int = Field(ge=1)
    prompt_sha256: str
    packets: list[GroupDiscoveryPacket]
    final_candidate_pair_count: int = Field(ge=0)
    signature_edge_count: int = Field(ge=0)
    baseline_only_disposition: Literal["retained_for_bounded_fallback"] = (
        "retained_for_bounded_fallback"
    )
    statistics: dict[str, int]
    model_calls_executed: Literal[0] = 0
    identity_evidence: Literal[False] = False
    apply_allowed: Literal[False] = False
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_plan(self) -> "GroupDiscoveryPlan":
        packet_ids = [packet.packet_id for packet in self.packets]
        if packet_ids != sorted(set(packet_ids)):
            raise ValueError("group-discovery packets must use canonical unique order")
        edge_pairs = [
            tuple(edge.claim_ids) for packet in self.packets for edge in packet.candidate_edges
        ]
        if len(edge_pairs) != len(set(edge_pairs)):
            raise ValueError("group-discovery plan must expose each signature edge exactly once")
        if len(edge_pairs) != self.signature_edge_count:
            raise ValueError("group-discovery signature edge denominator mismatch")
        baseline_only = self.final_candidate_pair_count - self.signature_edge_count
        if baseline_only < 0:
            raise ValueError("group-discovery pair denominators are invalid")
        claim_ids = {claim.claim_id for packet in self.packets for claim in packet.claims}
        sizes = [len(packet.model_dump_json().encode("utf-8")) for packet in self.packets]
        expected = {
            "packet_count": len(self.packets),
            "model_call_count": len(self.packets),
            "packet_claim_occurrence_count": sum(len(packet.claims) for packet in self.packets),
            "unique_claim_count": len(claim_ids),
            "signature_edge_exposure_count": len(edge_pairs),
            "baseline_only_retained_pair_count": baseline_only,
            "input_bytes": sum(sizes),
            "estimated_input_tokens": sum(max(1, math.ceil(size / 2)) for size in sizes),
        }
        if self.statistics != expected:
            raise ValueError("group-discovery plan statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("group-discovery plan SHA mismatch")
        return self


class GroupProposalParticipant(StrictGroupModel):
    claim_id: str
    role: Literal[
        "candidate_member", "component", "tension_side_a", "tension_side_b",
        "contrast_only",
    ]


class ScreeningGroupProposal(StrictGroupModel):
    local_group_id: str = Field(pattern=r"^G[0-9]{3}$")
    relation_kind: Literal["possible_equivalent", "component", "tension"]
    participants: list[GroupProposalParticipant] = Field(min_length=2)
    proposed_core_proposition: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    material_differences: list[str]
    evidence_required_claim_ids: list[str]
    requires_recall_extension: bool
    screening_only: Literal[True] = True
    identity_evidence: Literal[False] = False

    @model_validator(mode="before")
    @classmethod
    def canonicalize_lists(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        result = dict(value)
        if isinstance(result.get("participants"), list):
            result["participants"] = sorted(
                result["participants"], key=lambda item: str(item.get("claim_id", ""))
            )
        for name in ("material_differences", "evidence_required_claim_ids"):
            if isinstance(result.get(name), list):
                result[name] = sorted(set(str(item) for item in result[name]))
        return result

    @model_validator(mode="after")
    def validate_proposal(self) -> "ScreeningGroupProposal":
        ids = [participant.claim_id for participant in self.participants]
        if ids != sorted(set(ids)):
            raise ValueError("group proposal participants must be canonical and unique")
        if self.evidence_required_claim_ids != sorted(set(self.evidence_required_claim_ids)):
            raise ValueError("group proposal evidence requirements must be canonical")
        if not set(self.evidence_required_claim_ids) <= set(ids):
            raise ValueError("group proposal evidence requirement is outside participants")
        roles = {participant.role for participant in self.participants}
        if self.relation_kind == "possible_equivalent" and roles != {"candidate_member"}:
            raise ValueError("possible-equivalent proposal accepts only candidate members")
        if self.relation_kind == "component" and not roles <= {
            "candidate_member", "component", "contrast_only"
        }:
            raise ValueError("component proposal uses incompatible participant roles")
        if self.relation_kind == "tension" and not {
            "tension_side_a", "tension_side_b"
        } <= roles:
            raise ValueError("tension proposal requires both sides")
        return self


class GroupDiscoveryResponse(StrictGroupModel):
    schema_version: Literal["wang_viewpoint_group_discovery_response_v1"] = (
        "wang_viewpoint_group_discovery_response_v1"
    )
    packet_sha256: str
    proposals: list[ScreeningGroupProposal]
    unresolved_notes: list[str]

    @model_validator(mode="before")
    @classmethod
    def canonicalize_response(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        result = dict(value)
        if isinstance(result.get("proposals"), list):
            result["proposals"] = sorted(
                result["proposals"], key=lambda item: str(item.get("local_group_id", ""))
            )
        if isinstance(result.get("unresolved_notes"), list):
            result["unresolved_notes"] = sorted(
                set(str(item) for item in result["unresolved_notes"])
            )
        return result

    @model_validator(mode="after")
    def validate_response(self) -> "GroupDiscoveryResponse":
        ids = [proposal.local_group_id for proposal in self.proposals]
        if ids != sorted(set(ids)):
            raise ValueError("group proposal ids must be canonical and unique")
        return self


def validate_group_discovery_response(
    packet: GroupDiscoveryPacket,
    response: dict[str, Any] | GroupDiscoveryResponse,
) -> GroupDiscoveryResponse:
    signatures = {claim.claim_id: claim for claim in packet.claims}
    edges = {
        tuple(edge.claim_ids)
        for edge in packet.candidate_edges + packet.context_edges
    }

    def requires_extension(participant_ids: set[str]) -> bool:
        connected = {min(participant_ids)}
        while True:
            expanded = connected | {
                claim_id
                for edge in edges
                if set(edge) <= participant_ids and set(edge) & connected
                for claim_id in edge
            }
            if expanded == connected:
                return connected != participant_ids
            connected = expanded

    if isinstance(response, GroupDiscoveryResponse):
        result = response
    else:
        payload = dict(response)
        proposals = [dict(item) for item in payload.get("proposals") or []]
        for proposal in proposals:
            participant_ids = {
                str(item.get("claim_id"))
                for item in proposal.get("participants") or []
                if item.get("claim_id")
            }
            if participant_ids:
                proposal["requires_recall_extension"] = requires_extension(
                    participant_ids
                )
        payload["proposals"] = proposals
        result = GroupDiscoveryResponse.model_validate(payload)
    if result.packet_sha256 != packet.packet_sha256:
        raise ValueError("group-discovery response belongs to another packet")
    for proposal in result.proposals:
        participant_ids = {participant.claim_id for participant in proposal.participants}
        if not participant_ids <= set(signatures):
            raise ValueError("group proposal participant is outside packet")
        if proposal.requires_recall_extension != requires_extension(participant_ids):
            raise ValueError("group proposal recall-extension flag is incorrect")
        roles = {participant.claim_id: participant.role for participant in proposal.participants}
        for claim_id, role in roles.items():
            if role != "candidate_member":
                continue
            signature = signatures[claim_id]
            if all(
                atom.stance == "reported_external"
                for atom in signature.semantic_atoms
            ):
                raise ValueError("reported external Claim cannot be a candidate member")
    return result


def _compact_edge(edge: FinalCandidateEdge) -> GroupDiscoveryEdge:
    scores = [
        direction.neighbor.cosine_similarity for direction in edge.signature_directions
    ]
    return GroupDiscoveryEdge(
        claim_ids=edge.claim_ids,
        channels=edge.channels,
        signature_similarity_max=max(scores),
        signature_similarity_min=min(scores),
        signature_mutual=len(edge.signature_directions) == 2,
    )


def build_group_discovery_plan(
    *,
    signature_index: ClaimSignatureIndexArtifact,
    final_graph: ViewpointFinalCandidateGraphArtifact,
    model_id: str,
    reasoning_effort: Literal["low", "medium", "high", "xhigh"],
    max_output_tokens: int,
    prompt_sha256: str,
    max_claims_per_packet: int = MAX_GROUP_PACKET_CLAIMS,
) -> GroupDiscoveryPlan:
    if not 2 <= max_claims_per_packet <= MAX_GROUP_PACKET_CLAIMS:
        raise ValueError("group-discovery packet Claim bound must be between 2 and 48")
    if final_graph.signature_index_sha256 != signature_index.artifact_sha256:
        raise ValueError("group-discovery inputs use different signature indexes")
    signature_by_id = {signature.claim_id: signature for signature in signature_index.signatures}
    signature_edges = {
        tuple(edge.claim_ids): edge for edge in final_graph.edges if edge.signature_directions
    }
    adjacency = {claim_id: set() for claim_id in signature_by_id}
    for left, right in signature_edges:
        adjacency[left].add(right)
        adjacency[right].add(left)
    uncovered = set(signature_edges)
    packet_rows: list[tuple[list[str], list[tuple[str, str]]]] = []
    while uncovered:
        nodes: set[str] = set()
        while len(nodes) < max_claims_per_packet:
            endpoint_degree: Counter[str] = Counter(
                endpoint
                for pair in uncovered
                for endpoint in pair
                if endpoint not in nodes
            )
            adjacent_candidates = (
                set().union(*(adjacency[node] for node in nodes)) - nodes
                if nodes else set()
            )
            scored = []
            for candidate in adjacent_candidates:
                internal = sum(
                    tuple(sorted((candidate, node))) in uncovered for node in nodes
                )
                if not internal:
                    continue
                future = sum(
                    tuple(sorted((candidate, node))) in uncovered
                    for node in adjacency[candidate] - nodes
                )
                scored.append((-internal, -future, candidate))
            if scored:
                choice = min(scored)[2]
            elif endpoint_degree:
                choice = min(
                    (-degree, claim_id) for claim_id, degree in endpoint_degree.items()
                )[1]
            else:
                break
            nodes.add(choice)
        covered = sorted(
            pair for pair in uncovered if set(pair) <= nodes
        )
        if not covered:
            pair = min(uncovered)
            nodes.update(pair)
            covered = [pair]
        uncovered.difference_update(covered)
        packet_rows.append((sorted(nodes), covered))

    packets = []
    for claim_ids, edge_pairs in packet_rows:
        unsigned: dict[str, Any] = {
            "packet_id": "pending",
            "signature_index_sha256": signature_index.artifact_sha256,
            "final_candidate_graph_sha256": final_graph.artifact_sha256,
            "claims": [signature_by_id[claim_id].model_dump(mode="json") for claim_id in claim_ids],
            "candidate_edges": [
                _compact_edge(signature_edges[pair]).model_dump(mode="json")
                for pair in edge_pairs
            ],
            "context_edges": [
                _compact_edge(edge).model_dump(mode="json")
                for pair, edge in signature_edges.items()
                if set(pair) <= set(claim_ids) and pair not in set(edge_pairs)
            ],
        }
        identity = dict(unsigned)
        identity.pop("packet_id")
        unsigned["packet_id"] = f"VGDP-{sha256_json(identity)[:20]}"
        packet = GroupDiscoveryPacket(**unsigned, packet_sha256=sha256_json(unsigned))
        packets.append(packet)
    packets.sort(key=lambda packet: packet.packet_id)
    sizes = [len(packet.model_dump_json().encode("utf-8")) for packet in packets]
    exposed_claim_ids = {claim.claim_id for packet in packets for claim in packet.claims}
    baseline_only = sum(not edge.signature_directions for edge in final_graph.edges)
    statistics = {
        "packet_count": len(packets),
        "model_call_count": len(packets),
        "packet_claim_occurrence_count": sum(len(packet.claims) for packet in packets),
        "unique_claim_count": len(exposed_claim_ids),
        "signature_edge_exposure_count": len(signature_edges),
        "baseline_only_retained_pair_count": baseline_only,
        "input_bytes": sum(sizes),
        "estimated_input_tokens": sum(max(1, math.ceil(size / 2)) for size in sizes),
    }
    payload: dict[str, Any] = {
        "schema_version": GROUP_DISCOVERY_PLAN_VERSION,
        "signature_index_sha256": signature_index.artifact_sha256,
        "final_candidate_graph_sha256": final_graph.artifact_sha256,
        "model_id": model_id,
        "backend": "codex_subscription",
        "reasoning_effort": reasoning_effort,
        "max_output_tokens": max_output_tokens,
        "prompt_sha256": prompt_sha256,
        "packets": [packet.model_dump(mode="json") for packet in packets],
        "final_candidate_pair_count": len(final_graph.edges),
        "signature_edge_count": len(signature_edges),
        "baseline_only_disposition": "retained_for_bounded_fallback",
        "statistics": statistics,
        "model_calls_executed": 0,
        "identity_evidence": False,
        "apply_allowed": False,
    }
    return GroupDiscoveryPlan(**payload, artifact_sha256=sha256_json(payload))
