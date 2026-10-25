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


def test_a_review_report_follows_an_owner_round_back_to_its_first_review(tmp_path):
    from backend.api.draft_first_review_report import review_report

    first, owner = tmp_path / "review", tmp_path / "review-owner-01"
    _write(first / "review-run.json", {"status": "human_review_required"})
    _write(first / "round-00" / "outcome.json", {"passed": False, "blocking_findings": [
        {"finding_id": "R0-01", "gate": "review", "kind": "quality", "anchor": "句一",
         "summary": "太长｜required: 删去"}]})
    _write(first / "round-00" / "review.json", {"findings": [
        {"anchor": "句一", "required_change": "删去", "source_quote": "原話"}], "verdict": {}})
    _write(first / "round-00" / "revision.json", {"edits": [{}], "responses": [
        {"finding_id": "R0-01", "response": "disputed", "note": "原文如此"}]})
    _write(first / "round-01" / "outcome.json", {"passed": False, "blocking_findings": []})
    _write(first / "round-01" / "review.json", {"dispute_rulings": [
        {"finding_id": "R0-01", "ruling": "accepted", "note": "申辩成立"}], "verdict": {}})
    # The owner round re-records round 1 with the owner's note and adds round 2.
    _write(owner / "review-run.json", {"status": "review_passed", "continued_from": str(first)})
    _write(owner / "round-01" / "outcome.json", {"passed": False, "blocking_findings": [
        {"finding_id": "R1-01", "gate": "owner", "kind": "owner_direction", "anchor": "注一", "summary": "删"}]})
    _write(owner / "round-02" / "outcome.json", {"passed": True, "blocking_findings": []})
    _write(owner / "round-02" / "review-attempt-1" / "folder" / "checklist.json",
           [{"item_id": "V:1", "kind": "viewpoint", "text": "人子是自稱"}])
    _write(owner / "round-02" / "review.json", {"checklist_review": [
        {"item_id": "V:1", "acceptable": True, "reason": "有"}], "verdict": {}})
    _write(owner / "final-self-check.json", [{"item_id": "V:1", "status": "landed", "anchor": "句", "reason": "r"}])

    report = review_report(owner)
    assert [r["round"] for r in report["rounds"]] == [0, 1, 2]
    first_finding = report["rounds"][0]["findings"][0]
    assert first_finding["summary"] == "太长" and first_finding["required_change"] == "删去"
    assert first_finding["response"]["label"] == "申辩" and first_finding["ruling"]["label"] == "接受申辩"
    assert report["rounds"][1]["findings"][0]["gate_label"] == "负责人"
    assert report["checklist"][0]["status_label"] == "已写到" and report["status"] == "review_passed"


def test_article_rows_join_registered_reviews_and_runs(tmp_path):
    manifests, runs = tmp_path / "manifests", tmp_path / "runs"
    _write(manifests / "keys.json", {"variant": "draft_first", "review_id": "keys", "title": "鑰匙",
                                     "publication_decision": {"decision": "approved"}})
    _write(runs / "ransom" / "essay.json", {"review_id": "ransom", "passage": "太20:28"})
    _write(runs / "ransom" / "scope.json", {"working_title": "萬人的贖價"})
    _write(runs / "ransom" / "state.json", {"stages": {}, "stopped": {"stage": "scope", "needs": "x"}})
    rows = {row["review_id"]: row for row in wang_essay_runs.article_rows(runs, manifests)}
    assert rows["keys"]["stage_label"] == "已发布" and rows["keys"]["run_href"] is None
    assert rows["ransom"]["stage_label"] == "定问题" and rows["ransom"]["review_href"] is None
    assert rows["ransom"]["waiting"] == "文章的问题、经文或选定的观点还没写全"


def test_graph_components_leave_isolated_viewpoints_ungrouped():
    from backend.api.wang_knowledge_graph import components

    groups = components(["a", "b", "c", "d"], [("a", "b"), ("b", "c")])
    assert groups["a"] == groups["b"] == groups["c"] and "d" not in groups
