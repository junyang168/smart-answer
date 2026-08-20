"""One shape for what a model call cost, wherever the call was made.

Extraction reported its tokens and review did not, so the only stage whose
price was known was the one nobody was asking about.  Both now write the same
row, which is what makes "review costs N times extraction" a measurement
rather than an estimate.
"""

from __future__ import annotations

from typing import Any


def usage_row(usage: Any, attempt: int) -> dict[str, Any]:
    """Flatten one call's token usage, tolerating the SDK's optional fields.

    `cached_tokens` is the number worth watching: the source text is sent as a
    cached prefix precisely so a validation retry re-reads it instead of paying
    for it again, and this is the only evidence that it happens.
    """

    # The two SDKs name these differently, and reading only OpenAI's names left
    # every Anthropic run reporting zero — which hid the cost of the model this
    # pipeline now defaults to.
    details = getattr(usage, "prompt_tokens_details", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    cached = getattr(details, "cached_tokens", None)
    written = None
    if prompt_tokens is None:
        # Anthropic keeps the two cache legs out of `input_tokens` entirely, so
        # reading that field alone reported a 50k-token review as 1k of input.
        # Both are billed input; add them back.
        written = getattr(usage, "cache_creation_input_tokens", None)
        cached = getattr(usage, "cache_read_input_tokens", None)
        prompt_tokens = sum(
            value or 0
            for value in (getattr(usage, "input_tokens", None), written, cached)
        )
        completion_tokens = getattr(usage, "output_tokens", None)
    total = getattr(usage, "total_tokens", None)
    if total is None and prompt_tokens is not None and completion_tokens is not None:
        total = prompt_tokens + completion_tokens
    return {
        "attempt": attempt,
        "prompt_tokens": prompt_tokens,
        "cached_tokens": cached,
        "cache_write_tokens": written,
        "completion_tokens": completion_tokens,
        "total_tokens": total,
    }


def usage_summary(label: str, usage_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Add up the rows of one unit of work — a source, or one review."""

    prompt = sum(row["prompt_tokens"] or 0 for row in usage_rows)
    cached = sum(row["cached_tokens"] or 0 for row in usage_rows)
    return {
        "usage": label,
        "calls": len(usage_rows),
        "total_tokens": sum(row["total_tokens"] or 0 for row in usage_rows),
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "cache_hit": f"{100 * cached / prompt:.0f}%" if prompt else "n/a",
        "completion_tokens": sum(row["completion_tokens"] or 0 for row in usage_rows),
    }
