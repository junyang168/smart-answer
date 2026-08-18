from __future__ import annotations

import pytest

from backend.pipeline.argument_layer_view import (
    PILOT_KEY,
    UNSOURCED_KEY,
    _label,
    _lane,
    _ordinal,
    _source_key,
)


@pytest.mark.parametrize(
    ("object_id", "expected"),
    [
        ("DK-e0c52b3df8b7-E017", "e0c52b3df8b7"),
        ("DK-a26f0a7e9ba4-CL020H", "a26f0a7e9ba4"),
        # AI arbitration prefixes the id it repairs; the record still belongs to
        # the source it was extracted from.
        ("AI-ADJ-DK-02d0db2fc475-CL012-01", "02d0db2fc475"),
        ("L3-E001", PILOT_KEY),
        ("OBS-L4-E012", PILOT_KEY),
        ("CL-0004", PILOT_KEY),
        ("AI-ADJ-CL-0017-1", PILOT_KEY),
        # Hand-built against a manuscript: it names no source, and dropping it
        # would hide a record from the reviewer.
        ("ES-STEP-M16-003-1", UNSOURCED_KEY),
        ("POS-M16-SECOND-COMING-FAILED", UNSOURCED_KEY),
    ],
)
def test_source_key_keeps_every_id_shape_attached(object_id: str, expected: str) -> None:
    assert _source_key(object_id) == expected


def test_ordinal_reads_position_within_the_source() -> None:
    assert _ordinal("DK-e0c52b3df8b7-E017") == 17
    assert _ordinal("OBS-L3-E012") == 12
    assert _ordinal("AI-ADJ-DK-02d0db2fc475-CL012-01") == 12
    assert _ordinal("POS-M16-SECOND-COMING-FAILED") == 0


def test_label_drops_what_every_id_in_a_source_repeats() -> None:
    assert _label("DK-e0c52b3df8b7-E017") == "E017"
    assert _label("AI-ADJ-DK-02d0db2fc475-CL012-01") == "CL012-01"
    assert _label("OBS-L3-E012") == "OBS-L3-E012"


def test_lane_prefers_the_recorded_lane_then_step_type_then_role() -> None:
    assert _lane({"argument_lane": 3, "step_type": "reasoning"}) == 3
    assert _lane({"step_type": "scripture_evidence"}) == 1
    assert _lane({"step_type": "application"}) == 4
    # Pre-v2 steps carry no step_type; their discourse_role is the only signal.
    assert _lane({"discourse_role": "question_context"}) == 0
    assert _lane({}) == 2
