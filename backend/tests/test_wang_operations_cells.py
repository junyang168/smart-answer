"""What a cell says, when the thing it reports on has moved underneath it.

Written after a source read fully green on the overview while the authoring
store still held the extraction it had two weeks earlier. Every stage had in
fact re-run; the 入庫 cell fell back to "the store holds this source" and
skipped the staleness check that every other cell applies. Green meant "done"
to the person reading it, and the claim layer was the old one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.api.wang_operations import _as_datetime, _cell

NOW = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)


def _run(stage: str, *, finished: datetime, status: str = "succeeded", **quality):
    return {
        "stage": stage, "effective_status": status, "status": status,
        "finished_at": finished, "started_at": finished, "quality": quality,
        "run_id": "RUN-x", "trigger": "cli", "triggered_by": None,
        "model_id": None, "cost_usd": None, "error_message": None,
        "input_sha256": {}, "seconds": 0,
    }


def test_a_stage_is_stale_when_its_input_ran_again_afterwards() -> None:
    cell = _cell(
        [_run("review", finished=NOW - timedelta(days=7))],
        stage="review",
        current_source_sha=None,
        upstream_finished=NOW,
    )
    assert cell["state"] == "stale"
    assert cell["reason"] == "upstream_rerun"


def test_a_stage_is_current_when_nothing_upstream_moved() -> None:
    cell = _cell(
        [_run("review", finished=NOW)],
        stage="review",
        current_source_sha=None,
        upstream_finished=NOW - timedelta(days=1),
    )
    assert cell["state"] == "current"


def test_extraction_without_a_recorded_input_cannot_claim_freshness() -> None:
    cell = _cell(
        [_run("extraction", finished=NOW)],
        stage="extraction",
        current_source_sha="abc",
        upstream_finished=None,
    )
    assert cell["state"] == "stale"
    assert cell["reason"] == "no_recorded_input"


def test_as_datetime_reads_a_stored_timestamp_and_survives_a_bad_one() -> None:
    assert _as_datetime("2026-08-13T18:42:00+00:00") == datetime(
        2026, 8, 13, 18, 42, tzinfo=timezone.utc
    )
    # A naive timestamp is assumed UTC rather than compared against an aware
    # one, which would raise and take the whole overview down.
    naive = _as_datetime("2026-08-13T18:42:00")
    assert naive is not None and naive.tzinfo is not None
    assert _as_datetime(None) is None
    assert _as_datetime("not a date") is None
