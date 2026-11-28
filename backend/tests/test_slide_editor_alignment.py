import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.pipeline.slide_change_pilot import file_sha, save_json
import backend.pipeline.slide_editor_alignment as align


def _write_fake_evidence(tmp_path: Path) -> Path:
    ev = tmp_path / "evidence"
    asset_dir = ev / "assets" / "a1"
    asset_dir.mkdir(parents=True)
    cv2.imwrite(str(asset_dir / "original.png"), np.full((80, 120, 3), 30, np.uint8))
    (asset_dir / "reconstruction.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    collection = {
        "schema_version": "slide-visual-evidence-collection.v1",
        "source_video_sha256": "0" * 64,
        "assets": [
            {
                "asset_id": "a1",
                "image_sha256": "1" * 64,
                "original_image": "assets/a1/original.png",
                "visible_text": "羅11:11\n我且說，他們失腳是要他們跌倒麼？",
                "content": {
                    "asset_id": "a1",
                    "background": "#001B54",
                    "has_diagram": False,
                    "text_regions": [
                        {"id": "tr_0", "text": "羅11:11", "bbox": [10, 10, 200, 80], "color": "#FFFFFF", "uncertain": False}
                    ],
                    "shapes": [],
                    "edges": [],
                    "issues": [],
                },
                "svg": {"path": "assets/a1/reconstruction.svg", "background_mode": "white"},
            }
        ],
        "occurrences": [
            {"event_id": "e-00", "time_ms": 235000, "capture_ms": 235000, "kind": "new_slide", "asset_id": "a1"}
        ],
    }
    save_json(ev / "slide_evidence.json", collection)
    return ev


def test_load_transcript_paragraphs_supports_script_published(tmp_path):
    p = tmp_path / "sermon.json"
    save_json(p, {
        "metadata": {},
        "script": [
            {"type": "subtitle", "text": "## 標題"},
            {"index": 10, "start_time": 140, "end_time": 232, "text": "神集體的揀選..."},
            {"index": 11, "start_time": 232, "end_time": 310, "text": "因他們的過失..."},
        ],
    })
    rows = align.load_transcript_paragraphs(p)
    assert [r["paragraph_id"] for r in rows] == ["10", "11"]
    assert rows[0]["start_ms"] == 140000 and rows[1]["end_ms"] == 310000
    cands = align.candidate_paragraphs_for_time(rows, 235000)
    assert len(cands) == 2


def test_fidelity_audit_and_pure_scripture_exclusion(tmp_path, monkeypatch):
    ev = _write_fake_evidence(tmp_path)
    ev_doc = json.loads((ev / "slide_evidence.json").read_text())
    ev_doc["assets"][0]["image_sha256"] = file_sha(ev / "assets" / "a1" / "original.png")
    save_json(ev / "slide_evidence.json", ev_doc)
    transcript = tmp_path / "sermon.json"
    save_json(transcript, {
        "script": [
            {"index": 10, "start_time": 230, "end_time": 290, "text": "羅11:11 我且說..."}
        ]
    })

    class FakeReviewer:
        model, input_rate, output_rate = "fake-test", 0.75, 3.75

        def __init__(self, provider, prompt, schema):
            self.provider, self.prompt, self.config = provider, prompt, {"response_json_schema": schema}

        def contents(self, ids, paths):
            return ["img"]

        def count(self, contents):
            return 10

        def generate(self, contents):
            if "asset_id" in self.prompt:
                return {"results": [{"asset_id": "a1", "passed": True, "severity": "none", "issues": [], "summary": "一致"}]}
            return {"results": [{
                "event_id": "e-00",
                "category": "pure_scripture_only",
                "important": True,  # deliberately contradictory: code must enforce pure_scripture_only -> False
                "aligned_paragraph_id": "10",
                "helps_reader_understand": "",
                "cross_channel_notes": "",
            }]}

        def text(self, raw):
            return json.dumps(raw, ensure_ascii=False)

        def usage(self, raw):
            return {"estimated_usd_upper": 0.005}

    monkeypatch.setattr(align, "VisionReviewer", FakeReviewer)
    audit = align.verify_visual_fidelity(ev)
    assert audit["passed_count"] == 1 and audit["auto_corrections"] == 0
    bindings = align.align_and_filter_important_slides(ev, transcript, tmp_path / "aligned")
    assert bindings["important_count"] == 0
    assert bindings["pure_scripture_excluded_count"] == 1
    assert bindings["bindings"][0]["important"] is False
    assert bindings["bindings"][0]["source_role"] == "scripture_reference_only"
    assert bindings["fidelity_audit_sha256"] == file_sha(ev / "fidelity_audit.json")


def test_alignment_supports_agy_subscription_with_zero_api_cost(tmp_path, monkeypatch):
    ev = _write_fake_evidence(tmp_path)
    ev_doc = json.loads((ev / "slide_evidence.json").read_text())
    ev_doc["assets"][0]["image_sha256"] = file_sha(ev / "assets" / "a1" / "original.png")
    save_json(ev / "slide_evidence.json", ev_doc)
    transcript = tmp_path / "sermon.json"
    save_json(transcript, {
        "script": [{"index": 10, "start_time": 230, "end_time": 290, "text": "羅11:11 我且說..."}]
    })

    class FakeSubClient:
        model = "gemini-4-neon-high"

        def generate_json(self, system_prompt, user_prompt, json_schema):
            return {"results": [{
                "event_id": "e-00",
                "category": "exegetical_evidence_text",
                "important": True,
                "aligned_paragraph_id": "10",
                "helps_reader_understand": "說明羅11:11原文結構。",
                "cross_channel_notes": "",
            }]}

    monkeypatch.setattr(align, "AgySubscriptionClient", FakeSubClient)
    bindings = align.align_and_filter_important_slides(
        ev, transcript, tmp_path / "aligned_sub", provider="agy_subscription"
    )
    assert bindings["provider"] == "agy_subscription"
    assert bindings["estimated_usd_upper"] == 0.0
    assert bindings["important_count"] == 1
    assert bindings["bindings"][0]["aligned_paragraph_id"] == "10"


@pytest.mark.parametrize("repair_id", ["a1", "wrong-asset"])
def test_fidelity_auto_repair_updates_text_svg_and_annotates_review(tmp_path, monkeypatch, repair_id):
    ev = _write_fake_evidence(tmp_path)
    ev_doc = json.loads((ev / "slide_evidence.json").read_text())
    ev_doc["assets"][0]["image_sha256"] = file_sha(ev / "assets" / "a1" / "original.png")
    save_json(ev / "slide_evidence.json", ev_doc)
    collection = json.loads((ev / "slide_evidence.json").read_text())
    collection["assets"][0]["image_width"] = 120
    collection["assets"][0]["image_height"] = 80
    collection["assets"][0]["image_sha256"] = "1" * 64
    collection["assets"][0]["content"]["text_regions"][0]["text"] = "揀選以色列"
    collection["assets"][0]["visible_text"] = "揀選以色列"
    save_json(ev / "slide_evidence.json", collection)

    class FakeReviewer:
        model, input_rate, output_rate = "fake-test", 0.75, 3.75

        def __init__(self, provider, prompt, schema):
            self.schema = schema
            self.provider, self.prompt, self.config = provider, prompt, {"response_json_schema": schema}

        def contents(self, ids, paths):
            return ["img"]

        def count(self, contents):
            return 10

        def generate(self, contents):
            if "Fidelity issues to fix" in str(contents[-1]):
                return {"results": [{"asset_id": repair_id, "text_regions": [{"id": "tr_0", "text": "揀選以色"}]}]}
            return {"results": [{
                "asset_id": "a1",
                "passed": False,
                "severity": "major",
                "issues": ["第一行末多出「列」字"],
                "summary": "多出「列」字。",
            }]}

        def text(self, raw):
            return json.dumps(self.schema) + "\n" + json.dumps(raw, ensure_ascii=False)

        def usage(self, raw):
            return {"estimated_usd_upper": 0.005}

    monkeypatch.setattr(align, "VisionReviewer", FakeReviewer)
    if repair_id != "a1":
        with pytest.raises(ValueError, match="asset ID drifted"):
            align.verify_visual_fidelity(ev, auto_repair=True)
        assert json.loads((ev / "slide_evidence.json").read_text()) == collection
        return
    audit = align.verify_visual_fidelity(ev, auto_repair=True)
    assert audit["repaired_count"] == 1
    updated = json.loads((ev / "slide_evidence.json").read_text())["assets"][0]
    assert updated["visible_text"] == "揀選以色"
    assert updated["fidelity_review"]["repaired"] is True
    assert updated["fidelity_review"]["before_text"] == "揀選以色列"
    assert "揀選以色" in (ev / updated["svg"]["path"]).read_text()


def test_alignment_supports_codex_subscription_gpt_sol(tmp_path, monkeypatch):
    ev = _write_fake_evidence(tmp_path)
    ev_doc = json.loads((ev / "slide_evidence.json").read_text())
    ev_doc["assets"][0]["image_sha256"] = file_sha(ev / "assets" / "a1" / "original.png")
    save_json(ev / "slide_evidence.json", ev_doc)
    transcript = tmp_path / "sermon.json"
    save_json(transcript, {
        "script": [{"index": 10, "start_time": 230, "end_time": 290, "text": "羅11:11 我且說..."}]
    })

    class FakeCodexClient:
        def __init__(self, *, model, **kwargs):
            self.model = model

        def generate_json(self, system_prompt, user_prompt, json_schema):
            return {"results": [{
                "event_id": "e-00",
                "category": "exegetical_evidence_text",
                "important": True,
                "aligned_paragraph_id": "10",
                "helps_reader_understand": "說明羅11:11原文結構。",
                "cross_channel_notes": "",
            }]}

    monkeypatch.setattr(align, "CodexSubscriptionClient", FakeCodexClient)
    bindings = align.align_and_filter_important_slides(
        ev, transcript, tmp_path / "aligned_codex", provider="codex_subscription", model="gpt-6.2-sol"
    )
    assert bindings["provider"] == "codex_subscription"
    assert bindings["model"] == "gpt-6.2-sol"
    assert bindings["estimated_usd_upper"] == 0.0
    assert bindings["important_count"] == 1


def test_fidelity_supports_codex_subscription_with_image_paths(tmp_path, monkeypatch):
    ev = _write_fake_evidence(tmp_path)
    ev_doc = json.loads((ev / "slide_evidence.json").read_text())
    ev_doc["assets"][0]["image_sha256"] = file_sha(ev / "assets" / "a1" / "original.png")
    save_json(ev / "slide_evidence.json", ev_doc)
    seen_images = []

    class FakeCodexClient:
        def __init__(self, *, model, **kwargs):
            self.model = model

        def generate_json(self, system_prompt, user_prompt, json_schema, image_paths=None):
            seen_images.append(list(image_paths or []))
            return {"results": [{"asset_id": "a1", "passed": True, "severity": "none", "issues": [], "summary": "一致"}]}

    monkeypatch.setattr(align, "CodexSubscriptionClient", FakeCodexClient)
    audit = align.verify_visual_fidelity(ev, provider="codex_subscription", model="gpt-6.2-sol")
    assert audit["provider"] == "codex_subscription"
    assert audit["model"] == "gpt-6.2-sol"
    assert audit["estimated_usd_upper"] == 0.0
    assert audit["passed_count"] == 1
    assert len(seen_images) == 1 and len(seen_images[0]) == 1


def test_review_router_serves_white_background_svg_and_original_png(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import backend.api.sc_api.transcript_review_router as tr_router
    from backend.api.sc_api.transcript_review import ReviewStore

    data_root = tmp_path / "data"
    (data_root / "config").mkdir(parents=True)
    save_json(data_root / "config" / "sermon.json", [
        {"item": "S 200607", "title": "羅馬書", "assigned_to": "user@example.com", "assignment_epoch": 1}
    ])
    save_json(data_root / "script_review" / "S 200607.json", [
        {"index": "71", "text": "神集體的揀選", "type": "content"}
    ])
    review_dir = data_root / "transcript_review" / "S 200607"
    review_dir.mkdir(parents=True)
    save_json(review_dir / "context.json", {
        "schema_version": "transcript-review-context.v1",
        "script_sha256": file_sha(data_root / "script_review" / "S 200607.json"),
        "revision": 0,
        "paragraph_bindings": {"71": {"start_ms": 140000, "end_ms": 232000, "source_paragraph_id": "71"}},
        "visual_assets": {},
        "occurrences": {},
        "groups": [],
        "reviews": {},
    })

    ev = _write_fake_evidence(tmp_path)
    ev_doc = json.loads((ev / "slide_evidence.json").read_text())
    ev_doc["assets"][0]["image_sha256"] = file_sha(ev / "assets" / "a1" / "original.png")
    save_json(ev / "slide_evidence.json", ev_doc)
    (ev / "assets" / "a1" / "reconstruction.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="120" height="80" fill="#FFFFFF"/></svg>',
        encoding="utf-8",
    )
    bindings_path = tmp_path / "bindings.json"
    save_json(bindings_path, {
        "bindings": [{
            "event_id": "e-00",
            "asset_id": "a1",
            "time_ms": 147840,
            "important": True,
            "aligned_paragraph_id": "71",
            "helps_reader_understand": "說明集體揀選。",
        }]
    })
    align.import_bindings_to_review(ev, bindings_path, review_dir)

    monkeypatch.setattr(tr_router, "authenticated_actor", lambda req, body=b"": "user@example.com")
    monkeypatch.setattr(ReviewStore, "permissions", lambda self, actor: {"read", "write_owned_item"})
    app = FastAPI()
    app.include_router(tr_router.create_review_router(data_root))
    client = TestClient(app)

    doc_res = client.get("/review_document/S%20200607")
    assert doc_res.status_code == 200
    doc = doc_res.json()
    assert doc["context"]["visual_assets"]["a1"]["svg_path"].endswith("reconstruction.svg")

    png_res = client.get("/review_document/S%20200607/assets/a1")
    assert png_res.status_code == 200
    assert png_res.headers["content-type"].startswith("image/png")

    svg_res = client.get("/review_document/S%20200607/assets/a1/svg")
    assert svg_res.status_code == 200
    assert svg_res.headers["content-type"].startswith("image/svg+xml")
    assert 'fill="#FFFFFF"' in svg_res.text


def test_load_transcript_paragraphs_reads_formal_editor_timings_and_narrows_candidates(tmp_path):
    import pytest

    data_root = tmp_path / "data"
    (data_root / "script_review").mkdir(parents=True)
    review_dir = data_root / "transcript_review" / "S 200607"
    review_dir.mkdir(parents=True)

    script_path = data_root / "script_review" / "S 200607.json"
    save_json(script_path, [
        {"index": "71", "text": "開場段落", "type": "content"},
        {"index": "180", "text": "中段段落", "type": "content"},
        {"index": "520", "text": "後半段經文講解", "type": "content"},
    ])
    ctx_path = review_dir / "context.json"
    save_json(ctx_path, {
        "schema_version": "transcript-review-context.v1",
        "paragraph_bindings": {
            "71": {"source_paragraph_id": "71", "start_ms": 140000, "end_ms": 232000},
            "180": {"source_paragraph_id": "180", "start_ms": 453000, "end_ms": 582000},
            "520": {"source_paragraph_id": "520", "start_ms": 2400000, "end_ms": 2580000},
        },
    })

    # Passing script_review/<item>.json auto-discovers transcript_review/<item>/context.json
    rows_from_script = align.load_transcript_paragraphs(script_path)
    assert [(r["paragraph_id"], r["start_ms"], r["end_ms"]) for r in rows_from_script] == [
        ("71", 140000, 232000),
        ("180", 453000, 582000),
        ("520", 2400000, 2580000),
    ]
    # Passing context.json directly also resolves script_review/<item>.json
    rows_from_ctx = align.load_transcript_paragraphs(ctx_path)
    assert rows_from_ctx == rows_from_script

    # A slide in the second half of the video must narrow to paragraph 520, never the opening
    cands = align.candidate_paragraphs_for_time(rows_from_script, 2450000)
    assert [c["paragraph_id"] for c in cands] == ["520"]

    # Untimed transcripts must fail closed instead of falling back to the first 6 paragraphs
    untimed_path = tmp_path / "untimed.json"
    save_json(untimed_path, [{"index": "1", "text": "沒有時間的段落", "type": "content"}])
    untimed_rows = align.load_transcript_paragraphs(untimed_path)
    with pytest.raises(ValueError, match="no start_ms/end_ms timings"):
        align.candidate_paragraphs_for_time(untimed_rows, 2450000)


@pytest.mark.parametrize("source_colour", [False, True])
def test_import_bindings_uses_editor_versioning_preserves_times_dimensions_and_publishes_white_svg(tmp_path, source_colour):
    from backend.api.sc_api.transcript_review import ReviewStore, digest

    data_root = tmp_path / "data"
    (data_root / "config").mkdir(parents=True)
    (data_root / "script_review").mkdir(parents=True)
    (data_root / "script_published").mkdir(parents=True)
    save_json(data_root / "config" / "config.json", {
        "users": [{"id": "owner@example.com", "name": "Owner"}],
        "user_roles": [{"user": "owner@example.com", "role": "editor"}],
        "role_permissions": [
            {"role": "editor", "permission": p}
            for p in ["read_any_item", "write_owned_item", "assign_item"]
        ],
    })
    save_json(data_root / "config" / "sermon.json", [{
        "item": "S 200607",
        "title": "羅馬書",
        "assigned_to": "owner@example.com",
        "assignment_epoch": 1,
    }])
    save_json(data_root / "script_review" / "S 200607.json", [
        {"index": "71", "text": "神集體的揀選", "type": "content"},
    ])
    review_dir = data_root / "transcript_review" / "S 200607"
    (review_dir / "revisions").mkdir(parents=True)
    (review_dir / "assets" / "existing_a0").mkdir(parents=True)
    (review_dir / "assets" / "existing_a0" / "original.png").write_bytes(b"old-png")
    save_json(review_dir / "revisions" / "00000001.json", {"revision": 1, "kind": "initial"})

    initial_ctx = {
        "schema_version": "transcript-review-context.v1",
        "script_sha256": file_sha(data_root / "script_review" / "S 200607.json"),
        "revision": 1,
        "source_video_sha256": "v" * 64,
        "editorial_metadata": {"title": "羅馬書"},
        "paragraph_bindings": {"71": {"start_ms": 140000, "end_ms": 232000, "source_paragraph_id": "71"}},
        "timing_quality": {"status": "qualified"},
        "visual_assets": {
            "existing_a0": {
                "visible_text": "舊圖",
                "image": "transcript_review/S 200607/assets/existing_a0/original.png",
                "image_sha256": file_sha(review_dir / "assets" / "existing_a0" / "original.png"),
                "width": 100,
                "height": 80,
                "content": {"asset_id": "existing_a0", "background": "#001B54", "has_diagram": False, "text_regions": [], "shapes": [], "edges": [], "issues": []},
            }
        },
        "occurrences": {"e-old": {"asset_id": "existing_a0", "time_ms": 10000, "capture_ms": 12000}},
        "groups": [{"id": "g-e-old", "member_ids": ["e-old"], "slide_id": "e-old", "before_paragraph_id": "71", "hidden": True, "reason": "", "needs_review": False}],
        "reviews": {},
    }
    save_json(review_dir / "context.json", initial_ctx)

    ev = _write_fake_evidence(tmp_path)
    ev_doc = json.loads((ev / "slide_evidence.json").read_text())
    ev_doc["assets"][0]["image_sha256"] = file_sha(ev / "assets" / "a1" / "original.png")
    if source_colour:
        from backend.pipeline.slide_source_ink import source_ink_svg
        asset=ev_doc["assets"][0]
        image_path=ev/asset["original_image"]
        frame=np.full((80,120,3),(90,25,8),np.uint8)
        cv2.putText(frame,"ABC",(8,45),cv2.FONT_HERSHEY_SIMPLEX,.8,(0,235,235),2)
        cv2.imwrite(str(image_path),frame)
        asset["image_sha256"]=file_sha(image_path)
        asset["content"]["text_regions"][0].update(text="ABC",bbox=[0,0,1000,1000])
        source_svg=source_ink_svg(frame,asset["content"],asset["image_sha256"])
        assert source_svg
        (ev/asset["svg"]["path"]).write_text(source_svg)
        asset["display_note"]="source-glyphs.v1"
    # Distinct appearance time (235000) vs capture time (236500)
    ev_doc["occurrences"][0]["time_ms"] = 235000
    ev_doc["occurrences"][0]["capture_ms"] = 236500
    save_json(ev / "slide_evidence.json", ev_doc)

    bindings_path = tmp_path / "bindings.json"
    save_json(bindings_path, {
        "bindings": [{
            "event_id": "e-00",
            "asset_id": "a1",
            "time_ms": 235000,
            "important": True,
            "aligned_paragraph_id": "71",
            "helps_reader_understand": "說明集體揀選。",
        }]
    })

    store = ReviewStore(data_root)
    original = digest(store.document_value(*store.load("S 200607")))
    summary = align.import_bindings_to_review(ev, bindings_path, review_dir, expected_version=original)
    assert align.import_bindings_to_review(ev, bindings_path, review_dir, expected_version=original) == summary
    from backend.api.sc_api.transcript_review import ReviewError
    import pytest
    with pytest.raises(ReviewError):
        align.import_bindings_to_review(ev, bindings_path, review_dir,
                                        expected_version=original, operation_id="another-import")
    assert summary["revision"] == 2
    assert (review_dir / "revisions" / "00000001.json").exists()
    assert (review_dir / "revisions" / "00000002.json").exists()

    store = ReviewStore(data_root)
    rows, ctx = store.load("S 200607")
    assert ctx["revision"] == 2
    # Preserves existing attachments + adds new attachment
    assert "existing_a0" in ctx["visual_assets"]
    assert "a1" in ctx["visual_assets"]
    # Preserves original.png, image dimensions (120x80), and both timestamps
    assert (review_dir / "assets" / "a1" / "original.png").exists()
    assert ctx["visual_assets"]["a1"]["width"] == 120
    assert ctx["visual_assets"]["a1"]["height"] == 80
    assert ctx["occurrences"]["e-00"]["time_ms"] == 235000
    assert ctx["occurrences"]["e-00"]["capture_ms"] == 236500

    # Confirm paragraph and both groups, then publish via ReviewStore and verify white-background SVG output
    ctx["reviews"] = {
        "p:71": {"decision": "confirmed", "sha256": digest({"text": rows[0]["text"], **ctx["paragraph_bindings"]["71"]})},
        **{
            f"g:{g['id']}": {"decision": "confirmed", "sha256": digest(store.group_target(ctx, g))}
            for g in ctx["groups"]
        },
    }
    save_json(review_dir / "context.json", ctx)

    pub_receipt = store.publish("owner@example.com", "S 200607", {
        "version": digest(store.document_value(rows, ctx)),
        "epoch": 1,
        "operation_id": "pub-test-1",
    })
    assert pub_receipt["revision"] == 3
    published = json.loads((data_root / "script_published" / "S 200607.json").read_text(encoding="utf-8"))
    published_text = published["script"][0]["text"]
    if source_colour:
        assert source_svg in published_text
    assert "<svg " in published_text
    assert 'fill="#ffffff"' in published_text.lower()
