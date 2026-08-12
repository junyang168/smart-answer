"""Neutral research-batch model and reviewed-package merger.

A research batch is a processing cohort, not a topic.  It may be selected by
search terms or an editorial question, but it must not assign a canonical topic
before every transcript has been extracted and reviewed independently.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "wang_research_batch_v1"
MERGED_SCHEMA_VERSION = "wang_research_batch_knowledge_v1"
FORBIDDEN_SEMANTIC_KEYS = {
    "assumed_topic",
    "canonical_topic_id",
    "canonical_topic_ids",
    "target_topic_id",
    "target_topic_ids",
    "topic_id",
    "topic_ids",
}
COLLECTIONS = (
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


class ResearchBatchValidationError(ValueError):
    """Raised when a batch smuggles in a topic assumption or is malformed."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_research_batch(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ResearchBatchValidationError(f"schema_version must be {SCHEMA_VERSION}")
    batch_id = str(payload.get("batch_id") or "")
    if not re.fullmatch(r"RB-[A-Z0-9][A-Z0-9-]*", batch_id):
        raise ResearchBatchValidationError("batch_id must use the RB-UPPERCASE-ID form")
    if payload.get("semantic_assumption") != "none":
        raise ResearchBatchValidationError("semantic_assumption must be 'none'")
    forbidden = sorted(FORBIDDEN_SEMANTIC_KEYS.intersection(payload))
    if forbidden:
        raise ResearchBatchValidationError(
            "research batch cannot pre-assign topics: " + ", ".join(forbidden)
        )
    transcript_ids = payload.get("transcript_ids")
    if not isinstance(transcript_ids, list) or not transcript_ids:
        raise ResearchBatchValidationError("transcript_ids must be a non-empty list")
    if any(not isinstance(value, str) or not value.strip() for value in transcript_ids):
        raise ResearchBatchValidationError("every transcript_id must be a non-empty string")
    if len(set(transcript_ids)) != len(transcript_ids):
        raise ResearchBatchValidationError("transcript_ids cannot contain duplicates")
    policy = payload.get("candidate_generation_policy") or {}
    if policy.get("derive_after_independent_extraction") is not True:
        raise ResearchBatchValidationError(
            "candidate_generation_policy must derive topics after independent extraction"
        )
    if policy.get("allow_unassigned_material") is not True:
        raise ResearchBatchValidationError(
            "candidate_generation_policy must allow material to remain unassigned"
        )


def load_research_batch(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    validate_research_batch(payload)
    return {
        **payload,
        "batch_config_path": str(path),
        "batch_config_sha256": _sha256_bytes(raw),
    }


def _append_unique(
    target: list[dict[str, Any]],
    incoming: Iterable[dict[str, Any]],
    *,
    id_field: str,
    seen: set[str],
) -> None:
    for item in incoming:
        item_id = str(item.get(id_field) or "")
        if not item_id or item_id in seen:
            raise ResearchBatchValidationError(
                f"duplicate or missing {id_field}: {item_id!r}"
            )
        seen.add(item_id)
        target.append(item)


def merge_reviewed_packages(
    batch: dict[str, Any], package_paths: list[Path]
) -> dict[str, Any]:
    """Merge reviewed packages without inventing topics or product routes."""

    validate_research_batch(batch)
    expected = list(batch["transcript_ids"])
    if len(package_paths) != len(expected):
        raise ResearchBatchValidationError("one reviewed package is required per transcript")

    merged: dict[str, list[dict[str, Any]]] = {name: [] for name in COLLECTIONS}
    seen: dict[str, set[str]] = {name: set() for name in COLLECTIONS}
    lineage: list[dict[str, Any]] = []
    actual: list[str] = []

    for path in package_paths:
        raw = path.read_bytes()
        package = json.loads(raw)
        sources = package.get("source_documents") or []
        if len(sources) != 1:
            raise ResearchBatchValidationError(
                f"reviewed package must contain exactly one source: {path}"
            )
        transcript_id = str(sources[0].get("transcript_id") or "")
        actual.append(transcript_id)
        consensus = package.get("consensus_application") or {}
        if consensus.get("approval_status") not in {None, "not_human_approved"}:
            raise ResearchBatchValidationError(
                f"unexpected approval status in reviewed package: {path}"
            )
        for name in COLLECTIONS:
            _append_unique(
                merged[name], package.get(name) or [],
                id_field=ID_FIELDS[name], seen=seen[name],
            )
        lineage.append(
            {
                "transcript_id": transcript_id,
                "package_path": str(path),
                "package_sha256": _sha256_bytes(raw),
                "extraction_fingerprint": (package.get("extraction") or {}).get(
                    "fingerprint_sha256"
                ),
                "adjudication_fingerprint": consensus.get("adjudication_fingerprint"),
            }
        )

    if actual != expected:
        raise ResearchBatchValidationError(
            f"reviewed packages must follow batch order; expected {expected!r}, got {actual!r}"
        )

    object_ids = set().union(
        seen["questions"], seen["position_nodes"], seen["observations"],
        seen["evidence_steps"], seen["claims"],
    )
    for relation_name in ("knowledge_relations", "claim_relations"):
        for relation in merged[relation_name]:
            for endpoint in ("from_id", "to_id"):
                endpoint_id = str(relation.get(endpoint) or "")
                if endpoint_id not in object_ids:
                    raise ResearchBatchValidationError(
                        f"{relation_name} has unresolved {endpoint}: {endpoint_id!r}"
                    )

    return {
        "schema_version": MERGED_SCHEMA_VERSION,
        "batch": {
            "batch_id": batch["batch_id"],
            "purpose": batch.get("purpose"),
            "semantic_assumption": "none",
            "selection_is_not_classification": True,
            "batch_config_path": batch.get("batch_config_path"),
            "batch_config_sha256": batch.get("batch_config_sha256"),
        },
        **merged,
        "knowledge_routes": [],
        "topic_candidates": [],
        "candidate_generation": {
            "status": "pending_cross_sermon_comparison",
            "policy": batch["candidate_generation_policy"],
        },
        "lineage": lineage,
        "approval_status": "not_human_approved",
        "summary": {name: len(items) for name, items in merged.items()},
    }
