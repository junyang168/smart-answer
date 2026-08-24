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
from backend.pipeline.viewpoint_batch_resolution_runner import (
    pending_synopsis,
    run_batch,
)

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


def test_pending_synopsis_is_blocker_context_not_membership():
    pending = pending_synopsis(_proposal(), "CVB-test-001")
    assert [item["usage"] for item in pending] == ["blocker_context_only"]
    assert [item["status"] for item in pending] == ["pending_not_applied"]


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
