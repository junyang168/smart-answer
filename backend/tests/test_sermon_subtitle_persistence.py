from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.api.sc_api.script_delta import ScriptConflictError, ScriptDelta
from backend.pipeline import detailed_knowledge_extraction_runner as runner
from backend.pipeline.detailed_knowledge_extraction_runner import SectionSettings
from backend.pipeline.extraction_sections import FROM_SOURCE
from backend.pipeline.sermon_subtitle_persistence import (
    SubtitleBodyMutationError,
    SubtitlePersistenceError,
    apply_insertions,
    body_rows,
    payload_with_rows,
    transcript_rows,
    verify_saved_result,
    write_back_generated_subtitles,
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


def test_publish_is_atomic_and_bound_to_the_exact_review_snapshot(
    tmp_path: Path,
) -> None:
    item = "visual-source"
    for folder in ("script", "script_review", "script_published"):
        (tmp_path / folder).mkdir()
    (tmp_path / "script" / f"{item}.json").write_text(
        json.dumps({"entries": []}), encoding="utf-8"
    )
    review_path = tmp_path / "script_review" / f"{item}.json"
    review_path.write_text(
        json.dumps(
            [{"index": "subtitle-1", "type": "subtitle", "text": "## 图示"}],
            ensure_ascii=False,
            indent=4,
        ),
        encoding="utf-8",
    )
    review_sha = hashlib.sha256(review_path.read_bytes()).hexdigest()
    delta = ScriptDelta(str(tmp_path), item)

    with pytest.raises(ScriptConflictError, match="changed before publish"):
        delta.publish("editor@example.org", expected_review_sha256="0" * 64)
    assert not (tmp_path / "script_published" / f"{item}.json").exists()

    published_sha = delta.publish(
        "editor@example.org", expected_review_sha256=review_sha
    )
    published_path = tmp_path / "script_published" / f"{item}.json"
    assert hashlib.sha256(published_path.read_bytes()).hexdigest() == published_sha
    assert json.loads(published_path.read_text(encoding="utf-8"))["script"] == [
        {"index": "subtitle-1", "type": "subtitle", "text": "## 图示"}
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


def test_apply_insertions_rejects_duplicate_boundary() -> None:
    with pytest.raises(SubtitlePersistenceError, match="duplicate generated subtitle boundary"):
        apply_insertions(
            _rows(),
            [
                {"after_index": "START", "text": "## 标题一", "level": 1},
                {"after_index": "START", "text": "## 标题二", "level": 1},
            ],
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


def test_saved_result_rejects_comment_row_mutation_even_though_comments_are_not_source() -> None:
    before = [
        {"index": "comment-1", "type": "comment", "text": "编辑备注"},
        *_rows(),
    ]
    after = apply_insertions(
        before, _insertions(), source_sha256="d" * 64, user_id="pipeline@example.org"
    )
    next(row for row in after if row.get("type") == "comment")["text"] = "被改掉的备注"

    with pytest.raises(SubtitleBodyMutationError, match="pre-save sermon rows"):
        verify_saved_result(before, after, expected_insertions=2)


def test_saved_result_rejects_mutation_inside_soft_deleted_text() -> None:
    before = _rows()
    before[0]["text"] = "保留~~旧字~~正文。"
    after = apply_insertions(
        before, _insertions(), source_sha256="e" * 64, user_id="pipeline@example.org"
    )
    next(row for row in after if row.get("index") == 1)["text"] = "保留~~新字~~正文。"

    with pytest.raises(SubtitleBodyMutationError, match="pre-save sermon rows"):
        verify_saved_result(before, after, expected_insertions=2)


def test_atomic_subtitle_save_refuses_an_intervening_editor_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    expected = hashlib.sha256(source_path.read_bytes()).hexdigest()
    from backend.api.sc_api.script_delta import ScriptDelta

    atomic_save = ScriptDelta.save_rows

    def editor_wins_before_compare(*args: Any, **kwargs: Any) -> str:
        rows = json.loads(source_path.read_text(encoding="utf-8"))
        rows[-1]["text"] = "编辑刚刚保存的新正文。"
        source_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return atomic_save(*args, **kwargs)

    monkeypatch.setattr(ScriptDelta, "save_rows", staticmethod(editor_wins_before_compare))
    with pytest.raises(RuntimeError, match="changed before atomic save"):
        write_back_generated_subtitles(
            source_path,
            expected_source_sha256=expected,
            insertions=_insertions(),
            actor_id="editor@example.org",
        )

    assert json.loads(source_path.read_text(encoding="utf-8"))[-1]["text"] == (
        "编辑刚刚保存的新正文。"
    )


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
        payload = json.loads(raw)
        stage = self.path.parent.name
        rows = transcript_rows(payload, stage=stage)
        updated = apply_insertions(
            rows, insertions, source_sha256=expected_source_sha256, user_id=actor_id
        )
        if self.mutate_body:
            updated[-1]["text"] = "正文被保存层改坏。"
        updated_payload = payload_with_rows(payload, updated, stage=stage)
        self.path.write_text(
            json.dumps(updated_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
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


def _published_source(tmp_path: Path) -> Path:
    folder = tmp_path / "script_published"
    folder.mkdir()
    path = folder / "S published.json"
    path.write_text(
        json.dumps(
            {"metadata": {"status": "published"}, "script": _rows()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_published_subtitle_write_preserves_wrapper_and_every_existing_row(
    tmp_path: Path,
) -> None:
    source_path = _published_source(tmp_path)
    before_payload = json.loads(source_path.read_text(encoding="utf-8"))
    before_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()

    report = write_back_generated_subtitles(
        source_path,
        expected_source_sha256=before_sha,
        insertions=_insertions(),
        actor_id="editor@example.org",
    )

    after_payload = json.loads(source_path.read_text(encoding="utf-8"))
    assert after_payload["metadata"] == before_payload["metadata"]
    verify_saved_result(
        before_payload["script"], after_payload["script"], expected_insertions=2
    )
    assert body_rows(after_payload["script"]) == body_rows(before_payload["script"])
    assert report["before_body_sha256"] == report["after_body_sha256"]
    assert report["after_source_sha256"] == hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest()


def test_published_subtitle_write_refuses_a_concurrent_metadata_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _published_source(tmp_path)
    before_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    original_save = ScriptDelta.save_json_payload

    def metadata_editor_wins(
        base_folder: str, item: str, stage: str, payload: Any, **kwargs: Any
    ) -> str:
        current = json.loads(source_path.read_text(encoding="utf-8"))
        current["metadata"]["last_updated"] = "concurrent-editor"
        source_path.write_text(
            json.dumps(current, ensure_ascii=False), encoding="utf-8"
        )
        return original_save(base_folder, item, stage, payload, **kwargs)

    monkeypatch.setattr(ScriptDelta, "save_json_payload", metadata_editor_wins)

    with pytest.raises(ScriptConflictError, match="changed before atomic save"):
        write_back_generated_subtitles(
            source_path,
            expected_source_sha256=before_sha,
            insertions=_insertions(),
            actor_id="editor@example.org",
        )

    current = json.loads(source_path.read_text(encoding="utf-8"))
    assert current["metadata"]["last_updated"] == "concurrent-editor"
    assert not any(row.get("type") == "subtitle" for row in current["script"])


def test_headingless_published_stops_before_extraction_without_writeback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _published_source(tmp_path)
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(
        runner,
        "generate_subtitles",
        lambda *_args, **_kwargs: pytest.fail(
            "an internal section plan must not bypass title persistence"
        ),
    )

    with pytest.raises(SubtitlePersistenceError, match="untitled leading section"):
        runner.run_one(
            source_path,
            output_dir=tmp_path / "out",
            client=object(),
            prompt="prompt",
            reasoning_effort="medium",
            force=False,
        )

    assert captured == {}


def test_run_one_persists_published_titles_before_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _published_source(tmp_path)
    before_payload = json.loads(source_path.read_text(encoding="utf-8"))
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(runner, "generate_subtitles", lambda *_args, **_kwargs: _insertions())

    runner.run_one(
        source_path,
        output_dir=tmp_path / "out",
        client=object(),
        prompt="prompt",
        reasoning_effort="medium",
        force=False,
        write_back_subtitles=True,
        subtitle_actor_id="editor@example.org",
        subtitle_writer=_SavingWriter(source_path),
        subtitle_authorizer=lambda _actor_id: True,
    )

    after_payload = json.loads(source_path.read_text(encoding="utf-8"))
    assert after_payload["metadata"] == before_payload["metadata"]
    assert body_rows(after_payload["script"]) == body_rows(before_payload["script"])
    assert captured["source"]["script"][0]["text"] == "## 第一部分"
    assert captured["sections"].allow_generated is False


def test_write_boundary_reloads_and_rejects_a_corrupt_committed_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    expected = hashlib.sha256(source_path.read_bytes()).hexdigest()
    from backend.api.sc_api.script_delta import ScriptDelta

    def corrupt_save(_root: str, _item: str, _stage: str, rows: list[dict], **_kwargs: Any) -> str:
        corrupted = [dict(row) for row in rows]
        next(row for row in corrupted if row.get("index") == 1)["text"] = "提交时损坏"
        source_path.write_text(
            json.dumps(corrupted, ensure_ascii=False, indent=4), encoding="utf-8"
        )
        return hashlib.sha256(source_path.read_bytes()).hexdigest()

    monkeypatch.setattr(ScriptDelta, "save_rows", staticmethod(corrupt_save))

    with pytest.raises(SubtitleBodyMutationError, match="pre-save sermon rows"):
        write_back_generated_subtitles(
            source_path,
            expected_source_sha256=expected,
            insertions=_insertions(),
            actor_id="editor@example.org",
        )


def test_write_boundary_rejects_a_different_inserted_subtitle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    expected = hashlib.sha256(source_path.read_bytes()).hexdigest()
    from backend.api.sc_api.script_delta import ScriptDelta

    def replace_generated_title(
        _root: str, _item: str, _stage: str, rows: list[dict], **_kwargs: Any
    ) -> str:
        corrupted = [dict(row) for row in rows]
        next(row for row in corrupted if row.get("type") == "subtitle")["text"] = (
            "## 未经授权的不同标题"
        )
        source_path.write_text(
            json.dumps(corrupted, ensure_ascii=False, indent=4), encoding="utf-8"
        )
        return hashlib.sha256(source_path.read_bytes()).hexdigest()

    monkeypatch.setattr(ScriptDelta, "save_rows", staticmethod(replace_generated_title))

    with pytest.raises(SubtitlePersistenceError, match="exact authorized"):
        write_back_generated_subtitles(
            source_path,
            expected_source_sha256=expected,
            insertions=_insertions(),
            actor_id="editor@example.org",
        )


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
        subtitle_authorizer=lambda _actor_id: True,
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
        subtitle_authorizer=lambda _actor_id: True,
    )
    assert seen["client"] is client


def test_subtitles_only_persists_and_stops_before_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    writer = _SavingWriter(source_path)
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(runner, "generate_subtitles", lambda *_args, **_kwargs: _insertions())

    status, output = runner.run_one(
        source_path,
        output_dir=tmp_path / "out",
        client=object(),
        prompt="prompt",
        reasoning_effort="medium",
        force=False,
        write_back_subtitles=True,
        subtitle_actor_id="editor@example.org",
        subtitle_writer=writer,
        subtitles_only=True,
        subtitle_authorizer=lambda _actor_id: True,
    )

    assert status == "created"
    assert output == source_path
    assert writer.calls == 1
    assert captured == {}


def test_subtitles_only_skips_a_source_that_already_has_headings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path, heading=True)
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(
        runner, "generate_subtitles",
        lambda *_args, **_kwargs: pytest.fail("existing headings must not be regenerated"),
    )

    status, output = runner.run_one(
        source_path,
        output_dir=tmp_path / "out",
        client=object(),
        prompt="prompt",
        reasoning_effort="medium",
        force=False,
        write_back_subtitles=True,
        subtitle_actor_id="editor@example.org",
        subtitles_only=True,
        subtitle_authorizer=lambda _actor_id: True,
    )

    assert status == "skipped"
    assert output == source_path
    assert captured == {}


def test_subtitles_only_titles_only_the_prefix_before_a_later_heading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    rows = json.loads(source_path.read_text(encoding="utf-8"))
    rows.append({
        "index": "subtitle-existing", "type": "subtitle", "text": "## 已有后段标题"
    })
    source_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    writer = _SavingWriter(source_path)
    captured = _capture_run(monkeypatch)
    seen: dict[str, Any] = {}

    def generate(paragraphs: list[dict[str, Any]], **_kwargs: Any) -> list[dict[str, Any]]:
        seen["indexes"] = [str(row["index"]) for row in paragraphs]
        return _insertions()

    monkeypatch.setattr(runner, "generate_subtitles", generate)
    status, output = runner.run_one(
        source_path,
        output_dir=tmp_path / "out",
        client=object(),
        prompt="prompt",
        reasoning_effort="medium",
        force=False,
        write_back_subtitles=True,
        subtitle_actor_id="editor@example.org",
        subtitle_writer=writer,
        subtitles_only=True,
        subtitle_authorizer=lambda _actor_id: True,
    )

    assert status == "created"
    assert output == source_path
    assert writer.calls == 1
    assert seen["indexes"] == ["1", "21", "37"]
    assert json.loads(source_path.read_text(encoding="utf-8"))[0]["text"] == "## 第一部分"
    assert captured == {}


def test_subtitle_authorization_fails_before_the_generation_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    writer = _SavingWriter(source_path)
    monkeypatch.setattr(
        runner,
        "generate_subtitles",
        lambda *_args, **_kwargs: pytest.fail(
            "subtitle generation must not run before authorization"
        ),
    )

    with pytest.raises(PermissionError, match="permission"):
        runner.run_one(
            source_path,
            output_dir=tmp_path / "out",
            client=object(),
            prompt="prompt",
            reasoning_effort="medium",
            force=False,
            write_back_subtitles=True,
            subtitle_actor_id="reader@example.org",
            subtitle_writer=writer,
            subtitle_authorizer=lambda _actor_id: False,
        )

    assert writer.calls == 0
    assert json.loads(source_path.read_text(encoding="utf-8")) == _rows()


def test_custom_subtitle_writer_requires_an_authorization_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    writer = _SavingWriter(source_path)
    monkeypatch.setattr(
        runner,
        "generate_subtitles",
        lambda *_args, **_kwargs: pytest.fail(
            "subtitle generation must not run without an authorization preflight"
        ),
    )

    with pytest.raises(SubtitlePersistenceError, match="authorization preflight"):
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


def test_internal_generated_plan_never_substitutes_for_title_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    output_dir = tmp_path / "out"
    writer = _SavingWriter(source_path)
    captured = _capture_run(monkeypatch)
    before_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    plan = runner.SectionPlan(
        sections=(
            runner.Section(index=1, start=0, end=2, title="冻结的第一部分"),
            runner.Section(index=2, start=2, end=3, title="冻结的第二部分"),
        ),
        origin=runner.FROM_GENERATOR,
    )
    plan_path = output_dir / "section-plans" / f"{runner._slug(source_path.stem)}.json"
    runner.save_plan(plan_path, plan, before_sha)
    calls = 0

    def generate(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        return _insertions()

    monkeypatch.setattr(runner, "generate_subtitles", generate)

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
        subtitle_authorizer=lambda _actor_id: True,
    )

    assert calls == 1
    assert [
        row["text"] for row in captured["source"]["script"]
        if row.get("type") == "subtitle"
    ] == ["## 第一部分", "### 内部说明"]
    audit = json.loads(
        next((output_dir / "subtitle-applications").rglob("application.json")).read_text()
    )
    assert audit["insertion_origin"] == "new_model_generation"
    assert "cached_section_plan" not in audit


def test_legacy_physical_coordinate_plan_is_not_reused_when_comments_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    rows = json.loads(source_path.read_text(encoding="utf-8"))
    rows.insert(1, {"index": "comment-a", "type": "comment", "text": "编辑备注"})
    source_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / "out"
    plan_path = output_dir / "section-plans" / f"{runner._slug(source_path.stem)}.json"
    plan_path.parent.mkdir(parents=True)
    physical_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    plan_path.write_text(json.dumps({
        "source_sha256": physical_sha,
        "origin": runner.FROM_GENERATOR,
        "sections": [
            {"index": 1, "start": 0, "end": 2, "title": "旧坐标第一部分"},
            {"index": 2, "start": 2, "end": 3, "title": "旧坐标第二部分"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    called: list[list[dict[str, Any]]] = []

    def fresh_generation(paragraphs: list[dict[str, Any]], **_kwargs: Any):
        called.append(paragraphs)
        return _insertions()

    monkeypatch.setattr(runner, "generate_subtitles", fresh_generation)
    _capture_run(monkeypatch)
    runner.run_one(
        source_path,
        output_dir=output_dir,
        client=object(),
        prompt="prompt",
        reasoning_effort="medium",
        force=False,
        write_back_subtitles=True,
        subtitle_actor_id="editor@example.org",
        subtitle_writer=_SavingWriter(source_path),
        subtitle_authorizer=lambda _actor_id: True,
    )

    assert len(called) == 1
    assert [row["index"] for row in called[0]] == [1, 21, 37]
    audit = json.loads(
        next((output_dir / "subtitle-applications").rglob("application.json")).read_text()
    )
    assert audit["insertion_origin"] == "new_model_generation"


def test_headingless_published_source_migrates_exact_legacy_physical_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    source, raw = runner._load(source_path)
    projection = runner.project_script(source["script"])
    physical_sha = hashlib.sha256(raw).hexdigest()
    output_dir = tmp_path / "out"
    plan_path = output_dir / "section-plans" / f"{runner._slug(source_path.stem)}.json"
    frozen = runner.SectionPlan(
        sections=(
            runner.Section(index=1, start=0, end=2, title="冻结的第一部分"),
            runner.Section(index=2, start=2, end=3, title="冻结的第二部分"),
        ),
        origin=runner.FROM_GENERATOR,
    )
    runner.save_plan(plan_path, frozen, physical_sha)
    monkeypatch.setattr(
        runner,
        "generate_subtitles",
        lambda *_args, **_kwargs: pytest.fail("exact legacy plan must not regenerate"),
    )

    resolved = runner.resolve_section_plan(
        source=source,
        source_id=source_path.stem,
        source_sha256=projection.body_sha256,
        source_file_sha256=physical_sha,
        output_dir=output_dir,
        client=object(),
    )

    assert resolved == frozen
    migrated = json.loads(plan_path.read_text(encoding="utf-8"))
    assert migrated["source_body_sha256"] == projection.body_sha256
    assert migrated["source_file_sha256"] == physical_sha
    assert migrated["locator_space"] == runner.LOCATOR_SPACE


def test_headingless_published_source_regenerates_untitled_cached_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _published_source(tmp_path)
    before_raw = source_path.read_bytes()
    source, raw = runner._load(source_path)
    projection = runner.project_script(source["script"])
    output_dir = tmp_path / "out"
    plan_path = output_dir / "section-plans" / f"{runner._slug(source_path.stem)}.json"
    runner.save_plan(
        plan_path,
        runner.SectionPlan(
            sections=(
                runner.Section(index=1, start=0, end=1, title=""),
                runner.Section(index=2, start=1, end=3, title="旧缓存第二部分"),
            ),
            origin=runner.FROM_GENERATOR,
        ),
        projection.body_sha256,
        source_file_sha256=hashlib.sha256(raw).hexdigest(),
        editorial_structure_sha256=projection.editorial_structure_sha256,
        editorial_topology_sha256=projection.editorial_topology_sha256,
    )
    calls = 0

    def fresh_generation(*_args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        assert kwargs["require_leading_title"] is True
        return [
            {"after_index": "START", "text": "## 新的第一部分", "level": 1},
            {"after_index": "21", "text": "## 新的第二部分", "level": 1},
        ]

    monkeypatch.setattr(runner, "generate_subtitles", fresh_generation)

    resolved = runner.resolve_section_plan(
        source=source,
        source_id=source_path.stem,
        source_sha256=projection.body_sha256,
        source_file_sha256=hashlib.sha256(raw).hexdigest(),
        output_dir=output_dir,
        client=object(),
    )

    assert calls == 1
    assert [section.title for section in resolved.sections] == [
        "新的第一部分",
        "新的第二部分",
    ]
    assert source_path.read_bytes() == before_raw


def test_partially_headed_source_cannot_extract_an_untitled_leading_span(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = {
        "script": [
            _rows()[0],
            {"index": "subtitle-later", "type": "subtitle", "text": "## 后一部分"},
            *_rows()[1:],
        ]
    }
    projection = runner.project_script(source["script"])
    monkeypatch.setattr(
        runner,
        "generate_subtitles",
        lambda *_args, **_kwargs: pytest.fail(
            "source headings must not be silently replaced by generated headings"
        ),
    )

    with pytest.raises(runner.SectionBoundaryError, match="untitled"):
        runner.resolve_section_plan(
            source=source,
            source_id="partially headed",
            source_sha256=projection.body_sha256,
            output_dir=tmp_path / "out",
            client=object(),
        )


def test_disabling_generation_cannot_turn_a_headingless_source_into_one_untitled_section(
    tmp_path: Path,
) -> None:
    source = {"script": _rows()}
    projection = runner.project_script(source["script"])

    with pytest.raises(runner.SectionBoundaryError, match="untitled"):
        runner.resolve_section_plan(
            source=source,
            source_id="headingless no generation",
            source_sha256=projection.body_sha256,
            output_dir=tmp_path / "out",
            allow_generated=False,
        )


def test_untitled_source_heading_cache_cannot_bypass_plan_postcondition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = {
        "script": [
            _rows()[0],
            {"index": "subtitle-later", "type": "subtitle", "text": "## 后一部分"},
            *_rows()[1:],
        ]
    }
    projection = runner.project_script(source["script"])
    output_dir = tmp_path / "out"
    plan_path = output_dir / "section-plans" / f"{runner._slug('partial cached')}.json"
    runner.save_plan(
        plan_path,
        runner.SectionPlan(
            sections=(
                runner.Section(index=1, start=0, end=1, title=""),
                runner.Section(index=2, start=1, end=3, title="后一部分"),
            ),
            origin=FROM_SOURCE,
        ),
        projection.body_sha256,
        editorial_structure_sha256=projection.editorial_structure_sha256,
        editorial_topology_sha256=projection.editorial_topology_sha256,
    )
    monkeypatch.setattr(
        runner,
        "generate_subtitles",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid source-heading cache must not invoke full-source generation"
        ),
    )

    with pytest.raises(runner.SectionBoundaryError, match="untitled"):
        runner.resolve_section_plan(
            source=source,
            source_id="partial cached",
            source_sha256=projection.body_sha256,
            output_dir=output_dir,
            client=object(),
        )


def test_mixed_published_source_never_migrates_legacy_physical_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    rows = json.loads(source_path.read_text(encoding="utf-8"))
    rows.insert(1, {"index": "comment-a", "type": "comment", "text": "编辑备注"})
    source_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    source, raw = runner._load(source_path)
    projection = runner.project_script(source["script"])
    physical_sha = hashlib.sha256(raw).hexdigest()
    output_dir = tmp_path / "out"
    plan_path = output_dir / "section-plans" / f"{runner._slug(source_path.stem)}.json"
    runner.save_plan(
        plan_path,
        runner.SectionPlan(
            sections=(
                runner.Section(index=1, start=0, end=2, title="不安全的旧坐标"),
                runner.Section(index=2, start=2, end=3, title="不安全的旧坐标二"),
            ),
            origin=runner.FROM_GENERATOR,
        ),
        physical_sha,
    )
    calls = 0

    def fresh_generation(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        return _insertions()

    monkeypatch.setattr(runner, "generate_subtitles", fresh_generation)
    resolved = runner.resolve_section_plan(
        source=source,
        source_id=source_path.stem,
        source_sha256=projection.body_sha256,
        source_file_sha256=physical_sha,
        output_dir=output_dir,
        client=object(),
    )

    assert calls == 1
    assert resolved.sections[0].title != "不安全的旧坐标"


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
            subtitle_authorizer=lambda _actor_id: True,
        )

    assert captured == {}
    audit = json.loads(next((output_dir / "subtitle-applications").rglob("application.json")).read_text())
    assert audit["status"] == "failed"
    assert "RuntimeError" in audit["error"]


def test_resume_reconciles_a_transcript_commit_that_outlived_its_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    output_dir = tmp_path / "out"
    before_raw = source_path.read_bytes()
    before_sha = hashlib.sha256(before_raw).hexdigest()
    before = json.loads(before_raw)
    after = apply_insertions(
        before, _insertions(), source_sha256=before_sha, user_id="editor@example.org"
    )
    source_path.write_text(json.dumps(after, ensure_ascii=False, indent=4), encoding="utf-8")
    audit_dir = (
        output_dir / "subtitle-applications" / runner._slug(source_path.stem)
        / before_sha[:16]
    )
    audit_dir.mkdir(parents=True)
    (audit_dir / "before-source.json").write_bytes(before_raw)
    expected_after_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    (audit_dir / "application.json").write_text(json.dumps({
        "schema_version": "wang_sermon_subtitle_application_v1",
        "source_id": source_path.stem,
        "source_path": str(source_path),
        "before_source_sha256": before_sha,
        "actor_id": "editor@example.org",
        "insertions": _insertions(),
        "expected_after_source_sha256": expected_after_sha,
        "status": "applying",
    }, ensure_ascii=False), encoding="utf-8")
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(
        runner, "generate_subtitles",
        lambda *_args, **_kwargs: pytest.fail("recovery must not call the model"),
    )

    runner.run_one(
        source_path,
        output_dir=output_dir,
        client=object(),
        prompt="prompt",
        reasoning_effort="medium",
        force=False,
        write_back_subtitles=True,
        subtitle_actor_id="editor@example.org",
    )

    audit = json.loads((audit_dir / "application.json").read_text(encoding="utf-8"))
    assert audit["status"] == "persisted"
    assert audit["save_report"]["recovered_after_interrupted_audit"] is True
    assert captured["source"]["script"] == after


def test_subtitle_generation_rejects_an_empty_section_after_its_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(
        runner,
        "generate_subtitles",
        lambda paragraphs, **_kwargs: [
            {"after_index": "START", "text": "## 第一部分", "level": 1},
            {"after_index": str(paragraphs[-1]["index"]), "text": "## 空标题", "level": 1},
        ],
    )

    with pytest.raises(SubtitlePersistenceError, match="empty section"):
        runner.run_one(
            source_path,
            output_dir=tmp_path / "out",
            client=object(),
            prompt="prompt",
            reasoning_effort="medium",
            force=False,
            write_back_subtitles=True,
            subtitle_actor_id="editor@example.org",
            subtitle_writer=_SavingWriter(source_path),
            subtitle_authorizer=lambda _actor_id: True,
        )
    assert captured == {}


def test_subtitle_generation_rejects_a_duplicate_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(
        runner,
        "generate_subtitles",
        lambda *_args, **_kwargs: [
            {"after_index": "START", "text": "## 第一部分", "level": 1},
            {"after_index": "START", "text": "## 重复第一部分", "level": 1},
        ],
    )

    with pytest.raises(SubtitlePersistenceError, match="duplicate subtitle boundary"):
        runner.run_one(
            source_path,
            output_dir=tmp_path / "out",
            client=object(),
            prompt="prompt",
            reasoning_effort="medium",
            force=False,
            write_back_subtitles=True,
            subtitle_actor_id="editor@example.org",
            subtitle_writer=_SavingWriter(source_path),
            subtitle_authorizer=lambda _actor_id: True,
        )
    assert captured == {}


def test_subtitle_generation_rejects_an_anchor_outside_its_leading_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    rows = json.loads(source_path.read_text(encoding="utf-8"))
    rows.insert(
        2,
        {"index": "later-title", "type": "subtitle", "text": "## 已有后半标题"},
    )
    source_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(
        runner,
        "generate_subtitles",
        lambda *_args, **_kwargs: [
            {"after_index": "START", "text": "## 第一部分", "level": 1},
            {"after_index": "37", "text": "## 越界标题", "level": 1},
        ],
    )

    with pytest.raises(SubtitlePersistenceError, match="outside its generation scope"):
        runner.run_one(
            source_path,
            output_dir=tmp_path / "out",
            client=object(),
            prompt="prompt",
            reasoning_effort="medium",
            force=False,
            write_back_subtitles=True,
            subtitle_actor_id="editor@example.org",
            subtitle_writer=_SavingWriter(source_path),
            subtitle_authorizer=lambda _actor_id: True,
        )
    assert captured == {}


def test_subtitle_generation_preserves_integer_zero_as_an_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    rows = [
        {"index": 0, "end_index": 10, "text": "第一段正文。"},
        {"index": 11, "end_index": 20, "text": "第二段正文。"},
    ]
    source_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    writer = _SavingWriter(source_path)
    _capture_run(monkeypatch)
    monkeypatch.setattr(
        runner,
        "generate_subtitles",
        lambda *_args, **_kwargs: [
            {"after_index": "START", "text": "## 第一部分", "level": 1},
            {"after_index": 0, "text": "## 第二部分", "level": 1},
        ],
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
        subtitle_authorizer=lambda _actor_id: True,
    )

    assert writer.calls == 1
    persisted = json.loads(source_path.read_text(encoding="utf-8"))
    assert [row["text"] for row in persisted if row.get("type") == "subtitle"] == [
        "## 第一部分",
        "## 第二部分",
    ]


def test_headingless_capped_source_generates_a_plan_before_enforcing_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = {"script": _rows()}
    monkeypatch.setattr(
        runner,
        "generate_subtitles",
        lambda *_args, **_kwargs: [
            {"after_index": "START", "text": "## 第一部分", "level": 1},
            {"after_index": "21", "text": "## 第二部分", "level": 1},
        ],
    )

    plan = runner.resolve_section_plan(
        source=source,
        source_id="S test",
        source_sha256=runner.project_script(source["script"]).body_sha256,
        output_dir=tmp_path / "out",
        max_section_sentences=2,
        client=object(),
    )

    assert plan.origin == runner.FROM_GENERATOR
    assert [(section.start, section.end) for section in plan.sections] == [(0, 2), (2, 3)]


def test_new_cap_reuses_uncapped_generated_plan_without_resegmenting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = {"script": _rows()}
    calls = 0

    def generate(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        return [
            {"after_index": "START", "text": "## 第一部分", "level": 1},
            {"after_index": "21", "text": "## 第二部分", "level": 1},
        ]

    monkeypatch.setattr(runner, "generate_subtitles", generate)
    kwargs = {
        "source": source,
        "source_id": "S cached",
        "source_sha256": runner.project_script(source["script"]).body_sha256,
        "output_dir": tmp_path / "out",
        "client": object(),
    }
    base = runner.resolve_section_plan(**kwargs)
    capped = runner.resolve_section_plan(**kwargs, max_section_sentences=1)

    assert calls == 1
    assert [(row.start, row.end) for row in base.sections] == [(0, 2), (2, 3)]
    assert [(row.start, row.end) for row in capped.sections] == [(0, 1), (1, 2), (2, 3)]
    assert {row["boundary_kind"] for row in capped.split_lineage} == {"spoken_row"}


def test_old_capped_generated_plan_requires_explicit_transport_migration(
    tmp_path: Path,
) -> None:
    source = {"script": _rows()}
    projection = runner.project_script(source["script"])
    output_dir = tmp_path / "out"
    path = output_dir / "section-plans" / f"{runner._slug('S legacy capped')}.json"
    runner.save_plan(
        path,
        runner.SectionPlan(
            sections=(
                runner.Section(index=1, start=0, end=1, title="第一部分"),
                runner.Section(index=2, start=1, end=3, title="第二部分"),
            ),
            origin=runner.FROM_GENERATOR,
            max_section_sentences=180,
            strategy="next_heading_balanced_min_chunks_v1",
            split_lineage=({"section_index": 1},),
        ),
        source_sha256=projection.body_sha256,
        editorial_structure_sha256=projection.editorial_structure_sha256,
    )
    with pytest.raises(runner.SectionBoundaryError, match="explicit migration"):
        runner.resolve_section_plan(
            source=source,
            source_id="S legacy capped",
            source_sha256=projection.body_sha256,
            output_dir=output_dir,
            max_section_sentences=125,
            client=object(),
        )


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
            subtitle_authorizer=lambda _actor_id: True,
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


def test_headingless_review_stops_before_extraction_without_governed_writeback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    captured = _capture_run(monkeypatch)

    with pytest.raises(SubtitlePersistenceError, match="untitled leading section"):
        runner.run_one(
            source_path,
            output_dir=tmp_path / "out",
            client=object(),
            prompt="prompt",
            reasoning_effort="medium",
            force=False,
        )

    assert captured == {}


def test_later_heading_does_not_hide_an_untitled_leading_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = _source(tmp_path)
    rows = json.loads(source_path.read_text())
    rows.insert(2, {"index": "later-title", "type": "subtitle", "text": "## 后半标题"})
    source_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    captured = _capture_run(monkeypatch)

    with pytest.raises(SubtitlePersistenceError, match="untitled leading section"):
        runner.run_one(
            source_path,
            output_dir=tmp_path / "out",
            client=object(),
            prompt="prompt",
            reasoning_effort="medium",
            force=False,
        )

    assert captured == {}


def test_pipeline_default_writer_uses_governed_service_and_stops_on_acl_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.api.sc_api import sermon_manager as manager_module

    source_path = _source(tmp_path)
    output_dir = tmp_path / "out"
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(
        runner,
        "generate_subtitles",
        lambda *_args, **_kwargs: pytest.fail(
            "subtitle generation must not run before the default ACL preflight"
        ),
    )
    calls: list[tuple[str, str]] = []

    class DenyingManager:
        def authoritative_transcript_path(self, _item: str) -> Path:
            return source_path

        def can_persist_generated_subtitles(self, _actor_id: str) -> bool:
            return False

        def persist_generated_subtitles(self, actor_id: str, item: str, **_kwargs: Any) -> dict:
            calls.append((actor_id, item))
            pytest.fail("save must not run after the ACL preflight denies access")

    monkeypatch.setattr(manager_module, "sermonManager", DenyingManager())
    with pytest.raises(PermissionError, match="permission"):
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

    assert calls == []
    assert captured == {}
    assert not (output_dir / "subtitle-applications").exists()


def test_review_path_is_rejected_before_title_model_when_published_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_path = _source(tmp_path)
    published_dir = tmp_path / "script_published"
    published_dir.mkdir()
    published_path = published_dir / review_path.name
    published_path.write_text(
        json.dumps(
            {"metadata": {"status": "published"}, "script": _rows()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runner,
        "generate_subtitles",
        lambda *_args, **_kwargs: pytest.fail(
            "wrong source must fail before the title model"
        ),
    )

    with pytest.raises(SubtitlePersistenceError, match="not authoritative"):
        runner.run_one(
            review_path,
            output_dir=tmp_path / "out",
            client=object(),
            prompt="prompt",
            reasoning_effort="medium",
            force=False,
            write_back_subtitles=True,
            subtitle_actor_id="editor@example.org",
        )


def test_titled_review_cannot_bypass_same_name_published_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_path = _source(tmp_path, heading=True)
    published_dir = tmp_path / "script_published"
    published_dir.mkdir()
    published_path = published_dir / review_path.name
    published_path.write_text(
        json.dumps(
            {
                "metadata": {"status": "published"},
                "script": [
                    {"index": "published-title", "type": "subtitle", "text": "## Published"},
                    {"index": 1, "text": "published authority"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runner,
        "_run",
        lambda **_kwargs: pytest.fail("review must not reach extraction"),
    )

    with pytest.raises(SubtitlePersistenceError, match="not authoritative"):
        runner.run_one(
            review_path,
            output_dir=tmp_path / "out",
            client=object(),
            prompt="prompt",
            reasoning_effort="medium",
            force=False,
        )


def test_dry_run_cannot_report_review_as_source_when_published_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_path = _source(tmp_path, heading=True)
    published_dir = tmp_path / "script_published"
    published_dir.mkdir()
    (published_dir / review_path.name).write_text(
        json.dumps(
            {
                "metadata": {"status": "published"},
                "script": [
                    {"index": "published-title", "type": "subtitle", "text": "## Published"},
                    {"index": 1, "text": "published authority"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "detailed_knowledge_extraction_runner",
            "--transcript-dir", str(review_path.parent),
            "--ids", review_path.stem,
            "--output-dir", str(tmp_path / "out"),
            "--dry-run",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        runner.main()

    assert exc_info.value.code == 2


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


def test_saved_result_rejects_soft_deleted_body_rewrite_that_projects_equal() -> None:
    before = [
        {"index": "7", "text": "我們~~今天~~看"},
        {"index": "8", "text": "下一段"},
    ]
    after = [
        {"index": "7", "text": "我們\n看"},
        {"index": "8", "text": "下一段"},
        {"index": "new", "type": "subtitle", "text": "## 新標題"},
    ]

    assert body_rows(before) == body_rows(after)
    with pytest.raises(SubtitleBodyMutationError, match="pre-save sermon rows"):
        verify_saved_result(before, after, expected_insertions=1)


def test_saved_result_rejects_deleted_comment_row() -> None:
    before = [
        {"index": "comment-1", "type": "comment", "text": "編輯備註"},
        {"index": "7", "text": "正文"},
    ]
    after = [
        {"index": "7", "text": "正文"},
        {"index": "new", "type": "subtitle", "text": "## 新標題"},
    ]

    with pytest.raises(SubtitleBodyMutationError, match="pre-save sermon rows"):
        verify_saved_result(before, after, expected_insertions=1)


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
        subtitle_authorizer=lambda _actor_id: True,
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
    manager._acl = SimpleNamespace(
        get_user_permissions=lambda user_id: (
            ["read_any_item"] if user_id == "reader@example.org"
            else ["read_any_item", "write_owned_item", "assign_item"]
        )
    )

    assert manager.can_persist_generated_subtitles("reader@example.org") is False
    assert manager.can_persist_generated_subtitles("editor@example.org") is True

    with pytest.raises(PermissionError):
        manager.persist_generated_subtitles(
            "reader@example.org", "S governed",
            expected_source_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
            insertions=_insertions(),
        )
    assert json.loads(source_path.read_text()) == _rows()

    with pytest.raises(SubtitlePersistenceError, match="changed before subtitle write-back"):
        manager.persist_generated_subtitles(
            "editor@example.org", "S governed",
            expected_source_sha256="0" * 64,
            insertions=_insertions(),
        )


def test_editor_script_save_does_not_mutate_separate_metadata_before_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.api.sc_api import sermon_manager as module
    from backend.api.sc_api.sermon_manager import SermonManager

    manager = SermonManager.__new__(SermonManager)
    manager.base_folder = str(tmp_path)
    manager._sm = SimpleNamespace(
        update_sermon_metadata=lambda *_args: pytest.fail(
            "script CAS must not mutate separate metadata"
        )
    )
    manager.get_sermon_permissions = lambda *_args: SimpleNamespace(canWrite=True)

    class FakeScriptDelta:
        def __init__(self, *_args):
            pass

        def save_script(self, *_args, **_kwargs):
            return {"message": "saved", "script_sha256": "b" * 64}

    monkeypatch.setattr(module, "ScriptDelta", FakeScriptDelta)

    saved = manager.update_sermon(
        "editor@example.org",
        "scripts",
        "S governed",
        [],
        expected_script_sha256="a" * 64,
    )

    assert saved["script_sha256"] == "b" * 64


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
    manager._sm = SimpleNamespace(
        update_sermon_metadata=lambda *_args: pytest.fail(
            "subtitle persistence must remain a single-file atomic operation"
        )
    )
    manager._acl = SimpleNamespace(
        get_user_permissions=lambda *_args: [
            "read_any_item", "write_owned_item", "assign_item"
        ]
    )
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


def test_sermon_manager_updates_published_and_never_same_name_review(
    tmp_path: Path,
) -> None:
    from backend.api.sc_api.sermon_manager import SermonManager

    item = "S authoritative"
    review = tmp_path / "script_review"
    published = tmp_path / "script_published"
    review.mkdir()
    published.mkdir()
    review_path = review / f"{item}.json"
    review_path.write_text(
        json.dumps(
            [{"index": "review-title", "type": "subtitle", "text": "## Review 标题"}, *_rows()],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    review_before = review_path.read_bytes()
    published_path = published / f"{item}.json"
    published_before = {
        "metadata": {"status": "published", "title": item},
        "script": _rows(),
    }
    published_path.write_text(
        json.dumps(published_before, ensure_ascii=False), encoding="utf-8"
    )
    manager = SermonManager.__new__(SermonManager)
    manager.base_folder = str(tmp_path)
    manager._acl = SimpleNamespace(
        get_user_permissions=lambda *_args: ["read_any_item", "write_owned_item"]
    )

    report = manager.persist_generated_subtitles(
        "editor@example.org",
        item,
        expected_source_sha256=hashlib.sha256(published_path.read_bytes()).hexdigest(),
        insertions=_insertions(),
    )

    assert review_path.read_bytes() == review_before
    published_after = json.loads(published_path.read_text(encoding="utf-8"))
    assert published_after["metadata"] == published_before["metadata"]
    assert body_rows(published_after["script"]) == _rows()
    assert report["source_path"] == str(published_path)


def test_subtitle_commit_is_not_reported_failed_when_a_later_writer_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.api.sc_api.script_delta import ScriptDelta

    source_path = _source(tmp_path)
    before_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    original_save_rows = ScriptDelta.save_rows

    def save_then_follow_up(
        base_folder, item_name, folder, rows, *, expected_current_sha256=None
    ):
        committed_sha = original_save_rows(
            base_folder,
            item_name,
            folder,
            rows,
            expected_current_sha256=expected_current_sha256,
        )
        original_save_rows(
            base_folder,
            item_name,
            folder,
            [*rows, {"index": "comment-later", "type": "comment", "text": "后续编辑"}],
            expected_current_sha256=committed_sha,
        )
        return committed_sha

    monkeypatch.setattr(ScriptDelta, "save_rows", save_then_follow_up)

    report = write_back_generated_subtitles(
        source_path,
        expected_source_sha256=before_sha,
        insertions=_insertions(),
        actor_id="editor@example.org",
    )

    assert report["after_source_sha256"] != hashlib.sha256(source_path.read_bytes()).hexdigest()
    assert json.loads(source_path.read_text(encoding="utf-8"))[-1]["index"] == "comment-later"


def test_editor_snapshot_compare_and_swap_rejects_overwriting_pipeline_titles(
    tmp_path: Path,
) -> None:
    from backend.api.sc_api.script_delta import ScriptConflictError, ScriptDelta

    folder = tmp_path / "script_review"
    folder.mkdir()
    source_path = folder / "S governed.json"
    source_path.write_text(json.dumps(_rows(), ensure_ascii=False), encoding="utf-8")
    editor_rows, editor_sha = ScriptDelta.read_rows_with_sha(
        str(tmp_path), "S governed", "script_review"
    )
    pipeline_rows = apply_insertions(
        editor_rows,
        _insertions(),
        source_sha256=editor_sha,
        user_id="pipeline@example.org",
    )
    pipeline_sha = ScriptDelta.save_rows(
        str(tmp_path),
        "S governed",
        "script_review",
        pipeline_rows,
        expected_current_sha256=editor_sha,
    )

    with pytest.raises(ScriptConflictError, match="script changed before atomic save"):
        ScriptDelta.save_rows(
            str(tmp_path),
            "S governed",
            "script_review",
            editor_rows,
            expected_current_sha256=editor_sha,
        )

    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == pipeline_sha
    assert json.loads(source_path.read_text(encoding="utf-8")) == pipeline_rows


def test_low_level_script_review_writer_rejects_blind_write(tmp_path: Path) -> None:
    from backend.api.sc_api.script_delta import ScriptDelta

    with pytest.raises(ValueError, match="expected_current_sha256 is required"):
        ScriptDelta.save_rows(
            str(tmp_path),
            "S governed",
            "script_review",
            _rows(),
        )
