from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException

from backend.api import source_coverage


class FakeReader:
    def __init__(self, sources: dict[str, dict[str, Any]]):
        self.sources = sources
        self.loads = 0

    def load(self) -> dict[str, Any]:
        self.loads += 1
        return {"documents": self.sources}

    def build(self, source_id: str, corpus: dict[str, Any] | None = None) -> dict[str, Any]:
        if source_id not in self.sources:
            raise KeyError(source_id)
        return self.sources[source_id]


def _source(source_id: str, file_state: str = "current", **stats: int) -> dict[str, Any]:
    base = {
        "segments": 10,
        "segments_covered": 4,
        "sentences": 40,
        "sentences_covered": 9,
        "chars": 900,
        "chars_covered": 100,
        "fragments": 12,
        "fragments_placed": 12,
        "steps": 8,
        "observations": 3,
        "questions": 1,
        "positions": 0,
        "claims": 5,
    }
    base.update(stats)
    return {
        "source": {"source_id": source_id, "title": source_id, "file_state": file_state, "stats": base},
        "segments": [{"key": "S0001", "text": "第一句。"}],
        "fragments": {},
        "nodes": {},
        "claims": {},
    }


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> FakeReader:
    fake = FakeReader(
        {
            "SRC-A": _source("SRC-A"),
            "SRC-B": _source("SRC-B", file_state="drifted", segments=6, segments_covered=1),
            "SRC-C": _source("SRC-C", file_state="missing"),
        }
    )
    monkeypatch.setattr(source_coverage, "_reader", lambda: fake)
    return fake


def test_list_sources_leaves_the_professors_text_behind(reader: FakeReader) -> None:
    """The overview needs counts only; three hundred kilobytes of transcript per
    row would make the page unusable before it showed anything."""
    result = source_coverage.list_sources()
    assert [item["source_id"] for item in result["sources"]] == ["SRC-A", "SRC-B", "SRC-C"]
    assert "第一句。" not in json.dumps(result, ensure_ascii=False)


def test_totals_add_up_and_count_the_sources_that_cannot_be_trusted(reader: FakeReader) -> None:
    totals = source_coverage.list_sources()["totals"]
    assert totals["segments"] == 26 and totals["segments_covered"] == 9
    assert totals["sources"] == 3
    assert totals["sources_drifted"] == 1
    assert totals["sources_unreadable"] == 1


def test_source_detail_returns_the_text_and_the_records(reader: FakeReader) -> None:
    detail = source_coverage.source_detail("SRC-A")
    assert detail["segments"][0]["text"] == "第一句。"
    assert detail["source"]["stats"]["claims"] == 5


def test_source_detail_rejects_an_unknown_source(reader: FakeReader) -> None:
    with pytest.raises(HTTPException) as error:
        source_coverage.source_detail("SRC-absent")
    assert error.value.status_code == 404


def test_nothing_is_cached_between_requests(reader: FakeReader) -> None:
    """Coverage depends on the store and on files that change independently of
    it, so a cache keyed on either one alone would keep reporting a source as
    intact after it was edited."""
    source_coverage.list_sources()
    source_coverage.list_sources()
    source_coverage.source_detail("SRC-A")
    assert reader.loads == 3
