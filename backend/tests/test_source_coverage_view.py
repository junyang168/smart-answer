from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from backend.pipeline.base_contract_coverage import sentence_spans, split_sentences
from backend.pipeline.source_coverage_view import (
    SourceCoverageReader,
    _flatten_spans,
    _place_fragments,
    _sentence_rows,
    load_segments,
    resolve_source_path,
    segment_key,
)


def test_sentence_spans_and_split_sentences_stay_one_definition() -> None:
    """Two sentence splitters would mean two different sentences.

    Coverage is decided by whether a fragment span overlaps a sentence, so the
    offsets and the text must describe the same sentence or the report is about
    something no other tool measures.
    """
    text = "  甲說。乙說！  \n\n丙「說」？ 丁\n戊。"
    assert [text[start:end] for start, end in sentence_spans(text)] == split_sentences(text)
    assert split_sentences(text) == ["甲說。", "乙說！", "丙「說」？", "丁", "戊。"]


def test_sentence_spans_point_at_the_original_offsets() -> None:
    text = "  甲說。乙說。"
    assert sentence_spans(text) == [(2, 5), (5, 8)]


class FakeStore:
    def __init__(self, rows: list[tuple[str, str, str, dict[str, Any]]]):
        self.rows = rows

    def connect(self):
        rows = self.rows

        class Cursor:
            def __init__(self, wanted: list[str]):
                self.wanted = wanted

            def fetchall(self):
                return [row for row in rows if row[0] in self.wanted]

        class Connection:
            def execute(self, _query: str, params: tuple):
                return Cursor(list(params[0]))

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        return Connection()


@pytest.fixture
def transcript(tmp_path: Path) -> Path:
    payload = {
        "metadata": {"title": "測試"},
        "script": [
            {"index": 7, "text": "第一句。第二句。", "start_time": 0, "end_time": 10},
            {"index": 8, "text": "沒有人抽過這一段。", "start_time": 10, "end_time": 20},
            {"index": 9, "text": "這一段有人抽過。", "start_time": 20, "end_time": 30},
        ],
    }
    path = tmp_path / "script_published" / "T1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_segment_key_matches_what_extraction_addresses() -> None:
    assert segment_key(0) == "S0001"
    assert segment_key(207) == "S0208"


def test_load_segments_keeps_a_manuscript_and_a_transcript_on_one_scheme(tmp_path: Path, transcript: Path) -> None:
    """Extraction segments a manuscript by blank line and a transcript by its
    own script list.  A view that invented a third segmentation would put every
    stored anchor off by an unknown amount."""
    segments, sha256 = load_segments({"source_type": "sermon_transcript"}, transcript)
    assert [item["key"] for item in segments] == ["S0001", "S0002", "S0003"]
    assert segments[0]["index"] == 7 and segments[0]["start_time"] == 0
    assert sha256 == _sha256(transcript)

    manuscript = tmp_path / "final.md"
    manuscript.write_text("第一段。\n仍是第一段。\n\n第二段。\n", encoding="utf-8")
    blocks, _ = load_segments({"source_type": "notes_manuscript"}, manuscript)
    assert [item["text"] for item in blocks] == ["第一段。\n仍是第一段。", "第二段。"]
    assert [item["index"] for item in blocks] == [1, 2]


def test_a_heading_is_labelled_rather_than_filtered_out(tmp_path: Path) -> None:
    """Half a manuscript's segments are headings.

    Counting them as material the claim layer failed to take would roughly
    double the apparent gap; dropping them from the inventory would be an
    exclusion nobody recorded.  So they are kept and labelled.
    """
    manuscript = tmp_path / "final.md"
    manuscript.write_text(
        "## 一、試探神蹟\n\n太 16:1 記載，法利賽人來試探。\n\n### 釋經\n\n# 不是標題\n仍是同一段。\n",
        encoding="utf-8",
    )
    segments, _ = load_segments({"source_type": "notes_manuscript"}, manuscript)
    assert [item["is_heading"] for item in segments] == [True, False, True, False]


def test_resolve_source_path_falls_back_to_the_transcript_id(tmp_path: Path, transcript: Path) -> None:
    """The pilot's two lectures predate `source_path` and name only an id."""
    assert resolve_source_path({"source_path": str(transcript)}, []) == transcript
    assert resolve_source_path({"transcript_id": "T1"}, [transcript.parent]) == transcript
    assert resolve_source_path({"transcript_id": "absent"}, [transcript.parent]) is None
    assert resolve_source_path({"source_path": str(tmp_path / "gone.json")}, []) is None


def _segments(texts: list[str]) -> list[dict[str, Any]]:
    return [
        {"ordinal": position, "key": segment_key(position), "index": position + 1, "text": text, "spans": [], "fragment_ids": []}
        for position, text in enumerate(texts)
    ]


def test_a_fragment_is_placed_by_its_segment_key() -> None:
    segments = _segments(["第一句。第二句。"])
    placed = _place_fragments([{"fragment_id": "F1", "paragraph_key": "S0001", "verbatim_excerpt": "第二句。"}], segments)
    assert placed["F1"]["anchor_method"] == "segment_key"
    assert (placed["F1"]["char_start"], placed["F1"]["char_end"]) == (4, 8)
    assert segments[0]["fragment_ids"] == ["F1"]


def test_a_pilot_fragment_is_placed_by_the_transcripts_own_index() -> None:
    """143 pilot fragments address a segment by `index`, not by `S0001`.

    Dropping them would report material as unextracted that demonstrably was
    extracted.
    """
    segments = _segments(["甲。", "乙。"])
    placed = _place_fragments([{"fragment_id": "F1", "paragraph_key": "2", "verbatim_excerpt": "乙。"}], segments)
    assert placed["F1"]["anchor_method"] == "segment_index"
    assert placed["F1"]["segment_ordinal"] == 1


def test_a_fragment_naming_no_segment_is_placed_by_its_own_words() -> None:
    segments = _segments(["甲。", "乙。"])
    placed = _place_fragments([{"fragment_id": "F1", "paragraph_key": None, "verbatim_excerpt": "乙。"}], segments)
    assert placed["F1"]["anchor_method"] == "verbatim_search"
    assert placed["F1"]["segment_ordinal"] == 1


def test_an_excerpt_appearing_twice_is_not_guessed_at() -> None:
    """A threshold that 'mostly matches' is the silent loss this view exists to
    show, so an ambiguous excerpt stays unplaced and says why."""
    segments = _segments(["同一句。", "同一句。"])
    placed = _place_fragments([{"fragment_id": "F1", "paragraph_key": None, "verbatim_excerpt": "同一句。"}], segments)
    assert placed["F1"]["anchor_method"] == "ambiguous_excerpt"
    assert placed["F1"]["segment_ordinal"] is None


def test_a_moved_excerpt_reports_where_the_words_actually_survived() -> None:
    segments = _segments(["改寫過的段落。", "原本的話。"])
    placed = _place_fragments([{"fragment_id": "F1", "paragraph_key": "S0001", "verbatim_excerpt": "原本的話。"}], segments)
    assert placed["F1"]["anchor_method"] == "excerpt_moved"
    assert placed["F1"]["segment_ordinal"] is None
    assert placed["F1"]["found_at_ordinal"] == 1
    assert segments[0]["fragment_ids"] == []


def test_a_fragment_without_an_excerpt_is_not_placed() -> None:
    segments = _segments(["甲。"])
    placed = _place_fragments([{"fragment_id": "F1", "paragraph_key": "S0009", "verbatim_excerpt": ""}], segments)
    assert placed["F1"]["anchor_method"] == "no_excerpt"
    assert placed["F1"]["segment_ordinal"] is None


def test_overlapping_fragments_become_runs_that_keep_both_reachable() -> None:
    """A step and the observation behind it often cut the same words, and a
    highlight cannot nest."""
    runs = _flatten_spans([(0, 10, "A"), (5, 15, "B")])
    assert runs == [
        {"start": 0, "end": 5, "fragment_ids": ["A"]},
        {"start": 5, "end": 10, "fragment_ids": ["A", "B"]},
        {"start": 10, "end": 15, "fragment_ids": ["B"]},
    ]


def test_a_gap_between_two_fragments_is_not_a_run() -> None:
    assert _flatten_spans([(0, 2, "A"), (6, 8, "B")]) == [
        {"start": 0, "end": 2, "fragment_ids": ["A"]},
        {"start": 6, "end": 8, "fragment_ids": ["B"]},
    ]


def test_a_sentence_is_covered_only_when_a_span_actually_overlaps_it() -> None:
    text = "第一句。第二句。第三句。"
    rows = _sentence_rows(text, [{"start": 4, "end": 6, "fragment_ids": ["A"]}])
    assert [row["covered"] for row in rows] == [False, True, False]


def _corpus_rows(transcript: Path) -> list[tuple[str, str, str, dict[str, Any]]]:
    return [
        (
            "source_documents",
            "SRC-T1",
            "candidate",
            {
                "source_id": "SRC-T1",
                "title": "測試講道",
                "source_type": "sermon_transcript",
                "source_path": str(transcript),
                "source_sha256": _sha256(transcript),
            },
        ),
        (
            "source_fragments",
            "FR-1",
            "candidate",
            {"fragment_id": "FR-1", "source_id": "SRC-T1", "paragraph_key": "S0003", "verbatim_excerpt": "這一段有人抽過。"},
        ),
        (
            "evidence_steps",
            "E1",
            "candidate",
            {"evidence_step_id": "E1", "source_fragment_ids": ["FR-1"], "statement": "一個步驟", "produced_claim_ids": ["CL1"]},
        ),
        ("claims", "CL1", "approved", {"claim_id": "CL1", "statement": "一條主張", "evidence_step_ids": ["E1"]}),
        ("claims", "CL2", "approved", {"claim_id": "CL2", "statement": "別的來源的主張", "evidence_step_ids": ["E9"]}),
    ]


def test_build_places_the_claim_layer_on_the_source_text(tmp_path: Path, transcript: Path) -> None:
    reader = SourceCoverageReader(FakeStore(_corpus_rows(transcript)), tmp_path)
    detail = reader.build("SRC-T1")

    assert detail["source"]["file_state"] == "current"
    assert detail["segments"][2]["runs"] == [{"start": 0, "end": 8, "fragment_ids": ["FR-1"]}]
    assert detail["segments"][2]["node_ids"] == ["E1"]
    # Two of three segments were never touched, which is the whole question.
    assert detail["source"]["stats"]["segments_covered"] == 1
    assert detail["source"]["stats"]["segments"] == 3
    assert detail["source"]["stats"]["sentences_covered"] == 1
    assert detail["source"]["stats"]["sentences"] == 4

    assert list(detail["claims"]) == ["CL1"], "a claim reaching no fragment of this source is not this source's claim"
    assert detail["claims"]["CL1"]["fragment_ids"] == ["FR-1"]
    assert detail["claims"]["CL1"]["first_ordinal"] == 2
    assert detail["nodes"]["E1"]["kind"] == "step"


def test_a_claim_reaches_the_source_through_a_question_that_answers_with_it(tmp_path: Path, transcript: Path) -> None:
    """A claim is not anchored only through its own `evidence_step_ids`.

    334 fragments corpus-wide belong to a question the claim answers, and 66 to
    a step that names the claim without the claim naming it back.  Counting
    only one direction reported a fragment count no reader could reach.
    """
    rows = _corpus_rows(transcript)
    rows.append(
        (
            "source_fragments",
            "FR-2",
            "candidate",
            {"fragment_id": "FR-2", "source_id": "SRC-T1", "paragraph_key": "S0002", "verbatim_excerpt": "沒有人抽過這一段。"},
        )
    )
    rows.append(
        (
            "questions",
            "Q1",
            "candidate",
            {"question_id": "Q1", "source_fragment_ids": ["FR-2"], "text": "為什麼？", "answer_claim_ids": ["CL1"]},
        )
    )
    detail = SourceCoverageReader(FakeStore(rows), tmp_path).build("SRC-T1")

    claim = detail["claims"]["CL1"]
    assert claim["fragment_ids"] == ["FR-1", "FR-2"]
    assert claim["evidence_step_ids"] == ["E1"], "the question is not one of the claim's evidence steps"
    # Every fragment the count promises has to be reachable from a record the
    # panel renders, or the number is larger than anything it can show.
    reachable = {
        fragment_id
        for node in detail["nodes"].values()
        if node["id"] in claim["evidence_step_ids"] or "CL1" in node["claim_ids"]
        for fragment_id in node["fragment_ids"]
    }
    assert reachable == set(claim["fragment_ids"])


def test_build_reports_a_source_edited_after_extraction(tmp_path: Path, transcript: Path) -> None:
    """Every offset into a changed source is a guess, and the page has to say so
    rather than quietly highlighting the wrong words."""
    rows = _corpus_rows(transcript)
    rows[0][3]["source_sha256"] = "0" * 64
    detail = SourceCoverageReader(FakeStore(rows), tmp_path).build("SRC-T1")
    assert detail["source"]["file_state"] == "drifted"


def test_build_reports_a_source_whose_file_is_gone(tmp_path: Path, transcript: Path) -> None:
    rows = _corpus_rows(transcript)
    rows[0][3]["source_path"] = str(tmp_path / "gone.json")
    detail = SourceCoverageReader(FakeStore(rows), tmp_path).build("SRC-T1")
    assert detail["source"]["file_state"] == "missing"
    assert detail["segments"] == [] and detail["source"]["stats"]["segments"] == 0


def test_build_rejects_an_unknown_source(tmp_path: Path, transcript: Path) -> None:
    with pytest.raises(KeyError):
        SourceCoverageReader(FakeStore(_corpus_rows(transcript)), tmp_path).build("SRC-absent")
