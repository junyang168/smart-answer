from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
from backend.api.canonical_repository.reviewed_candidate_contract import (
    reviewed_candidate_artifact_sha256,
)
from backend.pipeline.viewpoint_source_attestation_runner import (
    _validated_lineage_inputs,
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
        "overrides_artifact_sha256": None,
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
    row.pop("overrides_artifact_sha256")
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


def _applied_attestation_payload() -> dict:
    payload = _attestation().model_dump(
        mode="json", exclude={"attestation_sha256"}
    )
    payload.update({
        "independent_review_decision": "changes_suggested_applied",
        "adjudication_artifact_sha256": "adjudication-sha",
        "adjudication_status": "auto_applied",
        "overrides_artifact_sha256": "overrides-sha",
    })
    return payload


def test_applied_attestation_rejects_legacy_hash_that_omits_exact_chain():
    payload = _applied_attestation_payload()
    legacy_payload = dict(payload)
    legacy_payload.pop("adjudication_artifact_sha256")
    legacy_payload.pop("adjudication_status")
    legacy_payload.pop("overrides_artifact_sha256")

    with pytest.raises(ValidationError, match="attestation SHA mismatch"):
        IdentitySourceEligibilityAttestation(
            **payload, attestation_sha256=sha256_json(legacy_payload)
        )


def test_applied_artifact_rejects_legacy_hash_that_omits_exact_chain():
    row_payload = _applied_attestation_payload()
    row = IdentitySourceEligibilityAttestation(
        **row_payload, attestation_sha256=sha256_json(row_payload)
    )
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
    legacy_payload = json.loads(json.dumps(payload))
    legacy_row = legacy_payload["attestations"][0]
    legacy_row.pop("adjudication_artifact_sha256")
    legacy_row.pop("adjudication_status")
    legacy_row.pop("overrides_artifact_sha256")

    with pytest.raises(ValidationError, match="artifact SHA mismatch"):
        IdentitySourceEligibilityArtifact(
            **payload, artifact_sha256=sha256_json(legacy_payload)
        )


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
            "review_artifact_sha256": "review-sha",
            "adjudication_artifact_sha256": "adjudication-sha",
            "adjudication_fingerprint": consensus_fingerprint,
            "overrides_artifact_sha256": "overrides-sha",
            "review_resolutions": [{
                "claim_id": "C1",
                "independent_review_decision": "changes_suggested",
                "adjudication_status": "withdrawn",
                "target_review_status": "ai_consensus_reviewed",
            }],
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
            "overrides_artifact_sha256": "overrides-sha",
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


def test_auto_applied_review_binds_adjudication_and_exact_overrides():
    inputs = _withdrawn_review_inputs()
    package = inputs["reviewed_packages_by_claim_id"]["C1"]["payload"]
    package["consensus_application"]["applied_claim_ids"] = ["C1"]
    package["consensus_application"]["review_resolutions"][0][
        "adjudication_status"
    ] = "auto_applied"
    binding = inputs["reviews_by_claim_id"]["C1"]
    binding["adjudication_result"]["status"] = "auto_applied"

    artifact = build_source_eligibility_artifact(**inputs)

    assert artifact.exceptions == []
    row = artifact.attestations[0]
    assert row.independent_review_decision == "changes_suggested_applied"
    assert row.adjudication_status == "auto_applied"
    assert row.adjudication_artifact_sha256 == "adjudication-sha"
    assert row.overrides_artifact_sha256 == "overrides-sha"


def test_auto_applied_review_rejects_wrong_review_fingerprint():
    inputs = _withdrawn_review_inputs()
    package = inputs["reviewed_packages_by_claim_id"]["C1"]["payload"]
    package["consensus_application"]["applied_claim_ids"] = ["C1"]
    package["consensus_application"]["review_resolutions"][0][
        "adjudication_status"
    ] = "auto_applied"
    binding = inputs["reviews_by_claim_id"]["C1"]
    binding["adjudication_result"]["status"] = "auto_applied"
    binding["adjudication_payload"]["adjudicator"]["review_fingerprint"] = "wrong"

    artifact = build_source_eligibility_artifact(**inputs)

    assert artifact.attestations == []
    assert artifact.exceptions[0].code == "unapplied_review_change"


def test_auto_applied_review_rejects_wrong_overrides_artifact():
    inputs = _withdrawn_review_inputs()
    package = inputs["reviewed_packages_by_claim_id"]["C1"]["payload"]
    package["consensus_application"]["applied_claim_ids"] = ["C1"]
    package["consensus_application"]["review_resolutions"][0][
        "adjudication_status"
    ] = "auto_applied"
    binding = inputs["reviews_by_claim_id"]["C1"]
    binding["adjudication_result"]["status"] = "auto_applied"
    binding["overrides_artifact_sha256"] = "wrong"

    artifact = build_source_eligibility_artifact(**inputs)

    assert artifact.attestations == []
    assert artifact.exceptions[0].code == "unapplied_review_change"


def test_human_spot_check_is_not_identity_eligible_even_when_first_review_passed():
    inputs = _withdrawn_review_inputs()
    package = inputs["reviewed_packages_by_claim_id"]["C1"]["payload"]
    package["consensus_application"]["review_resolutions"] = [{
        "claim_id": "C1",
        "independent_review_decision": "pass",
        "adjudication_status": "human_confirmation_required",
        "target_review_status": "human_review_required",
    }]
    review_binding = inputs["reviews_by_claim_id"]["C1"]
    review_binding["claim_review"]["decision"] = "pass"
    review_binding["adjudication_result"]["status"] = "human_confirmation_required"

    artifact = build_source_eligibility_artifact(**inputs)

    assert artifact.attestations == []
    assert artifact.exceptions[0].code == "human_review_required"


def test_attestation_runner_binds_overrides_before_database_access(
    tmp_path: Path,
) -> None:
    batch = tmp_path / "RB-TEST"
    for name in ("reviewed", "reviews", "adjudications", "overrides", "cross-section"):
        (batch / name).mkdir(parents=True, exist_ok=True)
    input_path = batch / "cross-section" / "one.cross-section.json"
    input_path.write_text('{"input":true}\n', encoding="utf-8")
    input_sha = hashlib.sha256(input_path.read_bytes()).hexdigest()
    review_path = batch / "reviews" / "one.independent-review.json"
    review_path.write_text(
        json.dumps({"source": {"package_path": str(input_path), "package_sha256": input_sha}}),
        encoding="utf-8",
    )
    adjudication_path = batch / "adjudications" / "one.ai-adjudication.json"
    adjudication_path.write_text('{"results":[]}\n', encoding="utf-8")
    overrides_path = batch / "overrides" / "one.consensus-overrides.json"
    overrides_path.write_text('{"claims":{}}\n', encoding="utf-8")

    reason = "独立 AI 复审：pass；仲裁：not_required"
    package = {
        "complete": True,
        "extraction": {"fingerprint_sha256": "e" * 64},
        "source_documents": [{"source_id": "S1"}],
        "source_fragments": [{"fragment_id": "F1", "source_id": "S1"}],
        "evidence_steps": [{
            "evidence_step_id": "E1",
            "source_fragment_ids": ["F1"],
            "produced_claim_ids": ["C1"],
        }],
        "claims": [{
            "claim_id": "C1",
            "evidence_step_ids": ["E1"],
            "review_status": "ai_consensus_reviewed",
            "reviewed_by": "reviewer",
            "reviewed_at": "2026-09-12T00:00:00+00:00",
            "review_note": reason,
        }],
        "consensus_application": {
            "schema_version": "wang_ai_consensus_application_v2",
            "scope_kind": "source_scoped",
            "review_completion": "complete",
            "review_artifact_sha256": hashlib.sha256(review_path.read_bytes()).hexdigest(),
            "review_fingerprint": "a" * 64,
            "adjudication_artifact_sha256": hashlib.sha256(adjudication_path.read_bytes()).hexdigest(),
            "adjudication_fingerprint": "b" * 64,
            "overrides_artifact_sha256": hashlib.sha256(overrides_path.read_bytes()).hexdigest(),
            "applied_claim_ids": [],
            "merged_claim_ids": {},
            "final_review_status_counts": {"ai_consensus_reviewed": 1},
            "review_resolutions": [{
                "schema_version": "wang_claim_ai_review_provenance_v1",
                "claim_id": "C1",
                "independent_review_decision": "pass",
                "adjudication_status": "not_required",
                "target_review_status": "ai_consensus_reviewed",
                "reviewer_id": "reviewer",
                "reason": reason,
                "approval_status": "not_human_approved",
            }],
            "approval_status": "not_human_approved",
        },
    }
    package["consensus_application"]["artifact_sha256"] = (
        reviewed_candidate_artifact_sha256(package)
    )
    candidate_path = batch / "reviewed" / "one.reviewed-candidate.json"
    candidate_path.write_text(json.dumps(package), encoding="utf-8")
    lineage_body = {
        "claims": [{
            "claim_id": "C1",
            "reviewed_candidate_path": str(candidate_path),
            "reviewed_candidate_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
            "independent_review_path": str(review_path),
            "independent_review_sha256": hashlib.sha256(review_path.read_bytes()).hexdigest(),
            "adjudication_path": str(adjudication_path),
            "adjudication_sha256": hashlib.sha256(adjudication_path.read_bytes()).hexdigest(),
            "overrides_path": str(overrides_path),
            "overrides_sha256": hashlib.sha256(overrides_path.read_bytes()).hexdigest(),
        }]
    }
    lineage_path = tmp_path / "lineage.json"
    lineage_path.write_text(
        json.dumps(lineage_body | {"artifact_sha256": sha256_json(lineage_body)}),
        encoding="utf-8",
    )

    assert len(_validated_lineage_inputs(
        manifest_claim_ids={"C1"}, lineage_manifest_path=lineage_path
    )) == 1

    overrides_path.write_text('{"claims":{"tampered":{}}}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="overrides_path SHA drift"):
        _validated_lineage_inputs(
            manifest_claim_ids={"C1"}, lineage_manifest_path=lineage_path
        )
