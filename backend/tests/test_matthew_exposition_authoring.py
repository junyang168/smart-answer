import json
from copy import deepcopy
from pathlib import Path

import pytest

from backend.pipeline.matthew_exposition_authoring import (
    AuthoringContractError,
    EDITORIAL_REVIEW_PACKET_MAX_BYTES,
    build_authoring_packet,
    build_editorial_review_packet,
    build_final_delta_review_packet,
    changed_markdown_paragraphs,
    deterministic_writing_warnings,
    evaluate_editorial_review,
    generation_fingerprint,
    merge_final_delta_review,
    rebind_review_after_hidden_metadata_normalization,
    select_delta_dimensions,
    sha256_text,
    validate_editorial_review,
    validate_final_delta_review,
    validate_author_result,
    validate_base_contract,
    validate_strict_schema,
    AUTHOR_RESULT_SCHEMA,
)
from backend.pipeline.matthew_exposition_authoring_runner import (
    _build_program_audit_manifest,
    _call_final_reviewer,
    run_authoring,
)


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


def full_authoring_packet():
    return build_authoring_packet(
        plan_path=PLAN_PATH,
        knowledge_path=KNOWLEDGE_PATH,
        contract_path=FIXTURE_DIR / "base-manuscript-contract.json",
        publication_profile_path=PUBLICATION_PROFILE_PATH,
        quality_profile_path=PROFILE_PATH,
    )


def verified_baseline():
    review = passing_review()
    manuscript = valid_author_result()["manuscript_markdown"]
    outcome = validate_editorial_review(
        review,
        contract=contract(),
        manuscript=manuscript,
        quality_profile=load_json(PROFILE_PATH),
    )
    outcome["manuscript_sha256"] = sha256_text(manuscript)
    return review, outcome, manuscript


def test_editorial_review_packet_is_bounded_and_excludes_authoring_bulk():
    packet = build_editorial_review_packet(
        authoring_packet=full_authoring_packet(),
        author_result=valid_author_result(),
    )
    serialized = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert len(serialized.encode("utf-8")) <= EDITORIAL_REVIEW_PACKET_MAX_BYTES
    assert packet["size_budget"]["actual_bytes"] == len(serialized.encode("utf-8"))
    forbidden = {
        "knowledge",
        "topic_nodes",
        "source_fragments",
        "evidence_steps",
        "plan",
        "base_manuscript_text",
        "base_manuscript_texts",
    }
    assert forbidden.isdisjoint(packet)
    assert packet["manuscript_sha256"] == sha256_text(
        valid_author_result()["manuscript_markdown"]
    )


def test_editorial_review_packet_fails_before_sending_oversize_manuscript():
    result = valid_author_result()
    result["manuscript_markdown"] += "\n\n" + ("超出預算。" * 10000)
    with pytest.raises(AuthoringContractError, match="exceeds 40960 byte budget"):
        build_editorial_review_packet(
            authoring_packet=full_authoring_packet(),
            author_result=result,
        )


def test_changed_paragraphs_exclude_unchanged_manuscript_text():
    changes = changed_markdown_paragraphs("one\n\ntwo\n\nthree", "one\n\nTWO\n\nthree")
    assert changes == [
        {
            "change": "replace",
            "before_paragraphs": ["two"],
            "after_paragraphs": ["TWO"],
        }
    ]


def test_delta_dimension_selection_is_explicit_and_bounded():
    affected = select_delta_dimensions(
        [{"dimension_id": "editorial_voice_restraint"}]
    )
    assert affected == [
        "general_reader_readability",
        "editorial_voice_restraint",
        "approved_written_style",
    ]


def test_delta_packet_sha_binding_and_programmatic_score_inheritance():
    baseline_review, baseline_outcome, manuscript = verified_baseline()
    revised = manuscript.replace("進一步的線索", "更清楚的線索", 1)
    accepted = [
        {
            "finding_id": "ERF-test-001",
            "dimension_id": "source_and_exegesis",
            "section_id": "matt16-18-rock",
            "severity": "medium",
            "blocking": True,
            "manuscript_anchor": "進一步的線索",
            "explanation": "Needs a clearer link.",
            "recommended_action": "Clarify the link.",
        }
    ]
    dispositions = [
        {"finding_id": "ERF-test-001", "status": "resolved", "note": "clarified"}
    ]
    profile = load_json(PROFILE_PATH)
    packet = build_final_delta_review_packet(
        baseline_review=baseline_review,
        baseline_outcome=baseline_outcome,
        baseline_manuscript=manuscript,
        revised_manuscript=revised,
        accepted_findings=accepted,
        dispositions=dispositions,
        quality_profile=profile,
        contract=contract(),
    )
    assert "manuscript_markdown" not in packet
    assert packet["manuscript_sha256"] == sha256_text(revised)
    affected = {item["id"] for item in packet["affected_dimensions"]}
    assert packet["affected_hard_failures"] == [
        "exegetical_observation_inference_conclusion_chain_missing"
    ]
    delta = {
        "scope_confirmation": "final_delta_writing_quality",
        "reviewed_manuscript_sha256": sha256_text(revised),
        "summary": "The changed paragraph resolves the finding.",
        "dimension_scores": [
            {
                "dimension_id": dimension_id,
                "score": next(item["weight"] for item in profile["dimensions"] if item["id"] == dimension_id),
                "evidence": "Verified in the supplied changed paragraph.",
            }
            for dimension_id in affected
        ],
        "hard_failure_assessments": [
            {
                "failure_id": "exegetical_observation_inference_conclusion_chain_missing",
                "failed": False,
                "evidence": "The chain remains present.",
            }
        ],
        "findings": [],
    }
    validate_final_delta_review(
        delta, packet=packet, revised_manuscript=revised, quality_profile=profile
    )
    merged, outcome = merge_final_delta_review(
        baseline_review=baseline_review,
        baseline_outcome=baseline_outcome,
        delta_review=delta,
        packet=packet,
        quality_profile=profile,
    )
    assert set(merged["score_provenance"]["rescored_dimensions"]) == affected
    assert outcome["total_score"] == 100
    assert outcome["passed"] is True

    with pytest.raises(AuthoringContractError, match="does not match revised manuscript SHA"):
        validate_final_delta_review(
            delta,
            packet=packet,
            revised_manuscript=revised + " changed",
            quality_profile=profile,
        )


def test_unverified_baseline_cannot_supply_inherited_scores():
    baseline_review, baseline_outcome, manuscript = verified_baseline()
    baseline_outcome.pop("manuscript_sha256")
    with pytest.raises(AuthoringContractError, match="baseline review is not verified"):
        build_final_delta_review_packet(
            baseline_review=baseline_review,
            baseline_outcome=baseline_outcome,
            baseline_manuscript=manuscript,
            revised_manuscript=manuscript + "\n\nrevision",
            accepted_findings=[{"finding_id": "F", "dimension_id": "approved_written_style"}],
            dispositions=[{"finding_id": "F", "status": "resolved", "note": "done"}],
            quality_profile=load_json(PROFILE_PATH),
            contract=contract(),
        )


class RetryingReviewClient:
    def __init__(self, failures):
        self.model = "fake-claude"
        self.timeout_seconds = 999
        self.max_retries = 9
        self.failures = list(failures)
        self.attempts = 0
        self.seen_timeout = None

    def generate_json(self, _prompt, _payload, _schema, *, timeout_seconds):
        self.seen_timeout = timeout_seconds
        last = None
        for _ in range(self.max_retries):
            self.attempts += 1
            if self.failures:
                last = self.failures.pop(0)
                continue
            return {"ok": True}
        raise last


def test_final_review_timeout_is_capped_and_retries_once():
    client = RetryingReviewClient([TimeoutError("slow")])
    assert _call_final_reviewer(client, "p", "{}", {"schema": {}}) == {"ok": True}
    assert client.seen_timeout == 300
    assert client.max_retries == 2
    assert client.attempts == 2


def test_truncated_json_retry_is_bounded_to_one():
    malformed = json.JSONDecodeError("truncated", '{"summary":', 11)
    client = RetryingReviewClient([malformed, malformed, malformed])
    with pytest.raises(json.JSONDecodeError, match="truncated"):
        _call_final_reviewer(client, "p", "{}", {"schema": {}})
    assert client.max_retries == 2
    assert client.attempts == 2


def test_malformed_schema_is_not_retried_by_workflow():
    client = RetryingReviewClient([])
    malformed = _call_final_reviewer(client, "p", "{}", {"schema": {}})
    assert client.attempts == 1
    with pytest.raises(AuthoringContractError):
        validate_final_delta_review(
            malformed,
            packet={"manuscript_sha256": sha256_text("draft"), "affected_dimensions": []},
            revised_manuscript="draft",
            quality_profile=load_json(PROFILE_PATH),
        )
    assert client.attempts == 1


class FakeClient:
    def __init__(self, responses, *, model, reasoning_effort="medium"):
        self.responses = list(responses)
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.calls = 0

    def generate_json(self, _system_prompt, _user_prompt, _schema, **_kwargs):
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


def test_runner_returns_unified_terminal_after_program_audit(monkeypatch, tmp_path):
    openai = FakeClient([valid_author_result()], model="fake-openai")
    claude = FakeClient([passing_review()], model="fake-claude")
    manuscript_sha = sha256_text(valid_author_result()["manuscript_markdown"])
    audit_dir = tmp_path / "program-audit"
    audit_dir.mkdir()
    manifest_path = audit_dir / "editorial-draft-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "editorial-draft-manifest.v1",
                "drafts": [
                    {
                        "draft_id": "DRAFT-1",
                        "relative_path": "manuscript.md",
                        "audit_config": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "backend.pipeline.matthew_exposition_authoring_runner._run_program_audit_stage",
        lambda **kwargs: {
            "status": "pass",
            "path": str(audit_dir / "program-audit.json"),
            "manifest_path": str(manifest_path),
            "summary": {"error_total": 0, "warning_total": 0},
            "manuscript_sha256": manuscript_sha,
        },
    )
    monkeypatch.setattr(
        "backend.pipeline.matthew_exposition_authoring_runner.publish_automated_editorial_draft",
        lambda *_args, **_kwargs: {
            "draft_id": "DRAFT-1",
            "destination": str(tmp_path / "repository/DRAFT-1"),
            "publication_decision_path": str(
                audit_dir / "automated-publication-decision.json"
            ),
        },
    )

    outcome = run_authoring(
        plan_path=PLAN_PATH,
        knowledge_path=KNOWLEDGE_PATH,
        contract_path=FIXTURE_DIR / "base-manuscript-contract.json",
        publication_profile_path=PUBLICATION_PROFILE_PATH,
        quality_profile_path=PROFILE_PATH,
        output_dir=tmp_path,
        openai_client=openai,
        claude_client=claude,
        program_audit_manifest_path=tmp_path / "template.json",
        program_audit_draft_id="DRAFT-1",
    )

    assert outcome["status"] == "workflow_published"
    assert outcome["editorial_status"] == "editorial_pass_no_revision"
    assert outcome["program_audit"]["manuscript_sha256"] == manuscript_sha


def test_runner_uses_delta_packet_after_revision_and_recomputes_score(tmp_path):
    review = passing_review()
    next(item for item in review["dimension_scores"] if item["dimension_id"] == "approved_written_style")["score"] = 8
    review["findings"] = [
        {
            "finding_id": "temporary",
            "dimension_id": "approved_written_style",
            "section_id": "matt16-18-rock",
            "severity": "low",
            "blocking": False,
            "manuscript_anchor": "進一步的線索",
            "explanation": "The sentence can be clearer.",
            "recommended_action": "Clarify it.",
        }
    ]
    revision = {
        **valid_author_result(),
        "status": "revised",
        "manuscript_markdown": valid_author_result()["manuscript_markdown"].replace(
            "進一步的線索", "更清楚的線索", 1
        ),
        "finding_dispositions": [
            {"finding_id": "placeholder", "status": "resolved", "note": "clarified"}
        ],
    }
    openai = FakeClient(
        [
            valid_author_result(),
            {"adjudications": [{"finding_id": "placeholder", "decision": "accept", "rationale": "valid"}]},
            revision,
        ],
        model="fake-openai",
    )
    claude = FakeClient([review, {}], model="fake-claude")
    original_openai_generate = openai.generate_json
    original_claude_generate = claude.generate_json

    def openai_generate(system_prompt, user_prompt, schema, **kwargs):
        if schema["name"].endswith("adjudication_v1"):
            finding_id = json.loads(user_prompt)["review"]["findings"][0]["finding_id"]
            openai.responses[0]["adjudications"][0]["finding_id"] = finding_id
        elif schema["name"].endswith("author_revision_v1"):
            finding_id = json.loads(user_prompt)["accepted_findings"][0]["finding_id"]
            openai.responses[0]["finding_dispositions"][0]["finding_id"] = finding_id
        return original_openai_generate(system_prompt, user_prompt, schema, **kwargs)

    def claude_generate(system_prompt, user_prompt, schema, **kwargs):
        if schema["name"].endswith("final_delta_review_v1"):
            packet = json.loads(user_prompt)
            profile = load_json(PROFILE_PATH)
            by_id = {item["id"]: item for item in profile["dimensions"]}
            claude.responses[0] = {
                "scope_confirmation": "final_delta_writing_quality",
                "reviewed_manuscript_sha256": packet["manuscript_sha256"],
                "summary": "The accepted change is resolved.",
                "dimension_scores": [
                    {
                        "dimension_id": item["id"],
                        "score": by_id[item["id"]]["weight"],
                        "evidence": "The supplied changed paragraph is clear.",
                    }
                    for item in packet["affected_dimensions"]
                ],
                "hard_failure_assessments": [],
                "findings": [],
            }
        return original_claude_generate(system_prompt, user_prompt, schema, **kwargs)

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
    assert outcome["status"] == "editorial_pass_after_delta_review"
    assert outcome["rubric_outcome"]["total_score"] == 100
    delta_packet = load_json(tmp_path / "final-delta-review-packet.json")["result"]
    assert "manuscript_markdown" not in delta_packet
    assert delta_packet["changed_paragraphs"]
    assert claude.calls == 2


def test_two_revision_rounds_use_one_review_call_per_round(tmp_path):
    review = passing_review()
    next(
        item
        for item in review["dimension_scores"]
        if item["dimension_id"] == "approved_written_style"
    )["score"] = 8
    review["findings"] = [
        {
            "finding_id": "initial-placeholder",
            "dimension_id": "approved_written_style",
            "section_id": "matt16-18-rock",
            "severity": "low",
            "blocking": False,
            "manuscript_anchor": "進一步的線索",
            "explanation": "The sentence can be clearer.",
            "recommended_action": "Clarify it.",
        }
    ]
    first_revision = {
        **valid_author_result(),
        "status": "revised",
        "manuscript_markdown": valid_author_result()["manuscript_markdown"].replace(
            "進一步的線索", "更清楚的線索", 1
        ),
        "finding_dispositions": [
            {"finding_id": "placeholder", "status": "resolved", "note": "clarified"}
        ],
    }
    second_revision = {
        **valid_author_result(),
        "status": "revised",
        "manuscript_markdown": first_revision["manuscript_markdown"].replace(
            "更清楚的線索", "最清楚的線索", 1
        ),
        "finding_dispositions": [
            {"finding_id": "placeholder", "status": "resolved", "note": "clarified again"}
        ],
    }
    openai = FakeClient(
        [
            valid_author_result(),
            {"adjudications": [{"finding_id": "placeholder", "decision": "accept", "rationale": "valid"}]},
            first_revision,
            {"adjudications": [{"finding_id": "placeholder", "decision": "accept", "rationale": "valid"}]},
            second_revision,
        ],
        model="fake-openai",
    )
    claude = FakeClient([review, {}, {}], model="fake-claude")
    original_openai_generate = openai.generate_json
    original_claude_generate = claude.generate_json
    delta_calls = 0

    def openai_generate(system_prompt, user_prompt, schema, **kwargs):
        if schema["name"].endswith("adjudication_v1"):
            finding_id = json.loads(user_prompt)["review"]["findings"][0]["finding_id"]
            openai.responses[0]["adjudications"][0]["finding_id"] = finding_id
        elif schema["name"].endswith("author_revision_v1"):
            finding_id = json.loads(user_prompt)["accepted_findings"][0]["finding_id"]
            openai.responses[0]["finding_dispositions"][0]["finding_id"] = finding_id
        return original_openai_generate(system_prompt, user_prompt, schema, **kwargs)

    def claude_generate(system_prompt, user_prompt, schema, **kwargs):
        nonlocal delta_calls
        if schema["name"].endswith("final_delta_review_v1"):
            delta_calls += 1
            packet = json.loads(user_prompt)
            profile = load_json(PROFILE_PATH)
            by_id = {item["id"]: item for item in profile["dimensions"]}
            findings = []
            if delta_calls == 1:
                findings = [
                    {
                        "finding_id": "next-placeholder",
                        "dimension_id": "approved_written_style",
                        "section_id": "matt16-18-rock",
                        "severity": "low",
                        "blocking": False,
                        "manuscript_anchor": "更清楚的線索",
                        "explanation": "One local style issue remains.",
                        "recommended_action": "Make the wording final.",
                    }
                ]
            claude.responses[0] = {
                "scope_confirmation": "final_delta_writing_quality",
                "reviewed_manuscript_sha256": packet["manuscript_sha256"],
                "summary": "The supplied changed paragraph was checked.",
                "dimension_scores": [
                    {
                        "dimension_id": item["id"],
                        "score": (
                            8
                            if delta_calls == 1
                            and item["id"] == "approved_written_style"
                            else by_id[item["id"]]["weight"]
                        ),
                        "evidence": "Verified only in the supplied changed paragraph.",
                    }
                    for item in packet["affected_dimensions"]
                ],
                "hard_failure_assessments": [],
                "findings": findings,
            }
        return original_claude_generate(system_prompt, user_prompt, schema, **kwargs)

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
        max_revision_rounds=2,
    )

    assert outcome["status"] == "editorial_pass_after_delta_review"
    assert delta_calls == 2
    assert claude.calls == 3  # one baseline review plus one delta review per revision
    inherited = load_json(
        tmp_path / "round-02/independent-editorial-review.json"
    )
    assert inherited["generation"]["role"] == "verified_delta_review_inheritance"
    assert not (tmp_path / "round-02/editorial-review-packet.json").exists()


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

    def openai_generate(system_prompt, user_prompt, schema, **kwargs):
        if schema["name"].endswith("adjudication_v1"):
            finding_id = json.loads(user_prompt)["review"]["findings"][0]["finding_id"]
            openai.responses[0]["adjudications"][0]["finding_id"] = finding_id
        return original_openai_generate(system_prompt, user_prompt, schema, **kwargs)

    def claude_generate(system_prompt, user_prompt, schema, **kwargs):
        if schema["name"].endswith("reconsideration_v1"):
            finding_id = json.loads(user_prompt)["rejected_finding_ids"][0]
            claude.responses[0]["reconsiderations"][0]["finding_id"] = finding_id
        return original_claude_generate(system_prompt, user_prompt, schema, **kwargs)

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


def test_hidden_metadata_normalization_rebinds_verified_review_sha():
    manuscript = valid_author_result()["manuscript_markdown"]
    review = passing_review()
    profile = load_json(PROFILE_PATH)
    outcome = validate_editorial_review(
        review,
        contract=contract(),
        manuscript=manuscript,
        quality_profile=profile,
    )
    outcome["manuscript_sha256"] = sha256_text(manuscript)
    normalized = manuscript.replace(
        '<!-- provenance: {"attribution":"professor",',
        '<!-- provenance: {"attribution":"professor","normalization":"v1",',
        1,
    )

    rebound, record = rebind_review_after_hidden_metadata_normalization(
        review=review,
        outcome=outcome,
        before_manuscript=manuscript,
        after_manuscript=normalized,
        contract=contract(),
        quality_profile=profile,
    )

    assert rebound["manuscript_sha256"] == sha256_text(normalized)
    assert record["reader_visible_text_unchanged"] is True


def test_hidden_metadata_normalization_rejects_reader_visible_change():
    manuscript = valid_author_result()["manuscript_markdown"]
    review = passing_review()
    profile = load_json(PROFILE_PATH)
    outcome = validate_editorial_review(
        review,
        contract=contract(),
        manuscript=manuscript,
        quality_profile=profile,
    )
    outcome["manuscript_sha256"] = sha256_text(manuscript)

    with pytest.raises(AuthoringContractError, match="reader-visible"):
        rebind_review_after_hidden_metadata_normalization(
            review=review,
            outcome=outcome,
            before_manuscript=manuscript,
            after_manuscript=manuscript + "\nVisible change.\n",
            contract=contract(),
            quality_profile=profile,
        )


def test_program_audit_manifest_uses_author_ledger_headings():
    template = {
        "schema_version": "editorial-draft-manifest.v2",
        "drafts": [
            {
                "draft_id": "DRAFT-1",
                "relative_path": "old.md",
                "presentation_package_path": "old-knowledge.json",
                "audit_config": {
                    "decision_sections": [
                        {"decision_id": "D1", "markdown_heading": "Old one"},
                        {"decision_id": "D2", "markdown_heading": "Old one"},
                        {"decision_id": "D3", "markdown_heading": "Old two"},
                    ],
                    "required_scripture_quotations": [
                        {"markdown_heading": "Old one", "required_markers": ["marker"]}
                    ],
                },
            }
        ],
    }

    staged = _build_program_audit_manifest(
        template=template,
        draft_id="DRAFT-1",
        sections=[
            {"decision_ids": ["D1", "D2"], "output_anchor": "### Reader one"},
            {"decision_ids": ["D3"], "output_anchor": "### Reader two"},
        ],
    )

    draft = staged["drafts"][0]
    assert draft["relative_path"] == "manuscript.md"
    assert draft["audit_config"]["decision_sections"] == [
        {"decision_id": "D1", "markdown_heading": "Reader one"},
        {"decision_id": "D2", "markdown_heading": "Reader one"},
        {"decision_id": "D3", "markdown_heading": "Reader two"},
    ]
    assert draft["audit_config"]["required_scripture_quotations"][0][
        "markdown_heading"
    ] == "經文與問題"
