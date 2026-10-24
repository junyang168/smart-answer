import json
from copy import deepcopy

import pytest

from backend.pipeline.draft_first_source_reader import SourceReadingClient
from backend.pipeline.theological_evidence_packet import (
    compile_theological_evidence_packet, make_editorial_scope,
    validate_theological_evidence_packet, TheologicalEvidencePacketError,
)
from backend.tests.test_theological_evidence_packet import _records, _source_reader


def packet():
    scope = make_editorial_scope(scope_id="test", working_title="test", reader_question="test?",
        passage_refs=[], structure_revision_id="selection-test", publication_profile_id="test")
    selection = {"selection_id": "selection-test", "attribution": "church_editor",
        "decision_basis": "owner reader question", "unresolved_items": [],
        "focal_viewpoints": _records()["viewpoint_structure_revisions"][0]["focal_viewpoints"]}
    def read(source):
        return {**_source_reader(source), "content": "first source block\n\nsecond source block"}
    return compile_theological_evidence_packet(scope=scope, records=_records(),
        editorial_selection=selection, source_delivery_mode="source_library", source_original_reader=read)


class Fake:
    model, backend, last_raw_response = "test", "fake", None

    def __init__(self, replies):
        self.replies, self.requests = iter(replies), []

    def generate_json(self, prompt, payload, schema, **kwargs):
        self.requests.append(json.loads(payload))
        reply = next(self.replies)
        self.last_raw_response = {"last_message": json.dumps(reply)}
        return reply


def test_library_reading_retains_exact_blocks_and_records_actual_payload(tmp_path):
    p = packet()
    validate_theological_evidence_packet(p)
    sid = p["source_documents"][0]["source_id"]
    client = Fake([
        {"ready": False, "notebook": "", "retain": [],
         "requests": [{"source_id": sid, "start_block": 2, "end_block": 2}]},
        {"ready": True, "notebook": "read second", "requests": [],
         "retain": [{"source_id": sid, "block": 2}]},
        {"manuscript_markdown": "model prose"},
    ])
    wrapped = SourceReadingClient(client, p, tmp_path)
    result = wrapped.generate_json("rules", json.dumps({"source_originals": ["ENTIRE_CORPUS"]}), {})
    assert result["manuscript_markdown"] == "model prose"
    assert "source_originals" not in client.requests[0]
    assert client.requests[-1]["source_originals"][0]["content"] == "second source block"
    assert (tmp_path / "call-01/receipt.json").is_file()
    resumed = Fake([])
    assert SourceReadingClient(resumed, p, tmp_path).generate_json(
        "rules", json.dumps({"source_originals": ["ENTIRE_CORPUS"]}), {}
    ) == result
    assert resumed.requests == []


def test_unread_block_cannot_be_declared_evidence(tmp_path):
    p = packet()
    client = Fake([{"ready": True, "notebook": "", "requests": [],
                    "retain": [{"source_id": p["source_documents"][0]["source_id"], "block": 1}]}])
    with pytest.raises(ValueError, match="unread"):
        SourceReadingClient(client, p, tmp_path).generate_json("rules", '{"source_originals": []}', {})


def test_unknown_sources_and_overflow_fail_before_final_generation(tmp_path):
    wrapped = SourceReadingClient(Fake([]), packet(), tmp_path, max_source_chars=1)
    sid = next(iter(wrapped.blocks))
    with pytest.raises(ValueError, match="capacity"):
        wrapped._read([{"source_id": sid, "start_block": 1, "end_block": 1}])
    with pytest.raises(ValueError, match="unknown source"):
        wrapped._read([{"source_id": "invented", "start_block": 1, "end_block": 1}])


def test_range_past_the_last_block_reads_to_the_end(tmp_path):
    wrapped = SourceReadingClient(Fake([]), packet(), tmp_path)
    sid = next(iter(wrapped.blocks))
    last = len(wrapped.blocks[sid])
    read = wrapped._read([{"source_id": sid, "start_block": last, "end_block": last + 4}])
    assert list(read) == [(sid, last)]
    with pytest.raises(ValueError, match="invalid source block range"):
        wrapped._read([{"source_id": sid, "start_block": last + 1, "end_block": last + 2}])


def test_article_selection_does_not_approve_structure_or_change_records():
    p = packet()
    assert p["structure"]["pointer"]["attribution"] == "church_editor"
    assert "review_status" not in p["structure"]["revision"]
    assert not any(x["collection"].startswith("viewpoint_structure") for x in p["dependency_manifest"])


def test_library_still_verifies_complete_original_sha():
    p = packet()
    changed = deepcopy(p)
    changed["source_originals"]["originals"][0]["content"] += "tampering"
    with pytest.raises(TheologicalEvidencePacketError, match="SHA"):
        validate_theological_evidence_packet(changed)


def test_retired_relation_is_not_an_authoring_constraint():
    from backend.pipeline.draft_first_author_runner import structure_unresolved_items
    p = packet()
    p["relations"] = [{"relation_type": "tensions_with", "effective_state": "retired", "reason": "obsolete"},
                      {"relation_type": "tensions_with", "effective_state": "active", "reason": "current"}]
    assert structure_unresolved_items(p) == ["current"]


def test_compiler_excludes_retired_relations_but_keeps_active_tension():
    p = packet()
    records = _records()
    ids = [x["viewpoint"]["viewpoint_id"] for x in p["focal_viewpoints"]]
    records["viewpoint_relations"] = [
        {"viewpoint_relation_id": "old", "source_viewpoint_id": ids[0],
         "target_viewpoint_id": ids[1], "review_status": "system_approved", "effective_state": "retired"},
        {"viewpoint_relation_id": "live", "source_viewpoint_id": ids[0],
         "target_viewpoint_id": ids[1], "review_status": "system_approved", "effective_state": "active"},
    ]
    out = compile_theological_evidence_packet(scope=p["scope"], records=records,
        editorial_selection=p["structure"]["revision"], source_original_reader=_source_reader,
        source_delivery_mode="source_library")
    assert [r["viewpoint_relation_id"] for r in out["relations"]] == ["live"]


def test_a_placeholder_reading_reply_is_retried_once_with_the_reason(tmp_path):
    """#439: Opus answered 「（见上）」 with no requests, twice in one revision."""

    p = packet()
    sid = p["source_documents"][0]["source_id"]
    placeholder = {"ready": False, "notebook": "（见上）", "requests": [], "retain": []}
    client = Fake([
        placeholder,
        {"ready": False, "notebook": "", "retain": [],
         "requests": [{"source_id": sid, "start_block": 2, "end_block": 2}]},
        {"ready": True, "notebook": "read second", "requests": [], "retain": [{"source_id": sid, "block": 2}]},
        {"manuscript_markdown": "model prose"},
    ])
    result = SourceReadingClient(client, p, tmp_path).generate_json("rules", '{"source_originals": []}', {})
    assert result["manuscript_markdown"] == "model prose"
    assert "previous_reply_rejected" in client.requests[1]
    with pytest.raises(ValueError, match="no progress"):
        SourceReadingClient(Fake([placeholder, placeholder]), p, tmp_path / "again").generate_json(
            "rules", '{"source_originals": []}', {})
