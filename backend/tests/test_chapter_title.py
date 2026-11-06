"""按章页面的一句话标题：程序从存档的投影片预先生成；投影片重做过就作废。"""

import json

from backend.pipeline import chapter_exposition, chapter_material, chapter_slides, chapter_title, original_audio_index
from backend.pipeline.chapter_material import Chapter

CHAPTER = Chapter(passage="mat-5-1-48", series="S", lectures=("L",))


class FakeClient:
    model = "fake"

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def generate_json(self, system, user, schema):
        self.prompts.append(user)
        return self.answers.pop(0)


def _slides(tmp_path, title="八福與彌賽亞的身份"):
    decisions = chapter_exposition.record_path(tmp_path, "mat-5-1-48", "L")
    decisions.parent.mkdir(parents=True, exist_ok=True)
    decisions.write_text("{}")
    target = chapter_slides.record_path(tmp_path, "mat-5-1-48", "L")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        "schema_version": chapter_slides.SCHEMA_VERSION,
        "decisions_sha256": chapter_slides.decisions_sha(tmp_path, "mat-5-1-48", "L"),
        "slides": [{"title": title, "points": [{"text": "祝福顯明講話者是誰"}]}],
    }, ensure_ascii=False))


def test_the_title_is_written_from_the_saved_slides_and_used_by_the_page(tmp_path, monkeypatch):
    monkeypatch.setitem(chapter_material.CHAPTERS, "mat-5-1-48", CHAPTER)
    _slides(tmp_path)
    client = FakeClient([{"title": "八福與彌賽亞的身份。"}])
    report = chapter_title.decide(CHAPTER, data_base_dir=tmp_path, client=client)
    assert report == {"passage": "mat-5-1-48", "status": "saved", "title": "八福與彌賽亞的身份"}
    assert "八福與彌賽亞的身份" in client.prompts[0] and "祝福顯明講話者是誰" in client.prompts[0]
    assert original_audio_index.passage_title("mat-5-1-48", tmp_path) == "八福與彌賽亞的身份"
    assert chapter_title.decide(CHAPTER, data_base_dir=tmp_path, client=client)["status"] == "unchanged"


def test_redone_slides_void_the_title_and_the_page_falls_back(tmp_path, monkeypatch):
    monkeypatch.setitem(chapter_material.CHAPTERS, "mat-5-1-48", CHAPTER)
    _slides(tmp_path)
    chapter_title.decide(CHAPTER, data_base_dir=tmp_path, client=FakeClient([{"title": "舊的一句"}]))
    _slides(tmp_path, title="重做過的投影片")
    assert chapter_title.load(tmp_path, "mat-5-1-48") is None
    assert original_audio_index.passage_title("mat-5-1-48", tmp_path) == original_audio_index.PASSAGE_TITLES["mat-5-1-48"]


def test_a_title_too_long_is_rejected_not_saved(tmp_path, monkeypatch):
    monkeypatch.setitem(chapter_material.CHAPTERS, "mat-5-1-48", CHAPTER)
    _slides(tmp_path)
    report = chapter_title.decide(CHAPTER, data_base_dir=tmp_path, client=FakeClient([{"title": "長" * 40}] * 2))
    assert report["status"] == "rejected"
    assert not chapter_title.record_path(tmp_path, "mat-5-1-48").exists()


def test_no_title_without_slides_for_every_lecture(tmp_path):
    report = chapter_title.decide(CHAPTER, data_base_dir=tmp_path, client=FakeClient([]))
    assert report["status"] == "no_slides"


def test_a_title_too_long_gets_one_retry_told_what_was_wrong(tmp_path, monkeypatch):
    monkeypatch.setitem(chapter_material.CHAPTERS, "mat-5-1-48", CHAPTER)
    _slides(tmp_path)
    client = FakeClient([{"title": "長" * 40}, {"title": "八福"}])
    report = chapter_title.decide(CHAPTER, data_base_dir=tmp_path, client=client)
    assert report["status"] == "saved" and report["title"] == "八福"
    assert "40 字" in client.prompts[1]
