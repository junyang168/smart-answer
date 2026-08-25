"""Review structures and relations that were committed without one.

The batch contract reviews what a batch proposes.  Sixteen structures and
relations reached the Registry before the review schema had a place for them,
and they belong to no pending batch -- there is no proposal left to review.

Re-running their original batches is not the way back: a rerun re-derives the
whole batch against today's prompts, and any drift is written as a second set
of records beside the ones already committed.  So the committed records are the
input here, read from the store and put to the same two questions the batch
review now has to answer.

This module decides nothing and writes nothing.  It compiles the packet, checks
the returned decisions cover exactly what was sent, and hands back the
provenance to attach.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import Field, model_validator

from .viewpoint_batch_resolution import BatchResolutionError, StrictBatchModel
from .viewpoint_foundation import sha256_json

BACKREVIEW_PACKET_VERSION = "wang_viewpoint_graph_backreview_packet_v1"
BACKREVIEW_VERSION = "wang_viewpoint_graph_backreview_v1"


class BackReviewedStructure(StrictBatchModel):
    """One ruling on a committed structure."""

    structure_revision_id: str = Field(min_length=1)
    decision: Literal["pass", "correct", "reject", "defer"]
    finding_codes: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    synthesis_entailed_by_focal: bool
    unresolved_material_omitted: list[str] = Field(default_factory=list)


class BackReviewedRelation(StrictBatchModel):
    """One ruling on a committed relation."""

    viewpoint_relation_id: str = Field(min_length=1)
    decision: Literal["pass", "correct", "reject", "defer"]
    finding_codes: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    direction_correct: bool


class ViewpointGraphBackReviewResponse(StrictBatchModel):
    schema_version: Literal["wang_viewpoint_graph_backreview_v1"] = BACKREVIEW_VERSION
    structure_reviews: list[BackReviewedStructure] = Field(default_factory=list)
    relation_reviews: list[BackReviewedRelation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_response(self) -> "ViewpointGraphBackReviewResponse":
        ids = [item.structure_revision_id for item in self.structure_reviews]
        if len(ids) != len(set(ids)):
            raise ValueError("structure reviews must be unique")
        ids = [item.viewpoint_relation_id for item in self.relation_reviews]
        if len(ids) != len(set(ids)):
            raise ValueError("relation reviews must be unique")
        return self


def build_backreview_packet(
    *,
    structure_revisions: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
    viewpoint_revisions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Committed graph records, with the propositions they point at spelled out.

    A reviewer cannot judge whether a synthesis follows from its focal
    viewpoints, or whether an edge reads correctly source-first, from ids.
    """

    proposition = {
        str(item["viewpoint_revision_id"]): str(item["core_proposition"])
        for item in viewpoint_revisions
    }

    def _named(revision_id: str) -> dict[str, str]:
        return {
            "viewpoint_revision_id": revision_id,
            "core_proposition": proposition.get(revision_id, "(不在库中)"),
        }

    packet = {
        "schema_version": BACKREVIEW_PACKET_VERSION,
        "structures": [
            {
                "structure_revision_id": str(item["structure_revision_id"]),
                "central_synthesis": str(item["central_synthesis"]),
                "unresolved_items": list(item.get("unresolved_items") or []),
                "focal": [
                    {
                        **_named(str(focal["viewpoint_revision_id"])),
                        "structure_role": str(focal["structure_role"]),
                    }
                    for focal in item.get("focal_viewpoints") or []
                ],
            }
            for item in sorted(
                structure_revisions, key=lambda row: str(row["structure_revision_id"])
            )
        ],
        "relations": [
            {
                "viewpoint_relation_id": str(item["viewpoint_relation_id"]),
                "relation_type": str(item["relation_type"]),
                "reason": str(item.get("reason") or ""),
                "source": _named(str(item["validated_source_viewpoint_revision_id"])),
                "target": _named(str(item["validated_target_viewpoint_revision_id"])),
            }
            for item in sorted(
                relations, key=lambda row: str(row["viewpoint_relation_id"])
            )
        ],
    }
    packet["packet_sha256"] = sha256_json(packet)
    return packet


def validate_backreview(
    *,
    backreview: ViewpointGraphBackReviewResponse,
    packet: Mapping[str, Any],
) -> dict[str, Any]:
    """Require a ruling on every record sent, and only on those."""

    findings: list[str] = []
    sent_structures = {item["structure_revision_id"] for item in packet["structures"]}
    ruled_structures = {item.structure_revision_id for item in backreview.structure_reviews}
    for missing in sorted(sent_structures - ruled_structures):
        findings.append(f"{missing}: committed structure has no review decision")
    for extra in sorted(ruled_structures - sent_structures):
        findings.append(f"{extra}: decision names no structure in this packet")

    sent_relations = {item["viewpoint_relation_id"] for item in packet["relations"]}
    ruled_relations = {item.viewpoint_relation_id for item in backreview.relation_reviews}
    for missing in sorted(sent_relations - ruled_relations):
        findings.append(f"{missing}: committed relation has no review decision")
    for extra in sorted(ruled_relations - sent_relations):
        findings.append(f"{extra}: decision names no relation in this packet")

    if findings:
        raise BatchResolutionError(findings)

    # A record only carries provenance once it survived the review. The two
    # structured questions override a typed `pass` here for the same reason
    # they do in the batch contract.
    approved_structures = sorted(
        item.structure_revision_id
        for item in backreview.structure_reviews
        if item.decision == "pass" and item.synthesis_entailed_by_focal
    )
    approved_relations = sorted(
        item.viewpoint_relation_id
        for item in backreview.relation_reviews
        if item.decision == "pass" and item.direction_correct
    )
    report = {
        "schema_version": "wang_viewpoint_graph_backreview_validation_v1",
        "packet_sha256": str(packet["packet_sha256"]),
        "structure_count": len(sent_structures),
        "relation_count": len(sent_relations),
        "approved_structure_revision_ids": approved_structures,
        "approved_viewpoint_relation_ids": approved_relations,
        "held_structure_revision_ids": sorted(sent_structures - set(approved_structures)),
        "held_viewpoint_relation_ids": sorted(sent_relations - set(approved_relations)),
        "checks_passed": ["exact_once_record_coverage"],
    }
    report["artifact_sha256"] = sha256_json(report)
    return report
