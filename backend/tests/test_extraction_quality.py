"""The measurement, without calling a model.

Every number here was previously produced by a person reading a package. These
tests are what makes the measurement reproducible; the models are not.
"""

from __future__ import annotations

import pytest

from backend.pipeline.extraction_quality import (
    CLAIM, LINKED_STEP, OBSERVATION, ORPHAN_STEP,
    agreement, combined_list, records, review_pass_rate, same_finding, score,
)

SECRECY = "耶穌確曾命令人保密，但皆有合理且合乎邏輯的原因。"
SECRECY_REPHRASED = "耶穌在事工期間確曾命令某些人保密，但皆有合理且合乎邏輯的原因。"
PARALLEL = "可1:43-45 亦見於太 8:1-4 及路 5:12-16，證明並非馬可的發明。"


def package(claims=(), steps=(), observations=()):
    return {"claims": list(claims), "evidence_steps": list(steps),
            "observations": list(observations)}


# ---------------------------------------------------------------- matching

def test_a_rephrasing_is_the_same_finding():
    assert same_finding(SECRECY, SECRECY_REPHRASED)


def test_two_points_about_the_same_verse_are_not_the_same_finding():
    """可4:11 是啟示說明 and 可4:11 也見於太13:11-17 share a verse, not a point."""

    assert not same_finding(
        "可4:11 並非保密的請求，而是神聖啟示的說明。",
        "可4:11 此段亦見於太 13:11-17，並非馬可獨有。",
    )


# ------------------------------------------------------------- reachability

def test_a_step_a_claim_links_to_is_reachable():
    """Authoring starts at a claim and walks `evidence_step_ids`."""

    pkg = package(
        claims=[{"title": "某個結論。", "evidence_step_ids": ["E1"]}],
        steps=[{"evidence_step_id": "E1", "statement": SECRECY}],
    )
    tiers = {r.text: r.tier for r in records(pkg)}

    assert tiers[SECRECY] == LINKED_STEP
    assert all(r.reachable for r in records(pkg))


def test_a_step_no_claim_points_at_is_not_reachable():
    pkg = package(steps=[{"evidence_step_id": "E1", "statement": SECRECY}])
    step = next(r for r in records(pkg) if r.text == SECRECY)

    assert step.tier == ORPHAN_STEP and not step.reachable


def test_a_step_that_names_its_claim_is_reachable_too():
    """Either direction of the link counts; the runner writes both."""

    pkg = package(
        claims=[{"title": "某個結論。"}],
        steps=[{"evidence_step_id": "E1", "statement": SECRECY,
                "produced_claim_ids": ["CL1"]}],
    )
    step = next(r for r in records(pkg) if r.text == SECRECY)

    assert step.tier == LINKED_STEP


def test_an_observation_is_never_reachable():
    """The authoring walk never visits observations."""

    pkg = package(observations=[{"statement": SECRECY}])
    observation = records(pkg)[0]

    assert observation.tier == OBSERVATION and not observation.reachable


# ------------------------------------------------------------ combined list

def test_the_list_merges_the_same_finding_across_runs():
    runs = {
        "a": package(claims=[{"title": SECRECY}]),
        "b": package(steps=[{"evidence_step_id": "E1", "statement": SECRECY_REPHRASED}]),
    }
    findings = combined_list(runs)

    assert len(findings) == 1
    assert findings[0].found_by == 2
    assert findings[0].seen_in["a"] == CLAIM
    assert findings[0].seen_in["b"] == ORPHAN_STEP


def test_the_list_keeps_the_best_tier_a_run_gave_a_finding():
    """A run that files the same point twice is credited with the better one."""

    runs = {"a": package(
        claims=[{"title": SECRECY, "evidence_step_ids": ["E1"]}],
        steps=[{"evidence_step_id": "E1", "statement": SECRECY_REPHRASED}],
    )}
    findings = combined_list(runs)

    assert len(findings) == 1 and findings[0].seen_in["a"] == CLAIM


def test_findings_only_one_run_produced_stay_in_the_list():
    runs = {
        "a": package(claims=[{"title": SECRECY}, {"title": PARALLEL}]),
        "b": package(claims=[{"title": SECRECY}]),
    }
    findings = combined_list(runs)

    assert len(findings) == 2
    assert [f.found_by for f in findings] == [2, 1]


# ------------------------------------------------------------------ scoring

def test_scoring_partitions_every_finding():
    """reachable + stranded + missing accounts for the whole list, always."""

    runs = {
        "a": package(
            claims=[{"title": SECRECY, "evidence_step_ids": ["E1"]}],
            steps=[{"evidence_step_id": "E1", "statement": PARALLEL}],
            observations=[{"statement": "太 16:20 記載耶穌囑咐門徒不可對人說祂是基督。"}],
        ),
        "b": package(claims=[{"title": "一個只有 b 找到的結論：門徒尚未明白受苦的使命。"}]),
    }
    findings = combined_list(runs)
    s = score("a", findings)

    assert len(s.reachable) + len(s.stranded) + len(s.missing) == s.total
    assert len(s.asserted) <= len(s.reachable), "asserted is a subset of reachable"


def test_a_package_that_files_everything_as_observations_delivers_nothing():
    """The failure the tier split exists to name: read, and out of reach."""

    runs = {"a": package(observations=[{"statement": SECRECY}, {"statement": PARALLEL}])}
    s = score("a", combined_list(runs))

    assert s.recall == 0.0
    assert len(s.stranded) == 2 and s.missing == ()


# ---------------------------------------------------------------- agreement

def test_agreement_counts_what_both_runs_produced():
    runs = {
        "a": package(claims=[{"title": SECRECY}, {"title": PARALLEL}]),
        "b": package(claims=[{"title": SECRECY}]),
    }
    findings = combined_list(runs)
    ag = agreement(findings, "a", "b")

    assert (ag.shared, ag.total) == (1, 2)
    assert ag.ratio == 0.5
    assert len(ag.disputed) == 1, "the worklist is the findings only one run had"


def test_two_identical_runs_agree_completely():
    pkg = package(claims=[{"title": SECRECY}])
    ag = agreement(combined_list({"a": pkg, "b": pkg}), "a", "b")

    assert ag.ratio == 1.0 and ag.disputed == ()


# ------------------------------------------------------------------- review

def test_review_verdicts_sum_to_a_pass_rate():
    """The verdicts exist per claim; nothing in the pipeline totals them."""

    review = {"claim_reviews": [
        {"decision": "pass"}, {"decision": "pass"}, {"decision": "changes_suggested"}]}

    assert review_pass_rate(review) == (2, 3)


def test_a_package_nobody_reviewed_reports_zero_of_zero():
    assert review_pass_rate({}) == (0, 0)
