from __future__ import annotations

import json
from pathlib import Path

from backend.pipeline.editorial_draft_audit import audit_editorial_draft


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _fixture(
    tmp_path: Path,
    *,
    anchor_state: str = "source_version_bound",
    editorial_boundary: bool = False,
    include_editor_label: bool = False,
    material_dispositions: list[dict] | None = None,
) -> Path:
    paragraph = (
        '<!-- provenance: {"attribution":"editor"} -->\n> **編輯說明：** 本段是編輯補充。'
        if include_editor_label
        else '<!-- provenance: {"attribution":"professor","claim_ids":["CL-1"]} -->\n正文。'
    )
    draft_markdown = """# 初稿

## 經文與問題

<!-- provenance: {"attribution":"editor"} -->
> **編輯導讀：** 本篇處理甚麼問題。

## 釋經

### 第一段

__TEST_PARAGRAPH__

## 神學意義

<!-- provenance: {"attribution":"professor","claim_ids":["CL-1"]} -->
正文。

## 生活應用

<!-- provenance: {"attribution":"editor"} -->
> **編輯說明：** 正文。

## 附錄

<!-- provenance: {"attribution":"editor"} -->
> **編輯說明：** 正文。
""".replace("__TEST_PARAGRAPH__", paragraph)
    (tmp_path / "draft.md").write_text(
        draft_markdown,
        encoding="utf-8",
    )
    _write_json(
        tmp_path / "snapshot.json",
        {
            "product_plans": [
                {
                    "plan_id": "CP-1",
                    "decisions": [
                        {
                            "decision_id": "CD-1",
                            "passage": "太16:1",
                            "section_title": "第一段",
                            "claim_ids": ["CL-1"],
                        }
                    ],
                }
            ],
            "claims": [
                {
                    "claim_id": "CL-1",
                    # Context is allowed when the same claim also has an
                    # explicitly eligible supporting step.
                    "evidence_step_ids": ["E-1", "E-CONTEXT"],
                }
            ],
            "evidence_steps": [
                {
                    "evidence_step_id": "E-1",
                    "support_eligibility": "eligible_candidate",
                    "source_fragment_ids": ["F-1"],
                },
                {
                    "evidence_step_id": "E-CONTEXT",
                    "support_eligibility": "context_only",
                    "source_fragment_ids": ["F-2"],
                },
            ],
            "source_fragments": [
                {"fragment_id": "F-1", "anchor_state": anchor_state},
                {"fragment_id": "F-2", "anchor_state": "source_version_bound"},
            ],
        },
    )
    manifest = tmp_path / "editorial-draft-manifest.json"
    _write_json(
        manifest,
        {
            "drafts": [
                {
                    "draft_id": "DRAFT-1",
                    "candidate_id": "CP-1",
                    "publication_profile_id": "PP-matthew-expository-teaching-v1",
                    "relative_path": "draft.md",
                    "audit_config": {
                        "plan_id": "CP-1",
                        "knowledge_snapshot_path": "snapshot.json",
                        "decision_sections": [
                            {
                                "decision_id": "CD-1",
                                "markdown_heading": "第一段",
                                **(
                                    {
                                        "editorial_boundary": {
                                            "required": True,
                                            "label": "編輯說明",
                                            "reason": "測試用編輯歸屬要求。",
                                        }
                                    }
                                    if editorial_boundary
                                    else {}
                                ),
                            }
                        ],
                        "material_dispositions": material_dispositions or [],
                    },
                }
            ]
        },
    )
    return manifest


def test_audit_accepts_context_when_claim_has_eligible_support(tmp_path: Path) -> None:
    result = audit_editorial_draft(_fixture(tmp_path), "DRAFT-1")

    assert result["status"] == "pass"
    assert result["summary"]["decision_headings_found"] == 1
    assert result["summary"]["valid_source_fragment_total"] == 2


def test_audit_accepts_explicit_coverage_gap_without_claims(tmp_path: Path) -> None:
    manifest = _fixture(
        tmp_path,
        editorial_boundary=True,
        include_editor_label=True,
    )
    snapshot_path = tmp_path / "snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    decision = snapshot["product_plans"][0]["decisions"][0]
    decision.update(
        {
            "action": "coverage_gap",
            "coverage": "missing",
            "claim_ids": [],
        }
    )
    _write_json(snapshot_path, snapshot)

    result = audit_editorial_draft(manifest, "DRAFT-1")

    assert result["status"] == "pass"
    assert not any(
        item["code"] == "decision_without_claims" for item in result["findings"]
    )


def test_audit_rejects_stale_source_anchor(tmp_path: Path) -> None:
    result = audit_editorial_draft(
        _fixture(tmp_path, anchor_state="stale_paragraph"), "DRAFT-1"
    )

    assert result["status"] == "fail"
    assert any(item["code"] == "invalid_source_anchor" for item in result["findings"])


def test_audit_rejects_missing_composition_heading(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    (tmp_path / "draft.md").write_text(
        (tmp_path / "draft.md").read_text(encoding="utf-8").replace("### 第一段", "### 另一段"),
        encoding="utf-8",
    )

    result = audit_editorial_draft(manifest, "DRAFT-1")

    assert result["status"] == "fail"
    assert any(item["code"] == "missing_decision_heading" for item in result["findings"])


def test_audit_rejects_missing_scripture_and_question_section(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(
        draft_path.read_text(encoding="utf-8").replace(
            "## 經文與問題\n\n<!-- provenance: {\"attribution\":\"editor\"} -->\n> **編輯導讀：** 本篇處理甚麼問題。\n\n",
            "",
        ),
        encoding="utf-8",
    )

    result = audit_editorial_draft(manifest, "DRAFT-1")

    assert result["status"] == "fail"
    assert any(
        item["code"] == "missing_required_section"
        and "經文與問題" in item["title"]
        for item in result["findings"]
    )


def test_audit_rejects_unlabelled_editorial_inference(tmp_path: Path) -> None:
    result = audit_editorial_draft(
        _fixture(tmp_path, editorial_boundary=True), "DRAFT-1"
    )

    assert result["status"] == "fail"
    assert any(
        item["code"] == "missing_editorial_attribution"
        for item in result["findings"]
    )


def test_audit_accepts_explicitly_labelled_editorial_inference(tmp_path: Path) -> None:
    result = audit_editorial_draft(
        _fixture(
            tmp_path,
            editorial_boundary=True,
            include_editor_label=True,
        ),
        "DRAFT-1",
    )

    assert result["status"] == "pass"


def test_audit_requires_declared_scripture_quotation(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["drafts"][0]["audit_config"]["required_scripture_quotations"] = [
        {
            "markdown_heading": "第一段",
            "required_markers": ["直接引入的經文"],
        }
    ]
    _write_json(manifest, manifest_data)

    failed = audit_editorial_draft(manifest, "DRAFT-1")
    assert failed["status"] == "fail"
    assert any(
        item["code"] == "missing_scripture_quotation"
        for item in failed["findings"]
    )

    draft_path = tmp_path / "draft.md"
    draft_path.write_text(
        draft_path.read_text(encoding="utf-8").replace(
            "### 第一段\n",
            "### 第一段\n\n<!-- provenance: {\"attribution\":\"scripture\",\"scripture_refs\":[\"Matt.16.1\"]} -->\n> 直接引入的經文。\n",
        ),
        encoding="utf-8",
    )
    passed = audit_editorial_draft(manifest, "DRAFT-1")
    assert passed["status"] == "pass"


def test_audit_rejects_unregistered_optional_application_section(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["drafts"][0]["audit_config"]["application_policy"] = {
        "section": "生活應用",
        "requires_registered_chains": True,
    }
    manifest_data["drafts"][0]["audit_config"]["application_chains"] = []
    _write_json(manifest, manifest_data)

    failed = audit_editorial_draft(manifest, "DRAFT-1")
    assert failed["status"] == "fail"
    assert any(
        item["code"] == "unregistered_application_section"
        for item in failed["findings"]
    )

    draft_path = tmp_path / "draft.md"
    draft_path.write_text(
        draft_path.read_text(encoding="utf-8").replace(
            "## 生活應用\n\n<!-- provenance: {\"attribution\":\"editor\"} -->\n> **編輯說明：** 正文。\n\n",
            "",
        ),
        encoding="utf-8",
    )
    passed = audit_editorial_draft(manifest, "DRAFT-1")
    assert passed["status"] == "pass"


def test_audit_preserves_source_only_material_for_human_verification(tmp_path: Path) -> None:
    result = audit_editorial_draft(
        _fixture(
            tmp_path,
            material_dispositions=[
                {
                    "disposition_id": "MD-1",
                    "title": "歷史學家的工作方式",
                    "claim_ids": ["CL-1"],
                    "action": "source_only",
                    "article_inclusion": False,
                    "review_status": "requires_human_verification",
                }
            ],
        ),
        "DRAFT-1",
    )

    assert result["status"] == "pass"
    assert result["summary"]["material_disposition_total"] == 1
    assert result["summary"]["source_only_pending_human_total"] == 1
    assert result["material_dispositions"][0]["title"] == "歷史學家的工作方式"


def test_audit_rejects_source_only_material_not_sent_for_human_verification(
    tmp_path: Path,
) -> None:
    result = audit_editorial_draft(
        _fixture(
            tmp_path,
            material_dispositions=[
                {
                    "disposition_id": "MD-1",
                    "title": "歷史學家的工作方式",
                    "claim_ids": ["CL-1"],
                    "action": "source_only",
                    "article_inclusion": False,
                    "review_status": "candidate",
                }
            ],
        ),
        "DRAFT-1",
    )

    assert result["status"] == "fail"
    assert any(
        item["code"] == "source_only_without_human_verification"
        for item in result["findings"]
    )


def test_audit_accepts_source_only_material_in_staged_knowledge_record(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "staged-record.json",
        {
            "claims": [
                {"claim_id": "CL-STAGED", "evidence_step_ids": ["E-STAGED"]}
            ],
            "evidence_steps": [
                {
                    "evidence_step_id": "E-STAGED",
                    "source_fragment_ids": ["F-STAGED"],
                }
            ],
            "source_fragments": [
                {
                    "fragment_id": "F-STAGED",
                    "anchor_state": "source_version_bound",
                }
            ],
        },
    )
    result = audit_editorial_draft(
        _fixture(
            tmp_path,
            material_dispositions=[
                {
                    "disposition_id": "MD-STAGED",
                    "title": "歷史學家的工作方式",
                    "claim_ids": ["CL-STAGED"],
                    "knowledge_record_path": "staged-record.json",
                    "action": "source_only",
                    "article_inclusion": False,
                    "review_status": "requires_human_verification",
                }
            ],
        ),
        "DRAFT-1",
    )

    assert result["status"] == "pass"
    assert result["material_dispositions"][0]["record_state"] == (
        "staged_for_human_verification"
    )
    assert result["material_dispositions"][0]["evidence_step_count"] == 1
    assert result["material_dispositions"][0]["source_fragment_count"] == 1


def test_audit_rejects_unmapped_manuscript_paragraph(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(
        draft_path.read_text(encoding="utf-8").replace(
            '<!-- provenance: {"attribution":"professor","claim_ids":["CL-1"]} -->\n正文。',
            "正文。",
            1,
        ),
        encoding="utf-8",
    )

    result = audit_editorial_draft(manifest, "DRAFT-1")

    assert result["status"] == "fail"
    assert any(
        item["code"] == "unmapped_manuscript_paragraph"
        for item in result["findings"]
    )


def test_audit_rejects_unknown_claim_in_paragraph_provenance(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(
        draft_path.read_text(encoding="utf-8").replace(
            '"claim_ids":["CL-1"]',
            '"claim_ids":["CL-MISSING"]',
            1,
        ),
        encoding="utf-8",
    )

    result = audit_editorial_draft(manifest, "DRAFT-1")

    assert result["status"] == "fail"
    assert any(
        item["code"] == "paragraph_provenance_unknown_claim"
        for item in result["findings"]
    )


def test_audit_rejects_manifest_owned_publication_structure(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["drafts"][0]["audit_config"]["required_top_level_sections"] = [
        "釋經"
    ]
    _write_json(manifest, manifest_data)

    result = audit_editorial_draft(manifest, "DRAFT-1")

    assert result["status"] == "fail"
    assert any(
        item["code"] == "manifest_publication_structure_override"
        for item in result["findings"]
    )
