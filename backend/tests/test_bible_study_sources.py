from __future__ import annotations

import json

import pytest

from backend.bible_study import cuv
from backend.bible_study.passage import WHOLE_CHAPTER, Passage, find_passages, parse_passage
from backend.bible_study.sources import build, carson_pages, study_for, wang_claims
from backend.reference_commentary.store import VolumeStore


V2 = "carson-ebc-matthew-1995-v2"


@pytest.mark.parametrize(
    "text, start, end, slug",
    [
        ("太 20:28", (20, 28), (20, 28), "mat-20-28"),
        ("太 20:17-34", (20, 17), (20, 34), "mat-20-17-34"),
        # Crosses chapters: backend.api.scripture.reference_slugs cannot read this.
        ("太 19:27–20:16", (19, 27), (20, 16), "mat-19-27-20-16"),
        ("太 20", (20, 1), (20, WHOLE_CHAPTER), "mat-20"),
        ("马太福音20:17-19", (20, 17), (20, 19), "mat-20-17-19"),
        ("Matthew 20:28", (20, 28), (20, 28), "mat-20-28"),
    ],
)
def test_parse_passage(text, start, end, slug):
    passage = parse_passage(text)
    assert (passage.book, passage.start, passage.end, passage.slug) == ("mat", start, end, slug)


@pytest.mark.parametrize("text", ["舊約聖經", "太 20:28；20:30", "太 20:30-17"])
def test_parse_passage_rejects_anything_but_one_passage(text):
    with pytest.raises(ValueError):
        parse_passage(text)


def test_overlap_is_by_verse_not_by_chapter():
    v28 = parse_passage("太 20:28")
    assert v28.overlaps(parse_passage("太 20:20-28"))
    assert v28.overlaps(parse_passage("太 19:27–20:28"))
    assert not v28.overlaps(parse_passage("太 20:1-16"))
    assert not v28.overlaps(parse_passage("可 10:45"))


def _volume(tmp_path):
    store = VolumeStore(tmp_path / "rc" / V2, V2)
    src = {"inbox_file": "Matthew/ch20/a.pdf"}
    for page, text in [
        (429, "#### 20:17–19\nthird prediction"),
        (430, "#### 20:20–28\nthe request"),
        (431, "the cup"),
        (432, "a ransom"),
        (434, "#### 20:29–34\ntwo blind men"),
        (435, "sight"),
    ]:
        store.ingest(printed_page=page, image=f"jpeg-{page}".encode(), text=text, source=src, chapter=20)
    return tmp_path / "rc"


def test_carson_pages_carry_a_heading_over_continuation_pages(tmp_path):
    result = carson_pages(parse_passage("太 20:28"), _volume(tmp_path))
    discusses = {p["printed_page"]: p["discusses_passage"] for p in result["pages"]}
    # 431 and 432 have no heading but continue 20:20–28. 434 follows a missing
    # page, so nothing is carried over the gap.
    assert discusses == {"429": False, "430": True, "431": True, "432": True, "434": False, "435": False}
    assert result["missing_pages"] == [433]
    assert result["chapters_without_pages"] == []


def test_carson_reports_unscanned_chapters(tmp_path):
    result = carson_pages(parse_passage("太 20:29–21:11"), _volume(tmp_path))
    assert result["chapters_without_pages"] == [21]
    assert carson_pages(parse_passage("可 10:45"), _volume(tmp_path))["note"]


def _claim(refs, lecture="2018 NYSC 專題：馬太福音釋經（七）1"):
    return {"statement": "…", "claim_type": "interpretive_judgment", "scripture_refs": refs, "occurrences": [{"lecture": lecture}]}


def test_wang_claims_match_by_verse_and_group_by_sermon():
    rows = [
        ("c1", "candidate", _claim(["馬太福音 20:28", "Mark 10:45"])),
        ("c2", "candidate", _claim(["Matthew 20:1-16"])),  # same chapter, other verses
        ("c3", "candidate", _claim(["太 20:20-28"], lecture="S 211205 多2-3靠恩典活")),
        ("c4", "candidate", _claim(["Matthew 19:27-20:28"])),  # crosses chapters
    ]
    result = wang_claims(parse_passage("太 20:28"), rows)
    assert [c["claim_id"] for c in result["claims"]] == ["c1", "c4", "c3"]
    assert result["claims"][0]["scripture_refs"] == ["馬太福音 20:28"]
    assert result["sermons"] == [
        {"sermon": "2018 NYSC 專題：馬太福音釋經（七）1", "claims": 2},
        {"sermon": "S 211205 多2-3靠恩典活", "claims": 1},
    ]


def test_build_keeps_hand_edits_unless_refreshed(tmp_path):
    study = study_for("2026-10-02", "太 20:28", root=tmp_path / "studies")
    fetched = []

    def fetch(book, chapter):
        fetched.append((book, chapter))
        return {"28": "正如人子來，不是要受人的服事……"}

    kwargs = dict(
        passage_text="太 20:28",
        claim_rows=lambda p: [("c1", "candidate", _claim(["馬太福音 20:28"]))],
        commentary_root=_volume(tmp_path),
        cuv_root=tmp_path / "cuv",
        fetch=fetch,
    )
    sources = build(study, **kwargs)
    assert study.directory.name == "2026-10-02-mat-20-28"
    assert sources["cuv_chapters"] == ["MAT/20"]
    assert json.loads((tmp_path / "cuv" / "MAT" / "20.json").read_text())["28"].startswith("正如人子來")

    with pytest.raises(FileExistsError):
        build(study, **kwargs)
    build(study, refresh=True, **kwargs)
    assert len(list(study.directory.glob("sources.*.json"))) == 1  # the old one, kept
    assert fetched == [("MAT", 20)]  # second build read the cache


def test_study_date_must_be_iso():
    with pytest.raises(ValueError):
        study_for("10/2/2026", "太 20:28")


def test_find_passages_skips_unknown_books():
    assert find_passages("LXX 77:2") == []
    assert find_passages("馬太福音 20:1-16") == [Passage("mat", (20, 1), (20, 16))]


def test_cuv_cache_is_read_before_the_network(tmp_path):
    (tmp_path / "MAT").mkdir()
    (tmp_path / "MAT" / "20.json").write_text('{"1": "cached"}', encoding="utf-8")

    def fetch(book, chapter):
        raise AssertionError("network used")

    assert cuv.chapter("mat", 20, root=tmp_path, fetch=fetch) == {"1": "cached"}
