from copy import deepcopy

import pytest

from backend.pipeline.other_claim_grouping_compaction_trial import encode, decode, trial


def payload():
    return {"scope_label": "other", "claims": [
        {"claim_id": "DK-long-original-id-1", "statement": "原文\nδίκαιος 與‘引號’，不可变。",
         "source_id": "SRC-long-original-source-id", "scripture_refs": ["太 16:19", "罗 4:5", "太 16:19"]},
        {"claim_id": "DK-long-original-id-2", "statement": "与兴起。", "source_id": "SRC-long-original-source-id",
         "scripture_refs": []}]}


def test_lossless_full_text_sources_reference_order_duplicates_and_empty_refs():
    p = payload()
    packed, mapping = encode(p)
    assert decode(packed, mapping) == p
    assert packed["sources"] == ["SRC-long-original-source-id"]
    report, _, _, request = trial(p)
    assert report["original_projection_sha256"] == report["restored_projection_sha256"]
    assert report["lossless_roundtrip"] and report["model_calls"] == 0
    assert report["runtime_integration"] == "not_implemented_by_trial"
    assert request["json_schema"]["strict"]
    assert trial(p)[0] == report


@pytest.mark.parametrize("bad", ["missing", "foreign", "duplicate"])
def test_mapping_scope_fail_closed(bad):
    packed, mapping = encode(payload())
    if bad == "missing": packed["rows"].pop()
    elif bad == "foreign": packed["rows"][0][0] = "K9999"
    else: packed["rows"].append(deepcopy(packed["rows"][0]))
    with pytest.raises(ValueError):
        decode(packed, mapping)


def test_duplicate_original_ids_rejected():
    p = payload()
    p["claims"][1]["claim_id"] = p["claims"][0]["claim_id"]
    with pytest.raises(ValueError):
        encode(p)
