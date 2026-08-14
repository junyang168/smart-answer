import json

import pytest

from backend.pipeline.timed_sermon_evidence import build_timed_sermon_evidence


def test_timed_sermon_evidence_adds_verified_claim_anchor(tmp_path) -> None:
    transcript = tmp_path / "sermon.json"
    transcript.write_text(
        json.dumps({"script": [{"index": 7, "start_time": 10, "end_time": 20, "text": "完整原聲內容"}]}),
        encoding="utf-8",
    )
    knowledge = {"claims": [{"claim_id": "CL-1", "evidence_step_ids": []}]}
    result = build_timed_sermon_evidence(
        knowledge,
        transcript,
        {"source_id": "SRC-1", "transcript_id": "講道", "title": "講道"},
        [{"claim_id": "CL-1", "source_index": 7, "verbatim_excerpt": "原聲內容"}],
    )
    fragment = result["source_fragments"][0]
    assert (fragment["media_time"], fragment["media_end_time"]) == (10, 20)
    assert result["evidence_steps"][0]["produced_claim_ids"] == ["CL-1"]
    assert result["claims"][0]["evidence_step_ids"] == [result["evidence_steps"][0]["evidence_step_id"]]
    assert result["media_projection"]["requires_model"] is False


def test_timed_sermon_evidence_rejects_non_verbatim_excerpt(tmp_path) -> None:
    transcript = tmp_path / "sermon.json"
    transcript.write_text(
        json.dumps({"script": [{"index": 7, "start_time": 10, "end_time": 20, "text": "原文"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not verbatim"):
        build_timed_sermon_evidence(
            {"claims": [{"claim_id": "CL-1"}]},
            transcript,
            {"source_id": "SRC-1", "transcript_id": "講道"},
            [{"claim_id": "CL-1", "source_index": 7, "verbatim_excerpt": "不存在"}],
        )


def test_timed_sermon_evidence_is_idempotent(tmp_path) -> None:
    transcript = tmp_path / "sermon.json"
    transcript.write_text(
        json.dumps({"script": [{"index": 7, "start_time": 10, "end_time": 20, "text": "完整原聲內容"}]}),
        encoding="utf-8",
    )
    source = {"source_id": "SRC-1", "transcript_id": "講道", "title": "講道"}
    bindings = [{"claim_id": "CL-1", "source_index": 7, "verbatim_excerpt": "原聲內容"}]
    first = build_timed_sermon_evidence(
        {"claims": [{"claim_id": "CL-1", "evidence_step_ids": []}]},
        transcript,
        source,
        bindings,
    )
    second = build_timed_sermon_evidence(first, transcript, source, bindings)
    assert len(second["source_fragments"]) == 1
    assert len(second["evidence_steps"]) == 1
    assert len(second["claims"][0]["occurrences"][0]["anchors"]) == 1
