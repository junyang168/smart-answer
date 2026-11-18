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


def test_a_book_lists_only_chapters_that_have_material(monkeypatch):
    monkeypatch.setattr(repo, "notes_pages", lambda: {})
    materials = repo.items(CATALOG, COVERAGE)
    result = repo.book_chapters(materials, "mat", [])
    assert [c["chapter"] for c in result["chapters"]] == [5, 24]
    five = result["chapters"][0]
    assert {(s["name"], s["count"], s["unit"]) for s in five["sources"]} == {("達拉斯聖道教會", 1, "篇"), ("教授釋經筆記", 1, "講")}


def test_the_chapter_page_sits_under_its_own_source(monkeypatch):
    monkeypatch.setattr(repo, "notes_pages", lambda: {})
    catalog = {"records": [{**CATALOG["records"][0], "source_organization": "紐約靈命進深會"}]}
    result = repo.chapter(repo.items(catalog, COVERAGE), "mat", 5, [])
    by_name = {s["name"]: s for s in result["sources"]}
    assert by_name["紐約靈命進深會"]["page"]["passage"] == "mat-5-1-48"
    assert "page" not in by_name["教授釋經筆記"]
    assert by_name["教授釋經筆記"]["items"][0]["href"] == "/resources/notes_to_manuscript_series/s/5"


def test_the_notes_page_sits_under_the_notes_source_with_its_own_title(monkeypatch):
    page = {"kind": "chapter", "source": "教授釋經筆記", "passage": "mat-5-1-48", "title": "筆記那一句",
            "media": "audio", "ai_voice": True}
    monkeypatch.setattr(repo, "notes_pages", lambda: {("mat", 5): page})
    result = repo.chapter(repo.items(CATALOG, COVERAGE), "mat", 5, [])
    by_name = {s["name"]: s for s in result["sources"]}
    assert by_name["教授釋經筆記"]["page"]["title"] == "筆記那一句"
    chapters = repo.book_chapters(repo.items(CATALOG, COVERAGE), "mat", [])["chapters"]
    assert page in chapters[0]["pages"]


def test_gtl_org_is_its_own_source():
    source = normalize_sermon_source({"source": "http://www.gtl.org"})
    assert source["source_organization"] == "主愛中華錄音事奉"
    assert source["source_url"] == "http://www.gtl.org"


def test_a_chapter_with_its_own_page_carries_that_pages_one_line_title(monkeypatch):
    monkeypatch.setattr(repo, "notes_pages", lambda: {})
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


def test_matthew_navigation_distinguishes_series_from_recording_sources(monkeypatch):
    ny = "紐約靈命進深會"
    dallas = "達拉斯聖道教會"
    unknown = "其他教會（名稱未記錄）"
    gtl = "主愛中華錄音事奉"

    def sermon(key, source, scripture, *, series=None, order=None):
        return {"transcript_id": key, "title": key, "source_organization": source,
                "canonical_scriptures": [scripture], "series_id": series, "series_order": order,
                "catalog_primary_passage": {"osis": scripture, "display": scripture}}

    council = "马太福音 24-28 释经"
    records = [
        sermon("nysc-26", ny, "Matt.26.1"),
        sermon("nysc-5", ny, "Matt.5.1"),
        sermon("retreat-4", unknown, "Matt.28.20", series=council, order=4),
        sermon("retreat-2", unknown, "Matt.27.45", series=council, order=2),
        sermon("retreat-3", unknown, "Matt.28.11", series=council, order=3),
        sermon("retreat-1", unknown, "Matt.24", series=council, order=1),
        sermon("different-visit", unknown, "Matt.25"),
        sermon("S 200503", dallas, "Matt.24", series="馬太福音24 章 釋經", order=2),
        sermon("S 200531", dallas, "Matt.24.29"),
        sermon("S 200426", dallas, "Matt.24", series="馬太福音24 章 釋經", order=1),
        sermon("dallas-marriage", dallas, "Matt.19.3"),
        sermon("canon", gtl, "Matt.22.14"),
    ]
    lecture = {"kind": "chapter", "source": repo.NOTES, "passage": "mat-17-1-27", "ai_voice": True}
    monkeypatch.setattr(repo, "chapter_pages", lambda: {})
    monkeypatch.setattr(repo, "notes_pages", lambda: {("mat", 17): lecture})
    materials = repo.items({"records": records}, COVERAGE)
    result = repo.book_chapters(materials, "mat", [])
    navigation = result["navigation"]
    assert [series["id"] for series in navigation["series"]] == ["nysc", "notes", "matthew-2007"]
    ny_series, notes_series, retreat = navigation["series"]
    assert ny_series["chapters"] == [5, 26]
    assert notes_series["chapters"] == [5, 17]
    assert [lecture["title"] for lecture in retreat["items"]] == [
        "retreat-1", "retreat-2", "retreat-3", "retreat-4",
    ]
    assert retreat["items"][0]["scripture"] == "Matt.24"
    assert retreat["items"][0]["href"] == "/resources/sermons/retreat-1"
    assert [group["id"] for group in navigation["related"]] == ["dallas-matthew-24"]
    assert [item["title"] for item in navigation["related"][0]["items"]] == [
        "S 200426", "S 200503", "S 200531",
    ]
    assert {item["title"] for item in navigation["talks"]} == {"canon", "dallas-marriage", "different-visit"}
    seventeen = next(row for row in result["chapters"] if row["chapter"] == 17)
    assert seventeen["sources"][0]["name"] == repo.NOTES
    assert seventeen["pages"] == [lecture]
    assert repo.chapter(materials, "mat", 17, [])["sources"][0]["page"] == lecture
    assert "navigation" not in repo.book_chapters(materials, "rom", [])


def test_matthew_chapter_browsing_excludes_editorial_articles(monkeypatch):
    monkeypatch.setattr(repo, "chapter_pages", lambda: {})
    monkeypatch.setattr(repo, "notes_pages", lambda: {})
    article = {"title": "編輯綜合", "href": "/article", "chapters": {("mat", 5), ("mat", 7), ("isa", 61)}}
    materials = repo.items(CATALOG, COVERAGE)
    result = repo.book_chapters(materials, "mat", [article])
    assert [row["chapter"] for row in result["chapters"]] == [5, 24]
    assert all(not row["articles"] for row in result["chapters"])
    assert repo.book_chapters(materials, "isa", [article])["chapters"][0]["articles"] == [article]
