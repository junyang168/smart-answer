"""Known returned no-progress proposals stay spent across an engineering fix."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm, claim_management_intake as intake
from backend.pipeline import claim_management_batch as batch
from backend.pipeline.claim_review_recovery import returned_no_progress_input
from backend.pipeline.claim_joint_update import finding_permission_extension
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.subscription_process import digest_file
from backend.tests.test_claim_managed_update import shared, CITATION
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_management_intake import pass_review


@pytest.fixture
def stopped(shared, monkeypatch):
    store, root = shared
    old_fields = intake.FIELDS['source_fragments'] - {'media_end_time'}
    monkeypatch.setitem(intake.FIELDS, 'source_fragments', old_fields)
    saved = intake.prepare(['C'], root, instruction='Refresh all related source locators', store=store)
    request, execution = next(iter(batch.load(saved['manifest']['path'], root / 'batch', store=store)[1].values()))
    def resolutions(packet):
        return [{'finding_id': f, 'disposition': 'persistent', 'reason': 'original field grant is missing',
            'citations': [CITATION], 'affected_target_keys': ['claims/C'],
            'affected_write_keys': ['source_fragments/F'], 'impact_reason': 'source locator needs its end time'} for f in packet['unresolved']]
    def proposal(packet):
        return {'changes': [{'record_key': 'source_fragments/F', 'fields_json': json.dumps({'media_time': 2}),
            'reason': 'move the start', 'citations': [CITATION]}], 'creations': [], 'retirements': [],
            'findings': [], 'finding_resolutions': resolutions(packet)}
    def review(packet):
        result = pass_review(packet)
        result['findings'] = [{'target_key': 'source_fragments/F', 'component': 'media_end_time', 'category': 'mapping',
            'reason': 'the original grant omitted the end time', 'acceptance_condition': 'grant and correct media_end_time',
            'citations': [CITATION], 'affected_target_keys': ['claims/C'],
            'affected_write_keys': ['source_fragments/F'] if packet['unresolved'] else [],
            'impact_reason': 'the paired locator must be correct'}]
        result['prior_findings'] = resolutions(packet)
        if packet['unresolved']:
            result['findings'] = []
        return result
    port = SyntheticPorts(store, execution, proposal=proposal, opus=review)
    result = execute_claim_management(store, request, root=execution, ports=port)
    assert result['reason'] == 'no_progress', result
    parent = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    key, slot = next((k, v) for k, v in parent['call_reservations'].items() if v['state'] == 'started')
    stage = Path(parent['execution_root']) / slot['round_id']
    raw_path = stage / 'gpt.raw.json'
    raw = json.loads(raw_path.read_text())
    # A synthetic durable successful transport, no provider invocation.
    raw['provider_raw'] = {'stdout': 'synthetic stdout', 'stderr': '', 'last_message': json.dumps(raw['response'])}
    raw['runtime_identity'] = {'file_sha256': 'a' * 64}
    raw['provider_raw_sha256'] = cm.sha256_json(raw['provider_raw'])
    raw['artifact_sha256'] = cm.sha256_json({k: v for k, v in raw.items() if k != 'artifact_sha256'})
    raw_path.write_text(json.dumps(raw))
    transport = stage / 'gpt.transport'
    transport.mkdir()
    (transport / 'stdout.log').write_text(raw['provider_raw']['stdout'])
    (transport / 'stderr.log').write_text('')
    (transport / 'response.json').write_text(json.dumps(raw['response']))
    (transport / 'invocation.json').write_text(json.dumps({'command_sha256': 'b' * 64,
        'input_sha256': 'c' * 64, 'executable_sha256': 'a' * 64, 'cwd': str(transport)}))
    (transport / 'exit.json').write_text(json.dumps({'returncode': 0,
        'files': {n: digest_file(transport / n) for n in ['stdout.log', 'stderr.log', 'response.json']}}))
    monkeypatch.setitem(intake.FIELDS, 'source_fragments', old_fields | {'media_end_time'})
    return parent, key, slot, stage, request


def test_returned_no_progress_is_consumed_without_content_acceptance(stopped):
    parent, key, slot, _, _ = stopped
    before = deepcopy(parent)
    result = returned_no_progress_input(parent, key, slot)
    assert result['response_state'] == 'returned_unaccepted' and not result['content_approved']
    assert parent == before and slot['state'] == 'started'


@pytest.mark.parametrize('damage', ['unknown_exit', 'changed_output', 'changed_intent', 'not_repeated', 'invocation'])
def test_no_progress_accounting_refuses_unknown_or_drifted_returns(stopped, damage):
    parent, key, slot, stage, _ = stopped
    if damage == 'unknown_exit': (stage / 'gpt.transport/exit.json').unlink()
    elif damage == 'changed_output': (stage / 'gpt.transport/response.json').write_text('{}')
    elif damage == 'changed_intent': slot['intent_sha256'] = '0' * 64
    elif damage == 'not_repeated': parent['candidate_fingerprints'] = {}
    else: (stage / 'gpt.transport/invocation.json').write_text('{}')
    with pytest.raises((ValueError, OSError)):
        returned_no_progress_input(parent, key, slot)


@pytest.mark.parametrize('damage', [None, 'claim_field', 'unrelated_object', 'remove_field'])
def test_engineering_field_extension_does_not_expand_content_scope(stopped, damage):
    parent, _, _, _, old = stopped
    request = old.model_copy(deep=True)
    fragment = next(p for p in request.permissions if p.key == 'source_fragments/F')
    fragment.fields.append('media_end_time')
    if damage == 'claim_field':
        fragment.fields.remove('media_end_time')
        next(p for p in request.permissions if p.key == 'claims/C').fields.append('new_identity')
    elif damage == 'unrelated_object': fragment.object_id = 'ABSENT'
    elif damage == 'remove_field': fragment.fields.remove('verbatim_excerpt')
    assert finding_permission_extension(parent, old, request) is (damage is None)


def test_linked_field_fix_uses_only_remaining_calls_and_normal_review(stopped, shared):
    from backend.api.canonical_repository.claim_identity_allocation import reserve
    parent, old_key, old_slot, stage, old = stopped
    store, original_root = shared
    root = original_root / 'linked-engineering'
    root.mkdir()
    policy = cm.Policy.model_validate(parent['policy']).model_copy(deep=True)
    policy.initial_candidate = 'proposal'
    policy.budget = policy.budget.model_copy(update={'max_revisions': 1, 'gpt_calls': 1, 'opus_calls': 1})
    policy_binding = intake.immutable(root / 'policy.json', policy.model_dump(mode='json'))
    request = old.model_copy(deep=True, update={'action_id': old.action_id + '-engineering',
        'predecessor_action_id': old.action_id, 'policy': cm.source.FileBinding.model_validate(policy_binding)})
    next(p for p in request.permissions if p.key == 'source_fragments/F').fields.append('media_end_time')
    authorization = {'schema_version': cm.VERSION, 'authorized_by': 'owner',
        'authorization_quote': 'Refresh all related source locators', 'human_content_approval': False,
        'request': request.model_dump(mode='json', exclude={'authorization', 'policy'}),
        'policy': policy_binding, 'required_maintenance_keys': [],
        'claim_creation_allocations': {'S': reserve(store, action_id=request.action_id, source_id='S',
            target_id='C', root=root / 'execution')}}
    request.authorization = cm.source.FileBinding.model_validate(intake.immutable(root / 'authority.json', authorization))
    with pytest.raises(ValueError, match='budget'):
        too_large = policy.model_copy(deep=True)
        too_large.budget.gpt_calls = 2
        cm.continuation_budget(parent, request, too_large)
    def proposal(packet):
        assert packet['continuation']['spent_generations']['gpt'] == 3
        assert old_key in packet['continuation']['unaccepted_no_progress_calls']
        return {'changes': [{'record_key': 'source_fragments/F',
            'fields_json': json.dumps({'media_time': 2, 'media_end_time': 3}),
            'reason': 'correct the complete locator', 'citations': [CITATION]}],
            'creations': [], 'retirements': [], 'findings': [], 'finding_resolutions': [
                {'finding_id': f, 'disposition': 'resolved', 'reason': 'complete locator fixed', 'citations': [CITATION],
                 'affected_target_keys': ['claims/C'], 'affected_write_keys': ['source_fragments/F'],
                 'impact_reason': 'the source locator is now complete'} for f in packet['unresolved']]}
    def review(packet):
        result = pass_review(packet)
        result['prior_findings'] = proposal(packet)['finding_resolutions']
        return result
    ports = SyntheticPorts(store, root / 'execution', proposal=proposal, opus=review)
    result = execute_claim_management(store, request, root=root / 'execution', ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['gpt', 'opus']
    assert store.get_record_state('source_fragments', 'F')['payload']['media_end_time'] == 3
    assert store.claim_management_qualification('C')['ready']
    predecessor = store.get_cvp_group('CMA-' + cm.sha256_json(old.action_id))
    assert predecessor['call_reservations'][old_key] == old_slot
    successor = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    assert successor['continuation']['spent_generations']['gpt'] == 3
