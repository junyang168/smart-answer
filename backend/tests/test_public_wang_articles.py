from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.api import public_wang_articles


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _publication_fixture(tmp_path: Path, monkeypatch, *, approved: bool = True) -> Path:
    root = tmp_path / "editorial_drafts"
    draft = root / "DRAFT-M16-002-V1"
    draft.mkdir(parents=True)
    manuscript = draft / "article.md"
    manuscript.write_text(
        "# 標題\n\n<!-- provenance: {\"claim_id\":\"CL-1\"} -->\n"
        "### 第一節\n\n正文。\n\n**資料說明：** 現有材料沒有對第 17 節作獨立展開。",
        encoding="utf-8",
    )
    decision_sections = []
    decisions = []
    clip_counts = [1, 1, 5, 2, 4, 3, 3]
    headings = ["第一節", "第二節", "第三節", "第四節", "第四節", "第四節", "第五節"]
    for index, count in enumerate(clip_counts):
        decision_id = f"CD-{index + 1}"
        decision_sections.append({"decision_id": decision_id, "markdown_heading": headings[index]})
        decisions.append(
            {
                "decision_id": decision_id,
                "section_title": f"原聲主題 {index + 1}",
                "passage": "太16:13–20",
                "source_presentations": [
                    {
                        "presentation_id": f"P-{index}-{clip}",
                        "source_id": "SRC-1",
                        "start_seconds": clip * 10,
                        "end_seconds": clip * 10 + 8,
                    }
                    for clip in range(count)
                ],
            }
        )
    _write_json(
        draft / "package.json",
        {
            "source_documents": [
                {
                    "source_id": "SRC-1",
                    "source_type": "sermon_transcript",
                    "transcript_id": "SERMON-1",
                    "title": "原始講道標籤",
                }
            ],
            "product_plans": [{"plan_id": "CP-1", "decisions": decisions}],
        },
    )
    _write_json(
        draft / "editorial-draft-manifest.json",
        {
            "drafts": [
                {
                    "draft_id": "DRAFT-M16-002-V1",
                    "candidate_id": "CP-1",
                    "title": "馬太福音 16:13–20：認信與教會",
                    "passage": "太16:13–20",
                    "relative_path": "article.md",
                    "presentation_package_path": "package.json",
                    "audit_config": {
                        "plan_id": "CP-1",
                        "publication_decision_path": "human-publication-decision.json",
                        "decision_sections": decision_sections,
                    },
                }
            ]
        },
    )
    _write_json(
        draft / "human-publication-decision.json",
        {
            "schema_version": "human-publication-decision.v1",
            "draft_id": "DRAFT-M16-002-V1",
            "decision": "approved" if approved else "rejected",
            "editorial_review_passed": True,
            "technical_audit_status": "pass_with_warnings",
            "manuscript_sha256": hashlib.sha256(manuscript.read_bytes()).hexdigest(),
        },
    )
    monkeypatch.setattr(public_wang_articles, "EDITORIAL_DRAFT_ROOT", root)
    monkeypatch.setattr(
        public_wang_articles.CanonicalRepositoryService,
        "_sermon_catalog_record",
        staticmethod(lambda _transcript_id: {"title": "可理解的講道標題", "deliver_date": "2022-02-07"}),
    )
    monkeypatch.setattr(
        public_wang_articles.CanonicalRepositoryService,
        "_sermon_media",
        staticmethod(
            lambda _transcript_id, _metadata, _catalog: type(
                "Media", (), {"model_dump": lambda self, mode: {"kind": "audio", "url": "/web/video/sermon.mp3"}}
            )()
        ),
    )
    return manuscript


def test_public_article_requires_approval_and_projects_reader_safe_content(tmp_path: Path, monkeypatch) -> None:
    _publication_fixture(tmp_path, monkeypatch)

    article = public_wang_articles.public_article_data("matthew-16-13-20")

    assert article["audio_section_count"] == 7
    assert article["player_count"] == 19
    assert [section["heading"] for section in article["audio_sections"]].count("第四節") == 3
    assert "provenance" not in article["markdown"]
    assert "claim_id" not in article["markdown"]
    assert "資料說明" not in article["markdown"]
    assert "閱讀提示" in article["markdown"]
    assert article["audio_sections"][0]["clips"][0]["delivered_on"] == "2022-02-07"
    assert "decision_id" not in json.dumps(article)


def test_unapproved_article_is_not_publicly_discoverable(tmp_path: Path, monkeypatch) -> None:
    _publication_fixture(tmp_path, monkeypatch, approved=False)

    assert public_wang_articles.list_public_articles() == {"articles": []}
    with pytest.raises(HTTPException) as exc:
        public_wang_articles.public_article_data("matthew-16-13-20")
    assert exc.value.status_code == 404


def test_public_article_index_contains_article_navigation_metadata(tmp_path: Path, monkeypatch) -> None:
    _publication_fixture(tmp_path, monkeypatch)

    result = public_wang_articles.list_public_articles()

    assert result == {
        "articles": [
            {
                "slug": "matthew-16-13-20",
                "title": "馬太福音 16:13–20：認信與教會",
                "passage": "太16:13–20",
                "scripture": {
                    "book": "Matt",
                    "book_label": "馬太福音",
                    "chapter": 16,
                    "verse_start": 13,
                    "end_chapter": 16,
                    "verse_end": 20,
                    "display": "太16:13–20",
                },
                "topics": ["認信", "教會"],
                "href": "/resources/wang-repository/articles/matthew-16-13-20",
            }
        ]
    }


def test_changed_manuscript_invalidates_existing_approval(tmp_path: Path, monkeypatch) -> None:
    manuscript = _publication_fixture(tmp_path, monkeypatch)
    manuscript.write_text(manuscript.read_text(encoding="utf-8") + "\n未批准的新段落。", encoding="utf-8")

    assert public_wang_articles.list_public_articles() == {"articles": []}
