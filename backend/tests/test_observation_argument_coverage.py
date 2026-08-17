from __future__ import annotations

from backend.pipeline.observation_argument_coverage import (
    IN_ARGUMENT,
    NO_ANCHOR,
    PAIRED_BY_EXCERPT,
    PARAGRAPH_HAS_NO_EVIDENCE,
    SAME_PARAGRAPH_UNPAIRED,
    measure_coverage,
)


def _fragment(fragment_id: str, excerpt: str, paragraph: str = "S0001", source: str = "SRC-1"):
    return {
        "fragment_id": fragment_id,
        "source_id": source,
        "paragraph_key": paragraph,
        "verbatim_excerpt": excerpt,
    }


def _package(*, fragments, observations, evidence_steps):
    return {
        "source_fragments": fragments,
        "observations": observations,
        "evidence_steps": evidence_steps,
    }


def test_a_shared_fragment_is_the_only_case_the_old_metric_could_see():
    report = measure_coverage(_package(
        fragments=[_fragment("FR-1", "ψυχή 可譯作生命。")],
        observations=[{
            "observation_id": "OBS-1", "observation_type": "original_language",
            "source_fragment_ids": ["FR-1"],
        }],
        evidence_steps=[{"evidence_step_id": "E-1", "source_fragment_ids": ["FR-1"]}],
    ))
    assert report["status_counts"][IN_ARGUMENT] == 1
    assert report["totals"]["linked_by_shared_fragment"] == 1
    assert report["totals"]["reached_argument_layer"] == 1


def test_the_same_sentence_split_at_a_different_point_still_counts_as_reached():
    """The real shape of the miss: OBS014's excerpt is a prefix of E023's, so
    the fragments differ and the old metric called it an orphan."""
    report = measure_coverage(_package(
        fragments=[
            _fragment("FR-OBS", "25、26、27 節，在原文中各以 γὰρ 開頭"),
            _fragment("FR-E", "緊接著的 25、26、27 節，在原文中各以 γὰρ 開頭，形成三個連續的理由子句。"),
        ],
        observations=[{
            "observation_id": "OBS-14", "observation_type": "original_language",
            "source_fragment_ids": ["FR-OBS"],
        }],
        evidence_steps=[{"evidence_step_id": "E-23", "source_fragment_ids": ["FR-E"]}],
    ))
    assert report["status_counts"][PAIRED_BY_EXCERPT] == 1
    assert report["status_counts"][IN_ARGUMENT] == 0
    # The old metric would have reported 0%; the content did reach the argument.
    assert report["totals"]["linked_by_shared_fragment"] == 0
    assert report["totals"]["reached_argument_layer"] == 1
    assert report["observations"][0]["paired_evidence_step_id"] == "E-23"


def test_unrelated_evidence_in_the_same_paragraph_is_not_a_pairing():
    report = measure_coverage(_package(
        fragments=[
            _fragment("FR-OBS", "釘十字架是羅馬人的專屬刑罰"),
            _fragment("FR-E", "彼拉多最終寫下的罪名是「猶太人的王」。"),
        ],
        observations=[{
            "observation_id": "OBS-11", "observation_type": "historical_cultural",
            "source_fragment_ids": ["FR-OBS"],
        }],
        evidence_steps=[{"evidence_step_id": "E-12", "source_fragment_ids": ["FR-E"]}],
    ))
    assert report["status_counts"][SAME_PARAGRAPH_UNPAIRED] == 1
    assert report["totals"]["reached_argument_layer"] == 0


def test_a_paragraph_with_no_evidence_is_reported_as_such():
    """Note what this status does and does not assert: the paragraph produced
    no evidence step.  It does not follow that the content is absent from the
    argument layer -- the professor may draw the inference paragraphs later,
    or another source may cover the same point."""
    report = measure_coverage(_package(
        fragments=[
            _fragment("FR-OBS", "此處原文動詞 φρονέω，意為「關心、重視」。", paragraph="S0037"),
            _fragment("FR-E", "別段的證據。", paragraph="S0099"),
        ],
        observations=[{
            "observation_id": "OBS-7", "observation_type": "original_language",
            "source_fragment_ids": ["FR-OBS"],
        }],
        evidence_steps=[{"evidence_step_id": "E-99", "source_fragment_ids": ["FR-E"]}],
    ))
    assert report["status_counts"][PARAGRAPH_HAS_NO_EVIDENCE] == 1
    assert report["extraction_gap_paragraphs"] == [
        {"source_id": "SRC-1", "paragraph_key": "S0037", "observation_ids": ["OBS-7"]}
    ]


def test_a_paragraph_key_is_scoped_by_its_source():
    """Two sermons both have an S0001; evidence in one must not pair with an
    observation in the other."""
    report = measure_coverage(_package(
        fragments=[
            _fragment("FR-OBS", "原文用單數。", paragraph="S0001", source="SRC-A"),
            _fragment("FR-E", "原文用單數。", paragraph="S0001", source="SRC-B"),
        ],
        observations=[{
            "observation_id": "OBS-1", "observation_type": "original_language",
            "source_fragment_ids": ["FR-OBS"],
        }],
        evidence_steps=[{"evidence_step_id": "E-1", "source_fragment_ids": ["FR-E"]}],
    ))
    assert report["status_counts"][PARAGRAPH_HAS_NO_EVIDENCE] == 1
    assert report["status_counts"][PAIRED_BY_EXCERPT] == 0


def test_an_observation_with_no_resolvable_fragment_is_reported_not_dropped():
    report = measure_coverage(_package(
        fragments=[],
        observations=[{"observation_id": "OBS-1", "observation_type": "original_language"}],
        evidence_steps=[],
    ))
    assert report["status_counts"][NO_ANCHOR] == 1
    assert sum(report["status_counts"].values()) == report["totals"]["observations"]


def test_the_singular_fragment_field_is_read_as_well_as_the_list():
    """`load_bearing_observation_promotion` writes `source_fragment_id`."""
    report = measure_coverage(_package(
        fragments=[_fragment("FR-1", "φρονέω 意為關心。")],
        observations=[{
            "observation_id": "OBS-1", "observation_type": "original_language",
            "source_fragment_id": "FR-1",
        }],
        evidence_steps=[{"evidence_step_id": "E-1", "source_fragment_id": "FR-1"}],
    ))
    assert report["status_counts"][IN_ARGUMENT] == 1


def test_coverage_is_broken_out_by_normalized_type_not_the_raw_label():
    report = measure_coverage(_package(
        fragments=[_fragment("FR-1", "希臘文用定冠詞。")],
        observations=[{
            "observation_id": "OBS-1", "observation_type": "希腊文文法观察",
            "source_fragment_ids": ["FR-1"],
        }],
        evidence_steps=[{"evidence_step_id": "E-1", "source_fragment_ids": ["FR-1"]}],
    ))
    assert report["by_normalized_type"] == {"original_language": {IN_ARGUMENT: 1}}


def test_a_type_the_vocabulary_cannot_settle_is_counted_separately():
    report = measure_coverage(_package(
        fragments=[_fragment("FR-1", "背景說明。")],
        observations=[{
            "observation_id": "OBS-1", "observation_type": "背景",
            "source_fragment_ids": ["FR-1"],
        }],
        evidence_steps=[],
    ))
    assert "(unmapped)" in report["by_normalized_type"]


def test_every_observation_lands_in_exactly_one_status():
    fragments = [
        _fragment("FR-A", "甲。", paragraph="S0001"),
        _fragment("FR-B", "乙的全句，包含乙。", paragraph="S0002"),
        _fragment("FR-B-OBS", "乙", paragraph="S0002"),
        _fragment("FR-C", "丙。", paragraph="S0003"),
    ]
    observations = [
        {"observation_id": "O1", "observation_type": "scripture_text", "source_fragment_ids": ["FR-A"]},
        {"observation_id": "O2", "observation_type": "scripture_text", "source_fragment_ids": ["FR-B-OBS"]},
        {"observation_id": "O3", "observation_type": "scripture_text", "source_fragment_ids": ["FR-C"]},
        {"observation_id": "O4", "observation_type": "scripture_text"},
    ]
    evidence_steps = [
        {"evidence_step_id": "E-A", "source_fragment_ids": ["FR-A"]},
        {"evidence_step_id": "E-B", "source_fragment_ids": ["FR-B"]},
    ]
    report = measure_coverage(_package(
        fragments=fragments, observations=observations, evidence_steps=evidence_steps
    ))
    assert sum(report["status_counts"].values()) == 4
    assert report["status_counts"] == {
        IN_ARGUMENT: 1,
        PAIRED_BY_EXCERPT: 1,
        SAME_PARAGRAPH_UNPAIRED: 0,
        PARAGRAPH_HAS_NO_EVIDENCE: 1,
        NO_ANCHOR: 1,
    }
    assert report["totals"]["reached_argument_layer"] == 2
    assert report["totals"]["reached_pct"] == 50.0
