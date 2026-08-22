from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from backend.pipeline.codex_subscription_client import (
    API_BILLING_ENV_VARS,
    CodexSubscriptionClient,
    CodexSubscriptionError,
    subscription_environment,
)
from backend.pipeline.detailed_knowledge_extraction import (
    DETAILED_RESPONSE_SCHEMA,
    extraction_identity,
)
from backend.pipeline import detailed_knowledge_extraction_runner as extraction_runner
from backend.pipeline.detailed_knowledge_extraction_runner import (
    SectionSettings,
    build_client,
    run_one,
)


def _transcript() -> dict:
    return {
        "metadata": {"title": "固定抽取来源", "status": "published"},
        "script": [
            {
                "index": 10,
                "start_time": 1.0,
                "end_time": 8.0,
                "text": "有人说人子只强调人性。我说不对。",
            },
            {
                "index": 11,
                "start_time": 8.0,
                "end_time": 16.0,
                "text": "但以理书所说的那一位人子领受永远的权柄。",
            },
            {
                "index": 12,
                "start_time": 16.0,
                "end_time": 20.0,
                "text": "听众：所以这表明神性吗？",
            },
        ],
    }


def _response() -> dict:
    return {
        "questions": [{
            "question_id": "Q001", "text": "这表明神性吗？", "questioner": "audience",
            "question_type": "clarification", "answer_state": "answered",
            "answer_claim_ids": ["CL001"],
            "anchors": [{"segment_index": "S0003", "start_time": 16.0,
                         "end_time": 20.0, "verbatim_excerpt": "所以这表明神性吗？"}],
        }],
        "positions": [{
            "position_id": "POS001", "title": "人子只强调人性", "attribution": "external_view",
            "anchors": [{"segment_index": "S0001", "start_time": 1.0,
                         "end_time": 8.0, "verbatim_excerpt": "有人说人子只强调人性"}],
        }],
        "observations": [{
            "observation_id": "OBS001", "statement": "人子领受永远权柄",
            "observation_type": "scripture_text", "argument_role": "background",
            "scripture_refs": ["但以理书7:13-14"],
            "anchors": [{"segment_index": "S0002", "start_time": 8.0,
                         "end_time": 16.0, "verbatim_excerpt": "那一位人子领受永远的权柄"}],
        }],
        "evidence_steps": [{
            "evidence_step_id": "E001", "statement": "教授否定只强调人性的读法",
            "step_type": "reasoning", "speaker": "professor", "stance": "asserted",
            "discourse_role": "refutation", "support_eligibility": "eligible_candidate",
            "scripture_refs": [], "produced_claim_ids": ["CL001"],
            "anchors": [{"segment_index": "S0001", "start_time": 1.0,
                         "end_time": 8.0, "verbatim_excerpt": "我说不对"}],
        }],
        "claims": [{
            "claim_id": "CL001", "statement": "那一位人子具有神性身份",
            "claim_kind": "reasoning_conclusion", "attribution": "professor",
            "scripture_refs": ["但以理书7:13-14"], "topic_terms": ["人子", "神性"],
            "evidence_step_ids": ["E001"], "opposed_position_ids": ["POS001"],
            "review_status": "candidate",
        }],
        "evidence_relations": [],
        "claim_relations": [],
        "sentence_audit": [
            {"sentence_id": "S0001#001", "status": "extracted", "covered_by": ["POS001"],
             "reason_code": None, "reason": ""},
            {"sentence_id": "S0001#002", "status": "extracted", "covered_by": ["E001"],
             "reason_code": None, "reason": ""},
            {"sentence_id": "S0002#003", "status": "extracted", "covered_by": ["OBS001"],
             "reason_code": None, "reason": ""},
            {"sentence_id": "S0003#004", "status": "extracted", "covered_by": ["Q001"],
             "reason_code": None, "reason": ""},
        ],
    }


def _completed(args: list[str], *, stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


def test_subscription_environment_removes_api_billing_credentials() -> None:
    source = {name: "secret" for name in API_BILLING_ENV_VARS}
    source.update({"PATH": "/bin", "CODEX_HOME": "/oauth"})
    result = subscription_environment(source)
    assert not API_BILLING_ENV_VARS.intersection(result)
    assert result["PATH"] == "/bin"
    assert result["CODEX_HOME"] == "/oauth"


def test_non_chatgpt_login_fails_closed_before_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return _completed(command, stdout="Logged in using an API key\n")

    monkeypatch.setattr("backend.pipeline.codex_subscription_client.subprocess.run", fake_run)
    client = CodexSubscriptionClient(model="gpt-5.6-sol", executable="codex")
    with pytest.raises(CodexSubscriptionError, match="ChatGPT login"):
        client.generate_json("system", "user", {"type": "object"})
    assert calls == [["codex", "login", "status"]]


def test_transport_failure_has_no_api_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[1:3] == ["login", "status"]:
            return _completed(command, stdout="Logged in using ChatGPT\n")
        return _completed(command, stderr="quota exhausted", returncode=1)

    monkeypatch.setattr("backend.pipeline.codex_subscription_client.subprocess.run", fake_run)
    client = build_client(
        "gpt-5.6-sol", backend="codex-subscription",
        reasoning_effort="medium", max_output_tokens=64000,
    )
    with pytest.raises(CodexSubscriptionError, match="quota exhausted"):
        client.generate_json("system", "user", {"type": "object"})
    assert len(calls) == 2


def test_api_client_remains_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    constructed: list[dict] = []

    class FakeAPIClient:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

    monkeypatch.setattr(extraction_runner, "Stage1OpenAIClient", FakeAPIClient)
    client = build_client(
        "gpt-5.6-sol", reasoning_effort="medium", max_output_tokens=64000,
    )
    assert isinstance(client, FakeAPIClient)
    assert constructed[0]["api_key_env"] == "OPENAI_API_KEY"


def test_subscription_section_passes_schema_validator_and_sentence_ledger_and_then_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript_path = tmp_path / "fixed-source.json"
    transcript_path.write_text(json.dumps(_transcript(), ensure_ascii=False), encoding="utf-8")
    output_dir = tmp_path / "output"
    calls: list[list[str]] = []
    child_environments: list[dict[str, str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        child_environments.append(kwargs["env"])
        if command[1:3] == ["login", "status"]:
            return _completed(command, stdout="Logged in using ChatGPT\n")
        schema_path = Path(command[command.index("--output-schema") + 1])
        assert json.loads(schema_path.read_text(encoding="utf-8")) == DETAILED_RESPONSE_SCHEMA["schema"]
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(json.dumps(_response(), ensure_ascii=False), encoding="utf-8")
        return _completed(command)

    monkeypatch.setattr("backend.pipeline.codex_subscription_client.subprocess.run", fake_run)
    environment = {"PATH": "/bin", "OPENAI_API_KEY": "must-not-leak", "CODEX_HOME": "/oauth"}
    client = CodexSubscriptionClient(
        model="gpt-5.6-sol", executable="codex", environment=environment,
    )
    status, output = run_one(
        transcript_path, output_dir=output_dir, client=client, prompt="extract",
        reasoning_effort="medium", force=False,
        sections=SectionSettings(allow_generated=False),
    )
    assert status == "created"
    package = json.loads(output.read_text(encoding="utf-8"))
    assert package["extraction"]["backend"] == "codex_subscription"
    assert len(package["extraction"]["model_output_sha256"]) == 64
    int(package["extraction"]["model_output_sha256"], 16)
    assert package["coverage"]["available"] is True
    assert package["coverage"]["unprocessed"] == 0
    assert all("OPENAI_API_KEY" not in child for child in child_environments)

    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("a full fingerprint cache hit must not launch Codex")

    monkeypatch.setattr("backend.pipeline.codex_subscription_client.subprocess.run", unexpected_run)
    fresh_client = CodexSubscriptionClient(
        model="gpt-5.6-sol", executable="codex", environment=environment,
    )
    cached_status, cached_output = run_one(
        transcript_path, output_dir=output_dir, client=fresh_client, prompt="extract",
        reasoning_effort="medium", force=False,
        sections=SectionSettings(allow_generated=False),
    )
    assert cached_status == "skipped"
    assert cached_output == output


def test_subscription_backend_changes_fingerprint_without_changing_api_identity() -> None:
    kwargs = {
        "source_sha256": hashlib.sha256(b"source").hexdigest(),
        "prompt": "prompt",
        "model_id": "gpt-5.6-sol",
        "reasoning_effort": "medium",
        "max_output_tokens": 64000,
    }
    existing_api_identity = extraction_identity(**kwargs)
    explicit_default_identity = extraction_identity(**kwargs, backend=None)
    subscription_identity = extraction_identity(**kwargs, backend="codex_subscription")
    assert existing_api_identity == explicit_default_identity
    assert "backend" not in existing_api_identity
    assert subscription_identity["backend"] == "codex_subscription"
    assert subscription_identity["fingerprint_sha256"] != existing_api_identity["fingerprint_sha256"]
