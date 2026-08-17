import copy

import pytest

from backend.pipeline.claim_layer_adjudication_batch import (
    ClaimLayerAdjudicationBatchError,
    merge_adjudication_artifacts,
)


def _artifact(claim_id: str, status: str) -> dict:
    return {
        "source": {"package_id": f"PKG-{claim_id}"},
        "adjudicator": {"fingerprint_sha256": f"fp-{claim_id}"},
        "results": [{"claim_id": claim_id, "status": status}],
        "claim_overrides": ({claim_id: {"statement": "fixed"}} if status == "auto_applied" else {}),
        "summary": {
            "auto_applied": int(status == "auto_applied"),
            "withdrawn": int(status == "withdrawn"),
            "human_confirmation_required": 0,
            "human_disagreement_required": 0,
        },
    }


def _application_override(claim_id: str) -> dict:
    return {
        "claims": {
            claim_id: {
                "title": "fixed",
                "status": "ai_consensus_applied",
                "approval_status": "not_human_approved",
            }
        }
    }


def test_merge_adjudication_proves_exact_coverage() -> None:
    result = merge_adjudication_artifacts(
        [_artifact("CL-1", "auto_applied"), _artifact("CL-2", "withdrawn")],
        expected_actionable_claim_ids=["CL-1", "CL-2"],
    )
    assert result["summary"] == {
        "auto_applied": 1,
        "withdrawn": 1,
        "human_confirmation_required": 0,
        "human_disagreement_required": 0,
    }
    assert list(result["claim_overrides"]) == ["CL-1"]
    assert result["source"]["exact_actionable_claim_coverage_verified"] is True


def test_merge_adjudication_preserves_application_ready_overrides() -> None:
    result = merge_adjudication_artifacts(
        [_artifact("CL-1", "auto_applied")],
        expected_actionable_claim_ids=["CL-1"],
        override_artifacts=[_application_override("CL-1")],
    )
    assert result["claim_overrides"]["CL-1"]["status"] == "ai_consensus_applied"
    assert result["claim_overrides"]["CL-1"]["title"] == "fixed"


def test_merge_adjudication_rejects_non_application_override() -> None:
    invalid = _application_override("CL-1")
    invalid["claims"]["CL-1"].pop("status")
    with pytest.raises(ClaimLayerAdjudicationBatchError, match="lack ai_consensus"):
        merge_adjudication_artifacts(
            [_artifact("CL-1", "auto_applied")],
            expected_actionable_claim_ids=["CL-1"],
            override_artifacts=[invalid],
        )


def test_merge_adjudication_rejects_duplicate_claim() -> None:
    duplicate = _artifact("CL-1", "withdrawn")
    with pytest.raises(ClaimLayerAdjudicationBatchError, match="duplicate"):
        merge_adjudication_artifacts(
            [_artifact("CL-1", "auto_applied"), copy.deepcopy(duplicate)],
            expected_actionable_claim_ids=["CL-1"],
        )


def test_merge_adjudication_rejects_override_mismatch() -> None:
    artifact = _artifact("CL-1", "auto_applied")
    artifact["claim_overrides"] = {}
    with pytest.raises(ClaimLayerAdjudicationBatchError, match="do not agree"):
        merge_adjudication_artifacts(
            [artifact], expected_actionable_claim_ids=["CL-1"]
        )
