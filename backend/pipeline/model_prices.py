"""What a run cost, in dollars, from the tokens it reported.

`llm_usage` made both SDKs report the same usage shape, which is what makes a
price computable at all.  It stops at tokens, though, and tokens are not the
question anyone asks -- "131 sermons through review" needs an amount, and the
only way to get one today is to read a vendor invoice a month later.

Two properties matter more than the numbers themselves:

**Prices are dated and never edited in place.**  A run priced last month keeps
the rates it was priced with.  `PRICE_TABLES` is append-only: a rate change adds
a version, and `pipeline_runs.price_version` records which one priced each row,
so a later change cannot silently rewrite what history cost.  Sonnet 5's
introductory rate is the worked case -- it expires 2026-08-31, and runs on
either side of that date must keep their own price.

**An unknown model costs `None`, never zero.**  A new model id appears every few
months, and a price table that guesses at one produces a number with no source.
`None` propagates to a NULL `cost_usd` and a visible warning; 0 would look like
a free run and sum into totals as if it were one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Optional


#: Reading a cached prefix costs about a tenth of fresh input, and writing one
#: costs about a quarter more than fresh input.  Both are ratios of the model's
#: own input rate rather than separate constants, because that is how the
#: vendors publish them and how they have moved when a model's price changed.
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25


@dataclass(frozen=True)
class ModelRate:
    """Dollars per million tokens for one model.

    `cache_read` and `cache_write` are derived from `input` unless a vendor
    publishes something that is not the usual ratio, in which case they are
    given explicitly.
    """

    input: float
    output: float
    cache_read: Optional[float] = None
    cache_write: Optional[float] = None

    def read_rate(self) -> float:
        return self.input * CACHE_READ_MULTIPLIER if self.cache_read is None else self.cache_read

    def write_rate(self) -> float:
        return self.input * CACHE_WRITE_MULTIPLIER if self.cache_write is None else self.cache_write


@dataclass(frozen=True)
class PriceTable:
    version: str
    effective: date
    #: Ends the day this table stops applying, or None while it is current.
    until: Optional[date]
    source: str
    rates: Mapping[str, ModelRate]

    def covers(self, when: date) -> bool:
        if when < self.effective:
            return False
        return self.until is None or when <= self.until


#: Append-only.  Newest last; `price_table_for` picks by date, not by position.
#:
#: Anthropic rates are first-party API list prices.  The OpenAI models this
#: pipeline defaults to (`gpt-5.6-sol`, `gpt-5.6-terra`) are deliberately absent
#: rather than estimated -- extraction runs on `gpt-5.6-sol`, so this is the
#: expensive gap, and an invented number there would be worse than a blank:
#: it would be the headline figure on the overview and nobody would know it was
#: a guess.  Add them with their published rates and the tables become complete;
#: until then those runs record NULL and say so.
PRICE_TABLES: tuple[PriceTable, ...] = (
    PriceTable(
        version="2026-08-20.intro",
        effective=date(2026, 8, 20),
        # Sonnet 5 carries an introductory rate through 2026-08-31. Review runs
        # on Sonnet 5, so a table that ignored the window would misprice every
        # review taken this month by 50%.
        until=date(2026, 8, 31),
        source="Anthropic first-party API list prices; Sonnet 5 introductory rate",
        rates={
            "claude-opus-5": ModelRate(input=5.00, output=25.00),
            "claude-sonnet-5": ModelRate(input=2.00, output=10.00),
            "claude-haiku-4-5": ModelRate(input=1.00, output=5.00),
        },
    ),
    PriceTable(
        version="2026-09-01.standard",
        effective=date(2026, 9, 1),
        until=None,
        source="Anthropic first-party API list prices; Sonnet 5 standard rate",
        rates={
            "claude-opus-5": ModelRate(input=5.00, output=25.00),
            "claude-sonnet-5": ModelRate(input=3.00, output=15.00),
            "claude-haiku-4-5": ModelRate(input=1.00, output=5.00),
        },
    ),
)

#: Model ids this pipeline runs on that no table prices yet.  Named explicitly
#: so "we have not entered this price" reads differently from "this model id is
#: a typo" -- the first is a known gap, the second is a bug.
UNPRICED_MODELS = ("gpt-5.6-sol", "gpt-5.6-terra")


def price_table_for(when: Optional[datetime] = None) -> PriceTable:
    """The table in force on a date, defaulting to the newest one."""

    moment = (when or datetime.now(timezone.utc)).date()
    for table in reversed(PRICE_TABLES):
        if table.covers(moment):
            return table
    # Before the first table exists there is nothing honest to say, so price
    # with the earliest one and let `price_version` record that it was applied
    # outside its window rather than refuse to record the run at all.
    return PRICE_TABLES[0]


@dataclass(frozen=True)
class RunCost:
    """The priced result of one run's usage rows."""

    cost_usd: Optional[float]
    price_version: str
    #: Model ids in these rows that the table could not price.  Non-empty means
    #: `cost_usd` is either None or an undercount, and callers must say so.
    unpriced: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.cost_usd is not None and not self.unpriced


def _row_tokens(row: Mapping[str, Any]) -> tuple[int, int, int, int]:
    """Split one usage row into (fresh input, cache read, cache write, output).

    `llm_usage.usage_row` reports `prompt_tokens` as the whole billed input --
    for Anthropic it deliberately adds the two cache legs back in, because
    reading `input_tokens` alone reported a 50k-token review as 1k.  So the
    fresh portion is what is left after removing the legs, and subtracting them
    here is what keeps cached tokens from being billed at the full rate.
    """

    prompt = int(row.get("prompt_tokens") or 0)
    cached = int(row.get("cached_tokens") or 0)
    written = int(row.get("cache_write_tokens") or 0)
    completion = int(row.get("completion_tokens") or 0)
    fresh = max(prompt - cached - written, 0)
    return fresh, cached, written, completion


def price_usage(
    usage_rows: Iterable[Mapping[str, Any]],
    model_id: Optional[str],
    *,
    when: Optional[datetime] = None,
    table: Optional[PriceTable] = None,
) -> RunCost:
    """Price every call a run made, including the attempts that were rejected.

    A package that needed three tries cost three calls.  `usage_rows` carries
    all of them for exactly that reason, so this sums the rows rather than
    trusting a summary.
    """

    table = table or price_table_for(when)
    rows = list(usage_rows or [])
    if not rows:
        # A stage that calls no model -- merge, ingest -- costs zero, and zero
        # is a fact about it.  NULL is for "nobody measured".
        return RunCost(cost_usd=0.0, price_version=table.version, unpriced=())

    total = 0.0
    unpriced: list[str] = []
    priced_any = False
    for row in rows:
        row_model = str(row.get("model_id") or model_id or "")
        rate = table.rates.get(row_model)
        if rate is None:
            if row_model not in unpriced:
                unpriced.append(row_model)
            continue
        fresh, cached, written, completion = _row_tokens(row)
        total += (
            fresh * rate.input
            + cached * rate.read_rate()
            + written * rate.write_rate()
            + completion * rate.output
        ) / 1_000_000
        priced_any = True

    if not priced_any:
        # Every call was on an unpriced model. Reporting 0.0 here would put a
        # free-looking run in the ledger and add nothing to the day's total,
        # which is how a cost cap gets quietly defeated.
        return RunCost(cost_usd=None, price_version=table.version, unpriced=tuple(unpriced))
    return RunCost(
        cost_usd=round(total, 4),
        price_version=table.version,
        unpriced=tuple(unpriced),
    )
