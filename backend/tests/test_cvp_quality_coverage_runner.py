import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('quality_coverage_runner', Path(__file__).parents[2] / 'scripts/cvp-quality-coverage-runner.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def source():
    return {'source_id': 'SRC-a', 'segments': [
        {'physical_ordinal': 1, 'editorial': True, 'live_text': 'editor summary'},
        {'physical_ordinal': 2, 'editorial': False, 'live_text': 'actual professor text\nend'}]}


def observations():
    return {'observations': [{'observation_id': 'OBS-1', 'judgment': 'judgment', 'qualifications': [],
         'status': 'observed', 'attribution': 'professor_explicit',
         'citations': [{'physical_ordinal': 2, 'quote': 'professor text'}]}], 'limitations': []}


def test_exact_observation_citations_exclude_editorial_and_deleted_text():
    MODULE.validate_observations(observations(), source())
    with pytest.raises(ValueError):
        MODULE.source_citation({'physical_ordinal': 1, 'quote': 'editor summary'}, source())
    with pytest.raises(ValueError):
        MODULE.source_citation({'physical_ordinal': 2, 'quote': 'textend'}, source())
    with pytest.raises(ValueError):
        MODULE.source_citation({'physical_ordinal': True, 'quote': 'professor'}, source())


def test_duplicate_observations_and_incomplete_coverage_are_rejected():
    o = observations()
    o['observations'].append(dict(o['observations'][0]))
    with pytest.raises(ValueError, match='duplicate'):
        MODULE.validate_observations(o, source())
    packet = {'complete_other_member_sources': [], 'complete_target_source': source(),
              'connected_complete_viewpoints': []}
    with pytest.raises(ValueError, match='denominator'):
        MODULE.validate_coverage({'coverage': [], 'limitations': []}, observations(), packet)


def test_frozen_coverage_contract_accepts_record_coverage_without_inventing_a_cvp_requirement():
    value = {'coverage': [{'observation_id': 'OBS-1', 'status': 'covered', 'record_ids': ['SRC-a'],
                          'reason': 'reason', 'citations': [{'source_id': 'SRC-a', 'physical_ordinal': 2, 'quote': 'professor'}]}],
             'limitations': []}
    packet = {'complete_other_member_sources': [], 'complete_target_source': source(),
              'connected_complete_viewpoints': []}
    assert MODULE.validate_coverage(value, observations(), packet) == value


def provider(text='{}', finish='STOP'):
    return {'modelVersion': 'gemini-3.8-flash', 'candidates': [{'finishReason': finish,
              'content': {'parts': [{'text': text}]}}]}


def test_cached_raw_is_exactly_reused_without_a_second_call(tmp_path):
    calls = []
    def send(body):
        calls.append(body)
        return provider()
    args = (tmp_path / 'once', {'request': 'same'}, 'gemini-3.8-flash', 'https://example.invalid', 'secret', 1000)
    assert MODULE.once_call(*args, transport=send)[1]
    assert not MODULE.once_call(*args, transport=send)[1]
    assert len(calls) == 1
    assert 'secret' not in (tmp_path / 'once' / 'request.json').read_text()
    conflicting = (args[0], {'request': 'changed'}) + args[2:]
    with pytest.raises(ValueError, match='conflicting'):
        MODULE.once_call(*conflicting, transport=send)


def test_unknown_intent_never_resends_after_transport_failure(tmp_path):
    calls = []
    def fail(body):
        calls.append(body)
        raise TimeoutError('never log secret-bearing URL')
    args = (tmp_path / 'once', {'request': 'same'}, 'gemini-3.8-flash', 'https://example.invalid', 'secret', 1000)
    assert MODULE.once_call(*args, transport=fail)[0]['state'] == 'outcome_unknown'
    assert MODULE.once_call(*args, transport=fail) == ({'state': 'outcome_unknown', 'reason': 'intent_without_raw; no resend'}, False)
    assert len(calls) == 1
    assert 'never log' not in (tmp_path / 'once' / 'failure.json').read_text()


def test_provider_refusal_incomplete_and_model_mismatch_never_become_answers():
    assert MODULE.decode_provider({'promptFeedback': {'blockReason': 'PROHIBITED_CONTENT'}}, 'gemini-3.8-flash')['state'] == 'provider_block'
    assert MODULE.decode_provider(provider(finish='MAX_TOKENS'), 'gemini-3.8-flash')['state'] == 'no_complete_answer'
    other = provider()
    other['modelVersion'] = 'another-model'
    assert MODULE.decode_provider(other, 'gemini-3.8-flash')['state'] == 'unexpected_provider_model'


def test_request_size_is_checked_before_any_intent_or_network(tmp_path):
    def forbidden(body):
        raise AssertionError('network must not run')
    with pytest.raises(ValueError, match='budget'):
        MODULE.once_call(tmp_path / 'once', {'request': 'too large'}, 'gemini-3.8-flash', 'https://example.invalid', 'secret', 1, transport=forbidden)
    assert not (tmp_path / 'once').exists()


@pytest.mark.parametrize('leak', [False, True])
def test_complete_runner_chain_records_protected_unit_and_reuses_saved_raw(tmp_path, monkeypatch, leak):
    volume = tmp_path / 'volume'
    root = volume / 'WangKnowledgePlatform/staging/cvp-quality-429/preflight'
    proposal_root = root / 'coverage-call-proposal-v1'
    proposal_root.mkdir(parents=True)
    original = source()
    original['physical_sha256'] = 'original-file-sha'
    obs_request = MODULE.CP.request('observe', MODULE.PK.compact_preview({'complete_source': original}))
    packet = {'complete_other_member_sources': [], 'complete_target_source': original,
              'connected_complete_viewpoints': [], 'source_claims': [], 'independent_observations': []}
    if leak:
        packet['ai_adjudication'] = {'approval_status': 'approved'}
    template = MODULE.CP.request('compare', MODULE.PK.compact_preview(packet))
    for name, value in [('source-01.observe-request.json', obs_request), ('source-01.comparison-template.json', template)]:
        MODULE.PF.write_new(proposal_root / name, value)
    snapshot = [{'kind': 'fixture'}]
    baseline = {'snapshot_sha256': MODULE.PF.sha(snapshot), 'sources': []}
    MODULE.PF.write_new(root / 'baseline.json', baseline)
    proposal = {'model': 'gemini-3.8-flash', 'endpoint_without_credentials': 'https://example.invalid',
        'code_sha256': MODULE.PF.digest(Path(MODULE.CP.__file__)), 'baseline_sha256': MODULE.PF.sha(baseline),
        'proposed_maximum_new_calls': 2, 'entries': [
         {'source_unit': 'source-01', 'observe_status': 'proposed', 'comparison_status': 'conditional',
          'maximum_observe_calls': 1, 'maximum_comparison_calls': 1,
          'observe_request_sha256': MODULE.PF.sha(obs_request), 'observe_request_bytes': len(MODULE.PF.encoded(obs_request)),
          'comparison_context_sha256': MODULE.PF.sha(packet), 'comparison_request_maximum_bytes': 100000},
         {'source_unit': 'source-02', 'observe_status': 'protected', 'comparison_status': 'protected',
          'maximum_observe_calls': 0, 'maximum_comparison_calls': 0}]}
    MODULE.PF.write_new(proposal_root / 'call-proposal.json', proposal)
    auth = root / 'authorization.json'
    MODULE.PF.write_new(auth, {'proposal_file_sha256': MODULE.PF.digest(proposal_root / 'call-proposal.json'),
                              'maximum_new_calls': 2, 'scope': 'source_to_viewpoint_coverage_only'})
    monkeypatch.setattr(MODULE.PF, 'VOLUME', volume)
    monkeypatch.setattr(MODULE.PF.subprocess, 'check_output', lambda args: MODULE.PF.plistlib.dumps({'VolumeUUID': MODULE.PF.UUID}))
    monkeypatch.setattr(MODULE.PF, 'settings', lambda: {'KNOWLEDGE_DATABASE_URL': 'fixture', 'GEMINI_API_KEY1': 'fixture'})
    monkeypatch.setattr(MODULE.PF, 'query_snapshot', lambda url: snapshot)
    monkeypatch.setattr(MODULE.PF, 'verify_sources', lambda sources: [])
    monkeypatch.setattr('sys.argv', ['runner', '--preflight', str(root), '--authorization', str(auth)])
    actual_once = MODULE.once_call
    calls = []
    def invoke(*args, **kwargs):
        def transport(body):
            calls.append(body)
            text = json.loads(body)['contents'][0]['parts'][0]['text']
            if text.startswith(MODULE.CP.OBS_PROMPT):
                response = observations()
            else:
                response = {'coverage': [{'observation_id': 'OBS-1', 'status': 'not_recorded', 'record_ids': [],
                  'reason': 'No destination in synthetic fixture', 'citations': [{'source_id': 'SRC-a', 'physical_ordinal': 2, 'quote': 'professor'}]}],
                  'limitations': []}
            return provider(json.dumps(response))
        return actual_once(*args, **kwargs, transport=transport)
    monkeypatch.setattr(MODULE, 'once_call', invoke)
    MODULE.main()
    results = [json.loads(p.read_text()) for p in (root / 'coverage-execution-v1').glob('execution-*.json')]
    assert results[0]['new_calls_this_invocation'] == (1 if leak else 2)
    if leak:
        assert results[0]['records'][0]['compare']['state'] == 'blind_projection_restricted'
    else:
        assert results[0]['records'][0]['compare']['validation_state'] == 'valid'
    assert results[0]['records'][1]['observe']['state'] == 'protected'
    assert results[0]['all_fixed_source_units_recorded']
    MODULE.main()
    assert len(calls) == (1 if leak else 2)
    assert any(json.loads(p.read_text())['new_calls_this_invocation'] == 0
               for p in (root / 'coverage-execution-v1').glob('execution-*.json'))
    spec = importlib.util.spec_from_file_location('quality_report_chain', Path(__file__).parents[2] / 'scripts/cvp-quality-coverage-report.py')
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    artifact = next((root / 'coverage-execution-v1').glob('execution-*.json'))
    result = report.build(root, artifact)
    assert result['fixed_source_denominator'] == 2
    assert result['actual_unique_intents'] == (1 if leak else 2)
    assert result['accepted_comparison_observation_denominator'] == (0 if leak else 1)
    assert result['quality_evaluation_complete'] is False
