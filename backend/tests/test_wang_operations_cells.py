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


def test_applied_body_coordinate_migration_proves_legacy_run_is_current() -> None:
    run = _run("extraction", finished=NOW)
    run["input_sha256"] = {"source_sha256": "legacy-raw-sha"}
    cell = _cell(
        [run], stage="extraction", current_source_sha="body-sha",
        current_store_source_sha="body-sha", upstream_finished=None,
    )
    assert cell["state"] == "current"


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


def test_cancelled_attempt_does_not_overwrite_successful_lineage() -> None:
    success = _run("extraction", finished=NOW - timedelta(hours=1))
    success["run_id"] = "RUN-success"
    success["input_sha256"] = {"source_sha256": "body-sha"}
    cancelled = _run("extraction", finished=NOW, status="cancelled")
    cancelled["run_id"] = "RUN-cancelled"

    cell = _cell(
        [success, cancelled], stage="extraction", current_source_sha="body-sha",
        upstream_finished=None,
    )
    assert cell["state"] == "current"
    assert cell["run"]["run_id"] == success["run_id"]


def test_no_output_extraction_timeout_does_not_overwrite_migrated_lineage() -> None:
    success = _run("extraction", finished=NOW - timedelta(hours=1))
    success["run_id"] = "RUN-success"
    success["input_sha256"] = {"source_sha256": "legacy-raw-sha"}
    failed = _run("extraction", finished=NOW, status="failed")
    failed.update({
        "run_id": "RUN-timeout",
        "input_sha256": {"source_sha256": "body-sha"},
        "output_paths": [],
        "error_message": (
            "CodexSubscriptionError: Codex subscription transport failed: "
            "TimeoutExpired: command timed out"
        ),
    })

    cell = _cell(
        [success, failed], stage="extraction", current_source_sha="body-sha",
        current_store_source_sha="body-sha", upstream_finished=None,
    )

    assert cell["state"] == "current"
    assert cell["run"]["run_id"] == "RUN-success"
    assert cell["failed_transport_attempt"]["run_id"] == "RUN-timeout"


def test_other_failed_extraction_still_overwrites_successful_lineage() -> None:
    success = _run("extraction", finished=NOW - timedelta(hours=1))
    failed = _run("extraction", finished=NOW, status="failed")
    failed.update({
        "input_sha256": {"source_sha256": "body-sha"},
        "output_paths": [],
        "error_message": "schema validation failed",
    })

    cell = _cell(
        [success, failed], stage="extraction", current_source_sha="body-sha",
        current_store_source_sha="body-sha", upstream_finished=None,
    )

    assert cell["state"] == "failed"


def test_rejected_obsolete_ingest_does_not_overwrite_successful_lineage() -> None:
    success = _run("ingest", finished=NOW - timedelta(days=10))
    success["run_id"] = "RUN-success"
    failed = _run("ingest", finished=NOW, status="failed")
    failed.update(
        {
            "run_id": "RUN-obsolete",
            "output_paths": [],
            "error_message": (
                "ChangeSetConflict: claims/CL-old was retired at 2026-08-20; "
                "re-ingesting the package that produced it would bring it back."
            ),
        }
    )

    cell = _cell(
        [success, failed],
        stage="ingest",
        current_source_sha=None,
        upstream_finished=success["finished_at"] - timedelta(seconds=1),
    )

    assert cell["state"] == "current"
    assert cell["run"]["run_id"] == "RUN-success"
    assert cell["rejected_obsolete_attempt"]["run_id"] == "RUN-obsolete"
    assert cell["rejected_obsolete_attempt"]["status"] == "failed"


def test_other_failed_ingest_still_overwrites_successful_lineage() -> None:
    success = _run("ingest", finished=NOW - timedelta(hours=1))
    failed = _run("ingest", finished=NOW, status="failed")
    failed["error_message"] = "database unavailable"

    cell = _cell(
        [success, failed],
        stage="ingest",
        current_source_sha=None,
        upstream_finished=None,
    )

    assert cell["state"] == "failed"
    assert cell["had_earlier_success"] is True


def test_a_run_that_names_no_module_and_wrote_nothing_is_not_evidence() -> None:
    """A 母本 whose extraction has succeeded three times was reading 失敗.

    The failure came from a fourth-model benchmark in a neighbouring worktree.
    It died before writing anything, so there was no output path to place it
    outside the canonical tree, and `run_ledger` had recorded its command as
    `-` -- what `sys.argv` holds when the code arrives on stdin, which is how
    that comparison is driven. Nothing tied it to a pipeline entry point and
    nothing tied it to a directory, yet it was the newest extraction and so it
    spoke for the source.
    """

    from pathlib import Path

    from backend.api.wang_operations import is_scratch_run

    root = Path("/data/staging/claim-layer")

    heredoc = {"output_paths": [], "command": "-"}
    assert is_scratch_run(heredoc, root)

    # A real run always names the module it was started with.
    real = {
        "output_paths": [],
        "command": "detailed_knowledge_extraction_runner.py --output-dir /data/staging/claim-layer/x",
    }
    assert not is_scratch_run(real, root)

    # And one writing outside the tree stays scratch however it was started.
    elsewhere = {
        "output_paths": [],
        "command": "detailed_knowledge_extraction_runner.py --output-dir /tmp/bench/out",
    }
    assert is_scratch_run(elsewhere, root)

    # An output inside the tree is the strongest evidence and wins outright.
    produced = {"output_paths": ["/data/staging/claim-layer/pkg.json"], "command": "-"}
    assert not is_scratch_run(produced, root)
