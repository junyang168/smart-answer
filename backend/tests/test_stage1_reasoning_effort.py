"""What `Stage1OpenAIClient` puts on the wire, and who decides it.

The parameter under test is invisible when it is wrong: a model that accepts
`reasoning_effort` and is not sent it runs on the provider's default and
reports nothing. Measured on `kimi-k3`, one "hello", varying only this
parameter: not sent 56 reasoning tokens, `low` 8, `high` 10, `max` 124. So
these tests assert the exact kwargs, not the outcome.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.pipeline import detailed_knowledge_extraction_runner as runner
from backend.pipeline.stage1 import Stage1OpenAIClient


class _Capture:
    """Stands in for the OpenAI SDK and keeps the kwargs it was called with."""

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}
        self.chat = self
        self.completions = self

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        message = type("M", (), {"content": '{"ok": true}'})()
        choice = type("C", (), {"message": message})()
        return type("R", (), {"choices": [choice], "usage": None})()

    def with_options(self, **_: Any) -> "_Capture":
        return self


def _client(monkeypatch: pytest.MonkeyPatch, model: str, **kwargs: Any) -> tuple[Stage1OpenAIClient, _Capture]:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = Stage1OpenAIClient(model=model, **kwargs)
    capture = _Capture()
    client.client = capture  # type: ignore[assignment]
    return client, capture


def _call(client: Stage1OpenAIClient, capture: _Capture) -> dict[str, Any]:
    client.generate_json("system", "user", {"name": "s", "schema": {}}, temperature=0.0)
    return capture.kwargs


# -- the production model must not move -------------------------------------


def test_the_production_model_sends_exactly_what_it_sent_before(monkeypatch: pytest.MonkeyPatch) -> None:
    """`gpt-5.6-sol` is the only model production extracts with. Its call is
    pinned key for key: this card replaces a guess with a declaration and is
    explicitly not allowed to change any model's behaviour."""

    client, capture = _client(monkeypatch, "gpt-5.6-sol", reasoning_effort="medium", max_output_tokens=64000)
    kwargs = _call(client, capture)
    assert kwargs == {
        "model": "gpt-5.6-sol",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        "response_format": {"type": "json_schema", "json_schema": {"name": "s", "schema": {}}},
        "max_completion_tokens": 64000,
        "reasoning_effort": "medium",
    }
    assert "temperature" not in kwargs


def test_an_undeclared_non_gpt_model_still_gets_temperature_and_no_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    client, capture = _client(monkeypatch, "deepseek-v4-flash")
    kwargs = _call(client, capture)
    assert "reasoning_effort" not in kwargs
    assert kwargs["temperature"] == 0.0


# -- the declaration wins over the prefix -----------------------------------


def test_a_backend_can_declare_that_effort_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: a model that is not named `gpt-5.6` can be sent the
    parameter, without anyone editing a prefix test."""

    client, capture = _client(monkeypatch, "kimi-k3", sends_reasoning_effort=True, reasoning_effort="low")
    kwargs = _call(client, capture)
    assert kwargs["reasoning_effort"] == "low"


def test_a_backend_can_declare_that_effort_is_not_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    client, capture = _client(monkeypatch, "gpt-5.6-sol", sends_reasoning_effort=False)
    kwargs = _call(client, capture)
    assert "reasoning_effort" not in kwargs
    assert kwargs["temperature"] == 0.0


def test_effort_and_temperature_are_no_longer_mutually_exclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    """`kimi-k3` needs both -- it takes `reasoning_effort`, and it rejects any
    temperature except 1. The old `if/else` made that combination unsayable."""

    client, capture = _client(
        monkeypatch, "kimi-k3", sends_reasoning_effort=True, sends_temperature=True, reasoning_effort="high"
    )
    client.generate_json("system", "user", {"name": "s", "schema": {}}, temperature=1.0)
    assert capture.kwargs["reasoning_effort"] == "high"
    assert capture.kwargs["temperature"] == 1.0


def test_a_model_can_be_sent_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    client, capture = _client(monkeypatch, "gpt-5.6-sol", sends_reasoning_effort=False, sends_temperature=False)
    kwargs = _call(client, capture)
    assert "reasoning_effort" not in kwargs
    assert "temperature" not in kwargs


# -- the registry is what declares it ---------------------------------------


def test_the_registry_carries_the_declaration_into_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    client = runner.build_client("deepseek-v4-flash", reasoning_effort="medium", max_output_tokens=16000)
    assert client.sends_reasoning_effort is False


def test_an_undeclared_family_reaches_the_client_as_undeclared(monkeypatch: pytest.MonkeyPatch) -> None:
    """`gpt` declares nothing, because the family spans models that take the
    parameter and models that do not. Undeclared must arrive as `None` rather
    than as a `False` nobody chose."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = runner.build_client("gpt-5.6-sol", reasoning_effort="medium", max_output_tokens=64000)
    assert client.sends_reasoning_effort is None
    assert client._wants_reasoning_effort() is True
