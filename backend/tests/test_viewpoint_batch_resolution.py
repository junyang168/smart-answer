import json
from pathlib import Path
from typing import Any

import pytest

from backend.api.canonical_repository.viewpoint_batch_resolution import (
    BatchResolutionError,
    CanonicalViewpointProposalResponse,
    CanonicalViewpointReviewResponse,
    ProposedComponent,
    ProposedSpan,
    build_batch_packet,
    component_key,
    split_batches,
    validate_proposal,
    validate_review,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
from backend.pipeline.viewpoint_batch_resolution_runner import run_batch

ROCK_STATEMENT = "磐石不是彼得这个人，而是彼得所承认的信仰"
MODAL_STATEMENT = "根基更可能是基督，而不是彼得个人"


def _evidence(claim_id: str, *, eligible: bool = True) -> dict[str, Any]:
    return {
        "evidence_step_id": f"{claim_id}-E1",
        "source_fragment_id": f"{claim_id}-F1",
        "source_id": "S1",
        "evidence_statement": "教授在该段落作出的推理步骤",
        "verbatim_excerpt": "逐字片段",
        "citation_id": "CIT-1",
        "citation_revision": 1,
        "citation_status": "approved" if eligible else "unresolved",
        "source_sha256": "source-sha",
        "support_eligibility": "eligible" if eligible else "eligible_candidate",
        "anchor_state": "source_version_bound",
        "valid_for_identity_review": eligible,
    }


def _claim(claim_id: str, statement: str, *, eligible: bool = True) -> ReviewClaim:
    return ReviewClaim(
        claim_id=claim_id,
        pinned_claim_revision=1,
        claim_revision_sha256=f"sha-{claim_id}",
        source_id="S1",
        statement=statement,
        review_status="approved",
        evidence=[_evidence(claim_id, eligible=eligible)],
    )


def _span(statement: str, text: str) -> dict[str, Any]:
    start = statement.index(text)
    return {"start_char": start, "end_char": start + len(text), "exact_text": text}


def _component(
    statement: str,
    text: str,
    disposition: str,
    claim_id: str = "C1",
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "spans": [_span(statement, text)],
        "disposition": disposition,
        "reason": "测试用理由",
        **extra,
    }
    if disposition in {
        "member_existing",
        "support_existing",
        "qualification_existing",
        "tension_existing",
        "new_viewpoint",
    }:
        payload.setdefault("evidence_step_ids", [f"{claim_id}-E1"])
        payload.setdefault("source_fragment_ids", [f"{claim_id}-F1"])
    return payload


def _candidate(local_key: str) -> dict[str, Any]:
    return {
        "local_key": local_key,
        "core_proposition": "太16:18 的磐石不指彼得本人",
        "subject": "太16:18 的磐石",
        "predicate_object": "指向彼得本人",
        "polarity": "denied",
        "modality": "教授的释经判断",
        "scripture_scope": ["Matt.16.18"],
        "novelty_comparison": "现有 Registry 未收录该否定命题",
        "conditions": [],
        "population_scope": [],
    }


def _proposal(**overrides: Any) -> CanonicalViewpointProposalResponse:
    payload: dict[str, Any] = {
        "batch_id": "CVB-test-001",
        "claim_decisions": [
            {
                "claim_id": "C1",
                "components": [
                    _component(ROCK_STATEMENT, "磐石不是彼得这个人", "new_viewpoint", local_new_viewpoint_key="ROCK-NOT-PETER"),
                    _component(ROCK_STATEMENT, "而是彼得所承认的信仰", "support_existing", target_viewpoint_revision_id="CVR-1"),
                ],
            }
        ],
        "new_viewpoint_candidates": [_candidate("ROCK-NOT-PETER")],
    }
    payload.update(overrides)
    return CanonicalViewpointProposalResponse.model_validate(payload)


def test_split_batches_defaults_to_twenty_and_stays_ordered():
    claim_ids = [f"C{index:03d}" for index in range(45)]
    batches = split_batches(claim_ids)
    assert [len(batch) for batch in batches] == [20, 20, 5]
    assert [claim for batch in batches for claim in batch] == sorted(claim_ids)


def test_proposal_component_rejects_derived_and_conflicting_fields():
    # statement_component is the concatenation of the spans, so the schema must
    # not accept it from the model at all.
    with pytest.raises(ValueError):
        ProposedComponent.model_validate(
            {
                **_component(ROCK_STATEMENT, "磐石不是彼得这个人", "new_viewpoint", local_new_viewpoint_key="K"),
                "statement_component": "磐石不是彼得这个人",
            }
        )
    with pytest.raises(ValueError, match="requires a target viewpoint revision"):
        ProposedComponent.model_validate(
            _component(ROCK_STATEMENT, "磐石不是彼得这个人", "member_existing")
        )
    with pytest.raises(ValueError, match="may not carry an identity target"):
        ProposedComponent.model_validate(
            _component(
                ROCK_STATEMENT,
                "磐石不是彼得这个人",
                "no_registry_assertion",
                target_viewpoint_revision_id="CVR-1",
            )
        )


def test_span_length_and_overlap_are_rejected_before_any_model_call():
    with pytest.raises(ValueError, match="length does not match"):
        ProposedSpan(start_char=0, end_char=5, exact_text="磐石")
    with pytest.raises(ValueError, match="overlap"):
        ProposedComponent.model_validate(
            {
                "spans": [
                    _span(ROCK_STATEMENT, "磐石不是彼得这个人"),
                    _span(ROCK_STATEMENT, "彼得这个人"),
                ],
                "disposition": "new_viewpoint",
                "local_new_viewpoint_key": "K",
                "evidence_step_ids": ["C1-E1"],
                "source_fragment_ids": ["C1-F1"],
                "reason": "测试",
            }
        )


def test_validate_proposal_accepts_a_span_bound_batch():
    report = validate_proposal(
        proposal=_proposal(),
        batch_id="CVB-test-001",
        claims=[_claim("C1", ROCK_STATEMENT)],
        registry_revision_ids=["CVR-1"],
    )
    assert report["component_count"] == 2
    assert report["disposition_counts"]["new_viewpoint"] == 1
    assert report["disposition_counts"]["support_existing"] == 1
    assert len(report["member_component_keys"]) == 1


def test_shifted_span_fails_because_exact_text_disagrees():
    # An offset drifting by one character is exactly what exact_text exists to
    # catch; offsets alone would be checked against the slice they selected.
    proposal = _proposal()
    payload = proposal.model_dump(mode="json")
    span = payload["claim_decisions"][0]["components"][0]["spans"][0]
    span["start_char"] += 1
    span["end_char"] += 1
    with pytest.raises(BatchResolutionError) as excinfo:
        validate_proposal(
            proposal=CanonicalViewpointProposalResponse.model_validate(payload),
            batch_id="CVB-test-001",
            claims=[_claim("C1", ROCK_STATEMENT)],
            registry_revision_ids=["CVR-1"],
        )
    assert any("does not match the pinned statement" in item for item in excinfo.value.findings)


def test_validate_proposal_reports_every_coverage_and_reference_failure_at_once():
    proposal = _proposal(
        claim_decisions=[
            {
                "claim_id": "C1",
                "components": [
                    _component(
                        ROCK_STATEMENT,
                        "磐石不是彼得这个人",
                        "member_existing",
                        target_viewpoint_revision_id="CVR-absent",
                    )
                ],
            },
            {
                "claim_id": "C9",
                "components": [
                    _component(ROCK_STATEMENT, "而是彼得所承认的信仰", "no_registry_assertion")
                ],
            },
        ],
        new_viewpoint_candidates=[_candidate("ORPHAN")],
    )
    with pytest.raises(BatchResolutionError) as excinfo:
        validate_proposal(
            proposal=proposal,
            batch_id="CVB-test-001",
            claims=[_claim("C1", ROCK_STATEMENT), _claim("C2", MODAL_STATEMENT)],
            registry_revision_ids=["CVR-1"],
        )
    findings = "\n".join(excinfo.value.findings)
    assert "C2: Claim has no disposition" in findings
    assert "C9: Claim is not in this batch" in findings
    assert "CVR-absent" in findings
    assert "ORPHAN: new viewpoint candidate has no member component" in findings


def test_ineligible_evidence_blocks_an_identity_disposition():
    with pytest.raises(BatchResolutionError, match="identity-eligible"):
        validate_proposal(
            proposal=_proposal(),
            batch_id="CVB-test-001",
            claims=[_claim("C1", ROCK_STATEMENT, eligible=False)],
            registry_revision_ids=["CVR-1"],
        )


def test_same_component_cannot_be_claimed_by_two_members():
    text = "磐石不是彼得这个人"
    proposal = _proposal(
        claim_decisions=[
            {
                "claim_id": "C1",
                "components": [
                    _component(ROCK_STATEMENT, text, "new_viewpoint", local_new_viewpoint_key="A"),
                ],
            }
        ],
        new_viewpoint_candidates=[_candidate("A")],
    )
    claim = _claim("C1", ROCK_STATEMENT)
    key = component_key(claim, proposal.claim_decisions[0].components[0])
    assert key == component_key(claim, proposal.claim_decisions[0].components[0])

    duplicate = proposal.model_dump(mode="json")
    duplicate["claim_decisions"][0]["components"].append(
        _component(ROCK_STATEMENT, text, "member_existing", target_viewpoint_revision_id="CVR-1")
    )
    with pytest.raises(ValueError, match="same span to two components"):
        CanonicalViewpointProposalResponse.model_validate(duplicate)


def test_review_must_answer_every_component():
    proposal = _proposal()
    proposal_sha = "proposal-sha"
    partial = CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": proposal_sha,
            "change_reviews": [
                {
                    "claim_id": "C1",
                    "component_index": 0,
                    "decision": "pass",
                    "finding_codes": [],
                    "reason": "真值条件一致",
                }
            ],
            "novelty_review": {"status": "pass", "missed_claim_ids": [], "reason": "无漏项"},
        }
    )
    with pytest.raises(BatchResolutionError, match="C1#1: no review decision"):
        validate_review(review=partial, proposal=proposal, proposal_sha256=proposal_sha)


def test_modality_finding_routes_the_batch_to_reconsideration():
    # The blind POC collapsed "更可能是基督……而不是彼得个人" into a categorical
    # member; the reviewer catching that must stop the batch, not soften it.
    proposal = _proposal()
    review = CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": "proposal-sha",
            "change_reviews": [
                {
                    "claim_id": "C1",
                    "component_index": 0,
                    "decision": "correct",
                    "finding_codes": ["modality_collapsed"],
                    "reason": "该成分带「更可能」，不是绝对断言",
                    "correction": "改为 support_existing，目标 revision 不变",
                },
                {
                    "claim_id": "C1",
                    "component_index": 1,
                    "decision": "pass",
                    "finding_codes": [],
                    "reason": "论据关系成立",
                },
            ],
            "novelty_review": {"status": "pass", "missed_claim_ids": [], "reason": "无漏项"},
        }
    )
    report = validate_review(review=review, proposal=proposal, proposal_sha256="proposal-sha")
    assert report["outcome"] == "findings"
    assert report["reconsideration_required"] is True
    assert report["decision_counts"] == {"pass": 1, "correct": 1, "reject": 0, "defer": 0}


def test_passing_review_needs_no_reconsideration():
    proposal = _proposal()
    review = CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": "proposal-sha",
            "change_reviews": [
                {
                    "claim_id": "C1",
                    "component_index": index,
                    "decision": "pass",
                    "finding_codes": [],
                    "reason": "判断成立",
                }
                for index in (0, 1)
            ],
            "novelty_review": {"status": "pass", "missed_claim_ids": [], "reason": "无漏项"},
        }
    )
    report = validate_review(review=review, proposal=proposal, proposal_sha256="proposal-sha")
    assert report["outcome"] == "pass"
    assert report["reconsideration_required"] is False


def test_missed_novelty_blocks_even_when_every_change_passes():
    proposal = _proposal()
    review = CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": "proposal-sha",
            "change_reviews": [
                {
                    "claim_id": "C1",
                    "component_index": index,
                    "decision": "pass",
                    "finding_codes": [],
                    "reason": "判断成立",
                }
                for index in (0, 1)
            ],
            "novelty_review": {
                "status": "missed_novelty",
                "missed_claim_ids": ["C1"],
                "reason": "钥匙授予对象未被提为独立观点",
            },
        }
    )
    report = validate_review(review=review, proposal=proposal, proposal_sha256="proposal-sha")
    assert report["outcome"] == "findings"


def test_packet_tells_the_proposer_the_registry_is_open():
    packet = build_batch_packet(
        batch_id="CVB-test-001",
        scope_label="matt16-13-20",
        claims=[_claim("C1", ROCK_STATEMENT)],
        registry_context=[{"viewpoint_revision_id": "CVR-1", "core_proposition": "..."}],
    )
    assert "开放参考集" in packet["registry_completeness_warning"]
    assert packet["packet_sha256"]


class _StubAdapter:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.calls = 0
        self.model_id = "stub-model"
        self.backend = "stub_subscription"
        self.prompt_sha256 = "prompt-sha"
        self.generation_config_sha256 = "config-sha"

    def generate(self, payload: Any) -> dict[str, Any]:
        self.calls += 1
        return self._response


def test_run_batch_writes_artifacts_measures_time_and_resumes(tmp_path: Path):
    claims = [_claim("C1", ROCK_STATEMENT)]
    proposal_payload = _proposal().model_dump(mode="json")
    review_payload = {
        "schema_version": "wang_canonical_viewpoint_review_v1",
        "proposal_sha256": sha256_json(proposal_payload),
        "change_reviews": [
            {
                "claim_id": "C1",
                "component_index": index,
                "decision": "pass",
                "finding_codes": [],
                "reason": "判断成立",
            }
            for index in (0, 1)
        ],
        "novelty_review": {"status": "pass", "missed_claim_ids": [], "reason": "无漏项"},
    }
    proposer = _StubAdapter(proposal_payload)
    reviewer = _StubAdapter(review_payload)

    kwargs: dict[str, Any] = {
        "batch_id": "CVB-test-001",
        "scope_label": "matt16-13-20",
        "claims": claims,
        "registry_context": [{"viewpoint_revision_id": "CVR-1"}],
        "pending_candidates": [],
        "output_dir": tmp_path / "batch-001",
        "proposer": proposer,
        "reviewer": reviewer,
    }
    report = run_batch(**kwargs)

    assert report["outcome"] == "pass"
    assert report["claim_count"] == 1
    assert report["component_count"] == 2
    assert report["master_data_mutations"] == 0
    assert report["apply_allowed"] is False
    assert report["measurements"]["proposal_calls_executed"] == 1
    assert report["measurements"]["review_calls_executed"] == 1
    for name in ("batch-packet.json", "raw-proposal.json", "proposal.json", "raw-review.json", "review.json", "batch-run.json"):
        assert (tmp_path / "batch-001" / name).exists()

    # Rerunning reuses the cached calls, which is what makes a partly finished
    # scope resumable without paying for the batches already done. The semantic
    # result is byte-identical; only the execution log grows.
    again = run_batch(**kwargs)
    assert proposer.calls == 1
    assert reviewer.calls == 1
    assert again["measurements"]["proposal_calls_executed"] == 0
    assert again["artifact_sha256"] == report["artifact_sha256"]
    log = json.loads((tmp_path / "batch-001" / "measurements.json").read_text(encoding="utf-8"))
    assert len(log["executions"]) == 2


def test_run_batch_refuses_a_proposal_that_skips_a_claim(tmp_path: Path):
    proposer = _StubAdapter(_proposal().model_dump(mode="json"))
    reviewer = _StubAdapter({})
    with pytest.raises(BatchResolutionError, match="C2: Claim has no disposition"):
        run_batch(
            batch_id="CVB-test-001",
            scope_label="matt16-13-20",
            claims=[_claim("C1", ROCK_STATEMENT), _claim("C2", MODAL_STATEMENT)],
            registry_context=[{"viewpoint_revision_id": "CVR-1"}],
            pending_candidates=[],
            output_dir=tmp_path / "batch-001",
            proposer=proposer,
            reviewer=reviewer,
        )
    assert reviewer.calls == 0
    assert not (tmp_path / "batch-001" / "review.json").exists()


def test_scope_selects_core_claims_without_a_target_proposition():
    from backend.pipeline.viewpoint_scope_packet_runner import scope_claim_ids

    scope = {
        "claims": [
            {"claim_id": "C1", "lane": "core", "passage_unit_ids": ["16:13-18"]},
            {"claim_id": "C2", "lane": "core", "passage_unit_ids": ["16:19"]},
            {"claim_id": "C3", "lane": "source_context_candidate", "passage_unit_ids": ["16:13-18"]},
            {"claim_id": "C4", "lane": "core", "passage_unit_ids": ["16:13-18", "16:19"]},
        ]
    }
    # A unit takes every core Claim assigned to it — the selection is the
    # passage, not a viewpoint someone named in advance.
    assert scope_claim_ids(scope, ["16:13-18"]) == ["C1", "C4"]
    # Cross-unit Claims appear in both units they were assigned to.
    assert scope_claim_ids(scope, ["16:19"]) == ["C2", "C4"]
    # No unit filter means the whole core lane; the context lane never enters.
    assert scope_claim_ids(scope, []) == ["C1", "C2", "C4"]


def test_registry_context_carries_boundaries_not_member_sets():
    from backend.pipeline.viewpoint_scope_packet_runner import registry_context

    revision = {
        "viewpoint_revision_id": "CVR-1",
        "viewpoint_id": "CV-1",
        "revision_number": 1,
        "revision": 1,
        "core_proposition": "太16:18 的磐石不指彼得本人",
        "proposition_signature": {
            "subject": "太16:18 的磐石",
            "predicate": "指向",
            "object": "彼得本人",
            "polarity": "denied",
            "modality": "教授的释经判断",
        },
        "scope": {"scripture_scope": ["Matt.16.18"]},
        "provenance": {"basis_identity_decision_ids": ["VID-1"], "review_artifact_sha256": "sha"},
        "review_status": "system_approved",
        "approved_by": "system",
        "approved_at": "2026-08-23T00:00:00Z",
    }
    viewpoints = [
        {
            "viewpoint_id": "CV-1",
            "current_revision_id": "CVR-1",
            "identity_status": "active",
            "created_from_candidate_id": "VIC-1",
            "review_status": "system_approved",
            "revision": 1,
        },
        {
            "viewpoint_id": "CV-2",
            "current_revision_id": "CVR-2",
            "identity_status": "retired",
            "created_from_candidate_id": "VIC-2",
            "review_status": "system_approved",
            "revision": 1,
        },
    ]
    context = registry_context(viewpoints, [revision])
    assert [item["viewpoint_id"] for item in context] == ["CV-1"]
    assert context[0]["core_proposition"] == "太16:18 的磐石不指彼得本人"
    # Member sets are deliberately absent: the proposer compares propositions.
    assert "members" not in context[0]
    assert "claim_ids" not in context[0]


def test_evidence_lists_are_independent_sets_not_positional_pairs():
    # One EvidenceStep legitimately binds several SourceFragments, so the two
    # lists have different lengths. Zipping them would invent pairs the model
    # never stated and silently drop the rest.
    claim = ReviewClaim(
        claim_id="C1",
        pinned_claim_revision=1,
        claim_revision_sha256="sha-C1",
        source_id="S1",
        statement=ROCK_STATEMENT,
        review_status="approved",
        evidence=[
            {**_evidence("C1"), "evidence_step_id": "E1", "source_fragment_id": "F1"},
            {**_evidence("C1"), "evidence_step_id": "E1", "source_fragment_id": "F2"},
            {**_evidence("C1"), "evidence_step_id": "E2", "source_fragment_id": "F3"},
        ],
    )
    proposal = _proposal(
        claim_decisions=[
            {
                "claim_id": "C1",
                "components": [
                    _component(
                        ROCK_STATEMENT,
                        "磐石不是彼得这个人",
                        "new_viewpoint",
                        local_new_viewpoint_key="ROCK-NOT-PETER",
                        evidence_step_ids=["E1", "E2"],
                        source_fragment_ids=["F1", "F2", "F3"],
                    )
                ],
            }
        ]
    )
    report = validate_proposal(
        proposal=proposal,
        batch_id="CVB-test-001",
        claims=[claim],
        registry_revision_ids=["CVR-1"],
    )
    assert report["component_count"] == 1


def test_referenced_evidence_must_form_a_real_pair():
    claim = ReviewClaim(
        claim_id="C1",
        pinned_claim_revision=1,
        claim_revision_sha256="sha-C1",
        source_id="S1",
        statement=ROCK_STATEMENT,
        review_status="approved",
        evidence=[
            {**_evidence("C1"), "evidence_step_id": "E1", "source_fragment_id": "F1"},
            {**_evidence("C1"), "evidence_step_id": "E2", "source_fragment_id": "F2"},
        ],
    )
    # E1 and F2 both belong to the Claim, but never together.
    proposal = _proposal(
        claim_decisions=[
            {
                "claim_id": "C1",
                "components": [
                    _component(
                        ROCK_STATEMENT,
                        "磐石不是彼得这个人",
                        "new_viewpoint",
                        local_new_viewpoint_key="ROCK-NOT-PETER",
                        evidence_step_ids=["E1"],
                        source_fragment_ids=["F2"],
                    )
                ],
            }
        ]
    )
    with pytest.raises(BatchResolutionError, match="forms no real"):
        validate_proposal(
            proposal=proposal,
            batch_id="CVB-test-001",
            claims=[claim],
            registry_revision_ids=["CVR-1"],
        )


def test_canonicalization_reorders_without_touching_meaning():
    from backend.api.canonical_repository.viewpoint_batch_resolution import canonicalize_proposal

    # A real proposer emits these in narrative order. Rejecting that would
    # throw away a ten-minute call over presentation.
    raw = {
        "batch_id": "CVB-test-001",
        "claim_decisions": [
            {"claim_id": "C2", "components": [{"evidence_step_id": "x", "evidence_step_ids": ["E2", "E1"]}]},
            {"claim_id": "C1", "components": []},
        ],
        "new_viewpoint_candidates": [
            {"local_key": "Z", "scripture_scope": ["Matt.16.19", "Matt.16.18"]},
            {"local_key": "A"},
        ],
    }
    canonical, changes = canonicalize_proposal(raw)
    assert [item["claim_id"] for item in canonical["claim_decisions"]] == ["C1", "C2"]
    assert [item["local_key"] for item in canonical["new_viewpoint_candidates"]] == ["A", "Z"]
    assert canonical["claim_decisions"][1]["components"][0]["evidence_step_ids"] == ["E1", "E2"]
    assert canonical["new_viewpoint_candidates"][1]["scripture_scope"] == ["Matt.16.18", "Matt.16.19"]
    assert "/claim_decisions" in changes
    # Text fields are never touched.
    assert canonical["claim_decisions"][1]["components"][0]["evidence_step_id"] == "x"


def test_grouping_must_cover_every_claim_exactly_once():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        ClaimGroupingResponse,
        batches_from_groups,
        validate_grouping,
    )

    grouping = ClaimGroupingResponse.model_validate(
        {
            "scope_label": "matt16-13-18",
            "groups": [
                {"group_key": "keys_authority", "claim_ids": ["C3"], "rationale": "钥匙"},
                {"group_key": "rock_referent", "claim_ids": ["C1", "C2"], "rationale": "磐石"},
            ],
        }
    )
    report = validate_grouping(grouping=grouping, scope_label="matt16-13-18", claim_ids=["C1", "C2", "C3"])
    assert report["group_count"] == 2

    with pytest.raises(BatchResolutionError, match="C4: Claim was not assigned"):
        validate_grouping(
            grouping=grouping, scope_label="matt16-13-18", claim_ids=["C1", "C2", "C3", "C4"]
        )

    # Grouping decides composition; the size ceiling stays the program's call.
    assert batches_from_groups(grouping, batch_size=20) == [["C1", "C2"], ["C3"]]
    assert batches_from_groups(grouping, batch_size=1) == [["C1"], ["C2"], ["C3"]]


def test_a_claim_in_two_groups_is_rejected():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        ClaimGroupingResponse,
        validate_grouping,
    )

    grouping = ClaimGroupingResponse.model_validate(
        {
            "scope_label": "s",
            "groups": [
                {"group_key": "a", "claim_ids": ["C1"], "rationale": "r"},
                {"group_key": "b", "claim_ids": ["C1"], "rationale": "r"},
            ],
        }
    )
    with pytest.raises(BatchResolutionError, match="in both group"):
        validate_grouping(grouping=grouping, scope_label="s", claim_ids=["C1"])


def test_grouping_coverage_is_repaired_not_thrown_away():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        ClaimGroupingResponse,
        RESIDUAL_GROUP_KEY,
        repair_grouping,
        validate_grouping,
    )

    # A real 190-Claim grouping put one Claim in two groups. Grouping is a
    # batching plan, so one slip must not cost the whole scope's call.
    grouping = ClaimGroupingResponse.model_validate(
        {
            "scope_label": "s",
            "groups": [
                {"group_key": "a_rock", "claim_ids": ["C1", "C2"], "rationale": "r"},
                {"group_key": "b_keys", "claim_ids": ["C2", "C3", "C9"], "rationale": "r"},
            ],
        }
    )
    repaired, repairs = repair_grouping(grouping=grouping, claim_ids=["C1", "C2", "C3", "C4"])
    assignments = [claim for group in repaired.groups for claim in group.claim_ids]

    assert sorted(assignments) == ["C1", "C2", "C3", "C4"]
    assert len(assignments) == 4, "exact-once coverage after repair"
    # First group in canonical order keeps the duplicate.
    assert dict((g.group_key, g.claim_ids) for g in repaired.groups)["a_rock"] == ["C1", "C2"]
    # Unassigned Claims land in a residual batch; out-of-scope ids are dropped.
    assert dict((g.group_key, g.claim_ids) for g in repaired.groups)[RESIDUAL_GROUP_KEY] == ["C4"]
    assert any("C2" in item and "already grouped" in item for item in repairs)
    assert any("C9" in item and "not in scope" in item for item in repairs)

    # The repaired plan is what gets validated, and it passes.
    validate_grouping(grouping=repaired, scope_label="s", claim_ids=["C1", "C2", "C3", "C4"])


def _review(*decisions: str) -> CanonicalViewpointReviewResponse:
    return CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": "proposal-sha",
            "change_reviews": [
                {
                    "claim_id": "C1",
                    "component_index": index,
                    "decision": decision,
                    "finding_codes": [] if decision == "pass" else ["modality_collapsed"],
                    "reason": "理由",
                    "correction": None if decision != "correct" else "改为 support_existing",
                }
                for index, decision in enumerate(decisions)
            ],
            "novelty_review": {"status": "pass", "missed_claim_ids": [], "reason": "无漏项"},
        }
    )


def _reconsideration(revised: dict[str, Any], *dispositions: str) -> Any:
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        CanonicalViewpointReconsiderationResponse,
    )

    return CanonicalViewpointReconsiderationResponse.model_validate(
        {
            "proposal_sha256": "proposal-sha",
            "review_sha256": "review-sha",
            "finding_dispositions": [
                {
                    "claim_id": "C1",
                    "component_index": index,
                    "disposition": disposition,
                    "reason": "理由",
                }
                for index, disposition in enumerate(dispositions)
            ],
            "revised_proposal": revised,
        }
    )


def test_accepted_finding_resolves_the_batch():
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_reconsideration

    proposal = _proposal()
    revised = proposal.model_dump(mode="json")
    revised["claim_decisions"][0]["components"][0]["disposition"] = "support_existing"
    revised["claim_decisions"][0]["components"][0]["target_viewpoint_revision_id"] = "CVR-1"
    revised["claim_decisions"][0]["components"][0]["local_new_viewpoint_key"] = None
    revised["new_viewpoint_candidates"] = []

    report = validate_reconsideration(
        reconsideration=_reconsideration(revised, "accepted"),
        proposal=proposal,
        review=_review("correct", "pass"),
        proposal_sha256="proposal-sha",
        review_sha256="review-sha",
    )
    assert report["outcome"] == "resolved"
    assert report["escalations"] == []


def test_rebutted_finding_goes_to_a_human_not_another_round():
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_reconsideration

    proposal = _proposal()
    report = validate_reconsideration(
        reconsideration=_reconsideration(proposal.model_dump(mode="json"), "rebutted"),
        proposal=proposal,
        review=_review("correct", "pass"),
        proposal_sha256="proposal-sha",
        review_sha256="review-sha",
    )
    assert report["outcome"] == "exception"
    assert report["escalations"] == ["C1#0:rebutted"]


def test_reconsideration_cannot_touch_a_component_the_reviewer_passed():
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_reconsideration

    proposal = _proposal()
    revised = proposal.model_dump(mode="json")
    # Component 1 passed review; quietly rewriting it is not a revision.
    revised["claim_decisions"][0]["components"][1]["reason"] = "偷偷改掉的理由"

    with pytest.raises(BatchResolutionError, match="unflagged component changed"):
        validate_reconsideration(
            reconsideration=_reconsideration(revised, "accepted"),
            proposal=proposal,
            review=_review("correct", "pass"),
            proposal_sha256="proposal-sha",
            review_sha256="review-sha",
        )


def test_every_finding_needs_a_disposition():
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_reconsideration

    proposal = _proposal()
    with pytest.raises(BatchResolutionError, match="C1#1: finding has no disposition"):
        validate_reconsideration(
            reconsideration=_reconsideration(proposal.model_dump(mode="json"), "accepted"),
            proposal=proposal,
            review=_review("correct", "correct"),
            proposal_sha256="proposal-sha",
            review_sha256="review-sha",
        )


def test_derived_summaries_survive_a_report_shape_change(tmp_path: Path):
    # batch-run.json is wholly derived from the immutable artifacts. Freezing it
    # meant that adding reconsideration fields to the report blocked a rerun of
    # a batch whose model calls were all cached.
    claims = [_claim("C1", ROCK_STATEMENT)]
    proposal_payload = _proposal().model_dump(mode="json")
    review_payload = {
        "schema_version": "wang_canonical_viewpoint_review_v1",
        "proposal_sha256": sha256_json(proposal_payload),
        "change_reviews": [
            {
                "claim_id": "C1",
                "component_index": index,
                "decision": "pass",
                "finding_codes": [],
                "reason": "判断成立",
            }
            for index in (0, 1)
        ],
        "novelty_review": {"status": "pass", "missed_claim_ids": [], "reason": "无漏项"},
    }
    kwargs: dict[str, Any] = {
        "batch_id": "CVB-test-001",
        "scope_label": "matt16-13-18",
        "claims": claims,
        "registry_context": [{"viewpoint_revision_id": "CVR-1"}],
        "pending_candidates": [],
        "output_dir": tmp_path / "batch-001",
        "proposer": _StubAdapter(proposal_payload),
        "reviewer": _StubAdapter(review_payload),
    }
    run_batch(**kwargs)

    # Simulate the report shape changing under a completed batch.
    path = tmp_path / "batch-001" / "batch-run.json"
    stale = json.loads(path.read_text(encoding="utf-8"))
    stale.pop("escalations", None)
    stale["removed_field"] = "from an older runner"
    path.write_text(json.dumps(stale, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    again = run_batch(**kwargs)
    assert again["outcome"] == "pass"
    assert "removed_field" not in json.loads(path.read_text(encoding="utf-8"))

    # The semantic artifacts stay immutable — those are the real record.
    proposal_path = tmp_path / "batch-001" / "raw-proposal.json"
    tampered = json.loads(proposal_path.read_text(encoding="utf-8"))
    tampered["response"]["batch_id"] = "CVB-somewhere-else"
    proposal_path.write_text(json.dumps(tampered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(BatchResolutionError):
        run_batch(**kwargs)


def _route(**overrides: Any) -> dict[str, Any]:
    return {
        "local_route_key": "ROUTE-GREEK",
        "conclusion_ref": {"local_new_viewpoint_key": "ROCK-NOT-PETER"},
        "proposed_action": "create_new",
        "route_label": "以 Petrus／petra 的性别差异论证磐石不指彼得本人",
        "inference_method_codes": ["morphology"],
        "ordered_inference_nodes": [
            {
                "route_step_key": "P1",
                "role": "observation",
                "normalized_proposition": "Petrus 是阳性、petra 是阴性",
                "required_for_full_attestation": True,
            },
            {
                "route_step_key": "C1",
                "role": "conclusion",
                "conclusion_ref": {"local_new_viewpoint_key": "ROCK-NOT-PETER"},
                "required_for_full_attestation": True,
            },
        ],
        "identity_comparison": "Registry 中无同一骨架的路线",
        **overrides,
    }


def _attestation(**overrides: Any) -> dict[str, Any]:
    return {
        "local_attestation_key": "ATTEST-1",
        "route_ref": {"local_route_key": "ROUTE-GREEK"},
        "source_id": "S1",
        "claim_ids": ["C1"],
        "step_bindings": [
            {
                "route_step_key": "P1",
                "evidence_step_ids": ["C1-E1"],
                "source_fragment_ids": ["C1-F1"],
                "attestation_status": "attested",
            },
            {
                "route_step_key": "C1",
                "evidence_step_ids": ["C1-E1"],
                "source_fragment_ids": ["C1-F1"],
                "attestation_status": "attested",
            },
        ],
        "terminal_claim_component_key": "CCK-1",
        "completeness": "full",
        "reason": "本篇给了前提也讲出结论",
        **overrides,
    }


def _routes(**overrides: Any) -> Any:
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        ArgumentRouteProposalResponse,
    )

    payload: dict[str, Any] = {
        "batch_id": "CVB-test-001",
        "argument_route_candidates": [_route()],
        "source_route_attestations": [_attestation()],
    }
    payload.update(overrides)
    return ArgumentRouteProposalResponse.model_validate(payload)


def _check_routes(routes: Any, claims: list[ReviewClaim]) -> dict[str, Any]:
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        validate_route_proposal,
    )

    return validate_route_proposal(
        routes=routes,
        batch_id="CVB-test-001",
        claims=claims,
        known_revisions=["CVR-1"],
        settled_conclusion_keys=["ROCK-NOT-PETER"],
    )


def test_routes_validate_against_settled_conclusions():
    report = _check_routes(_routes(), [_claim("C1", ROCK_STATEMENT)])
    assert report["route_count"] == 1
    assert report["full_count"] == 1
    assert report["inference_method_codes"] == ["morphology"]


def test_an_attestation_may_not_span_two_sermons():
    # The one error this layer exists to make impossible: a premise from one
    # sermon and a conclusion from another is an argument nobody delivered.
    routes = _routes(source_route_attestations=[_attestation(claim_ids=["C1", "C2"])])
    with pytest.raises(BatchResolutionError, match="an attestation is one source only"):
        _check_routes(
            routes,
            [
                _claim("C1", ROCK_STATEMENT),
                ReviewClaim(
                    claim_id="C2",
                    pinned_claim_revision=1,
                    claim_revision_sha256="sha-C2",
                    source_id="S2",
                    statement=MODAL_STATEMENT,
                    review_status="approved",
                    evidence=[{**_evidence("C2"), "source_id": "S2"}],
                ),
            ],
        )


def test_borrowed_evidence_is_caught_even_within_one_batch():
    routes = _routes(
        source_route_attestations=[
            _attestation(
                step_bindings=[
                    {
                        "route_step_key": "P1",
                        "evidence_step_ids": ["C9-E1"],
                        "source_fragment_ids": ["C1-F1"],
                        "attestation_status": "attested",
                    }
                ],
                completeness="partial",
                terminal_claim_component_key=None,
            )
        ]
    )
    with pytest.raises(BatchResolutionError, match="EvidenceStep C9-E1 is outside this source"):
        _check_routes(routes, [_claim("C1", ROCK_STATEMENT)])


def test_full_requires_every_required_node_attested():
    routes = _routes(
        source_route_attestations=[
            _attestation(
                step_bindings=[
                    {
                        "route_step_key": "P1",
                        "evidence_step_ids": ["C1-E1"],
                        "source_fragment_ids": ["C1-F1"],
                        "attestation_status": "missing",
                    },
                    {
                        "route_step_key": "C1",
                        "evidence_step_ids": ["C1-E1"],
                        "source_fragment_ids": ["C1-F1"],
                        "attestation_status": "attested",
                    },
                ]
            )
        ]
    )
    with pytest.raises(BatchResolutionError, match="required step P1"):
        _check_routes(routes, [_claim("C1", ROCK_STATEMENT)])


def test_node_roles_and_method_codes_come_from_the_policy_vocabulary():
    from backend.api.canonical_repository.viewpoint_batch_resolution import ArgumentRouteCandidate

    # A free-text slug is exactly what route identity must not turn on.
    with pytest.raises(ValueError, match="not a policy inference method code"):
        ArgumentRouteCandidate.model_validate(_route(inference_method_codes=["greek_morphology"]))
    with pytest.raises(ValueError):
        ArgumentRouteCandidate.model_validate(
            _route(
                ordered_inference_nodes=[
                    {
                        "route_step_key": "P1",
                        "role": "希臘文詞形論證",
                        "normalized_proposition": "x",
                        "required_for_full_attestation": True,
                    },
                    {
                        "route_step_key": "C1",
                        "role": "conclusion",
                        "conclusion_ref": {"local_new_viewpoint_key": "ROCK-NOT-PETER"},
                        "required_for_full_attestation": True,
                    },
                ]
            )
        )
    with pytest.raises(ValueError, match="other requires a reviewable note"):
        ArgumentRouteCandidate.model_validate(_route(inference_method_codes=["other"]))


def test_a_route_nobody_preached_is_rejected():
    with pytest.raises(BatchResolutionError, match="proposed with no source attestation"):
        _check_routes(_routes(source_route_attestations=[]), [_claim("C1", ROCK_STATEMENT)])


def test_cross_source_composition_can_never_be_a_pass():
    from backend.api.canonical_repository.viewpoint_batch_resolution import RouteReview

    with pytest.raises(ValueError, match="never a pass"):
        RouteReview.model_validate(
            {
                "status": "pass",
                "cross_source_composition_found": True,
                "reason": "发现跨来源拼接",
            }
        )


def test_route_packet_carries_settled_conclusions_and_drops_non_bearing():
    from backend.pipeline.viewpoint_batch_resolution_runner import build_route_packet

    proposal = _proposal(
        claim_decisions=[
            {
                "claim_id": "C1",
                "components": [
                    _component(
                        ROCK_STATEMENT,
                        "磐石不是彼得这个人",
                        "new_viewpoint",
                        local_new_viewpoint_key="ROCK-NOT-PETER",
                    ),
                    _component(ROCK_STATEMENT, "而是彼得所承认的信仰", "no_registry_assertion"),
                ],
            }
        ]
    )
    packet = build_route_packet(
        batch_id="CVB-test-001",
        proposal=proposal,
        claims=[_claim("C1", ROCK_STATEMENT)],
        registry_context=[{"viewpoint_revision_id": "CVR-1", "core_proposition": "太16:18 的磐石不指彼得本人"}],
    )
    # The conclusion arrives with its wording; the route pass is not asked to
    # re-decide identity, only how the professor reached it.
    assert packet["settled_conclusions"] == [
        {"conclusion_key": "ROCK-NOT-PETER", "core_proposition": "太16:18 的磐石不指彼得本人"}
    ]
    # A component that asserts nothing has no conclusion to argue toward.
    assert [item["disposition"] for item in packet["route_bearing_components"]] == ["new_viewpoint"]
    assert "single_source_note" in packet


def test_a_route_may_not_target_a_conclusion_the_batch_never_settled():
    routes = _routes(
        argument_route_candidates=[
            _route(conclusion_ref={"local_new_viewpoint_key": "NEVER-PROPOSED"})
        ],
        source_route_attestations=[],
    )
    with pytest.raises(BatchResolutionError, match="NEVER-PROPOSED"):
        _check_routes(routes, [_claim("C1", ROCK_STATEMENT)])


def test_review_must_decide_every_route_and_attestation():
    routes = _routes()
    review = CanonicalViewpointReviewResponse.model_validate(
        {
            "proposal_sha256": "proposal-sha",
            "change_reviews": [
                {
                    "claim_id": "C1",
                    "component_index": index,
                    "decision": "pass",
                    "finding_codes": [],
                    "reason": "判断成立",
                }
                for index in (0, 1)
            ],
            "novelty_review": {"status": "pass", "missed_claim_ids": [], "reason": "无漏项"},
            "route_review": {
                "status": "pass",
                "cross_source_composition_found": False,
                "reason": "无跨来源拼接",
            },
        }
    )
    with pytest.raises(BatchResolutionError, match="ROUTE-GREEK: no review decision"):
        validate_review(
            review=review,
            proposal=_proposal(),
            proposal_sha256="proposal-sha",
            routes=routes,
        )
