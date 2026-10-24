"""Two-way review (#439): one checklist for author and reviewer, an agent reviewer, disputes."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.pipeline import draft_first_review_runner as runner
from backend.pipeline.draft_first_checklist import build_checklist, disposition_problems
from backend.pipeline.draft_first_review_agent import audit_events, command_escapes, prepare_folder
from backend.pipeline.draft_first_review_runner import (
    DraftReviewContractError,
    validate_revision,
    validate_two_way_review,
)

MANUSCRIPT = "# 人子\n\n耶穌每次說人子都帶定冠詞。\n\n中文沒有辦法翻出來。\n"
SERMON = "耶穌每一次講人子的時候都加定冠詞。我們中文沒有辦法翻。"


def _packet():
    return {
        "focal_viewpoints": [
            {
                "structure_role": "supporting_conclusion",
                "revision": {
                    "viewpoint_revision_id": "CVR-1",
                    "core_proposition": "耶稣每次说「人子」时都使用定冠词。",
                    "proposition_signature": {"modality": "断言", "conditions": ["限于福音书中耶稣的自称"]},
                },
            }
        ],
        "observation_supports": [
            {"observation_support_id": "VOS-1", "validated_against_viewpoint_revision_id": "CVR-1",
             "source_id": "S1", "observation_summary": "中文没有定冠词。", "support_scope_note": "概括"},
            {"observation_support_id": "VOS-OLD", "effective_state": "retired", "observation_summary": "旧"},
        ],
        "relations": [
            {"viewpoint_relation_id": "VR-1", "relation_type": "tensions_with", "reason": "約5:27 例外"},
            {"viewpoint_relation_id": "VR-2", "relation_type": "extends", "reason": "延伸"},
        ],
        "structure": {"revision": {"unresolved_items": ["两种说法未统一"]}},
        "argument_routes": [
            {"full_attestation_count": 1,
             "revision": {"argument_route_revision_id": "AR-1", "route_label": "路线",
                          "validated_against_conclusion_viewpoint_revision_id": "CVR-1",
                          "ordered_inference_nodes": [{"normalized_proposition": "甲"}, {"normalized_proposition": "乙"}]}}
        ],
        "source_originals": {"originals": [{"source_id": "S1", "title": "講道", "source_type": "sermon_transcript",
                                            "content": SERMON + "\n\n第二段。"}]},
    }


def _profile():
    return {
        "dimensions": [{"id": "source_and_exegesis", "weight": 15, "minimum": 12}],
        "hard_failures": ["conclusion_reader_answer_broken"],
    }


def _landed(checklist):
    return [{"item_id": item["item_id"], "status": "landed", "anchor": "耶穌每次說人子都帶定冠詞。", "reason": ""}
            for item in checklist]


def _review(checklist, findings=(), rulings=(), acceptable=True):
    return {
        "summary": "ok",
        "checklist_review": [{**item, "acceptable": acceptable} for item in _landed(checklist)],
        "dimension_scores": [{"dimension_id": "source_and_exegesis", "score": 13, "evidence": "「耶穌每次說人子都帶定冠詞。」"}],
        "hard_failure_assessments": [{"failure_id": "conclusion_reader_answer_broken", "failed": False, "evidence": ""}],
        "findings": list(findings),
        "dispute_rulings": list(rulings),
    }


def _removed(item_id, quote=SERMON[:12], upholds=""):
    return {"direction": "removed", "kind": "distorted", "dimension_id": "source_and_exegesis", "item_id": item_id,
            "anchor": "耶穌每次說人子都帶定冠詞。", "source_quote": quote, "summary": "s", "required_change": "r",
            "blocking": True, "upholds": upholds}


def _validate(review, checklist, disputed=()):
    packet = _packet()
    from backend.pipeline.draft_first_checklist import quote_corpus
    return validate_two_way_review(review, manuscript=MANUSCRIPT, profile=_profile(), checklist=checklist,
                                   corpus=quote_corpus(packet, checklist), disputed_ids=list(disputed))


def test_checklist_covers_cvp_judgements_only():
    checklist = build_checklist(_packet())
    assert [item["item_id"] for item in checklist] == [
        "V:CVR-1", "C:CVR-1:1", "O:VOS-1", "T:VR-1", "U:1", "R:AR-1",
    ]
    assert checklist[-1]["text"] == "甲 → 乙"


def test_a_self_check_answers_every_item_once_with_verbatim_anchors():
    checklist = build_checklist(_packet())
    assert disposition_problems(checklist, _landed(checklist), MANUSCRIPT) == []
    broken = _landed(checklist)[1:] + [
        {"item_id": "V:NOPE", "status": "landed", "anchor": "耶穌", "reason": ""},
        {"item_id": "U:1", "status": "absent", "anchor": "", "reason": " "},
        {"item_id": "R:AR-1", "status": "landed", "anchor": "稿裡沒有", "reason": ""},
    ]
    problems = " | ".join(disposition_problems(checklist, broken, MANUSCRIPT))
    for text in ("no disposition for: V:CVR-1", "unknown items: V:NOPE", "more than one disposition",
                 "U:1: absent without a reason", "R:AR-1: anchor not verbatim"):
        assert text in problems


def test_a_valid_two_way_review_passes():
    checklist = build_checklist(_packet())
    assert _validate(_review(checklist), checklist) == {"dimensions_below_minimum": [], "hard_failures_failed": []}


def test_removed_teaching_must_be_quoted_verbatim_and_named():
    checklist = build_checklist(_packet())
    with pytest.raises(DraftReviewContractError, match="source quote not verbatim"):
        _validate(_review(checklist, [_removed("V:CVR-1", quote="教授從沒說過的話")]), checklist)
    with pytest.raises(DraftReviewContractError, match="names no checklist item"):
        _validate(_review(checklist, [_removed("V:ELSEWHERE")]), checklist)
    missing = {**_removed("V:CVR-1"), "kind": "missing", "anchor": ""}
    _validate(_review(checklist, [missing]), checklist)


def test_an_unacceptable_item_needs_a_blocking_finding():
    checklist = build_checklist(_packet())
    with pytest.raises(DraftReviewContractError, match="without a blocking finding"):
        _validate(_review(checklist, acceptable=False), checklist)


def test_every_dispute_gets_one_ruling_and_upheld_ones_are_reissued():
    checklist = build_checklist(_packet())
    with pytest.raises(DraftReviewContractError, match="dispute rulings"):
        _validate(_review(checklist), checklist, disputed=["R0-03"])
    upheld = [{"finding_id": "R0-03", "ruling": "upheld", "note": "原文另有限定"}]
    with pytest.raises(DraftReviewContractError, match="not re-issued"):
        _validate(_review(checklist, rulings=upheld), checklist, disputed=["R0-03"])
    _validate(_review(checklist, [_removed("V:CVR-1", upholds="R0-03")], upheld), checklist, disputed=["R0-03"])
    accepted = [{"finding_id": "R0-03", "ruling": "accepted", "note": "申辩成立"}]
    _validate(_review(checklist, rulings=accepted), checklist, disputed=["R0-03"])


def test_only_reviewer_findings_can_be_disputed_and_only_with_the_source():
    before = MANUSCRIPT * 3
    findings = [{"finding_id": "R0-01", "gate": "review"}, {"finding_id": "R0-02", "gate": "quote_check"}]

    def revision(*responses):
        return {"manuscript_markdown": before, "responses": list(responses)}

    fixed = {"finding_id": "R0-02", "response": "fixed", "note": "", "source_quote": ""}
    dispute = {"finding_id": "R0-01", "response": "disputed", "note": "原文正是這樣說", "source_quote": SERMON[:16]}
    assert validate_revision(before, revision(dispute, fixed), findings, corpus=SERMON) == before
    with pytest.raises(DraftReviewContractError, match="not verbatim"):
        validate_revision(before, revision({**dispute, "source_quote": "我想是這樣"}, fixed), findings, corpus=SERMON)
    with pytest.raises(DraftReviewContractError, match="only a reviewer finding"):
        validate_revision(before, revision({**dispute, "response": "fixed"}, {**fixed, "response": "disputed",
                                           "source_quote": SERMON[:16]}), findings, corpus=SERMON)


def test_reads_outside_the_review_folder_void_the_review(tmp_path):
    folder = tmp_path / "folder"
    for ok in ("/bin/zsh -lc 'cat draft.md'", "/bin/zsh -lc 'python3 tools/src.py search 人子 | head -40'",
               f"/bin/zsh -lc 'cat {folder}/checklist.json'", "/bin/zsh -lc 'rg -n 定冠詞 sources 2>/dev/null'"):
        assert command_escapes(ok, folder) is None, ok
    for bad in ("/bin/zsh -lc 'cat /Users/someone/app/other-draft.md'", "/bin/zsh -lc 'cat ../call/stdout.log'",
                "/bin/zsh -lc 'ls ~'", "/bin/zsh -lc 'cat $HOME/.codex/auth.json'"):
        assert command_escapes(bad, folder), bad
    events = [
        {"type": "item.completed", "item": {"type": "command_execution", "command": "/bin/zsh -lc 'cat draft.md'", "exit_code": 0}},
        {"type": "item.completed", "item": {"type": "web_search", "query": "son of man"}},
    ]
    audit = audit_events(events, folder)
    assert audit["commands"] == [{"command": "/bin/zsh -lc 'cat draft.md'", "exit_code": 0}]
    assert audit["violations"] == ["tool outside the review folder's shell: web_search"]


def test_the_review_folder_and_its_reader(tmp_path):
    folder = tmp_path / "folder"
    prepare_folder(folder, manuscript=MANUSCRIPT, packet=_packet(), files={"checklist.json": [{"item_id": "V:CVR-1"}]})
    assert (folder / "draft.md").read_text() == MANUSCRIPT
    assert "source_originals" not in json.loads((folder / "structure.json").read_text())

    def tool(*args):
        return subprocess.run([sys.executable, "tools/src.py", *args], cwd=folder, capture_output=True,
                              text=True, check=True).stdout

    assert tool("list").startswith("S1\t01-S1.md\t2\t")
    assert tool("search", "定冠詞") == f"[S1 #1]\n{SERMON}\n\n"
    assert tool("show", "S1", "2") == "[S1 #2]\n第二段。\n\n"


def test_prompts_share_one_rule_text():
    from backend.pipeline.draft_first_author_runner import prompt_text

    prompts = Path(runner.__file__).with_name("prompts")
    rules = (prompts / "draft_first_two_way_rules.md").read_text().strip()
    for name in ("draft_first_topic_author.md", "draft_first_two_way_review.md"):
        text = prompt_text(prompts / name)
        assert rules in text and "{TWO_WAY_RULES}" not in text
    revision = prompt_text(prompts / "draft_first_revision.md").replace(
        "{WRITING_RULES}", prompt_text(prompts / "draft_first_topic_author.md"))
    assert rules in revision
    for phrase in ("不要堆砌材料", "生活应用、背景知识", "口头语、口误、重复、自我推介"):
        assert phrase in rules


class FakeAgent:
    model, backend, reasoning_effort = "gpt-6.1-sol", "codex_subscription_agent", "high"

    def __init__(self, replies):
        self.replies, self.tasks = list(replies), []

    def review(self, *, folder, call_dir, prompt, schema):
        self.tasks.append(json.loads((folder / "task.json").read_text()))
        assert "{TWO_WAY_RULES}" not in prompt and (folder / "tools" / "src.py").exists()
        return self.replies.pop(0), {"model": self.model}


def _gates(tmp_path, monkeypatch, agent, self_check):
    def call(client, prompt, payload, schema):
        if prompt.name == "draft_first_blind_read.md":
            return {"answer_in_one_sentence": "answer", "comprehension_obstacles": []}, {}
        return {"answer_matches_settled_positions": True, "modality_preserved": True, "mismatches": []}, {}

    monkeypatch.setattr(runner, "_call", call)
    monkeypatch.setattr(runner, "scripture_corpus", lambda packet: {"chapters": {}, "text": ""})
    clients = {"blind_read": SimpleNamespace(model="haiku"), "blind_compare": SimpleNamespace(model="gpt"), "review": agent}
    return runner.run_gates(manuscript=MANUSCRIPT, packet=_packet(), profile=_profile(), clients=clients,
                            round_dir=tmp_path, self_check=self_check)


def test_a_rejected_review_is_retried_once_and_kept(tmp_path, monkeypatch):
    checklist = build_checklist(_packet())
    bad = _review(checklist, [_removed("V:CVR-1", quote="不在原文")])
    agent = FakeAgent([bad, _review(checklist)])
    outcome = _gates(tmp_path, monkeypatch, agent, _landed(checklist))
    assert outcome["passed"] is True
    assert "previous_attempt_rejected" in agent.tasks[1]
    kept = json.loads((tmp_path / "review-rejected.json").read_text())
    assert len(kept["rejected_attempts"]) == 1

    # A second rejection leaves the review incomplete; it never passes.
    agent = FakeAgent([bad, bad])
    outcome = _gates(tmp_path / "b", monkeypatch, agent, _landed(checklist))
    assert outcome["passed"] is False and outcome["review_completed"] is False
    assert outcome["stage_errors"][0]["stage"] == "review"


def test_an_incomplete_self_check_goes_back_to_the_author(tmp_path, monkeypatch):
    checklist = build_checklist(_packet())
    outcome = _gates(tmp_path, monkeypatch, FakeAgent([_review(checklist)]), _landed(checklist)[1:])
    assert [(f["gate"], f["kind"]) for f in outcome["blocking_findings"]] == [("self_check", "self_check_incomplete")]


def _corpus():
    from backend.pipeline.draft_first_coverage_search import corpus_index

    records = {
        "canonical_viewpoints": [
            {"viewpoint_id": "CV-A", "identity_status": "active", "current_revision_id": "R-A"},
            {"viewpoint_id": "CV-B", "identity_status": "active", "current_revision_id": "R-B"},
            {"viewpoint_id": "CV-OLD", "identity_status": "merged", "current_revision_id": "R-OLD"},
        ],
        "viewpoint_revisions": [
            {"viewpoint_revision_id": "R-A", "core_proposition": "人子帶定冠詞。"},
            {"viewpoint_revision_id": "R-B", "core_proposition": "人子象徵代表神。"},
        ],
        "viewpoint_claim_links": [
            {"claim_id": "CL-1", "viewpoint_id": "CV-A", "effective_state": "active"},
            {"claim_id": "CL-2", "viewpoint_id": "CV-A", "effective_state": "retired"},
        ],
        "claims": [
            {"claim_id": "CL-1", "statement": "每次都加定冠詞", "occurrences": [{"source_id": "S1"}]},
            {"claim_id": "CL-2", "statement": "人子、人代表神", "occurrences": []},
            {"claim_id": "CL-3", "statement": "舊說法", "review_status": "superseded"},
        ],
    }
    return corpus_index(records)


def test_coverage_corpus_holds_current_viewpoints_and_live_claims():
    viewpoints, claims = _corpus()
    assert [v["viewpoint_id"] for v in viewpoints] == ["CV-A", "CV-B"]
    assert viewpoints[0]["member_claim_count"] == 1
    assert {c["claim_id"]: c["viewpoint_ids"] for c in claims} == {"CL-1": ["CV-A"], "CL-2": []}


def test_coverage_candidates_are_real_unselected_and_shown_from_the_record(tmp_path):
    from backend.pipeline.draft_first_coverage_search import prepare_folder, validate_candidates

    viewpoints, claims = _corpus()
    selection = {"selection_id": "S", "decision_basis": "核心", "focal_viewpoints": [{"viewpoint_revision_id": "R-A", "structure_role": "central_claim"}]}

    def candidate(kind, id_):
        return {"kind": kind, "id": id_, "why_relevant": "模型的轉述", "suggestion": "include", "reason": "r"}

    result = {"candidates": [candidate("viewpoint", "CV-B"), candidate("claim", "CL-2")]}
    listed = validate_candidates(result, selection=selection, viewpoints=viewpoints, claims=claims)
    assert [c["text"] for c in listed] == ["人子象徵代表神。", "人子、人代表神"]
    for bad, message in ((candidate("viewpoint", "CV-A"), "already selected"),
                         (candidate("claim", "CL-1"), "already belongs"),
                         (candidate("claim", "CL-9"), "not a current claim")):
        with pytest.raises(ValueError, match=message):
            validate_candidates({"candidates": [bad]}, selection=selection, viewpoints=viewpoints, claims=claims)

    folder = tmp_path / "folder"
    prepare_folder(folder, scope={"reader_question": "Q"}, selection=selection, viewpoints=viewpoints, claims=claims)
    out = subprocess.run([sys.executable, "tools/search.py", "claims", "代表神", "--unlinked"], cwd=folder,
                         capture_output=True, text=True, check=True).stdout
    assert out == "CL-2\t-\t人子、人代表神\n"


def test_the_owner_decides_every_candidate_before_the_selection_changes():
    from backend.pipeline.draft_first_coverage_search import apply_decisions

    selection = {"selection_id": "S", "decision_basis": "核心", "focal_viewpoints": [{"viewpoint_revision_id": "R-A"}]}
    record = {"candidates": [
        {"kind": "viewpoint", "id": "CV-B", "viewpoint_revision_id": "R-B", "text": "人子象徵代表神。"},
        {"kind": "claim", "id": "CL-2", "viewpoint_ids": [], "text": "人子、人代表神"},
    ]}
    with pytest.raises(ValueError, match="undecided"):
        apply_decisions(selection, record, [{"id": "CV-B", "decision": "include", "structure_role": "supporting_conclusion", "reason": "r"}])
    with pytest.raises(ValueError, match="needs a reason"):
        apply_decisions(selection, record, [{"id": "CV-B", "decision": "adjacent", "reason": ""},
                                            {"id": "CL-2", "decision": "adjacent", "reason": "r"}])
    updated = apply_decisions(selection, record, [
        {"id": "CV-B", "decision": "include", "structure_role": "supporting_conclusion", "reason": "論證需要"},
        {"id": "CL-2", "decision": "include", "reason": "論證需要"},
    ])
    assert [f["viewpoint_revision_id"] for f in updated["focal_viewpoints"]] == ["R-A", "R-B"]
    assert updated["coverage_review"]["decisions"][1]["needs_cvp_work"] is True


def test_calibration_scores_hits_against_the_owner_key():
    import importlib.util

    path = Path(runner.__file__).resolve().parents[2] / "scripts" / "draft-first-calibrate.py"
    spec = importlib.util.spec_from_file_location("calibrate", path)
    calibrate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(calibrate)
    key = json.loads((path.parents[1] / "backend/config/draft_first_calibration/wkp439-son-of-man-draft-a.json").read_text())
    findings = [
        {"anchor": "母本列了幾處平行經文", "summary": "後台用語", "blocking": True},
        {"anchor": "定冠詞就是幹這件事的。", "summary": "油滑", "blocking": False},
        {"anchor": "教授說得很準", "summary": "評判", "blocking": False},
    ]
    results = {row["id"]: row["result"] for row in calibrate.score(key, findings)}
    assert results == {"K1": "hit", "K2": "hit", "K3": "miss", "K4": "miss", "K5": "manual",
                       "K7": "miss", "K9": "miss", "K10": "hit_nonblocking"}
