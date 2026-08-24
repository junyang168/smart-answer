from typing import Any

import pytest

from backend.api.canonical_repository.viewpoint_argument_routes import (
    RouteProposalResponse,
    validate_route_proposal,
)
from backend.api.canonical_repository.viewpoint_batch_resolution import (
    BatchResolutionError,
    CanonicalViewpointProposalResponse,
)
from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
from backend.pipeline.viewpoint_argument_route_runner import source_slices


def _proposal(payload: dict[str, Any]) -> RouteProposalResponse:
    return RouteProposalResponse.model_validate({"source_id": "SRC-A", **payload})


def _attestation(**overrides: Any) -> dict[str, Any]:
    return {
        "conclusion_key": "CVR-1",
        "route_label": "以 Petrus／Petra 的性别差异论证",
        "inference_pattern": "greek_morphology",
        "ordered_evidence_step_ids": ["E1", "E2"],
        "completeness": "full",
        "reason": "先观察用词差异，再据此推出磐石不指彼得本人",
        **overrides,
    }


BASE = dict(
    source_id="SRC-A",
    source_evidence_step_ids=["E1", "E2", "E3"],
    conclusion_keys=["CVR-1"],
    member_evidence_step_ids=["E2"],
    identity_components=[("C1", 0, "E1"), ("C1", 1, "E2")],
)


def test_a_route_that_borrows_a_step_from_another_sermon_is_rejected():
    # The whole reason this pass runs per source: a premise from another sermon
    # makes an argument the professor never delivered here.
    with pytest.raises(BatchResolutionError, match="is not in this source"):
        validate_route_proposal(
            proposal=_proposal({"attestations": [_attestation(ordered_evidence_step_ids=["E1", "E9"])]}),
            **BASE,
        )


def test_full_requires_the_source_to_state_the_conclusion():
    # Premises alone are a partial attestation, however convincing they look.
    with pytest.raises(BatchResolutionError, match="full requires the source to state"):
        validate_route_proposal(
            proposal=_proposal(
                {
                    "attestations": [_attestation(ordered_evidence_step_ids=["E1"])],
                    "unused_components": [{"claim_id": "C1", "component_index": 1, "reason": "无推理步骤"}],
                }
            ),
            **BASE,
        )
    report = validate_route_proposal(
        proposal=_proposal(
            {
                "attestations": [
                    _attestation(ordered_evidence_step_ids=["E1"], completeness="partial")
                ],
                "unused_components": [{"claim_id": "C1", "component_index": 1, "reason": "无推理步骤"}],
            }
        ),
        **BASE,
    )
    assert report["partial_count"] == 1


def test_several_arguments_for_one_conclusion_stay_separate():
    report = validate_route_proposal(
        proposal=_proposal(
            {
                "attestations": [
                    _attestation(ordered_evidence_step_ids=["E1", "E2"]),
                    _attestation(
                        ordered_evidence_step_ids=["E3"],
                        inference_pattern="peter_character",
                        route_label="以彼得本人靠不住论证",
                        completeness="partial",
                    ),
                ]
            }
        ),
        **BASE,
    )
    assert report["attestation_count"] == 2
    assert report["inference_patterns"] == ["greek_morphology", "peter_character"]


def test_identity_material_cannot_silently_vanish():
    with pytest.raises(BatchResolutionError, match="in no route and not explained"):
        validate_route_proposal(
            proposal=_proposal({"attestations": [_attestation(ordered_evidence_step_ids=["E2"])]}),
            **BASE,
        )


def test_conclusion_must_have_been_linked_from_this_source():
    with pytest.raises(BatchResolutionError, match="was not linked from this source"):
        validate_route_proposal(
            proposal=_proposal({"attestations": [_attestation(conclusion_key="CVR-elsewhere")]}),
            **BASE,
        )


def test_source_slices_split_a_batch_by_sermon():
    claims = {
        cid: ReviewClaim(
            claim_id=cid,
            pinned_claim_revision=1,
            claim_revision_sha256=f"sha-{cid}",
            source_id=source,
            statement="教會所建造的磐石不是彼得這個人",
            review_status="approved",
            evidence=[
                {
                    "evidence_step_id": f"{cid}-E1",
                    "source_fragment_id": f"{cid}-F1",
                    "source_id": source,
                    "evidence_statement": "推理",
                    "verbatim_excerpt": "片段",
                    "citation_id": "CIT-1",
                    "citation_revision": 1,
                    "citation_status": "approved",
                    "source_sha256": "sha",
                    "support_eligibility": "eligible",
                    "anchor_state": "source_version_bound",
                    "valid_for_identity_review": True,
                }
            ],
        )
        for cid, source in (("C1", "SRC-A"), ("C2", "SRC-B"))
    }
    proposal = CanonicalViewpointProposalResponse.model_validate(
        {
            "batch_id": "CVB-1",
            "claim_decisions": [
                {
                    "claim_id": cid,
                    "components": [
                        {
                            "spans": [
                                {
                                    "start_char": 0,
                                    "end_char": 15,
                                    "exact_text": "教會所建造的磐石不是彼得這個人",
                                }
                            ],
                            "disposition": "member_existing",
                            "target_viewpoint_revision_id": "CVR-1",
                            "evidence_step_ids": [f"{cid}-E1"],
                            "source_fragment_ids": [f"{cid}-F1"],
                            "reason": "同一真值条件",
                        }
                    ],
                }
                for cid in ("C1", "C2")
            ],
        }
    )
    slices = source_slices(proposal=proposal, claims=claims)
    assert sorted(slices) == ["SRC-A", "SRC-B"]
    # Each sermon sees only its own steps — that is the guarantee, not a policy.
    assert slices["SRC-A"]["member_steps"] == {"C1-E1"}
    assert slices["SRC-B"]["member_steps"] == {"C2-E1"}


def test_every_conclusion_reaches_the_model_with_its_wording(tmp_path):
    # The first real run failed because an existing viewpoint reached the packet
    # as a bare revision id. The model correctly refused to route to a
    # conclusion it could not read, so the packet must carry the wording.
    from backend.pipeline.viewpoint_argument_route_runner import run_source

    claim = ReviewClaim(
        claim_id="C1",
        pinned_claim_revision=1,
        claim_revision_sha256="sha-C1",
        source_id="SRC-A",
        statement="磐石不是彼得這個人",
        review_status="approved",
        evidence=[
            {
                "evidence_step_id": "E1",
                "source_fragment_id": "F1",
                "source_id": "SRC-A",
                "evidence_statement": "希臘文性別差異",
                "verbatim_excerpt": "片段",
                "citation_id": "CIT-1",
                "citation_revision": 1,
                "citation_status": "approved",
                "source_sha256": "sha",
                "support_eligibility": "eligible",
                "anchor_state": "source_version_bound",
                "valid_for_identity_review": True,
            }
        ],
    )
    captured: dict[str, Any] = {}

    class _Router:
        model_id = "stub"
        backend = "stub"
        prompt_sha256 = "p"
        generation_config_sha256 = "g"

        def generate(self, packet: Any) -> dict[str, Any]:
            captured.update(packet)
            return {
                "source_id": "SRC-A",
                "attestations": [
                    {
                        "conclusion_key": "CVR-1",
                        "route_label": "以希臘文性別差異論證",
                        "inference_pattern": "greek_morphology",
                        "ordered_evidence_step_ids": ["E1"],
                        "completeness": "full",
                        "reason": "本篇既給前提也講出結論",
                    }
                ],
                "unused_components": [],
            }

    run_source(
        source_id="SRC-A",
        claims=[claim],
        entry={
            "claim_ids": {"C1"},
            "components": [("C1", 0, "E1")],
            "conclusions": {"CVR-1"},
            "member_steps": {"E1"},
        },
        candidate_labels={"CVR-1": "太16:18 的磐石不指彼得本人"},
        output_dir=tmp_path / "SRC-A",
        router=_Router(),
    )
    assert captured["conclusions"] == [
        {"conclusion_key": "CVR-1", "core_proposition": "太16:18 的磐石不指彼得本人"}
    ]


def test_a_cached_route_answer_is_not_reused_for_a_changed_packet(tmp_path):
    import json as _json

    from backend.pipeline.viewpoint_argument_route_runner import run_source

    cache = tmp_path / "SRC-A" / "raw-route.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        _json.dumps({"packet_sha256": "answers-an-older-packet", "response": {}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="answers an older packet"):
        run_source(
            source_id="SRC-A",
            claims=[],
            entry={"claim_ids": set(), "components": [], "conclusions": set(), "member_steps": set()},
            candidate_labels={},
            output_dir=tmp_path / "SRC-A",
            router=None,
        )
