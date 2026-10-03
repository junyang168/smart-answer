import json
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.viewpoint_foundation import canonical_json
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.api.canonical_repository.viewpoint_batch_resolution import CanonicalViewpointReconsiderationResponse
from backend.pipeline.viewpoint_resolution_runtime import call_model


def adapter(compact):
    client = SimpleNamespace(model="gpt-6.1-sol", backend="codex", reasoning_effort="high")
    obj = StructuredJsonReviewerAdapter(client=client, prompt="fixed prompt",
        response_model=CanonicalViewpointReconsiderationResponse, schema_name="fixed",
        compact_payload=compact)
    return obj, client


def test_compact_keeps_every_field_and_text_and_counts_actual_request():
    payload = {"claims": [{"statement": "原文  空白\n不可變", "evidence": ["甲", "乙"]}] * 20,
               "registry": {"unknown": None}, "findings": ["must correct"]}
    small, client = adapter(True)
    pretty, _ = adapter(False)
    captured = []
    client.generate_json = lambda **kw: captured.append(kw) or {}
    small.generate(payload)
    request = captured[0]
    assert json.loads(request["user_prompt"]) == payload
    assert small.request_bytes(payload) == len(canonical_json(request).encode())
    assert small.request_bytes(payload) < pretty.request_bytes(payload)
    assert small.prompt_sha256 == pretty.prompt_sha256
    assert small.generation_config_sha256 != pretty.generation_config_sha256


def test_compact_does_not_weaken_ceiling_or_call_model_on_excess(tmp_path):
    small, client = adapter(True)
    small.max_request_bytes = 10
    client.generate_json = lambda **kw: pytest.fail("must stop before model")
    with pytest.raises(ValueError, match="exceeding the frozen ceiling"):
        call_model(small, {"evidence": "a" * 100}, tmp_path / "raw.json")
    assert not (tmp_path / "raw.json").exists()
