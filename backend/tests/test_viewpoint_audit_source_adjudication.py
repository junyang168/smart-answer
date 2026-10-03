from copy import deepcopy
import pytest
from backend.pipeline import viewpoint_audit_source_adjudication as module
from backend.pipeline.viewpoint_resolution_runtime import write_immutable, read_artifact
from backend.tests.test_viewpoint_deferred_source_check import Adapter


def packet():
    return {'artifact_sha256': 'pin', 'items': [{'item_id': '4:v:c', 'sources': [
        {'source_id': 's', 'paragraphs': [{'paragraph_key': 'S0001', 'text': '王就是這位彌賽亞。'}]}]}]}


def answer(verdict='audit_objection_rejected'):
    return {'findings': [{'item_id': '4:v:c', 'verdict': verdict, 'source_id': 's',
                          'paragraph_key': 'S0001', 'exact_quote': '王就是這位彌賽亞。',
                          'reason': '同一原文明確消解身份指代。'}]}


@pytest.mark.parametrize('mutation', ['foreign_source', 'foreign_paragraph', 'forged_quote', 'duplicate', 'missing', 'empty_quote'])
def test_invalid_findings_rejected(mutation):
    raw = answer()
    row = raw['findings'][0]
    if mutation == 'foreign_source': row['source_id'] = 'foreign'
    elif mutation == 'foreign_paragraph': row['paragraph_key'] = 'S9999'
    elif mutation == 'forged_quote': row['exact_quote'] = '教授沒說過'
    elif mutation == 'duplicate': raw['findings'] *= 2
    elif mutation == 'missing': raw['findings'] = []
    else: row['exact_quote'] = ''
    with pytest.raises(ValueError): module.validate_findings(raw, packet())


@pytest.mark.parametrize('right,action', [
    ('audit_objection_rejected', 'independent_resolution_required'),
    ('audit_objection_upheld', 'owner_judgment_required'),
    ('unresolved', 'owner_judgment_required')])
def test_bounded_blind_adjudication_never_grants_completion(tmp_path, monkeypatch, right, action):
    monkeypatch.setattr(module, 'build_packet', lambda *args: packet())
    adapters = [Adapter(answer()), Adapter(answer(right))]
    report = module.run(tmp_path/'audit.json', tmp_path/'out', object(), adapters)
    assert report['actions'][0]['next_action'] == action
    assert report['group_complete'] is False and report['master_data_mutations'] == 0
    assert adapters[0].payloads == adapters[1].payloads
    module.run(tmp_path/'audit.json', tmp_path/'out', object(), adapters)
    assert [a.calls for a in adapters] == [1, 1]


def test_failure_preserves_raw_and_does_not_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'build_packet', lambda *args: packet())
    bad = answer(); bad['findings'][0]['exact_quote'] = '假的'
    adapters = [Adapter(bad), Adapter(answer())]
    with pytest.raises(ValueError): module.run(tmp_path/'audit.json', tmp_path/'out', object(), adapters)
    assert (tmp_path/'out/raw-gpt.json').exists()
    assert (tmp_path/'out/failure.json').exists()
    assert not (tmp_path/'out/handoff.json').exists()
    assert [a.calls for a in adapters] == [1, 0]


def test_incomplete_audit_not_routed_as_content():
    audit = {'layers': {'4': {'complete': False, 'judged': 1, 'disputed': 1,
                            'results': [{'verdict': 'disputed'}]}}}
    with pytest.raises(ValueError, match='incomplete'): module.disputes(audit)


def test_passing_audit_has_no_adjudication():
    assert module.disputes({'layers': {'4': {'results': [{'verdict': 'same'}]}}}) == []


def test_missing_population_cannot_be_routed():
    audit = {'layers': {'3': {'complete': True, 'judged': 1, 'results': [{'claim_id': 'other'}]}}}
    with pytest.raises(ValueError, match='population'):
        module.validate_population(audit, ['c'], ['v'])
