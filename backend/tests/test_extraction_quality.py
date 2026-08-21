"""Proposition recall: the column that survived when the others could not rank.

Prose coverage was 24-25/132 for four different models and orphans were 0 for
all of them, so neither could separate them. Claim count could, but it was
separating granularity: 15 atomic claims and 6 compound ones can carry the
same argument.
"""

from __future__ import annotations

import json

import pytest

from backend.pipeline.extraction_quality import (
    GOLD_DIR, GoldSet, Proposition, score_package,
)


def gold(*propositions: Proposition) -> GoldSet:
    return GoldSet(gold_id="t", source_id="s", section=1, propositions=propositions)


P_SECRECY = Proposition(
    id="P01", text="保密命令有合理原因",
    match=(("保密",), ("合理",), ("原因",)),
)


def test_a_proposition_needs_one_term_from_every_group():
    """AND of ORs. 可4:11 說明 and 可4:11 平行記載 must not match each other."""

    explain = Proposition(id="P09", text="可4:11 是啟示說明而非保密請求",
                          match=(("4:11",), ("啟示",), ("請求", "並非保密")))

    assert explain.matches("可4:11 並非保密的請求，而是神聖啟示的說明。")
    assert not explain.matches("可4:11 此段亦見於太13:11-17，並非馬可獨有。")


def test_a_compound_claim_scores_the_same_as_several_atomic_ones():
    """The whole reason this exists.

    gpt-5.6-sol states 可1:43-45's practical reason and its synoptic parallels
    as two claims; gemini-3.7-flash joins them with 且. Counting objects makes
    that a 2:1 difference; counting propositions makes it a tie.
    """

    reason = Proposition(id="A", text="有實際原因", match=(("1:43",), ("實際原因",)))
    parallel = Proposition(id="B", text="非馬可發明", match=(("平行", "太"), ("發明", "虛構")))
    g = gold(reason, parallel)

    atomic = {"claims": [{"title": "可1:43-45 的保密命令有明確的實際原因。"},
                         {"title": "可1:43-45 亦見於太 8:1-4，並非馬可的發明。"}]}
    compound = {"claims": [{"title": "可1:43-45 的保密命令有明確的實際原因，"
                                     "且共觀平行記載證明其非馬可虛構。"}]}

    assert score_package(atomic, g).claim_recall == 1.0
    assert score_package(compound, g).claim_recall == 1.0


def test_a_step_a_claim_links_to_counts_as_delivered():
    """Authoring starts at a claim and walks `evidence_step_ids` to the steps.

    So a proposition in a linked step is reachable, not lost. Scoring by which
    array it landed in instead reported one model at 5 of 18 when 16 of 18 were
    reachable -- a filing decision read as a capability gap.
    """

    package = {
        "claims": [{"title": "與此無關的結論。", "evidence_step_ids": ["E1"]}],
        "evidence_steps": [
            {"evidence_step_id": "E1",
             "statement": "耶穌確曾命令人保密，但皆有合理且合乎邏輯的原因。"}],
    }
    score = score_package(package, gold(P_SECRECY))

    assert score.in_claims == ()
    assert score.in_linked_steps == ("P01",)
    assert score.recall == 1.0, "reachable from a claim is delivered"
    assert score.stranded == ()


def test_a_step_no_claim_points_at_is_stranded():
    """The loss this column exists to catch: in the package, out of reach."""

    package = {
        "claims": [],
        "evidence_steps": [
            {"evidence_step_id": "E1",
             "statement": "耶穌確曾命令人保密，但皆有合理且合乎邏輯的原因。"}],
    }
    score = score_package(package, gold(P_SECRECY))

    assert score.stranded == ("P01",)
    assert score.recall == 0.0


def test_an_observation_is_stranded_because_the_walk_never_visits_it():
    package = {"claims": [], "observations": [
        {"statement": "耶穌確曾命令人保密，但皆有合理且合乎邏輯的原因。"}]}
    score = score_package(package, gold(P_SECRECY))

    assert score.stranded == ("P01",)
    assert score.recall == 0.0


def test_a_claim_is_preferred_over_a_step_for_the_same_proposition():
    package = {
        "claims": [{"title": "耶穌的保密命令都有合理的原因。",
                    "evidence_step_ids": ["E1"]}],
        "evidence_steps": [{"evidence_step_id": "E1",
                            "statement": "耶穌命令保密，有合理的原因。"}],
    }
    score = score_package(package, gold(P_SECRECY))

    assert score.in_claims == ("P01",) and score.in_linked_steps == ()


def test_a_missing_proposition_is_named_not_just_counted():
    """A score you cannot audit is a score you cannot fix."""

    package = {"claims": [{"title": "與此無關的一句話。"}]}
    score = score_package(package, gold(P_SECRECY))

    assert score.missing == ("P01",)
    assert score.recall == 0.0


def test_evidence_records_the_text_that_satisfied_each_match():
    package = {"claims": [{"title": "耶穌的保密命令都有合理的原因。"}]}
    score = score_package(package, gold(P_SECRECY))

    assert "合理" in score.evidence["P01"]


def test_the_shipped_gold_set_loads_and_is_marked_unreviewed():
    """It was built from model output, so it ranks; it does not yet standardise."""

    g = GoldSet.load(GOLD_DIR / "matthew16_notes_s1.json")

    assert len(g.propositions) == 18
    assert g.source_id == "16_章_-_彌賽亞，捨己"
    assert g.human_reviewed is False, "flip this only after a person reads the list"
    assert len({p.id for p in g.propositions}) == 18
