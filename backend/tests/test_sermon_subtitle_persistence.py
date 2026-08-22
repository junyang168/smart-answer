from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.pipeline import detailed_knowledge_extraction_runner as runner
from backend.pipeline.detailed_knowledge_extraction_runner import SectionSettings
from backend.pipeline.sermon_subtitle_persistence import (
    SubtitleBodyMutationError,
    SubtitlePersistenceError,
    apply_insertions,
    body_rows,
    verify_saved_result,
)


def _rows() -> list[dict[str, Any]]:
    return [
        {"index": 1, "end_index": 20, "text": "第一段正文。"},
        {"index": 21, "end_index": 36, "text": "第二段正文。"},
        {"index": 37, "end_index": 50, "text": "第三段正文。"},
    ]


def _insertions() -> list[dict[str, Any]]:
    return [
        {"after_index": "START", "text": "## 第一部分", "level": 1},
        {"after_index": "21", "text": "### 内部说明", "level": 2},
    ]


def test_apply_insertions_preserves_every_body_row_and_all_subtitle_levels() -> None:
    before = _rows()
    after = apply_insertions(
        before, _insertions(), source_sha256="a" * 64, user_id="pipeline@example.org"
    )

    assert body_rows(after) == before
    assert [row["text"] for row in after if row.get("type") == "subtitle"] == [
        "## 第一部分", "### 内部说明"
    ]
    assert after[0]["index"] == "subtitle-pipeline-aaaaaaaaaaaa-01"


def test_apply_insertions_rejects_unknown_anchor_without_partial_output() -> None:
    with pytest.raises(SubtitlePersistenceError, match="does not name"):
        apply_insertions(
            _rows(),
            [{"after_index": "missing", "text": "## 标题", "level": 1}],
            source_sha256="b" * 64,
            user_id="pipeline@example.org",
        )


def test_saved_result_rejects_body_text_mutation() -> None:
    before = _rows()
    after = apply_insertions(
        before, _insertions(), source_sha256="c" * 64, user_id="pipeline@example.org"
    )
    after[-1]["text"] = "被改掉的正文。"
    with pytest.raises(SubtitleBodyMutationError, match="pre-save sermon rows"):
        verify_saved_result(before, after, expected_insertions=2)


class _SavingWriter:
    def __init__(self, path: Path, *, save_error: bool = False, mutate_body: bool = False):
        self.path = path
        self.save_error = save_error
        self.mutate_body = mutate_body
        self.calls = 0

    def __call__(
        self, actor_id: str, item: str, *, expected_source_sha256: str,
        insertions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.calls += 1
        assert item == self.path.stem
        source_path = self.path
        if self.save_error:
            raise RuntimeError("write failed")
        raw = self.path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected_source_sha256
        rows = json.loads(raw)
        updated = apply_insertions(
            rows, insertions, source_sha256=expected_source_sha256, user_id=actor_id
        )
        if self.mutate_body:
            updated[-1]["text"] = "正文被保存层改坏。"
        self.path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
        after_sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        return {
            "source_path": str(self.path),
            "after_source_sha256": after_sha256,
            "insertions": len(insertions),
        }


def _source(tmp_path: Path, *, heading: bool = False) -> Path:
    folder = tmp_path / "script_review"
    folder.mkdir()
    path = folder / "S test.json"
    rows = _rows()
    if heading:
        rows.insert(0, {"index": "subtitle-existing", "type": "subtitle", "text": "## 已有标题"})
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


def _capture_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_run(**kwargs: Any) -> tuple[str, Path]:
        captured.update(kwargs)
        return "created", kwargs["output_dir"] / "result.json"

    monkeypatch.setattr(runner, "_run", fake_run)
    return captured


def test_run_one_reloads_persisted_source_before_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    output_dir = tmp_path / "out"
    writer = _SavingWriter(source_path)
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(runner, "generate_subtitles", lambda *_args, **_kwargs: _insertions())

    runner.run_one(
        source_path,
        output_dir=output_dir,
        client=object(),
        prompt="prompt",
        reasoning_effort="medium",
        force=False,
        sections=SectionSettings(),
        write_back_subtitles=True,
        subtitle_actor_id="editor@example.org",
        subtitle_writer=writer,
    )

    assert writer.calls == 1
    assert hashlib.sha256(captured["raw"]).hexdigest() == hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest()
    assert captured["sections"].allow_generated is False
    assert captured["source"]["script"][0]["text"] == "## 第一部分"
    audit = json.loads(next((output_dir / "subtitle-applications").rglob("application.json")).read_text())
    assert audit["status"] == "persisted"
    assert len(audit["insertions"]) == 2


def test_write_back_subtitle_generation_uses_the_subscription_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    writer = _SavingWriter(source_path)
    _capture_run(monkeypatch)
    seen: dict[str, Any] = {}

    class FakeSubscriptionClient:
        pass

    client = FakeSubscriptionClient()
    monkeypatch.setattr(runner, "CodexSubscriptionClient", FakeSubscriptionClient)

    def fake_generate(*_args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        seen.update(kwargs)
        return _insertions()

    monkeypatch.setattr(runner, "generate_subtitles", fake_generate)
    runner.run_one(
        source_path,
        output_dir=tmp_path / "out",
        client=client,
        prompt="prompt",
        reasoning_effort="medium",
        force=False,
        write_back_subtitles=True,
        subtitle_actor_id="editor@example.org",
        subtitle_writer=writer,
    )
    assert seen["client"] is client


def test_write_failure_stops_before_extraction_and_is_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    output_dir = tmp_path / "out"
    writer = _SavingWriter(source_path, save_error=True)
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(runner, "generate_subtitles", lambda *_args, **_kwargs: _insertions())

    with pytest.raises(RuntimeError, match="write failed"):
        runner.run_one(
            source_path,
            output_dir=output_dir,
            client=object(),
            prompt="prompt",
            reasoning_effort="medium",
            force=False,
            write_back_subtitles=True,
            subtitle_actor_id="editor@example.org",
            subtitle_writer=writer,
        )

    assert captured == {}
    audit = json.loads(next((output_dir / "subtitle-applications").rglob("application.json")).read_text())
    assert audit["status"] == "failed"
    assert "RuntimeError" in audit["error"]


def test_post_save_body_mutation_stops_before_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    writer = _SavingWriter(source_path, mutate_body=True)
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(runner, "generate_subtitles", lambda *_args, **_kwargs: _insertions())

    with pytest.raises(SubtitleBodyMutationError, match="pre-save sermon rows"):
        runner.run_one(
            source_path,
            output_dir=tmp_path / "out",
            client=object(),
            prompt="prompt",
            reasoning_effort="medium",
            force=False,
            write_back_subtitles=True,
            subtitle_actor_id="editor@example.org",
            subtitle_writer=writer,
        )
    assert captured == {}


def test_missing_actor_stops_before_generation_or_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(
        runner, "generate_subtitles",
        lambda *_args, **_kwargs: pytest.fail("generator must not run without an actor"),
    )

    with pytest.raises(SubtitlePersistenceError, match="subtitle-user-id"):
        runner.run_one(
            source_path,
            output_dir=tmp_path / "out",
            client=object(),
            prompt="prompt",
            reasoning_effort="medium",
            force=False,
            write_back_subtitles=True,
        )
    assert captured == {}


def test_pipeline_default_writer_uses_governed_service_and_stops_on_acl_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.api.sc_api import sermon_manager as manager_module

    source_path = _source(tmp_path)
    output_dir = tmp_path / "out"
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(runner, "generate_subtitles", lambda *_args, **_kwargs: _insertions())
    calls: list[tuple[str, str]] = []

    class DenyingManager:
        def persist_generated_subtitles(self, actor_id: str, item: str, **_kwargs: Any) -> dict:
            calls.append((actor_id, item))
            raise PermissionError("ACL denied")

    monkeypatch.setattr(manager_module, "sermonManager", DenyingManager())
    with pytest.raises(PermissionError, match="ACL denied"):
        runner.run_one(
            source_path,
            output_dir=output_dir,
            client=object(),
            prompt="prompt",
            reasoning_effort="medium",
            force=False,
            write_back_subtitles=True,
            subtitle_actor_id="reader@example.org",
        )

    assert calls == [("reader@example.org", "S test")]
    assert captured == {}
    audit = json.loads(
        next((output_dir / "subtitle-applications").rglob("application.json")).read_text()
    )
    assert audit["status"] == "failed"
    assert "PermissionError" in audit["error"]


def test_saved_result_rejects_mutation_of_an_existing_subtitle_row() -> None:
    before = [
        {"index": "existing-subtitle", "type": "subtitle", "text": "### 原有提示"},
        *_rows(),
    ]
    after = apply_insertions(
        before, _insertions(), source_sha256="d" * 64, user_id="editor@example.org"
    )
    next(row for row in after if row["index"] == "existing-subtitle")["text"] = (
        "### 被改掉的原有提示"
    )
    with pytest.raises(SubtitleBodyMutationError, match="pre-save sermon rows"):
        verify_saved_result(before, after, expected_insertions=2)


def test_existing_headings_are_a_noop_for_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path, heading=True)
    writer = _SavingWriter(source_path)
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(
        runner, "generate_subtitles",
        lambda *_args, **_kwargs: pytest.fail("generator must not run"),
    )

    runner.run_one(
        source_path,
        output_dir=tmp_path / "out",
        client=object(),
        prompt="prompt",
        reasoning_effort="medium",
        force=False,
        write_back_subtitles=True,
        subtitle_actor_id="editor@example.org",
        subtitle_writer=writer,
    )
    assert writer.calls == 0
    assert captured["raw"] == source_path.read_bytes()


def test_sermon_manager_save_service_enforces_acl_and_expected_sha(tmp_path: Path) -> None:
    from backend.api.sc_api.sermon_manager import SermonManager

    folder = tmp_path / "script_review"
    folder.mkdir()
    source_path = folder / "S governed.json"
    source_path.write_text(json.dumps(_rows(), ensure_ascii=False), encoding="utf-8")
    manager = SermonManager.__new__(SermonManager)
    manager.base_folder = str(tmp_path)
    manager._sm = SimpleNamespace(update_sermon_metadata=lambda *_args: None)
    manager.get_sermon_permissions = lambda *_args: SimpleNamespace(canWrite=False)

    with pytest.raises(PermissionError):
        manager.persist_generated_subtitles(
            "reader@example.org", "S governed",
            expected_source_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
            insertions=_insertions(),
        )
    assert json.loads(source_path.read_text()) == _rows()

    manager.get_sermon_permissions = lambda *_args: SimpleNamespace(canWrite=True)
    with pytest.raises(SubtitlePersistenceError, match="changed before subtitle write-back"):
        manager.persist_generated_subtitles(
            "editor@example.org", "S governed",
            expected_source_sha256="0" * 64,
            insertions=_insertions(),
        )


def test_sermon_manager_save_service_preserves_body_and_returns_post_save_sha(
    tmp_path: Path,
) -> None:
    from backend.api.sc_api.sermon_manager import SermonManager

    folder = tmp_path / "script_review"
    folder.mkdir()
    source_path = folder / "S governed.json"
    source_path.write_text(json.dumps(_rows(), ensure_ascii=False), encoding="utf-8")
    manager = SermonManager.__new__(SermonManager)
    manager.base_folder = str(tmp_path)
    manager._sm = SimpleNamespace(update_sermon_metadata=lambda *_args: None)
    manager.get_sermon_permissions = lambda *_args: SimpleNamespace(canWrite=True)
    before_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()

    report = manager.persist_generated_subtitles(
        "editor@example.org", "S governed",
        expected_source_sha256=before_sha,
        insertions=_insertions(),
    )

    after = json.loads(source_path.read_text())
    assert body_rows(after) == _rows()
    assert report["before_source_sha256"] == before_sha
    assert report["after_source_sha256"] == hashlib.sha256(source_path.read_bytes()).hexdigest()
    assert report["before_body_sha256"] == report["after_body_sha256"]
