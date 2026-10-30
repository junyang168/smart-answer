"""Proofreading workflow: legacy compatibility, ownership, recovery and publication."""

import copy
import hashlib
import hmac
import json
from pathlib import Path
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.sc_api.transcript_review import (
    ReviewStore,
    ReviewError,
    atomic,
    digest,
    file_hash,
    read,
)
from backend.api.sc_api.transcript_review_router import create_review_router
from backend.api.sc_api.script_delta import ScriptDelta, ScriptConflictError


def op(doc, **value):
    return {
        "version": doc["version"],
        "epoch": doc.get("epoch"),
        "operation_id": uuid.uuid4().hex,
        **value,
    }


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "data"
    source = tmp_path / "candidate"
    source.mkdir()
    (source / "reading-layout").mkdir()
    users = [{"id": i, "name": i} for i in ["a", "b", "admin", "reader"]]
    atomic(
        root / "config/config.json",
        {
            "users": users,
            "user_roles": [
                {"user": i, "role": i if i in ("admin", "reader") else "editor"}
                for i in ["a", "b", "admin", "reader"]
            ],
            "role_permissions": [
                {"role": role, "permission": p}
                for role, perms in [
                    ("editor", ["read_any_item", "write_owned_item", "assign_item"]),
                    ("admin", ["read_any_item", "write_any_item", "assign_any_item"]),
                    ("reader", ["read_any_item"]),
                ]
                for p in perms
            ],
        },
    )
    atomic(
        root / "config/sermon.json",
        [
            {
                "item": "講道 test",
                "title": "智慧",
                "status": "ready",
                "assigned_to": None,
            }
        ],
    )
    # Minimal real pipeline-shaped candidate, including an absolute timestamp >60m.
    image = source / "original.png"
    image.write_bytes(b"PNG original source")
    sha = file_hash(image)
    aid = "a" + sha
    content = {
        "asset_id": aid,
        "background": "#001b50",
        "has_diagram": False,
        "text_regions": [
            {
                "id": "r",
                "text": "心 καρδία",
                "bbox": [0, 0, 900, 200],
                "color": "#ffffff",
                "uncertain": False,
            }
        ],
        "shapes": [],
        "edges": [],
        "issues": [],
    }
    (source / "source.mp4").write_bytes(b"source video fixture")
    record = {
        "source_sha256": file_hash(source / "source.mp4"),
        "visibility": [],
        "paragraphs": [
            {
                "id": "w03-p1",
                "text": "耶穌基督。",
                "start_ms": 3600123,
                "end_ms": 3605000,
            },
            {
                "id": "w03-p2",
                "text": "保羅所說的智慧。",
                "start_ms": 3605000,
                "end_ms": 3610000,
            },
        ],
        "visual_assets": [
            {
                "asset_id": aid,
                "image_sha256": sha,
                "image_width": 640,
                "image_height": 480,
                "original_image": str(image),
                "visible_text": "心 καρδία",
                "content": content,
            }
        ],
        "slides": [{"id": "s1", "slide_sha256": sha, "time_ms": 3600123}],
    }
    from backend.pipeline.slide_change_pilot import object_sha

    atomic(source / "full_transcript.json", record)
    atomic(
        source / "reading-layout/layout.json",
        {
            "source_record_sha256": object_sha(record),
            "groups": [
                {
                    "id": "g1",
                    "member_ids": ["s1"],
                    "reason": "完整代表圖",
                    "representatives": [
                        {
                            "slide_id": "s1",
                            "before_paragraph_id": "w03-p1",
                            "needs_review": False,
                        }
                    ],
                }
            ],
        },
    )
    store = ReviewStore(root)
    store.register_candidate("講道 test", source, expected_script_sha256=None)
    return store, root, source


def adopted(setup, actor="a"):
    store, root, source = setup
    item = "講道 test"
    store.candidate_command(
        actor, item, op(store.candidate(actor, item), action="claim")
    )
    store.candidate_command(
        actor, item, op(store.candidate(actor, item), action="adopt")
    )
    return store.document(actor, item)


def confirmed(store, actor="a"):
    item = "講道 test"
    doc = store.document(actor, item)
    operations = [
        {
            "kind": "paragraph_review",
            "id": p["index"],
            "decision": "confirmed",
            "content_sha256": doc["paragraph_shas"][p["index"]],
        }
        for p in doc["script"]
    ]
    operations += [
        {
            "kind": "group_review",
            "id": g["id"],
            "decision": "confirmed",
            "content_sha256": doc["group_shas"][g["id"]],
        }
        for g in doc["context"]["groups"]
    ]
    store.mutate(actor, item, op(doc, operations=operations))
    return store.document(actor, item)


def test_claim_race_adopt_and_legacy_timestamp_compatibility(setup):
    store, root, _ = setup
    item = "講道 test"
    a = store.candidate("a", item)
    b = store.candidate("b", item)

    def claim(actor, doc):
        try:
            store.candidate_command(actor, item, op(doc, action="claim"))
            return True
        except ReviewError:
            return False

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(claim, "a", a), pool.submit(claim, "b", b)]
        results = [f.result() for f in futures]
    assert sum(results) == 1
    actor = "a" if results[0] else "b"
    store.candidate_command(
        actor, item, op(store.candidate(actor, item), action="adopt")
    )
    rows = read(root / "script_review" / f"{item}.json")
    assert (
        isinstance(rows, list)
        and rows[0]["index"] == "1"
        and "_" not in rows[0]["index"]
    )
    sd = ScriptDelta(str(root), item)
    assert sd.loadReviewScript() == ["耶穌基督。", "保羅所說的智慧。"]
    timeline = sd.get_final_script_data()
    assert timeline[0]["start_time"] == 3600 and timeline[1]["start_time"] == 3605
    assert (
        store.document(actor, item)["context"]["paragraph_bindings"]["1"]["start_ms"]
        == 3600123
    )
    with pytest.raises(ScriptConflictError):
        sd.save_script_rows(
            "script_review",
            rows,
            expected_current_sha256=file_hash(root / "script_review" / f"{item}.json"),
        )
    with pytest.raises(ScriptConflictError):
        sd.publish(actor)


def test_actual_legacy_saver_and_publisher_still_work_for_unmigrated_item(setup):
    _, root, _ = setup
    item = "legacy"
    rows = [{"index": "1", "end_index": "1", "type": "content", "text": "原文"}]
    atomic(
        root / "script" / f"{item}.json",
        {
            "entries": [
                {
                    "index": "1",
                    "start": "01:02:03,000",
                    "end": "01:02:08,000",
                    "text": "原文",
                }
            ]
        },
    )
    atomic(root / "script_review" / f"{item}.json", rows)
    atomic(root / "script_patched" / f"{item}.json", rows)
    sd = ScriptDelta(str(root), item)
    rows[0]["text"] = "修訂"
    sd.save_script_rows(
        "script_review",
        rows,
        expected_current_sha256=file_hash(root / "script_review" / f"{item}.json"),
    )
    sd.publish(
        "a", expected_review_sha256=file_hash(root / "script_review" / f"{item}.json")
    )
    metadata, published = sd.loadPublishedScript()
    assert published[0]["text"] == "修訂"


def test_owner_epoch_and_version_enforced_even_for_admin(setup):
    store, _, _ = setup
    item = "講道 test"
    doc = adopted(setup)
    req = op(doc, operations=[{"kind": "text", "id": "1", "text": "王教授。"}])
    for actor in ["b", "admin", "reader"]:
        with pytest.raises(ReviewError) as error:
            store.mutate(actor, item, req)
        assert error.value.status == 403
    missing = copy.deepcopy(req)
    missing.pop("epoch")
    with pytest.raises(ReviewError):
        store.mutate("a", item, missing)
    store.mutate("a", item, req)
    assert (
        store.mutate("a", item, req)["version"] == store.document("a", item)["version"]
    )  # same op receipt, not duplicate revision
    with pytest.raises(ReviewError) as error:
        store.mutate("a", item, op(doc, operations=req["operations"]))
    assert error.value.status == 412
    old = store.document("a", item)
    store.claim("admin", item, op(store.document("admin", item), action="takeover"))
    with pytest.raises(ReviewError):
        store.mutate("a", item, op(old, operations=req["operations"]))
    admin = store.document("admin", item)
    assert admin["can_edit"] and not admin["can_publish"]
    store.claim("admin", item, op(admin, action="unclaim"))
    store.claim("a", item, op(store.document("a", item), action="claim"))
    renewed = store.document("a", item)
    assert renewed["epoch"] != old["epoch"]
    with pytest.raises(ReviewError):
        store.mutate(
            "a",
            item,
            {**op(renewed, operations=req["operations"]), "epoch": old["epoch"]},
        )


def test_scoped_confirmation_invalidation_history_and_public_stability(setup):
    store, root, _ = setup
    item = "講道 test"
    adopted(setup)
    with pytest.raises(ReviewError):
        store.publish("a", item, op(store.document("a", item)))
    doc = confirmed(store)
    store.publish("a", item, op(doc))
    published_path = root / "script_published" / f"{item}.json"
    before = published_path.read_bytes()
    published = read(published_path)
    assert set(published) == {"metadata", "script"}
    assert (
        "review_public/%E8%AC%9B%E9%81%93%20test/assets/"
        in published["script"][0]["text"]
    )
    assert (
        "非口述" in published["script"][0]["text"]
        and "心 καρδία" in published["script"][0]["text"]
    )
    assert published["metadata"]["visual_source_attestations"] == []
    from backend.pipeline.source_projection import (
        project_script,
        validate_visual_source_attestations,
        VisualSourceAttestationError,
    )

    projection = project_script(published["script"])
    assert projection.spoken_rows[0]["text"].strip() == "耶穌基督。"
    assert projection.visual_blocks and projection.visual_blocks[0].raw_svg.startswith(
        "<svg"
    )
    with pytest.raises(VisualSourceAttestationError):
        validate_visual_source_attestations(projection, {})
    sd = ScriptDelta(str(root), item)
    assert "耶穌基督。" in sd.loadPublishedScript()[1][0]["text"]
    doc = store.document("a", item)
    revision = doc["context"]["revision"]
    snapshot = store.revision("a", item, revision)
    store.mutate(
        "a",
        item,
        op(doc, operations=[{"kind": "text", "id": "1", "text": "王教授提及耶穌。"}]),
    )
    changed = store.document("a", item)
    assert (
        "p:1" not in changed["context"]["reviews"]
        and "p:2" in changed["context"]["reviews"]
        and "g:g1" in changed["context"]["reviews"]
    )
    assert snapshot == store.revision("a", item, revision)
    store.claim("a", item, op(changed, action="unclaim"))
    assert (
        published_path.read_bytes() == before and store.document("b", item)["published"]
    )
    assert read(root / "config/sermon.json")[0]["status"] == "published"


def test_ocr_representative_and_placement_invalidations(setup):
    store, _, _ = setup
    item = "講道 test"
    adopted(setup)
    doc = confirmed(store)
    aid = next(iter(doc["context"]["visual_assets"]))
    store.mutate(
        "a",
        item,
        op(
            doc,
            operations=[
                {"kind": "ocr", "id": aid, "region_id": "r", "text": "心（καρδία）"}
            ],
        ),
    )
    doc = store.document("a", item)
    assert "g:g1" not in doc["context"]["reviews"]
    assert "p:1" in doc["context"]["reviews"]
    assert (
        doc["context"]["visual_assets"][aid]["svg_review_status"]
        == "unreviewed_model_candidate"
    )
    doc = confirmed(store)
    store.mutate(
        "a",
        item,
        op(doc, operations=[{"kind": "placement", "id": "g1", "paragraph_id": "2"}]),
    )
    assert "g:g1" not in store.document("a", item)["context"]["reviews"]
    with pytest.raises(ReviewError):
        store.mutate(
            "a",
            item,
            op(
                store.document("a", item),
                operations=[
                    {"kind": "representative", "id": "g1", "slide_id": "not-in-group"}
                ],
            ),
        )


def test_recovery_rolls_forward_and_unknown_third_file_fails_closed(setup, monkeypatch):
    store, root, _ = setup
    item = "講道 test"
    doc = adopted(setup)
    original = store.install

    def crash_after_one(operation):
        entry = operation["files"][0]
        atomic(root / entry["path"], entry["value"])
        raise OSError("simulated power loss")

    monkeypatch.setattr(store, "install", crash_after_one)
    req = op(doc, operations=[{"kind": "text", "id": "1", "text": "已提交的更正"}])
    with pytest.raises(OSError):
        store.mutate("a", item, req)
    monkeypatch.setattr(store, "install", original)
    assert store.document("a", item)["script"][0]["text"] == "已提交的更正"
    assert store.mutate("a", item, req)["revision"] == 2
    doc = store.document("a", item)
    monkeypatch.setattr(store, "install", crash_after_one)
    with pytest.raises(OSError):
        store.mutate(
            "a",
            item,
            op(doc, operations=[{"kind": "text", "id": "2", "text": "待恢復"}]),
        )
    atomic(root / "script_review" / f"{item}.json", [{"text": "外部未知版本"}])
    monkeypatch.setattr(store, "install", original)
    with pytest.raises(ReviewError):
        store.document("a", item)
    assert read(root / "script_review" / f"{item}.json") == [{"text": "外部未知版本"}]


def test_manual_edits_and_published_baselines_never_overwritten(setup):
    store, root, _ = setup
    item = "講道 test"
    atomic(
        root / "script_review" / f"{item}.json", [{"index": "1", "text": "人工修改"}]
    )
    store.candidate_command("a", item, op(store.candidate("a", item), action="claim"))
    with pytest.raises(ReviewError):
        store.candidate_command(
            "a", item, op(store.candidate("a", item), action="adopt")
        )
    assert read(root / "script_review" / f"{item}.json")[0]["text"] == "人工修改"


def signed(method, path, body=b"", actor="a", secret="test-secret"):
    timestamp = str(int(time.time()))
    message = "\n".join(
        (method, path, actor, timestamp, hashlib.sha256(body).hexdigest())
    )
    return {
        "x-review-actor": actor,
        "x-review-time": timestamp,
        "x-review-signature": hmac.new(
            secret.encode(), message.encode(), hashlib.sha256
        ).hexdigest(),
    }


def test_authenticated_http_etag_tampering_and_public_snapshot(setup, monkeypatch):
    store, root, _ = setup
    adopted(setup)
    app = FastAPI()
    app.include_router(create_review_router(root), prefix="/sc_api")
    client = TestClient(app)
    from urllib.parse import quote

    path = "/sc_api/review_document/" + quote("講道 test", safe="")
    monkeypatch.setenv("TRANSCRIPT_REVIEW_ACTION_SECRET", "test-secret")
    assert client.get(path).status_code == 401
    response = client.get(path, headers=signed("GET", path))
    assert response.status_code == 200
    assert response.headers["etag"].strip('"') == digest(response.json())
    doc = store.document("a", "講道 test")
    body = json.dumps(
        op(doc, operations=[{"kind": "text", "id": "1", "text": "合法"}])
    ).encode()
    headers = {
        **signed("PATCH", path, body),
        "content-type": "application/json",
        "if-match": '"' + doc["version"] + '"',
    }
    assert client.patch(path, content=body + b" ", headers=headers).status_code == 401
    headers["x-review-actor"] = "admin"
    assert client.patch(path, content=body, headers=headers).status_code == 401
    headers.update(signed("PATCH", path, body))
    assert client.patch(path, content=body, headers=headers).status_code == 200
    public = "/sc_api/review_public/" + quote("講道 test", safe="")
    assert client.get(public).status_code == 404
    store.publish("a", "講道 test", op(confirmed(store)))
    view = client.get(public)
    assert view.status_code == 200 and view.json()["script"][0]["text"] == "合法"
    aid = next(iter(doc["context"]["visual_assets"]))
    assert client.get(public + "/assets/" + aid).content == b"PNG original source"
    monkeypatch.delenv("TRANSCRIPT_REVIEW_ACTION_SECRET")
    assert client.get(path).status_code == 503


def test_public_reading_uses_existing_soft_deletion_without_changing_draft(setup):
    store, root, _ = setup
    item = "講道 test"
    doc = adopted(setup)
    text = "保留~~刪除的\n內容~~結尾"
    store.mutate("a", item, op(doc, operations=[{"kind": "text", "id": "1", "text": text}]))
    store.publish("a", item, op(confirmed(store)))
    app = FastAPI()
    app.include_router(create_review_router(root), prefix="/sc_api")
    from urllib.parse import quote

    response = TestClient(app).get("/sc_api/review_public/" + quote(item, safe=""))
    assert response.status_code == 200
    assert response.json()["script"][0]["text"] == "保留\n結尾"
    assert store.document("a", item)["script"][0]["text"] == text
    _, snapshot = store.published_snapshot(item)
    assert snapshot["script"][0]["text"] == text


def test_beta_restricts_every_route_including_probe_public_assets_and_writes(setup, monkeypatch):
    store, root, _ = setup
    adopted(setup)
    monkeypatch.setenv("TRANSCRIPT_REVIEW_ACTION_SECRET", "test-secret")
    app = FastAPI()
    app.include_router(create_review_router(root, beta_owner="a"), prefix="/sc_api")
    client = TestClient(app)
    from urllib.parse import quote

    item = quote("講道 test", safe="")
    for method, suffix in [
        ("GET", f"review_mode/{item}"),
        ("GET", f"review_candidate/{item}"),
        ("POST", f"review_candidate/{item}"),
        ("GET", f"review_document/{item}"),
        ("PATCH", f"review_document/{item}"),
        ("POST", f"review_document/{item}/publish"),
        ("GET", f"review_document/{item}/history"),
        ("GET", f"review_document/{item}/media"),
        ("GET", f"review_document/{item}/assets/a1"),
        ("GET", f"review_public/{item}"),
        ("GET", f"review_public/{item}/assets/a1"),
    ]:
        path = "/sc_api/" + suffix
        assert client.request(method, path).status_code == 401
        assert client.request(method, path, headers=signed(method, path, actor="b")).status_code == 403
    path = "/sc_api/review_document/" + item
    authorized = client.get(path, headers=signed("GET", path))
    assert authorized.status_code == 200
    assert authorized.headers["cache-control"] == "private, no-store"
    doc = store.document("a", "講道 test")
    body = json.dumps(op(doc, operations=[{"kind": "text", "id": "1", "text": "beta修改"}])).encode()
    response = client.patch(path, content=body, headers={**signed("PATCH", path, body), "content-type": "application/json", "if-match": doc["version"]})
    assert response.status_code == 200
    assert store.document("a", "講道 test")["script"][0]["text"] == "beta修改"


def test_catalog_recovery_preserves_another_sermon_and_legacy_cache(setup, monkeypatch):
    store, root, _ = setup
    item = "講道 test"
    adopted(setup)
    metadata = read(root / "config/sermon.json")
    metadata.append({"item": "other", "title": "原標題", "status": "ready"})
    atomic(root / "config/sermon.json", metadata)
    from backend.api.sc_api.sermon_meta import SermonMetaManager

    legacy = SermonMetaManager(str(root), lambda _: {})
    original = store.install

    def crash_before_metadata(operation):
        entry = operation["files"][0]
        atomic(root / entry["path"], entry["value"])
        raise OSError("power loss")

    monkeypatch.setattr(store, "install", crash_before_metadata)
    with pytest.raises(OSError):
        store.claim("a", item, op(store.document("a", item), action="unclaim"))
    metadata = read(root / "config/sermon.json")
    metadata[1]["title"] = "另一篇的新標題"
    atomic(root / "config/sermon.json", metadata)
    monkeypatch.setattr(store, "install", original)
    store.document("a", item)
    assert read(root / "config/sermon.json")[1]["title"] == "另一篇的新標題"
    legacy.sermons[0].assigned_to = "b"  # old long-lived manager's stale claim
    legacy.save_sermon_metadata()
    assert read(root / "config/sermon.json")[0]["assigned_to"] is None
    assert not list((store.folder(item) / "operations").glob("*.prepared.json"))


def test_hidden_orphan_requires_explicit_disposition_and_empty_ocr_is_recoverable(
    setup,
):
    store, root, _ = setup
    item = "講道 test"
    doc = adopted(setup)
    aid = next(iter(doc["context"]["visual_assets"]))
    store.mutate(
        "a",
        item,
        op(
            doc,
            operations=[
                {"kind": "ocr", "id": aid, "region_id": "r", "text": ""},
                {"kind": "hidden", "id": "g1", "hidden": True, "reason": ""},
            ],
        ),
    )
    doc = store.document("a", item)
    with pytest.raises(ReviewError):
        store.mutate(
            "a",
            item,
            op(
                doc,
                operations=[
                    {
                        "kind": "group_review",
                        "id": "g1",
                        "decision": "confirmed",
                        "content_sha256": doc["group_shas"]["g1"],
                    }
                ],
            ),
        )
    store.mutate(
        "a",
        item,
        op(
            doc,
            operations=[
                {
                    "kind": "hidden",
                    "id": "g1",
                    "hidden": False,
                    "reason": "移除錯辨文字，原圖仍可讀",
                }
            ],
        ),
    )
    store.publish("a", item, op(confirmed(store)))
    assert (
        '<figure data-transcript-slide="v1"'
        in read(root / "script_published" / f"{item}.json")["script"][0]["text"]
    )


def test_history_and_published_snapshot_are_sha_bound_and_media_survives_source_cleanup(
    setup,
):
    store, root, source = setup
    item = "講道 test"
    doc = adopted(setup)
    media = store.folder(item) / "source.mp4"
    assert not media.is_symlink()
    (source / "source.mp4").unlink()
    assert media.read_bytes() == b"source video fixture"
    doc = confirmed(store)
    store.publish("a", item, op(doc))
    published = read(root / "script_published" / f"{item}.json")
    revision = published["metadata"]["review_revision"]
    path = store.folder(item) / "revisions" / f"{revision:08}.json"
    assert file_hash(path) == published["metadata"]["review_snapshot_sha256"]
    snapshot = read(path)
    snapshot["actor"] = "spoofed"
    atomic(path, snapshot)
    with pytest.raises(ReviewError):
        store.history("a", item)
    with pytest.raises(ReviewError):
        store.asset_path(item, next(iter(doc["context"]["visual_assets"])), public=True)


def test_beta_activation_preserves_local_edits_history_and_legacy_manuscript(setup, tmp_path):
    import importlib.util
    script = Path(__file__).resolve().parents[2] / "scripts/activate-transcript-beta.py"
    spec = importlib.util.spec_from_file_location("activate_beta", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    store, source_root, _ = setup
    item = "講道 test"
    doc = adopted(setup)
    store.mutate("a", item, op(doc, operations=[{"kind": "text", "id": "1", "text": "已校對~~刪除~~正文"}]))
    original = confirmed(store)
    history_files = {p.name: file_hash(p) for p in (store.folder(item) / "revisions").glob("*.json")}
    production = tmp_path / "production"
    atomic(production / "config/config.json", read(source_root / "config/config.json"))
    atomic(production / "script_review" / (item + ".json"), [{"index": "old", "text": "原人工稿"}])
    legacy_sha = file_hash(production / "script_review" / (item + ".json"))
    module.activate(source_root, production, item, "a")
    beta_root = production / "transcript-review-beta"
    beta = ReviewStore(beta_root, public_api_base="/api/sc_api/beta/review_public")
    copied = beta.document("a", item)
    assert copied["script"] == original["script"]
    assert copied["context"]["reviews"] == original["context"]["reviews"]
    for name, sha in history_files.items():
        assert file_hash(beta.folder(item) / "revisions" / name) == sha
    assert beta.history("a", item)
    assert read(beta_root / "activation.json")["content_review_decisions_added"] == 0
    assert copied["can_edit"]
    assert file_hash(production / "script_review" / (item + ".json")) == legacy_sha
    beta.publish("a", item, op(copied))
    published = read(beta_root / "script_published" / (item + ".json"))
    assert "/api/sc_api/beta/review_public/" in published["script"][0]["text"]
    assert not (production / "script_published" / (item + ".json")).exists()
    assert store.document("a", item)["version"] == original["version"]
    with pytest.raises(ValueError, match="refusing to overwrite"):
        module.activate(source_root, production, item, "a")
