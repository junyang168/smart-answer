"""Deterministically merge source-scoped knowledge packages for joint review.

This module does not synthesize, deduplicate, or approve claims.  It only
combines already validated packages and verifies that every ID and endpoint
remains unambiguous before an AI reviewer sees the cross-source collection.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


MERGED_COLLECTIONS = (
    "source_documents",
    "source_fragments",
    "questions",
    "position_nodes",
    "observations",
    "evidence_steps",
    "claims",
    "knowledge_relations",
    "claim_relations",
)

ID_FIELDS = {
    "source_documents": "source_id",
    "source_fragments": "fragment_id",
    "questions": "question_id",
    "position_nodes": "position_id",
    "observations": "observation_id",
    "evidence_steps": "evidence_step_id",
    "claims": "claim_id",
    "knowledge_relations": "relation_id",
    "claim_relations": "claim_relation_id",
}


class KnowledgePackageMergeError(ValueError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ids(rows: list[dict[str, Any]], field: str, label: str) -> set[str]:
    values = [str(row.get(field) or "") for row in rows]
    if not all(values):
        raise KnowledgePackageMergeError(f"{label}: missing {field}")
    if len(values) != len(set(values)):
        raise KnowledgePackageMergeError(f"{label}: duplicate {field}")
    return set(values)


def _reference_ids(row: dict[str, Any], field: str, owner: str) -> set[str]:
    """Return one reference set without hiding repeated edges in an array."""

    values = [str(value) for value in row.get(field) or []]
    if len(values) != len(set(values)):
        raise KnowledgePackageMergeError(
            f"{owner}: duplicate references in {field}"
        )
    return set(values)


def validate_merged_package(package: dict[str, Any]) -> None:
    ids = {
        name: _ids(list(package.get(name) or []), field, name)
        for name, field in ID_FIELDS.items()
    }
    id_owners: dict[str, str] = {}
    collisions: list[str] = []
    for collection, values in ids.items():
        for value in values:
            prior = id_owners.setdefault(value, collection)
            if prior != collection:
                collisions.append(f"{value} ({prior}, {collection})")
    if collisions:
        raise KnowledgePackageMergeError(
            "record IDs must be globally unique: " + ", ".join(sorted(collisions))
        )
    source_ids = ids["source_documents"]
    fragment_ids = ids["source_fragments"]
    evidence_ids = ids["evidence_steps"]
    claim_ids = ids["claims"]
    position_ids = ids["position_nodes"]
    observation_ids = ids["observations"]

    for row in package.get("source_fragments", []):
        if str(row.get("source_id") or "") not in source_ids:
            raise KnowledgePackageMergeError(
                f"{row['fragment_id']}: unknown source_id {row.get('source_id')}"
            )
    for collection in ("questions", "position_nodes", "observations", "evidence_steps"):
        for row in package.get(collection, []):
            referenced_fragments = _reference_ids(
                row,
                "source_fragment_ids",
                f"{collection}/{row[ID_FIELDS[collection]]}",
            )
            if row.get("source_fragment_id"):
                referenced_fragments.add(str(row["source_fragment_id"]))
            missing = referenced_fragments - fragment_ids
            if missing:
                raise KnowledgePackageMergeError(
                    f"{collection}/{row[ID_FIELDS[collection]]}: unknown fragments {sorted(missing)}"
                )
    for row in package.get("questions", []):
        missing = _reference_ids(
            row, "answer_claim_ids", row["question_id"]
        ) - claim_ids
        if missing:
            raise KnowledgePackageMergeError(
                f"{row['question_id']}: unknown answer claims {sorted(missing)}"
            )
    for row in package.get("evidence_steps", []):
        missing = _reference_ids(
            row, "produced_claim_ids", row["evidence_step_id"]
        ) - claim_ids
        if missing:
            raise KnowledgePackageMergeError(
                f"{row['evidence_step_id']}: unknown produced claims {sorted(missing)}"
            )
    for row in package.get("claims", []):
        missing_evidence = _reference_ids(
            row, "evidence_step_ids", row["claim_id"]
        ) - evidence_ids
        missing_positions = _reference_ids(
            row, "opposed_position_ids", row["claim_id"]
        ) - position_ids
        if missing_evidence:
            raise KnowledgePackageMergeError(
                f"{row['claim_id']}: unknown evidence {sorted(missing_evidence)}"
            )
        if missing_positions:
            raise KnowledgePackageMergeError(
                f"{row['claim_id']}: unknown positions {sorted(missing_positions)}"
            )

    # These are two stored projections of one many-to-many ``used_for`` link,
    # not independent hints.  Checking only endpoint existence let a Claim
    # consume an EvidenceStep that did not name it, while the EvidenceStep
    # simultaneously claimed to serve a different conclusion.  Downstream
    # route and publication consumers then saw different graphs depending on
    # which direction they traversed.
    claim_evidence_pairs = {
        (str(claim["claim_id"]), str(evidence_id))
        for claim in package.get("claims", [])
        for evidence_id in claim.get("evidence_step_ids") or []
    }
    evidence_claim_pairs = {
        (str(claim_id), str(evidence["evidence_step_id"]))
        for evidence in package.get("evidence_steps", [])
        for claim_id in evidence.get("produced_claim_ids") or []
    }
    if claim_evidence_pairs != evidence_claim_pairs:
        claim_only = sorted(claim_evidence_pairs - evidence_claim_pairs)
        evidence_only = sorted(evidence_claim_pairs - claim_evidence_pairs)
        raise KnowledgePackageMergeError(
            "claim/evidence bindings must be reciprocal; "
            f"claim_only={claim_only}, evidence_only={evidence_only}"
        )
    # An evidence relation may reason from an observation to an evidence step.
    # Treating both endpoints as evidence rejected every normal detailed package
    # that preserved a load-bearing observation, even though extraction's own
    # schema and validator require that relation.
    semantic_edges: dict[tuple[str, str, str, str], str] = {}

    def validate_edge_identity(
        collection: str, edge_id: str, row: dict[str, Any]
    ) -> tuple[str, str]:
        from_id = str(row.get("from_id") or "")
        to_id = str(row.get("to_id") or "")
        relation_type = str(row.get("relation_type") or "").strip()
        if not from_id or not to_id:
            raise KnowledgePackageMergeError(
                f"{collection}/{edge_id}: empty endpoint"
            )
        if from_id == to_id:
            raise KnowledgePackageMergeError(
                f"{collection}/{edge_id}: relation cannot point to itself"
            )
        if not relation_type:
            raise KnowledgePackageMergeError(
                f"{collection}/{edge_id}: missing relation_type"
            )
        signature = (collection, from_id, to_id, relation_type)
        prior = semantic_edges.setdefault(signature, edge_id)
        if prior != edge_id:
            raise KnowledgePackageMergeError(
                f"{collection}: duplicate semantic relation {prior} and {edge_id} "
                f"both name {from_id}->{to_id} ({relation_type})"
            )
        return from_id, to_id

    for row in package.get("knowledge_relations", []):
        relation_id = str(row.get("relation_id") or "")
        from_id, to_id = validate_edge_identity(
            "knowledge_relations", relation_id, row
        )
        missing_from = {str(row.get("from_id") or "")} - (evidence_ids | observation_ids)
        missing_to = {str(row.get("to_id") or "")} - evidence_ids
        if missing_from or missing_to:
            raise KnowledgePackageMergeError(
                f"knowledge_relations/{row[ID_FIELDS['knowledge_relations']]}: "
                f"unknown endpoints {sorted(missing_from | missing_to)}"
            )
    for row in package.get("claim_relations", []):
        relation_id = str(row.get("claim_relation_id") or "")
        from_id, to_id = validate_edge_identity(
            "claim_relations", relation_id, row
        )
        missing = {from_id, to_id} - claim_ids
        if missing:
            raise KnowledgePackageMergeError(
                f"claim_relations/{row[ID_FIELDS['claim_relations']]}: "
                f"unknown endpoints {sorted(missing)}"
            )


def merge_packages(
    paths: list[Path],
    *,
    package_id: str,
    batch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not paths:
        raise KnowledgePackageMergeError("no packages selected")
    merged: dict[str, Any] = {
        "schema_version": "wang_shared_knowledge_review_batch_v1",
        "package_id": package_id,
        **{name: [] for name in MERGED_COLLECTIONS},
        "source_packages": [],
    }
    if batch is not None:
        merged["batch"] = dict(batch)
    for path in paths:
        package = json.loads(path.read_text(encoding="utf-8"))
        merged["source_packages"].append(
            {
                "path": str(path),
                "sha256": _sha256(path),
                "package_id": package.get("package_id"),
                "extraction": package.get("extraction") or {},
            }
        )
        for name in MERGED_COLLECTIONS:
            merged[name].extend(package.get(name) or [])
    validate_merged_package(merged)
    merged["summary"] = {
        f"{name}_count": len(merged[name]) for name in MERGED_COLLECTIONS
    }
    return merged
