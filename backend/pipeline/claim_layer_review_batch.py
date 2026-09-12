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

from backend.pipeline.corpus_ai_review import AI_REVIEW_VERSION, apply_risk_routing
from backend.pipeline.knowledge_package import live_claims
from backend.pipeline.source_keys import package_row_key


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


def _derived_batch_sha256(package: dict[str, Any]) -> str:
    candidate = copy.deepcopy(package)
    (candidate.get("review_batch") or {}).pop("batch_artifact_sha256", None)
    return _sha256_json(candidate)


def validate_split_claim_layer_package(package: dict[str, Any]) -> None:
    """Prove a claim-only review partition has not changed after splitting."""

    review_batch = package.get("review_batch")
    if not isinstance(review_batch, dict):
        raise ClaimLayerReviewBatchError("derived review package lacks review_batch")
    expected = str(review_batch.get("batch_artifact_sha256") or "")
    if not expected or expected != _derived_batch_sha256(package):
        raise ClaimLayerReviewBatchError(
            "derived review package artifact SHA is missing or invalid"
        )
    claims = live_claims(package)
    claim_ids = [str(row.get("claim_id") or "") for row in claims]
    declared = [str(value) for value in review_batch.get("claim_ids") or []]
    if (
        not all(claim_ids)
        or claim_ids != declared
        or int(review_batch.get("claim_count") or -1) != len(claim_ids)
    ):
        raise ClaimLayerReviewBatchError(
            "derived review package claim snapshot does not match its partition"
        )
    other_ids = [
        str(row.get("claim_id") or "")
        for row in review_batch.get("other_batch_claims") or []
        if isinstance(row, dict)
    ]
    if len(other_ids) != len(set(other_ids)) or set(other_ids) & set(claim_ids):
        raise ClaimLayerReviewBatchError(
            "derived review package other-claim scope is ambiguous"
        )


def split_claim_layer_package(
    package: dict[str, Any], *, batch_size: int
) -> list[dict[str, Any]]:
    """Split only claims while preserving all sources and relation context."""
    if batch_size <= 0:
        raise ClaimLayerReviewBatchError("batch_size must be positive")
    claims = live_claims(package)
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
        in_batch = {str(item["claim_id"]) for item in batch_claims}
        batch["review_batch"] = {
            "batch_index": index,
            "batch_count": total,
            "claim_count": len(batch_claims),
            "claim_ids": [item["claim_id"] for item in batch_claims],
            # Id and statement of every other claim in the package.  Duplicate
            # detection is the one check whose answer lies outside the batch:
            # section extraction states a conclusion once per section, and an
            # arbitrary partition puts the twin wherever it falls.  The reviewer
            # reviews only `claims`; it may point at these.
            "other_batch_claims": [
                {
                    "claim_id": str(row["claim_id"]),
                    "statement": str(row.get("title") or row.get("statement") or ""),
                }
                for row in claims
                if str(row["claim_id"]) not in in_batch
            ],
            "source_package_id": package.get("package_id"),
            "source_package_sha256": _sha256_json(package),
            "partition_policy": "claims_only_all_sources_and_relations_retained",
        }
        batch["package_id"] = f"{package.get('package_id') or 'CLAIM-LAYER'}-REVIEW-{index:02d}"
        batch["review_batch"]["batch_artifact_sha256"] = _derived_batch_sha256(
            batch
        )
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
    claims = live_claims(package)
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
        batch["review_batch"]["batch_artifact_sha256"] = _derived_batch_sha256(
            batch
        )
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
    try:
        package_on_disk = json.loads(source_package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaimLayerReviewBatchError(
            f"cannot read source package snapshot: {source_package_path}"
        ) from exc
    if package_on_disk != source_package:
        raise ClaimLayerReviewBatchError(
            "source package object does not match the package snapshot on disk"
        )
    from backend.pipeline.corpus_ai_review_runner import _normalize_claim_layer

    expected_claims = _normalize_claim_layer(source_package)["candidate_claims"]
    expected_ids = [str(row.get("claim_id") or "") for row in expected_claims]
    expected_source_package_sha256 = _sha256_json(source_package)
    reviews: list[dict[str, Any]] = []
    reviewed_claims: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []
    reviewer_batches: list[dict[str, Any]] = []
    partition_policies: set[str] = set()
    spot_check_percents: set[int] = set()
    usage_rows: list[dict[str, Any]] = []
    batch_indexes: list[int] = []
    declared_batch_counts: set[int] = set()
    for index, artifact in enumerate(artifacts, start=1):
        from backend.pipeline.corpus_ai_review_runner import _matching_review_artifact

        reviewed_snapshot = artifact.get("reviewed_claims") or []
        reviewer_fingerprint = str(
            (artifact.get("reviewer") or {}).get("fingerprint_sha256") or ""
        )
        spot_check_percent = artifact.get("spot_check_percent")
        review_batch = (artifact.get("source") or {}).get("review_batch") or {}
        reviewed_ids = [
            str(row.get("claim_id") or "")
            for row in reviewed_snapshot
            if isinstance(row, dict)
        ]
        declared_ids = [str(value) for value in review_batch.get("claim_ids") or []]
        if (
            reviewed_ids != declared_ids
            or review_batch.get("source_package_sha256")
            != expected_source_package_sha256
        ):
            raise ClaimLayerReviewBatchError(
                f"review batch {index} is not bound to its source-package partition"
            )
        try:
            batch_index = int(review_batch["batch_index"])
            batch_count = int(review_batch["batch_count"])
            claim_count = int(review_batch["claim_count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ClaimLayerReviewBatchError(
                f"review batch {index} has incomplete partition metadata"
            ) from exc
        if claim_count != len(reviewed_ids) or batch_index <= 0 or batch_count <= 0:
            raise ClaimLayerReviewBatchError(
                f"review batch {index} has inconsistent partition metadata"
            )
        batch_indexes.append(batch_index)
        declared_batch_counts.add(batch_count)
        if not isinstance(spot_check_percent, int) or not _matching_review_artifact(
            artifact,
            survey={
                "candidate_claims": reviewed_snapshot,
                "other_batch_claims": review_batch.get("other_batch_claims") or [],
            },
            expected_fingerprint=reviewer_fingerprint,
            spot_check_percent=spot_check_percent,
        ):
            raise ClaimLayerReviewBatchError(
                f"review batch {index} is incomplete or was modified"
            )
        reviews.extend(artifact.get("claim_reviews") or [])
        reviewed_claims.extend(artifact.get("reviewed_claims") or [])
        # Every batch was billed.  Dropping these left the batched path -- the
        # only one a package this size can take -- with no answer to what the
        # review cost, which is the number the per-call rows exist to produce.
        usage_rows.extend(
            {**row, "batch_index": index} for row in artifact.get("usage") or []
        )
        assessments.append(
            {
                "batch_index": index,
                **(artifact.get("sermon_assessment") or {}),
            }
        )
        spot_check_percents.add(int(artifact["spot_check_percent"]))
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
    if (
        len(reviewed_claim_ids) != len(set(reviewed_claim_ids))
        or set(reviewed_claim_ids) != set(expected_ids)
    ):
        raise ClaimLayerReviewBatchError("combined reviewed-claim snapshot is incomplete")
    if declared_batch_counts != {len(artifacts)} or sorted(batch_indexes) != list(
        range(1, len(artifacts) + 1)
    ):
        raise ClaimLayerReviewBatchError(
            "review batches do not form one complete ordered partition"
        )
    if len(partition_policies) > 1:
        raise ClaimLayerReviewBatchError(
            f"review batches use inconsistent partition policies: {sorted(partition_policies)}"
        )
    if len(spot_check_percents) != 1:
        raise ClaimLayerReviewBatchError(
            "review batches use inconsistent spot-check percentages"
        )

    review_by_id = {row["claim_id"]: row for row in reviews}
    claim_by_id = {row["claim_id"]: row for row in reviewed_claims}
    ordered_reviews = [review_by_id[claim_id] for claim_id in expected_ids]
    ordered_claims = [claim_by_id[claim_id] for claim_id in expected_ids]
    if ordered_claims != expected_claims:
        raise ClaimLayerReviewBatchError(
            "combined reviewed-claim snapshot differs from the source package"
        )
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
    spot_check_percent = next(iter(spot_check_percents))
    routed = apply_risk_routing(
        {"claim_reviews": ordered_reviews},
        reviewer_fingerprint_sha256=aggregate_reviewer_fingerprint,
        spot_check_percent=spot_check_percent,
    )
    systemic_risks = list(
        dict.fromkeys(
            str(value)
            for assessment in assessments
            for value in assessment.get("systemic_risks") or []
            if str(value)
        )
    )
    combined = {
        "schema_version": AI_REVIEW_VERSION,
        "source": {
            "input_mode": "curated_claim_layer_batched",
            "package_path": str(source_package_path),
            "package_id": source_package.get("package_id"),
            "package_sha256": hashlib.sha256(source_package_path.read_bytes()).hexdigest(),
            "claim_count": len(expected_ids),
            "transcript_id": package_row_key(source_package),
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
        "spot_check_percent": spot_check_percent,
        "sermon_assessment": {
            "summary": "\n".join(
                str(row.get("summary") or "")
                for row in assessments
                if str(row.get("summary") or "")
            ),
            "systemic_risks": systemic_risks,
        },
        "sermon_assessments": assessments,
        "usage": usage_rows,
        "reviewed_claims": ordered_claims,
        **routed,
    }
    from backend.pipeline.corpus_ai_review_runner import _review_artifact_sha256

    combined["reviewer"]["artifact_sha256"] = _review_artifact_sha256(
        combined
    )
    return combined
