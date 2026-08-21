"""The bench's own arithmetic, without calling a model.

Every column here was previously produced by a person reading a package and
typing a number into a table. These tests are the part that makes the table
reproducible; the models are not.
"""

from __future__ import annotations

import json

import pytest

from backend.pipeline import extraction_model_bench as bench


def package(**overrides):
    base = {
        "observations": [
            {"observation_id": "OBS1", "argument_role": "load_bearing",
             "statement": "耶穌囑咐門徒不可對人說他是基督。"},
            {"observation_id": "OBS2", "argument_role": "load_bearing",
             "statement": "彼得在該撒利亞腓立比宣認耶穌為基督。"},
            {"observation_id": "OBS3", "argument_role": "background",
             "statement": "這段記載出現在馬太福音。"},
        ],
        "evidence_steps": [{"evidence_step_id": "E1", "statement": "門徒尚未明白使命。"}],
        "claims": [{"claim_id": "CL1", "title": "彌賽亞的使命是受苦。"}],
        "knowledge_relations": [{"from_id": "OBS1", "to_id": "E1"}],
        "coverage": {"by_category": {"prose": {"represented": 128, "total": 132}},
                     "unprocessed": 11, "fragments_unplaced": 0},
        "sections": [{"index": 1, "attempts": 3, "cached": False},
                     {"index": 2, "attempts": 1, "cached": False}],
        "usage": [
            {"attempt": 1, "prompt_tokens": 1000, "cached_tokens": 0, "completion_tokens": 500},
            {"attempt": 2, "prompt_tokens": 1000, "cached_tokens": 900, "completion_tokens": 500},
        ],
        "extraction": {"model_id": "kimi-k3"},
    }
    base.update(overrides)
    return base


def test_counts_and_coverage_come_from_the_package():
    row = bench.row_from_package(package(), model="kimi-k3",
                                 reasoning_effort="low", elapsed_seconds=1.0)

    assert (row.observations, row.evidence_steps, row.claims) == (3, 1, 1)
    assert row.load_bearing == 2
    assert (row.prose_represented, row.prose_total) == (128, 132)
    assert row.fragments_unplaced == 0


def test_a_load_bearing_observation_nothing_points_at_is_an_orphan():
    """OBS2 is marked as carrying the argument and is connected to nothing.

    This is the column that separates "looks thorough" from "is wired up": a
    package can reach every sentence and still leave its load-bearing claims
    unsupported.
    """

    row = bench.row_from_package(package(), model="kimi-k3",
                                 reasoning_effort="low", elapsed_seconds=1.0)

    assert row.orphans == 1


def test_retries_count_tries_after_the_first():
    """Three attempts on one section is two retries, not three."""

    row = bench.row_from_package(package(), model="kimi-k3",
                                 reasoning_effort="low", elapsed_seconds=1.0)

    assert row.retries == 2


def test_cost_sums_rejected_attempts_and_discounts_cached_input():
    row = bench.row_from_package(package(), model="kimi-k3",
                                 reasoning_effort="low", elapsed_seconds=1.0)

    assert row.prompt_tokens == 2000
    assert row.cached_tokens == 900
    assert row.completion_tokens == 1000
    # 1100 fresh @2.78 + 900 cached @0.28 + 1000 output @13.89, per million.
    assert row.cost_usd == pytest.approx(
        (1100 * 2.78 + 900 * 0.28 + 1000 * 13.89) / 1_000_000, rel=1e-6
    )
    assert row.cost_complete is True


def test_a_cached_section_makes_the_cost_incomplete_rather_than_cheap():
    """The trap this bench would otherwise walk into.

    A replayed section contributes its observations and no usage row, so the
    objects are counted while the tokens that produced them are not. Left
    alone, `price_usage` would return a smaller number with no hint that it
    covers three quarters of the work -- and a fully cached run would price at
    exactly 0.0, which is a real answer for a stage that calls no model.
    """

    row = bench.row_from_package(
        package(sections=[{"index": 1, "attempts": 0, "cached": True},
                          {"index": 2, "attempts": 1, "cached": False}]),
        model="kimi-k3", reasoning_effort="low", elapsed_seconds=1.0,
    )

    assert row.cached_sections == 1
    assert row.cost_complete is False
    assert any("cache" in note for note in row.notes)


def test_an_unpriced_model_is_not_a_free_one():
    row = bench.row_from_package(
        package(extraction={"model_id": "qwen3-max"}),
        model="qwen3-max", reasoning_effort="low", elapsed_seconds=1.0,
    )

    assert row.cost_usd is None
    assert row.cost_complete is False
    assert any("unpriced" in note for note in row.notes)


def test_simplified_text_is_counted_and_traditional_text_is_not():
    converter = bench._simplified_converter()
    if converter is None:
        pytest.skip("OpenCC unavailable")

    assert bench.script_counts(["這節經文記載"], converter) == (6, 0)
    trad, simp = bench.script_counts(["这节经文记载"], converter)
    assert simp == 5 and trad + simp == 6


@pytest.mark.parametrize("phrase", ["征服羅馬", "耶穌不准門徒宣布", "彌賽亞秘密",
                                    "台大電機", "只有神才有的權柄", "我名叫群"])
def test_traditional_text_opencc_would_rewrite_is_not_simplified(phrase):
    """Every one of these came out of a real package and was miscounted.

    OpenCC normalises toward one Traditional standard, so it rewrites 不准 to
    不準 and 秘 to 祕. Counting that as 简体 reported twenty simplified
    characters in a package that had none -- worse than the hand count it
    replaced, because a library produced it.
    """

    converter = bench._simplified_converter()
    if converter is None:
        pytest.skip("OpenCC unavailable")

    _trad, simp = bench.script_counts([phrase], converter)
    assert simp == 0


def test_context_decides_a_one_to_many_mapping():
    """征 alone converts to 徵; 征服 does not. Whole strings, not characters."""

    converter = bench._simplified_converter()
    if converter is None:
        pytest.skip("OpenCC unavailable")

    assert bench.script_counts(["征服羅馬壓迫"], converter) == (6, 0)


def test_mixed_script_is_what_the_column_exists_to_catch():
    """DeepSeek's failure: anchors in 繁體, statements in 简体."""

    converter = bench._simplified_converter()
    if converter is None:
        pytest.skip("OpenCC unavailable")
    trad, simp = bench.script_counts(["這節經文", "这节经文"], converter)

    assert trad > 0 and simp > 0


def test_a_model_that_fails_is_a_row_not_a_dead_run(monkeypatch, tmp_path):
    """One bad candidate must not cost the other models their results."""

    def explode(*_args, **_kwargs):
        raise RuntimeError("engine overloaded")

    monkeypatch.setattr(bench, "_bench_client", explode)
    rows = bench.bench_source(
        {"source_id": "S", "source_type": "notes_manuscript", "source_path": "x.md"},
        models=["kimi-k3", "gpt-5.6-sol"], output_dir=tmp_path,
    )

    assert [r.model for r in rows] == ["kimi-k3", "gpt-5.6-sol"]
    assert all("engine overloaded" in r.error for r in rows)
    assert "—" in bench.render_markdown(rows)


def test_markdown_marks_an_incomplete_cost_so_the_table_cannot_mislead():
    row = bench.row_from_package(
        package(sections=[{"index": 1, "attempts": 0, "cached": True}]),
        model="kimi-k3", reasoning_effort="low", elapsed_seconds=1.0,
    )
    table = bench.render_markdown([row])

    assert "*" in table
    assert "cost does not cover the whole source" in table


def test_a_candidate_needs_no_production_registry_entry():
    """The whole point: evaluating a model must not require editing production."""

    from backend.pipeline.detailed_knowledge_extraction_runner import MODEL_BACKENDS

    assert "gemini" in bench.CANDIDATES
    assert "gemini" not in MODEL_BACKENDS


def test_json_object_client_sends_the_token_cap_deepseek_actually_reads():
    """`max_completion_tokens` is accepted and ignored; `max_tokens` is obeyed.

    Measured on deepseek-v4-flash: asked for 200 via max_completion_tokens it
    returned 971 and stopped on its own; asked via max_tokens it returned
    exactly 200 with finish_reason `length`. A cap that is silently dropped is
    how one section produced 40,938 completion tokens against a 16,000 budget.
    """

    assert bench.JsonObjectClient.token_limit_param == "max_tokens"


def test_deepseek_falls_back_to_json_object_because_json_schema_is_disabled():
    """Not a preference -- v4-flash and v4-pro reject the type outright."""

    assert "deepseek" in bench.JSON_OBJECT_FALLBACK


def test_json_mode_prompt_meets_deepseek_documented_requirements():
    """The guide requires the word `json` and a sample of the wanted shape."""

    assert "json" in bench._JSON_MODE_INSTRUCTION
    assert "{schema}" in bench._JSON_MODE_INSTRUCTION
