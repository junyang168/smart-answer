import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_model_cache_handoff import prepare_handoff, old_request
from backend.pipeline.viewpoint_resolution_runtime import call_model, write_immutable, recorded_model_executions


def fixture(tmp_path, role):
    src, dst = tmp_path / 'src', tmp_path / 'dst'
    adapter = SimpleNamespace(model_id='gpt-6.1-sol', backend='codex', prompt_sha256='p',
        generation_config_sha256='g', generate=lambda p: pytest.fail('must reuse, never call'))
    packet = {'batch_id': 'old', 'claims': [{'statement': '不能刪限定', 'evidence': ['完整證據']}], 'registry_context': []}
    packet['packet_sha256'] = sha256_json(packet)
    proposal = {'batch_id': 'old', 'statement': '原提案'}
    old = copy.deepcopy(packet)
    response = {'batch_id': 'old', 'statement': '原提案'}
    if role == 'raw-consolidation.json':
        old = {'batch_id': 'old', 'candidates': ['不能合併']}
        old['packet_sha256'] = sha256_json(old)
        response = {'verdicts': [{'decision': 'new'}]}
    elif role == 'raw-review.json':
        old = {'batch_id': 'old', 'packet': packet, 'proposal': proposal,
               'proposal_sha256': sha256_json(proposal), 'revision_dependents': {}}
        response = {'proposal_sha256': sha256_json(proposal), 'findings': ['必須修正'], 'decision': 'correct'}
    write_immutable(src / 'batch-packet.json', packet)
    for name in ('raw-proposal.json', 'raw-consolidation.json', 'raw-review.json'):
        body = {k: getattr(adapter, k) for k in ('model_id','backend','prompt_sha256','generation_config_sha256')}
        body.update(request_payload_sha256=sha256_json(old), response=response,
                    response_sha256=sha256_json(response), wall_seconds=9)
        write_immutable(src / name, body | {'artifact_sha256': sha256_json(body)})
    freeze = tmp_path / 'freeze.json';write_immutable(freeze, {'runner_commit': 'new'})
    prepare_handoff(src, dst, target_batch_id='new', target_freeze=freeze)
    new = old_request(old, 'new')
    return adapter, new, dst / role, src, response


@pytest.mark.parametrize('role', ['raw-proposal.json','raw-consolidation.json','raw-review.json'])
def test_exact_semantic_reuse_preserves_findings_and_zero_model_calls(tmp_path, role):
    adapter, payload, cache, src, original = fixture(tmp_path, role)
    result, calls, seconds = call_model(adapter, payload, cache)
    assert calls == seconds == 0
    if 'decision' in original:
        assert result['decision'] == 'correct' and result['findings'] == original['findings']
    else:
        assert {k:v for k,v in result.items() if k!='batch_id'} == {k:v for k,v in original.items() if k!='batch_id'}
    assert call_model(adapter, payload, cache)[1] == 0
    assert recorded_model_executions(cache.parent, raw_artifacts={'test': role})['calls_recorded_total'] == 0


@pytest.mark.parametrize('drift', ['text','evidence','registry','model','prompt','physical','freeze','target'])
def test_drift_fails_without_fallback(tmp_path, drift):
    a,p,c,src,_ = fixture(tmp_path, 'raw-proposal.json')
    if drift == 'text':p['claims'][0]['statement']='變了'
    elif drift == 'evidence':p['claims'][0]['evidence']=[]
    elif drift == 'registry':p['registry_context']=[{'new': 'viewpoint'}]
    elif drift == 'model':a.model_id='other'
    elif drift == 'prompt':a.prompt_sha256='other'
    elif drift == 'physical':(src/'raw-proposal.json').write_text('{}')
    elif drift == 'freeze':(tmp_path/'freeze.json').write_text('{}')
    else:p['batch_id']='wrong'
    with pytest.raises(ValueError):call_model(a,p,c)
    assert not c.exists()


def test_existing_reused_cache_still_checks_original_provenance(tmp_path):
    adapter, payload, cache, src, _ = fixture(tmp_path, 'raw-proposal.json')
    call_model(adapter, payload, cache)
    (src / 'raw-proposal.json').write_text('{}')
    with pytest.raises(ValueError, match='physical SHA drift'):
        call_model(adapter, payload, cache)
