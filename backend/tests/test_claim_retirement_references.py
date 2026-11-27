"""Retire can compare current Claims without granting them review or write rights."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm
from backend.pipeline.claim_direct_scope import retirement_reference_keys, retirement_references
from backend.pipeline.claim_joint_update import context_roots
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import (
    SyntheticPorts, operation_request, seed_claims, passing,
)
from backend.tests.test_claim_management_human_source_follow_up import authorize


def reference_request(tmp_path, keys=None):
    authority = tmp_path / 'reference-authority.json'
    authority.write_text(json.dumps({} if keys is None else {'retirement_reference_keys': keys}))
    bound = {'path': '/not-a-source', 'file_sha256': 'a' * 64}
    return cm.Request(action_id='readonly-retirement', operation='Retire', allowed_operations=['Retire'],
        target_keys=['claims/A'], permissions=[cm.source.Permission(collection='claims', object_id='A', fields=['statement'])],
        source_bindings={'S': bound}, authorization=binding(authority), policy=bound)


def snapshot(cid, source='S'):
    return {'payload': {'claim_id': cid, 'statement': cid, 'occurrences': [{'source_id': source}]},
            'revision': 1, 'content_sha256': 'b' * 64, 'retired': False}


def test_legacy_authority_has_no_added_context_or_projection(tmp_path):
    request = reference_request(tmp_path)
    assert retirement_reference_keys(request) == []
    assert retirement_references(request, {'unrelated': {}}) == {}
    assert context_roots(request) == [('claims', 'A'), ('source_documents', 'S')]


@pytest.mark.parametrize('keys', [['claims/B', 'claims/B'], ['questions/Q'], ['claims/'], ['claims/ B'], 'claims/B'])
def test_reference_keys_are_explicit_distinct_claims(tmp_path, keys):
    with pytest.raises(ValueError, match='distinct explicit Claim'):
        retirement_reference_keys(reference_request(tmp_path, keys))


@pytest.mark.parametrize('key', ['claims/A', 'claims/B'])
def test_reference_cannot_also_be_target_or_writable(tmp_path, key):
    request = reference_request(tmp_path, [key])
    if key.endswith('/B'):
        request.permissions.append(cm.source.Permission(collection='claims', object_id='B', fields=['statement']))
    with pytest.raises(ValueError, match='outside targets and write permissions'):
        retirement_reference_keys(request)


@pytest.mark.parametrize('problem', ['missing', 'retired', 'held', 'superseded', 'foreign', 'mixed'])
def test_reference_requires_active_frozen_authorized_source(tmp_path, problem):
    request = reference_request(tmp_path, ['claims/B'])
    records = {'claims/B': snapshot('B')}
    if problem == 'missing': records.clear()
    if problem == 'retired': records['claims/B']['retired'] = True
    if problem == 'held': records['claims/B']['payload']['review_status'] = 'human_review_required'
    if problem == 'superseded': records['claims/B']['payload']['superseded_by'] = 'C'
    if problem == 'foreign': records['claims/B']['payload']['occurrences'] = [{'source_id': 'T'}]
    if problem == 'mixed': records['claims/B']['payload']['occurrences'].append({'source_id': 'T'})
    with pytest.raises(ValueError, match='retirement reference'):
        retirement_references(request, records)


def test_model_packet_cannot_forge_or_omit_frozen_reference(tmp_path):
    request = reference_request(tmp_path, ['claims/B'])
    records = {'claims/B': snapshot('B')}
    materials = {'unit_request': request.model_dump(mode='json'), 'before': {}, 'transaction_before': records,
                 'retirement_reference_claims': {'claims/B': snapshot('changed')}}
    with pytest.raises(ValueError, match='reference facts drifted'):
        cm.model_packet(materials)
    materials.pop('retirement_reference_claims')
    with pytest.raises(ValueError, match='reference facts drifted'):
        cm.model_packet(materials)
    references = retirement_references(request, records)
    references['claims/B']['payload']['statement'] = 'changed'
    assert records['claims/B']['payload']['statement'] == 'B'


def test_reference_cannot_hide_an_actual_required_audit_dependency(tmp_path):
    request = reference_request(tmp_path, ['claims/B'])
    records = {'claims/A': snapshot('A'), 'claims/B': snapshot('B'),
        'source_documents/S': {'payload': {'source_id': 'S'}, 'retired': False},
        'claim_relations/R': {'payload': {'from_id': 'A', 'to_id': 'B', 'relation_type': 'supports'}, 'retired': False}}
    with pytest.raises(ValueError, match='cannot replace a required audit object'):
        cm.audit_keys(request, records)


def test_retired_shared_support_is_history_not_another_live_review_root(tmp_path):
    request = reference_request(tmp_path, ['claims/REFERENCE'])
    records = {'claims/'+cid: snapshot(cid) for cid in ['A', 'B', 'REFERENCE']}
    records['source_documents/S'] = {'payload': {'source_id': 'S'}, 'retired': False}
    for cid in ['A', 'B']:
        records['claims/'+cid]['payload']['opposed_position_ids'] = ['OLD']
    records['position_nodes/OLD'] = {'payload': {'position_id': 'OLD', 'title': 'old opposition'}, 'retired': True}
    assert 'position_nodes/OLD' in cm.audit_keys(request, records)
    assert 'claims/B' not in cm.audit_keys(request, records)
    # Existing full Retire requests retain their frozen scope.
    legacy_path = tmp_path / 'legacy'; legacy_path.mkdir()
    legacy = reference_request(legacy_path)
    assert 'claims/B' in cm.audit_keys(legacy, records)
    # Active shared support still requires all of its real current owners.
    records['position_nodes/OLD']['retired'] = False
    assert 'claims/B' in cm.audit_keys(request, records)


@pytest.mark.parametrize('concurrent_change', [False, True])
@pytest.mark.parametrize('reference_source', ['S', 'T'])
def test_retire_reference_is_pinned_but_not_reviewed_written_or_qualified(action, concurrent_change, reference_source):
    store, _, _, _, tmp_path, _ = action
    seed_claims(store, ['C', 'D'])
    request, output = operation_request(action, 'Retire', ['C'])
    request.policy = cm.source.FileBinding.model_validate(binding(cm.DEFAULT_POLICY.parent / 'wang_claim_management_opus_only_v1.json'))
    if reference_source == 'T':
        file = tmp_path / 'reference-source.json'
        file.write_text(json.dumps({'script': [{'text': '乙是对照原文。'}]}))
        request.source_bindings['T'] = cm.source.FileBinding.model_validate(binding(file))
        changed = deepcopy(store.get_record_state('claims', 'D')['payload'])
        changed['occurrences'] = [{'source_id': 'T', 'anchors': []}]
        store.apply_plan(store.plan_package({'package_id': 'explicit-other-reference-source',
            'claims': [changed], 'source_documents': [{'source_id': 'T', 'title': 'reference',
            'source_type': 'sermon_transcript', 'source_path': str(file), 'source_file_sha256': binding(file)['file_sha256']}]}))
    authorize(request, {'retirement_reference_keys': ['claims/D']})
    original = store.get_record_state('claims', 'D')
    def check(packet):
        assert set(packet['sources']) == ({'S'} if reference_source == 'S' else {'S', 'T'})
        assert packet['retirement_reference_claims']['claims/D'] == cm.model_state(original)
        assert 'claims/D' not in packet['before']
        assert packet['action_obligations']['target_keys'] == ['claims/C']
        assert all(p['object_id'] != 'D' for p in packet['request']['permissions'])
    def propose(packet):
        check(packet)
        candidate = cm.existing_candidate(packet['before'])
        for disposition in candidate.dispositions:
            if disposition.record_key == 'claims/C':
                disposition.action = 'retire'
                disposition.reason = 'synthetic teaching preserved in readonly reference'
                disposition.citations = [cm.Citation(source_id='S', paragraph_key='S0001', exact_quote='甲是假设，不是断言。')]
        return candidate.model_dump(mode='json')
    def review(packet):
        check(packet)
        assert 'claims/D' not in packet['denominator']
        if concurrent_change:
            changed = deepcopy(original['payload']); changed['statement'] = '乙是另一项判断。'
            store.apply_plan(store.plan_package({'package_id': 'concurrent-reference-edit', 'claims': [changed]}))
        return passing(packet)
    ports = SyntheticPorts(store, tmp_path, proposal=propose, opus=review)
    result = execute_claim_management(store, request, root=output, ports=ports)
    assert ports.calls == ['gpt', 'opus']
    parent = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    child = store.get_cvp_group(next(iter(parent['latest_rounds'].values())))
    assert 'claims/D' in child['materials']['transaction_before']
    assert 'D' not in child['claim_ids']
    assert not store.claim_management_qualification('D')
    if concurrent_change:
        assert result['status'] == 'paused', result
        assert not store.get_record_state('claims', 'C')['retired']
        assert not child['change_set_ids']
    else:
        assert result['status'] == 'completed', result
        assert store.get_record_state('claims', 'C')['retired']
        assert store.get_record_state('claims', 'D') == original


def test_other_operations_cannot_import_retirement_reference_authority(action):
    store, _, _, _, _, _ = action
    seed_claims(store, ['C', 'D'])
    request, _ = operation_request(action, 'Update', ['C'])
    authorize(request, {'retirement_reference_keys': ['claims/D']})
    with pytest.raises(ValueError, match='belongs only to Retire'):
        cm.load_authority(request)


def test_legacy_two_source_targets_retire_sequentially_without_reviving_old_shared_position(action):
    store, _, _, _, tmp_path, _ = action
    other = tmp_path / 'old-note-source.json'
    other.write_text(json.dumps({'script': [{'text': '甲是假设，不是断言。'}]}))
    seed_claims(store, ['D'])
    store.apply_plan(store.plan_package({'package_id': 'legacy-retired-position',
        'source_documents': [{'source_id': 'T', 'source_type': 'notes_manuscript', 'title': 'old notes',
            'source_path': str(other), 'source_file_sha256': binding(other)['file_sha256']}],
        'claims': [{'claim_id': cid, 'statement': '甲是假设。', 'claim_type': 'exegesis',
            'occurrences': [{'source_id': sid, 'anchors': []} for sid in ['S', 'T']],
            'opposed_position_ids': ['P']} for cid in ['A', 'B']],
        'position_nodes': [{'position_id': 'P', 'title': 'old opposition'}]}))
    # Reproduce legacy data predating the dangling-reference guard. Normal
    # retirement correctly refuses to create this historical inconsistency.
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET retired_at=now() WHERE collection='position_nodes' AND object_id='P'")
    old_position = store.get_record_state('position_nodes', 'P')
    request, output = operation_request(action, 'Retire', ['A', 'B'])
    request.source_bindings['T'] = cm.source.FileBinding.model_validate(binding(other))
    policy = cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent / 'wang_claim_management_opus_only_v1.json').read_text())
    policy.budget.gpt_calls = policy.budget.opus_calls = 3
    policy.budget.max_revisions = 2
    policy_path = tmp_path / 'two-unit-retirement-policy.json'; policy_path.write_text(policy.model_dump_json())
    request.policy = cm.source.FileBinding.model_validate(binding(policy_path))
    authorize(request, {'retirement_reference_keys': ['claims/D']})
    def propose(packet):
        target = packet['unit_request']['target_keys'][0]
        assert {k for k in packet['before'] if k.startswith('claims/')} == {target}
        assert packet['before']['position_nodes/P']['retired']
        candidate = cm.existing_candidate(packet['before'])
        disposition = next(d for d in candidate.dispositions if d.record_key == target)
        disposition.action = 'retire'
        disposition.reason = 'synthetic old duplicate with preserved reference teaching'
        disposition.citations = [cm.Citation(source_id='S', paragraph_key='S0001', exact_quote='甲是假设，不是断言。')]
        return candidate.model_dump(mode='json')
    ports = SyntheticPorts(store, tmp_path, proposal=propose)
    result = execute_claim_management(store, request, root=output, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['gpt', 'opus', 'gpt', 'opus']
    assert all(store.get_record_state('claims', cid)['retired'] for cid in ['A', 'B'])
    assert store.get_record_state('position_nodes', 'P') == old_position
    assert not store.claim_management_qualification('D')
