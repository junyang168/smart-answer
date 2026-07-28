import json
from types import SimpleNamespace

import pytest

from backend.api import series_manuscript_application as application
from backend.api import sermon_converter_service as service


def _configure(monkeypatch, tmp_path):
    project_root = tmp_path / "projects"
    current_root = project_root / "current"
    prior_root = project_root / "prior"
    series_root = tmp_path / "series" / "series-1"
    current_root.mkdir(parents=True)
    prior_root.mkdir(parents=True)
    (current_root / "meta.json").write_text("{}", encoding="utf-8")
    prior_text = "## 既有单元\n\n### 釋經\n\n旧内容。\n"
    (prior_root / "final.md").write_text(prior_text, encoding="utf-8")
    proposal_id = "proposal-1"
    changes = [
        {
            "canonical_unit_id": "CU-OLD",
            "change_type": "updated",
            "target_project_id": "prior",
            "target_final_sha256": application._sha256_text(prior_text),
            "previous_title": "既有单元",
            "unit_title": "既有单元及新增证据",
            "change_summary": "加入新证据。",
            "evidence_ids": ["E1"],
            "markdown": "## 既有单元及新增证据\n\n### 釋經\n\n旧内容与新证据。",
        },
        {
            "canonical_unit_id": "CU-NEW",
            "change_type": "new",
            "target_project_id": None,
            "unit_title": "新正文",
            "change_summary": "新正文。",
            "evidence_ids": ["E2"],
            "markdown": "## 新正文\n\n### 釋經\n\n正文内容。",
        },
        {
            "canonical_unit_id": "CU-APP",
            "change_type": "appendix",
            "target_project_id": None,
            "unit_title": "附录问题",
            "change_summary": "新附录。",
            "evidence_ids": ["E3"],
            "markdown": "## 附录问题\n\n### 附錄\n\n附录内容。",
        },
    ]
    build_path = series_root / "merge_runs" / proposal_id / "build.json"
    build_path.parent.mkdir(parents=True)
    build_path.write_text(
        json.dumps(
            {
                "status": "draft_ready",
                "series_id": "series-1",
                "project_id": "current",
                "proposal_id": proposal_id,
                "integration_changes": changes,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    series_root.mkdir(parents=True, exist_ok=True)
    (series_root / "evidence_registry.json").write_text(
        json.dumps(
            {
                "evidence": [
                    {"evidence_id": "E1", "disposition": "merged_as_extension"},
                    {"evidence_id": "E2", "disposition": "fully_represented"},
                    {"evidence_id": "E3", "disposition": "fully_represented"},
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(application, "get_series_manuscript_dir", lambda series_id: series_root)
    monkeypatch.setattr(application, "get_sermon_draft_path", lambda project_id: project_root / project_id / "draft_v1.md")
    monkeypatch.setattr(application, "get_sermon_final_path", lambda project_id: project_root / project_id / "final.md")
    monkeypatch.setattr(
        application,
        "get_sermon_project_metadata",
        lambda project_id: SimpleNamespace(project_type="transcript", series_id="series-1"),
    )
    monkeypatch.setattr(
        application,
        "save_sermon_draft",
        lambda project_id, content: (project_root / project_id / "draft_v1.md").write_text(content, encoding="utf-8"),
    )
    monkeypatch.setattr(application, "update_transcript_coverage_audit_state", lambda *args, **kwargs: True)
    monkeypatch.setattr(application, "reset_theological_audit_state", lambda *args, **kwargs: True)
    return project_root, series_root, prior_text


def test_materialize_writes_only_local_units_and_review_patches(monkeypatch, tmp_path):
    project_root, series_root, prior_text = _configure(monkeypatch, tmp_path)

    status = application.materialize_integrated_manuscript("series-1", "current", "proposal-1")

    draft = (project_root / "current" / "draft_v1.md").read_text(encoding="utf-8")
    assert "## 新正文" in draft
    assert "## 附录问题" in draft
    assert "既有单元及新增证据" not in draft
    assert (project_root / "prior" / "final.md").read_text(encoding="utf-8") == prior_text
    assert status.status == "draft_generated_pending_patch_review"
    assert status.local_unit_count == 2
    assert status.pending_patch_count == 1
    assert status.evidence_count == 3
    coverage = json.loads((project_root / "current" / "coverage_audit.json").read_text(encoding="utf-8"))
    assert coverage["payload"]["overall_status"] == "pass"
    assert coverage["payload"]["audit_kind"] == "integration_coverage_check"
    assert coverage["payload"]["evidence_count"] == 3
    patch_files = list((series_root / "applications" / status.application_id / "patches").glob("*.md"))
    assert len(patch_files) == 1
    assert "新增证据" in patch_files[0].read_text(encoding="utf-8")


def test_materialize_refuses_to_overwrite_human_draft(monkeypatch, tmp_path):
    project_root, _, _ = _configure(monkeypatch, tmp_path)
    (project_root / "current" / "draft_v1.md").write_text("人工编辑", encoding="utf-8")

    with pytest.raises(ValueError, match="human edits"):
        application.materialize_integrated_manuscript("series-1", "current", "proposal-1")


def test_apply_safe_patches_updates_target_draft_but_not_final(monkeypatch, tmp_path):
    project_root, _, prior_text = _configure(monkeypatch, tmp_path)
    (project_root / "prior" / "draft_v1.md").write_text(prior_text, encoding="utf-8")
    status = application.materialize_integrated_manuscript("series-1", "current", "proposal-1")

    applied = application.apply_safe_integration_patches(
        "series-1", "current", status.application_id
    )

    assert applied.applied_patch_count == 1
    assert applied.conflict_patch_count == 0
    assert "既有单元及新增证据" in (project_root / "prior" / "draft_v1.md").read_text(encoding="utf-8")
    assert (project_root / "prior" / "final.md").read_text(encoding="utf-8") == prior_text
    assert json.loads((project_root / "prior" / "meta.json").read_text(encoding="utf-8"))["audit_passed"] is False


def test_apply_safe_patch_certifies_transcript_target_and_marks_review_stale(monkeypatch, tmp_path):
    project_root, _, prior_text = _configure(monkeypatch, tmp_path)
    (project_root / "prior" / "meta.json").write_text(
        json.dumps({"project_type": "transcript"}), encoding="utf-8"
    )
    (project_root / "prior" / "draft_v1.md").write_text(prior_text, encoding="utf-8")
    status = application.materialize_integrated_manuscript("series-1", "current", "proposal-1")

    application.apply_safe_integration_patches(
        "series-1", "current", status.application_id
    )

    coverage = json.loads(
        (project_root / "prior" / "coverage_audit.json").read_text(encoding="utf-8")
    )
    meta = json.loads((project_root / "prior" / "meta.json").read_text(encoding="utf-8"))
    assert coverage["payload"]["overall_status"] == "pass"
    assert coverage["payload"]["audit_kind"] == "integration_patch_coverage_check"
    assert coverage["payload"]["evidence_ids"] == ["E1"]
    assert meta["theological_review_stale"] is True


def test_restart_theological_review_replaces_old_review_copy(monkeypatch, tmp_path):
    project_id = "review-target"
    project_root = tmp_path / project_id
    project_root.mkdir()
    monkeypatch.setattr(service, "NOTES_TO_SERMON_DIR", tmp_path)
    (project_root / "meta.json").write_text(
        json.dumps(
            {
                "project_type": "transcript",
                "theological_review_stale": True,
                "theological_audit_completed": False,
            }
        ),
        encoding="utf-8",
    )
    (project_root / "draft_v1.md").write_text(
        "## 更新单元\n\n### 釋經\n\n新内容。\n", encoding="utf-8"
    )
    (project_root / "final.md").write_text(
        "## 旧单元\n\n### 釋經\n\n旧内容。\n", encoding="utf-8"
    )
    (project_root / "chunks_meta.json").write_text("[]", encoding="utf-8")
    chunks_dir = project_root / "chunks"
    chunks_dir.mkdir()
    (chunks_dir / "old.md").write_text("旧内容。", encoding="utf-8")

    assert service.start_theological_review(project_id) is True

    assert (project_root / "final.md").read_text(encoding="utf-8") == (
        project_root / "draft_v1.md"
    ).read_text(encoding="utf-8")
    refreshed_meta = json.loads((project_root / "meta.json").read_text(encoding="utf-8"))
    assert refreshed_meta["theological_review_stale"] is False
    assert refreshed_meta["theological_audit_completed"] is False
    assert not (chunks_dir / "old.md").exists()
    assert list(chunks_dir.glob("*.md"))


def test_apply_marks_edited_target_unit_as_conflict(monkeypatch, tmp_path):
    project_root, _, prior_text = _configure(monkeypatch, tmp_path)
    edited = prior_text.replace("旧内容。", "人工修改后的内容。")
    (project_root / "prior" / "draft_v1.md").write_text(edited, encoding="utf-8")
    status = application.materialize_integrated_manuscript("series-1", "current", "proposal-1")

    result = application.apply_safe_integration_patches(
        "series-1", "current", status.application_id
    )

    assert result.applied_patch_count == 0
    assert result.conflict_patch_count == 1
    assert (project_root / "prior" / "draft_v1.md").read_text(encoding="utf-8") == edited
