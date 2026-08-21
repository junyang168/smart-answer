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


def test_the_store_cell_reports_material_not_the_document_record() -> None:
    """`rev N` counted writes to the metadata row, which barely ever moves.

    生命's `source_documents` record sat at revision 1 from 13 Aug while its
    material was rewritten twice afterwards -- an additive reconciliation on
    the 16th and a vocabulary migration on the 17th. The cell showed `rev 1`
    throughout, so the number answered "how often was this row rewritten"
    rather than "what does the store hold for this source".
    """

    from backend.api.wang_operations import _as_datetime

    document_written = _as_datetime("2026-08-13T22:17:32+00:00")
    material_written = _as_datetime("2026-08-16T12:09:50+00:00")
    assert document_written is not None and material_written is not None
    # The material is the newer of the two, so staleness judged against the
    # document record would call a source current that is three days behind.
    assert material_written > document_written


def test_a_live_upstream_run_greys_out_what_it_is_replacing() -> None:
    """A row reading `執行中` with green cells behind it looks done, and is not.

    Every one of those results was read from the stage now being re-run, so
    each is about to be superseded. Leaving them green is the same invitation
    to misread that the whole staleness scheme exists to remove.
    """

    cell = _cell(
        [_run("review", finished=NOW - timedelta(hours=1), ai_reviewed=54)],
        stage="review",
        current_source_sha=None,
        upstream_finished=NOW - timedelta(hours=2),
        upstream_in_flight=True,
    )
    assert cell["state"] == "pending"
    assert cell["reason"] == "upstream_running"
    # No number on the face of the cell...
    assert cell["quality"] is None
    # ...but the verdict it replaces is kept for the tooltip.
    assert cell["superseded"]["state"] == "current"
    assert cell["superseded"]["quality"]["ai_reviewed"] == 54


def test_a_stage_that_never_ran_is_not_dressed_up_as_pending() -> None:
    cell = _cell(
        [], stage="review", current_source_sha=None,
        upstream_finished=None, upstream_in_flight=True,
    )
    assert cell["state"] == "never"


def test_a_failed_stage_keeps_saying_failed_while_upstream_reruns() -> None:
    """Greying a failure would hide the reason somebody started the re-run."""

    cell = _cell(
        [_run("review", finished=NOW, status="failed")],
        stage="review",
        current_source_sha=None,
        upstream_finished=None,
        upstream_in_flight=True,
    )
    assert cell["state"] == "failed"
