"""The resumable essay command (#451): stops where the owner decides, never repeats a stage."""

import json
from pathlib import Path

from backend.pipeline.draft_first_essay import EssayRun


def _arg(argv, flag):
    return Path(argv[argv.index(flag) + 1])


class FakeCommands:
    """Writes what each real command would write; records every call."""

    def __init__(self, review_status="human_review_required"):
        self.calls = []
        self.review_status = review_status

    def __call__(self, stage, argv, log):
        self.calls.append(stage)
        if stage == "coverage":
            out = _arg(argv, "--output-dir")
            out.mkdir(parents=True, exist_ok=True)
            (out / "coverage-candidates.json").write_text(json.dumps({"candidates": [
                {"id": "CL1", "kind": "claim", "suggestion": "include", "text": "一條 Claim", "why_relevant": "w", "reason": "r"}]}))
        elif stage == "decisions":
            decisions = json.loads(_arg(argv, "--apply-decisions").read_text())
            (_arg(argv, "--output-dir") / "editorial-selection.json").write_text(json.dumps({"coverage_review": {"decisions": [
                {**d, "text": "一條 Claim", **({"needs_cvp_work": True} if d["decision"] == "include" else {})}
                for d in decisions]}}))
        elif stage == "packet":
            out = _arg(argv, "--output-dir")
            out.mkdir(parents=True, exist_ok=True)
            (out / "theological-evidence-packet.json").write_text(_arg(argv, "--scope").read_text())
        elif stage == "author":
            out = _arg(argv, "--output-dir")
            out.mkdir(parents=True, exist_ok=True)
            (out / "draft.md").write_text("# 稿\n")
            (out / "self-check.json").write_text("[]")
        elif stage == "review" or stage.startswith("owner-round"):
            out = _arg(argv, "--output-dir")
            status = self.review_status if stage == "review" else "review_passed"
            (out / "round-00").mkdir(parents=True, exist_ok=True)
            (out / "round-00" / "outcome.json").write_text(json.dumps({"blocking_findings": [
                {"finding_id": "R0-01", "gate": "review", "kind": "quality", "anchor": "稿", "summary": "s"}]}))
            (out / "review-run.json").write_text(json.dumps({"status": status}))
            (out / "final.md").write_text("# 稿\n")
            (out / "final-self-check.json").write_text("[]")
        elif stage == "binding":
            _arg(argv, "--output").write_text("{}")
        elif stage == "blurb":
            _arg(argv, "--output").write_text("{}")


def test_an_essay_run_stops_for_the_owner_and_resumes_without_repeating(tmp_path):
    fake = FakeCommands()
    run = lambda: EssayRun(tmp_path, runner=fake).advance()

    assert run()["stage"] == "scope"
    (tmp_path / "essay.json").write_text(json.dumps({"review_id": "test-essay", "passage": "太20:28"}))
    (tmp_path / "scope.json").write_text(json.dumps({"reader_question": "贖價是什麼？"}))
    (tmp_path / "editorial-selection.json").write_text("{}")

    stopped = run()
    assert stopped["stage"] == "decisions" and stopped["candidates"][0]["id"] == "CL1"

    # An included Claim in no viewpoint stops for CVP work.
    (tmp_path / "coverage-decisions.json").write_text(json.dumps([{"id": "CL1", "decision": "include", "reason": "r"}]))
    stopped = run()
    assert stopped["stage"] == "cvp_gap" and stopped["claims"][0]["id"] == "CL1"

    # The owner records it as adjacent instead; only the changed decision reruns.
    (tmp_path / "coverage-decisions.json").write_text(json.dumps([{"id": "CL1", "decision": "adjacent", "reason": "r"}]))
    stopped = run()
    assert stopped["stage"] == "review" and stopped["open"][0]["finding_id"] == "R0-01"
    assert fake.calls == ["coverage", "decisions", "decisions", "packet", "author", "review"]

    # The owner adds a round with a note; the run goes on to registration.
    (tmp_path / "owner-notes").mkdir()
    (tmp_path / "owner-notes" / "01.json").write_text(json.dumps({"notes": [{"anchor": "稿", "summary": "改"}]}))
    stopped = run()
    assert stopped["stage"] == "final_review" and stopped["page"].endswith("/test-essay")
    assert fake.calls[-4:] == ["owner-round-01", "binding", "blurb", "register"]

    # Running again repeats nothing and stops at the same place.
    before = list(fake.calls)
    assert run()["stage"] == "final_review" and fake.calls == before
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["current_review"].endswith("review-owner-01")


def test_a_changed_input_reruns_its_stage_and_what_follows(tmp_path):
    fake = FakeCommands(review_status="review_passed")
    (tmp_path / "essay.json").write_text(json.dumps({"review_id": "e", "passage": "p"}))
    (tmp_path / "scope.json").write_text(json.dumps({"reader_question": "q"}))
    (tmp_path / "editorial-selection.json").write_text("{}")
    (tmp_path / "coverage-decisions.json").write_text(json.dumps([{"id": "CL1", "decision": "adjacent", "reason": "r"}]))
    EssayRun(tmp_path, runner=fake).advance()
    first = len(fake.calls)
    (tmp_path / "scope.json").write_text(json.dumps({"reader_question": "改過的問題"}))
    EssayRun(tmp_path, runner=fake).advance()
    # The rewritten essay came out word for word the same, so its blurb stands.
    assert fake.calls[first:] == ["coverage", "decisions", "packet", "author", "review", "binding", "register"]
    # An unchanged packet does not rewrite the essay.
    (tmp_path / "editorial-selection.json").write_text("{ }")
    third = len(fake.calls)
    EssayRun(tmp_path, runner=fake).advance()
    assert fake.calls[third:] == ["coverage", "decisions"]


def test_init_writes_the_owner_inputs_once(tmp_path):
    import pytest
    from backend.pipeline.draft_first_essay import init_run

    init_run(tmp_path, review_id="ransom-v1", title="萬人的贖價", question="贖價是為誰付的？",
             passages=["太20:28", "提前2:6"], decision_basis="負責人：跨講道專題")
    scope = json.loads((tmp_path / "scope.json").read_text())
    selection = json.loads((tmp_path / "editorial-selection.json").read_text())
    assert scope["reader_question"] == "贖價是為誰付的？" and scope["structure_revision_id"] == selection["selection_id"]
    assert selection["focal_viewpoints"] == []
    assert EssayRun(tmp_path, runner=FakeCommands()).advance()["stage"] == "decisions"
    with pytest.raises(SystemExit):
        init_run(tmp_path, review_id="x", title="t", question="q", passages=["p"], decision_basis="b")
