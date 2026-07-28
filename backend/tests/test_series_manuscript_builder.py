import json

import pytest

from backend.api import series_manuscript_builder as builder


def _write_artifact(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"source_sha256": "source", "payload": payload}, ensure_ascii=False),
        encoding="utf-8",
    )


def _proposal(prior_text):
    evidence = [
        {"evidence_id": "E001", "content": "既有论点的重复。", "source_ranges": [{"start_line": 1, "end_line": 1}]},
        {"evidence_id": "E002", "content": "同一论点的新经文证据。", "source_ranges": [{"start_line": 2, "end_line": 2}]},
        {"evidence_id": "E003", "content": "新的释经内容。", "source_ranges": [{"start_line": 3, "end_line": 3}]},
        {"evidence_id": "E004", "content": "离题但有价值的问答。", "source_ranges": [{"start_line": 4, "end_line": 4}]},
    ]
    return {
        "proposal_id": "proposal-1",
        "project_id": "fourth",
        "status": "proposed",
        "current_evidence": evidence,
        "source_snapshot": {
            "prior_projects": [
                {
                    "project_id": "third",
                    "content_sha256": builder._sha256_text(prior_text),
                }
            ],
            "current_evidence_sha256": builder._sha256_text(
                json.dumps(evidence, ensure_ascii=False, sort_keys=True)
            ),
        },
        "decisions": [
            {
                "current_evidence_ids": ["E001"],
                "recommended_action": "omit_exact_duplicate",
                "reason": "已完整表达。",
                "matched_prior_units": [{"project_id": "third", "unit_title": "登山变像"}],
            },
            {
                "current_evidence_ids": ["E002"],
                "recommended_action": "merge_into_existing",
                "reason": "增加证据。",
                "matched_prior_units": [{"project_id": "third", "unit_title": "登山变像"}],
            },
            {
                "current_evidence_ids": ["E003"],
                "recommended_action": "create_new_unit",
                "reason": "新主题。",
                "matched_prior_units": [],
            },
            {
                "current_evidence_ids": ["E004"],
                "recommended_action": "move_to_appendix",
                "reason": "保留为附录。",
                "matched_prior_units": [],
            },
        ],
    }


def _configure(monkeypatch, tmp_path):
    project_root = tmp_path / "notes_to_sermon"
    series_root = tmp_path / "series_manuscripts" / "series-1"
    prior_text = (
        "# 马太福音\n\n"
        "## 登山变像\n\n### 釋經\n\n既有释经内容。\n\n"
        "### 神學意義\n\n既有神学内容。\n\n"
        "## 另一个单元\n\n### 釋經\n\n完全不应改变。\n"
    )
    prior_path = project_root / "third" / "final.md"
    prior_path.parent.mkdir(parents=True)
    prior_path.write_text(prior_text, encoding="utf-8")
    proposal = _proposal(prior_text)
    _write_artifact(
        project_root / "fourth" / "evidence_inventory.json",
        {"evidence": proposal["current_evidence"]},
    )
    source_path = project_root / "fourth" / "unified_source.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("重复。\n新增证据。\n新释经。\n延伸问答。\n", encoding="utf-8")
    monkeypatch.setattr(builder, "NOTES_TO_SERMON_DIR", project_root)
    monkeypatch.setattr(builder, "get_series_manuscript_dir", lambda series_id: series_root)
    monkeypatch.setattr(builder, "get_sermon_final_path", lambda project_id: project_root / project_id / "final.md")
    monkeypatch.setattr(builder, "get_latest_proposal", lambda series_id, project_id: proposal)
    return project_root, series_root, proposal, prior_text


class FakeLLM:
    def generate_json(self, system_prompt, user_prompt, json_schema, timeout_seconds):
        evidence_ids = json_schema["schema"]["properties"]["covered_new_evidence_ids"]["items"]["enum"]
        if "merge_existing" in user_prompt:
            return {
                "unit_title": "登山变像及其新证据",
                "manuscript_sections": {
                    "exegesis": "既有释经内容。新增的交叉经文证据也支持此结论。",
                    "theological_significance": "既有神学内容。",
                    "application": None,
                    "appendix": None,
                },
                "covered_new_evidence_ids": evidence_ids,
                "change_summary": "加入新的经文证据。",
            }
        if "create_appendix" in user_prompt:
            return {
                "unit_title": "附录：课堂延伸问答",
                "manuscript_sections": {"exegesis": None, "theological_significance": None, "application": None, "appendix": "有价值的延伸说明。"},
                "covered_new_evidence_ids": evidence_ids,
                "change_summary": "保留延伸问答。",
            }
        return {
            "unit_title": "新的释经单元",
            "manuscript_sections": {"exegesis": "新的释经内容。", "theological_significance": None, "application": None, "appendix": None},
            "covered_new_evidence_ids": evidence_ids,
            "change_summary": "建立新单元。",
        }


def test_split_canonical_units_keeps_subsections_inside_unit():
    units = builder._split_canonical_units(
        "p1",
        "# 标题\n\n## 第一单元\n\n### 釋經\n\n正文\n\n### 神學意義\n\n意义\n\n## 第二单元\n\n正文二",
    )

    assert [item["title"] for item in units] == ["第一单元", "第二单元"]
    assert "### 神學意義" in units[0]["markdown"]
    assert "第二单元" not in units[0]["markdown"]


def test_new_decisions_with_shared_scripture_and_related_qa_become_one_unit():
    proposal = {
        "current_evidence": [
            {"evidence_id": "E1", "scripture_refs": ["馬太福音 17:20-21"]},
            {"evidence_id": "E2", "scripture_refs": ["馬太福音 17:20-21"]},
            {"evidence_id": "E3", "scripture_refs": []},
        ],
        "decisions": [
            {"current_evidence_ids": ["E1"], "relationship": "new", "recommended_action": "create_new_unit"},
            {"current_evidence_ids": ["E2"], "relationship": "new", "recommended_action": "create_new_unit"},
            {"current_evidence_ids": ["E3"], "relationship": "related_qa", "recommended_action": "create_new_unit"},
        ],
    }

    operations, _ = builder._build_operations(proposal, [])

    assert len(operations) == 1
    assert operations[0]["evidence_ids"] == ["E1", "E2", "E3"]
    assert len(operations[0]["decisions"]) == 3


def test_build_series_draft_accounts_for_all_evidence_without_overwriting_projects(monkeypatch, tmp_path):
    project_root, series_root, _, prior_text = _configure(monkeypatch, tmp_path)

    result = builder.build_series_draft("series-1", "fourth", "proposal-1", llm=FakeLLM())

    assert result["changed_unit_count"] == 1
    assert result["new_unit_count"] == 2
    assert result["evidence_count"] == 4
    assert len(result["integration_changes"]) == 3
    assert sorted(item["change_type"] for item in result["integration_changes"]) == ["appendix", "new", "updated"]
    assert (project_root / "third" / "final.md").read_text(encoding="utf-8") == prior_text
    assert not (project_root / "fourth" / "final.md").exists()
    draft = (series_root / "draft.md").read_text(encoding="utf-8")
    assert "## 登山变像及其新证据" in draft
    assert "## 另一个单元" in draft
    assert "完全不应改变" in draft
    assert "## 新的释经单元" in draft
    assert "## 附录：课堂延伸问答" in draft
    registry = json.loads((series_root / "evidence_registry.json").read_text(encoding="utf-8"))
    assert sorted(item["evidence_id"] for item in registry["evidence"]) == ["E001", "E002", "E003", "E004"]


def test_build_rejects_stale_prior_manuscript(monkeypatch, tmp_path):
    project_root, _, _, _ = _configure(monkeypatch, tmp_path)
    (project_root / "third" / "final.md").write_text("changed", encoding="utf-8")

    with pytest.raises(ValueError, match="changed after proposal review"):
        builder.build_series_draft("series-1", "fourth", "proposal-1", llm=FakeLLM())


def test_build_rejects_changed_evidence_inventory(monkeypatch, tmp_path):
    project_root, _, _, _ = _configure(monkeypatch, tmp_path)
    _write_artifact(
        project_root / "fourth" / "evidence_inventory.json",
        {"evidence": [{"evidence_id": "E999", "content": "changed"}]},
    )

    with pytest.raises(ValueError, match="changed after proposal review"):
        builder.build_series_draft("series-1", "fourth", "proposal-1", llm=FakeLLM())
