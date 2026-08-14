from __future__ import annotations

import json
from pathlib import Path

from backend.pipeline.editorial_draft_audit import audit_editorial_draft


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _fixture(tmp_path: Path, *, anchor_state: str = "source_version_bound") -> Path:
    (tmp_path / "draft.md").write_text(
        """# 初稿

## 釋經

### 第一段

正文。

## 神學意義

正文。

## 生活應用

正文。

## 附錄

正文。
""",
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
                    "relative_path": "draft.md",
                    "audit_config": {
                        "plan_id": "CP-1",
                        "knowledge_snapshot_path": "snapshot.json",
                        "required_top_level_sections": ["釋經", "神學意義", "生活應用", "附錄"],
                        "decision_sections": [
                            {"decision_id": "CD-1", "markdown_heading": "第一段"}
                        ],
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
