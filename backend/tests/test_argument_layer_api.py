from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.api import argument_layer


def _node(node_id: str, statement: str) -> dict:
    return {"id": node_id, "label": node_id, "statement": statement, "ordinal": 1}


@pytest.fixture
def corpus(monkeypatch: pytest.MonkeyPatch) -> dict:
    data = {
        "lanes": ["問題・背景", "經文證據", "解經・推理", "結論", "神學・應用"],
        "totals": {"steps": 2, "claims": 1},
        "sources": [
            {
                "key": "abc123",
                "title": "2019-05-26 罗马书5章1至2节",
                "note": "",
                "source_type": "sermon_transcript",
                "source_ids": ["SRC-1-abc123"],
                "stats": {"steps": 2, "claims": 1},
                "steps": [_node("DK-abc123-E001", "教授指出人子有定冠詞"), _node("DK-abc123-E002", "別的")],
                "observations": [],
                "claims": [_node("DK-abc123-CL001", "人子指但以理書七章的那一位")],
                "questions": [],
                "positions": [],
                "edges": [],
            }
        ],
    }
    monkeypatch.setattr(argument_layer, "_data", lambda: data)
    return data


def test_list_sources_leaves_the_professors_text_behind(corpus: dict) -> None:
    """The overview needs counts only; shipping every quote would send megabytes."""
    result = argument_layer.list_sources()
    assert result["totals"] == corpus["totals"]
    assert [source["key"] for source in result["sources"]] == ["abc123"]
    assert set(result["sources"][0]) == {"key", "title", "note", "source_type", "source_ids", "stats"}


def test_source_detail_returns_the_whole_source(corpus: dict) -> None:
    result = argument_layer.source_detail("abc123")
    assert result["source"]["steps"][0]["statement"].startswith("教授指出")
    assert result["lanes"][3] == "結論"


def test_source_detail_rejects_an_unknown_key(corpus: dict) -> None:
    with pytest.raises(HTTPException) as error:
        argument_layer.source_detail("nope")
    assert error.value.status_code == 404


def test_search_crosses_sources_and_reports_what_it_did_not_return(corpus: dict) -> None:
    result = argument_layer.search("人子")
    assert result["total"] == 2
    # Claims come first: a reviewer searching a phrase usually wants the
    # conclusion it belongs to before the step that produced it.
    assert [hit["kind"] for hit in result["hits"]] == ["claim", "step"]
    assert result["hits"][0]["source_title"] == "2019-05-26 罗马书5章1至2节"

    capped = argument_layer.search("人子", limit=1)
    assert capped["total"] == 2 and len(capped["hits"]) == 1


def test_search_also_matches_an_id(corpus: dict) -> None:
    assert argument_layer.search("DK-abc123-E002")["total"] == 1


def test_empty_query_returns_nothing_rather_than_the_whole_corpus(corpus: dict) -> None:
    assert argument_layer.search("   ") == {"query": "   ", "total": 0, "hits": []}
