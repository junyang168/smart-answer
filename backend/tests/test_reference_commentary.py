from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from backend.config.reference_commentary_paths import reference_commentary_paths
from backend.reference_commentary import inbox as inbox_module
from backend.reference_commentary.images import PageImage
from backend.reference_commentary.ocr import OcrPage, OcrUnavailable, ocr_page, parse_output
from backend.reference_commentary.store import VolumeStore, find_passages
from backend.reference_commentary.volumes import resolve_folder


V2 = "carson-ebc-matthew-1995-v2"


def test_paths_resolve_without_creating(tmp_path):
    paths = reference_commentary_paths(tmp_path / "data", inbox=tmp_path / "icloud")
    assert paths.root == (tmp_path / "data" / "reference-commentary").resolve()
    assert paths.inbox == tmp_path / "icloud"
    assert not paths.root.exists()


def test_folder_decides_volume():
    assert resolve_folder("Matthew", "ch12")[0].volume_id == "carson-ebc-matthew-1995-v1"
    assert resolve_folder("matthew", "Chapter 20") == (resolve_folder("Matthew", "ch20")[0], 20)
    assert resolve_folder("Matthew", "ch20")[0].volume_id == V2
    for book, chapter in [("Mark", "ch1"), ("Matthew", "20"), ("Matthew", "ch29")]:
        with pytest.raises(ValueError):
            resolve_folder(book, chapter)


def test_parse_output_reads_and_strips_page_mark():
    page = parse_output("```markdown\n<!-- Page 427 -->\n\n3\"About the third hour\n```")
    assert page.printed_page == "427"
    assert page.text == '3"About the third hour'
    assert parse_output("<!-- Page xii -->\nPreface").printed_page == "xii"
    assert parse_output("<!-- Page ? -->\nbody").printed_page is None
    assert parse_output("no mark").printed_page is None


def test_find_passages_from_section_headings():
    text = "#### 20:17–19\nbody\n### 20:29-34\n###### 21:1–11\n## Notes\n### ²⁰Then the mother"
    assert find_passages(text) == ["20:17-19", "20:29-34", "21:1-11"]
    assert find_passages("## 2. Humility\n**18:3-4**\nbody **18:5** inline\n20:3\n") == ["18:3-4"]


def test_ocr_retries_rate_limit_then_gives_up():
    calls = []

    def flaky(_, __):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return "<!-- Page 5 -->\ntext"

    assert ocr_page(b"x", call=flaky, sleep=lambda _: None).printed_page == "5"
    with pytest.raises(OcrUnavailable):
        ocr_page(b"x", call=lambda *_: (_ for _ in ()).throw(RuntimeError("429")), attempts=2, sleep=lambda _: None)
    with pytest.raises(ValueError):
        ocr_page(b"x", call=lambda *_: (_ for _ in ()).throw(ValueError("bad request")), sleep=lambda _: None)


def test_missing_page_mark_asks_for_the_number_alone():
    from backend.reference_commentary.ocr import PAGE_NUMBER_PROMPT

    replies = {PAGE_NUMBER_PROMPT: "428"}
    page = ocr_page(b"x", call=lambda _, prompt: replies.get(prompt, "**3-7** body"), sleep=lambda _: None)
    assert (page.printed_page, page.text) == ("428", "**3-7** body")
    replies[PAGE_NUMBER_PROMPT] = "?"
    assert ocr_page(b"x", call=lambda _, prompt: replies.get(prompt, "body"), sleep=lambda _: None).printed_page is None


def test_store_never_overwrites_and_protects_proofread(tmp_path):
    store = VolumeStore(tmp_path / V2, V2)
    src = {"inbox_file": "Matthew/ch20/a.pdf"}
    assert store.ingest(printed_page=427, image=b"img1", text="first […] text", source=src, chapter=20).action == "created"
    record = store.page("427")
    assert record["gaps"] == [{"line": 1, "column": 7}]
    first_text = record["text"]

    # Rescan while unproofread: new image and text become current, old kept.
    assert store.ingest(printed_page="427", image=b"img2", text="second", source=src, chapter=20).action == "updated"
    record = store.page(427)
    assert record["image_history"][0]["sha256"] != record["image"]["sha256"]
    assert (store.directory / first_text["file"]).exists()
    assert [r["source"] for r in record["revisions"]] == ["ocr", "ocr"]

    # Proofread, then rescan: proofread text stays current.
    store.proofread(427, "second, corrected", expected_sha256=record["text"]["sha256"])
    with pytest.raises(ValueError):
        store.proofread(427, "stale", expected_sha256=record["text"]["sha256"])
    assert store.ingest(printed_page=427, image=b"img3", text="third", source=src).action == "rescan_kept_proofread"
    record = store.page(427)
    assert store.read_text(record) == "second, corrected\n"
    assert record["status"] == "proofread"
    assert record["pending_ocr"]["sha256"]


def _inbox(tmp_path):
    paths = reference_commentary_paths(tmp_path / "data", inbox=tmp_path / "icloud")
    folder = paths.inbox / "Matthew" / "ch20"
    folder.mkdir(parents=True)
    return paths, folder


def _old(path: Path) -> Path:
    os.utime(path, (1, 1))
    return path


def test_inbox_run_ingests_pages_reports_gaps_and_skips_known_scans(tmp_path):
    paths, folder = _inbox(tmp_path)
    pdf = folder / "Scanned Document.pdf"
    pdf.write_bytes(b"pdf-1")
    _old(pdf)
    fresh = folder / "IMG_1.HEIC"
    fresh.write_bytes(b"still syncing")  # mtime now: left for the next run

    pages = {1: "<!-- Page 427 -->\n#### 20:3\nA", 2: "<!-- Page 429 -->\nC", 3: "<!-- Page ? -->\nD"}
    scan = lambda path: [PageImage(f"jpeg{n}".encode(), n) for n in pages]
    ocr = lambda jpeg: parse_output(pages[int(jpeg.decode()[4:])])

    result = inbox_module.run_once(paths, scan=scan, ocr=ocr, download=lambda _: None)
    assert result["processed"] == 1

    store = VolumeStore(paths.volume(V2), V2)
    assert [p["printed_page"] for p in store.pages()] == ["427", "429"]
    assert store.page(427)["chapters"] == [20]
    assert store.page(427)["image"]["source"]["pdf_page"] == 1
    assert (paths.volume(V2) / "unassigned").is_dir()

    problems = (folder / "_problems.md").read_text(encoding="utf-8")
    assert "缺页：428" in problems
    assert "PDF 第 3 页" in problems
    assert "IMG_1" not in problems

    ledger = json.loads(paths.inbox_ledger.read_text())
    assert len(ledger["scans"]) == 1

    # Same file again: nothing new is OCR'd.
    def must_not_ocr(_):
        raise AssertionError("known scan was OCR'd again")

    assert inbox_module.run_once(paths, scan=scan, ocr=must_not_ocr, download=lambda _: None)["processed"] == 0


def test_inbox_retries_when_vertex_unavailable_and_requests_evicted_files(tmp_path):
    paths, folder = _inbox(tmp_path)
    photo = folder / "a.jpg"
    photo.write_bytes(b"x")
    _old(photo)
    (folder / ".b.pdf.icloud").write_bytes(b"")
    requested = []

    def down(_):
        raise OcrUnavailable("429")

    result = inbox_module.run_once(
        paths, scan=lambda p: [PageImage(b"j", None)], ocr=down, download=requested.append
    )
    assert result == {"processed": 0, "pending_download": 1}
    assert requested[0].name == ".b.pdf.icloud"
    assert not paths.inbox_ledger.exists()

    ok = inbox_module.run_once(
        paths,
        scan=lambda p: [PageImage(b"j", None)],
        ocr=lambda _: OcrPage("430", "text", "m"),
        download=lambda _: None,
    )
    assert ok["processed"] == 1
    assert not (folder / "_problems.md").exists()


def test_inbox_reports_unknown_folder(tmp_path):
    paths = reference_commentary_paths(tmp_path / "data", inbox=tmp_path / "icloud")
    folder = paths.inbox / "Matthew" / "misc"
    folder.mkdir(parents=True)
    photo = folder / "a.jpg"
    photo.write_bytes(b"x")
    _old(photo)
    inbox_module.run_once(paths, scan=lambda p: [], ocr=lambda _: None, download=lambda _: None)
    assert "should look like ch21" in (folder / "_problems.md").read_text(encoding="utf-8")


def test_legacy_import_splits_page_marks_and_refuses_crowded_folder(tmp_path, monkeypatch):
    from backend.reference_commentary import import_legacy

    assert import_legacy.split_by_page_marks("intro\n<!-- Page 396 -->\nA\n<!-- Page 397 -->\nB") == {"396": "A", "397": "B"}

    photos = tmp_path / "photos"
    photos.mkdir()
    for name in ["p1.heic", "p2.heic", "cat.jpg", "dog.png", "trip.jpg", "menu.png"]:
        (photos / name).write_bytes(b"x")
    md = tmp_path / "chapter.md"
    md.write_text("<!-- Page 1 -->\nA\n<!-- Page 2 -->\nB", encoding="utf-8")
    monkeypatch.setattr(import_legacy, "ask_page_number", lambda _: pytest.fail("asked Gemini"))
    monkeypatch.setattr(import_legacy, "photo_to_jpeg", lambda _: pytest.fail("converted a photo"))
    assert import_legacy.main(["--folder", "Matthew/ch18", "--images", str(photos), "--pages-md", str(md), "--dry-run"]) == 1
