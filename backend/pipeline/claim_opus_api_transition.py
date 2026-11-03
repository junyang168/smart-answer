"""Explicit Opus transport/quota continuations; no erased call or new budget."""
import json
from pathlib import Path

from backend.pipeline.cvp_sql_commit import binding, check_binding


def authorize_subscription_resumption(previous, request, policy, authority):
    """Bind a new owner resumption after a proved, settled quota refusal."""
    from backend.pipeline import claim_management as cm
    proof = authority.get('opus_subscription_resumption')
    if proof is None:
        return None
    old = cm.Request.model_validate(previous['request'])
    original = cm.Policy.model_validate(previous['policy'])
    expected = {'predecessor_action_id', 'prior_metadata_sha256',
                'authorization_quote', 'capacity_resumption'}
    if (not isinstance(proof, dict) or set(proof) != expected
            or proof['predecessor_action_id'] != old.action_id
            or proof['prior_metadata_sha256'] != cm.sha256_json(previous)
            or not (proof['authorization_quote'] or '').strip()
            or previous['result'].get('status') != 'paused'
            or previous['result'].get('reason') != 'model_failed'
            or authority.get('authorized_by') != 'owner'
            or authority.get('human_content_approval') is not False
            or policy.models != original.models or policy.api_spend != original.api_spend
            or original.models['opus']['backend'] != 'claude_subscription'
            or policy.initial_candidate != 'existing'
            or request.permissions != old.permissions
            or request.creation_permissions != old.creation_permissions):
        raise ValueError('subscription resumption requires the exact owner-bound paused scope and backend')
    check_binding(proof['capacity_resumption'])
    resumed = json.loads(Path(proof['capacity_resumption']['path']).read_text())
    if (resumed.get('previous_state', {}).get('blocked') != 'usage_limit_reached'
            or resumed.get('model_calls') != 0 or resumed.get('budget_extension') is not False
            or not resumed.get('owner_direction') or not resumed.get('recovery_id')
            or type(resumed.get('resumed_at')) not in (int, float)):
        raise ValueError('subscription resumption requires a verified common-capacity recovery')
    refused = []
    from datetime import datetime
    for key, slot in previous['call_reservations'].items():
        if slot['state'] != 'started':
            continue
        receipt = quota_refusal(previous, key, slot)
        ended = json.loads(Path(receipt['exit']['path']).read_text()).get('finished_at')
        if not ended or datetime.fromisoformat(ended).timestamp() >= resumed['resumed_at']:
            raise ValueError('subscription resumption must be newer than its quota refusal')
        refused.append(key)
    if len(refused) != 1:
        raise ValueError('subscription resumption requires exactly one proved quota refusal')
    return proof


def authorize(previous, request, policy, authority):
    from backend.pipeline import claim_management as cm
    proof=authority.get('opus_api_transition')
    if proof is None:return None
    old=cm.Request.model_validate(previous['request'])
    original=cm.Policy.model_validate(previous['policy'])
    models={**original.models,'opus':{**original.models['opus'],'backend':'anthropic_api'}}
    expected={'predecessor_action_id','prior_metadata_sha256','authorization_quote','models','api_spend'}
    if (not isinstance(proof,dict) or set(proof)!=expected
            or proof['predecessor_action_id']!=old.action_id
            or proof['prior_metadata_sha256']!=cm.sha256_json(previous)
            or not (proof['authorization_quote'] or '').strip()
            or proof['models']!=models or policy.models!=models
            or proof['api_spend']!=policy.api_spend
            or original.models['opus']['backend']!='claude_subscription'
            or previous['result'].get('reason') not in {'model_failed','engineering_required','model_capacity_paused'}
            or previous['result'].get('status')!='paused'
            or request.permissions!=old.permissions or request.creation_permissions!=old.creation_permissions
            or policy.initial_candidate not in {'existing','proposal'} or authority.get('authorized_by')!='owner'
            or authority.get('human_content_approval') is not False):
        raise ValueError('API transport change requires exact owner-bound original action, authority and model')
    return proof


def quota_refusal(previous, key, slot):
    """A returned zero-token Claude quota refusal stays spent in its old ledger."""
    from backend.pipeline import claim_management as cm
    root=Path(previous['execution_root'])/slot['round_id']
    path=root/(slot['role']+'.transport')
    intent=root/(slot['role']+'.reservation.json')
    receipt=path/'exit.json'
    # Check the same immutable intent and byte-bound durable exit used by replay.
    from backend.pipeline.source_reconciliation_runner import body,read
    if (slot['role']!='opus' or slot['state']!='started' or not intent.exists()
            or body(read(intent))!=slot.get('intent') or cm.sha256_json(slot['intent'])!=slot['intent_sha256']
            or (root/'opus.raw.json').exists() or not receipt.exists()):
        raise ValueError('API continuation cannot discard an unknown or returned semantic call')
    saved=json.loads(receipt.read_text())
    if saved.get('returncode')!=1:raise ValueError('not a returned Claude quota refusal')
    for name,digest in saved['files'].items():
        check_binding({'path':str(path/name),'file_sha256':digest})
    envelope=json.loads((path/'stdout.log').read_text())
    usage=envelope.get('usage',{})
    if (envelope.get('is_error') is not True or envelope.get('api_error_status')!=429
            or envelope.get('api_error')!='usage_limit_reached'
            or usage.get('input_tokens')!=0 or usage.get('output_tokens')!=0
            or envelope.get('modelUsage') or (path/'response.json').exists()):
        raise ValueError('API continuation requires exact zero-token subscription quota evidence')
    return {'exit':binding(receipt),'original_slot':key,'spent_call_preserved':True}
