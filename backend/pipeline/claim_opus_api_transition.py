"""Explicit subscription-to-API continuation; no erased call or new budget."""
import json
from pathlib import Path

from backend.pipeline.cvp_sql_commit import binding, check_binding


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
