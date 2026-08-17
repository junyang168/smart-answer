"""Deterministic batching and recombination for claim-layer AI review.

Large claim packages can exceed a review model's practical response budget.
Only the claims are partitioned: every batch retains the complete source
documents and the complete relation context.  The combined artifact is built
mechanically and must cover every source claim exactly once.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


class ClaimLayerReviewBatchError(ValueError):
    pass


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def split_claim_layer_package(
    package: dict[str, Any], *, batch_size: int
) -> list[dict[str, Any]]:
    """Split only claims while preserving all sources and relation context."""
    if batch_size <= 0:
        raise ClaimLayerReviewBatchError("batch_size must be positive")
    claims = list(package.get("claims") or [])
    if not claims:
        raise ClaimLayerReviewBatchError("claim-layer package has no claims")
    claim_ids = [str(item.get("claim_id") or "") for item in claims]
    if not all(claim_ids) or len(claim_ids) != len(set(claim_ids)):
        raise ClaimLayerReviewBatchError("source package has missing or duplicate claim IDs")

    batches: list[dict[str, Any]] = []
    total = (len(claims) + batch_size - 1) // batch_size
    for index, start in enumerate(range(0, len(claims), batch_size), start=1):
        batch = copy.deepcopy(package)
        batch_claims = claims[start : start + batch_size]
        batch["claims"] = copy.deepcopy(batch_claims)
        batch["review_batch"] = {
            "batch_index": index,
            "batch_count": total,
            "claim_count": len(batch_claims),
            "claim_ids": [item["claim_id"] for item in batch_claims],
            "source_package_id": package.get("package_id"),
            "source_package_sha256": _sha256_json(package),
            "partition_policy": "claims_only_all_sources_and_relations_retained",
        }
        batch["package_id"] = f"{package.get('package_id') or 'CLAIM-LAYER'}-REVIEW-{index:02d}"
        batches.append(batch)
    return batches


def split_claim_layer_package_by_source(
    package: dict[str, Any]
) -> list[dict[str, Any]]:
    """Partition source-scoped claims and retain only their complete source.

    This is appropriate before cross-source synthesis: it verifies whether the
    extraction faithfully represents each source, without asking the reviewer
    to solve cross-source grouping in the same call.
    """
    source_documents = list(package.get("source_documents") or [])
    claims = list(package.get("claims") or [])
    source_ids = [str(row.get("source_id") or "") for row in source_documents]
    if not source_ids or not all(source_ids) or len(source_ids) != len(set(source_ids)):
        raise ClaimLayerReviewBatchError("source documents have missing or duplicate IDs")
    by_source: dict[str, list[dict[str, Any]]] = {source_id: [] for source_id in source_ids}
    for claim in claims:
        occurrence_sources = {
            str(occurrence.get("source_id") or occurrence.get("transcript_id") or "")
            for occurrence in claim.get("occurrences") or []
        }
        occurrence_sources.discard("")
        if len(occurrence_sources) != 1:
            raise ClaimLayerReviewBatchError(
                f"{claim.get('claim_id')}: expected exactly one source before synthesis, "
                f"found {sorted(occurrence_sources)}"
            )
        source_id = next(iter(occurrence_sources))
        if source_id not in by_source:
            raise ClaimLayerReviewBatchError(
                f"{claim.get('claim_id')}: unknown occurrence source {source_id}"
            )
        by_source[source_id].append(claim)

    batches: list[dict[str, Any]] = []
    source_package_sha256 = _sha256_json(package)
    nonempty_sources = [source_id for source_id in source_ids if by_source[source_id]]
    for index, source_id in enumerate(nonempty_sources, start=1):
        batch = copy.deepcopy(package)
        batch["source_documents"] = [
            copy.deepcopy(row)
            for row in source_documents
            if str(row.get("source_id") or "") == source_id
        ]
        batch["claims"] = copy.deepcopy(by_source[source_id])
        batch["review_batch"] = {
            "batch_index": index,
            "batch_count": len(nonempty_sources),
            "claim_count": len(batch["claims"]),
            "claim_ids": [row["claim_id"] for row in batch["claims"]],
            "source_ids": [source_id],
            "source_package_id": package.get("package_id"),
            "source_package_sha256": source_package_sha256,
            "partition_policy": "source_scoped_review_before_cross_source_synthesis",
        }
        batch["package_id"] = f"{package.get('package_id') or 'CLAIM-LAYER'}-SOURCE-{index:02d}"
        batches.append(batch)
    return batches


def merge_review_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    source_package: dict[str, Any],
    source_package_path: Path,
) -> dict[str, Any]:
    """Combine batch reviews and prove exact one-time claim coverage."""
    if not artifacts:
        raise ClaimLayerReviewBatchError("no review artifacts to merge")
    expected_ids = [str(row.get("claim_id") or "") for row in source_package.get("claims", [])]
    reviews: list[dict[str, Any]] = []
    reviewed_claims: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []
    reviewer_batches: list[dict[str, Any]] = []
    routing_summary: dict[str, int] = {}
    partition_policies: set[str] = set()
    for index, artifact in enumerate(artifacts, start=1):
        reviews.extend(artifact.get("claim_reviews") or [])
        reviewed_claims.extend(artifact.get("reviewed_claims") or [])
        assessments.append(
            {
                "batch_index": index,
                **(artifact.get("sermon_assessment") or {}),
            }
        )
        reviewer_batches.append(
            {
                "batch_index": index,
                "reviewer": artifact.get("reviewer") or {},
                "source": artifact.get("source") or {},
            }
        )
        partition_policy = str(
            ((artifact.get("source") or {}).get("review_batch") or {}).get(
                "partition_policy"
            )
            or ""
        )
        if partition_policy:
            partition_policies.add(partition_policy)
        for key, value in (artifact.get("routing_summary") or {}).items():
            routing_summary[key] = routing_summary.get(key, 0) + int(value)

    review_ids = [str(row.get("claim_id") or "") for row in reviews]
    if len(review_ids) != len(set(review_ids)):
        raise ClaimLayerReviewBatchError("combined review contains duplicate claim IDs")
    if set(review_ids) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(review_ids))
        extra = sorted(set(review_ids) - set(expected_ids))
        raise ClaimLayerReviewBatchError(
            f"combined review coverage mismatch; missing={missing}, extra={extra}"
        )
    reviewed_claim_ids = [str(row.get("claim_id") or "") for row in reviewed_claims]
    if set(reviewed_claim_ids) != set(expected_ids):
        raise ClaimLayerReviewBatchError("combined reviewed-claim snapshot is incomplete")
    if len(partition_policies) > 1:
        raise ClaimLayerReviewBatchError(
            f"review batches use inconsistent partition policies: {sorted(partition_policies)}"
        )

    review_by_id = {row["claim_id"]: row for row in reviews}
    claim_by_id = {row["claim_id"]: row for row in reviewed_claims}
    ordered_reviews = [review_by_id[claim_id] for claim_id in expected_ids]
    ordered_claims = [claim_by_id[claim_id] for claim_id in expected_ids]
    aggregate_reviewer_fingerprint = _sha256_json(
        {
            "reviewer_batches": reviewer_batches,
            "partition_policy": (
                next(iter(partition_policies))
                if partition_policies
                else "legacy_partition_policy_not_recorded"
            ),
        }
    )
    return {
        "schema_version": "wang_corpus_independent_review_batched_v1",
        "source": {
            "input_mode": "curated_claim_layer_batched",
            "package_path": str(source_package_path),
            "package_id": source_package.get("package_id"),
            "package_sha256": hashlib.sha256(source_package_path.read_bytes()).hexdigest(),
            "claim_count": len(expected_ids),
        },
        "review_strategy": {
            "batch_count": len(artifacts),
            "partition_policy": (
                next(iter(partition_policies))
                if partition_policies
                else "legacy_partition_policy_not_recorded"
            ),
            "exact_claim_coverage_verified": True,
            "reviewer_batches": reviewer_batches,
        },
        "reviewer": {
            "provider": "anthropic_batched",
            "fingerprint_sha256": aggregate_reviewer_fingerprint,
        },
        "sermon_assessments": assessments,
        "reviewed_claims": ordered_claims,
        "claim_reviews": ordered_reviews,
        "routing_summary": routing_summary,
    }
