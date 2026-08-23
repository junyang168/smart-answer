from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_source_attestation import (
    IdentitySourceEligibilityArtifact,
    IdentitySourceEligibilityAttestation,
)


def _attestation() -> IdentitySourceEligibilityAttestation:
    payload = {
        "claim_id": "C1",
        "pinned_claim_revision": 1,
        "claim_revision_sha256": "claim-sha",
        "source_id": "S1",
        "source_sha256": "source-sha",
        "extraction_model_id": "gpt-5.6-sol",
        "extraction_backend": "codex_subscription",
        "extraction_fingerprint_sha256": "extract-sha",
        "independent_review_model_id": "claude-sonnet-5",
        "independent_review_provider": "anthropic",
        "independent_review_fingerprint_sha256": "review-fingerprint",
        "independent_review_decision": "pass",
        "review_input_artifact_sha256": "review-input-sha",
        "independent_review_artifact_sha256": "review-sha",
        "reviewed_candidate_artifact_sha256": "candidate-sha",
        "evidence_dependency_sha256": "evidence-sha",
        "eligibility_scope": "viewpoint_identity_review",
        "approval_status": "not_human_approved",
        "master_data_mutation": False,
    }
    return IdentitySourceEligibilityAttestation(
        **payload, attestation_sha256=sha256_json(payload)
    )


def test_source_attestation_is_not_approval_and_is_sha_bound():
    row = _attestation()
    payload = {
        "schema_version": "wang_viewpoint_source_eligibility_attestation_v1",
        "claim_manifest_sha256": "manifest-sha",
        "attestations": [row.model_dump(mode="json")],
        "exceptions": [],
        "statistics": {
            "input_claim_count": 1,
            "attested_claim_count": 1,
            "exception_claim_count": 0,
        },
        "approval_status": "not_human_approved",
        "master_data_mutations": 0,
    }
    artifact = IdentitySourceEligibilityArtifact(
        **payload, artifact_sha256=sha256_json(payload)
    )
    assert artifact.approval_status == "not_human_approved"
    assert artifact.master_data_mutations == 0

    tampered = artifact.model_dump(mode="json")
    tampered["attestations"][0]["source_sha256"] = "changed"
    with pytest.raises(ValidationError, match="attestation SHA mismatch"):
        IdentitySourceEligibilityArtifact.model_validate(tampered)
