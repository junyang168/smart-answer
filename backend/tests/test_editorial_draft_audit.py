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

<!-- provenance: {"attribution":"editor","application_chain_id":"AC-1"} -->
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
                        "application_chains": [
                            {
                                "chain_id": "AC-1",
                                "scripture_context": "太16:1 的處境。",
                                "professor_interpretation_claim_ids": ["CL-1"],
                                "enduring_principle": "不變原則。",
                                "present_context": "今日處境。",
                                "application_and_limits": "應用與限制。",
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

    assert result["status"] == "pass_with_warnings"
    assert any(
        item["code"] == "missing_decision_heading" and item["severity"] == "warning"
        for item in result["findings"]
    )


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

    # Reported, not blocking. This check needs the manifest to name the heading
    # that carries the label, and nothing rebuilds the manifest when an article
    # is restructured -- see MANIFEST_SHAPE_CODES.
    assert result["status"] == "pass_with_warnings"
    assert any(
        item["code"] == "missing_editorial_attribution"
        and item["severity"] == "warning"
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
    assert failed["status"] == "pass_with_warnings"
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


def test_an_application_section_without_chains_is_no_longer_a_failure(tmp_path: Path) -> None:
    """A 生活應用 section used to fail unless every paragraph pointed at a
    registered five-link chain. No article ever registered one, so the rule
    only ever blocked applications the professor had actually made.
    """

    manifest = _fixture(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["drafts"][0]["audit_config"]["application_policy"] = {
        "section": "生活應用",
        "requires_registered_chains": True,
    }
    data["drafts"][0]["audit_config"]["application_chains"] = []
    _write_json(manifest, data)

    result = audit_editorial_draft(manifest, "DRAFT-1")
    codes = {item["code"] for item in result["findings"]}
    assert "unregistered_application_section" not in codes


def test_application_content_no_longer_needs_a_registered_chain(
    tmp_path: Path,
) -> None:
    """Registration is retired. A five-link chain -- scripture context,
    professor's interpretation, enduring principle, present context,
    application and limits -- asked for a structure finer than the source has:
    the professor states an application in a sentence and illustrates it, and
    no chain was ever registered for any article. What registration was for --
    an application must not be invented -- is what the grounding gate does
    already, against the claims the paragraph declares.

    On Matt.16.1-12 the contract required an application while this rule
    forbade writing one, and the run deadlocked with nothing to point at.
    """

    manifest = _fixture(tmp_path)
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(
        draft_path.read_text(encoding="utf-8").replace(
            "## 神學意義\n\n<!-- provenance: {\"attribution\":\"professor\",\"claim_ids\":[\"CL-1\"]} -->\n正文。\n",
            "## 神學意義\n\n"
            "<!-- provenance: {\"attribution\":\"professor\",\"claim_ids\":[\"CL-1\"]} -->\n正文。\n\n"
            "<!-- provenance: {\"attribution\":\"editorial_synthesis\",\"claim_ids\":[\"CL-1\"],"
            "\"synthesis_note\":\"牧養收束。\"} -->\n"
            "讀者今天也應當省察自己的期待，並在困惑中繼續信靠。\n",
        ),
        encoding="utf-8",
    )

    result = audit_editorial_draft(manifest, "DRAFT-1")

    codes = {item["code"] for item in result["findings"]}
    assert "undeclared_application_content" not in codes
    assert "unregistered_application_paragraph" not in codes
    assert "application_chain_not_registered" not in codes
    assert "unregistered_application_section" not in codes


def test_audit_rejects_incomplete_chain_behind_declared_application(
    tmp_path: Path,
) -> None:
    manifest = _fixture(tmp_path)
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    chain = manifest_data["drafts"][0]["audit_config"]["application_chains"][0]
    chain["professor_interpretation_claim_ids"] = ["CL-UNKNOWN"]
    chain["application_and_limits"] = ""
    _write_json(manifest, manifest_data)

    result = audit_editorial_draft(manifest, "DRAFT-1")

    assert result["status"] == "fail"
    codes = {item["code"] for item in result["findings"]}
    assert "incomplete_application_chain" in codes
    assert "application_chain_missing_claim" in codes


def test_audit_accepts_editorial_synthesis_declared_as_non_application(
    tmp_path: Path,
) -> None:
    """Ordinary exegetical synthesis is not treated as application content."""
    manifest = _fixture(tmp_path)
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(
        draft_path.read_text(encoding="utf-8").replace(
            "## 神學意義\n",
            "## 神學意義\n\n"
            "<!-- provenance: {\"attribution\":\"editorial_synthesis\",\"claim_ids\":[\"CL-1\"],"
            "\"synthesis_note\":\"綜合本段論證。\",\"contains_application\":false} -->\n"
            "本段的兩項論證共同指向同一個結論。\n",
        ),
        encoding="utf-8",
    )

    result = audit_editorial_draft(manifest, "DRAFT-1")

    assert result["status"] == "pass"
    assert not any(
        item["code"] == "undeclared_application_content" for item in result["findings"]
    )


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


def test_audit_rejects_reader_facing_internal_source_id(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(
        draft_path.read_text(encoding="utf-8").replace(
            "正文。",
            "王教授在 `S 220206` 與 transcript `220-426-110-1139` 中指出這一點。",
            1,
        ),
        encoding="utf-8",
    )

    result = audit_editorial_draft(manifest, "DRAFT-1")

    assert result["status"] == "fail"
    assert any(
        item["code"] == "reader_facing_internal_source_id"
        for item in result["findings"]
    )


def test_audit_allows_internal_id_only_in_sermon_link_target(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    draft_path = tmp_path / "draft.md"
    draft_path.write_text(
        draft_path.read_text(encoding="utf-8").replace(
            "正文。",
            "王教授在 [2022 年 2 月 6 日達拉斯聖道教會講道](/resources/sermons/S%20220206) 中指出這一點。",
            1,
        ),
        encoding="utf-8",
    )

    result = audit_editorial_draft(manifest, "DRAFT-1")

    assert result["status"] == "pass"


def test_a_footnote_definition_is_apparatus_not_unattributed_prose(tmp_path: Path) -> None:
    """Regression: the audit read `[^1]: ...` lines as a body paragraph, found
    no provenance comment above them, and reported a real article's entire
    footnote block as prose with no source attribution.
    """

    from backend.pipeline.editorial_draft_audit import _markdown_blocks

    blocks = _markdown_blocks(
        "<!-- provenance: {\"attribution\":\"professor\",\"claim_ids\":[\"CL-1\"]} -->\n"
        "這是有來源的正文。\n"
        "\n"
        "[^1]: 希臘文為 Χριστός，意為「受膏者」。\n"
        "[^2]: 「彼得」為 Πέτρος。\n"
    )
    assert [block["text"] for block in blocks] == ["這是有來源的正文。"]


def test_the_blocking_checks_are_the_ones_that_need_no_checklist() -> None:
    """The audit's remaining errors must be provable from the manuscript and
    the knowledge layer alone. Anything that also needs the manifest to still
    describe this version of the article is a warning: a run of a real article
    produced fourteen errors, every one of them the checklist describing the
    previous version, and a gate that cries wolf that often teaches its reader
    to bypass it.
    """

    from backend.pipeline.editorial_draft_audit import MANIFEST_SHAPE_CODES, _finding

    for code in MANIFEST_SHAPE_CODES:
        assert _finding(code, "error", "t", "d")["severity"] == "warning"

    # A paragraph with no provenance at all is still blocking, and is the one
    # thing no other gate covers: the grounding check skips a paragraph that
    # declares no claims, so nothing else would ever look at it.
    assert _finding("unmapped_manuscript_paragraph", "error", "t", "d")["severity"] == "error"
    assert _finding("invalid_source_anchor", "error", "t", "d")["severity"] == "error"
