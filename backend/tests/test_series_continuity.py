import json
from types import SimpleNamespace

import pytest

from backend.api import series_manuscript_service as service


def _write_artifact(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"source_sha256": "source", "payload": payload}, ensure_ascii=False),
        encoding="utf-8",
    )


def _configure_series(monkeypatch, tmp_path):
    project_root = tmp_path / "notes_to_surmon"
    series_root = tmp_path / "series_manuscripts"
    projects = {
        "third": SimpleNamespace(
            id="third",
            title="A title that does not identify the passage",
            series_id="series-1",
            project_type="transcript",
        ),
        "fourth": SimpleNamespace(
            id="fourth",
            title="Another editorial title",
            series_id="series-1",
            project_type="transcript",
        ),
    }
    series = SimpleNamespace(
        id="series-1",
        lectures=[SimpleNamespace(project_ids=["third", "fourth"])],
    )
    monkeypatch.setattr(service, "NOTES_TO_SERMON_DIR", project_root)
    monkeypatch.setattr(service, "SERIES_MANUSCRIPTS_DIR", series_root)
    monkeypatch.setattr(service, "get_series", lambda series_id: series if series_id == "series-1" else None)
    monkeypatch.setattr(service, "get_sermon_project_metadata", projects.get)
    monkeypatch.setattr(
        service,
        "get_sermon_final_path",
        lambda project_id: project_root / project_id / "final.md",
    )
    return project_root, series_root


def test_context_uses_content_and_scripture_not_project_titles(monkeypatch, tmp_path):
    project_root, _ = _configure_series(monkeypatch, tmp_path)
    prior = project_root / "third" / "final.md"
    prior.parent.mkdir(parents=True)
    prior.write_text(
        "## 登山變像\n\n詩篇2篇與以賽亞書42章共同說明耶穌是君王和受苦的僕人。",
        encoding="utf-8",
    )
    _write_artifact(
        project_root / "fourth" / "evidence_inventory.json",
        {
            "evidence": [
                {
                    "evidence_id": "E001",
                    "content": "使徒行傳13:33補充詩篇2篇，說明耶穌復活與高升。",
                    "scripture_refs": ["使徒行傳13:33", "詩篇2篇"],
                }
            ]
        },
    )

    context = service.build_continuity_context("series-1", "fourth")

    assert context["prior_projects"][0]["project_id"] == "third"
    assert context["prior_candidates"]
    assert "詩篇2篇" in context["prior_candidates"][0]["text"]


def test_continuity_proposal_is_validated_and_saved(monkeypatch, tmp_path):
    project_root, series_root = _configure_series(monkeypatch, tmp_path)
    prior = project_root / "third" / "final.md"
    prior.parent.mkdir(parents=True)
    prior.write_text("## 登山變像\n\n詩篇2篇說明君王身分。", encoding="utf-8")
    _write_artifact(
        project_root / "fourth" / "evidence_inventory.json",
        {
            "evidence": [
                {
                    "evidence_id": "E001",
                    "content": "詩篇2篇說明君王身分。",
                    "scripture_refs": ["詩篇2篇"],
                },
                {
                    "evidence_id": "E002",
                    "content": "使徒行傳13:33把宣告與復活高升相連。",
                    "scripture_refs": ["使徒行傳13:33"],
                },
            ]
        },
    )

    class FakeLLM:
        def generate_json(self, system_prompt, user_prompt, json_schema, timeout_seconds):
            assert "不可根據 Project 標題" in system_prompt
            candidate_id = service.build_continuity_context(
                "series-1", "fourth"
            )["prior_candidates"][0]["section_id"]
            decision_properties = json_schema["schema"]["properties"]["decisions"]["items"]["properties"]
            assert decision_properties["current_evidence_ids"]["items"]["enum"] == ["E001", "E002"]
            assert candidate_id in decision_properties["matched_prior_section_ids"]["items"]["enum"]
            return {
                "summary": "一項重複，一項新增證據。",
                "decisions": [
                    {
                        "current_evidence_ids": ["E001"],
                        "relationship": "duplicate",
                        "matched_prior_section_ids": [candidate_id],
                        "new_contribution": "無新增內容",
                        "recommended_action": "omit_exact_duplicate",
                        "reason": "既有段落已表達相同結論與證據。",
                        "confidence": "high",
                    },
                    {
                        "current_evidence_ids": ["E002"],
                        "relationship": "extension",
                        "matched_prior_section_ids": [candidate_id],
                        "new_contribution": "增加使徒行傳13:33。",
                        "recommended_action": "merge_into_existing",
                        "reason": "相同主題增加新的交叉經文。",
                        "confidence": "high",
                    },
                ],
                "unassigned_evidence_ids": [],
            }

    result = service.analyze_series_continuity("series-1", "fourth", llm=FakeLLM())

    assert result["status"] == "proposed"
    assert result["model"] == "gpt-5.6-sol"
    assert result["decisions"][0]["matched_prior_units"] == [
        {
            "project_id": "third",
            "unit_title": "登山變像",
            "section_id": result["decisions"][0]["matched_prior_section_ids"][0],
        }
    ]
    assert (series_root / "series-1" / "manifest.json").exists()
    assert (
        series_root
        / "series-1"
        / "merge_runs"
        / result["proposal_id"]
        / "proposal.json"
    ).exists()


def test_invalid_first_assignment_is_repaired_once(monkeypatch, tmp_path):
    project_root, _ = _configure_series(monkeypatch, tmp_path)
    prior = project_root / "third" / "final.md"
    prior.parent.mkdir(parents=True)
    prior.write_text("## 登山變像\n\n詩篇2篇說明君王身分。", encoding="utf-8")
    _write_artifact(
        project_root / "fourth" / "evidence_inventory.json",
        {
            "evidence": [
                {"evidence_id": "E001", "content": "詩篇2篇說明君王。", "scripture_refs": ["詩篇2篇"]},
                {"evidence_id": "E002", "content": "新增復活證據。", "scripture_refs": []},
            ]
        },
    )

    class RepairingLLM:
        calls = 0

        def generate_json(self, system_prompt, user_prompt, json_schema, timeout_seconds):
            self.calls += 1
            candidate_id = json_schema["schema"]["properties"]["decisions"]["items"]["properties"][
                "matched_prior_section_ids"
            ]["items"]["enum"][0]
            ids = ["E001"] if self.calls == 1 else ["E001", "E002"]
            return {
                "summary": "修复分配。",
                "decisions": [
                    {
                        "current_evidence_ids": ids,
                        "relationship": "extension",
                        "matched_prior_section_ids": [candidate_id],
                        "new_contribution": "新增证据。",
                        "recommended_action": "merge_into_existing",
                        "reason": "同一主题有新增内容。",
                        "confidence": "high",
                    }
                ],
                "unassigned_evidence_ids": [],
            }

    fake = RepairingLLM()
    result = service.analyze_series_continuity("series-1", "fourth", llm=fake)

    assert fake.calls == 2
    assert result["decisions"][0]["current_evidence_ids"] == ["E001", "E002"]


def test_proposal_rejects_missing_or_duplicate_evidence():
    with pytest.raises(ValueError, match="duplicates"):
        service._validate_proposal(
            {
                "decisions": [
                    {"current_evidence_ids": ["E001"], "matched_prior_section_ids": []},
                    {"current_evidence_ids": ["E001"], "matched_prior_section_ids": []},
                ],
                "unassigned_evidence_ids": [],
            },
            {"E001", "E002"},
            set(),
        )


def test_prior_unit_titles_are_deduplicated_for_multiple_matched_sections():
    proposal = {
        "decisions": [
            {
                "matched_prior_section_ids": ["PS-1", "PS-2", "PS-3"],
            }
        ]
    }
    service._enrich_matched_prior_units(
        proposal,
        [
            {"section_id": "PS-1", "project_id": "p1", "heading_path": ["同一单元", "释经"]},
            {"section_id": "PS-2", "project_id": "p1", "heading_path": ["同一单元", "神学意义"]},
            {"section_id": "PS-3", "project_id": "p2", "heading_path": ["另一单元", "附录"]},
        ],
    )

    assert proposal["decisions"][0]["matched_prior_units"] == [
        {"project_id": "p1", "unit_title": "同一单元", "section_id": "PS-1"},
        {"project_id": "p2", "unit_title": "另一单元", "section_id": "PS-3"},
    ]


def test_project_must_follow_an_earlier_checked_in_manuscript(monkeypatch, tmp_path):
    project_root, _ = _configure_series(monkeypatch, tmp_path)
    _write_artifact(
        project_root / "fourth" / "evidence_inventory.json",
        {
            "evidence": [
                {"evidence_id": "E001", "content": "新內容", "scripture_refs": []}
            ]
        },
    )

    with pytest.raises(ValueError, match="No earlier checked-in"):
        service.build_continuity_context("series-1", "fourth")
