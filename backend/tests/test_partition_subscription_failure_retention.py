import subprocess
from pathlib import Path

import pytest

from backend.pipeline.codex_subscription_client import CodexSubscriptionClient, CodexSubscriptionError
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient, ClaudeSubscriptionError


def test_codex_preserves_malformed_last_message_before_temp_cleanup(monkeypatch):
    def run(command, **kwargs):
        Path(command[command.index("--output-last-message") + 1]).write_text("{broken")
        return subprocess.CompletedProcess(command, 0, "raw stdout", "raw stderr")
    monkeypatch.setattr("backend.pipeline.codex_subscription_client.subprocess.run", run)
    client = CodexSubscriptionClient(model="gpt-6.1-sol")
    client._authenticated = True
    with pytest.raises(CodexSubscriptionError):
        client.generate_json("instruction", "input", {"type": "object"})
    assert client.last_raw_response == {
        "stdout": "raw stdout", "stderr": "raw stderr", "last_message": "{broken"}


def test_claude_preserves_malformed_envelope(monkeypatch):
    monkeypatch.setattr("backend.pipeline.claude_subscription_client.subprocess.run",
                        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "bad envelope", "stderr"))
    client = ClaudeSubscriptionClient(model="claude-opus-5-5")
    client._authenticated = True
    with pytest.raises(ClaudeSubscriptionError):
        client.generate_json("instruction", "input", {"type": "object"})
    assert client.last_raw_response == {"stdout": "bad envelope", "stderr": "stderr"}
