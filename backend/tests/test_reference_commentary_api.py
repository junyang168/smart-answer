from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.api import reference_commentary as api
from backend.config.reference_commentary_paths import reference_commentary_paths
from backend.reference_commentary.store import VolumeStore


V2 = "carson-ebc-matthew-1995-v2"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    paths = reference_commentary_paths(tmp_path / "data", inbox=tmp_path / "icloud")
    monkeypatch.setattr(api, "paths", lambda: paths)
    store = VolumeStore(paths.volume(V2), V2)
    src = {"inbox_file": "Matthew/ch20/a.pdf"}
    store.ingest(printed_page=427, image=b"jpeg-427", text="about the third hour", source=src, chapter=20)
    store.ingest(printed_page=429, image=b"jpeg-429", text="#### 20:17–19\nthe verb ταπεινώσει", source=src, chapter=20)
    store.ingest(
        printed_page=436,
        image=b"jpeg-436",
        text="end of 20\n###### 21:1–11\nBethany",
        source=src,
        chapter=20,
    )
    unassigned = paths.volume(V2) / "unassigned"
    unassigned.mkdir()
    (unassigned / "abcdef012345-002.jpg").write_bytes(b"jpeg-428")
    (unassigned / "abcdef012345-002.md").write_text("**3-7** twelve hours\n", encoding="utf-8")
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app), store


def test_shelf_counts_missing_and_unassigned(client):
    http, _ = client
    volumes = {v["volume_id"]: v for v in http.get("/admin/reference-commentary").json()["volumes"]}
    v2 = volumes[V2]
    assert [p["printed_page"] for p in v2["pages"]] == ["427", "429", "436"]
    assert v2["missing_pages"] == [428, *range(430, 436)]
    assert v2["unassigned"] == [{"id": "abcdef012345-002"}]
    assert v2["confirmed"] is False
    assert volumes["carson-ebc-matthew-1995-v1"]["pages"] == []


def test_chapter_includes_pages_whose_headings_name_it(client):
    http, _ = client
    chapter = http.get("/admin/reference-commentary/chapters/20").json()
    assert [p["printed_page"] for p in chapter["pages"]] == ["427", "429", "436"]
    assert chapter["pages"][0]["text"] == "about the third hour\n"
    # p. 436 was scanned under ch20 but opens 21:1-11, so chapter 21 starts there.
    assert [p["printed_page"] for p in http.get("/admin/reference-commentary/chapters/21").json()["pages"]] == ["436"]
    assert http.get("/admin/reference-commentary/chapters/5").status_code == 404


def test_page_detail_image_and_neighbours(client):
    http, _ = client
    page = http.get(f"/admin/reference-commentary/volumes/{V2}/pages/429").json()
    assert (page["previous_page"], page["next_page"]) == ("427", "436")
    assert page["passages"] == ["20:17-19"]
    assert http.get(f"/admin/reference-commentary/volumes/{V2}/pages/429/image").content == b"jpeg-429"
    assert http.get(f"/admin/reference-commentary/volumes/{V2}/pages/428").status_code == 404
    assert http.get("/admin/reference-commentary/volumes/nope/pages/1").status_code == 404


def test_proofread_saves_version_and_refuses_stale_edit(client):
    http, _ = client
    url = f"/admin/reference-commentary/volumes/{V2}/pages/427"
    page = http.get(url).json()
    saved = http.post(f"{url}/proofread", json={"text": "About the third hour", "expected_sha256": page["text_sha256"]})
    assert saved.status_code == 200
    body = saved.json()
    assert body["status"] == "proofread"
    assert [r["source"] for r in body["revisions"]] == ["ocr", "proofread"]
    old = http.get(f"{url}/versions/{page['text_sha256']}").json()
    assert old["text"] == "about the third hour\n"
    stale = http.post(f"{url}/proofread", json={"text": "x", "expected_sha256": page["text_sha256"]})
    assert stale.status_code == 409
    assert http.post(f"{url}/proofread", json={"text": " ", "expected_sha256": body["text_sha256"]}).status_code == 400


def test_assign_unassigned_page(client):
    http, store = client
    base = f"/admin/reference-commentary/volumes/{V2}/unassigned"
    assert http.get(f"{base}/abcdef012345-002/image").content == b"jpeg-428"
    assert http.get(f"{base}/..%2Fpages.json/image").status_code == 404
    assert http.post(f"{base}/abcdef012345-002/assign", json={"printed_page": "x1"}).status_code == 400
    result = http.post(f"{base}/abcdef012345-002/assign", json={"printed_page": "428"}).json()
    assert result == {"volume_id": V2, "printed_page": "428", "action": "created"}
    assert store.read_text(store.page(428)) == "**3-7** twelve hours\n"
    assert http.post(f"{base}/abcdef012345-002/assign", json={"printed_page": "428"}).status_code == 404


def test_search_ignores_case_and_greek_accents(client):
    http, _ = client
    hits = http.get("/admin/reference-commentary/search", params={"q": "ταπεινωσει"}).json()["hits"]
    assert [(h["printed_page"], h["snippet"]) for h in hits] == [("429", "the verb ταπεινώσει")]
    assert [h["printed_page"] for h in http.get("/admin/reference-commentary/search", params={"q": "BETHANY"}).json()["hits"]] == ["436"]
    assert http.get("/admin/reference-commentary/search", params={"q": "a"}).status_code == 400
