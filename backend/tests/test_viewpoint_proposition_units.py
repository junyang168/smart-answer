from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.api.canonical_repository.viewpoint_proposition_units import (
    AtomicDecompositionProposal,
    build_claim_atomic_decomposition,
)
from backend.tests.test_viewpoint_resolution import _fixture


def _whole_claim_proposal(packet, claim):
    evidence = claim.evidence[0]
    return {
        "parent_packet_sha256": packet.packet_sha256,
        "claim_id": claim.claim_id,
        "pinned_claim_revision": claim.pinned_claim_revision,
        "claim_revision_sha256": claim.claim_revision_sha256,
        "units": [
            {
                "local_unit_id": "U001",
                "unit_statement": claim.statement,
                "structural_role": "whole_claim",
                "claim_statement_spans": [
                    {
                        "start_char": 0,
                        "end_char": len(claim.statement),
                        "exact_text": claim.statement,
                    }
                ],
                "evidence_references": [
                    {
                        "evidence_step_id": evidence.evidence_step_id,
                        "source_fragment_id": evidence.source_fragment_id,
                    }
                ],
                "wording_is_verbatim_or_conservative": True,
                "added_truth_conditions": [],
            }
        ],
        "coverage_segments": [
            {
                "start_char": 0,
                "end_char": len(claim.statement),
                "exact_text": claim.statement,
                "disposition": "proposition_unit",
                "local_unit_ids": ["U001"],
                "non_propositional_reason": None,
            }
        ],
        "rationale": "该 Claim 是单一命题。",
    }


def test_atomic_decomposition_is_sha_bound_and_never_master_data(tmp_path):
    del tmp_path
    packet, _ = _fixture(source_count=2)
    claim = packet.claims[0]
    proposal = AtomicDecompositionProposal.model_validate(
        _whole_claim_proposal(packet, claim)
    )
    artifact = build_claim_atomic_decomposition(
        parent_packet_sha256=packet.packet_sha256,
        claim=claim,
        proposal=proposal,
        model_calls_executed=1,
    )

    assert len(artifact.proposition_units) == 1
    assert artifact.proposition_units[0].proposition_unit_id.startswith("VPU-")
    assert artifact.proposition_units[0].approval_status == "not_human_approved"
    assert artifact.master_data_mutations == 0
    assert artifact.apply_allowed is False


def test_atomic_decomposition_rejects_silent_statement_gap():
    packet, _ = _fixture(source_count=2)
    claim = packet.claims[0]
    raw = _whole_claim_proposal(packet, claim)
    raw["coverage_segments"][0]["end_char"] -= 1
    raw["coverage_segments"][0]["exact_text"] = claim.statement[:-1]
    proposal = AtomicDecompositionProposal.model_validate(raw)
    with pytest.raises(ValueError, match="does not reach the end"):
        build_claim_atomic_decomposition(
            parent_packet_sha256=packet.packet_sha256,
            claim=claim,
            proposal=proposal,
            model_calls_executed=1,
        )


def test_atomic_decomposition_rejects_invented_evidence():
    packet, _ = _fixture(source_count=2)
    claim = packet.claims[0]
    raw = _whole_claim_proposal(packet, claim)
    raw["units"][0]["evidence_references"][0]["source_fragment_id"] = "INVENTED"
    proposal = AtomicDecompositionProposal.model_validate(raw)
    with pytest.raises(ValueError, match="invented evidence"):
        build_claim_atomic_decomposition(
            parent_packet_sha256=packet.packet_sha256,
            claim=claim,
            proposal=proposal,
            model_calls_executed=1,
        )


def test_model_cannot_skip_or_duplicate_local_unit_ids():
    packet, _ = _fixture(source_count=2)
    claim = packet.claims[0]
    raw = _whole_claim_proposal(packet, claim)
    raw["units"][0]["local_unit_id"] = "U002"
    raw["coverage_segments"][0]["local_unit_ids"] = ["U002"]
    with pytest.raises(ValidationError, match="sequential"):
        AtomicDecompositionProposal.model_validate(raw)


def test_model_cannot_add_truth_conditions():
    packet, _ = _fixture(source_count=2)
    claim = packet.claims[0]
    raw = _whole_claim_proposal(packet, claim)
    raw["units"][0]["added_truth_conditions"] = ["教授未断言的因果关系"]
    with pytest.raises(ValidationError, match="may not add truth conditions"):
        AtomicDecompositionProposal.model_validate(raw)
