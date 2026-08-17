from __future__ import annotations

import json

from backend.api import matthew_exposition_progress as progress


def _planned(passage: str, *, claims: bool = True) -> dict:
    parsed = progress._passage(passage)
    assert parsed is not None
    return {
        "article_unit_id": parsed["osis"],
        "passage": parsed,
        "title": passage,
        "draft_id": None,
        "plan_refs": [],
        "claim_ids": ["CL-1"] if claims else [],
        "coverage": "complete" if claims else "requires_cross_chapter_scope",
    }


def _published(passage: str, draft_id: str) -> dict:
    parsed = progress._passage(passage)
    assert parsed is not None
    return {
        "article_unit_id": parsed["osis"],
        "passage": parsed,
        "title": passage,
        "draft_id": draft_id,
        "current_stage": "repository_published",
        "repository_published": True,
        "production_visible": None,
        "blockers": [],
    }


def test_cross_chapter_passage_counts_both_chapters() -> None:
    passage = progress._passage("太16:28–17:8")

    assert passage == {
        "osis": "Matt.16.28-Matt.17.8",
        "display": "太16:28–17:8",
        "start": {"chapter": 16, "verse": 28},
        "end": {"chapter": 17, "verse": 8},
        "cross_chapter": True,
    }
    assert progress._verse_keys(passage) == [(16, 28), *[(17, verse) for verse in range(1, 9)]]


def test_actual_articles_supersede_overlapping_candidate_units(monkeypatch) -> None:
    actual = [
        _published("太16:1–12", "DRAFT-1"),
        _published("太16:13–20", "DRAFT-2"),
        _published("太16:21–23", "DRAFT-3"),
    ]
    planned = [
        _planned("太16:1–12"),
        _planned("太16:13–18"),
        _planned("太16:19"),
        _planned("太16:20–23"),
        _planned("太16:24–27"),
        _planned("太16:28–17:8", claims=False),
    ]
    monkeypatch.setattr(progress, "_production_slugs", lambda: (None, {"deployment_state": "unknown"}))
    monkeypatch.setattr(progress, "_repository_articles", lambda _slugs: actual)
    monkeypatch.setattr(progress, "_planned_candidates", lambda: (planned, []))

    payload = progress.progress_data()

    assert payload["schema_version"] == "wang-matthew-exposition-progress.v1"
    assert payload["summary"]["planned_article_count"] == 5
    assert payload["summary"]["generated_article_count"] == 3
    assert payload["summary"]["repository_published_count"] == 3
    assert payload["summary"]["production_visible_count"] is None
    assert [item["passage"]["display"] for item in payload["articles"]] == [
        "太16:1–12",
        "太16:13–20",
        "太16:21–23",
        "太16:24–27",
        "太16:28–17:8",
    ]


def test_unconfigured_production_is_unknown_not_false(monkeypatch) -> None:
    monkeypatch.setattr(progress, "_production_slugs", lambda: (None, {"deployment_state": "unknown"}))
    monkeypatch.setattr(progress, "_repository_articles", lambda _slugs: [_published("太16:1–12", "DRAFT-1")])
    monkeypatch.setattr(progress, "_planned_candidates", lambda: ([], []))

    payload = progress.progress_data()

    assert payload["articles"][0]["production_visible"] is None
    assert payload["chapters"][15]["production_verse_count"] is None
    assert payload["summary"]["production_visible_count"] is None


def test_artifact_endpoint_resolves_only_manifest_bound_paths(tmp_path, monkeypatch) -> None:
    draft_dir = tmp_path / "editorial_drafts" / "DRAFT-1"
    draft_dir.mkdir(parents=True)
    (draft_dir / "article.md").write_text("# Article", encoding="utf-8")
    (draft_dir / "audit.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    (draft_dir / "editorial-draft-manifest.json").write_text(
        json.dumps(
            {
                "drafts": [
                    {
                        "draft_id": "DRAFT-1",
                        "relative_path": "article.md",
                        "audit_config": {"audit_output_path": "audit.json"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(progress, "WANG_REPOSITORY_DIR", tmp_path)

    assert progress.get_matthew_progress_artifact("DRAFT-1", "program-audit") == {"status": "pass"}
    manuscript = progress.get_matthew_progress_artifact("DRAFT-1", "manuscript")
    assert manuscript.body == b"# Article"
