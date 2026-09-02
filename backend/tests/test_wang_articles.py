from types import SimpleNamespace

from backend.api import wang_articles


def test_article_workbench_lists_materialized_drafts_without_plan_queue(
    tmp_path, monkeypatch
):
    draft = {
        "draft_id": "DRAFT-1",
        "title": "文章一",
        "passage": "太16:18",
        "slug": "article-1",
        "profile_id": None,
        "review": None,
    }
    monkeypatch.setattr(wang_articles, "_data_base", lambda: tmp_path)
    monkeypatch.setattr(
        wang_articles,
        "wang_platform_paths",
        lambda _base: SimpleNamespace(repository=tmp_path),
    )
    monkeypatch.setattr(wang_articles, "_drafts", lambda _paths: {"DRAFT-1": draft})
    monkeypatch.setattr(
        wang_articles,
        "_progress_by_draft",
        lambda: {
            "DRAFT-1": {
                "current_stage": "repository_published",
                "repository_published": True,
            }
        },
    )
    monkeypatch.setattr(wang_articles, "_quality_profiles", lambda: {})
    monkeypatch.setattr(wang_articles, "_article_citations", lambda _paths: ({}, []))
    monkeypatch.setattr(wang_articles, "_load_runs", lambda: ([], []))

    payload = wang_articles.articles()

    assert payload["schema_version"] == "wang-operations-articles.v2"
    assert payload["summary"]["articles"] == 1
    assert payload["summary"]["published"] == 1
    assert payload["rows"][0]["draft_id"] == "DRAFT-1"
    assert "plan_id" not in payload["rows"][0]
