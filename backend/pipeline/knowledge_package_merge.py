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


def validate_merged_package(package: dict[str, Any]) -> None:
    ids = {
        name: _ids(list(package.get(name) or []), field, name)
        for name, field in ID_FIELDS.items()
    }
    source_ids = ids["source_documents"]
    fragment_ids = ids["source_fragments"]
    evidence_ids = ids["evidence_steps"]
    claim_ids = ids["claims"]
    position_ids = ids["position_nodes"]

    for row in package.get("source_fragments", []):
        if str(row.get("source_id") or "") not in source_ids:
            raise KnowledgePackageMergeError(
                f"{row['fragment_id']}: unknown source_id {row.get('source_id')}"
            )
    for collection in ("questions", "position_nodes", "observations", "evidence_steps"):
        for row in package.get(collection, []):
            missing = set(row.get("source_fragment_ids") or []) - fragment_ids
            if missing:
                raise KnowledgePackageMergeError(
                    f"{collection}/{row[ID_FIELDS[collection]]}: unknown fragments {sorted(missing)}"
                )
    for row in package.get("questions", []):
        missing = set(row.get("answer_claim_ids") or []) - claim_ids
        if missing:
            raise KnowledgePackageMergeError(
                f"{row['question_id']}: unknown answer claims {sorted(missing)}"
            )
    for row in package.get("evidence_steps", []):
        missing = set(row.get("produced_claim_ids") or []) - claim_ids
        if missing:
            raise KnowledgePackageMergeError(
                f"{row['evidence_step_id']}: unknown produced claims {sorted(missing)}"
            )
    for row in package.get("claims", []):
        missing_evidence = set(row.get("evidence_step_ids") or []) - evidence_ids
        missing_positions = set(row.get("opposed_position_ids") or []) - position_ids
        if missing_evidence:
            raise KnowledgePackageMergeError(
                f"{row['claim_id']}: unknown evidence {sorted(missing_evidence)}"
            )
        if missing_positions:
            raise KnowledgePackageMergeError(
                f"{row['claim_id']}: unknown positions {sorted(missing_positions)}"
            )
    for collection, endpoints in (
        ("knowledge_relations", evidence_ids),
        ("claim_relations", claim_ids),
    ):
        for row in package.get(collection, []):
            missing = {str(row.get("from_id") or ""), str(row.get("to_id") or "")} - endpoints
            if missing:
                raise KnowledgePackageMergeError(
                    f"{collection}/{row[ID_FIELDS[collection]]}: unknown endpoints {sorted(missing)}"
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
