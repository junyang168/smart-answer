"""Explicit owner resume of a proved subscription quota rejection, once.

Original outcome_unknown evidence is never deleted or relabelled. Only an
exact quota/no-answer transport can receive this manual continuation. Human
decided items are removed with their own exact audit authority, not a pass.
"""
from pathlib import Path
from backend.api.canonical_repository.postgres_store import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable

SCHEMA = 'wang_cvp_sql_manual_quota_resume_v1'
PROOF = 'owner-quota-resume-proof.json'
NAME = 'manual-quota-resume-binding.json'
FILES = ('packet.json', 'raw-gpt.call-intent.json', 'raw-gpt.call-failure.json',
         'raw-gpt.request-encoding.json', 'raw-gpt.request-budget.json',
         'failed-transport-gpt.json', 'failure.json')


def sealed(value):
    if value.get('artifact_sha256') != sha256_json({k:v for k,v in value.items() if k!='artifact_sha256'}):
        raise ValueError('manual quota resume seal drift')


def quota_failure(root):
    """Fail closed on timeout/network, output, absent receipt or other errors."""
    from backend.pipeline.viewpoint_resolution_runtime import subscription_client, model_request_identity
    from backend.pipeline.viewpoint_audit_source_adjudication import source_review_adapter
    root=Path(root)
    if any((root/n).exists() for n in ('raw-gpt.json','gpt-final-handoff.json')):
        raise ValueError('quota resume cannot repeat an answered request')
    packet=read_artifact(root/'packet.json');sealed(packet)
    intent=read_artifact(root/'raw-gpt.call-intent.json');sealed(intent)
    failure=read_artifact(root/'raw-gpt.call-failure.json');sealed(failure)
    transport=read_artifact(root/'failed-transport-gpt.json')
    raw=transport.get('raw_response',{})
    quota='You’ve hit your usage limit.'
    stderr=raw.get('stderr','');last=stderr.rstrip().splitlines()
    if (raw.get('stdout') != '' or raw.get('last_message') not in (None,'')
            or not last or not last[-1].startswith('ERROR: '+quota)
            or 'startup websocket prewarm setup failed: '+quota not in stderr
            or not transport.get('error','').startswith('Codex subscription generation failed (exit 1):')
            or failure.get('error_type')!='CodexSubscriptionError'
            or failure.get('execution_status')!='outcome_unknown'
            or failure.get('automatic_retry') is not False
            or failure.get('invocation_sha256')!=intent['artifact_sha256']):
        raise ValueError('manual resume requires exact startup quota/no-answer rejection')
    adapter=source_review_adapter(subscription_client('codex','gpt-6.1-sol','high'),root/'raw-gpt.json')
    expected={'schema_version':'wang_model_call_intent_v1',**model_request_identity(adapter,packet),
              'cache_path':str((root/'raw-gpt.json').resolve()),'automatic_retry':False}
    if intent!={**expected,'artifact_sha256':sha256_json(expected)}:
        raise ValueError('quota original request/model/config drift')
    encoding={'schema_version':'wang_model_request_encoding_proof_v1',**adapter.request_encoding_proof(packet)}
    if read_artifact(root/'raw-gpt.request-encoding.json')!={**encoding,'artifact_sha256':sha256_json(encoding)}:
        raise ValueError('quota original wire/encoding drift')
    budget=read_artifact(root/'raw-gpt.request-budget.json')
    if (budget.get('request_identity')!=model_request_identity(adapter,packet)
            or budget.get('actual_request_bytes')!=adapter.request_bytes(packet)
            or budget.get('max_request_bytes')!=600000):
        raise ValueError('quota original request budget drift')
    return packet


def verify_delta(old,current,human_ids):
    from backend.pipeline.viewpoint_audit_source_adjudication import restricted_packet
    oldids={i['item_id'] for i in old['items']}
    if not human_ids or not human_ids < oldids or current!=restricted_packet(old,oldids-human_ids):
        raise ValueError('quota continuation may only exclude exact human-decided items')


def make_direction(store,run_id,original,quote,human_direction):
    from backend.pipeline.cvp_sql_flow import binding
    from backend.pipeline.cvp_exact_human_audit_retention import validate_direction,verify_item
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet,restricted_packet
    old=quota_failure(original);audit=read_artifact(old['audit_binding']['path'])
    actual=build_packet(Path(old['audit_binding']['path']),store,True)
    ids={i['item_id'] for i in old['items']}
    if restricted_packet(actual,ids)!=old:raise ValueError('quota current audited material drift')
    h=read_artifact(human_direction);validate_direction(h)
    human={i['item_id'] for i in h['items']}
    for i in old['items']:
        if i['item_id'] in human:verify_item(i,h,audit)
    current=restricted_packet(old,ids-human)
    verify_delta(old,current,human)
    d={'schema_version':SCHEMA,'run_id':run_id,'campaign_sha256':store.get_cvp_group(run_id)['campaign_sha256'],
       'authorized_by':'owner','authorization_quote':quote,'maximum_additional_calls':1,
       'automatic_future_retries_authorized':False,'human_content_approval':False,
       'original_root':str(Path(original).resolve()),'original_bindings':[binding(Path(original)/n) for n in FILES],
       'audit_binding':old['audit_binding'],'human_direction':binding(human_direction),
       'human_item_ids':sorted(human),'corrected_packet_sha256':current['artifact_sha256']}
    return {**d,'artifact_sha256':sha256_json(d)},current


def verify_proof(path):
    from backend.pipeline.cvp_sql_flow import check_binding
    from backend.pipeline.viewpoint_audit_source_adjudication import restricted_packet
    from backend.pipeline.cvp_exact_human_audit_retention import verify_item,validate_direction
    proof=read_artifact(path);sealed(proof)
    auth=proof['authorization'];check_binding(auth);d=read_artifact(auth['path']);sealed(d)
    if (d.get('schema_version')!=SCHEMA or d.get('authorized_by')!='owner' or not d.get('authorization_quote')
            or d.get('maximum_additional_calls')!=1 or d.get('automatic_future_retries_authorized') is not False
            or d.get('human_content_approval') is not False
            or Path(path).parent.resolve()!=(Path(d['original_root'])/'manual-quota-resume-once').resolve()):
        raise ValueError('quota resume lacks exact fixed owner once authority')
    expected=[Path(d['original_root'])/n for n in FILES]
    if [Path(b['path']) for b in d['original_bindings']]!=expected:
        raise ValueError('quota original receipt population drift')
    for b in [*d['original_bindings'],d['audit_binding'],d['human_direction']]:check_binding(b)
    old=quota_failure(d['original_root']);audit=read_artifact(d['audit_binding']['path'])
    if old['audit_binding']!=d['audit_binding']:raise ValueError('quota audit binding drift')
    human=read_artifact(d['human_direction']['path']);validate_direction(human)
    ids=set(d['human_item_ids'])
    if ids!={i['item_id'] for i in human['items']} or human['campaign_sha256']!=d['campaign_sha256'] or human['run_id']!=d['run_id']:
        raise ValueError('quota human disposition identity drift')
    for i in old['items']:
        if i['item_id'] in ids:verify_item(i,human,audit)
    current=read_artifact(Path(path).parent/'packet.json');sealed(current)
    verify_delta(old,current,ids)
    if current['artifact_sha256']!=d['corrected_packet_sha256']:raise ValueError('quota continuation packet drift')
    return d,proof


def execute(store,run_id,auth):
    from backend.pipeline.cvp_sql_flow import binding
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet,restricted_packet,run
    from backend.pipeline.viewpoint_adjudication_history import target_identity
    from backend.pipeline.cvp_exact_human_audit_retention import partition
    d=read_artifact(auth);sealed(d)
    if d['run_id']!=run_id or d['campaign_sha256']!=store.get_cvp_group(run_id)['campaign_sha256']:
        raise ValueError('quota direction foreign group/campaign')
    original=Path(d['original_root']);old=quota_failure(original)
    ids={i['item_id'] for i in old['items']}
    actual=restricted_packet(build_packet(Path(d['audit_binding']['path']),store,True),ids)
    if actual!=old:raise ValueError('quota original current source/SQL drift')
    current,human=partition(read_artifact(d['audit_binding']['path']),old,original.parents[2],store)
    verify_delta(old,current,human)
    out=original/'manual-quota-resume-once'
    write_immutable(out/'packet.json',current)
    body={'authorization':binding(auth)}
    write_immutable(out/PROOF,{**body,'artifact_sha256':sha256_json(body)})
    verify_proof(out/PROOF)
    store.register_adjudication_input_repair(run_id,targets=[target_identity(i) for i in current['items']],
        original_root=original,original_packet=d['original_bindings'][0],root=out,
        packet_binding=binding(out/'packet.json'),authorization=binding(auth),quota_resume=True)
    if not (out/'raw-gpt.json').exists():
        import os
        from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
        os.environ['CODEX_EXECUTABLE']=canonical_runtime()['identity']['cli']['path']
    return run(Path(d['audit_binding']['path']),out,store,final_gpt=True,history_roots=[],
               only_item_ids={i['item_id'] for i in current['items']})
