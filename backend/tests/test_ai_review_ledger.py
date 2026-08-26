"""The AI review verdicts must reach the store without inventing approval."""

from __future__ import annotations

import json
import os
from pathlib import Path

from backend.pipeline.ai_review_ledger import (
    build_plan,
    collect_verdicts,
    review_reason,
)


def _artifact(path: Path, rows: list[dict], reviewer: dict | None = None) -> Path:
    payload: dict = {
        "schema_version": "wang_corpus_independent_review_v1",
        "claim_reviews": rows,
    }
    if reviewer is not None:
        payload["reviewer"] = reviewer
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_pass_becomes_ai_consensus_reviewed_and_the_rest_goes_to_a_person(tmp_path):
    _artifact(
        tmp_path / "review.json",
        [
            {"claim_id": "DK-a-CL001", "decision": "pass", "rationale": "锚点足够"},
            {"claim_id": "DK-a-CL002", "decision": "changes_suggested", "rationale": "过宽"},
            {"claim_id": "DK-a-CL003", "decision": "human_review_required", "rationale": "分歧"},
        ],
    )
    verdicts = collect_verdicts(tmp_path)
    plan = build_plan(
        {"DK-a-CL001": "candidate", "DK-a-CL002": "candidate", "DK-a-CL003": "candidate"},
        verdicts,
    )
    assert plan.summary()["changes_by_status"] == {
        "ai_consensus_reviewed": 1,
        "human_review_required": 2,
    }


def test_changes_suggested_never_counts_as_reviewed_and_fine():
    """The reviewer asked for a change; the artifact does not say it was made.

    Reading `changes_suggested` as "reviewed, fine as it stands" would have
    promoted 86 claims on the strength of a verdict that said the opposite.
    """

    from backend.pipeline.ai_review_ledger import VERDICT_STATUS

    assert VERDICT_STATUS["changes_suggested"] == "human_review_required"
    assert VERDICT_STATUS["pass"] != "approved"
    assert "approved" not in set(VERDICT_STATUS.values())
    assert "human_approved" not in set(VERDICT_STATUS.values())


def test_a_human_decision_is_never_overwritten(tmp_path):
    _artifact(
        tmp_path / "review.json",
        [{"claim_id": "DK-a-CL001", "decision": "pass", "rationale": "ok"}],
    )
    plan = build_plan({"DK-a-CL001": "approved"}, collect_verdicts(tmp_path))
    assert plan.changes == []
    assert plan.unchanged == ["DK-a-CL001"]


def test_a_claim_with_no_verdict_stays_candidate(tmp_path):
    _artifact(
        tmp_path / "review.json",
        [{"claim_id": "DK-a-CL001", "decision": "pass"}],
    )
    plan = build_plan(
        {"DK-a-CL001": "candidate", "DK-a-CL002": "candidate"}, collect_verdicts(tmp_path)
    )
    assert plan.no_verdict == ["DK-a-CL002"]
    assert [change.claim_id for change in plan.changes] == ["DK-a-CL001"]


def test_an_unrecognised_verdict_is_counted_not_guessed(tmp_path):
    _artifact(
        tmp_path / "review.json",
        [{"claim_id": "DK-a-CL001", "decision": "looks_fine_to_me"}],
    )
    plan = build_plan({"DK-a-CL001": "candidate"}, collect_verdicts(tmp_path))
    assert plan.changes == []
    assert plan.unknown_verdicts == {"looks_fine_to_me": 1}


def test_rerunning_changes_nothing(tmp_path):
    _artifact(
        tmp_path / "review.json",
        [{"claim_id": "DK-a-CL001", "decision": "pass"}],
    )
    verdicts = collect_verdicts(tmp_path)
    assert build_plan({"DK-a-CL001": "candidate"}, verdicts).changes != []
    assert build_plan({"DK-a-CL001": "ai_consensus_reviewed"}, verdicts).changes == []


def test_a_regenerated_review_supersedes_the_one_it_replaced(tmp_path):
    old = _artifact(
        tmp_path / "review.json",
        [{"claim_id": "DK-a-CL001", "decision": "pass"}],
    )
    new = _artifact(
        tmp_path / "generations" / "review.abc123.json",
        [{"claim_id": "DK-a-CL001", "decision": "human_review_required"}],
    )
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))
    verdicts = collect_verdicts(tmp_path)
    assert verdicts["DK-a-CL001"].decision == "human_review_required"


def test_the_reason_names_the_artifact_that_justified_it(tmp_path):
    _artifact(
        tmp_path / "review.json",
        [{"claim_id": "DK-a-CL001", "decision": "pass", "rationale": "锚点足够"}],
        reviewer={"model": "claude-opus-5"},
    )
    verdict = collect_verdicts(tmp_path)["DK-a-CL001"]
    reason = review_reason(verdict)
    assert "review.json" in reason
    assert verdict.artifact_sha256[:12] in reason
    assert verdict.reviewer_id == "claude-opus-5"


def test_an_unreadable_artifact_is_skipped_not_fatal(tmp_path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    _artifact(
        tmp_path / "review.json",
        [{"claim_id": "DK-a-CL001", "decision": "pass"}],
    )
    assert set(collect_verdicts(tmp_path)) == {"DK-a-CL001"}
