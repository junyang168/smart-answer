from __future__ import annotations

import pytest

from backend.api import service


def _section(book, chapter, first, last):
    return {
        "book": book,
        "display": f"{book} {chapter}:{first}-{last}",
        "verses": [{"chapter": chapter, "verse": v, "text": "…"} for v in range(first, last + 1)],
    }


# 2026-10-04: 創 5:21–29 (9 verses) makes two slides, so three passages make four slides.
OCT_4 = [_section("創世記", 5, 21, 29), _section("創世記", 7, 11, 13), _section("希伯來書", 11, 5, 7)]


def _readers_by_slide(sections, readers):
    data, summary, assigned = service._prepare_scripture_sections(sections, readers)
    return [(d["label"], d["reader"]) for d in data], [s["reader"] for s in summary], assigned


def test_three_readers_for_four_slides_share_a_passage():
    slides, summary, assigned = _readers_by_slide(OCT_4, ["王冬丽", "楊軍", "余克宇"])
    assert slides == [
        ("創世記 5:21-25", "王冬丽"),
        ("創世記 5:26-29", "王冬丽"),
        ("創世記 7:11-13", "楊軍"),
        ("希伯來書 11:5-7", "余克宇"),
    ]
    assert summary == ["王冬丽", "王冬丽", "楊軍", "余克宇"]
    assert assigned == ["王冬丽", "楊軍", "余克宇"]


def test_enough_readers_still_get_one_slide_each():
    slides, _, assigned = _readers_by_slide(OCT_4, ["A", "B", "C", "D", "E"])
    assert [reader for _, reader in slides] == ["A", "B", "C", "D"]
    assert assigned == ["A", "B", "C", "D"]


def test_spare_readers_go_to_the_longest_passage():
    long_one = [_section("詩篇", 119, 1, 24), _section("約翰福音", 3, 16, 19)]  # 24 verses -> several slides
    slides, _, _ = _readers_by_slide(long_one, ["A", "B", "C"])
    readers = [reader for label, reader in slides if label.startswith("詩篇")]
    assert set(readers) == {"A", "B"} and readers == sorted(readers)  # contiguous chunks
    assert [reader for label, reader in slides if label.startswith("約翰")] == ["C"]


def test_every_passage_needs_a_reader():
    with pytest.raises(ValueError, match="3 段經文至少需要 3 位讀經同工"):
        service._prepare_scripture_sections(OCT_4, ["A", "B"])


def test_duplicate_readers_are_still_refused():
    with pytest.raises(ValueError, match="讀經同工不可重複：A"):
        service._prepare_scripture_sections(OCT_4, ["A", "B", "A"])
