import hashlib
import subprocess

import pytest

from backend.pipeline import codex_subscription_client as m
from backend.pipeline.codex_subscription_preflight import verify_runtime


@pytest.mark.parametrize("version", ["0.144.0-alpha.4", "0.159.1", "0.159.2-alpha.1"])
def test_stale_binary_rejected_before_auth_or_model_not_account_failure(tmp_path, monkeypatch, version):
    path = tmp_path / "codex"
    path.write_bytes(b"old binary")
    monkeypatch.setattr(m.shutil, "which", lambda *a, **k: str(path))
    calls = []
    def run(argv, **kw):
        calls.append(argv)
        assert argv[-1] == "--version"
        return subprocess.CompletedProcess(argv, 0, "codex-cli " + version + "\n", "")
    monkeypatch.setattr(m.subprocess, "run", run)
    client = m.CodexSubscriptionClient(model="gpt-6.1-sol", executable=str(path))
    with pytest.raises(m.CodexSubscriptionError, match="client configuration failure"):
        client._verify_chatgpt_login()
    assert len(calls) == 1
    assert client.cli_identity is None


@pytest.mark.parametrize("version", ["0.159.2", "0.160.0"])
def test_current_binary_identity_then_chatgpt_login_same_executable(tmp_path, monkeypatch, version):
    path = tmp_path / "codex"
    path.write_bytes(b"current binary")
    monkeypatch.setattr(m.shutil, "which", lambda *a, **k: str(path))
    calls = []
    def run(argv, **kw):
        calls.append(argv)
        assert argv[0] == str(path)
        assert "OPENAI_API_KEY" not in kw["env"]
        response = "codex-cli " + version if argv[-1] == "--version" else "Logged in using ChatGPT"
        return subprocess.CompletedProcess(argv, 0, response, "")
    monkeypatch.setattr(m.subprocess, "run", run)
    client = m.CodexSubscriptionClient(model="gpt-6.1-sol", executable=str(path), environment={"OPENAI_API_KEY": "forbidden"})
    client._verify_chatgpt_login()
    assert client.cli_identity["file_sha256"] == hashlib.sha256(b"current binary").hexdigest()
    assert client.executable == str(path)
    assert len(calls) == 2
    client._verify_chatgpt_login()
    assert len(calls) == 2


@pytest.mark.parametrize("result", [{"ok": False}, {}, {"ok": True, "other": 1}])
def test_probe_response_must_be_exact_and_no_fallback(monkeypatch, result):
    calls = []
    client = m.CodexSubscriptionClient(model="gpt-6.1-sol", reasoning_effort="high")
    monkeypatch.setattr(client, "generate_json", lambda *a, **kw: calls.append(kw) or result)
    with pytest.raises(m.CodexSubscriptionError, match="diagnostic"):
        client.verify_model_access()
    assert len(calls) == 1
    assert calls[0]["timeout_seconds"] == 60


def test_probe_failure_not_retried_or_replaced_with_other_model(monkeypatch):
    client = m.CodexSubscriptionClient(model="gpt-6.1-sol", reasoning_effort="high")
    calls = []
    def fail(*a, **k):
        calls.append(client.model)
        raise m.CodexSubscriptionError("quota or transport")
    monkeypatch.setattr(client, "generate_json", fail)
    with pytest.raises(m.CodexSubscriptionError, match="quota"):
        client.verify_model_access()
    assert calls == ["gpt-6.1-sol"]


def test_probe_success_explicit_separate_from_corpus_dryrun(monkeypatch):
    client = m.CodexSubscriptionClient(model="gpt-6.1-sol", reasoning_effort="high")
    client.cli_identity = {"path": "/current/codex", "version": "codex-cli 0.159.2", "file_sha256": "sha"}
    monkeypatch.setattr(client, "generate_json", lambda *a, **k: {"ok": True})
    assert client.verify_model_access() == {
        "status": "verified", "backend": "codex_subscription", "model": "gpt-6.1-sol",
        "reasoning_effort": "high", "cli": client.cli_identity, "model_calls": 1, "corpus_writes": 0}


def test_missing_or_unparseable_version_never_infers_account_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(m.shutil, "which", lambda *a, **k: None)
    with pytest.raises(m.CodexSubscriptionError, match="executable unavailable"):
        m.verify_cli_identity("missing", "gpt-6.1-sol", {})
    path = tmp_path / "codex"
    path.write_bytes(b"binary")
    monkeypatch.setattr(m.shutil, "which", lambda *a, **k: str(path))
    monkeypatch.setattr(m.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "unknown", ""))
    with pytest.raises(m.CodexSubscriptionError, match="Cannot verify"):
        m.verify_cli_identity(str(path), "gpt-6.1-sol", {})


def test_runtime_proof_reused_without_model_and_invalidated_by_binary_or_login(tmp_path, monkeypatch):
    home = tmp_path / 'codex-home'
    home.mkdir()
    (home / 'auth.json').write_text('login-state-not-a-real-credential')
    client = m.CodexSubscriptionClient(model='gpt-6.1-sol', reasoning_effort='high', environment={'CODEX_HOME': str(home)})
    client.cli_identity = {'path': '/current/codex', 'version': 'codex-cli 0.159.2', 'file_sha256': 'binary1'}
    local_checks, calls = [], []
    monkeypatch.setattr(client, '_verify_chatgpt_login', lambda: local_checks.append(True))
    monkeypatch.setattr(client, 'generate_json', lambda *a, **k: calls.append(client.model) or {'ok': True})
    cache = tmp_path / 'proofs'
    first = verify_runtime(client, cache)
    second = verify_runtime(client, cache)
    assert first['new_model_calls'] == 1 and second['new_model_calls'] == 0
    assert len(calls) == 1 and len(local_checks) == 2
    client.cli_identity['file_sha256'] = 'binary2'
    assert verify_runtime(client, cache)['new_model_calls'] == 1
    (home / 'auth.json').write_text('new-login-state')
    assert verify_runtime(client, cache)['new_model_calls'] == 1
    assert len(calls) == 3


def test_saved_runtime_proof_tamper_rejected_not_model_retry(tmp_path, monkeypatch):
    import json
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'auth.json').write_text('login')
    client = m.CodexSubscriptionClient(model='gpt-6.1-sol', environment={'CODEX_HOME': str(home)})
    client.cli_identity = {'path': '/current/codex'}
    monkeypatch.setattr(client, '_verify_chatgpt_login', lambda: None)
    calls = []
    monkeypatch.setattr(client, 'generate_json', lambda *a, **k: calls.append(True) or {'ok': True})
    first = verify_runtime(client, tmp_path / 'proofs')
    path = m.Path(first['proof_path'])
    proof = json.loads(path.read_text())
    proof['result']['model'] = 'other-model'
    path.write_text(json.dumps(proof))
    with pytest.raises(m.CodexSubscriptionError, match='mismatch'):
        verify_runtime(client, tmp_path / 'proofs')
    assert len(calls) == 1
