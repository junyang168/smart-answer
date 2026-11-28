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


def test_one_reader_can_cover_four_verses_from_three_books():
    from backend.sunday_assistant.role_assignment import required_reader_count

    refs = ["heb-12-28", "luk-1-74-75", "rev-22-3"]
    sections = [
        _section("希伯來書", 12, 28, 28),
        _section("路加福音", 1, 74, 75),
        _section("啟示錄", 22, 3, 3),
    ]
    assert required_reader_count(refs) == 1
    slides, summary, assigned = _readers_by_slide(sections, ["甲"])
    assert slides == [("希伯來書 12:28", "甲"), ("路加福音 1:74-75", "甲"), ("啟示錄 22:3", "甲")]
    assert summary == ["甲", "甲", "甲"]
    assert assigned == ["甲"]


@pytest.mark.parametrize("refs, expected", [
    (["heb-12-28", "luk-1-74-77"], 1),
    (["heb-12-28", "luk-1-74-78"], 2),
    (["luk-1-74-78"], 1),
    (["luk-1-74-79"], 2),
])
def test_single_reader_cutoff_at_five_verses(refs, expected):
    from backend.api.scripture import parse_reference
    from backend.sunday_assistant.role_assignment import required_reader_count

    assert required_reader_count(refs) == expected
    sections = []
    for ref in refs:
        info = parse_reference(ref)
        sections.append(_section(info["osis_book"], info["chapter"], info["start"], info["end"]))

    _, _, assigned = _readers_by_slide(sections, ["甲", "乙"][:expected])
    assert len(assigned) == expected
    if expected == 2:
        with pytest.raises(ValueError, match="至少需要 2 位"):
            _readers_by_slide(sections, ["甲"])


def test_nine_verse_passage_requires_two_readers_even_if_text_is_short():
    with pytest.raises(ValueError,match='至少需要 4 位'):
        _readers_by_slide(OCT_4,['甲','乙','丙'])
    slides,summary,assigned=_readers_by_slide(OCT_4,['甲','乙','丙','丁'])
    assert slides==[('創世記 5:21-25','甲'),('創世記 5:26-29','乙'),('創世記 7:11-13','丙'),('希伯來書 11:5-7','丁')]
    assert summary==assigned==['甲','乙','丙','丁']


def test_more_than_four_readers_is_rejected():
    with pytest.raises(ValueError,match='最多 4 位'):
        _readers_by_slide(OCT_4,['A','B','C','D','E'])


def test_twenty_four_plus_four_verses_need_exactly_four_readers():
    sections=[_section('詩篇',119,1,24),_section('約翰福音',3,16,19)]
    slides,_,assigned=_readers_by_slide(sections,['A','B','C','D'])
    assert assigned==['A','B','C','D']
    assert [reader for label,reader in slides if label.startswith('約翰')]==['D']
    assert all(sum(len(page['lines']) for page in service._prepare_scripture_sections(sections,['A','B','C','D'])[0] if page['reader']==name)<=8 for name in assigned)


def test_long_text_spans_pages_without_adding_readers():
    section=_section('列王紀上',19,1,18)
    for verse in section['verses']:verse['text']='這是需要舒適行距與分頁的完整經文。'*3
    pages,summary,assigned=service._prepare_scripture_sections([section],['甲','乙','丙'])
    assert len(pages)>3 and assigned==['甲','乙','丙']
    assert summary==[{'label':'列王紀上 19:1-6','reader':'甲'}, {'label':'列王紀上 19:7-12','reader':'乙'}, {'label':'列王紀上 19:13-18','reader':'丙'}]
    assert all(sum(len(page['lines']) for page in pages if page['reader']==name)==6 for name in assigned)


def test_over_thirty_two_verses_requires_explicit_arrangement():
    with pytest.raises(ValueError,match='最多 4 位同工，每人最多 8 節'):
        _readers_by_slide([_section('詩篇',119,1,33)],['A','B','C','D'])


def test_duplicate_readers_are_still_refused():
    with pytest.raises(ValueError, match="讀經同工不可重複：A"):
        service._prepare_scripture_sections(OCT_4, ["A", "B", "A"])
