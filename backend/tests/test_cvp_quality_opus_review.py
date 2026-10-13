import importlib.util
import json
from pathlib import Path

import pytest


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).parents[2] / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_source_semantic_review_keeps_nonverbatim_citations_without_declaring_the_meaning_wrong():
    m = module('cvp-quality-opus-review')
    source = {'source_id': 'S1', 'segments': [{'physical_ordinal': 1, 'editorial': False,
        'physical_text': '只有在条件成立时才适用。', 'live_text': '只有在条件成立时才适用。'}]}
    packet = {'complete_target_source': source, 'complete_other_member_sources': [],
              'independent_observations': {'observations': [{'observation_id': 'O1'}]}, 'source_claims': [{'claim_id': 'C1'}]}
    value = {'observations': [{'observation_id': 'O1', 'meaning_status': 'faithful', 'record_ids': ['C1'],
                'citations': [{'source_id': 'S1', 'physical_ordinal': 1, 'quote': '条件成立才适用。'}]}], 'additional_findings': []}
    result = m.validate(value, packet)
    assert result[0]['all_reviewer_citations_bound'] is False
    assert value['observations'][0]['meaning_status'] == 'faithful'
    assert result[0]['citations'][0]['citation_integrity'] == 'needs_source_check'
    value['observations'][0]['record_ids'] = ['foreign']
    with pytest.raises(ValueError, match='foreign record'):
        m.validate(value, packet)


def test_masterdata_review_checks_all_links_and_their_exact_claim_identity():
    m = module('cvp-quality-masterdata-review')
    packet = {'target': {'links': [{'viewpoint_claim_link_id': 'L1', 'claim_id': 'C1'}], 'complete_own_sources': []},
              'nearby_viewpoints': []}
    value = {'link_assessments': [], 'nearby_assessments': []}
    with pytest.raises(ValueError, match='denominator'):
        m.validate(value, packet)
    value['link_assessments'] = [{'viewpoint_claim_link_id': 'L1', 'claim_id': 'wrong', 'citations': []}]
    with pytest.raises(ValueError, match='identity mismatch'):
        m.validate(value, packet)


def test_candidate_discovery_never_approves_merges_and_rejects_duplicates():
    m = module('cvp-quality-masterdata-candidates')
    records = [{'viewpoint_id': 'V1'}, {'viewpoint_id': 'V2'}]
    pair = {'left_viewpoint_id': 'V1', 'right_viewpoint_id': 'V2', 'candidate_kind': 'possible_same_identity', 'reason': 'fixture'}
    result = m.validate({'pairs': [pair]}, records)
    assert result[0]['identity_verdict'] is None and result[0]['full_source_verified'] is False
    assert result[0]['quality_defect_confirmed'] is False
    with pytest.raises(ValueError, match='duplicate/self/foreign'):
        m.validate({'pairs': [pair, {**pair, 'left_viewpoint_id': 'V2', 'right_viewpoint_id': 'V1'}]}, records)


def test_subscription_environment_does_not_inherit_api_billing_credentials(monkeypatch):
    m = module('cvp-quality-opus-review')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'fixture')
    monkeypatch.setenv('ANTHROPIC_AUTH_TOKEN', 'fixture')
    monkeypatch.setenv('CLAUDE_CODE_USE_VERTEX', '1')
    env = m.environment()
    assert not {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_USE_VERTEX'}.intersection(env)


def test_masterdata_report_keeps_missing_units_and_does_not_invent_defect_or_recall_rates():
    m = module('cvp-quality-masterdata-report')
    opinions = {'V1': [{'result': {'canonical_fidelity': 'faithful', 'cross_sermon_identity': 'supported',
                                 'link_assessments': [{'role_status': 'misclassified'}]}}]}
    result = m.aggregate(['V1', 'V2'], opinions)
    assert result['fixed_denominator'] == 2 and result['complete_opinions'] == 1
    assert result['without_complete_opinion'] == ['V2']
    assert result['confirmed_defect_count'] is None and result['population_recall'] is None
    assert result['link_role_opinions'] == {'misclassified': 1}


def test_shared_component_is_distinct_from_complete_master_identity():
    m = module('cvp-quality-masterdata-review')
    choices = m.schema()['properties']['nearby_assessments']['items']['properties']['relation']['enum']
    assert 'shared_component_distinct_records' in choices and 'same_master_identity' in choices


def test_masterdata_report_rejects_a_result_changed_after_provider_response(tmp_path):
    m = module('cvp-quality-masterdata-report')
    request = {'model': m.M.O.MODEL, 'effort': 'high', 'system_prompt': 'fixture', 'schema': {},
               'viewpoint_id': 'V1', 'neighbor_ids': []}
    usage = {m.M.O.MODEL: {'inputTokens': 1}}
    wrapper = {'is_error': False, 'structured_output': {'fixture': 'original'}, 'modelUsage': usage, 'num_turns': 1}
    def save(name, value):
        (tmp_path / name).write_text(json.dumps(value))
    save('request.json', request)
    save('intent.json', {'request_sha256': m.PF.sha(request), 'code_sha256': 'fixture'})
    save('provider-response.json', {'returncode': 0, 'stdout': json.dumps(wrapper)})
    assessment = {'provider_response_sha256': m.PF.digest(tmp_path / 'provider-response.json'),
                  'model_usage': usage, 'result': wrapper['structured_output'], 'citation_bindings': []}
    save('assessment.json', assessment)
    assert m.authenticate(tmp_path)['opinion_is_confirmed_defect'] is False
    assessment['result'] = {'fixture': 'changed'}
    save('assessment.json', assessment)
    with pytest.raises(ValueError, match='binding failed'):
        m.authenticate(tmp_path)
