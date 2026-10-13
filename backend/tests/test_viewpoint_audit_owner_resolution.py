from copy import deepcopy
import pytest
from backend.pipeline import viewpoint_audit_owner_resolution as module
from backend.pipeline.viewpoint_cvp_campaign_runner import validate_audit
from backend.pipeline.viewpoint_resolution_runtime import write_immutable, read_artifact
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


def seal(body):
    return body | {'artifact_sha256': sha256_json(body)}


def fixture(tmp_path, monkeypatch):
    finding = {'viewpoint_id': 'v', 'revision_id': 'r', 'verdict': 'disputed',
               'claim_ids_in_question': ['c'], 'reason': '原異議'}
    audit = {'meta': {'model': 'gemini-3.8-flash'}, 'layers': {
        '3': {'complete': True, 'judged': 1, 'results': [{'claim_id': 'c', 'verdict': 'supported'}]},
        '4': {'complete': True, 'judged': 1, 'disputed': 1, 'results': [finding]}}}
    audit_path = tmp_path/'audit.json'; write_immutable(audit_path, audit)
    root = tmp_path/'adjudication'
    packet = seal({'audit_binding': module.binding(audit_path), 'items': [{
        'item_id': 'i', 'viewpoint': {'viewpoint_id': 'v'}, 'claim': {'claim_id': 'c'},
        'sources': [{'source_id': 's', 'paragraphs': [{'paragraph_key': 'S0001', 'text': '原文界定。'}]}]}]})
    write_immutable(root/'packet.json', packet)
    answer = {'item_id': 'i', 'verdict': 'audit_objection_rejected', 'source_id': 's',
              'paragraph_key': 'S0001', 'exact_quote': '原文界定。', 'reason': '自己的原文明确界定'}
    for label, model, backend in [('gpt', 'gpt-6.1-sol', 'codex_subscription'),
                                 ('opus', 'claude-opus-5-5', 'claude_subscription')]:
        response = {'findings': [answer]}
        write_immutable(root/f'raw-{label}.json', seal({'model_id': model, 'backend': backend,
            'request_payload_sha256': sha256_json(packet), 'response': response,
            'response_sha256': sha256_json(response)}))
    handoff = seal({'packet_sha256': packet['artifact_sha256'], 'actions': [{
        'item_id': 'i', 'verdict': 'audit_objection_rejected', 'gpt': answer, 'opus': answer}]})
    write_immutable(root/'handoff.json', handoff)
    monkeypatch.setattr(module, 'build_packet', lambda *args: packet)
    monkeypatch.setattr(module, 'build_packet_for_saved', lambda *args: packet)
    monkeypatch.setattr(module, 'run', lambda *args: handoff)
    path = tmp_path/'resolution.json'
    module.create_resolution(audit_path, root, path, object(), authorized_by='owner',
                             authorization_quotes=['已判定异议不对，继续'])
    return audit, path, root


def test_exact_owner_exception_preserves_original_dispute(tmp_path, monkeypatch):
    audit, path, root = fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError): validate_audit(audit, ['c'], ['v'])
    validate_audit(audit, ['c'], ['v'], owner_resolution_path=path)
    assert audit['layers']['4']['results'][0]['verdict'] == 'disputed'
    assert read_artifact(path)['master_data_mutations'] == 0


@pytest.mark.parametrize('mutation', ['foreign_claim', 'foreign_viewpoint', 'revision', 'blocked', 'quota', 'claim_dispute'])
def test_resolution_does_not_waive_other_failures(tmp_path, monkeypatch, mutation):
    audit, path, root = fixture(tmp_path, monkeypatch)
    if mutation == 'foreign_claim': audit['layers']['3']['results'][0]['claim_id'] = 'other'
    elif mutation == 'foreign_viewpoint': audit['layers']['4']['results'][0]['viewpoint_id'] = 'other'
    elif mutation == 'revision': audit['layers']['4']['results'][0]['revision_id'] = 'other'
    elif mutation == 'blocked': audit['layers']['4']['blocked'] = 1
    elif mutation == 'quota': audit['layers']['4']['model_errors'] = 1
    else: audit['layers']['3']['results'][0]['verdict'] = 'disputed'
    with pytest.raises(ValueError): validate_audit(audit, ['c'], ['v'], owner_resolution_path=path)


def test_tampered_raw_proof_invalidates_owner_resolution(tmp_path, monkeypatch):
    audit, path, root = fixture(tmp_path, monkeypatch)
    (root/'raw-gpt.json').write_text('{}')
    with pytest.raises(ValueError, match='physical'):
        validate_audit(audit, ['c'], ['v'], owner_resolution_path=path)


def test_no_owner_authorization_cannot_create_resolution(tmp_path):
    with pytest.raises(ValueError, match='explicit'):
        module.create_resolution(tmp_path/'audit', tmp_path, tmp_path/'out', object(),
                                 authorized_by='', authorization_quotes=[])


def test_source_repair_preparer_reconsumes_completed_owner_resolution(tmp_path, monkeypatch):
    from backend.pipeline.viewpoint_reviewed_cvp_prepare import validate_completed_parent_audit
    audit, path, root = fixture(tmp_path, monkeypatch)
    row = {'group_key': 'g', 'audit_path': str(tmp_path/'audit.json'),
           'viewpoint_ids': ['v'], 'owner_resolution_path': str(path),
           'bindings': [module.binding(tmp_path/'audit.json'), module.binding(path)]}
    assert validate_completed_parent_audit(row, {'g': {'claim_ids': ['c']}}) == ['c']
    assert audit['layers']['4']['results'][0]['verdict'] == 'disputed'
    without = dict(row); without.pop('owner_resolution_path')
    with pytest.raises(ValueError): validate_completed_parent_audit(without, {'g': {'claim_ids': ['c']}})
    without_pin = dict(row, bindings=[module.binding(tmp_path/'audit.json')])
    with pytest.raises(ValueError, match='physical binding'):
        validate_completed_parent_audit(without_pin, {'g': {'claim_ids': ['c']}})
    (root/'raw-opus.json').write_text('{}')
    with pytest.raises(ValueError, match='physical'):
        validate_completed_parent_audit(row, {'g': {'claim_ids': ['c']}})
