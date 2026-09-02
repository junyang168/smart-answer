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


def _publication_fixture(
    tmp_path: Path,
    monkeypatch,
    *,
    approved: bool = True,
    decision_schema: str = "human-publication-decision.v1",
) -> Path:
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
            "schema_version": decision_schema,
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


def test_automated_approval_is_publicly_discoverable(tmp_path: Path, monkeypatch) -> None:
    _publication_fixture(
        tmp_path,
        monkeypatch,
        decision_schema="automated-publication-decision.v1",
    )

    result = public_wang_articles.list_public_articles()
    assert [item["slug"] for item in result["articles"]] == ["matthew-16-13-20"]


def test_changed_manuscript_invalidates_existing_approval(tmp_path: Path, monkeypatch) -> None:
    manuscript = _publication_fixture(tmp_path, monkeypatch)
    manuscript.write_text(manuscript.read_text(encoding="utf-8") + "\n未批准的新段落。", encoding="utf-8")

    assert public_wang_articles.list_public_articles() == {"articles": []}


def test_source_annotations_travel_with_the_publication(tmp_path: Path, monkeypatch) -> None:
    """The reader page's 显示原文来源 toggle renders exactly what publish wrote
    to source-annotations.json; a publication without the file degrades to []."""

    manuscript = _publication_fixture(tmp_path, monkeypatch)
    draft = tmp_path / "editorial_drafts" / "DRAFT-M16-002-V1"
    manifest = json.loads((draft / "editorial-draft-manifest.json").read_text(encoding="utf-8"))
    assert public_wang_articles.public_article_data("matthew-16-13-20")["source_annotations"] == []

    manuscript.write_text(
        manuscript.read_text(encoding="utf-8").replace(
            "正文。\n\n**資料說明：**",
            "正文。\n\n[查看本段来源](#review-source-evidence-p1)\n\n**資料說明：**",
        ),
        encoding="utf-8",
    )
    manifest["drafts"][0]["source_annotations_path"] = "source-annotations.json"
    paragraph_sha = hashlib.sha256("正文。".encode("utf-8")).hexdigest()
    _write_json(
        draft / "source-annotations.json",
        {
            "source_annotations": [
                {
                    "annotation_id": "p1",
                    "paragraph_sha256": paragraph_sha,
                    "sources": [{"fragment_ids": ["F-1"], "title": "母本片段", "excerpts": ["原文"]}],
                }
            ]
        },
    )
    annotations_sha = hashlib.sha256((draft / "source-annotations.json").read_bytes()).hexdigest()
    manifest["drafts"][0]["source_annotations_sha256"] = annotations_sha
    _write_json(draft / "editorial-draft-manifest.json", manifest)
    decision_path = draft / "human-publication-decision.json"
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    decision["manuscript_sha256"] = hashlib.sha256(manuscript.read_bytes()).hexdigest()
    decision["source_annotations_sha256"] = annotations_sha
    _write_json(decision_path, decision)
    payload = public_wang_articles.public_article_data("matthew-16-13-20")
    assert payload["source_annotations"][0]["annotation_id"] == "p1"

    # A path that escapes the publication directory invalidates the current
    # publication; it must not silently serve unverified disclosure data.
    manifest["drafts"][0]["source_annotations_path"] = "../../outside.json"
    _write_json(draft / "editorial-draft-manifest.json", manifest)
    with pytest.raises(HTTPException) as escaped:
        public_wang_articles.public_article_data("matthew-16-13-20")
    assert escaped.value.status_code == 404


def test_source_annotation_tampering_or_wrong_paragraph_anchor_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    manuscript = _publication_fixture(tmp_path, monkeypatch)
    draft = tmp_path / "editorial_drafts" / "DRAFT-M16-002-V1"
    manuscript.write_text(
        manuscript.read_text(encoding="utf-8")
        + "\n\n受约束段落。\n\n[查看本段来源](#review-source-evidence-p1)",
        encoding="utf-8",
    )
    annotations_path = draft / "source-annotations.json"
    _write_json(
        annotations_path,
        {
            "source_annotations": [
                {
                    "annotation_id": "p1",
                    "paragraph_sha256": "0" * 64,
                    "sources": [{"title": "来源", "excerpts": ["原文"]}],
                }
            ]
        },
    )
    annotations_sha = hashlib.sha256(annotations_path.read_bytes()).hexdigest()
    manifest_path = draft / "editorial-draft-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["drafts"][0].update(
        {
            "source_annotations_path": "source-annotations.json",
            "source_annotations_sha256": annotations_sha,
        }
    )
    _write_json(manifest_path, manifest)
    decision_path = draft / "human-publication-decision.json"
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    decision["manuscript_sha256"] = hashlib.sha256(manuscript.read_bytes()).hexdigest()
    decision["source_annotations_sha256"] = annotations_sha
    _write_json(decision_path, decision)

    assert public_wang_articles.list_public_articles() == {"articles": []}

    # Even a correctly anchored file stops being trusted after a byte changes.
    payload = json.loads(annotations_path.read_text(encoding="utf-8"))
    payload["source_annotations"][0]["paragraph_sha256"] = hashlib.sha256(
        "受约束段落。".encode("utf-8")
    ).hexdigest()
    _write_json(annotations_path, payload)
    assert public_wang_articles.list_public_articles() == {"articles": []}


def test_latest_slug_decision_replaces_old_revision_without_reviving_it(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "editorial_drafts"

    def write_revision(draft_id: str, title: str, decided_at: str) -> Path:
        draft = root / draft_id
        draft.mkdir(parents=True)
        manuscript = draft / "article.md"
        manuscript.write_text(f"# {title}\n\n正文。", encoding="utf-8")
        _write_json(
            draft / "editorial-draft-manifest.json",
            {
                "drafts": [
                    {
                        "draft_id": draft_id,
                        "title": title,
                        "passage": "太16:18",
                        "public_slug": "church-foundation",
                        "relative_path": "article.md",
                        "audit_config": {"publication_decision_path": "decision.json"},
                    }
                ]
            },
        )
        decision_path = draft / "decision.json"
        _write_json(
            decision_path,
            {
                "schema_version": "human-publication-decision.v1",
                "draft_id": draft_id,
                "decision": "approved",
                "editorial_review_passed": True,
                "technical_audit_status": "pass",
                "manuscript_sha256": hashlib.sha256(manuscript.read_bytes()).hexdigest(),
                "decided_at": decided_at,
                "source": "wang-article-reviews-publish-endpoint",
            },
        )
        return decision_path

    write_revision("church-foundation-draft-first-v1", "第一版", "2026-09-01T10:00:00+00:00")
    current_decision = write_revision(
        "church-foundation-draft-first-v2", "第二版", "2026-09-01T11:00:00+00:00"
    )
    monkeypatch.setattr(public_wang_articles, "EDITORIAL_DRAFT_ROOT", root)

    assert public_wang_articles.public_article_data("church-foundation")["title"] == "第二版"

    withdrawn = json.loads(current_decision.read_text(encoding="utf-8"))
    withdrawn["decision"] = "withdrawn"
    _write_json(current_decision, withdrawn)
    assert public_wang_articles.list_public_articles() == {"articles": []}
    with pytest.raises(HTTPException):
        public_wang_articles.public_article_data("church-foundation")


def test_legacy_decisions_without_timestamp_keep_first_valid_publication(
    tmp_path: Path, monkeypatch
) -> None:
    _publication_fixture(tmp_path, monkeypatch)
    root = tmp_path / "editorial_drafts"
    backup = root / "ZZ-BACKUP"
    backup.mkdir()
    manuscript = backup / "article.md"
    manuscript.write_text("# 备份", encoding="utf-8")
    _write_json(
        backup / "editorial-draft-manifest.json",
        {
            "drafts": [
                {
                    "draft_id": "ZZ-BACKUP",
                    "title": "不应遮住正式稿",
                    "passage": "太16:13–20",
                    "relative_path": "article.md",
                    "audit_config": {"publication_decision_path": "decision.json"},
                }
            ]
        },
    )
    _write_json(
        backup / "decision.json",
        {
            "schema_version": "human-publication-decision.v1",
            "draft_id": "ZZ-BACKUP",
            "decision": "withdrawn",
            "editorial_review_passed": True,
            "technical_audit_status": "pass",
            "manuscript_sha256": hashlib.sha256(manuscript.read_bytes()).hexdigest(),
        },
    )

    assert public_wang_articles.public_article_data("matthew-16-13-20")["title"] == (
        "馬太福音 16:13–20：認信與教會"
    )
