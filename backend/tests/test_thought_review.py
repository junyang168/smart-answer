from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.api import thought_review


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _fixture_paths(tmp_path: Path, monkeypatch) -> None:
    # Fixture JSON is the authority in these isolated unit tests.  A developer's
    # local .env may point at a populated PostgreSQL authoring store, which must
    # never leak into deterministic fixture assertions.
    monkeypatch.delenv("KNOWLEDGE_DATABASE_URL", raising=False)
    claims = {
        "source_projects": [{"project_id": "p1", "transcript_id": "讲道 3"}],
        "claims": [{
            "claim_id": "CL-1", "group_id": "CG-1", "title": "主张一", "claim_type": "解经判断",
            "scripture_refs": ["太17:1"], "lectures": ["第3讲"], "recurrence": 2,
            "review_status": "candidate", "opposes": "另一种解释",
            "occurrences": [{"project_id": "p1", "lecture": "第3讲", "transcript_id": "讲道 3"}],
        }],
    }
    graph = {
        "evidence_nodes": [
            {"id": "E1", "topic": "CG-1", "lec": "第3讲", "lane": 0, "ty": "问题", "full": "问题", "q": "原话", "scr": [], "qt": 65},
            {"id": "E2", "topic": "CG-1", "lec": "第3讲", "lane": 3, "ty": "结论", "full": "答案", "q": "答案原话", "scr": ["太17:1"], "qt": 70},
        ],
        "relations": [{"source_evidence_id": "E1", "target_evidence_id": "E2", "relation_type": "answers"}],
        "argument_lanes": [],
    }
    composition = {
        "plan_id": "CP-1", "title": "计划", "description": "说明",
        "decisions": [{"decision_id": "CD-1", "passage": "太17:1", "section_title": "第一段", "action": "main_section", "decision": "详讲", "rationale": "是核心", "claim_ids": ["CL-1"], "claim_hierarchy": {"paragraph_thesis": "CL-1"}, "coverage": "available", "review_status": "candidate"}],
    }
    shared = {
        "package_id": "SKP-1",
        "title": "两讲试验",
        "corpus_scope": {"description": "仅两讲", "warning": "不代表全库", "source_count": 1},
        "summary": {"counts": {"sources": 1, "fragments": 2, "claims": 1, "relations": 1, "questions": 1}},
        "questions": [{"question_id": "OQ-1", "question": "尚未回答？", "answer_state": "unanswered"}],
        "claims": claims["claims"],
        "evidence_steps": [
            {"evidence_step_id": "E1", "claim_group_ids": ["CG-1"], "support_eligibility": "contextual_only", "speaker": "audience", "stance": "questioned", "discourse_role": "audience_prompt", "anchor_quality": "verified_context"},
            {"evidence_step_id": "E2", "claim_group_ids": ["CG-1"], "support_eligibility": "eligible", "speaker": "professor", "stance": "endorsed", "discourse_role": "own_reasoning", "anchor_quality": "verified_candidate"},
        ],
        "position_nodes": [],
        "claim_relations": [],
        "knowledge_routes": [{"route_id": "ROUTE-CL-1", "claim_id": "CL-1", "route_type": "scripture_exposition", "target_id": "CP-1"}],
        "cross_source_syntheses": [{
            "synthesis_id": "SYN-1", "synthesis_type": "topic_retrieval_lead", "title": "检索线索",
            "description": "不是最终专题", "claim_ids": ["CL-1"], "review_status": "candidate",
        }],
        "validation_experiments": [{"experiment_id": "VAL-1", "title": "问答验证"}],
        "product_plans": [
            composition,
            {
                "plan_id": "CP-TOPIC-1",
                "title": "人子专题计划",
                "axis": "topic",
                "product_type": "topic_research",
                "source_leads": [{
                    "source_lead_id": "SL-1",
                    "transcript_id": "011WSR01",
                    "title": "那人子称号",
                    "evidence_maturity": "survey_claims_with_timecoded_anchors",
                }],
                "decisions": [{
                    "decision_id": "CD-TOPIC-1",
                    "section_title": "称号与原文",
                    "action": "topic_main_section",
                    "decision": "先处理称号限定",
                    "claim_ids": ["CL-1"],
                    "source_lead_ids": ["SL-1"],
                    "review_status": "candidate",
                }],
            },
        ],
    }
    qa_validation = {
        "schema_version": "qa-validation.v1",
        "plan_id": "QA-PILOT-1",
        "title": "獨立問答驗證",
        "description": "問答是獨立產品，但只引用共享知識。",
        "corpus_scope": "測試語料",
        "cases": [
            {
                "case_id": "QA-ANSWERED",
                "case_type": "direct_answer",
                "question": "教授如何回答這個問題？",
                "answer_state": "answered",
                "answer_summary": "教授以主張一回答。",
                "full_answer_sections": [
                    {
                        "heading": "完整說明",
                        "section_type": "answer",
                        "paragraphs": ["這是根據主張一整理的完整說明。"],
                        "claim_ids": ["CL-1"],
                    }
                ],
                "answer_claim_ids": ["CL-1"],
                "context_claim_ids": [],
                "source_question_ids": ["OQ-1"],
                "opposed_position_ids": [],
                "related_product_plan_ids": ["CP-1"],
                "limitation": "只代表測試語料。",
            },
            {
                "case_id": "QA-UNANSWERED",
                "case_type": "unanswered_boundary",
                "question": "教授沒有回答什麼？",
                "answer_state": "unanswered",
                "answer_summary": "現有語料沒有完整回答。",
                "answer_claim_ids": [],
                "context_claim_ids": ["CL-1"],
                "source_question_ids": ["OQ-1"],
                "opposed_position_ids": [],
                "related_product_plan_ids": [],
                "limitation": "不可用背景主張代答。",
            },
        ],
    }
    paths = {
        "CLAIMS_PATH": tmp_path / "claims.json",
        "GRAPH_PATH": tmp_path / "graph.json",
        "COMPOSITION_PATH": tmp_path / "composition.json",
        "SHARED_KNOWLEDGE_PATH": tmp_path / "shared.json",
        "QA_VALIDATION_PATH": tmp_path / "qa-validation.json",
        "QA_DIAGNOSTICS_PATH": tmp_path / "qa-diagnostics.json",
        "REVIEW_STATE_PATH": tmp_path / "review-state.json",
        # Left unwritten on purpose: the default workspace has no AI review yet.
        "AI_REVIEW_PATH": tmp_path / "independent-review.json",
        "AI_ADJUDICATION_PATH": tmp_path / "adjudication.json",
        "DETAILED_EXTRACTION_DIR": tmp_path / "detailed-extractions",
    }
    _write(paths["CLAIMS_PATH"], claims)
    _write(paths["GRAPH_PATH"], graph)
    _write(paths["COMPOSITION_PATH"], composition)
    _write(paths["SHARED_KNOWLEDGE_PATH"], shared)
    _write(paths["QA_VALIDATION_PATH"], qa_validation)
    for name, path in paths.items():
        monkeypatch.setattr(thought_review, name, path)
    paths["DETAILED_EXTRACTION_DIR"].mkdir()


def _write_ai_review(reviews: list[dict], results: list[dict] | None = None) -> None:
    _write(
        thought_review.AI_REVIEW_PATH,
        {
            "spot_check_percent": 10,
            "reviewer": {"generated_at": "2026-08-10T00:00:00+00:00"},
            "claim_reviews": reviews,
        },
    )
    if results is not None:
        _write(thought_review.AI_ADJUDICATION_PATH, {"results": results})


def _claim_review(claim_id: str, **overrides) -> dict:
    return {
        "claim_id": claim_id,
        "decision": "pass",
        "issues": [],
        "rationale": "",
        "confidence": "high",
        "human_review_reason": "",
        "routing_status": "ai_reviewed",
        "spot_check_selected": False,
        **overrides,
    }


def test_workspace_and_claim_detail(tmp_path: Path, monkeypatch) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    workspace = thought_review.workspace_data()
    assert workspace["claim_counts"]["candidate"] == 1
    assert workspace["composition"]["counts"]["candidate"] == 2
    assert workspace["pilot"]["counts"]["fragments"] == 2
    assert workspace["synthesis"]["counts"]["candidate"] == 1
    assert workspace["qa"]["counts"] == {
        "answered": 1,
        "partially_answered": 0,
        "unanswered": 1,
    }

    detail = thought_review.claim_detail_data("CL-1")
    assert [item["id"] for item in detail["evidence"]] == ["E2"]
    assert [item["id"] for item in detail["context_evidence"]] == ["E1"]
    assert detail["relations"][0]["relation_type"] == "answers"
    assert detail["context_evidence"][0]["source_url"].endswith("%E8%AE%B2%E9%81%93%203?t=65")


def test_database_only_claim_gets_legacy_ui_defaults() -> None:
    rows = thought_review._merge_rows(
        [],
        [{"claim_id": "CL-DB", "statement": "数据库新主张"}],
        "claim_id",
    )
    assert rows == [
        {
            "claim_id": "CL-DB",
            "statement": "数据库新主张",
            "title": "数据库新主张",
            "scripture_refs": [],
            "lectures": [],
            "recurrence": 1,
            "opposes": None,
        }
    ]


def test_postgres_workspace_does_not_require_pilot_json(tmp_path: Path, monkeypatch) -> None:
    """Production authoring must not depend on the historical pilot artifact."""

    class FakeStore:
        def compile_package(self, package_id=None):
            assert package_id is None
            return {
                "package_id": "DB-PACKAGE",
                "summary": {"counts": {"claims": 1}},
                "claims": [{"claim_id": "CL-DB", "statement": "資料庫主張"}],
            }

    missing = tmp_path / "missing-pilot.json"
    monkeypatch.setattr(thought_review, "SHARED_KNOWLEDGE_PATH", missing)
    monkeypatch.setattr(thought_review, "_postgres_store", lambda: FakeStore())

    payload = thought_review._shared_payload()

    assert payload["authoring_authority"] == "postgresql"
    assert payload["claims"][0]["claim_id"] == "CL-DB"
    assert payload["summary"]["counts"]["claims"] == 1


def test_postgres_workspace_is_not_filtered_to_pilot_package(
    tmp_path: Path, monkeypatch
) -> None:
    """The optional pilot must not restrict the production authoring corpus."""

    class FakeStore:
        def compile_package(self, package_id=None):
            assert package_id is None
            return {
                "package_id": "FULL-DATABASE",
                "summary": {"counts": {"claims": 2}},
                "claims": [
                    {"claim_id": "CL-PILOT", "statement": "試驗主張"},
                    {"claim_id": "CL-NEW", "statement": "後來匯入的主張"},
                ],
            }

    pilot = tmp_path / "shared.json"
    _write(
        pilot,
        {
            "package_id": "PILOT-ONLY",
            "claims": [{"claim_id": "CL-PILOT", "title": "舊顯示標題"}],
        },
    )
    monkeypatch.setattr(thought_review, "SHARED_KNOWLEDGE_PATH", pilot)
    monkeypatch.setattr(thought_review, "_postgres_store", lambda: FakeStore())

    payload = thought_review._shared_payload()

    assert [item["claim_id"] for item in payload["claims"]] == ["CL-PILOT", "CL-NEW"]
    assert payload["claims"][0]["title"] == "舊顯示標題"


def test_candidate_evidence_is_visible_but_does_not_unlock_approval(
    tmp_path: Path, monkeypatch
) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    shared = json.loads(thought_review.SHARED_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    shared["evidence_steps"][1]["support_eligibility"] = "eligible_candidate"
    shared["claims"][0]["evidence_step_ids"] = ["E2"]
    _write(thought_review.SHARED_KNOWLEDGE_PATH, shared)

    detail = thought_review.claim_detail_data("CL-1")
    assert detail["evidence"] == []
    assert [item["id"] for item in detail["candidate_evidence"]] == ["E2"]
    assert detail["review_gate"]["can_approve"] is False


def test_candidates_project_scripture_and_topic_plans_with_readable_routes(
    tmp_path: Path, monkeypatch
) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    payload = thought_review.candidates_data()
    assert [item["candidate_id"] for item in payload["scripture_candidates"]] == ["CP-1"]
    assert [item["candidate_id"] for item in payload["topic_candidates"]] == ["CP-TOPIC-1"]
    assert payload["scripture_candidates"][0]["decision_count"] == 1

    detail = thought_review.claim_detail_data("CL-1")
    route = detail["knowledge_routes"][0]
    assert route["route_type_label"] == "釋經候選"
    assert route["target_label"] == "计划"
    assert route["candidate_href"].startswith(
        "/admin/thought-review/candidates?axis=scripture"
    )


def test_candidates_use_postgres_decision_text_instead_of_internal_id(
    tmp_path: Path, monkeypatch
) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    shared = json.loads(thought_review.SHARED_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    topic_plan = next(item for item in shared["product_plans"] if item["plan_id"] == "CP-TOPIC-1")
    topic_plan["decisions"] = [{
        "decision_id": "CD-INTERNAL-01",
        "decision": "先說明『人子』稱號的原文限定",
        "decision_type": "topic_main_section",
        "claim_ids": ["CL-1"],
        "review_status": "candidate",
    }]
    _write(thought_review.SHARED_KNOWLEDGE_PATH, shared)

    payload = thought_review.candidates_data()
    decision = payload["topic_candidates"][0]["decisions"][0]
    assert decision["title"] == "先說明『人子』稱號的原文限定"
    assert decision["title"] != decision["decision_id"]


def test_qa_projection_keeps_answers_separate_from_context(tmp_path: Path, monkeypatch) -> None:
    _fixture_paths(tmp_path, monkeypatch)

    answered = thought_review.qa_case_detail_data("QA-ANSWERED")
    assert answered["case"]["answer_state"] == "answered"
    assert answered["case"]["full_answer_sections"][0]["heading"] == "完整說明"
    assert answered["answer_claims"][0]["claim_id"] == "CL-1"
    assert answered["answer_claims"][0]["eligible_evidence_count"] == 1
    assert answered["related_products"][0]["plan_id"] == "CP-1"

    unanswered = thought_review.qa_case_detail_data("QA-UNANSWERED")
    assert unanswered["answer_claims"] == []
    assert unanswered["context_claims"][0]["claim_id"] == "CL-1"
    assert unanswered["case"]["answer_summary"] == "現有語料沒有完整回答。"


def _write_qa_diagnostics(tmp_path: Path, *, qa_hash: str | None = None, knowledge_hash: str | None = None) -> None:
    import hashlib

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    _write(
        thought_review.QA_DIAGNOSTICS_PATH,
        {
            "source": {
                "qa_sha256": qa_hash or digest(thought_review.QA_VALIDATION_PATH),
                "knowledge_sha256": knowledge_hash or digest(thought_review.SHARED_KNOWLEDGE_PATH),
            },
            "summary": {"cases": 2, "issues": 1, "human_required": 1},
            "models": {"independent_reviewer": "claude-sonnet-5"},
            "claude_review": {"case_reviews": [{"case_id": "QA-ANSWERED", "issues": [{"issue_id": "QA-ANSWERED::ISS-1", "answer_excerpt": "有問題的一句"}]}]},
            "outcomes": [
                {
                    "case_id": "QA-ANSWERED",
                    "issue_id": "QA-ANSWERED::ISS-1",
                    "status": "human_diagnostic_required",
                    "earliest_error_layer": "knowledge_data",
                }
            ],
            "repair_queue": [],
        },
    )


def test_qa_case_needing_a_person_is_flagged_in_the_list(tmp_path: Path, monkeypatch) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    _write_qa_diagnostics(tmp_path)

    cases = {item["case_id"]: item for item in thought_review.workspace_data()["qa"]["cases"]}
    assert cases["QA-ANSWERED"]["human_required"] is True
    assert cases["QA-UNANSWERED"]["human_required"] is False


def test_qa_diagnostics_go_stale_when_either_input_moves(tmp_path: Path, monkeypatch) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    _write_qa_diagnostics(tmp_path)
    assert thought_review.workspace_data()["qa"]["diagnostics"]["stale"] is False

    # The verdict was about one specific answer text, not only one knowledge package.
    _write_qa_diagnostics(tmp_path, qa_hash="0" * 64)
    workspace = thought_review.workspace_data()
    assert workspace["qa"]["diagnostics"]["stale"] is True
    assert thought_review.qa_case_detail_data("QA-ANSWERED")["diagnostics"]["stale"] is True
    # A stale verdict must not keep a case parked in the human queue.
    assert all(not item["human_required"] for item in workspace["qa"]["cases"])

    _write_qa_diagnostics(tmp_path, knowledge_hash="0" * 64)
    assert thought_review.workspace_data()["qa"]["diagnostics"]["stale"] is True


def test_qa_case_reports_missing_opposed_position(tmp_path: Path, monkeypatch) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    plan = json.loads(thought_review.QA_VALIDATION_PATH.read_text(encoding="utf-8"))
    plan["cases"][0]["opposed_position_ids"] = ["POS-MISSING"]
    plan["cases"][0]["context_claim_ids"] = ["CL-MISSING"]
    _write(thought_review.QA_VALIDATION_PATH, plan)

    warnings = thought_review.qa_case_detail_data("QA-ANSWERED")["quality_warnings"]
    assert any("反方立場" in item for item in warnings)
    assert any("背景主張" in item for item in warnings)


def test_qa_full_answer_cannot_cite_a_claim_outside_the_case(
    tmp_path: Path, monkeypatch
) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    plan = json.loads(thought_review.QA_VALIDATION_PATH.read_text(encoding="utf-8"))
    plan["cases"][0]["full_answer_sections"][0]["claim_ids"] = ["CL-OUTSIDE"]
    _write(thought_review.QA_VALIDATION_PATH, plan)

    warnings = thought_review.qa_case_detail_data("QA-ANSWERED")["quality_warnings"]
    assert any("未授權的主張" in item and "CL-OUTSIDE" in item for item in warnings)


def test_review_is_saved_separately_from_claim_data(tmp_path: Path, monkeypatch) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    original = thought_review.CLAIMS_PATH.read_text(encoding="utf-8")
    saved = thought_review.update_review(
        "claims", "CL-1", thought_review.ReviewUpdate(status="approved", note="可以代表教授", reviewer="王同工")
    )
    assert saved["status"] == "approved"
    assert thought_review.CLAIMS_PATH.read_text(encoding="utf-8") == original
    assert thought_review.workspace_data()["claim_counts"]["approved"] == 1


def test_composition_detail_links_supporting_claims(tmp_path: Path, monkeypatch) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    detail = thought_review.composition_detail_data("CD-1")
    assert detail["decision"]["decision"] == "详讲"
    assert detail["linked_claims"][0]["title"] == "主张一"
    assert detail["claim_hierarchy"]["paragraph_thesis"] == "CL-1"


def test_workspace_and_detail_expose_both_composition_axes(tmp_path: Path, monkeypatch) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    workspace = thought_review.workspace_data()
    plans = {plan["plan_id"]: plan for plan in workspace["composition"]["plans"]}
    assert plans["CP-1"]["axis"] == "scripture"
    assert plans["CP-TOPIC-1"]["axis"] == "topic"

    detail = thought_review.composition_detail_data("CD-TOPIC-1")
    assert detail["plan"]["plan_id"] == "CP-TOPIC-1"
    assert detail["plan"]["axis"] == "topic"
    assert detail["source_leads"][0]["transcript_id"] == "011WSR01"


def test_claim_without_eligible_evidence_cannot_be_approved(tmp_path: Path, monkeypatch) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    shared = json.loads(thought_review.SHARED_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    for evidence in shared["evidence_steps"]:
        evidence["support_eligibility"] = "withheld_missing_anchor"
    _write(thought_review.SHARED_KNOWLEDGE_PATH, shared)

    _write_ai_review([_claim_review("CL-1")])

    detail = thought_review.claim_detail_data("CL-1")
    assert detail["review_gate"]["can_approve"] is False
    # An AI pass never overrides "there is nothing left to cite", but source
    # repair remains an automation queue rather than an automatic human task.
    assert detail["attention"] == "pending_evidence_review"
    assert thought_review.workspace_data()["ai_review"]["counts"]["human_required"] == 0
    with pytest.raises(HTTPException) as error:
        thought_review.review_claim("CL-1", thought_review.ReviewUpdate(status="approved"))
    assert error.value.status_code == 409


def test_claim_without_ai_review_enters_ai_queue_not_human_queue(tmp_path: Path, monkeypatch) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    workspace = thought_review.workspace_data()
    assert workspace["ai_review"]["available"] is False
    assert workspace["claims"][0]["attention"] == "pending_ai_review"


def test_ai_cleared_claim_leaves_the_human_queue(tmp_path: Path, monkeypatch) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    _write_ai_review([_claim_review("CL-1")])

    workspace = thought_review.workspace_data()
    assert workspace["ai_review"]["counts"]["ai_cleared"] == 1
    assert workspace["ai_review"]["counts"]["human_required"] == 0
    claim = workspace["claims"][0]
    assert claim["attention"] == "ai_cleared"
    # The claim is still "candidate" as knowledge; it just is not human work.
    assert claim["review"]["status"] == "candidate"
    assert thought_review.claim_detail_data("CL-1")["ai_review"]["decision"] == "pass"


def test_sampled_pass_and_unsettled_disagreement_are_the_human_queue(
    tmp_path: Path, monkeypatch
) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    _write_ai_review(
        [
            _claim_review("CL-1", spot_check_selected=True, routing_status="human_spot_check"),
        ]
    )
    assert thought_review.workspace_data()["claims"][0]["attention"] == "human_spot_check"

    _write_ai_review(
        [
            _claim_review(
                "CL-1",
                decision="changes_suggested",
                issues=[{"issue_type": "speaker_attribution", "severity": "high", "explanation": "疑似聽眾發言"}],
                routing_status="awaiting_openai_adjudication",
            )
        ],
        results=[],
    )
    claim = thought_review.workspace_data()["claims"][0]
    assert claim["attention"] == "pending_ai"

    _write_ai_review(
        [
            _claim_review(
                "CL-1",
                decision="changes_suggested",
                routing_status="awaiting_openai_adjudication",
            )
        ],
        results=[
            {
                "claim_id": "CL-1",
                "status": "human_disagreement_required",
                "openai": {"decision": "reject", "rationale": "來源不支持"},
                "claude_reconsideration": {"decision": "maintain", "rationale": "原話仍支持"},
            }
        ],
    )
    detail = thought_review.claim_detail_data("CL-1")
    assert detail["attention"] == "human_required"
    assert detail["ai_review"]["adjudication"]["reconsideration_decision"] == "maintain"


def test_human_review_required_is_not_auto_applied_away(tmp_path: Path, monkeypatch) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    _write_ai_review(
        [
            _claim_review(
                "CL-1",
                decision="human_review_required",
                human_review_reason="無法從來源判斷這是教授還是反方立場",
                routing_status="awaiting_openai_adjudication",
            )
        ],
        results=[
            {
                "claim_id": "CL-1",
                "status": "human_confirmation_required",
                "claude_decision": "human_review_required",
                "human_review_reason": "無法從來源判斷這是教授還是反方立場",
                "openai": {"decision": "accept", "rationale": "同意", "patch": {"structural_notes": []}},
                "claude_reconsideration": None,
            }
        ],
    )
    claim = thought_review.workspace_data()["claims"][0]
    assert claim["attention"] == "human_required"
    assert "反方立場" in claim["attention_reason"]


def test_recorded_human_decision_clears_the_queue(tmp_path: Path, monkeypatch) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    _write_ai_review([_claim_review("CL-1", spot_check_selected=True)])
    thought_review.update_review(
        "claims", "CL-1", thought_review.ReviewUpdate(status="approved", reviewer="王同工")
    )
    counts = thought_review.workspace_data()["ai_review"]["counts"]
    assert counts == {
        "human_required": 0,
        "human_spot_check": 0,
        "pending_ai_review": 0,
        "pending_evidence_review": 0,
        "pending_ai": 0,
        "ai_cleared": 0,
        "resolved": 1,
    }


def test_consensus_excluded_anchor_is_not_offered_as_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    shared = json.loads(thought_review.SHARED_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    shared["claims"][0]["evidence_step_ids"] = ["E1", "E2", "AI-ADJ-CL-1-1"]
    shared["claims"][0]["ai_adjudication"] = {
        "status": "ai_consensus_applied",
        "approval_status": "not_human_approved",
        "excluded_evidence_step_ids": ["E2"],
    }
    shared["source_fragments"] = [
        {
            "fragment_id": "FR-AI-ADJ-CL-1-1",
            "lecture": "第3讲",
            "verbatim_excerpt": "補入的教授原话",
            "media_time": None,
            "source_url": "/resources/sermons/%E8%AE%B2%E9%81%93%203",
            "anchor_origin": "ai_consensus_adjudication",
        }
    ]
    shared["evidence_steps"].append(
        {
            "evidence_step_id": "AI-ADJ-CL-1-1",
            "source_fragment_id": "FR-AI-ADJ-CL-1-1",
            "claim_group_ids": ["CG-1"],
            "function": "推理",
            "statement": "補入的教授原话",
            "scripture_refs": [],
            "support_eligibility": "eligible",
            "speaker": "professor",
            "review_note": "OpenAI/Claude fidelity consensus source repair.",
        }
    )
    _write(thought_review.SHARED_KNOWLEDGE_PATH, shared)

    detail = thought_review.claim_detail_data("CL-1")
    eligible = [item["id"] for item in detail["evidence"]]
    withheld = {item["id"]: item for item in detail["withheld_evidence"]}
    assert "E2" not in eligible, "被兩模型排除的錨點不得再算作合格證據"
    assert "AI-ADJ-CL-1-1" in eligible, "補入的來源必須讓審核者看得到"
    assert withheld["E2"]["support_eligibility"] == "withheld_ai_consensus"
    assert detail["evidence"][0]["review_note"].startswith("兩個模型")
    assert detail["review_gate"]["eligible_evidence_count"] == 1


def test_synthesis_detail_and_review(tmp_path: Path, monkeypatch) -> None:
    _fixture_paths(tmp_path, monkeypatch)
    detail = thought_review.synthesis_detail_data("SYN-1")
    assert detail["linked_claims"][0]["claim_id"] == "CL-1"
    thought_review.update_review(
        "syntheses", "SYN-1", thought_review.ReviewUpdate(status="approved", reviewer="同工")
    )
    assert thought_review.workspace_data()["synthesis"]["counts"]["approved"] == 1
