from __future__ import annotations

import pytest

from backend.pipeline.observation_coverage_adjudication import (
    AdjudicationValidationError,
    build_packet,
    pending_observations,
    summarize,
    validate_adjudication,
)


def _package():
    return {
        "source_fragments": [
            {"fragment_id": "FR-O", "source_id": "S", "paragraph_key": "S0001",
             "verbatim_excerpt": "該撒利亞腓立比位於黑門山下。"},
            {"fragment_id": "FR-E", "source_id": "S", "paragraph_key": "S0009",
             "verbatim_excerpt": "彼得在此認信。"},
        ],
        "observations": [{
            "observation_id": "OBS-1", "statement": "該撒利亞腓立比位於黑門山下。",
            "observation_type": "historical_cultural", "source_fragment_ids": ["FR-O"],
        }],
        "evidence_steps": [{
            "evidence_step_id": "E-1", "statement": "彼得在此認信耶穌是基督。",
            "source_fragment_ids": ["FR-E"],
        }],
        "claims": [{
            "claim_id": "CL-1", "statement": "彼得認信耶穌是基督。",
            "evidence_step_ids": ["E-1"],
        }],
    }


def _packet():
    package = _package()
    return build_packet(package, pending_observations(package), scope="Matt16:13–20")


def test_only_observations_the_structural_measure_could_not_settle_are_judged():
    package = _package()
    assert [row["observation_id"] for row in pending_observations(package)] == ["OBS-1"]


def test_the_packet_carries_the_evidence_wording_behind_each_claim():
    """An observation is often covered by a step's wording rather than by the
    claim's own summary; withholding it would manufacture false gaps."""
    packet = _packet()
    assert packet["claims"][0]["evidence_statements"] == ["彼得在此認信耶穌是基督。"]
    assert packet["scope"] == "Matt16:13–20"


def _verdict(**overrides):
    row = {
        "observation_id": "OBS-1", "verdict": "not_covered",
        "covering_claim_ids": [], "reason": "沒有 claim 承載黑門山下的地理內容。",
    }
    row.update(overrides)
    return {"verdicts": [row]}


def test_a_well_formed_verdict_set_validates():
    validate_adjudication(_verdict(), _packet())


def test_covered_without_naming_a_claim_is_refused():
    """`covered` has to be checkable, or 'yes' becomes a shrug."""
    response = _verdict(verdict="covered", covering_claim_ids=[])
    with pytest.raises(AdjudicationValidationError, match="covered without naming a claim"):
        validate_adjudication(response, _packet())


def test_a_claim_outside_the_packet_cannot_be_cited():
    """Otherwise a gap can be explained away with an invented alibi."""
    response = _verdict(verdict="covered", covering_claim_ids=["CL-999"])
    with pytest.raises(AdjudicationValidationError, match="unknown claim CL-999"):
        validate_adjudication(response, _packet())


def test_not_covered_may_not_name_claims():
    response = _verdict(covering_claim_ids=["CL-1"])
    with pytest.raises(AdjudicationValidationError, match="not_covered but names claims"):
        validate_adjudication(response, _packet())


def test_an_unjudged_observation_is_reported_rather_than_passing_silently():
    with pytest.raises(AdjudicationValidationError, match="not judged: OBS-1"):
        validate_adjudication({"verdicts": []}, _packet())


def test_judging_the_same_observation_twice_is_refused():
    response = _verdict()
    response["verdicts"].append(dict(response["verdicts"][0]))
    with pytest.raises(AdjudicationValidationError, match="judged more than once"):
        validate_adjudication(response, _packet())


def test_a_verdict_for_an_observation_not_in_the_packet_is_refused():
    with pytest.raises(AdjudicationValidationError, match="not an observation in this packet"):
        validate_adjudication(_verdict(observation_id="OBS-9"), _packet())


def test_a_reason_is_always_required():
    with pytest.raises(AdjudicationValidationError, match="reason is required"):
        validate_adjudication(_verdict(reason="  "), _packet())


def test_the_summary_reports_the_gap_with_the_statement_that_is_missing():
    packet = _packet()
    response = _verdict()
    validate_adjudication(response, packet)
    report = summarize(response, packet)

    assert report["totals"] == {"judged": 1, "covered": 0, "not_covered": 1}
    assert report["not_covered"][0]["statement"] == "該撒利亞腓立比位於黑門山下。"
    assert report["scope"] == "Matt16:13–20"
