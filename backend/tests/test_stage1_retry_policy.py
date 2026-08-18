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


class _Message:
    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self.content = content
        self.usage = None


class _Text:
    type = "text"

    def __init__(self, text):
        self.text = text


def _post(client, message):
    """Run the response-handling half of `_post_chat_completion`."""

    # Below STREAMING_OUTPUT_THRESHOLD so the non-streaming branch runs; the
    # response handling under test is shared by both.
    client.max_output_tokens = 8000
    client.client = type("C", (), {"messages": type("M", (), {"create": staticmethod(lambda **_: message)})()})()
    client.model = "claude-sonnet-5"
    client.timeout_seconds = 60.0
    client.system_cache_ttl = "1h"
    client.prefix_cache_ttl = "5m"
    return client._post_chat_completion(system_prompt="s", user_prompt="u", temperature=0.0)


def test_a_truncated_answer_is_an_overflow_not_transport_noise() -> None:
    """Regression: the budget check only fired when the model emitted no text
    at all. A review that ran out mid-string came back as half a JSON document,
    the caller raised a parse error, and the retry loop sent the same oversized
    request twice more before giving up.
    """

    client = _client(3)
    with pytest.raises(OutputBudgetExceeded, match="mid-answer"):
        _post(client, _Message("max_tokens", [_Text('{"partial": "cut off here')]))


def test_an_empty_answer_still_names_the_budget() -> None:
    client = _client(3)
    with pytest.raises(OutputBudgetExceeded, match="without emitting any text"):
        _post(client, _Message("max_tokens", []))


def test_a_complete_answer_is_returned() -> None:
    client = _client(3)
    assert _post(client, _Message("end_turn", [_Text('{"ok": true}')])) == '{"ok": true}'
