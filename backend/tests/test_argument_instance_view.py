"""The reader must preserve normalized links and the bound source without model calls."""
import hashlib
import json

import pytest

from backend.api.argument_instance_view import build_view


@pytest.fixture
def data(tmp_path, monkeypatch):
    from backend.api import config
    monkeypatch.setattr(config, "DATA_BASE_PATH", tmp_path)
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"script": [
        {"index": 1, "text": "前文。第一個理由。結論。", "start_time": 0, "end_time": 12}]}))
    candidate = tmp_path / "candidate.json"
    candidate.write_text("{}")
    (tmp_path / "creation-result.json").write_text(json.dumps({
        "group_claim_ids": {"G001": ["CL0001"]},
        "groups": [{"title": "一個完整論證"}]}))
    records = {
        "source_documents": [{"source_id": "S1", "title": "測試講道", "source_path": str(source)}],
        "claims": [{"claim_id": "DK-CL0001", "title": "結論", "evidence_step_ids": ["DK-P01-E1"],
                    "opposed_position_ids": ["DK-P01-P1"], "occurrences": [
                        {"anchors": [{"source_fragment_id": "F2"}]}]}],
        "evidence_steps": [{"evidence_step_id": "DK-P01-E1", "statement": "理由",
                            "discourse_role": "由理由推出結論", "source_fragment_ids": ["F1"],
                            "produced_claim_ids": ["DK-CL0001"]}],
        "position_nodes": [{"position_id": "DK-P01-P1", "title": "反方", "source_fragment_ids": ["F1"]}],
        "observations": [{"observation_id": "DK-P01-O1", "statement": "觀察", "source_fragment_ids": ["F1"]}],
        "knowledge_relations": [{"relation_id": "R1", "from_id": "DK-P01-O1", "to_id": "DK-P01-E1",
                                 "relation_type": "supports", "reason": "觀察給出理由"}],
        "source_fragments": [
            {"fragment_id": "F1", "verbatim_excerpt": "第一個理由。", "source_segment_index": 1},
            {"fragment_id": "F2", "verbatim_excerpt": "結論。", "source_segment_index": 1}],
    }
    return records, {"candidate_path": str(candidate),
                     "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()}


def test_native_and_explicit_links_form_one_path_without_duplicates(data):
    view = build_view(*data)
    edges = view["groups"][0]["relations"]
    triples = [(e["from"], e["to"], e["type"]) for e in edges]
    assert triples.count(("DK-P01-E1", "DK-CL0001", "supports")) == 1
    assert ("DK-P01-O1", "DK-P01-E1", "supports") in triples
    assert ("DK-CL0001", "DK-P01-P1", "refutes") in triples
    assert next(e for e in edges if e["from"] == "DK-P01-E1")["reason"] == "由理由推出結論"


def test_quote_returns_its_snapshot_context_and_narrower_clip(data):
    view = build_view(*data)
    anchor = view["nodes"]["DK-P01-E1"]["anchors"][0]
    assert anchor["quote"] == "第一個理由。"
    assert anchor["paragraph"] == "前文。第一個理由。結論。"
    assert 0 < anchor["start"] < anchor["end"] < 12
    # Own Claim words must remain distinct from its supporting fragments.
    assert [a["quote"] for a in view["nodes"]["DK-CL0001"]["anchors"]] == ["結論。"]


def test_changed_run_is_rejected_instead_of_showing_unbound_grouping(data):
    records, meta = data
    from pathlib import Path
    Path(meta["candidate_path"]).write_text('{"changed":true}')
    with pytest.raises(ValueError, match="一致"):
        build_view(records, meta)
