import copy
import json
from types import SimpleNamespace

import pytest

from backend.pipeline import viewpoint_batch_resolution_runner as runner
from backend.pipeline.viewpoint_resolution_runtime import call_model
from backend.api.canonical_repository.viewpoint_resolution import structured_json_request


def proposer(monkeypatch):
    requests = []
    client = SimpleNamespace(model='gpt-6.1-sol', backend='codex_subscription',
        reasoning_effort='high', max_output_tokens=None,
        generate_json=lambda **kw: requests.append(kw) or {})
    monkeypatch.setattr(runner, '_subscription_client', lambda *args: client)
    adapter = runner.build_proposer('gpt-6.1-sol', 'high', provider='codex')
    adapter.max_request_bytes = 500000
    return adapter, requests


def test_initial_proposal_compaction_is_lossless_and_separately_identified(monkeypatch):
    adapter, requests = proposer(monkeypatch)
    packet = {'claims': [{'statement': '義\n  ψυχή', 'conditions': ['不可刪'],
        'source_sha256': 'a'*64, 'evidence': [{'text': '完整原文'}]}]*1700}
    before = copy.deepcopy(packet)
    adapter.generate(packet)
    pretty = structured_json_request(packet, prompt=adapter._prompt,
        response_model=adapter._response_model, schema_name=adapter._schema_name)
    assert json.loads(requests[0]['user_prompt']) == before == packet
    assert requests[0]['system_prompt'] == pretty['system_prompt']
    assert requests[0]['json_schema'] == pretty['json_schema']
    assert adapter._compact_payload
    assert adapter.backend == 'codex_subscription'
    assert adapter.model_id == 'gpt-6.1-sol'
    assert len(requests[0]['user_prompt'].encode()) < len(pretty['user_prompt'].encode())


def test_initial_proposal_keeps_frozen_ceiling_and_zero_calls_on_overflow(monkeypatch, tmp_path):
    adapter, requests = proposer(monkeypatch)
    with pytest.raises(ValueError, match='frozen ceiling 500000'):
        call_model(adapter, {'whole_original': 'X'*500001}, tmp_path/'raw-proposal.json')
    assert not requests
    assert not (tmp_path/'raw-proposal.json').exists()
