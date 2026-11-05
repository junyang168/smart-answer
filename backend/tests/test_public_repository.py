"""文库入口：书卷、来源、章。"""

from pathlib import Path

from backend.api import public_repository as repo
from backend.pipeline.sermon_catalog import normalize_sermon_source

CATALOG = {"records": [
    {"transcript_id": "a", "title": "A", "source_category_label": "達拉斯聖道教會",
     "source_organization": "達拉斯聖道教會", "canonical_scriptures": ["Matt.5.3-Matt.5.10", "Isa.61.1"],
     # 提到過的書卷不算：哥林多前書因此有 124 篇。
     "books": ["馬太福音", "哥林多前書"]},
    {"transcript_id": "b", "title": "B", "source_category_label": "其他來源",
     "source_organization": "主愛中華錄音事奉", "canonical_scriptures": ["Matt.24.3"]},
    {"transcript_id": "c", "title": "C", "source_category_label": "來源待確認", "canonical_scriptures": []},
]}
COVERAGE = {"source_directory": [
    {"source_id": "n1", "source_category": "notes_to_manuscript", "title": "5 章", "assigned_chapters": [5],
     "source_url": "/resources/notes_to_manuscript_series/s/5"},
    {"source_id": "n2", "source_category": "notes_to_manuscript", "title": "引言", "assigned_chapters": []},
]}


def test_sources_and_books_come_from_the_data_not_a_fixed_list():
    materials = repo.items(CATALOG, COVERAGE)
    result = repo.books(materials)
    assert {s["name"] for s in result["sources"]} == {"達拉斯聖道教會", "主愛中華錄音事奉", "教授釋經筆記"}
    assert [b["slug"] for b in result["books"]] == ["isa", "mat"]
    assert result["books"][0]["testament"] == "old"
    assert "1co" not in {b["slug"] for b in result["books"]}


def test_a_book_lists_only_chapters_that_have_material():
    materials = repo.items(CATALOG, COVERAGE)
    result = repo.book_chapters(materials, "mat", [])
    assert [c["chapter"] for c in result["chapters"]] == [5, 24]
    five = result["chapters"][0]
    assert {(s["name"], s["count"], s["unit"]) for s in five["sources"]} == {("達拉斯聖道教會", 1, "篇"), ("教授釋經筆記", 1, "講")}


def test_the_chapter_page_sits_under_its_own_source():
    catalog = {"records": [{**CATALOG["records"][0], "source_organization": "紐約靈命進深會"}]}
    result = repo.chapter(repo.items(catalog, COVERAGE), "mat", 5, [])
    by_name = {s["name"]: s for s in result["sources"]}
    assert by_name["紐約靈命進深會"]["page"]["passage"] == "mat-5-1-48"
    assert "page" not in by_name["教授釋經筆記"]
    assert by_name["教授釋經筆記"]["items"][0]["href"] == "/resources/notes_to_manuscript_series/s/5"


def test_gtl_org_is_its_own_source():
    source = normalize_sermon_source({"source": "http://www.gtl.org"})
    assert source["source_organization"] == "主愛中華錄音事奉"
    assert source["source_url"] == "http://www.gtl.org"


def test_a_chapter_with_its_own_page_carries_that_pages_one_line_title():
    result = repo.book_chapters(repo.items(CATALOG, COVERAGE), "mat", [])
    five, twenty_four = result["chapters"]
    from backend.api.config import DATA_BASE_PATH
    from backend.pipeline import original_audio_index

    assert five["page"]["title"] == original_audio_index.passage_title("mat-5-1-48", Path(DATA_BASE_PATH))
    assert five["page"]["title"]
    assert twenty_four["page"] is None


def test_a_chapter_page_is_labelled_by_its_recordings(tmp_path, monkeypatch):
    from backend.pipeline import original_audio_index

    data = tmp_path / "data"
    video = tmp_path / "video"
    video.mkdir()
    (video / "A.mp4").write_bytes(b"")
    (video / "B.mp3").write_bytes(b"")
    assert original_audio_index.media_file(data, "A") == (video / "A.mp4", "video")
    assert original_audio_index.media_file(data, "B") == (video / "B.mp3", "audio")
    assert original_audio_index.media_file(data, "C") is None
