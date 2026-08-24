"""No-apply contracts for blind, bottom-up semantic graph discovery.

This module deliberately knows nothing about CanonicalViewpoint, ArgumentRoute,
Topic, or a target research question.  It is a calibration boundary: models see
only pinned Claims and their source evidence, then discover proposition identity
and argument structure from that material.
"""

from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from itertools import combinations, product
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .viewpoint_foundation import sha256_json


PACKET_VERSION = "wang_blind_semantic_graph_packet_v1"
DISCOVERY_VERSION = "wang_blind_semantic_graph_discovery_v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BlindEvidence(StrictModel):
    evidence_step_id: str
    source_fragment_id: str
    source_id: str
    paragraph_key: str
    media_time: float | None
    evidence_statement: str
    discourse_role: str
    scripture_refs: list[str]
    verbatim_excerpt: str


class BlindClaim(StrictModel):
    claim_id: str
    pinned_claim_revision: int = Field(ge=1)
    claim_revision_sha256: str
    source_id: str
    statement: str = Field(min_length=1)
    attribution: str
    scripture_refs: list[str]
    evidence: list[BlindEvidence] = Field(min_length=1)


class BlindSemanticGraphPacket(StrictModel):
    schema_version: Literal["wang_blind_semantic_graph_packet_v1"] = PACKET_VERSION
    claims: list[BlindClaim] = Field(min_length=1)
    packet_sha256: str

    @model_validator(mode="after")
    def validate_packet(self) -> "BlindSemanticGraphPacket":
        claim_ids = [row.claim_id for row in self.claims]
        if claim_ids != sorted(set(claim_ids)):
            raise ValueError("blind packet Claims must be canonical and unique")
        for claim in self.claims:
            if any(row.source_id != claim.source_id for row in claim.evidence):
                raise ValueError(f"{claim.claim_id}: cross-source evidence is forbidden")
        payload = self.model_dump(mode="json", exclude={"packet_sha256"})
        if self.packet_sha256 != sha256_json(payload):
            raise ValueError("blind packet SHA mismatch")
        return self


def build_blind_packet(batch_packet: Mapping[str, Any]) -> BlindSemanticGraphPacket:
    """Project only source Claims/evidence out of a legacy CVP batch packet."""

    claims = []
    for raw in sorted(batch_packet.get("claims") or [], key=lambda row: row["claim_id"]):
        claims.append(
            {
                key: raw[key]
                for key in (
                    "claim_id",
                    "pinned_claim_revision",
                    "claim_revision_sha256",
                    "source_id",
                    "statement",
                    "attribution",
                    "scripture_refs",
                )
            }
            | {
                "evidence": [
                    {
                        key: evidence.get(key)
                        for key in (
                            "evidence_step_id",
                            "source_fragment_id",
                            "source_id",
                            "paragraph_key",
                            "media_time",
                            "evidence_statement",
                            "discourse_role",
                            "scripture_refs",
                            "verbatim_excerpt",
                        )
                    }
                    for evidence in raw.get("evidence") or []
                ]
            }
        )
    payload = {"schema_version": PACKET_VERSION, "claims": claims}
    return BlindSemanticGraphPacket.model_validate(
        payload | {"packet_sha256": sha256_json(payload)}
    )


class ClaimComponent(StrictModel):
    component_id: str = Field(pattern=r"^C[0-9]{2}$")
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    exact_text: str = Field(min_length=1)
    normalized_proposition: str = Field(min_length=1)
    discourse_function: Literal[
        "observation",
        "premise",
        "bridge",
        "conclusion",
        "qualification",
        "objection",
        "application",
        "context",
    ]


class ClaimDecomposition(StrictModel):
    claim_id: str
    components: list[ClaimComponent] = Field(min_length=1)


class PropositionNode(StrictModel):
    node_id: str = Field(pattern=r"^N[0-9]{3}$")
    canonical_proposition: str = Field(min_length=1)
    component_keys: list[str] = Field(min_length=1)
    semantic_kind: Literal[
        "textual_observation",
        "interpretive_assertion",
        "theological_assertion",
        "methodological_boundary",
        "application",
        "context",
    ]


class SemanticEdge(StrictModel):
    from_node_id: str
    to_node_id: str
    relation: Literal[
        "supports",
        "qualifies",
        "tensions_with",
        "applies",
        "contextualizes",
    ]
    rationale: str = Field(min_length=1)


class ArgumentComplex(StrictModel):
    complex_id: str = Field(pattern=r"^A[0-9]{2}$")
    focal_node_ids: list[str] = Field(min_length=1)
    member_node_ids: list[str] = Field(min_length=1)
    structure_summary: str = Field(min_length=1)


class SynthesisStatement(StrictModel):
    statement: str = Field(min_length=1)
    basis_node_ids: list[str] = Field(min_length=1)


class UnresolvedItem(StrictModel):
    node_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)


class BlindSemanticGraphDiscovery(StrictModel):
    schema_version: Literal["wang_blind_semantic_graph_discovery_v1"]
    input_packet_sha256: str
    claim_decompositions: list[ClaimDecomposition] = Field(min_length=1)
    proposition_nodes: list[PropositionNode] = Field(min_length=1)
    semantic_edges: list[SemanticEdge]
    argument_complexes: list[ArgumentComplex] = Field(min_length=1)
    central_synthesis: list[SynthesisStatement] = Field(min_length=1)
    unresolved_items: list[UnresolvedItem]


def validate_discovery(
    packet: BlindSemanticGraphPacket,
    raw: Mapping[str, Any],
) -> BlindSemanticGraphDiscovery:
    """Validate evidence coverage, graph references, and argument connectivity."""

    discovery = BlindSemanticGraphDiscovery.model_validate(raw)
    if discovery.input_packet_sha256 != packet.packet_sha256:
        raise ValueError("discovery is bound to the wrong blind packet")

    claims = {row.claim_id: row for row in packet.claims}
    decomposition_ids = [row.claim_id for row in discovery.claim_decompositions]
    if decomposition_ids != sorted(claims):
        raise ValueError("every input Claim must be decomposed exactly once in canonical order")

    component_keys: set[str] = set()
    for decomposition in discovery.claim_decompositions:
        claim = claims[decomposition.claim_id]
        expected_ids = [f"C{index:02d}" for index in range(1, len(decomposition.components) + 1)]
        if [row.component_id for row in decomposition.components] != expected_ids:
            raise ValueError(f"{claim.claim_id}: component ids/order are not canonical")
        previous_start = -1
        for component in decomposition.components:
            if component.start_char < previous_start:
                raise ValueError(f"{claim.claim_id}: component spans are not ordered")
            if component.end_char > len(claim.statement):
                raise ValueError(f"{claim.claim_id}: component span exceeds Claim statement")
            if claim.statement[component.start_char : component.end_char] != component.exact_text:
                raise ValueError(f"{claim.claim_id}: component exact_text does not match span")
            previous_start = component.start_char
            component_keys.add(f"{claim.claim_id}#{component.component_id}")

    node_ids = [row.node_id for row in discovery.proposition_nodes]
    if node_ids != [f"N{index:03d}" for index in range(1, len(node_ids) + 1)]:
        raise ValueError("proposition node ids/order are not canonical")
    used_component_keys = [key for node in discovery.proposition_nodes for key in node.component_keys]
    if len(used_component_keys) != len(set(used_component_keys)):
        raise ValueError("a Claim component belongs to more than one proposition node")
    if set(used_component_keys) != component_keys:
        missing = sorted(component_keys - set(used_component_keys))
        extra = sorted(set(used_component_keys) - component_keys)
        raise ValueError(f"proposition-node component coverage mismatch: missing={missing}, extra={extra}")

    node_set = set(node_ids)
    seen_edges: set[tuple[str, str, str]] = set()
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in discovery.semantic_edges:
        if edge.from_node_id not in node_set or edge.to_node_id not in node_set:
            raise ValueError("semantic edge has an unknown endpoint")
        if edge.from_node_id == edge.to_node_id:
            raise ValueError("semantic self-edges are forbidden")
        key = (edge.from_node_id, edge.to_node_id, edge.relation)
        if key in seen_edges:
            raise ValueError("duplicate semantic edge")
        seen_edges.add(key)
        adjacency[edge.from_node_id].add(edge.to_node_id)
        adjacency[edge.to_node_id].add(edge.from_node_id)

    complex_ids = [row.complex_id for row in discovery.argument_complexes]
    if complex_ids != [f"A{index:02d}" for index in range(1, len(complex_ids) + 1)]:
        raise ValueError("argument complex ids/order are not canonical")
    focal_nodes: set[str] = set()
    for complex_ in discovery.argument_complexes:
        members = set(complex_.member_node_ids)
        focals = set(complex_.focal_node_ids)
        if len(members) != len(complex_.member_node_ids) or len(focals) != len(complex_.focal_node_ids):
            raise ValueError(f"{complex_.complex_id}: duplicate node reference")
        if not members <= node_set or not focals <= members:
            raise ValueError(f"{complex_.complex_id}: invalid member or focal node")
        focal_nodes |= focals
        reached = set()
        queue = deque([next(iter(members))])
        while queue:
            current = queue.popleft()
            if current in reached:
                continue
            reached.add(current)
            queue.extend(adjacency[current] & members - reached)
        if reached != members and len(members) > 1:
            raise ValueError(f"{complex_.complex_id}: member nodes are not graph-connected")

    for synthesis in discovery.central_synthesis:
        if not set(synthesis.basis_node_ids) <= focal_nodes:
            raise ValueError("central synthesis must cite discovered focal nodes only")
    for unresolved in discovery.unresolved_items:
        if not set(unresolved.node_ids) <= node_set:
            raise ValueError("unresolved item cites an unknown node")
    return discovery


def canonicalize_component_key_delimiters(raw: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    """Repair only the unambiguous ``Claim::Cnn`` spelling to ``Claim#Cnn``.

    The raw response remains immutable.  This narrow repair exists because v1's
    prose prompt initially failed to state a delimiter even though the validator
    did.  No proposition text, grouping, edge, or conclusion may change here.
    """

    normalized = deepcopy(dict(raw))
    count = 0
    for node in normalized.get("proposition_nodes") or []:
        keys = []
        for key in node.get("component_keys") or []:
            if isinstance(key, str) and "::C" in key and "#" not in key:
                prefix, suffix = key.rsplit("::", 1)
                if len(suffix) == 3 and suffix.startswith("C") and suffix[1:].isdigit():
                    key = f"{prefix}#{suffix}"
                    count += 1
            keys.append(key)
        node["component_keys"] = keys
    return normalized, count


def discovery_metrics(discovery: BlindSemanticGraphDiscovery) -> dict[str, Any]:
    claim_by_component = {
        f"{row.claim_id}#{component.component_id}": row.claim_id
        for row in discovery.claim_decompositions
        for component in row.components
    }
    node_claims = {
        node.node_id: sorted({claim_by_component[key] for key in node.component_keys})
        for node in discovery.proposition_nodes
    }
    focal_node_ids = {
        node_id for complex_ in discovery.argument_complexes for node_id in complex_.focal_node_ids
    }
    return {
        "claim_count": len(discovery.claim_decompositions),
        "component_count": sum(len(row.components) for row in discovery.claim_decompositions),
        "proposition_node_count": len(discovery.proposition_nodes),
        "semantic_edge_count": len(discovery.semantic_edges),
        "argument_complex_count": len(discovery.argument_complexes),
        "focal_node_count": len(focal_node_ids),
        "central_synthesis_count": len(discovery.central_synthesis),
        "unresolved_item_count": len(discovery.unresolved_items),
        "focal_claim_ids": sorted(
            {claim_id for node_id in focal_node_ids for claim_id in node_claims[node_id]}
        ),
    }


def discovery_structure_sets(discovery: BlindSemanticGraphDiscovery) -> dict[str, set[str]]:
    """Project model-local nodes into comparable Claim-level structural sets."""

    claim_by_component = {
        f"{row.claim_id}#{component.component_id}": row.claim_id
        for row in discovery.claim_decompositions
        for component in row.components
    }
    node_claims = {
        node.node_id: sorted({claim_by_component[key] for key in node.component_keys})
        for node in discovery.proposition_nodes
    }
    structures: dict[str, set[str]] = {
        "equivalent_claim_pairs": set(),
        "argument_complex_claim_pairs": set(),
        "focal_claim_ids": set(),
        "central_basis_claim_ids": set(),
        **{
            f"relation_{relation}": set()
            for relation in (
                "supports",
                "qualifies",
                "tensions_with",
                "applies",
                "contextualizes",
            )
        },
    }
    for claims in node_claims.values():
        structures["equivalent_claim_pairs"].update(
            "|".join(pair) for pair in combinations(claims, 2)
        )
    for edge in discovery.semantic_edges:
        for left, right in product(node_claims[edge.from_node_id], node_claims[edge.to_node_id]):
            if edge.relation == "tensions_with":
                left, right = sorted((left, right))
            structures[f"relation_{edge.relation}"].add(f"{left}|{right}")
    for complex_ in discovery.argument_complexes:
        claim_ids = sorted(
            {
                claim_id
                for node_id in complex_.member_node_ids
                for claim_id in node_claims[node_id]
            }
        )
        structures["argument_complex_claim_pairs"].update(
            "|".join(pair) for pair in combinations(claim_ids, 2)
        )
        structures["focal_claim_ids"].update(
            claim_id
            for node_id in complex_.focal_node_ids
            for claim_id in node_claims[node_id]
        )
    structures["central_basis_claim_ids"].update(
        claim_id
        for synthesis in discovery.central_synthesis
        for node_id in synthesis.basis_node_ids
        for claim_id in node_claims[node_id]
    )
    return structures
