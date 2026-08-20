from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.pipeline.model_prices import (
    PRICE_TABLES,
    price_table_for,
    price_usage,
)
from backend.pipeline.run_ledger import RunRecord, run_record


# -- pricing ---------------------------------------------------------------


def test_cached_tokens_are_not_billed_as_fresh_input():
    """`prompt_tokens` is the whole billed input, cache legs included.

    `llm_usage` deliberately adds Anthropic's two cache legs back into
    `prompt_tokens`, so pricing that field at the input rate would charge the
    cached prefix at ten times its actual cost -- on a review that is most of
    the request.
    """
    rows = [{"prompt_tokens": 100_000, "cached_tokens": 90_000, "completion_tokens": 0}]
    cost = price_usage(rows, "claude-opus-5")
    # 10k fresh at $5/M plus 90k cached at $0.50/M, not 100k at $5/M.
    assert cost.cost_usd == pytest.approx(0.05 + 0.045)
    assert cost.cost_usd < price_usage(
        [{"prompt_tokens": 100_000, "completion_tokens": 0}], "claude-opus-5"
    ).cost_usd


def test_every_attempt_is_priced_including_the_rejected_ones():
    """A package that needed three tries cost three calls."""
    one = [{"prompt_tokens": 1_000, "completion_tokens": 1_000}]
    three = one * 3
    assert price_usage(three, "claude-opus-5").cost_usd == pytest.approx(
        price_usage(one, "claude-opus-5").cost_usd * 3
    )


def test_unknown_model_costs_none_not_zero():
    """Zero would look like a free run and sum into totals as one.

    A new model id appears every few months, and the table will not know it on
    the day someone first runs it.
    """
    cost = price_usage(
        [{"prompt_tokens": 1_000, "completion_tokens": 1_000}], "gpt-6-not-yet-priced"
    )
    assert cost.cost_usd is None
    assert cost.unpriced == ("gpt-6-not-yet-priced",)
    assert not cost.complete


def test_openai_cached_input_uses_the_published_rate():
    """OpenAI publishes a cached rate outright; it is not a ratio we chose.

    Writing to an OpenAI cache is not billed at all, unlike Anthropic's
    explicit cache writes, so a run whose input is entirely cached costs a tenth
    of the fresh price rather than a tenth plus a write fee.
    """
    rows = [{"prompt_tokens": 1_000_000, "cached_tokens": 1_000_000, "completion_tokens": 0}]
    assert price_usage(rows, "gpt-5.6-sol").cost_usd == pytest.approx(0.50)


def test_the_models_this_pipeline_actually_runs_on_are_all_priced():
    """Extraction runs on gpt-5.6-sol and review on claude-sonnet-5.

    An unpriced default is not a neutral gap: it is a blank in the column the
    whole dashboard exists to fill.
    """
    table = price_table_for()
    for model_id in ("gpt-5.6-sol", "gpt-5.6-terra", "claude-sonnet-5", "claude-opus-5"):
        assert model_id in table.rates, model_id


def test_a_stage_that_calls_no_model_costs_zero():
    """Merge and ingest are free, and that is a measurement, not a gap."""
    cost = price_usage([], "claude-opus-5")
    assert cost.cost_usd == 0.0
    assert cost.complete


def test_each_usage_row_may_name_its_own_model():
    """Adjudication runs two families at two prices in one run."""
    rows = [
        {"prompt_tokens": 1_000_000, "completion_tokens": 0, "model_id": "claude-opus-5"},
        {"prompt_tokens": 1_000_000, "completion_tokens": 0, "model_id": "claude-haiku-4-5"},
    ]
    assert price_usage(rows, None).cost_usd == pytest.approx(5.00 + 1.00)


def test_price_tables_are_dated_and_do_not_overlap():
    """A run keeps the rates it was priced with; versions never edit in place."""
    versions = [table.version for table in PRICE_TABLES]
    assert len(versions) == len(set(versions))
    for earlier, later in zip(PRICE_TABLES, PRICE_TABLES[1:]):
        assert earlier.until is not None, "only the newest table may be open-ended"
        assert earlier.until < later.effective


def test_the_introductory_rate_expires_on_its_own_date():
    """Sonnet 5's intro price ends 2026-08-31; runs either side keep their own."""
    rows = [{"prompt_tokens": 1_000_000, "completion_tokens": 0}]
    during = price_usage(rows, "claude-sonnet-5", when=datetime(2026, 8, 25, tzinfo=timezone.utc))
    after = price_usage(rows, "claude-sonnet-5", when=datetime(2026, 9, 25, tzinfo=timezone.utc))
    assert during.cost_usd == pytest.approx(2.00)
    assert after.cost_usd == pytest.approx(3.00)
    assert during.price_version != after.price_version


def test_price_table_lookup_picks_by_date_not_position():
    assert price_table_for(datetime(2026, 8, 21, tzinfo=timezone.utc)).version == "2026-08-20.intro"
    assert price_table_for(datetime(2027, 1, 1, tzinfo=timezone.utc)).version == "2026-09-01.standard"


# -- recording -------------------------------------------------------------


class _Recorded(RunRecord):
    """A record that keeps its writes instead of needing a database."""

    def __init__(self, **kwargs):
        super().__init__(conn=None, **kwargs)
        self.finished: list[tuple[str, str | None]] = []

    def start(self, **kwargs) -> None:  # no row, no heartbeat thread
        pass

    def finish(self, status, error_message=None):
        self.finished.append((status, error_message))


def _capture(monkeypatch) -> list[_Recorded]:
    made: list[_Recorded] = []

    def _factory(*, run_id, subject_id, stage, subject_kind, conn):
        record = _Recorded(
            run_id=run_id, subject_id=subject_id, stage=stage, subject_kind=subject_kind
        )
        made.append(record)
        return record

    monkeypatch.setattr("backend.pipeline.run_ledger.RunRecord", _factory)
    monkeypatch.setattr("backend.pipeline.run_ledger._connect", lambda: None)
    return made


def test_a_raised_exception_is_recorded_as_failed_and_re_raised(monkeypatch):
    made = _capture(monkeypatch)
    with pytest.raises(ValueError):
        with run_record(subject="S", stage="extraction"):
            raise ValueError("sentence S0142 did not validate")
    status, detail = made[0].finished[0]
    assert status == "failed"
    # The first line is what the runs list shows unexpanded, so it has to be
    # the exception rather than the head of a traceback.
    assert detail.splitlines()[0] == "ValueError: sentence S0142 did not validate"


def test_an_interrupt_is_a_stop_not_a_success(monkeypatch):
    made = _capture(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        with run_record(subject="S", stage="extraction"):
            raise KeyboardInterrupt
    assert made[0].finished[0][0] == "cancelled"


def test_a_source_run_declares_itself_without_being_told(monkeypatch):
    """Otherwise it would vanish from its own row while appearing in the runs list."""
    _capture(monkeypatch)
    record = RunRecord(
        run_id="RUN-x", subject_id="2016 NYSC 專題：馬太福音釋經（四）3",
        stage="extraction", subject_kind="source", conn=None,
    )
    assert record._recorded_sources() == ["2016 NYSC 專題：馬太福音釋經（四）3"]


def test_an_article_run_records_every_source_it_cites():
    """The Matt 16:13-20 article cites eight; the overview projects it onto all of them."""
    record = RunRecord(
        run_id="RUN-y", subject_id="DRAFT-M16-002-V1", stage="article",
        subject_kind="draft", conn=None,
    )
    record.sources(["A", "B", "A"])
    assert record._recorded_sources() == ["A", "B"]


def test_a_draft_run_does_not_invent_itself_as_a_source():
    record = RunRecord(
        run_id="RUN-z", subject_id="DRAFT-M16-002-V1", stage="article",
        subject_kind="draft", conn=None,
    )
    assert record._recorded_sources() == []


def test_missing_database_does_not_break_the_work(monkeypatch):
    """Ten minutes of extraction must not be lost to a bookkeeping outage."""
    monkeypatch.delenv("KNOWLEDGE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with run_record(subject="S", stage="extraction") as record:
        record.usage([{"prompt_tokens": 1, "completion_tokens": 1}])
        assert not record.recording
        assert record.cancel_requested() is False


def test_unknown_stage_is_rejected():
    with pytest.raises(ValueError):
        with run_record(subject="S", stage="transcribe"):
            pass
