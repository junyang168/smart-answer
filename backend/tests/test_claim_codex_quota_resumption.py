"""A closed no-answer quota refusal is not an unknown or a spent generation."""
import json
import subprocess
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
from backend.pipeline.subscription_process import digest_file
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import operation_request, seed_claims, SyntheticPorts
from backend.tests.test_claim_owner_call_extension import authorize


@pytest.fixture
def refused(action, monkeypatch):
    store = action[0]
    seed_claims(store, ['C'])
    old, root = operation_request(action, 'Update', ['C'])
    policy = cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v3.json').read_text())
    policy.initial_candidate = 'proposal'
    policy.model_output_contract = 'claim_changes_v2'
    authorize(old, policy, root.with_suffix('.policy.json'))
    message = 'You’ve hit your usage limit. Try again tomorrow.'
    output = '\n'.join(json.dumps(e, ensure_ascii=False) for e in [
        {'type': 'thread.started', 'thread_id': 'test'}, {'type': 'turn.started'},
        {'type': 'error', 'message': message}, {'type': 'turn.failed', 'error': {'message': message}}])
    class FailedClient(CodexSubscriptionClient):
        def _verify_chatgpt_login(self):
            pass
        def generate_json(self, *args, **kwargs):
            def fail(command, **options):
                options['stdout'].write(output)
                options['stderr'].write('WARN startup websocket prewarm setup failed: ' + message)
                return subprocess.CompletedProcess(command, 1)
            with monkeypatch.context() as patch:
                patch.setattr('backend.pipeline.subscription_process.subprocess.run', fail)
                return super().generate_json(*args, **kwargs)
    class FailedPorts(SyntheticPorts):
        def claim_management_adapter(self, role, policy):
            adapter = claim_management_model_adapter(role, policy)
            adapter._client = FailedClient(model=policy.models[role]['model_id'], executable='/bin/echo')
            return adapter
    assert execute_claim_management(store, old, root=root, ports=FailedPorts(store, root))['reason'] == 'model_failed'
    previous = store.get_cvp_group('CMA-' + cm.sha256_json(old.action_id))
    previous = {k: v for k, v in previous.items() if k not in {'run_id', 'status', 'error'}}
    new, following = operation_request(action, 'Update', ['C'])
    new.predecessor_action_id = old.action_id
    new.permissions = old.permissions
    policy.budget.gpt_calls = policy.budget.opus_calls = policy.budget.max_revisions = 1
    authorize(new, policy, following.with_suffix('.policy.json'))
    key = next(k for k, v in previous['call_reservations'].items() if v['state'] == 'started')
    status = root/'current-allowance.json'
    status.write_text(json.dumps({'model_calls': 0, 'response': {'result': {'ordinaryUsageAllowed': True}}}))
    direction = {'slot': key, 'predecessor_action_id': old.action_id,
        'prior_metadata_sha256': cm.sha256_json(previous), 'authorization_quote': 'GPT has allowance; continue original task',
        'current_rate_limits': binding(status)}
    path = Path(new.authorization.path)
    authority = json.loads(path.read_text())
    authority['codex_quota_resumption'] = direction
    path.write_text(json.dumps(authority))
    new.authorization = cm.source.FileBinding.model_validate(binding(path))
    return store, old, root, previous, new, following, policy, direction


def test_original_remaining_budget_resumes_without_repeating_an_answer(refused):
    store, old, _, previous, new, root, policy, _ = refused
    proof = cm.continuation_budget(previous, new, policy)
    assert proof['spent_generations']['gpt'] == 0
    assert proof['codex_quota_resumption']
    ports = SyntheticPorts(store, root,
        proposal=lambda packet: {'changes': [], 'findings': [], 'finding_resolutions': []},
        opus=lambda packet: {'complete_review': True, 'findings': [], 'prior_findings': []})
    result = execute_claim_management(store, new, root=root, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['gpt', 'opus']
    after = store.get_cvp_group('CMA-' + cm.sha256_json(old.action_id))
    for key, slot in previous['call_reservations'].items():
        if slot['state'] != 'planned':
            assert after['call_reservations'][key] == slot
    assert store.claim_management_qualification('C')['ready']


@pytest.mark.parametrize('fault', ['no_direction', 'quota_still_blocked', 'metadata', 'missing_exit',
    'partial_output', 'other_error', 'schema', 'second_resumption'])
def test_quota_recovery_rejects_unknown_output_drift_and_implicit_retries(refused, fault):
    _, _, root, previous, new, _, policy, direction = refused
    slot = previous['call_reservations'][direction['slot']]
    transport = root/slot['round_id']/'gpt.transport'
    path = Path(new.authorization.path)
    auth = json.loads(path.read_text())
    if fault == 'no_direction':
        auth.pop('codex_quota_resumption')
    elif fault == 'quota_still_blocked':
        status = Path(direction['current_rate_limits']['path'])
        status.write_text(json.dumps({'model_calls': 0, 'response': {'result': {'ordinaryUsageAllowed': False}}}))
        auth['codex_quota_resumption']['current_rate_limits'] = binding(status)
    elif fault == 'metadata':
        auth['codex_quota_resumption']['prior_metadata_sha256'] = '0'*64
    elif fault == 'missing_exit':
        (transport/'exit.json').unlink()
    elif fault == 'schema':
        (transport/'response-schema.json').write_text('{}')
    elif fault == 'second_resumption':
        previous['continuation'] = {'codex_quota_resumption': {'used': True}}
        auth['codex_quota_resumption']['prior_metadata_sha256'] = cm.sha256_json(previous)
    else:
        from backend.pipeline.source_reconciliation_runner import body, read, write
        stdout = transport/'stdout.log'
        if fault == 'partial_output':
            stdout.write_text(stdout.read_text() + '\n' + json.dumps({'type': 'item.completed', 'item': {'text': 'partial'}}))
        else:
            stdout.write_text(stdout.read_text().replace('You’ve hit your usage limit.', 'network error'))
        receipt = transport/'exit.json'
        value = json.loads(receipt.read_text())
        value['files']['stdout.log'] = digest_file(stdout)
        receipt.write_text(json.dumps(value))
        failed = root/slot['round_id']/'gpt.failure.json'
        value = body(read(failed))
        value['provider_raw']['stdout'] = stdout.read_text()
        failed.unlink()
        write(failed, value)
    path.write_text(json.dumps(auth))
    new.authorization = cm.source.FileBinding.model_validate(binding(path))
    with pytest.raises((ValueError, FileNotFoundError)):
        cm.continuation_budget(previous, new, policy)
