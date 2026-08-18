from __future__ import annotations

import pytest

from backend.pipeline.stage1 import OutputBudgetExceeded, Stage1AnthropicClient


def _client(max_retries: int) -> Stage1AnthropicClient:
    """A client without the constructor's API key and SDK setup.

    `_with_retries` reads only `max_retries` and `_format_exception`, so the
    retry policy can be exercised without a network or a key.
    """

    client = object.__new__(Stage1AnthropicClient)
    client.max_retries = max_retries
    return client


def test_an_output_budget_overflow_is_not_retried() -> None:
    """Regression: a review that spent its whole `max_tokens` thinking and
    emitted no text was retried three times. The request is identical each
    time, so it overflows identically -- and every attempt is billed for the
    full input and the full output budget it burned. One run cost three times
    67k input tokens and eight minutes to arrive at the same failure.
    """

    attempts = 0

    def overflow():
        nonlocal attempts
        attempts += 1
        raise OutputBudgetExceeded("spent the whole budget thinking")

    with pytest.raises(OutputBudgetExceeded):
        _client(3)._with_retries(overflow)
    assert attempts == 1


def test_an_ordinary_failure_is_still_retried() -> None:
    attempts = 0

    def flaky():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("Anthropic API error 529 overloaded")
        return {"ok": True}

    client = _client(3)
    # The backoff sleeps between attempts; the policy is what is under test.
    import backend.pipeline.stage1 as stage1

    slept: list[float] = []
    original = stage1.time.sleep
    stage1.time.sleep = slept.append
    try:
        assert client._with_retries(flaky) == {"ok": True}
    finally:
        stage1.time.sleep = original
    assert attempts == 3
    assert slept, "an overloaded response should back off before retrying"


def test_output_budget_exceeded_is_a_runtime_error() -> None:
    """Callers that already catch RuntimeError -- the grounding check turns one
    into a finding rather than ending the run -- must keep catching this.
    """

    assert issubclass(OutputBudgetExceeded, RuntimeError)
