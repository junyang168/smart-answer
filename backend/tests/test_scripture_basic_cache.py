"""The chapter listener keeps its Chinese verses when the external API fails."""
import asyncio
import json

import httpx
import pytest

from backend.api import scripture
from backend.bible_study import paths


@pytest.mark.parametrize("cached", [True, False])
def test_basic_scripture_uses_local_cuv_and_preserves_network_fallback(tmp_path, monkeypatch, cached):
    monkeypatch.setattr(paths, "cuv_dir", lambda: tmp_path)
    monkeypatch.setattr(scripture, "BIBLE_API_TRANSLATION_ZH", "cuv")
    book = tmp_path / "MAT"
    book.mkdir()
    if cached:
        (book / "5.json").write_text(json.dumps({"3": "虛心的人有福了！", "4": "哀慟的人有福了！"}))
    calls = []

    async def remote(client, book, chapter, start, end, translation):
        calls.append(translation)
        if translation == "cuv":
            return "<p>remote Chinese</p>"
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(scripture, "fetch_bible_api", remote)
    result = asyncio.run(scripture.get_scripture_basic("mat-5-3-4"))
    assert result["reference"] == "Matt 5:3-4"
    assert result["passages"]["en"] == ""
    if cached:
        assert result["passages"]["zh"] == "<p>虛心的人有福了！<br/>哀慟的人有福了！</p>"
        assert "cuv" not in calls
    else:
        assert result["passages"]["zh"] == "<p>remote Chinese</p>"
        assert "cuv" in calls
