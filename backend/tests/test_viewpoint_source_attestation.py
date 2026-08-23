from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.api.canonical_repository.knowledge_models import ClaimRecord
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha
from backend.api.canonical_repository.viewpoint_source_attestation import (
    IdentitySourceEligibilityArtifact,
    IdentitySourceEligibilityAttestation,
    build_source_eligibility_artifact,
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
        "adjudication_artifact_sha256": None,
        "adjudication_status": None,
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


def test_pre_adjudication_v1_artifact_remains_sha_valid():
    row = _attestation().model_dump(mode="json")
    row.pop("adjudication_artifact_sha256")
    row.pop("adjudication_status")
    row_body = {key: value for key, value in row.items() if key != "attestation_sha256"}
    row["attestation_sha256"] = sha256_json(row_body)
    payload = {
        "schema_version": "wang_viewpoint_source_eligibility_attestation_v1",
        "claim_manifest_sha256": "manifest-sha",
        "attestations": [row],
        "exceptions": [],
        "statistics": {
            "input_claim_count": 1,
            "attested_claim_count": 1,
            "exception_claim_count": 0,
        },
        "approval_status": "not_human_approved",
        "master_data_mutations": 0,
    }
    legacy = {**payload, "artifact_sha256": sha256_json(payload)}

    artifact = IdentitySourceEligibilityArtifact.model_validate(legacy)

    assert artifact.attestations[0].independent_review_decision == "pass"


def _withdrawn_review_inputs(*, consensus_fingerprint: str = "adjudication-fingerprint"):
    claim = {
        "claim_id": "C1",
        "statement": "磐石不是彼得本人，而是彼得对耶稣的准确认识与信仰。",
        "claim_type": "explicit_claim",
        "evidence_step_ids": ["E1"],
        "review_status": "approved",
        "revision": 1,
    }
    evidence = {
        "evidence_step_id": "E1",
        "source_fragment_id": "F1",
        "statement": "释经证据",
        "support_eligibility": "eligible",
        "citation_ids": ["CIT-1"],
    }
    fragment = {
        "fragment_id": "F1",
        "source_id": "S1",
        "verbatim_excerpt": "不是彼得本人，乃是彼得对于耶稣准确的认识。",
        "citation_id": "CIT-1",
        "source_sha256": "source-sha",
        "anchor_state": "source_version_bound",
    }
    claim_revision_sha = semantic_record_sha(ClaimRecord.model_validate(claim))
    manifest_payload = {
        "schema_version": "viewpoint_input_claim_manifest_v1",
        "claims": [{
            "claim_id": "C1",
            "pinned_claim_revision": 1,
            "claim_revision_sha256": claim_revision_sha,
        }],
    }
    manifest = {**manifest_payload, "manifest_sha256": sha256_json(manifest_payload)}
    review_fingerprint = "review-fingerprint"
    adjudication_fingerprint = "adjudication-fingerprint"
    package = {
        "claims": [claim],
        "extraction": {
            "model_id": "gpt-5.6-sol",
            "backend": "codex_subscription",
            "fingerprint_sha256": "extraction-fingerprint",
        },
        "consensus_application": {
            "applied_claim_ids": [],
            "adjudication_fingerprint": consensus_fingerprint,
        },
    }
    review = {
        "reviewer": {
            "review_model_id": "claude-opus-5",
            "provider": "anthropic",
            "fingerprint_sha256": review_fingerprint,
        },
        "source": {"package_sha256": "review-input-sha"},
    }
    review_row = {
        "claim_id": "C1",
        "decision": "changes_suggested",
        "reviewer_fingerprint": review_fingerprint,
    }
    adjudication = {
        "adjudicator": {
            "review_fingerprint": review_fingerprint,
            "fingerprint_sha256": adjudication_fingerprint,
        },
        "results": [{"claim_id": "C1", "status": "withdrawn"}],
    }
    return {
        "claim_manifest": manifest,
        "claims": [claim],
        "evidence_steps": [evidence],
        "source_fragments": [fragment],
        "reviewed_packages_by_claim_id": {
            "C1": {"payload": package, "artifact_sha256": "package-sha"}
        },
        "reviews_by_claim_id": {"C1": {
            "payload": review,
            "claim_review": review_row,
            "review_input_artifact_sha256": "review-input-sha",
            "artifact_sha256": "review-sha",
            "adjudication_payload": adjudication,
            "adjudication_result": adjudication["results"][0],
            "adjudication_artifact_sha256": "adjudication-sha",
        }},
    }


def test_final_withdrawn_adjudication_makes_claim_identity_eligible():
    artifact = build_source_eligibility_artifact(**_withdrawn_review_inputs())

    assert artifact.exceptions == []
    assert len(artifact.attestations) == 1
    row = artifact.attestations[0]
    assert row.independent_review_decision == "changes_suggested_withdrawn"
    assert row.adjudication_status == "withdrawn"
    assert row.adjudication_artifact_sha256 == "adjudication-sha"


def test_withdrawn_adjudication_must_bind_consensus_fingerprint():
    artifact = build_source_eligibility_artifact(
        **_withdrawn_review_inputs(consensus_fingerprint="different")
    )

    assert artifact.attestations == []
    assert artifact.exceptions[0].code == "unapplied_review_change"
