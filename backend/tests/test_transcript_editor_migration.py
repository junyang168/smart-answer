from backend.api.sc_api.legacy_editor_snapshot import legacy_history
from backend.api.sc_api.transcript_review import ReviewStore, atomic, read
from backend.pipeline.transcript_editor_migration import archive_clip, convert_legacy, CLIP_ID, FULL_ID


def test_legacy_migration_retains_duplicate_text_and_original_timeline(tmp_path):
    item = "sermon"
    rows = [{"index": 1, "text": "first"}, {"index": 1, "text": "second"}]
    timeline = {"entries": [{"index": 1, "start": "00:00:00,000", "end": "00:00:03,000"}]}
    atomic(tmp_path / "config/sermon.json", [{"item": item}])
    atomic(tmp_path / "script" / f"{item}.json", timeline)
    atomic(tmp_path / "script_patched" / f"{item}.json", rows)
    atomic(tmp_path / "script_review" / f"{item}.json", rows)
    store = ReviewStore(tmp_path)
    count, timed, exceptions = convert_legacy(store, item)
    saved, ctx = store.load(item)
    assert count == 2 and timed == 1 and len(exceptions) == 1
    assert [row["text"] for row in saved] == ["first", "second"]
    assert len({row["index"] for row in saved}) == 2
    assert ctx["paragraph_bindings"][saved[1]["index"]]["start_ms"] is None
    assert read(store.folder(item) / "legacy-baseline.json")["script"] == rows
    assert read(tmp_path / "script" / f"{item}.json") == timeline


def test_legacy_history_events_can_be_read_without_full_snapshot(tmp_path):
    log = tmp_path / "logs/sermon_meta_audit.log"
    log.parent.mkdir()
    log.write_text('{"item":"test","actor_name":"Editor","timestamp":"2025-01-01","context":"publish"}\n')
    assert legacy_history(tmp_path, "test", include_snapshot=False) == {
        "events": [{"actor": "Editor", "time": "2025-01-01", "action": "publish"}],
        "snapshot_available": False,
    }


def test_clip_originals_are_bound_to_the_full_lecture(tmp_path):
    for kind in ("script", "script_patched", "script_review"):
        atomic(tmp_path / kind / f"{CLIP_ID}.json", {"kind": kind})
    files = archive_clip(tmp_path)
    assert len(files) == 3
    assert {read(tmp_path / row["path"])["kind"] for row in files} == {
        "script", "script_patched", "script_review",
    }
    assert read(tmp_path / "transcript_review" / FULL_ID / "related-sources/manifest.json")["source_item"] == CLIP_ID
