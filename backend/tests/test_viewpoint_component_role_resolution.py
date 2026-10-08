from copy import deepcopy

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import write_immutable, read_artifact
from backend.pipeline.viewpoint_audit_owner_resolution import binding
from backend.pipeline import viewpoint_component_role_resolution as resolution
from backend.tests.test_viewpoint_component_roles import graph


def seal(value):
    return {**value, 'artifact_sha256': sha256_json(value)}


def fixture(tmp_path, monkeypatch, fault=None):
    links, claims, steps, fragments = graph()
    if fault == 'overlap': links[1]['component_locator'] = deepcopy(links[0]['component_locator'])
    if fault == 'hold': steps['E2']['support_eligibility'] = 'context_only'
    source = tmp_path/'source.txt'; source.write_text('結論和理由都是教授所講。')
    finding = {'viewpoint_id': 'V', 'revision_id': 'R', 'verdict': 'disputed',
               'reason': '兩條連接是否重複', 'claim_ids_in_question': ['C']}
    rejected = {'viewpoint_id': 'V2', 'revision_id': 'R2', 'verdict': 'disputed',
                'reason': '另一語義异議', 'claim_ids_in_question': ['C2']}
    audit = {'meta': {'model': 'gemini-3.8-flash'}, 'layers': {
        '3': {'complete': True, 'judged': 2, 'disputed': 0, 'results': [
            {'claim_id': c, 'verdict': 'supported'} for c in ('C', 'C2')]},
        '4': {'complete': True, 'judged': 2, 'disputed': 2, 'results': [finding, rejected]}}}
    audit_path = tmp_path/'audit.json'; write_immutable(audit_path, audit)
    item = {'item_id': '4:V:C', 'audit_finding': finding, 'claim': claims['C'],
            'viewpoint': {'viewpoint_id': 'V', 'current_revision_id': 'R'}, 'connections': links,
            'evidence': [{'step': steps[f'E{i}'], 'fragment': fragments[f'F{i}']} for i in (1, 2)],
            'sources': [{'source_id': 'S', **binding(source), 'paragraphs': [
                {'paragraph_key': 'S0001', 'text': source.read_text()}]}]}
    other = {**deepcopy(item), 'item_id': '4:V2:C2', 'audit_finding': rejected, 'claim': {'claim_id': 'C2'}}
    packet = seal({'items': [item, other], 'audit_binding': binding(audit_path)})
    prior = tmp_path/'prior'; write_immutable(prior/'packet.json', packet)
    answers = [{'item_id': row['item_id'], 'verdict': 'audit_objection_upheld' if i == 0 else 'audit_objection_rejected',
                'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': source.read_text(), 'reason': '回應異議'}
               for i, row in enumerate(packet['items'])]
    if fault == 'unresolved': answers[0]['verdict'] = 'unresolved'
    if fault == 'extra_upheld': answers[1]['verdict'] = 'audit_objection_upheld'
    raw = seal({'model_id': 'gpt-6.1-sol', 'backend': 'codex_subscription', 'response': {'findings': answers}})
    write_immutable(prior/'raw-gpt.json', raw)
    handoff = seal({'packet_sha256': packet['artifact_sha256'], 'maximum_model_calls': 1,
                    'actions': [{'item_id': a['item_id'], 'verdict': a['verdict'], 'gpt': a} for a in answers]})
    write_immutable(prior/'gpt-final-handoff.json', handoff)
    auth = {'schema_version': resolution.AUTH_SCHEMA, 'campaign_sha256': 'campaign', 'run_id': 'run',
            'authorized_by': 'owner', 'authorization_quotes': ['允許兩條成分不重疊、各有證據、作用不同的連接。'],
            'accept_structural_duplicate_finding_only': True, 'audit_binding': binding(audit_path),
            'packet_binding': binding(prior/'packet.json'),
            'targets': [{'item_id': item['item_id'], 'item_sha256': sha256_json(item), 'link_ids': ['L1', 'L2']}]}
    if fault == 'foreign_target': auth['targets'][0]['item_id'] = '4:foreign:C'
    if fault == 'wrong_pin': auth['targets'][0]['item_sha256'] = 'f'*64
    if fault == 'wrong_links': auth['targets'][0]['link_ids'] = ['L1', 'foreign']
    if fault == 'no_permission': auth['accept_structural_duplicate_finding_only'] = False
    if fault == 'other_campaign': auth['campaign_sha256'] = 'other'
    if fault == 'other_group': auth['run_id'] = 'other'
    auth_path = tmp_path/'auth.json'; write_immutable(auth_path, auth)
    monkeypatch.setattr('backend.pipeline.viewpoint_adjudication_history.validated_history', lambda root: (read_artifact(root/'packet.json'), {}))
    monkeypatch.setattr('backend.pipeline.viewpoint_audit_source_adjudication.build_packet', lambda *args: packet)
    out = tmp_path/'resolution.json'
    result = resolution.create(audit_path, prior, auth_path, out, object(), campaign_sha256='campaign', run_id='run')
    return audit, result, prior, out


def test_owner_structural_disposition_preserves_upheld_and_other_rejections(tmp_path, monkeypatch):
    audit, result, prior, out = fixture(tmp_path, monkeypatch)
    assert resolution.verify(audit, result) == {'3': set(), '4': {'V', 'V2'}}
    assert result['new_model_calls'] == result['master_data_mutations'] == 0
    assert read_artifact(prior/'gpt-final-handoff.json')['actions'][0]['verdict'] == 'audit_objection_upheld'
    assert audit['layers']['4']['disputed'] == 2
    from backend.pipeline.viewpoint_cvp_campaign_runner import validate_audit
    validate_audit(audit, ['C', 'C2'], ['V', 'V2'], owner_resolution_path=out)


@pytest.mark.parametrize('fault', ['overlap', 'hold', 'unresolved', 'extra_upheld', 'foreign_target',
                                  'wrong_pin', 'wrong_links', 'no_permission', 'other_campaign', 'other_group'])
def test_owner_permission_never_waives_other_content_or_mechanical_gates(fault, tmp_path, monkeypatch):
    with pytest.raises(ValueError): fixture(tmp_path, monkeypatch, fault)


@pytest.mark.parametrize('fault', ['raw', 'source', 'auth', 'finding', 'new_call', 'write'])
def test_completed_resolution_is_still_bound_to_exact_originals(fault, tmp_path, monkeypatch):
    audit, result, prior, _ = fixture(tmp_path, monkeypatch)
    if fault == 'raw': (prior/'raw-gpt.json').write_text('{}')
    elif fault == 'source': (tmp_path/'source.txt').write_text('變動')
    elif fault == 'auth': (tmp_path/'auth.json').write_text('{}')
    elif fault == 'finding': audit['layers']['4']['results'][0]['reason'] = '另一異議'
    else:
        result['new_model_calls' if fault == 'new_call' else 'master_data_mutations'] = 1
        result = seal({k: v for k, v in result.items() if k != 'artifact_sha256'})
    with pytest.raises(ValueError): resolution.verify(audit, result)
