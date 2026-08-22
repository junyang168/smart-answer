from __future__ import annotations

import pytest

from backend.api.canonical_repository.knowledge_models import ClaimRecord
from backend.api.canonical_repository.viewpoint_foundation import (
    semantic_record_sha,
    sha256_json,
)
from backend.pipeline.viewpoint_embedding_plan_runner import (
    build_claim_embedding_budget,
)


def _claim(claim_id: str) -> ClaimRecord:
    return ClaimRecord(
        claim_id=claim_id,
        statement=f"{claim_id} 关于彼得与磐石的解释",
        claim_type="interpretive_judgment",
        attribution="professor",
        scripture_refs=["Matt.16.18"],
    )


def _manifest(claims: list[ClaimRecord]) -> dict:
    payload = {
        "schema_version": "viewpoint_input_claim_manifest_v1",
        "coverage_snapshot_id": "VCS-1",
        "claims": [
            {
                "claim_id": claim.claim_id,
                "pinned_claim_revision": claim.revision,
                "claim_revision_sha256": semantic_record_sha(claim),
                "source_id": f"S-{claim.claim_id}",
            }
            for claim in claims
        ],
    }
    payload["manifest_sha256"] = sha256_json(payload)
    return payload


def test_budget_runner_is_no_call_sha_bound_and_byte_stable():
    claims = [_claim("C1"), _claim("C2"), _claim("C3")]
    first = build_claim_embedding_budget(
        claim_manifest=_manifest(claims), claims=claims, batch_size=2
    )
    second = build_claim_embedding_budget(
        claim_manifest=_manifest(claims), claims=list(reversed(claims)), batch_size=2
    )

    assert first["projection_manifest"] == second["projection_manifest"]
    assert first["plan"] == second["plan"]
    summary = first["summary"].model_dump(mode="json")
    assert summary == {
        "schema_version": "wang_viewpoint_claim_embedding_budget_v1",
        "claim_manifest_sha256": _manifest(claims)["manifest_sha256"],
        "projection_manifest_sha256": first["projection_manifest"].artifact_sha256,
        "plan_sha256": first["plan"].plan_sha256,
        "model_calls_executed": 0,
        "estimated_provider_call_count": 2,
        "apply_allowed": False,
        "projection_count": 3,
        "input_claim_count": 3,
        "source_ineligible_claim_count": 0,
        "source_ineligible_claim_ids": [],
        "batch_count": 2,
        "input_bytes": summary["input_bytes"],
        "estimated_input_tokens": summary["estimated_input_tokens"],
        "model": "gemini-embedding-2",
        "dimensions": 768,
        "artifact_sha256": summary["artifact_sha256"],
    }


def test_budget_runner_fails_if_claim_changed_after_manifest():
    claim = _claim("C1")
    changed = claim.model_copy(update={"statement": "changed"})

    with pytest.raises(ValueError, match="Claim SHA changed"):
        build_claim_embedding_budget(
            claim_manifest=_manifest([claim]), claims=[changed]
        )


def test_budget_runner_keeps_ineligible_claim_in_denominator_without_embedding_call():
    eligible = _claim("C1")
    ineligible = _claim("C2").model_copy(update={"review_status": "superseded"})
    artifacts = build_claim_embedding_budget(
        claim_manifest=_manifest([eligible, ineligible]),
        claims=[eligible, ineligible],
    )

    assert artifacts["summary"].input_claim_count == 2
    assert artifacts["summary"].projection_count == 1
    assert artifacts["summary"].source_ineligible_claim_ids == ["C2"]
