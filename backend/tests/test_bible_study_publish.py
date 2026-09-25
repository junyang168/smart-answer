from __future__ import annotations

import json

import pytest

from backend.bible_study.cuv import refs_in_slides
from backend.bible_study.publish import publish


DECK = "你們不知道所求的是甚麼 太20-17至34.pptx"


def _study(tmp_path, deck=b"deck v1"):
    folder = tmp_path / "studies" / "2026-09-25-mat-20-17-34"
    out = folder / "out"
    out.mkdir(parents=True)
    (out / DECK).write_bytes(deck)
    (out / "逐字稿.md").write_text("script", encoding="utf-8")
    (out / "verses.json").write_text("[]", encoding="utf-8")  # bookkeeping, not published
    return folder, tmp_path / "fellowship" / "2026-09-25"


def nothing_open():
    return []


def test_publishes_and_records_what_it_published(tmp_path):
    folder, target = _study(tmp_path)
    publish(folder, target=target, presentations=nothing_open)
    assert sorted(p.name for p in target.iterdir()) == sorted([DECK, "逐字稿.md"])
    assert set(json.loads((folder / "out" / "published.json").read_text())) == {DECK, "逐字稿.md"}


def test_stops_while_powerpoint_has_the_deck_open(tmp_path):
    folder, target = _study(tmp_path)
    with pytest.raises(RuntimeError, match="PowerPoint has .* open"):
        publish(folder, target=target, presentations=lambda: [DECK])
    assert not target.exists()


def test_stops_when_powerpoint_does_not_answer(tmp_path):
    folder, target = _study(tmp_path)
    with pytest.raises(RuntimeError, match="did not answer"):
        publish(folder, target=target, presentations=lambda: None)


def test_republishing_its_own_output_is_fine(tmp_path):
    folder, target = _study(tmp_path)
    publish(folder, target=target, presentations=nothing_open)
    (folder / "out" / DECK).write_bytes(b"deck v2")
    publish(folder, target=target, presentations=nothing_open)
    assert (target / DECK).read_bytes() == b"deck v2"


def test_stops_when_the_owner_edited_the_published_deck(tmp_path):
    # 2026-09-25: the owner edited both decks in PowerPoint after they were published.
    folder, target = _study(tmp_path)
    publish(folder, target=target, presentations=nothing_open)
    (target / DECK).write_bytes(b"deck v1, edited by the owner")
    (folder / "out" / DECK).write_bytes(b"deck v2")
    with pytest.raises(RuntimeError, match="changed since the last publish"):
        publish(folder, target=target, presentations=nothing_open)
    assert (target / DECK).read_bytes() == b"deck v1, edited by the owner"
    publish(folder, target=target, presentations=nothing_open, overwrite=True)
    assert (target / DECK).read_bytes() == b"deck v2"


def test_a_file_published_before_this_tool_counts_as_edited(tmp_path):
    folder, target = _study(tmp_path)
    target.mkdir(parents=True)
    (target / DECK).write_bytes(b"published by hand")
    with pytest.raises(RuntimeError, match=DECK):
        publish(folder, target=target, presentations=nothing_open)


def test_refs_in_slides_lists_each_chapter_once():
    spec = {
        "slides": [
            {"type": "scripture", "refs": ["MAT 20:17-34"]},
            {"type": "pair", "left": ["MAT 27:56"], "right": ["MRK 15:40"]},
            {"type": "points", "items": ["x"], "refs": ["MAT 20:24", "1CO 9:19"]},
            {"type": "statement", "text": "x"},
        ]
    }
    assert refs_in_slides(spec) == {("MAT", 20), ("MAT", 27), ("MRK", 15), ("1CO", 9)}
