import json
from copy import deepcopy
from pathlib import Path

import pytest

from backend.pipeline.matthew_exposition_authoring import (
    AuthoringContractError,
    deterministic_writing_warnings,
    evaluate_editorial_review,
    generation_fingerprint,
    validate_author_result,
    validate_base_contract,
    validate_strict_schema,
    AUTHOR_RESULT_SCHEMA,
)
from backend.pipeline.matthew_exposition_authoring_runner import run_authoring


FIXTURE_DIR = Path(__file__).parent / "fixtures/matthew_exposition/matt16-18-v1"
PROFILE_PATH = (
    Path(__file__).parents[1]
    / "config/editorial_quality_profiles/WQ-matthew-exposition-v1.json"
)
ROOT = Path(__file__).parents[2]
PLAN_PATH = ROOT / "output/claim-layer/matthew-16-13-20-sources/composition-reviews-with-relations/CP-matthew-16-13-20.reviewed-candidate.json"
KNOWLEDGE_PATH = ROOT / "output/claim-layer/matthew-16-13-20-sources/shared-knowledge-projection.json"
PUBLICATION_PROFILE_PATH = ROOT / "backend/config/publication_profiles/PP-matthew-expository-teaching-v1.json"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def contract():
    return load_json(FIXTURE_DIR / "base-manuscript-contract.json")


def mini_plan():
    return {
        "decisions": [
            {"decision_id": "CD-M16-002-04"},
            {"decision_id": "CD-M16-002-05"},
        ]
    }


def valid_author_result():
    manuscript = (FIXTURE_DIR / "good-reference.md").read_text(encoding="utf-8")
    return {
        "status": "drafted",
        "manuscript_markdown": manuscript,
        "plan_change_requests": [],
        "sections": [
            {
                "section_id": "matt16-18-rock",
                "decision_ids": ["CD-M16-002-04", "CD-M16-002-05"],
                "base_step_ids_preserved": [
                    "M16-18-S01",
                    "M16-18-S02",
                    "M16-18-S03",
                    "M16-18-S04",
                ],
                "claim_ids_used": [],
                "integration_operations": ["tension"],
                "omissions": [],
                "output_anchor": "耶穌說：「你是彼得",
            }
        ],
    }


def test_base_contract_rejects_stale_source_hash():
    value = contract()
    value["base_source"]["sha256"] = "0" * 64
    with pytest.raises(AuthoringContractError, match="stale base source"):
        validate_base_contract(value)


def test_author_ledger_allows_many_decisions_in_one_reader_section():
    validate_author_result(valid_author_result(), contract=contract(), plan=mini_plan())


def test_author_ledger_rejects_unaccounted_base_step():
    result = valid_author_result()
    result["sections"][0]["base_step_ids_preserved"].pop()
    with pytest.raises(AuthoringContractError, match="unaccounted base steps"):
        validate_author_result(result, contract=contract(), plan=mini_plan())


def test_author_ledger_rejects_omission_of_required_base_step():
    result = valid_author_result()
    result["sections"][0]["base_step_ids_preserved"].pop()
    result["sections"][0]["omissions"] = [
        {"step_id": "M16-18-S04", "reason": "compressed for length"}
    ]
    with pytest.raises(AuthoringContractError, match="required base steps cannot be omitted"):
        validate_author_result(result, contract=contract(), plan=mini_plan())


def test_author_ledger_rejects_unknown_claim_id():
    result = valid_author_result()
    result["sections"][0]["claim_ids_used"] = ["DK-not-real-CL999"]
    with pytest.raises(AuthoringContractError, match="unknown claim_ids"):
        validate_author_result(
            result,
            contract=contract(),
            plan=mini_plan(),
            valid_claim_ids={"DK-real-CL001"},
        )


def test_author_ledger_requires_output_anchor_in_full_manuscript():
    result = valid_author_result()
    result["sections"][0]["output_anchor"] = "not in the manuscript"
    with pytest.raises(AuthoringContractError, match="output anchor not found"):
        validate_author_result(result, contract=contract(), plan=mini_plan())


def test_plan_change_handoff_stops_before_draft():
    result = {
        "status": "plan_change_required",
        "manuscript_markdown": "",
        "sections": [],
        "plan_change_requests": [
            {
                "request_id": "PCR-01",
                "reason": "claim set must change",
                "proposed_change": "return to Composition Agent",
                "affected_decision_ids": ["CD-M16-002-04"],
            }
        ],
    }
    validate_author_result(result, contract=contract(), plan=mini_plan())


def test_bad_golden_triggers_production_language_but_good_does_not():
    profile = load_json(PROFILE_PATH)
    bad = (FIXTURE_DIR / "bad-production-current.md").read_text(encoding="utf-8")
    good = (FIXTURE_DIR / "good-reference.md").read_text(encoding="utf-8")
    assert any(
        finding["code"] == "production_language"
        for finding in deterministic_writing_warnings(bad, profile)
    )
    assert deterministic_writing_warnings(good, profile) == []


def test_hard_gate_cannot_be_offset_by_high_total_score():
    profile = load_json(PROFILE_PATH)
    scores = []
    for dimension in profile["dimensions"]:
        score = dimension["weight"]
        if dimension["id"] == "base_manuscript_preservation":
            score = dimension["minimum"] - 1
        scores.append({"dimension_id": dimension["id"], "score": score, "evidence": "fixture"})
    outcome = evaluate_editorial_review(
        {"dimension_scores": scores, "hard_failures": []}, profile
    )
    assert outcome["total_score"] >= profile["passing_score"]
    assert outcome["passed"] is False
    assert outcome["hard_gate_failures"] == ["base_manuscript_preservation"]


def test_publication_score_requires_ninety_points():
    profile = load_json(PROFILE_PATH)
    assert profile["passing_score"] == 90
    scores = [
        {
            "dimension_id": dimension["id"],
            "score": dimension["weight"],
            "evidence": "fixture",
        }
        for dimension in profile["dimensions"]
    ]
    by_id = {item["dimension_id"]: item for item in scores}
    by_id["general_reader_readability"]["score"] = 0
    assert evaluate_editorial_review(
        {"dimension_scores": scores, "hard_failures": []}, profile
    )["passed"] is True

    by_id["editorial_voice_restraint"]["score"] = 9
    outcome = evaluate_editorial_review(
        {"dimension_scores": scores, "hard_failures": []}, profile
    )
    assert outcome["total_score"] == 89
    assert outcome["passed"] is False


def test_generation_fingerprint_changes_with_prompt_model_or_input():
    kwargs = {
        "inputs": {"plan_sha256": "a"},
        "prompt_text": "prompt",
        "schema": {"type": "object"},
        "model": "model-a",
        "reasoning": "medium",
    }
    baseline = generation_fingerprint(**kwargs)
    for key, value in [
        ("inputs", {"plan_sha256": "b"}),
        ("prompt_text", "changed"),
        ("model", "model-b"),
    ]:
        changed = deepcopy(kwargs)
        changed[key] = value
        assert generation_fingerprint(**changed) != baseline


def test_local_strict_schema_rejects_malformed_plan_change_request():
    result = {
        "status": "plan_change_required",
        "manuscript_markdown": "",
        "sections": [],
        "plan_change_requests": [
            {"request_id": "PCR-01", "reason": "reason", "proposed_change": "change"}
        ],
    }
    with pytest.raises(AuthoringContractError, match="affected_decision_ids"):
        validate_strict_schema(result, AUTHOR_RESULT_SCHEMA)


class FakeClient:
    def __init__(self, responses, *, model, reasoning_effort="medium"):
        self.responses = list(responses)
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.calls = 0

    def generate_json(self, _system_prompt, _user_prompt, _schema):
        self.calls += 1
        if not self.responses:
            raise AssertionError("unexpected model call")
        return deepcopy(self.responses.pop(0))


def passing_review():
    profile = load_json(PROFILE_PATH)
    return {
        "scope_confirmation": "writing_quality_and_base_preservation",
        "summary": "The scoped reference section preserves the base argument.",
        "dimension_scores": [
            {
                "dimension_id": dimension["id"],
                "score": dimension["weight"],
                "evidence": "The supplied section and ledger preserve the required chain.",
            }
            for dimension in profile["dimensions"]
        ],
        "hard_failures": [],
        "section_reviews": [
            {
                "section_id": "matt16-18-rock",
                "base_step_ids_preserved": [
                    "M16-18-S01",
                    "M16-18-S02",
                    "M16-18-S03",
                    "M16-18-S04",
                ],
                "assessment": "Preserved in readable prose.",
            }
        ],
        "findings": [],
    }


def test_runner_reuses_matching_author_and_review_generations(tmp_path):
    openai = FakeClient([valid_author_result()], model="fake-openai")
    claude = FakeClient([passing_review()], model="fake-claude")
    kwargs = {
        "plan_path": PLAN_PATH,
        "knowledge_path": KNOWLEDGE_PATH,
        "contract_path": FIXTURE_DIR / "base-manuscript-contract.json",
        "publication_profile_path": PUBLICATION_PROFILE_PATH,
        "quality_profile_path": PROFILE_PATH,
        "output_dir": tmp_path,
        "openai_client": openai,
        "claude_client": claude,
    }
    first = run_authoring(**kwargs)
    assert first["status"] == "editorial_pass_no_revision"
    assert openai.calls == 1
    assert claude.calls == 1

    cached_openai = FakeClient([], model="fake-openai")
    cached_claude = FakeClient([], model="fake-claude")
    second = run_authoring(
        **{**kwargs, "openai_client": cached_openai, "claude_client": cached_claude}
    )
    assert second["status"] == "editorial_pass_no_revision"
    assert second["author_cached"] is True
    assert second["review_cached"] is True
    assert cached_openai.calls == 0
    assert cached_claude.calls == 0


def test_rejected_finding_maintained_by_reviewer_requires_human(tmp_path):
    review = passing_review()
    for item in review["dimension_scores"]:
        if item["dimension_id"] == "base_manuscript_preservation":
            item["score"] = 11
    review["hard_failures"] = ["load_bearing_base_argument_removed_or_reordered"]
    review["findings"] = [
        {
            "finding_id": "temporary-1",
            "dimension_id": "base_manuscript_preservation",
            "section_id": "matt16-18-rock",
            "severity": "high",
            "blocking": True,
            "manuscript_anchor": "以弗所書 2:20 提供了進一步的線索",
            "explanation": "The base chain is incomplete.",
            "recommended_action": "Restore the missing inferential bridge.",
        }
    ]
    openai = FakeClient(
        [
            valid_author_result(),
            {
                "adjudications": [
                    {"finding_id": "ERF-placeholder", "decision": "reject", "rationale": "disagree"}
                ]
            },
        ],
        model="fake-openai",
    )
    claude = FakeClient(
        [
            review,
            {
                "reconsiderations": [
                    {"finding_id": "ERF-placeholder", "decision": "maintain", "rationale": "still blocking"}
                ]
            },
        ],
        model="fake-claude",
    )
    original_openai_generate = openai.generate_json
    original_claude_generate = claude.generate_json

    def openai_generate(system_prompt, user_prompt, schema):
        if schema["name"].endswith("adjudication_v1"):
            finding_id = json.loads(user_prompt)["review"]["findings"][0]["finding_id"]
            openai.responses[0]["adjudications"][0]["finding_id"] = finding_id
        return original_openai_generate(system_prompt, user_prompt, schema)

    def claude_generate(system_prompt, user_prompt, schema):
        if schema["name"].endswith("reconsideration_v1"):
            finding_id = json.loads(user_prompt)["rejected_finding_ids"][0]
            claude.responses[0]["reconsiderations"][0]["finding_id"] = finding_id
        return original_claude_generate(system_prompt, user_prompt, schema)

    openai.generate_json = openai_generate
    claude.generate_json = claude_generate
    outcome = run_authoring(
        plan_path=PLAN_PATH,
        knowledge_path=KNOWLEDGE_PATH,
        contract_path=FIXTURE_DIR / "base-manuscript-contract.json",
        publication_profile_path=PUBLICATION_PROFILE_PATH,
        quality_profile_path=PROFILE_PATH,
        output_dir=tmp_path,
        openai_client=openai,
        claude_client=claude,
    )
    assert outcome["status"] == "human_review_required"
    assert len(outcome["human_required_finding_ids"]) == 1
