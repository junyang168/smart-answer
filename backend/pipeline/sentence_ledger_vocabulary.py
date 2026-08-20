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
AUTO_TERMINAL_REASONS = frozenset({"duplicate_of"})
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
