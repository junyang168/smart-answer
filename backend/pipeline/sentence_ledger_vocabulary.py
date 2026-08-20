"""Reason codes for excluding a sentence, and what each one costs to accept.

Split out of `sentence_ledger` so extraction can name the same vocabulary
without importing the canonical-repository models, which pull in the web
application. One list, two readers.

The tiers follow what can be *checked*, not what the sentence looks like:
`duplicate_of` names a record that either covers the content or does not, while
`background_only` is the interpretive call that failed in #64 and #53.
"""

from __future__ import annotations

#: Terminal without a human, because a machine can verify them.
#:
#: `structural_markup` is the source's own scaffolding -- a Markdown heading, a
#: list bullet. Whether a line is `## 一、Wrede彌賽亞秘密理論` is decided by a
#: regex, not by judgement, and 51 of one manuscript's 64 unaccounted sentences
#: were section titles. Routing them through `not_exegesis` put the professor's
#: table of contents in the same review queue as prose somebody had actually
#: read and set aside, which buried the three sentences that deserved the look.
AUTO_TERMINAL_REASONS = frozenset({"duplicate_of", "structural_markup"})
#: A person may approve these in bulk; they are not terminal on their own.
BULK_APPROVABLE_REASONS = frozenset({"not_exegesis"})
#: Never terminal without a person, however unremarkable the sentence looks.
HUMAN_ONLY_REASONS = frozenset({"background_only", "deferred"})
REASON_CODES = AUTO_TERMINAL_REASONS | BULK_APPROVABLE_REASONS | HUMAN_ONLY_REASONS


def is_terminal(reason_code: str, *, approved: bool) -> bool:
    """Whether a candidate exclusion counts without a human looking at it."""

    if reason_code not in REASON_CODES:
        raise ValueError(f"unknown reason_code: {reason_code!r}")
    return True if approved else reason_code in AUTO_TERMINAL_REASONS
