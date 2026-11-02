"""Known truncated CLI output is spent, never approved or treated as unknown."""
import json
import subprocess
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm
from backend.pipeline.claim_review_recovery import consumed_subscription_output_limit
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
from backend.pipeline.subscription_process import digest_file
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import operation_request, seed_claims, SyntheticPorts
from backend.tests.test_claim_owner_call_extension import authorize


@pytest.fixture
def truncated(action, monkeypatch):
    store = action[0]; seed_claims(store, ['C'])
    old, root = operation_request(action, 'Update', ['C'])
    policy = cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v3.json').read_text())
    policy.initial_candidate = 'proposal'
    policy.model_output_contract = 'claim_changes_v2'
    authorize(old, policy, root.with_suffix('.policy.json'))
    message = 'Incomplete response returned, reason: max_output_tokens'
    output = '\n'.join(json.dumps(e) for e in [
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{"changes":[],"findings":[],"finding_resolutions":[' + ' ' * 100}},
        {'type': 'error', 'message': message}, {'type': 'turn.failed', 'error': {'message': message}}])
    class FailedClient(CodexSubscriptionClient):
        def _verify_chatgpt_login(self): pass
        def generate_json(self, *args, **kwargs):
            def fail(command, **options):
                options['stdout'].write(output)
                return subprocess.CompletedProcess(command, 1)
            with monkeypatch.context() as patch:
                patch.setattr('backend.pipeline.subscription_process.subprocess.run', fail)
                return super().generate_json(*args, **kwargs)
    class FailedPorts(SyntheticPorts):
        def claim_management_adapter(self, role, policy):
            adapter = claim_management_model_adapter(role, policy)
            assert role == 'gpt'
            adapter._client = FailedClient(model=policy.models[role]['model_id'], executable='/bin/echo')
            return adapter
    assert execute_claim_management(store, old, root=root, ports=FailedPorts(store, root))['reason'] == 'model_failed'
    parent = store.get_cvp_group('CMA-' + cm.sha256_json(old.action_id))
    previous = {k: v for k, v in parent.items() if k not in {'run_id', 'status', 'error'}}
    new, next_root = operation_request(action, 'Update', ['C'])
    new.predecessor_action_id = old.action_id; new.permissions = old.permissions
    follow = policy.model_copy(deep=True)
    follow.budget.gpt_calls = follow.budget.opus_calls = follow.budget.max_revisions = 1
    before = {cm.source.key(*k): cm.model_state(v) for k, v in store.read_claim_context([('claims', 'C')]).items()}
    extension = {'basis': 'subscription_output_limit', 'predecessor_action_id': old.action_id,
        'prior_metadata_sha256': cm.sha256_json(previous), 'budget': follow.budget.model_dump(mode='json'),
        'authorization_quote': 'Automatically analyze and recover the finished output failure once',
        'current_objects_sha256': cm.sha256_json(before), 'related_action_metadata_sha256': {}}
    authorize(new, follow, next_root.with_suffix('.policy.json'), extension)
    return store, old, root, previous, new, next_root, follow, extension


def test_truncated_proposal_continues_with_one_pair_and_keeps_spend(truncated):
    store, old, root, previous, new, next_root, policy, _ = truncated
    proof = cm.continuation_budget(previous, new, policy)
    assert proof['spent_generations']['gpt'] == 1
    assert proof['subscription_output_limit_recovery'] and not proof['preserved_unknown_calls']
    def propose(packet):
        return {'changes': [], 'findings': [], 'finding_resolutions': []}
    ports = SyntheticPorts(store, next_root, proposal=propose,
        opus=lambda packet: {'complete_review': True, 'findings': [], 'prior_findings': []})
    result = execute_claim_management(store, new, root=next_root, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['gpt', 'opus']
    after = store.get_cvp_group('CMA-' + cm.sha256_json(old.action_id))['call_reservations']
    for key, slot in previous['call_reservations'].items():
        if slot['state'] != 'planned': assert after[key] == slot
        else: assert after[key]['state'] == 'cancelled' and after[key]['transferred_to']
    assert store.claim_management_qualification('C')['ready']
    assert execute_claim_management(store, new, root=next_root, ports=ports) == result
    assert ports.calls == ['gpt', 'opus']


@pytest.mark.parametrize('defect', ['missing_exit', 'stdout', 'wrong_input', 'schema', 'complete_answer', 'other_error', 'successful_turn'])
def test_truncation_recovery_refuses_unknown_tampered_and_other_failures(truncated, defect):
    _, _, root, previous, _, _, _, _ = truncated
    key, slot = next((k, s) for k, s in previous['call_reservations'].items() if s['state'] == 'started')
    transport = root/slot['round_id']/'gpt.transport'
    if defect == 'missing_exit': (transport/'exit.json').unlink()
    elif defect == 'stdout': (transport/'stdout.log').write_text('different output')
    elif defect == 'wrong_input':
        p = transport/'invocation.json'; value = json.loads(p.read_text()); value['input_sha256'] = '0'*64; p.write_text(json.dumps(value))
    elif defect == 'schema': (transport/'response-schema.json').write_text('{}')
    elif defect == 'complete_answer': (transport/'response.json').write_text('{}')
    else:
        p = transport/'stdout.log'
        p.write_text(p.read_text().replace('max_output_tokens', 'network_error') if defect == 'other_error' else p.read_text()+'\n'+json.dumps({'type': 'turn.completed'}))
        receipt = transport/'exit.json'; value = json.loads(receipt.read_text()); value['files']['stdout.log'] = digest_file(p); receipt.write_text(json.dumps(value))
        # Even a self-consistent transport/failure cannot change its error class.
        from backend.pipeline.source_reconciliation_runner import body, read, write
        failure = root/slot['round_id']/'gpt.failure.json'; value = body(read(failure)); value['provider_raw']['stdout'] = p.read_text(); failure.unlink(); write(failure, value)
    with pytest.raises((ValueError, FileNotFoundError)):
        consumed_subscription_output_limit(previous, key, slot)


@pytest.mark.parametrize('defect', ['extra_calls', 'larger_output', 'second_recovery'])
def test_truncated_followup_has_bounded_authority(truncated, defect):
    _, _, _, previous, new, next_root, policy, extension = truncated
    if defect == 'extra_calls': policy.budget.gpt_calls = 2
    elif defect == 'larger_output': policy.budget.max_output_tokens += 1
    else:
        previous['continuation'] = {'subscription_output_limit_recovery': {'spent': True}}
        extension['prior_metadata_sha256'] = cm.sha256_json(previous)
    extension['budget'] = policy.budget.model_dump(mode='json')
    authorize(new, policy, next_root.with_suffix('.policy.json'), extension)
    with pytest.raises(ValueError): cm.continuation_budget(previous, new, policy)


def test_a_second_truncation_needs_the_new_schema_mitigation_and_same_spend_bounds(truncated):
    _, _, _, previous, new, next_root, policy, extension = truncated
    previous['continuation'] = {'subscription_output_limit_recovery': {'spent': True}}
    extension['prior_metadata_sha256'] = cm.sha256_json(previous)
    authorize(new, policy, next_root.with_suffix('.policy.json'), extension)
    with pytest.raises(ValueError): cm.continuation_budget(previous, new, policy)
    policy.proposal_empty_arrays = 'program_validated_v1'
    authorize(new, policy, next_root.with_suffix('.policy.json'), extension)
    proof = cm.continuation_budget(previous, new, policy)
    assert proof['spent_generations']['gpt'] == 1 and proof['subscription_output_limit_recovery']
    previous['policy']['proposal_empty_arrays'] = 'program_validated_v1'
    extension['prior_metadata_sha256'] = cm.sha256_json(previous)
    authorize(new, policy, next_root.with_suffix('.policy.json'), extension)
    with pytest.raises(ValueError): cm.continuation_budget(previous, new, policy)
