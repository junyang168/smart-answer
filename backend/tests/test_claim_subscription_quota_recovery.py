"""Quota recovery keeps the subscription, spent call and saved candidate."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import time

import pytest

from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_commit import binding
from backend.tests.test_claim_opus_api_transition import paused
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_owner_call_extension import authorize


def subscription_successor(paused):
    store, old, root, previous, successor, next_root, policy = paused
    policy = policy.model_copy(deep=True)
    policy.models = deepcopy(previous['policy']['models'])
    policy.api_spend = previous['policy'].get('api_spend')
    for slot in previous['call_reservations'].values():
        if slot['state'] == 'started':
            path = root/slot['round_id']/'opus.transport/exit.json'
            value = json.loads(path.read_text())
            value['finished_at'] = datetime.fromtimestamp(time.time()-2, timezone.utc).isoformat()
            path.write_text(json.dumps(value))
    capacity = next_root.parent/'capacity-resumption.json'
    capacity.write_text(json.dumps({'previous_state': {'blocked':'usage_limit_reached'},
        'owner_direction':'繼續吧', 'recovery_id':'one-owner-resumption',
        'resumed_at':time.time(), 'model_calls':0, 'budget_extension':False}))
    authorize(successor, policy, next_root.with_suffix('.policy.json'))
    authority = json.loads(cm.source.verify_file(successor.authorization).read_text())
    authority.pop('opus_api_transition', None)
    authority['opus_subscription_resumption'] = {'predecessor_action_id':old.action_id,
        'prior_metadata_sha256':cm.sha256_json(previous), 'authorization_quote':'繼續吧',
        'capacity_resumption':binding(capacity)}
    def save_authority():
        path = cm.source.verify_file(successor.authorization)
        path.write_text(json.dumps(authority))
        successor.authorization = cm.source.FileBinding.model_validate(binding(path))
    save_authority()
    return store, old, root, previous, successor, next_root, policy, authority, save_authority, capacity


@pytest.mark.parametrize('paused',['initial','saved_proposal'],indirect=True)
def test_subscription_quota_handoff_does_not_repeat_gpt_or_reset_spent_call(paused):
    store, old, root, previous, new, next_root, policy, _, _, _ = subscription_successor(paused)
    proof = cm.continuation_budget(previous,new,policy)
    assert proof['spent_generations']['opus'] == 1
    assert proof['remaining_generations']['opus'] == policy.budget.opus_calls
    assert proof['opus_subscription_resumption']
    ports = SyntheticPorts(store,next_root)
    result = execute_claim_management(store,new,root=next_root,ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['opus']
    if proof['spent_generations']['gpt']:
        assert store.get_record_state('claims','C')['payload']['statement'] == '甲是假设，不是断言。'
    parent = store.get_cvp_group('CMA-'+cm.sha256_json(old.action_id))
    assert parent['continued_by'] == 'CMA-'+cm.sha256_json(new.action_id)
    for key,slot in previous['call_reservations'].items():
        if slot['state'] != 'planned':assert parent['call_reservations'][key] == slot
    assert execute_claim_management(store,new,root=next_root,ports=ports) == result
    assert ports.calls == ['opus']


@pytest.mark.parametrize('fault',['unknown','partial','tampered','old_resumption','unrelated_block','metadata','budget','backend','no_authority'])
def test_subscription_quota_resumption_refuses_invalid_or_wider_replay(paused,fault):
    _,_,root,previous,new,_,policy,authority,save_authority,capacity = subscription_successor(paused)
    slot = next(v for v in previous['call_reservations'].values() if v['state']=='started')
    transport = root/slot['round_id']/'opus.transport'
    if fault == 'unknown':(transport/'exit.json').unlink()
    elif fault in {'partial','tampered'}:
        path = transport/'stdout.log';v=json.loads(path.read_text());v['usage']['output_tokens']=1;path.write_text(json.dumps(v))
        if fault == 'partial':
            from backend.pipeline.subscription_process import digest_file
            receipt=transport/'exit.json';e=json.loads(receipt.read_text());e['files']['stdout.log']=digest_file(path);receipt.write_text(json.dumps(e))
    elif fault in {'old_resumption','unrelated_block'}:
        value=json.loads(capacity.read_text())
        if fault=='old_resumption':value['resumed_at']=1
        else:value['previous_state']['blocked']='authentication_required'
        capacity.write_text(json.dumps(value));authority['opus_subscription_resumption']['capacity_resumption']=binding(capacity);save_authority()
    elif fault=='metadata':authority['opus_subscription_resumption']['prior_metadata_sha256']='0'*64;save_authority()
    elif fault=='budget':policy.budget.opus_calls+=1
    elif fault=='backend':policy.models['opus']['backend']='anthropic_api'
    else:authority.pop('opus_subscription_resumption');save_authority()
    with pytest.raises((ValueError,FileNotFoundError)):
        cm.continuation_budget(previous,new,policy)


def test_subscription_handoff_still_refuses_changed_claim_content(paused):
    store, _, _, _, new, next_root, _, _, _, _ = subscription_successor(paused)
    payload=deepcopy(store.get_record_state('claims','C')['payload'])
    payload['statement']='Changed independently after quota refusal'
    store.apply_plan(store.plan_package({'package_id':'semantic-drift-after-quota','claims':[payload]}))
    ports=SyntheticPorts(store,next_root)
    with pytest.raises((ValueError,RuntimeError),match='drift|changed'):
        execute_claim_management(store,new,root=next_root,ports=ports)
    assert not ports.calls
