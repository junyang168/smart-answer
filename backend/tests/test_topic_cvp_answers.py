"""The owner's answers decide what a topic run writes (#456, solution doc §6, §9)."""
import json

import pytest

from backend.pipeline import topic_cvp_write as w


def _run(tmp_path, answers):
    proposal = {"relations": [{"item": "REL:1"}, {"item": "REL:2"}], "existing_relation_checks": [
        {"relation": "E001", "verdict": "direction_or_type_mismatch", "corrected": {"from": "V2", "to": "V1", "type": "qualifies"}},
        {"relation": "E002", "verdict": "duplicate", "corrected": None},
        {"relation": "E003", "verdict": "evidence_found", "corrected": None}]}
    verdicts = {"REL:1": {"item": "REL:1", "verdict": "accept"}, "REL:2": {"item": "REL:2", "verdict": "revise"},
                "VP:V1": {"item": "VP:V1", "verdict": "reject"}}
    questions = [{"item": k, "choices": ["write", "skip"], "answer": a} for k, a in answers.items()]
    for name, value in {"final-proposal.json": proposal, "final-review.json": {"verdicts": verdicts},
                        "questions.json": questions,
                        "state.json": {"proposal_raw": "p.json", "review_raw": "r.json"}}.items():
        (tmp_path / name).write_text(json.dumps(value))
    return tmp_path


def test_answers_accept_or_drop_escalated_items(tmp_path):
    _, verdicts, decisions, _, _ = w.settled_inputs(_run(tmp_path, {"REL:2": "skip", "VP:V1": "write"}))
    assert verdicts["REL:1"]["verdict"] == "accept"
    assert verdicts["REL:2"]["verdict"] == "reject" and decisions["drop_relations"] == ["REL:2"]
    assert verdicts["VP:V1"]["verdict"] == "accept"


def test_reviewed_corrections_become_relation_fixes(tmp_path):
    _, _, decisions, _, _ = w.settled_inputs(_run(tmp_path, {}))
    assert decisions["relation_fixes"] == [
        {"relation": "E001", "replace_with": {"from": "V2", "to": "V1", "type": "qualifies"}},
        {"relation": "E002", "replace_with": None}]


def test_unanswered_questions_stop_the_write(tmp_path):
    with pytest.raises(SystemExit, match="not answered"):
        w.settled_inputs(_run(tmp_path, {"REL:2": None}))
