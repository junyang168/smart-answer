"""Deterministically combine independently adjudicated claim-layer batches."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ClaimLayerAdjudicationBatchError(ValueError):
    pass


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def merge_adjudication_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    expected_actionable_claim_ids: list[str],
    override_artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge batch decisions and prove exact one-time actionable coverage."""
    if not artifacts:
        raise ClaimLayerAdjudicationBatchError("no adjudication artifacts to merge")
    results = [row for artifact in artifacts for row in artifact.get("results") or []]
    result_ids = [str(row.get("claim_id") or "") for row in results]
    if not all(result_ids) or len(result_ids) != len(set(result_ids)):
        raise ClaimLayerAdjudicationBatchError(
            "combined adjudication contains missing or duplicate claim IDs"
        )
    if set(result_ids) != set(expected_actionable_claim_ids):
        missing = sorted(set(expected_actionable_claim_ids) - set(result_ids))
        extra = sorted(set(result_ids) - set(expected_actionable_claim_ids))
        raise ClaimLayerAdjudicationBatchError(
            f"combined adjudication coverage mismatch; missing={missing}, extra={extra}"
        )

    overrides: dict[str, Any] = {}
    summary: dict[str, int] = {
        "auto_applied": 0,
        "withdrawn": 0,
        "human_confirmation_required": 0,
        "human_disagreement_required": 0,
    }
    batch_sources: list[dict[str, Any]] = []
    for index, artifact in enumerate(artifacts, start=1):
        batch_sources.append(
            {
                "batch_index": index,
                "source": artifact.get("source") or {},
                "adjudicator": artifact.get("adjudicator") or {},
            }
        )
        for key in summary:
            summary[key] += int((artifact.get("summary") or {}).get(key, 0))
    override_sources = override_artifacts if override_artifacts is not None else artifacts
    if len(override_sources) != len(artifacts):
        raise ClaimLayerAdjudicationBatchError(
            "adjudication and application-override batch counts do not agree"
        )
    for artifact in override_sources:
        rows = artifact.get("claims") if override_artifacts is not None else artifact.get("claim_overrides")
        for claim_id, patch in (rows or {}).items():
            if claim_id in overrides:
                raise ClaimLayerAdjudicationBatchError(
                    f"duplicate claim override across batches: {claim_id}"
                )
            overrides[claim_id] = patch

    auto_applied = {row["claim_id"] for row in results if row.get("status") == "auto_applied"}
    if set(overrides) != auto_applied or summary["auto_applied"] != len(overrides):
        raise ClaimLayerAdjudicationBatchError(
            "auto-applied result and override sets do not agree"
        )
    if override_artifacts is not None:
        invalid = sorted(
            claim_id
            for claim_id, patch in overrides.items()
            if patch.get("status") != "ai_consensus_applied"
        )
        if invalid:
            raise ClaimLayerAdjudicationBatchError(
                f"application overrides lack ai_consensus_applied status: {invalid}"
            )
    ordered = {claim_id: row for claim_id, row in zip(result_ids, results)}
    return {
        "schema_version": "wang_claim_layer_adjudication_batched_v1",
        "source": {
            "batch_count": len(artifacts),
            "exact_actionable_claim_coverage_verified": True,
            "expected_actionable_claim_count": len(expected_actionable_claim_ids),
            "batch_sources": batch_sources,
        },
        "results": [ordered[claim_id] for claim_id in expected_actionable_claim_ids],
        "claim_overrides": overrides,
        "summary": summary,
        "fingerprint_sha256": _sha256_json(
            {"results": results, "claim_overrides": overrides, "summary": summary}
        ),
        "approval_status": "not_human_approved",
        "note": (
            "AI consensus repairs candidate data only. This artifact does not approve "
            "claims, publish products, or replace editorial review of composition."
        ),
    }
