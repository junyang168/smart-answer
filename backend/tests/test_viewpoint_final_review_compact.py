import json
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline import viewpoint_batch_resolution_runner as runner
from backend.pipeline.viewpoint_batch_resolution_runner import CanonicalViewpointReviewResponse
from backend.pipeline.viewpoint_resolution_runtime import call_model


def adapter():
    captured = []
    client = SimpleNamespace(model='claude-opus-5-5', backend='claude_subscription',
                             reasoning_effort='high', max_output_tokens=None,
                             generate_json=lambda **kw: captured.append(kw) or {})
    return StructuredJsonReviewerAdapter(client=client, prompt='unchanged',
        response_model=CanonicalViewpointReviewResponse, schema_name='review'), captured


def test_compact_copy_is_lossless_distinct_and_does_not_mutate_initial_reviewer():
    initial, requests = adapter()
    initial.max_request_bytes = 500000
    config = initial.generation_config_sha256
    final = initial.compact_copy()
    payload = {'claims': [{'text': '義\n  限定 ψυχή', 'evidence': ['原文', '不可刪']}]*100}
    initial.generate(payload); final.generate(payload)
    assert json.loads(requests[0]['user_prompt']) == json.loads(requests[1]['user_prompt']) == payload
    assert final.request_bytes(payload) < initial.request_bytes(payload)
    assert initial.generation_config_sha256 == config
    assert final.generation_config_sha256 != config
    assert final.prompt_sha256 == initial.prompt_sha256
    assert final.model_id == initial.model_id and final.backend == initial.backend
    assert final.max_request_bytes == 500000
    assert final.compact_copy() is final


def test_final_review_uses_compact_request_without_changing_payload(monkeypatch, tmp_path):
    initial, _ = adapter()
    initial.max_request_bytes = 500000
    captured = {}
    class Stop(Exception): pass
    def capture(a, payload, path):
        captured.update(adapter=a, payload=payload, path=path)
        raise Stop()
    monkeypatch.setattr(runner, '_call', capture)
    model = lambda d: SimpleNamespace(model_dump=lambda **kw: d)
    packet = {'claims': [{'statement': '限定', 'evidence': ['完整原文']}]}
    with pytest.raises(Stop):
        runner.run_final_cvp_review(batch_id='b', packet=packet,
            proposal=model({'batch_id': 'b', 'conditions': ['不可刪']}),
            revision_dependents={'preserved': True}, initial_proposal_sha256='old',
            initial_review=model({'findings': ['修正']}),
            reconsideration=model({'patches': ['已修正']}), reviewer=initial, output_dir=tmp_path)
    assert captured['adapter'] is not initial
    assert captured['adapter']._compact_payload
    assert not initial._compact_payload
    assert captured['payload']['packet'] == packet
    assert captured['payload']['allows_further_correction'] is False
    assert captured['path'].name == 'raw-final-review.json'


def test_compact_final_review_still_enforces_frozen_ceiling(tmp_path):
    initial, requests = adapter()
    final = initial.compact_copy()
    final.max_request_bytes = 500000
    with pytest.raises(ValueError, match='frozen ceiling 500000'):
        call_model(final, {'unchanged_content': 'X'*500001}, tmp_path/'raw-final-review.json')
    assert not requests and not (tmp_path/'raw-final-review.json').exists()
