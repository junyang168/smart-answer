from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.api.canonical_repository.viewpoint_identity_boundary import (
    IdentityBoundaryAssessment,
    run_identity_boundary_review,
)
from backend.api.canonical_repository.viewpoint_resolution import (
    CallableReviewerAdapter,
)
from backend.tests.test_viewpoint_resolution import _fixture


def _answer(packet, relation="equivalent_all", *, hypothesis_id="VIH-TEST"):
    return {
        "hypothesis_id": hypothesis_id,
        "packet_sha256": packet.packet_sha256,
        "participant_claim_ids": packet.candidate.candidate_claim_ids,
        "whole_relation": relation,
        "mixed_partition": [],
        "mixed_unassigned_claim_ids": [],
        "rationale": "仅判断完整 participant set 的真值条件边界。",
    }


def test_two_boundary_reviewers_can_only_advance_equivalent_all(tmp_path):
    packet, _ = _fixture(source_count=2)
    proposal = _answer(packet)
    blind = _answer(packet) | {"rationale": "独立判断，边界相同。"}
    result = run_identity_boundary_review(
        hypothesis_id="VIH-TEST",
        packet=packet,
        proposal_reviewer=CallableReviewerAdapter(
            model_id="proposal-model",
            prompt="proposal boundary prompt",
            generate=lambda _: proposal,
        ),
        blind_reviewer=CallableReviewerAdapter(
            model_id="blind-model",
            prompt="blind boundary prompt",
            generate=lambda _: blind,
        ),
        output_dir=tmp_path,
    )

    assert result.semantic_agreement is True
    assert result.agreed_relation == "equivalent_all"
    assert result.synthesis_eligible is True
    assert result.master_data_mutations == 0
    assert result.apply_allowed is False


def test_boundary_disagreement_is_not_adjudicated_into_consensus(tmp_path):
    packet, _ = _fixture(source_count=2)
    proposal = _answer(packet, "equivalent_all")
    blind = _answer(packet, "related_only")
    calls = {"proposal": 0, "blind": 0}

    def generated(name, value):
        def run(_):
            calls[name] += 1
            return value

        return run

    result = run_identity_boundary_review(
        hypothesis_id="VIH-TEST",
        packet=packet,
        proposal_reviewer=CallableReviewerAdapter(
            model_id="proposal-model",
            prompt="proposal boundary prompt",
            generate=generated("proposal", proposal),
        ),
        blind_reviewer=CallableReviewerAdapter(
            model_id="blind-model",
            prompt="blind boundary prompt",
            generate=generated("blind", blind),
        ),
        output_dir=tmp_path,
    )

    assert calls == {"proposal": 1, "blind": 1}
    assert result.disposition == "boundary_disagreement"
    assert result.agreed_relation is None
    assert result.synthesis_eligible is False


def test_mixed_partition_is_exact_disjoint_and_cannot_restate_whole_group():
    base = {
        "hypothesis_id": "VIH-MIXED",
        "packet_sha256": "packet-sha",
        "participant_claim_ids": ["C1", "C2", "C3"],
        "whole_relation": "mixed",
        "mixed_unassigned_claim_ids": ["C3"],
        "rationale": "C1 与 C2 成组，C3 尚未归组。",
    }
    valid = IdentityBoundaryAssessment.model_validate(
        base
        | {
            "mixed_partition": [
                {
                    "relation": "equivalent_all",
                    "participant_claim_ids": ["C1", "C2"],
                }
            ]
        }
    )
    assert valid.mixed_partition[0].participant_claim_ids == ["C1", "C2"]

    with pytest.raises(ValidationError, match="cover the exact participant set"):
        IdentityBoundaryAssessment.model_validate(
            base
            | {
                "mixed_unassigned_claim_ids": [],
                "mixed_partition": [
                    {
                        "relation": "equivalent_all",
                        "participant_claim_ids": ["C1", "C2"],
                    }
                ],
            }
        )

    with pytest.raises(ValidationError, match="cannot restate"):
        IdentityBoundaryAssessment.model_validate(
            base
            | {
                "mixed_unassigned_claim_ids": [],
                "mixed_partition": [
                    {
                        "relation": "equivalent_all",
                        "participant_claim_ids": ["C1", "C2", "C3"],
                    }
                ],
            }
        )


def test_boundary_phase_rejects_invented_claim_or_authoring_fields():
    with pytest.raises(ValidationError):
        IdentityBoundaryAssessment.model_validate(
            {
                "hypothesis_id": "VIH-TEST",
                "packet_sha256": "packet-sha",
                "participant_claim_ids": ["C1", "INVENTED"],
                "whole_relation": "equivalent_all",
                "mixed_partition": [],
                "mixed_unassigned_claim_ids": [],
                "rationale": "错误输入。",
                "core_proposition": "模型不得在第一阶段生成这项。",
            }
        )
