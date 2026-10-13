from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_historical_source_eligibility import HistoricalSourceEligibilityArtifact
from backend.pipeline.viewpoint_scope_packet_runner import load_source_eligibility


def fixture(tmp_path):
    row = {"claim_id": "C1", "pinned_claim_revision": 2, "claim_revision_sha256": "current-claim-sha",
           "source_id": "S1", "source_sha256": "current-source-sha", "historical_review_bridge_sha256": "verified-bridge-sha"}
    row["attestation_sha256"] = sha256_json(row)
    body = {"schema_version": "wang_historical_viewpoint_source_eligibility_v1", "claim_manifest_sha256": "manifest-sha",
            "bridge_manifest_binding": {"path": str(tmp_path / "bridges.json"), "file_sha256": "physical-sha"},
            "attestations": [row], "exceptions": [],
            "statistics": {"input_claim_count": 1, "attested_claim_count": 1, "exception_claim_count": 0},
            "approval_status": "not_human_approved", "master_data_mutations": 0}
    return body | {"artifact_sha256": sha256_json(body)}


def test_typed_historical_eligibility_preserves_distinct_proof_contract(tmp_path):
    payload = fixture(tmp_path)
    loaded = load_source_eligibility(payload)
    assert isinstance(loaded, HistoricalSourceEligibilityArtifact)
    assert loaded.approval_status == "not_human_approved"
    assert loaded.attestations[0].historical_review_bridge_sha256 == "verified-bridge-sha"
    assert "reviewed_candidate_artifact_sha256" not in loaded.attestations[0].model_dump()


@pytest.mark.parametrize("mutation", ["row_pin", "row_proof", "artifact_sha", "denominator", "duplicate", "exception", "human_approval", "relative_binding", "invented_extraction"])
def test_invalid_history_eligibility_never_becomes_a_scope_attestation(tmp_path, mutation):
    payload = deepcopy(fixture(tmp_path))
    if mutation == "row_pin":
        payload["attestations"][0]["pinned_claim_revision"] += 1
    elif mutation == "row_proof":
        payload["attestations"][0]["historical_review_bridge_sha256"] = "changed"
    elif mutation == "artifact_sha":
        payload["artifact_sha256"] = "changed"
    elif mutation == "denominator":
        payload["statistics"]["input_claim_count"] += 1
    elif mutation == "duplicate":
        payload["attestations"].append(deepcopy(payload["attestations"][0]))
    elif mutation == "exception":
        payload["exceptions"] = [{"claim_id": "C2"}]
    elif mutation == "human_approval":
        payload["approval_status"] = "human_approved"
    elif mutation == "relative_binding":
        payload["bridge_manifest_binding"]["path"] = "relative.json"
        unsigned = {key: value for key, value in payload.items() if key != "artifact_sha256"}
        payload["artifact_sha256"] = sha256_json(unsigned)
    else:
        payload["attestations"][0]["extraction_model_id"] = "invented-model"
    with pytest.raises(ValidationError):
        load_source_eligibility(payload)
