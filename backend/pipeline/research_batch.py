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

from backend.api.canonical_repository.postgres_store import sha256_json
from backend.api.canonical_repository.reviewed_candidate_contract import (
    CONSENSUS_APPLICATION_VERSION,
    RESEARCH_BATCH_AGGREGATE,
    ConsensusApplicationError,
    reseal_after_relation_id_migration,
    reviewed_candidate_artifact_sha256,
    validate_reviewed_candidate_artifact,
)
from backend.pipeline.knowledge_package_merge import (
    KnowledgePackageMergeError,
    validate_merged_package,
)
from backend.pipeline.relation_id_namespace import (
    migrate_legacy_cross_section_relation_ids,
)


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


#: What a batch member can be. `transcript_ids` names sermon transcripts by id
#: and resolves them against a transcript directory; `sources` carries the
#: notes manuscripts, which have no such directory and are addressed by path.
#: Both end up as members, and a member is what every stage runs against.
SOURCE_TYPES = ("sermon_transcript", "notes_manuscript")


class ResearchBatchValidationError(ValueError):
    """Raised when a batch smuggles in a topic assumption or is malformed."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_sources(sources: Any) -> None:
    """Check the `sources` rows a batch carries for non-transcript members.

    The row is the extraction runner's source-manifest row, deliberately: the
    runner writes the manifest out of these and `load_source_manifest` checks
    them again against the file on disk. Validating a different shape here
    would mean two schemas for one thing.
    """

    if not isinstance(sources, list):
        raise ResearchBatchValidationError("sources must be a list")
    seen: set[str] = set()
    for index, row in enumerate(sources):
        if not isinstance(row, dict):
            raise ResearchBatchValidationError(f"source row {index} is not an object")
        source_id = str(row.get("source_id") or "").strip()
        source_path = str(row.get("source_path") or "").strip()
        source_type = str(row.get("source_type") or "").strip()
        if not source_id or not source_path or not source_type:
            raise ResearchBatchValidationError(
                f"source row {index} requires source_id, source_path and source_type"
            )
        if source_type not in SOURCE_TYPES:
            raise ResearchBatchValidationError(
                f"source row {index} has unknown source_type {source_type!r}; "
                f"expected one of {SOURCE_TYPES}"
            )
        if source_id in seen:
            raise ResearchBatchValidationError(f"duplicate source_id: {source_id}")
        seen.add(source_id)


def batch_members(batch: dict[str, Any]) -> list[dict[str, Any]]:
    """Every source this batch processes, transcripts first, in batch order.

    Ordering is load-bearing rather than cosmetic: `merge_reviewed_packages`
    requires the reviewed packages to arrive in batch order, and that check is
    what catches a package silently pointing at the wrong source.
    """

    members = [
        {"key": transcript_id, "source_type": "sermon_transcript", "transcript_id": transcript_id}
        for transcript_id in batch.get("transcript_ids") or []
    ]
    members.extend(
        {**row, "key": str(row["source_id"]), "transcript_id": str(row["source_id"])}
        for row in batch.get("sources") or []
    )
    return members


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
    transcript_ids = payload.get("transcript_ids") or []
    if not isinstance(transcript_ids, list):
        raise ResearchBatchValidationError("transcript_ids must be a list")
    if any(not isinstance(value, str) or not value.strip() for value in transcript_ids):
        raise ResearchBatchValidationError("every transcript_id must be a non-empty string")
    if len(set(transcript_ids)) != len(transcript_ids):
        raise ResearchBatchValidationError("transcript_ids cannot contain duplicates")
    sources = payload.get("sources") or []
    _validate_sources(sources)
    if not transcript_ids and not sources:
        raise ResearchBatchValidationError(
            "a batch needs at least one transcript_id or one source"
        )
    keys = list(transcript_ids) + [str(row["source_id"]) for row in sources]
    if len(set(keys)) != len(keys):
        raise ResearchBatchValidationError(
            "a source_id cannot repeat a transcript_id or another source_id"
        )
    reuse = payload.get("reviewed_package_reuse") or {}
    if not isinstance(reuse, dict):
        raise ResearchBatchValidationError("reviewed_package_reuse must be an object")
    unknown_reuse = sorted(set(reuse).difference(keys))
    if unknown_reuse:
        raise ResearchBatchValidationError(
            "reviewed_package_reuse contains members outside the batch: "
            + ", ".join(unknown_reuse)
        )
    if any(not isinstance(value, str) or not value.strip() for value in reuse.values()):
        raise ResearchBatchValidationError(
            "every reviewed_package_reuse path must be a non-empty string"
        )
    section_limits = payload.get("extraction_max_section_sentences") or {}
    if not isinstance(section_limits, dict):
        raise ResearchBatchValidationError(
            "extraction_max_section_sentences must be an object"
        )
    unknown_limits = sorted(set(section_limits).difference(keys))
    if unknown_limits:
        raise ResearchBatchValidationError(
            "extraction_max_section_sentences contains members outside the batch: "
            + ", ".join(unknown_limits)
        )
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in section_limits.values()
    ):
        raise ResearchBatchValidationError(
            "every extraction_max_section_sentences value must be a positive integer"
        )
    visual_attestations = payload.get("visual_source_attestations") or {}
    if not isinstance(visual_attestations, dict):
        raise ResearchBatchValidationError(
            "visual_source_attestations must be an object"
        )
    unknown_visual_sources = sorted(set(visual_attestations).difference(keys))
    if unknown_visual_sources:
        raise ResearchBatchValidationError(
            "visual_source_attestations contains members outside the batch: "
            + ", ".join(unknown_visual_sources)
        )
    for member_key, rows in visual_attestations.items():
        if not isinstance(rows, dict) or not rows:
            raise ResearchBatchValidationError(
                f"visual_source_attestations[{member_key!r}] must be a non-empty object"
            )
        for locator, raw_sha256 in rows.items():
            if not re.fullmatch(r"S[0-9]{4,}/V[0-9]{2,}", str(locator)):
                raise ResearchBatchValidationError(
                    f"invalid visual source locator for {member_key}: {locator!r}"
                )
            if not re.fullmatch(r"[0-9a-f]{64}", str(raw_sha256)):
                raise ResearchBatchValidationError(
                    f"invalid visual source SHA256 for {member_key}#{locator}"
                )
    review_batch_size = payload.get("review_batch_size", 20)
    if (
        not isinstance(review_batch_size, int)
        or isinstance(review_batch_size, bool)
        or review_batch_size <= 0
    ):
        raise ResearchBatchValidationError(
            "review_batch_size must be a positive integer"
        )
    review_spot_check_percent = payload.get("review_spot_check_percent", 0)
    if (
        not isinstance(review_spot_check_percent, int)
        or isinstance(review_spot_check_percent, bool)
        or not 0 <= review_spot_check_percent <= 100
    ):
        raise ResearchBatchValidationError(
            "review_spot_check_percent must be an integer from 0 through 100"
        )
    policy = payload.get("candidate_generation_policy") or {}
    if policy.get("derive_after_independent_extraction") is not True:
        raise ResearchBatchValidationError(
            "candidate_generation_policy must derive topics after independent extraction"
        )
    if policy.get("allow_unassigned_material") is not True:
        raise ResearchBatchValidationError(
            "candidate_generation_policy must allow material to remain unassigned"
        )
    corrections = payload.get("source_fidelity_corrections") or []
    if not isinstance(corrections, list):
        raise ResearchBatchValidationError("source_fidelity_corrections must be a list")
    if corrections:
        raise ResearchBatchValidationError(
            "source_fidelity_corrections after independent review are retired; "
            "correct the extraction input or create a new review/adjudication generation"
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
    expected = [member["key"] for member in batch_members(batch)]
    if len(package_paths) != len(expected):
        raise ResearchBatchValidationError("one reviewed package is required per batch member")

    merged: dict[str, list[dict[str, Any]]] = {name: [] for name in COLLECTIONS}
    seen: dict[str, set[str]] = {name: set() for name in COLLECTIONS}
    lineage: list[dict[str, Any]] = []
    review_resolutions: list[dict[str, Any]] = []
    applied_claim_ids: set[str] = set()
    merged_claim_ids: dict[str, str] = {}
    actual: list[str] = []

    for path in package_paths:
        raw = path.read_bytes()
        original_package = json.loads(raw)
        try:
            validate_reviewed_candidate_artifact(original_package)
            validate_merged_package(original_package)
        except (ConsensusApplicationError, KnowledgePackageMergeError) as exc:
            raise ResearchBatchValidationError(
                f"invalid reviewed package {path}: {exc}"
            ) from exc
        package, relation_id_migration = migrate_legacy_cross_section_relation_ids(
            original_package
        )
        package = reseal_after_relation_id_migration(
            original_package, package, relation_id_migration
        )
        try:
            validate_merged_package(package)
        except KnowledgePackageMergeError as exc:
            raise ResearchBatchValidationError(
                f"reviewed package migration produced an invalid graph {path}: {exc}"
            ) from exc
        sources = package.get("source_documents") or []
        if len(sources) != 1:
            raise ResearchBatchValidationError(
                f"reviewed package must contain exactly one source: {path}"
            )
        # A notes manuscript carries the same value under both keys; falling
        # back keeps a manifest row that never set `transcript_id` readable.
        transcript_id = str(sources[0].get("transcript_id") or sources[0].get("source_id") or "")
        actual.append(transcript_id)
        consensus = package.get("consensus_application") or {}
        original_consensus = original_package.get("consensus_application") or {}
        if consensus.get("approval_status") not in {None, "not_human_approved"}:
            raise ResearchBatchValidationError(
                f"unexpected approval status in reviewed package: {path}"
            )
        for name in COLLECTIONS:
            _append_unique(
                merged[name], package.get(name) or [],
                id_field=ID_FIELDS[name], seen=seen[name],
            )
        for resolution in consensus.get("review_resolutions") or []:
            review_resolutions.append(
                {
                    **dict(resolution),
                    "source_transcript_id": transcript_id,
                    "source_reviewed_candidate_artifact_sha256": consensus.get(
                        "artifact_sha256"
                    ),
                    "source_review_artifact_sha256": consensus.get(
                        "review_artifact_sha256"
                    ),
                    "source_review_fingerprint": consensus.get(
                        "review_fingerprint"
                    ),
                    "source_adjudication_artifact_sha256": consensus.get(
                        "adjudication_artifact_sha256"
                    ),
                    "source_overrides_artifact_sha256": consensus.get(
                        "overrides_artifact_sha256"
                    ),
                    "source_adjudication_fingerprint": consensus.get(
                        "adjudication_fingerprint"
                    ),
                }
            )
        applied_claim_ids.update(consensus.get("applied_claim_ids") or [])
        for claim_id, survivor_id in (
            consensus.get("merged_claim_ids") or {}
        ).items():
            prior = merged_claim_ids.setdefault(str(claim_id), str(survivor_id))
            if prior != str(survivor_id):
                raise ResearchBatchValidationError(
                    f"conflicting merged claim target: {claim_id}"
                )
        lineage.append(
            {
                "transcript_id": transcript_id,
                "package_path": str(path),
                "package_sha256": _sha256_bytes(raw),
                "package_canonical_sha256": sha256_json(original_package),
                "effective_package_sha256": sha256_json(package),
                "relation_id_namespace_migration": relation_id_migration,
                "extraction_fingerprint": (package.get("extraction") or {}).get(
                    "fingerprint_sha256"
                ),
                "review_fingerprint": consensus.get("review_fingerprint"),
                "adjudication_fingerprint": consensus.get("adjudication_fingerprint"),
                "upstream_reviewed_candidate_artifact_sha256": (
                    original_consensus.get("artifact_sha256")
                ),
                "reviewed_candidate_artifact_sha256": consensus.get("artifact_sha256"),
                "review_artifact_sha256": consensus.get("review_artifact_sha256"),
                "adjudication_artifact_sha256": consensus.get(
                    "adjudication_artifact_sha256"
                ),
                "overrides_artifact_sha256": consensus.get(
                    "overrides_artifact_sha256"
                ),
                "review_resolution_count": len(
                    consensus.get("review_resolutions") or []
                ),
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

    aggregate_refs = [
        {
            "transcript_id": row["transcript_id"],
            "reviewed_candidate_artifact_sha256": row[
                "reviewed_candidate_artifact_sha256"
            ],
            "review_artifact_sha256": row["review_artifact_sha256"],
            "adjudication_artifact_sha256": row[
                "adjudication_artifact_sha256"
            ],
            "overrides_artifact_sha256": row["overrides_artifact_sha256"],
            "review_fingerprint": row["review_fingerprint"],
            "adjudication_fingerprint": row["adjudication_fingerprint"],
            "review_resolution_count": row["review_resolution_count"],
        }
        for row in lineage
    ]
    review_counts: dict[str, int] = {}
    for claim in merged["claims"]:
        status = str(claim.get("review_status") or "candidate")
        review_counts[status] = review_counts.get(status, 0) + 1
    result = {
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
        "source_fidelity_corrections": [],
        "consensus_application": {
            "schema_version": CONSENSUS_APPLICATION_VERSION,
            "scope_kind": RESEARCH_BATCH_AGGREGATE,
            "review_completion": "complete",
            "review_artifact_sha256": sha256_json(
                [
                    (row["transcript_id"], row["review_artifact_sha256"])
                    for row in aggregate_refs
                ]
            ),
            "review_fingerprint": sha256_json(
                [
                    (row["transcript_id"], row.get("review_fingerprint"))
                    for row in lineage
                ]
            ),
            "adjudication_artifact_sha256": sha256_json(
                [
                    (row["transcript_id"], row["adjudication_artifact_sha256"])
                    for row in aggregate_refs
                ]
            ),
            "adjudication_fingerprint": sha256_json(
                [
                    (row["transcript_id"], row["adjudication_fingerprint"])
                    for row in aggregate_refs
                ]
            ),
            "overrides_artifact_sha256": sha256_json(
                [
                    (row["transcript_id"], row["overrides_artifact_sha256"])
                    for row in aggregate_refs
                ]
            ),
            "applied_claim_ids": sorted(applied_claim_ids),
            "merged_claim_ids": dict(sorted(merged_claim_ids.items())),
            "final_review_status_counts": dict(sorted(review_counts.items())),
            "review_resolutions": review_resolutions,
            "member_artifact_lineage": aggregate_refs,
            "approval_status": "not_human_approved",
        },
        "approval_status": "not_human_approved",
        "summary": {name: len(items) for name, items in merged.items()},
    }
    result["consensus_application"]["artifact_sha256"] = (
        reviewed_candidate_artifact_sha256(result)
    )
    try:
        validate_merged_package(result)
        validate_reviewed_candidate_artifact(result)
    except (KnowledgePackageMergeError, ConsensusApplicationError) as exc:
        raise ResearchBatchValidationError(
            f"merged reviewed package is internally inconsistent: {exc}"
        ) from exc
    return result
