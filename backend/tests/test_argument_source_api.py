import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import backend.api.sc_api.transcript_review_router as tr_router
from backend.api.sc_api.transcript_review import ReviewError, ReviewStore
from backend.pipeline.argument_source import (
    SCHEMA_VERSION,
    build_argument_source_package,
    project_argument_source,
)
from backend.pipeline.slide_change_pilot import save_json


def _setup_sermon(tmp_path: Path, *, with_slides: bool = True, confirmed: bool = True) -> Path:
    data_root = tmp_path / "data"
    (data_root / "config").mkdir(parents=True)
    save_json(data_root / "config" / "sermon.json", [
        {"item": "S 200607", "title": "羅馬書", "assigned_to": "user@example.com", "assignment_epoch": 1}
    ])
    rows = [
        {"index": "sub-1", "text": "## 揀選的真義", "type": "subtitle"},
        {"index": "71", "text": "神集體的揀選，是為了救恩。", "type": "content"},
        {"index": "97", "text": "因他們的過失，救恩臨到外邦人。", "type": "content"},
    ]
    save_json(data_root / "script_review" / "S 200607.json", rows)
    script_sha = hashlib.sha256((data_root / "script_review" / "S 200607.json").read_bytes()).hexdigest()

    review_dir = data_root / "transcript_review" / "S 200607"
    review_dir.mkdir(parents=True)
    context = {
        "schema_version": "transcript-review-context.v1",
        "script_sha256": script_sha,
        "revision": 0,
        "paragraph_bindings": {
            "71": {"start_ms": 140000, "end_ms": 232000, "source_paragraph_id": "71"},
            "97": {"start_ms": 232000, "end_ms": 453000, "source_paragraph_id": "97"},
        },
        "visual_assets": {},
        "occurrences": {},
        "groups": [],
        "reviews": {},
    }
    if with_slides:
        asset_dir = review_dir / "assets" / "a1"
        asset_dir.mkdir(parents=True)
        (asset_dir / "original.png").write_bytes(b"png-bytes")
        svg_text = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="854" height="480" viewBox="0 0 854 480">'
            '<rect x="0" y="0" width="854" height="480" fill="#FFFFFF"/>'
            '<text id="tr_0" fill="#111827"><tspan x="10" y="20">ἡ ἐκλογή</tspan></text>'
            '</svg>'
        )
        (asset_dir / "reconstruction.svg").write_text(svg_text, encoding="utf-8")
        img_sha = hashlib.sha256((asset_dir / "original.png").read_bytes()).hexdigest()
        svg_sha = hashlib.sha256((asset_dir / "reconstruction.svg").read_bytes()).hexdigest()
        context["visual_assets"]["a1"] = {
            "visible_text": "ἡ ἐκλογή",
            "image": "transcript_review/S 200607/assets/a1/original.png",
            "image_sha256": img_sha,
            "svg_path": "transcript_review/S 200607/assets/a1/reconstruction.svg",
            "svg_sha256": svg_sha,
            "content": {"text_regions": []},
        }
        context["occurrences"] = {
            "c-01": {"asset_id": "a1", "time_ms": 147840, "capture_ms": 147840},
            "c-02": {"asset_id": "a1", "time_ms": 235000, "capture_ms": 235000},
        }
        context["groups"] = [
            {"id": "g-c-01", "member_ids": ["c-01"], "slide_id": "c-01", "before_paragraph_id": "71", "hidden": False, "reason": "說明集體揀選", "needs_review": False},
            {"id": "g-c-02", "member_ids": ["c-02"], "slide_id": "c-02", "before_paragraph_id": "97", "hidden": True, "reason": "pure_scripture_only", "needs_review": False},
        ]
        if confirmed:
            context["reviews"]["g:g-c-01"] = {"decision": "confirmed", "sha256": "x", "actor": "user@example.com"}

    save_json(review_dir / "context.json", context)
    return data_root


def test_argument_source_handles_no_slides(tmp_path):
    root = _setup_sermon(tmp_path, with_slides=False)
    pkg = build_argument_source_package(root, "S 200607")
    assert pkg["schema_version"] == SCHEMA_VERSION
    assert [p["paragraph_id"] for p in pkg["paragraphs"]] == ["71", "97"]
    assert pkg["visual_blocks"] == []
    assert pkg["visual_source_attestations"] == {}
    proj = project_argument_source(pkg)
    assert len(proj.spoken_rows) == 2 and proj.visual_blocks == ()


def test_argument_source_enforces_confirmation_gate_and_excludes_hidden(tmp_path):
    root = _setup_sermon(tmp_path, with_slides=True, confirmed=False)
    strict_pkg = build_argument_source_package(root, "S 200607", require_confirmed_visuals=True)
    assert strict_pkg["stats"] == {
        "paragraph_count": 2,
        "admitted_visual_count": 0,
        "pending_visual_count": 1,
        "excluded_hidden_count": 1,
    }
    assert strict_pkg["visual_source_attestations"] == {}

    confirmed_root = _setup_sermon(tmp_path / "confirmed", with_slides=True, confirmed=True)
    pkg = build_argument_source_package(confirmed_root, "S 200607", require_confirmed_visuals=True)
    assert pkg["stats"]["admitted_visual_count"] == 1
    assert pkg["stats"]["excluded_hidden_count"] == 1
    block = pkg["visual_blocks"][0]
    assert block["before_paragraph_id"] == "71"
    assert block["source_modality"] == "visual"
    assert pkg["visual_source_attestations"] == {block["locator"]: block["svg_sha256"]}
    proj = project_argument_source(pkg)
    assert len(proj.visual_blocks) == 1
    assert proj.visual_blocks[0].binding_kind == "sidecar_svg"


def test_argument_source_rejects_tampered_svg(tmp_path):
    root = _setup_sermon(tmp_path, with_slides=True, confirmed=True)
    svg_path = root / "transcript_review/S 200607/assets/a1/reconstruction.svg"
    svg_path.write_text("<svg>tampered</svg>", encoding="utf-8")
    with pytest.raises(ReviewError, match="雜湊驗證失敗"):
        build_argument_source_package(root, "S 200607")


def test_argument_source_http_endpoint(tmp_path, monkeypatch):
    root = _setup_sermon(tmp_path, with_slides=True, confirmed=True)
    monkeypatch.setattr(tr_router, "authenticated_actor", lambda req, body=b"": "user@example.com")
    monkeypatch.setattr(ReviewStore, "permissions", lambda self, actor: {"read"})
    app = FastAPI()
    app.include_router(tr_router.create_review_router(root))
    client = TestClient(app)
    res = client.get("/argument_source/S%20200607")
    assert res.status_code == 200
    body = res.json()
    assert body["schema_version"] == SCHEMA_VERSION
    assert body["stats"]["admitted_visual_count"] == 1
