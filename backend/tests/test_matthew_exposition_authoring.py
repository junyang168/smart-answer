import argparse
import json
import math
from copy import deepcopy
from pathlib import Path

import pytest

from backend.pipeline.matthew_exposition_authoring import (
    AuthoringContractError,
    EDITORIAL_REVIEW_PACKET_MAX_BYTES,
    build_authoring_packet,
    build_authoring_packet_from_store,
    contract_from_plan_payload,
    build_editorial_review_packet,
    build_final_delta_review_packet,
    canonical_json,
    changed_markdown_paragraphs,
    deterministic_writing_warnings,
    quote_fidelity_warnings,
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
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.pipeline.matthew_exposition_authoring_runner import (
    _build_program_audit_manifest,
    _call_final_reviewer,
    _require_audit_draft,
    _run_program_audit_stage,
    run_authoring,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures/matthew_exposition/matt16-18-v1"
PROFILE_PATH = (
    Path(__file__).parents[1]
    / "config/editorial_quality_profiles/WQ-matthew-exposition-v1.json"
)
ROOT = Path(__file__).parents[2]
WANG_FIXTURE_DIR = Path(__file__).parent / "fixtures/wang_knowledge_platform"
PLAN_PATH = WANG_FIXTURE_DIR / "matthew_exposition/article2-reviewed-plan.json"
KNOWLEDGE_PATH = (
    WANG_FIXTURE_DIR / "matthew_exposition/article2-knowledge-projection.json"
)
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
                "preserved_step_anchors": [
                    {
                        "step_id": "M16-18-S01",
                        "anchor": "彼得是 *Petros*，磐石是 *petra*",
                    },
                    {
                        "step_id": "M16-18-S02",
                        "anchor": "耶穌的話不能不經解釋就縮成",
                    },
                    {
                        "step_id": "M16-18-S03",
                        "anchor": "原文把使徒和先知放在同一個定冠詞之下",
                    },
                    {
                        "step_id": "M16-18-S04",
                        "anchor": "教會終極的根基卻不是使徒個人的身分和權位，而是他們所見證的基督",
                    },
                ],
                "claim_ids_used": [],
                "integration_operations": ["tension"],
                "applied_operations": ["preserve", "clarify"],
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
    result["sections"][0]["preserved_step_anchors"].pop()
    with pytest.raises(AuthoringContractError, match="unaccounted base steps"):
        validate_author_result(result, contract=contract(), plan=mini_plan())


def test_author_ledger_rejects_omission_of_required_base_step():
    result = valid_author_result()
    result["sections"][0]["base_step_ids_preserved"].pop()
    result["sections"][0]["preserved_step_anchors"].pop()
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


def test_author_ledger_rejects_preserved_step_without_anchor():
    result = valid_author_result()
    result["sections"][0]["preserved_step_anchors"] = [
        item
        for item in result["sections"][0]["preserved_step_anchors"]
        if item["step_id"] != "M16-18-S03"
    ]
    with pytest.raises(
        AuthoringContractError, match="preserved base steps without a manuscript anchor"
    ):
        validate_author_result(result, contract=contract(), plan=mini_plan())


def test_author_ledger_rejects_step_anchor_that_is_not_in_the_manuscript():
    result = valid_author_result()
    for item in result["sections"][0]["preserved_step_anchors"]:
        if item["step_id"] == "M16-18-S02":
            # A paraphrase of the manuscript, not a literal substring of it.
            item["anchor"] = "耶穌的話不能不經解釋就縮成教會建造在彼得身上"
    with pytest.raises(
        AuthoringContractError, match="preserved step anchor not found in manuscript: M16-18-S02"
    ):
        validate_author_result(result, contract=contract(), plan=mini_plan())


def test_editorial_review_packet_carries_verified_step_anchors():
    packet = build_editorial_review_packet(
        authoring_packet=full_authoring_packet(),
        author_result=valid_author_result(),
    )
    ledger = packet["author_section_ledger"][0]
    assert [item["step_id"] for item in ledger["preserved_step_anchors"]] == [
        "M16-18-S01",
        "M16-18-S02",
        "M16-18-S03",
        "M16-18-S04",
    ]
    for item in ledger["preserved_step_anchors"]:
        assert item["anchor"] in packet["manuscript_markdown"]


def test_author_ledger_rejects_ineligible_operation():
    value = contract()
    value["sections"][0]["ineligible_operations"].append("invent_life_application_chain")
    result = valid_author_result()
    result["sections"][0]["applied_operations"].append("invent_life_application_chain")
    with pytest.raises(AuthoringContractError, match="ineligible operations"):
        validate_author_result(result, contract=value, plan=mini_plan())


def test_author_ledger_rejects_ineligible_supplemental_operation():
    value = contract()
    value["sections"][0]["ineligible_operations"].append("tension")
    result = valid_author_result()
    with pytest.raises(AuthoringContractError, match="ineligible operations"):
        validate_author_result(result, contract=value, plan=mini_plan())


def test_author_ledger_rejects_operation_outside_allowed_list():
    result = valid_author_result()
    result["sections"][0]["applied_operations"].append("invent_life_application_chain")
    with pytest.raises(AuthoringContractError, match="outside allowed_operations"):
        validate_author_result(result, contract=contract(), plan=mini_plan())


def test_author_ledger_requires_declared_operations():
    result = valid_author_result()
    result["sections"][0]["applied_operations"] = []
    with pytest.raises(AuthoringContractError, match="at least one applied operation"):
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


# The professor's own words, verbatim from SRC-2016_NYSC_4 segment 732: rule 8e
# sends the author here for his phrasing, so the check that a quote is really
# his is written against the same text rather than a paraphrase of it.
SEGMENT_732 = (
    "因為你不關心、你不重視神的意思，你所關心的是人的意思。我們中文翻成「體貼」那個字，"
    "phroneō 那個字，是關心、重視的意思，你不是從神的觀點來看這件事情。"
    "第一功課是什麼？你要先知道耶穌就是基督。可是門徒通過第一課的考試，耶穌開始教他們第二課。"
)
NOTES_SENTENCE = "彼得並非不認識耶穌是彌賽亞，他的問題在於他不認識彌賽亞的性質。"
PROFESSOR_PROVENANCE = '<!-- provenance: {"attribution":"professor","claim_ids":["DK-1"]} -->\n'


def professor_paragraph(text: str) -> str:
    return PROFESSOR_PROVENANCE + text


def test_a_verbatim_quote_of_the_professor_raises_no_warning():
    draft = professor_paragraph("他這樣重讀那個字：「我們中文翻成「體貼」那個字，phroneō 那個字，是關心、重視」。")
    assert quote_fidelity_warnings(draft, [SEGMENT_732]) == []


def test_prose_rewritten_inside_quotation_marks_is_reported():
    draft = professor_paragraph("他說：「你不夠關心神的心意，反而更在乎人的看法」。")
    assert quote_fidelity_warnings(draft, [SEGMENT_732]) == [
        {"code": "quote_not_verbatim", "quoted_text": "你不夠關心神的心意，反而更在乎人的看法"}
    ]


def test_a_nested_term_quote_does_not_hide_a_rewritten_outer_quote():
    # `「體貼」` is below the term-mention threshold on its own; matching only the
    # inner span would skip the outer sentence, which is the quote 8e is about.
    draft = professor_paragraph("他說：「我們中文翻成「體貼」那個字，其實帶著情感上的體恤」。")
    assert [item["quoted_text"] for item in quote_fidelity_warnings(draft, [SEGMENT_732])] == [
        "我們中文翻成「體貼」那個字，其實帶著情感上的體恤"
    ]


def test_an_elided_quote_matches_when_both_sides_are_verbatim_and_in_order():
    kept = professor_paragraph("「因為你不關心、你不重視神的意思⋯⋯你不是從神的觀點」。")
    assert quote_fidelity_warnings(kept, [SEGMENT_732]) == []
    # An elision may skip material but may not reorder it.
    reordered = professor_paragraph("「耶穌開始教他們第二課⋯⋯門徒通過第一課的考試」。")
    assert quote_fidelity_warnings(reordered, [SEGMENT_732])


def test_quote_matching_ignores_punctuation_the_transcriber_chose():
    draft = professor_paragraph("「門徒通過第一課的考試；耶穌開始教他們第二課」。")
    assert quote_fidelity_warnings(draft, [SEGMENT_732]) == []


def test_naming_a_term_is_not_quoting_a_sentence():
    draft = professor_paragraph("中文譯本把這個字翻成「體貼」，容易讀成情感上的體恤。")
    assert quote_fidelity_warnings(draft, [SEGMENT_732]) == []


def test_a_quote_from_the_base_manuscript_is_not_reported_as_invented():
    draft = professor_paragraph("「他的問題在於他不認識彌賽亞的性質」。")
    assert quote_fidelity_warnings(draft, [SEGMENT_732, NOTES_SENTENCE]) == []


def test_only_paragraphs_claiming_the_professor_are_checked():
    scripture = (
        '<!-- provenance: {"attribution":"scripture","scripture_refs":["Matt.16.23"]} -->\n'
        "「撒但，退我後邊去吧，你是絆我腳的」。"
    )
    assert quote_fidelity_warnings(scripture, [SEGMENT_732]) == []


def test_warnings_skip_quote_fidelity_when_no_source_texts_are_given():
    profile = load_json(PROFILE_PATH)
    draft = professor_paragraph("他說：「你不夠關心神的心意，反而更在乎人的看法」。")
    assert deterministic_writing_warnings(draft, profile) == []
    assert any(
        finding["code"] == "quote_not_verbatim"
        for finding in deterministic_writing_warnings(draft, profile, source_texts=[SEGMENT_732])
    )


def test_hard_gate_cannot_be_offset_by_high_total_score():
    """Nine dimensions at full marks do not buy the tenth a pass."""

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
    total_weight = sum(item["weight"] for item in profile["dimensions"])
    assert outcome["total_score"] > total_weight * 0.9
    assert outcome["passed"] is False
    assert outcome["hard_gate_failures"] == ["base_manuscript_preservation"]


def test_every_dimension_carries_the_same_share_of_its_own_weight():
    """The bar is 80% of each dimension's weight, not a total. A rubric that
    gates on a sum lets a weak dimension be carried by the strong ones."""

    profile = load_json(PROFILE_PATH)
    assert "passing_score" not in profile
    for dimension in profile["dimensions"]:
        assert dimension["minimum"] == math.ceil(dimension["weight"] * 0.8)


def test_publication_requires_every_dimension_to_reach_its_minimum():
    """One point below a minimum fails, whichever dimension it is."""

    profile = load_json(PROFILE_PATH)
    for dimension in profile["dimensions"]:
        scores = [
            {"dimension_id": item["id"], "score": item["minimum"], "evidence": "fixture"}
            for item in profile["dimensions"]
        ]
        at_minimum = evaluate_editorial_review(
            {"dimension_scores": scores, "hard_failures": []}, profile
        )
        assert at_minimum["passed"] is True, dimension["id"]
        assert at_minimum["hard_gate_failures"] == []

        by_id = {item["dimension_id"]: item for item in scores}
        by_id[dimension["id"]]["score"] -= 1
        below = evaluate_editorial_review(
            {"dimension_scores": scores, "hard_failures": []}, profile
        )
        assert below["passed"] is False, dimension["id"]
        assert below["hard_gate_failures"] == [dimension["id"]]


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


class _FakeAuthoringStore:
    """A minimal stand-in for PostgresKnowledgeStore's read path."""

    def __init__(self, records: dict[str, dict[str, dict]]):
        self._records = records

    def get_record(self, collection, object_id):
        return self._records.get(collection, {}).get(object_id)

    # Borrowed rather than reimplemented: the real assembly is the thing under
    # test here, and it reaches the store only through `get_record`.
    get_plan_document = PostgresKnowledgeStore.get_plan_document

    def compile_package(self, *, package_id=None):
        """Mirror the real store: a full snapshot stamped with `compiled_at`.

        The stamp is the reason the caller strips it -- a wall-clock field
        would make packet_sha256 differ on every run with identical data.
        """

        package = json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
        package["package_id"] = package_id or "PG-SNAPSHOT-TEST"
        package["compiled_at"] = "2026-08-17T12:00:00Z"
        package["authority"] = "postgresql_authoring_store"
        return package


def _store_from_migrated_contract():
    """Build a fake store whose plan/decisions mirror the real JSON fixtures.

    This exercises the same merge the real migration performs
    (`authoring_contract_migration.merge_contract_into_plan`), so a store-backed
    packet can be compared against the file-backed one built from the exact
    same source contract and plan.
    """

    from backend.pipeline.authoring_contract_migration import (
        load_contract,
        merge_contract_into_plan,
    )

    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    contract = load_contract(FIXTURE_DIR / "base-manuscript-contract.json")
    decisions = plan.pop("decisions")
    plan["decision_ids"] = [d["decision_id"] for d in decisions]
    merged_plan = merge_contract_into_plan(
        plan, contract, confirmed_by="test-editor", confirmed_at="2026-08-17T00:00:00Z"
    )
    decisions_by_id = {d["decision_id"]: d for d in decisions}
    return _FakeAuthoringStore(
        {"composition_plans": {merged_plan["plan_id"]: merged_plan}, "composition_decisions": decisions_by_id}
    )


def test_contract_from_plan_payload_round_trips_every_field_the_contract_needs():
    """Regression: an earlier version of this reconstruction silently dropped
    `authoring_mode`, which only surfaces as a validation error deep inside
    `build_authoring_packet`, not as a missing-field error at the call site.
    """

    store = _store_from_migrated_contract()
    plan_payload = store.get_record("composition_plans", "CP-matthew-16-13-20")
    contract = contract_from_plan_payload(plan_payload, plan_document_sha256="irrelevant-here")
    original = json.loads(
        (FIXTURE_DIR / "base-manuscript-contract.json").read_text(encoding="utf-8")
    )
    original = original.get("result", original)
    for field in ("schema_version", "contract_id", "passage", "authoring_mode", "base_source", "sections"):
        assert contract[field] == original[field], f"{field} did not round-trip"
    # Optional in this older fixture; must still round-trip when absent, not
    # silently become a different falsy value.
    assert contract["supplemental_material"] == original.get("supplemental_material", [])
    assert contract["global_rules"] == original.get("global_rules", [])
    # No `status`: the `editor_confirmed` gate was residual from an early
    # version and certified nothing -- it read a string that a migration
    # script's `--confirmed-by` argument had written.
    assert "status" not in contract


def test_run_authoring_uses_a_supplied_packet_without_rebuilding_from_files(tmp_path):
    """A store-sourced run has no plan/contract file to rebuild from.

    Passing deliberately non-existent paths proves the supplied packet is used
    rather than silently re-read from disk, which would crash for --plan-id.
    """
    openai = FakeClient([valid_author_result()], model="fake-openai")
    claude = FakeClient([passing_review()], model="fake-claude")
    result = run_authoring(
        packet=full_authoring_packet(),
        plan_path=tmp_path / "does-not-exist-plan.json",
        knowledge_path=KNOWLEDGE_PATH,
        contract_path=tmp_path / "does-not-exist-contract.json",
        publication_profile_path=PUBLICATION_PROFILE_PATH,
        quality_profile_path=PROFILE_PATH,
        output_dir=tmp_path / "out",
        openai_client=openai,
        claude_client=claude,
        skip_grounding_gate=True,
    )
    assert result["status"] == "editorial_pass_no_revision"


def _grounding(assertions=()):
    """A reviewer reply. The quoted sentences are the whole answer: the gate
    derives its verdict from them, so there is no verdict to pass in."""

    return {
        "schema_version": "matthew-exposition-grounding-result.v2",
        "unsupported_assertions": list(assertions),
        "notes": "",
    }


def test_grounding_gate_stops_an_ungrounded_draft_before_the_writing_reviewer(tmp_path):
    """The rubric cannot tell a supported inference from an invented one --
    both look like a complete argument. So grounding runs first, and a failure
    stops the run before any score is computed over unchecked prose.
    """
    # Quote text from inside a checked paragraph: unsupported_assertions are
    # substring-verified against that paragraph, not the whole document.
    invented = "彼得是 *Petros*"
    openai = FakeClient([valid_author_result()], model="fake-openai")
    # The fixture manuscript has exactly 3 paragraphs the gate checks; a
    # wrong count here would leave a grounding response to be consumed by
    # the review call and mask what this test is asserting.
    claude = FakeClient([_grounding([invented])] * 3, model="fake-claude")
    result = run_authoring(
        packet=full_authoring_packet(),
        plan_path=PLAN_PATH,
        knowledge_path=KNOWLEDGE_PATH,
        contract_path=FIXTURE_DIR / "base-manuscript-contract.json",
        publication_profile_path=PUBLICATION_PROFILE_PATH,
        quality_profile_path=PROFILE_PATH,
        output_dir=tmp_path / "out",
        openai_client=openai,
        claude_client=claude,
    )
    assert result["status"] == "grounding_gate_failed"
    assert result["unsupported_paragraph_count"] >= 1
    # The evidence is written out, not just summarised in a status string.
    report = json.loads((tmp_path / "out" / "grounding-report.json").read_text(encoding="utf-8"))
    assert report["result"]["passed"] is False
    unsupported = [
        f for f in report["result"]["findings"] if f["code"] == "unsupported_assertion"
    ]
    assert unsupported, "gate must report which assertions exceeded the material"
    assert unsupported[0]["unsupported_assertions"] == [invented]


def test_grounding_gate_lets_a_grounded_draft_through_to_review(tmp_path):
    openai = FakeClient([valid_author_result()], model="fake-openai")
    claude = FakeClient([_grounding()] * 3 + [passing_review()], model="fake-claude")
    result = run_authoring(
        packet=full_authoring_packet(),
        plan_path=PLAN_PATH,
        knowledge_path=KNOWLEDGE_PATH,
        contract_path=FIXTURE_DIR / "base-manuscript-contract.json",
        publication_profile_path=PUBLICATION_PROFILE_PATH,
        quality_profile_path=PROFILE_PATH,
        output_dir=tmp_path / "out",
        openai_client=openai,
        claude_client=claude,
    )
    assert result["status"] == "editorial_pass_no_revision"
    report = json.loads((tmp_path / "out" / "grounding-report.json").read_text(encoding="utf-8"))
    assert report["result"]["passed"] is True


def test_grounding_gate_is_on_by_default(tmp_path):
    """Skipping must be an explicit choice: a caller that forgets the flag
    gets the check, not a silently unguarded run.
    """
    openai = FakeClient([valid_author_result()], model="fake-openai")
    claude = FakeClient([], model="fake-claude")  # any model call fails
    with pytest.raises(AssertionError, match="unexpected model call"):
        run_authoring(
            packet=full_authoring_packet(),
            plan_path=PLAN_PATH,
            knowledge_path=KNOWLEDGE_PATH,
            contract_path=FIXTURE_DIR / "base-manuscript-contract.json",
            publication_profile_path=PUBLICATION_PROFILE_PATH,
            quality_profile_path=PROFILE_PATH,
            output_dir=tmp_path / "out",
            openai_client=openai,
            claude_client=claude,
        )


def test_store_built_packet_hash_is_stable_across_runs():
    """Regression: the plan and contract are staged through a temp directory
    whose name changes every run. Leaving that path in `sources` made
    packet_sha256 non-deterministic, which silently defeats the generation
    cache -- every run would look like new inputs and re-call the models.
    """
    store = _store_from_migrated_contract()
    kwargs = dict(
        plan_id="CP-matthew-16-13-20",
        store=store,
        knowledge_path=KNOWLEDGE_PATH,
        publication_profile_path=PUBLICATION_PROFILE_PATH,
        quality_profile_path=PROFILE_PATH,
    )
    hashes = {build_authoring_packet_from_store(**kwargs)["packet_sha256"] for _ in range(3)}
    assert len(hashes) == 1

    packet = build_authoring_packet_from_store(**kwargs)
    assert "tmp" not in json.dumps(packet["sources"], ensure_ascii=False)
    assert packet["sources"]["plan"]["authority"] == "postgresql_authoring_store"
    assert packet["sources"]["plan"]["object_id"] == "CP-matthew-16-13-20"


def test_build_authoring_packet_from_store_matches_the_file_based_packet():
    store = _store_from_migrated_contract()
    from_file = full_authoring_packet()
    from_store = build_authoring_packet_from_store(
        plan_id="CP-matthew-16-13-20",
        store=store,
        knowledge_path=KNOWLEDGE_PATH,
        publication_profile_path=PUBLICATION_PROFILE_PATH,
        quality_profile_path=PROFILE_PATH,
    )
    assert from_store["knowledge"] == from_file["knowledge"]
    assert from_store["base_manuscript_texts"] == from_file["base_manuscript_texts"]
    assert from_store["sermon_transcript_texts"] == from_file["sermon_transcript_texts"]
    assert from_store["base_contract"]["sections"] == from_file["base_contract"]["sections"]
    assert from_store["base_contract"]["base_source"] == from_file["base_contract"]["base_source"]
    assert {d["decision_id"] for d in from_store["plan"]["decisions"]} == {
        d["decision_id"] for d in from_file["plan"]["decisions"]
    }


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
    # The slice carries sentences, never the records they were drawn from, and
    # never the surrounding sermon speech the author wrote from.
    assert set(packet["source_slice"]) == {
        "base_manuscript_exegesis",
        "cited_source_excerpts",
        "source_tensions",
    }
    authoring_packet = full_authoring_packet()
    whole_manuscript = authoring_packet["base_manuscript_text"]
    for row in packet["source_slice"]["base_manuscript_exegesis"]:
        assert row["sentence"] in whole_manuscript
        assert len(row["sentence"]) < len(whole_manuscript)
    assert packet["manuscript_sha256"] == sha256_text(
        valid_author_result()["manuscript_markdown"]
    )


def test_editorial_review_packet_carries_the_base_sentence_a_step_preserved():
    """`statement` is the contract's rewording; scoring preservation against it
    only asks whether a step was mentioned, not whether the base manuscript's
    own argument survived."""

    packet = build_editorial_review_packet(
        authoring_packet=full_authoring_packet(),
        author_result=valid_author_result(),
    )
    contract_steps = {
        step["step_id"]: step
        for section in contract()["sections"]
        for step in section["required_argument_steps"]
    }
    sent = {
        step["step_id"]: step
        for section in packet["base_preservation_contract"]["sections"]
        for step in section["required_argument_steps"]
    }
    assert set(sent) == set(contract_steps)
    for step_id, step in sent.items():
        assert step["source_excerpt"] == contract_steps[step_id].get("source_excerpt", "")
    assert any(step["source_excerpt"] for step in sent.values())


def test_editorial_review_packet_keeps_the_slice_inside_the_passage():
    """A base manuscript covers a whole lecture. Only the paragraphs explaining
    this article's verses may reach the reviewer, or the slice grows with the
    lecture rather than with the article."""

    authoring_packet = full_authoring_packet()
    packet = build_editorial_review_packet(
        authoring_packet=authoring_packet,
        author_result=valid_author_result(),
    )
    sentences = [
        row["sentence"] for row in packet["source_slice"]["base_manuscript_exegesis"]
    ]
    assert sentences
    whole = authoring_packet["base_manuscript_text"]
    assert sum(len(item) for item in sentences) < len(whole) / 2


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


TWO_SECTION_MANUSCRIPT = "\n\n".join(
    [
        "## 甲節：磐石的身份",
        "希臘文用了兩個相關卻不同的詞：彼得是陽性名詞，磐石是陰性名詞。",
        "## 乙節：教會的根基",
        "以弗所書 2:20 把使徒和先知放在同一個定冠詞之下，共同指向房角石基督。",
        "",
    ]
)


def two_section_contract():
    return {
        "sections": [
            {
                "section_id": "sec-a",
                "decision_ids": ["CD-A"],
                "required_argument_steps": [
                    {"step_id": "S-A", "statement": "Petros 與 petra 的詞形不同。"}
                ],
            },
            {
                "section_id": "sec-b",
                "decision_ids": ["CD-B"],
                "required_argument_steps": [
                    {"step_id": "S-B", "statement": "使徒和先知共同指向基督。"}
                ],
            },
        ]
    }


def two_section_ledger():
    return [
        {"section_id": "sec-a", "output_anchor": "## 甲節：磐石的身份"},
        {"section_id": "sec-b", "output_anchor": "## 乙節：教會的根基"},
    ]


def two_section_baseline():
    """A verified passing baseline whose findings attribute dimensions to sections."""

    profile = load_json(PROFILE_PATH)
    review = {
        "scope_confirmation": "writing_quality_and_base_preservation",
        "summary": "Both sections preserve the base argument.",
        "dimension_scores": [
            {
                "dimension_id": dimension["id"],
                "score": dimension["weight"],
                "evidence": "fixture",
            }
            for dimension in profile["dimensions"]
        ],
        "hard_failures": [],
        "section_reviews": [
            {
                "section_id": "sec-a",
                "base_step_ids_preserved": ["S-A"],
                "assessment": "Readable prose.",
            },
            {
                "section_id": "sec-b",
                "base_step_ids_preserved": ["S-B"],
                "assessment": "Readable prose.",
            },
        ],
        "findings": [
            {
                "finding_id": "ERF-sec-a-001",
                "dimension_id": "theological_tension_and_attribution",
                "section_id": "sec-a",
                "severity": "low",
                "blocking": False,
                "manuscript_anchor": "彼得是陽性名詞",
                "explanation": "The tension could be attributed more precisely.",
                "recommended_action": "Name the second source once.",
            },
            {
                "finding_id": "ERF-sec-b-001",
                "dimension_id": "base_manuscript_preservation",
                "section_id": "sec-b",
                "severity": "low",
                "blocking": False,
                "manuscript_anchor": "同一個定冠詞之下",
                "explanation": "The inferential bridge is thin.",
                "recommended_action": "Spell the bridge out.",
            },
        ],
    }
    outcome = validate_editorial_review(
        review,
        contract=two_section_contract(),
        manuscript=TWO_SECTION_MANUSCRIPT,
        quality_profile=profile,
    )
    outcome["manuscript_sha256"] = sha256_text(TWO_SECTION_MANUSCRIPT)
    return review, outcome


def test_delta_scope_includes_dimensions_of_changed_sections():
    baseline_review, baseline_outcome = two_section_baseline()
    # The accepted finding only points at 甲節; the revision also rewrote 乙節.
    revised = TWO_SECTION_MANUSCRIPT.replace(
        "以弗所書 2:20 把使徒和先知放在同一個定冠詞之下，共同指向房角石基督。",
        "以弗所書 2:20 把使徒和先知放在同一個定冠詞之下，這見證所指向的中心正是基督。",
        1,
    )
    accepted = [
        {
            "finding_id": "ERF-sec-a-001",
            "dimension_id": "theological_tension_and_attribution",
            "section_id": "sec-a",
            "severity": "low",
            "blocking": False,
            "manuscript_anchor": "彼得是陽性名詞",
            "explanation": "The tension could be attributed more precisely.",
            "recommended_action": "Name the second source once.",
        }
    ]
    packet = build_final_delta_review_packet(
        baseline_review=baseline_review,
        baseline_outcome=baseline_outcome,
        baseline_manuscript=TWO_SECTION_MANUSCRIPT,
        revised_manuscript=revised,
        accepted_findings=accepted,
        dispositions=[
            {"finding_id": "ERF-sec-a-001", "status": "resolved", "note": "named"}
        ],
        quality_profile=load_json(PROFILE_PATH),
        contract=two_section_contract(),
        baseline_sections=two_section_ledger(),
    )

    assert packet["changed_section_ids"] == ["sec-b"]
    affected = {item["id"] for item in packet["affected_dimensions"]}
    # From the accepted finding.
    assert "theological_tension_and_attribution" in affected
    # 乙節's own dimension, which no accepted finding mentioned.
    assert "base_manuscript_preservation" in affected
    # Prose written anew can never inherit its readability score.
    assert "general_reader_readability" in affected
    assert "load_bearing_base_argument_removed_or_reordered" in packet[
        "affected_hard_failures"
    ]


def test_unchanged_section_dimensions_are_still_inherited():
    baseline_review, baseline_outcome = two_section_baseline()
    profile = load_json(PROFILE_PATH)
    revised = TWO_SECTION_MANUSCRIPT.replace(
        "以弗所書 2:20 把使徒和先知放在同一個定冠詞之下，共同指向房角石基督。",
        "以弗所書 2:20 把使徒和先知放在同一個定冠詞之下，這見證所指向的中心正是基督。",
        1,
    )
    accepted = [
        {
            "finding_id": "ERF-sec-b-001",
            "dimension_id": "base_manuscript_preservation",
            "section_id": "sec-b",
            "severity": "low",
            "blocking": False,
            "manuscript_anchor": "同一個定冠詞之下",
            "explanation": "The inferential bridge is thin.",
            "recommended_action": "Spell the bridge out.",
        }
    ]
    packet = build_final_delta_review_packet(
        baseline_review=baseline_review,
        baseline_outcome=baseline_outcome,
        baseline_manuscript=TWO_SECTION_MANUSCRIPT,
        revised_manuscript=revised,
        accepted_findings=accepted,
        dispositions=[
            {"finding_id": "ERF-sec-b-001", "status": "resolved", "note": "expanded"}
        ],
        quality_profile=profile,
        contract=two_section_contract(),
        baseline_sections=two_section_ledger(),
    )
    affected = [item["id"] for item in packet["affected_dimensions"]]
    # 甲節 was not touched, so its attributed dimension is not rescored.
    assert "theological_tension_and_attribution" not in affected

    weights = {item["id"]: item["weight"] for item in profile["dimensions"]}
    delta = {
        "scope_confirmation": "final_delta_writing_quality",
        "reviewed_manuscript_sha256": packet["manuscript_sha256"],
        "summary": "The rewritten section is verified.",
        "dimension_scores": [
            {
                "dimension_id": dimension_id,
                "score": weights[dimension_id],
                "evidence": "Verified in the supplied changed paragraph.",
            }
            for dimension_id in affected
        ],
        "hard_failure_assessments": [
            {"failure_id": failure_id, "failed": False, "evidence": "Not present."}
            for failure_id in packet["affected_hard_failures"]
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
    assert "theological_tension_and_attribution" in merged["score_provenance"][
        "inherited_dimensions"
    ]
    assert outcome["passed"] is True


def _delta_packet_for(dimension_id, *, source_slice=None):
    baseline_review, baseline_outcome, manuscript = verified_baseline()
    revised = manuscript.replace("進一步的線索", "更清楚的線索", 1)
    return build_final_delta_review_packet(
        baseline_review=baseline_review,
        baseline_outcome=baseline_outcome,
        baseline_manuscript=manuscript,
        revised_manuscript=revised,
        accepted_findings=[
            {
                "finding_id": "ERF-test-001",
                "dimension_id": dimension_id,
                "section_id": "matt16-18-rock",
                "severity": "low",
                "blocking": False,
                "manuscript_anchor": "進一步的線索",
                "explanation": "Needs work.",
                "recommended_action": "Fix it.",
            }
        ],
        dispositions=[
            {"finding_id": "ERF-test-001", "status": "resolved", "note": "done"}
        ],
        quality_profile=load_json(PROFILE_PATH),
        contract=contract(),
        baseline_sections=valid_author_result()["sections"],
        source_slice=source_slice,
    )


def test_delta_packet_carries_the_slice_only_for_the_dimensions_that_need_it():
    """A dimension must not be scored against the sources in round one and
    against the manuscript alone in round two. When one of the source-judged
    three is rescored the delta reviewer gets the same slice; when the revision
    only touched prose, sending it would just crowd the budget."""

    slice_payload = {"base_manuscript_exegesis": [{"source_id": "s", "sentence": "原文作 πέτρα。"}]}

    rescores_a_source_dimension = _delta_packet_for(
        "source_and_exegesis", source_slice=slice_payload
    )
    affected = {item["id"] for item in rescores_a_source_dimension["affected_dimensions"]}
    assert "source_and_exegesis" in affected
    assert rescores_a_source_dimension["source_slice"] == slice_payload

    prose_only = _delta_packet_for("general_reader_readability", source_slice=slice_payload)
    affected = {item["id"] for item in prose_only["affected_dimensions"]}
    assert not affected & {
        "source_and_exegesis",
        "base_manuscript_preservation",
        "theological_tension_and_attribution",
    }
    assert "source_slice" not in prose_only

    assert "source_slice" not in _delta_packet_for("source_and_exegesis")


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
        baseline_sections=valid_author_result()["sections"],
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
            baseline_sections=valid_author_result()["sections"],
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
        # These tests assert reviewer-call counts and caching; the grounding
        # gate is a separate Claude call with its own dedicated tests below.
        "skip_grounding_gate": True,
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
        skip_grounding_gate=True,
        program_audit_manifest_path=tmp_path / "template.json",
        program_audit_draft_id="DRAFT-1",
    )

    assert outcome["status"] == "workflow_published"
    assert outcome["editorial_status"] == "editorial_pass_no_revision"
    assert outcome["program_audit"]["manuscript_sha256"] == manuscript_sha


def test_compiled_snapshot_is_kept_as_a_run_artifact_without_moving_the_fingerprint(tmp_path):
    """Regression: the snapshot compiled from the store lived only inside the
    packet builder's temporary directory, which is deleted when it returns.
    The Program Audit copies that file at the end of the run, so the whole
    `--plan-id` without `--knowledge` path -- the one the session guide tells
    a new article to use -- died on `shutil.copyfile(None, ...)` after every
    model call had been paid for.

    Keeping it must not move packet_sha256, or every existing generation
    cache would miss and the run would re-call every model.
    """

    store = _store_from_migrated_contract()
    snapshot_path = tmp_path / "run" / "compiled-knowledge-snapshot.json"
    kwargs = dict(
        plan_id="CP-matthew-16-13-20",
        store=store,
        publication_profile_path=PUBLICATION_PROFILE_PATH,
        quality_profile_path=PROFILE_PATH,
    )
    kept = build_authoring_packet_from_store(compiled_snapshot_path=snapshot_path, **kwargs)
    ephemeral = build_authoring_packet_from_store(**kwargs)

    assert snapshot_path.is_file()
    assert kept["packet_sha256"] == ephemeral["packet_sha256"]
    assert kept["sources"]["knowledge"]["compiled"] is True
    assert "path" not in kept["sources"]["knowledge"]

    written = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert "compiled_at" not in written, "wall-clock stamp would break the cache"
    # The audit reads this file directly, so it has to carry the collections
    # the audit looks the draft's plan and claims up in.
    assert {"product_plans", "claims", "evidence_steps"} <= set(written)


def test_program_audit_without_a_knowledge_snapshot_fails_before_any_model_call(tmp_path):
    """The snapshot is only *used* on a passing editorial path, at the end of
    the run. A missing one must not be discovered there.
    """

    openai = FakeClient([valid_author_result()], model="fake-openai")
    claude = FakeClient([passing_review()], model="fake-claude")

    with pytest.raises(AuthoringContractError, match="knowledge snapshot"):
        run_authoring(
            plan_path=PLAN_PATH,
            knowledge_path=None,
            contract_path=FIXTURE_DIR / "base-manuscript-contract.json",
            publication_profile_path=PUBLICATION_PROFILE_PATH,
            quality_profile_path=PROFILE_PATH,
            output_dir=tmp_path,
            openai_client=openai,
            claude_client=claude,
            skip_grounding_gate=True,
            program_audit_manifest_path=tmp_path / "template.json",
            program_audit_draft_id="DRAFT-1",
        )
    assert openai.calls == 0 and claude.calls == 0


def test_half_specified_program_audit_fails_before_any_model_call(tmp_path):
    openai = FakeClient([valid_author_result()], model="fake-openai")
    claude = FakeClient([passing_review()], model="fake-claude")

    with pytest.raises(AuthoringContractError, match="manifest path and draft_id"):
        run_authoring(
            plan_path=PLAN_PATH,
            knowledge_path=KNOWLEDGE_PATH,
            contract_path=FIXTURE_DIR / "base-manuscript-contract.json",
            publication_profile_path=PUBLICATION_PROFILE_PATH,
            quality_profile_path=PROFILE_PATH,
            output_dir=tmp_path,
            openai_client=openai,
            claude_client=claude,
            skip_grounding_gate=True,
            program_audit_manifest_path=tmp_path / "template.json",
        )
    assert openai.calls == 0 and claude.calls == 0


def test_program_audit_stage_copies_the_snapshot_it_was_given(tmp_path):
    """The stage that used to receive None. Exercised unmocked, because the
    only existing program-audit test replaces this function wholesale.
    """

    template_path = tmp_path / "template.json"
    template_path.write_text(
        json.dumps(
            {
                "schema_version": "editorial-draft-manifest.v1",
                "drafts": [{"draft_id": "DRAFT-1", "audit_config": {}}],
            }
        ),
        encoding="utf-8",
    )
    sections = valid_author_result()["sections"]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "backend.pipeline.matthew_exposition_authoring_runner.write_editorial_draft_audit",
            lambda manifest_path, draft_id: _stub_audit_output(manifest_path),
        )
        audit = _run_program_audit_stage(
            template_path=template_path,
            draft_id="DRAFT-1",
            knowledge_path=KNOWLEDGE_PATH,
            output_dir=tmp_path,
            manuscript="# 標題\n\n段落。\n",
            manuscript_sections=sections,
        )

    staged = tmp_path / "program-audit/knowledge-snapshot.json"
    assert staged.is_file()
    assert json.loads(staged.read_text(encoding="utf-8")) == load_json(KNOWLEDGE_PATH)
    assert audit["status"] == "pass"


def test_audit_manifest_precheck_rejects_a_draft_id_it_cannot_name(tmp_path):
    """`_build_program_audit_manifest` already refuses a draft_id it cannot
    find exactly once, but it only runs after the article has passed review.
    The same check at parse time costs one file read.
    """

    manifest_path = tmp_path / "template.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "editorial-draft-manifest.v1",
                "drafts": [{"draft_id": "DRAFT-1"}, {"draft_id": "DRAFT-1"}],
            }
        ),
        encoding="utf-8",
    )
    parser = argparse.ArgumentParser()
    for draft_id in ("DRAFT-2", "DRAFT-1"):  # absent, then duplicated
        with pytest.raises(SystemExit):
            _require_audit_draft(parser, manifest_path, draft_id)

    manifest_path.write_text("not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        _require_audit_draft(parser, manifest_path, "DRAFT-1")


def _stub_audit_output(manifest_path):
    """Write the audit result `_run_program_audit_stage` reads back."""

    audit_path = Path(manifest_path).parent / "program-audit.json"
    audit_path.write_text(
        json.dumps({"status": "pass", "summary": {"error_total": 0, "warning_total": 0}}),
        encoding="utf-8",
    )
    return audit_path


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
        skip_grounding_gate=True,
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
        skip_grounding_gate=True,
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
        skip_grounding_gate=True,
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


def _profile():
    return load_json(PROFILE_PATH)


def _review_scoring(pastoral_score):
    review = passing_review()
    for item in review["dimension_scores"]:
        if item["dimension_id"] == "pastoral_theological_landing":
            item["score"] = pastoral_score
    return review


def test_a_contract_that_forbids_an_application_chain_puts_the_pastoral_dimension_out_of_scope():
    from backend.pipeline.matthew_exposition_authoring import out_of_scope_dimensions

    contract_value = contract()
    contract_value["sections"][0]["ineligible_operations"].append(
        "invent_life_application_chain"
    )
    scoped_out = out_of_scope_dimensions(contract_value)
    assert "pastoral_theological_landing" in scoped_out
    assert "invent_life_application_chain" in scoped_out["pastoral_theological_landing"]


def test_out_of_scope_dimension_is_excluded_from_the_total_not_awarded():
    """生活應用 is optional; omitting one the material cannot support must not
    cost the article points -- but awarding the weight instead would score it
    the same as an article that wrote an excellent application. The dimension
    was not measured, so it contributes to neither side.
    """
    review = _review_scoring(1)
    scored = evaluate_editorial_review(review, _profile())
    excluded = evaluate_editorial_review(
        review, _profile(), {"pastoral_theological_landing": "contract forbids it"}
    )
    # The one point it did score is removed from the numerator...
    assert excluded["total_score"] == scored["total_score"] - 1
    # ...and its weight from what was measured.
    assert excluded["applicable_weight"] == 95
    assert excluded["not_applicable_dimensions"] == {
        "pastoral_theological_landing": "contract forbids it"
    }


def test_excluding_a_dimension_leaves_the_others_judged_as_they_were():
    """Exclusion removes a dimension from what was measured and from nothing
    else. With the bar set per dimension there is no total for an excluded
    weight to drag around, so the remaining nine pass or fail on their own."""

    profile = _profile()
    minimums = {item["id"]: item["minimum"] for item in profile["dimensions"]}
    review = passing_review()
    for item in review["dimension_scores"]:
        item["score"] = minimums[item["dimension_id"]]

    scored = evaluate_editorial_review(review, profile)
    excluded = evaluate_editorial_review(
        review, profile, {"pastoral_theological_landing": "contract forbids it"}
    )
    assert scored["passed"] is True
    assert excluded["passed"] is True
    assert excluded["applicable_weight"] == 95
    assert (
        excluded["total_score"]
        == scored["total_score"] - minimums["pastoral_theological_landing"]
    )

    # A failure elsewhere is unaffected by the exclusion.
    for item in review["dimension_scores"]:
        if item["dimension_id"] == "approved_written_style":
            item["score"] -= 1
    still_failing = evaluate_editorial_review(
        review, profile, {"pastoral_theological_landing": "contract forbids it"}
    )
    assert still_failing["passed"] is False
    assert still_failing["hard_gate_failures"] == ["approved_written_style"]


def test_out_of_scope_dimension_cannot_fail_its_minimum():
    review = _review_scoring(0)
    outcome = evaluate_editorial_review(
        review, _profile(), {"pastoral_theological_landing": "contract forbids it"}
    )
    assert "pastoral_theological_landing" not in outcome["hard_gate_failures"]


def test_scoring_rejects_an_unknown_not_applicable_dimension():
    with pytest.raises(AuthoringContractError, match="not-applicable"):
        evaluate_editorial_review(passing_review(), _profile(), {"no_such_dimension": "x"})


def test_a_failing_fresh_review_must_say_what_to_change():
    review = passing_review()
    for item in review["dimension_scores"]:
        item["score"] = 0
    review["findings"] = []
    with pytest.raises(AuthoringContractError, match="blocking finding"):
        validate_editorial_review(
            review,
            contract=contract(),
            manuscript=valid_author_result()["manuscript_markdown"],
            quality_profile=_profile(),
        )


def test_an_inherited_review_may_fail_with_nothing_blocking_left():
    """Below the threshold with no must-fix findings is a real state for a
    merged review carried into a later round; the runner stops for a human.
    """
    review = passing_review()
    for item in review["dimension_scores"]:
        item["score"] = 0
    review["findings"] = []
    outcome = validate_editorial_review(
        review,
        contract=contract(),
        manuscript=valid_author_result()["manuscript_markdown"],
        quality_profile=_profile(),
        require_blocking_finding_when_failing=False,
    )
    assert outcome["passed"] is False


def test_grounding_failure_is_repaired_and_rechecked_before_giving_up(tmp_path):
    """The gate runs before the writing reviewer, so without a repair path a
    single overreaching sentence would discard the whole draft.
    """
    class Grounder:
        """Flags the first sentence of whichever paragraph it is given, so the
        quoted assertion is always a real substring of that paragraph."""

        def __init__(self, passes_after):
            self.model = "fake-claude"
            self.calls = 0
            self.passes_after = passes_after

        def generate_json(self, _prompt, packet, _schema, **_kwargs):
            self.calls += 1
            if "dimension_scores" in str(_schema):
                return passing_review()
            text = json.loads(packet)["paragraph_text"]
            if self.calls > self.passes_after:
                return _grounding()
            return _grounding([text.strip().splitlines()[0][:10]])

    # The repair rewrites the paragraphs it was given findings on, so the
    # recheck asks about different prose. It must: an unchanged paragraph now
    # keeps the verdict it was already given, because re-asking a
    # byte-identical packet was returning a different answer and no repair
    # round could converge.
    repaired = valid_author_result()
    repaired["manuscript_markdown"] = "\n".join(
        line + "（已依 grounding 意見修正）"
        if line.strip() and not line.startswith(("#", ">", "<!--", "[^"))
        else line
        for line in repaired["manuscript_markdown"].splitlines()
    )
    openai = FakeClient([valid_author_result(), repaired], model="fake-openai")
    claude = Grounder(passes_after=3)
    result = run_authoring(
        packet=full_authoring_packet(),
        plan_path=PLAN_PATH,
        knowledge_path=KNOWLEDGE_PATH,
        contract_path=FIXTURE_DIR / "base-manuscript-contract.json",
        publication_profile_path=PUBLICATION_PROFILE_PATH,
        quality_profile_path=PROFILE_PATH,
        output_dir=tmp_path / "out",
        openai_client=openai,
        claude_client=claude,
    )
    assert result["status"] == "editorial_pass_no_revision"
    assert openai.calls == 2, "author drafted once, then repaired once"
    assert (tmp_path / "out" / "grounding-repair-01.json").is_file()


def test_grounding_repair_is_bounded(tmp_path):
    class AlwaysFails:
        model = "fake-claude"
        calls = 0

        def generate_json(self, _prompt, packet, _schema):
            AlwaysFails.calls += 1
            text = json.loads(packet)["paragraph_text"]
            return _grounding([text.strip().splitlines()[0][:10]])

    openai = FakeClient([valid_author_result()] * 5, model="fake-openai")
    claude = AlwaysFails()
    result = run_authoring(
        packet=full_authoring_packet(),
        plan_path=PLAN_PATH,
        knowledge_path=KNOWLEDGE_PATH,
        contract_path=FIXTURE_DIR / "base-manuscript-contract.json",
        publication_profile_path=PUBLICATION_PROFILE_PATH,
        quality_profile_path=PROFILE_PATH,
        output_dir=tmp_path / "out",
        openai_client=openai,
        claude_client=claude,
        max_grounding_attempts=2,
    )
    assert result["status"] == "grounding_gate_failed"
    assert result["grounding_attempts"] == 2


def _review_with_hard_failure():
    review = passing_review()
    review["hard_failures"] = ["exegetical_observation_inference_conclusion_chain_missing"]
    review["findings"] = [
        {
            "finding_id": "F-1",
            "dimension_id": "exegetical_reasoning",
            "section_id": "matt16-18-rock",
            "severity": "high",
            "blocking": True,
            "manuscript_anchor": "彼得是 *Petros*",
            "explanation": "推論鏈跳步。",
            "recommended_action": "補上橋樑。",
        }
    ]
    return review


def test_a_hard_failure_falls_with_the_finding_that_evidenced_it():
    """Otherwise the run deadlocks: adjudication rejects the finding, so
    nothing is left to revise, but the veto it rested on still blocks
    publication -- every draft ends at human review however good it is.
    """
    from backend.pipeline.matthew_exposition_authoring import hard_failures_after_adjudication

    kept, withdrawn = hard_failures_after_adjudication(
        _review_with_hard_failure(), withdrawn_finding_ids={"F-1"}
    )
    assert kept == []
    assert "exegetical_observation_inference_conclusion_chain_missing" in withdrawn
    assert "F-1" in withdrawn["exegetical_observation_inference_conclusion_chain_missing"]


def test_a_hard_failure_stands_while_any_of_its_findings_was_accepted():
    from backend.pipeline.matthew_exposition_authoring import hard_failures_after_adjudication

    review = _review_with_hard_failure()
    review["findings"].append({**review["findings"][0], "finding_id": "F-2"})
    kept, withdrawn = hard_failures_after_adjudication(review, withdrawn_finding_ids={"F-1"})
    assert kept == ["exegetical_observation_inference_conclusion_chain_missing"]
    assert withdrawn == {}


def test_a_hard_failure_with_no_finding_behind_it_is_left_alone():
    """Nothing was adjudicated, so there is nothing to overturn: a safety
    declaration must not evaporate for lack of paperwork.
    """
    from backend.pipeline.matthew_exposition_authoring import hard_failures_after_adjudication

    review = _review_with_hard_failure()
    review["findings"] = []
    kept, withdrawn = hard_failures_after_adjudication(review, withdrawn_finding_ids={"F-1"})
    assert kept == ["exegetical_observation_inference_conclusion_chain_missing"]
    assert withdrawn == {}


def test_a_grounding_repair_does_not_destroy_the_author_artifact(tmp_path):
    """Regression: a repair recurses into the same output directory and wrote
    its seeded result over `authoring.json`. That file's fingerprint is what
    lets a re-invocation skip the author call, and the seed's fingerprint is
    keyed on the seed manuscript instead, so a fresh run never matched and
    re-drafted the whole article. One interrupted run cost six full drafts.
    """

    class Grounder:
        def __init__(self):
            self.model = "fake-claude"
            self.calls = 0

        def generate_json(self, _prompt, packet, _schema, **_kwargs):
            self.calls += 1
            if "dimension_scores" in str(_schema):
                return passing_review()
            text = json.loads(packet)["paragraph_text"]
            if "已修正" in text:
                return _grounding()
            return _grounding([text.strip().splitlines()[0][:10]])

    repaired = valid_author_result()
    repaired["manuscript_markdown"] = "\n".join(
        line + "（已修正）"
        if line.strip() and not line.startswith(("#", ">", "<!--", "[^"))
        else line
        for line in repaired["manuscript_markdown"].splitlines()
    )
    out = tmp_path / "out"
    openai = FakeClient([valid_author_result(), repaired], model="fake-openai")
    run_authoring(
        packet=full_authoring_packet(),
        plan_path=PLAN_PATH,
        knowledge_path=KNOWLEDGE_PATH,
        contract_path=FIXTURE_DIR / "base-manuscript-contract.json",
        publication_profile_path=PUBLICATION_PROFILE_PATH,
        quality_profile_path=PROFILE_PATH,
        output_dir=out,
        openai_client=openai,
        claude_client=Grounder(),
    )

    author_artifact = json.loads((out / "authoring.json").read_text(encoding="utf-8"))
    assert author_artifact["generation"]["role"] == "author", (
        "the author's own artifact must survive the repair that follows it"
    )
    assert "已修正" not in author_artifact["result"]["manuscript_markdown"]
    seeded = out / "authoring-grounding-02.json"
    assert seeded.is_file(), "the repair's seed keeps its own path"
    assert json.loads(seeded.read_text(encoding="utf-8"))["generation"]["role"] == (
        "revision_round_seed"
    )


def test_review_packet_shows_scoped_material_the_manuscript_left_unused():
    """Regression: the reviewer scored `pastoral_theological_landing` 3 of 5,
    could name no material for a landing, and proposed a discipleship
    application instead. Adjudication rejected it for citing no evidence --
    correctly -- and the reviewer withdrew, both concluding the passage had no
    application to make. Four claims of `claim_type: "application"` were in the
    author's packet unused, and the review packet carried no claims at all, so
    neither agent could look.

    Only the uncited ones are sent. The full set is 13KB against a 40KB budget,
    and what the manuscript used is already in the prose in front of the
    reviewer.
    """

    packet = full_authoring_packet()
    author = valid_author_result()
    manuscript = author["manuscript_markdown"]
    scoped = {item["claim_id"] for item in packet["knowledge"]["claims"]}
    cited = {claim_id for claim_id in scoped if claim_id in manuscript}
    assert cited, "fixture must cite at least one scoped claim"
    assert scoped - cited, "fixture must leave at least one scoped claim unused"

    review_packet = build_editorial_review_packet(
        authoring_packet=packet, author_result=author
    )
    unused = review_packet["unused_scoped_claims"]
    ids = {item["claim_id"] for item in unused}

    assert ids == scoped - cited, (
        "exactly the scoped material the manuscript did not cite -- what it did "
        "cite is already in the prose in front of the reviewer"
    )
    assert all(item.get("statement") for item in unused), (
        "an id alone tells the reviewer nothing about whether it could land"
    )
    assert len(canonical_json(review_packet).encode()) <= EDITORIAL_REVIEW_PACKET_MAX_BYTES


def test_the_manuscript_that_publishes_is_ground_checked(tmp_path):
    """Regression: the gate ran before the writing reviewer, on the author's
    draft. The revision then rewrote prose to satisfy editorial findings and
    nothing re-checked it, so the manuscript that actually published had never
    passed the gate. A real run's publishable draft was four rewritten
    paragraphs deep and one of them overreached; it was caught only because the
    check was run by hand.

    The finishing check runs on every path that finishes an editorial pass, and
    writes its own report rather than overwriting the author's.
    """

    class Grounder:
        model = "fake-claude"

        def generate_json(self, _prompt, packet, schema, **_kwargs):
            if "dimension_scores" in str(schema):
                return passing_review()
            return _grounding()

    out = tmp_path / "out"
    result = run_authoring(
        packet=full_authoring_packet(),
        plan_path=PLAN_PATH,
        knowledge_path=KNOWLEDGE_PATH,
        contract_path=FIXTURE_DIR / "base-manuscript-contract.json",
        publication_profile_path=PUBLICATION_PROFILE_PATH,
        quality_profile_path=PROFILE_PATH,
        output_dir=out,
        openai_client=FakeClient([valid_author_result()], model="fake-openai"),
        claude_client=Grounder(),
    )

    assert result["status"] == "editorial_pass_no_revision"
    assert (out / "final-grounding-report.json").is_file(), (
        "the manuscript being finished must be checked, not assumed"
    )
    assert (out / "grounding-report.json").is_file(), (
        "the author's own report is not overwritten by the finishing check"
    )


def test_a_finishing_manuscript_that_overreaches_does_not_reach_publication(tmp_path):
    """The finishing check has to be able to stop the run, not merely report."""

    class Grounder:
        model = "fake-claude"

        def __init__(self):
            self.seen = 0

        def generate_json(self, _prompt, packet, schema, **_kwargs):
            if "dimension_scores" in str(schema):
                return passing_review()
            self.seen += 1
            # Clean while the author's draft is checked; the finishing check
            # asks about the same paragraphs and is answered from cache, so
            # flag every paragraph to stand in for a revision that overreached.
            text = json.loads(packet)["paragraph_text"]
            return _grounding([text.strip().splitlines()[0][:10]])

    out = tmp_path / "out"
    result = run_authoring(
        packet=full_authoring_packet(),
        plan_path=PLAN_PATH,
        knowledge_path=KNOWLEDGE_PATH,
        contract_path=FIXTURE_DIR / "base-manuscript-contract.json",
        publication_profile_path=PUBLICATION_PROFILE_PATH,
        quality_profile_path=PROFILE_PATH,
        output_dir=out,
        openai_client=FakeClient(
            [valid_author_result(), valid_author_result()], model="fake-openai"
        ),
        claude_client=Grounder(),
        max_grounding_attempts=1,
    )
    assert result["status"] == "grounding_gate_failed"
    assert "draft_path" not in result, "an ungrounded manuscript never reaches publication"


def test_the_final_check_reuses_what_the_first_one_already_answered(tmp_path):
    """Only what the revision rewrote costs anything: the two checks share one
    cache, so a manuscript the revision did not touch is free to re-verify.
    """

    class Counting:
        model = "fake-claude"

        def __init__(self):
            self.paragraph_calls = 0

        def generate_json(self, _prompt, packet, schema, **_kwargs):
            if "dimension_scores" in str(schema):
                return passing_review()
            self.paragraph_calls += 1
            return _grounding()

    claude = Counting()
    out = tmp_path / "out"
    run_authoring(
        packet=full_authoring_packet(),
        plan_path=PLAN_PATH,
        knowledge_path=KNOWLEDGE_PATH,
        contract_path=FIXTURE_DIR / "base-manuscript-contract.json",
        publication_profile_path=PUBLICATION_PROFILE_PATH,
        quality_profile_path=PROFILE_PATH,
        output_dir=out,
        openai_client=FakeClient([valid_author_result()], model="fake-openai"),
        claude_client=claude,
    )
    checked = len(list((out / "grounding-cache").iterdir()))
    assert claude.paragraph_calls == checked, (
        "the finishing check asked nothing the author's check had already answered"
    )


def test_a_contract_no_longer_has_to_claim_it_was_confirmed():
    """The `editor_confirmed` gate was residual from an early version. What it
    had become was a check that a string was non-empty: `status` came from
    `contract_confirmed_by`, which came from a migration script's
    `--confirmed-by` argument. All three Matthew plans carry `junyang168` and a
    round `00:00:00` timestamp, and that editor had never seen the contracts.
    A gate that certifies nothing reads as though the system guarantees a human
    looked, which is worse than having no gate at all.
    """

    contract = json.loads(
        (FIXTURE_DIR / "base-manuscript-contract.json").read_text(encoding="utf-8")
    )
    contract = contract.get("result", contract)
    contract.pop("status", None)
    validate_base_contract(contract, verify_source=False)
