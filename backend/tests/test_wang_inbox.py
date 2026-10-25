"""The library admin's to-do list: only what waits for a person (#453)."""

import json

from backend.api import wang_essay_runs, wang_inbox


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_a_passed_unpublished_article_waits_for_the_final_review(tmp_path):
    _write(tmp_path / "son.json", {"variant": "draft_first", "review_id": "son", "title": "人子",
                                   "workflow_status": "review_passed", "publication_decision": None,
                                   "registered_at": "2026-10-25T00:00:00Z"})
    _write(tmp_path / "keys.json", {"variant": "draft_first", "review_id": "keys", "title": "鑰匙",
                                    "workflow_status": "review_passed",
                                    "publication_decision": {"decision": "approved"}})
    _write(tmp_path / "poc.json", {"title": "旧流程", "workflow_status": "review_passed"})
    items = wang_inbox.final_reviews(tmp_path)
    assert [item["title"] for item in items] == ["人子"]
    assert items[0]["href"].endswith("/reviews/son")


def test_an_essay_stopped_for_the_owner_is_listed_in_the_owners_terms(tmp_path):
    run = tmp_path / "ransom"
    _write(run / "essay.json", {"review_id": "ransom", "passage": "太20:28"})
    _write(run / "scope.json", {"working_title": "萬人的贖價", "reader_question": "贖價付給誰？",
                                "passage_refs": ["太20:28"]})
    _write(run / "state.json", {"stages": {"scope": {"status": "done", "at": "t1"},
                                           "coverage": {"status": "done", "at": "t2"}},
                                "stopped": {"stage": "decisions", "needs": "x", "at": "t3",
                                            "candidates": [{"id": "a"}, {"id": "b"}]}})
    # A run at the final review is listed once, from the review manifest.
    done = tmp_path / "son"
    _write(done / "essay.json", {"review_id": "son", "passage": "p"})
    _write(done / "state.json", {"stages": {}, "stopped": {"stage": "final_review", "needs": "x"}})

    items = wang_inbox.essay_stops(tmp_path)
    assert len(items) == 1
    assert items[0]["title"] == "萬人的贖價"
    assert items[0]["summary"] == "覆盖搜索找到 2 个候选，等逐项定纳入还是相邻"
    summary = wang_essay_runs.run_summary(run)
    assert summary["stage_label"] == "选材" and summary["stages_done"] == ["scope", "coverage"]


def test_no_runs_and_no_manifests_is_an_empty_list(tmp_path):
    assert wang_inbox.essay_stops(tmp_path / "missing") == []
    assert wang_inbox.final_reviews(tmp_path / "missing") == []
