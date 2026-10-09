"""One explicitly authorized scope amendment, not a fresh issue budget.

Old discovery/proposal remains immutable. Only a pre-review, uncommitted action
may acquire this once-only amendment; corrections/refusals/unknown calls do not.
"""
import json
from pathlib import Path

from backend.api.canonical_repository.postgres_store import sha256_json
from backend.pipeline import source_reconciliation as c
from backend.pipeline.source_reconciliation_runner import body, read, write, binding, validate_raw

SCHEMA = 'wang_claim_issue_scope_amendment_v1'
NAME = 'claim-issue-rescope-authorization-binding.json'


def validate_direction(direction, *, snapshots=None):
    if (direction.get('schema_version') != SCHEMA or direction.get('authorized_by') != 'owner'
            or not direction.get('authorization_quote') or direction.get('human_content_approval') is not False
            or direction.get('additional_proposal_limit') != 1
            or direction.get('artifact_sha256') != sha256_json({k:v for k,v in direction.items() if k!='artifact_sha256'})):
        raise ValueError('scope amendment lacks exact owner/once authority')
    for name in ('old_materials', 'old_packet', 'old_raw', 'new_policy'):
        c.bound_bytes(direction[name], snapshots)
    root = Path(direction['old_action_root'])
    if root.name != direction['old_action_id'] or any(
            Path(direction[n]['path']) != root/f for n,f in (
                ('old_materials','scoped-materials.json'),('old_packet','proposal.packet.json'),('old_raw','proposal.raw.json'))):
        raise ValueError('scope amendment must retain original exact output root')
    material = body(read(root/'scoped-materials.json'))
    packet = body(read(root/'proposal.packet.json'))
    from backend.pipeline.source_reconciliation_runner import build_proposal_packet
    if packet != build_proposal_packet(material):
        raise ValueError('original amendment proposal packet differs from its material')
    raw = validate_raw(root, 'proposal', packet)
    response = raw['response']
    if (response.get('records') or not any(d['action']=='unresolved' for d in response['dispositions'])
            or any(a['cause'] not in {'already_sufficient','evidence_binding','cvp_link'} for a in response['claim_assessments'])):
        raise ValueError('amendment is not an uncommitted qualification/link diagnosis')
    # A consumed review/correction cannot acquire another production attempt.
    if any((root/(s+'.'+suffix)).exists() for s in ('initial_review','correction','final_review','final_review_input_repair')
           for suffix in ('raw.json','reservation.json','raw.call-intent.json')):
        raise ValueError('scope amendment cannot reset review/correction/unknown budget')
    if material['sql_scope']['run_id'] != direction['run_id']:
        raise ValueError('scope amendment belongs to another SQL group')
    from backend.pipeline.claim_issue_management import read_policy
    policy = read_policy(direction['new_policy'], snapshots)
    if (not policy.evidence_eligibility_review or policy.claim_fields or policy.evidence_fields
            or policy.link_fields or policy.cvp_permissions):
        raise ValueError('amendment allows qualification only plus exact separate link permissions')
    permissions = [c.Permission.model_validate(p) for p in direction['link_permissions']]
    if len({p.key for p in permissions}) != len(permissions):
        raise ValueError('duplicate amendment link permissions')
    states = c.index(material['before_records'])
    if sorted(direction['evidence_ids'])!=sorted(set(direction['evidence_ids'])) or any(
            c.key('evidence_steps',eid) not in states for eid in direction['evidence_ids']):
        raise ValueError('amendment Evidence denominator missing/duplicate')
    for p in permissions:
        old = states.get(p.key)
        fields = set(p.fields)
        if (p.collection!='viewpoint_claim_links' or p.create or not old
                or old.get('review_status') in c.HUMAN_HOLDS|{'human_hold','rejected'}
                or old.get('effective_state','active')!='active'
                or (fields != {'effective_state'} and not fields <= {'component_locator','evidence_bindings'})
                or not fields or (fields=={'effective_state'} and (old['link_type']!='supports' or old.get('supporting_relation_ids')))):
            raise ValueError('amendment link scope would alter endpoints/core/hold or unrelated roles')
    if sorted(direction['context_claim_ids']) != sorted(set(direction['context_claim_ids'])):
        raise ValueError('duplicate context Claim IDs')
    return material, raw


def validate_live(store, direction):
    material, raw = validate_direction(direction)
    saved = store.get_cvp_group(direction['run_id'])
    previous = store.get_cvp_action(direction['run_id'],direction['old_action_id'])
    if (not saved or saved['campaign_sha256']!=direction['campaign_sha256']
            or not previous or previous['materials']!=material
            or store.cvp_action_committed(direction['run_id'],direction['old_action_id'])):
        raise ValueError('scope amendment cannot replace a foreign or committed action')
    from backend.pipeline.cvp_sql_flow import decoded_pins
    with store.connect() as conn, conn.cursor() as cursor:
        store._assert_transaction_read_pins(cursor,decoded_pins(material['sql_scope']['read_pins']))
    for issue in material['claim_issue_context']['issues']:
        from backend.pipeline.claim_issue_management import Issue
        row=store.get_claim_issue(Issue.model_validate(issue).issue_id)
        if not row or row['correction_used'] or row['terminal_refusal']:
            raise ValueError('original issue budget cannot acquire an amendment')
    return material, raw


def permissions(context, states, snapshots=None):
    direction=json.loads(c.bound_bytes(context['scope_amendment'],snapshots))
    old,_=validate_direction(direction,snapshots=snapshots)
    if context['issues']!=old['claim_issue_context']['issues'] or context['policy']!=direction['new_policy']:
        raise ValueError('amendment cannot rename original issues or replace its new policy')
    result=[c.Permission.model_validate(p) for p in direction['link_permissions']]
    before=c.index(old['before_records'])
    if any(states.get(p.key)!=before[p.key] for p in result):
        raise ValueError('amendment current link differs from original diagnosis')
    return result


def amend_manifest(store, direction, authorization, root):
    old,_=validate_live(store,direction)
    # The already verified original SQL closure is the authority, not a reason
    # to expand the whole reverse graph again for unchanged source/Claims.
    from backend.pipeline.claim_issue_management import read_policy,policy_permissions
    manifest=c.Manifest.model_validate(old['manifest'])
    states=c.index(old['before_records'])
    context=body(old['claim_issue_context'])
    context['policy']=direction['new_policy']
    context['scope_amendment']=authorization
    write(root/'issue-context.json',context)
    manifest.claim_issue=c.FileBinding.model_validate(binding(root/'issue-context.json'))
    policy=read_policy(direction['new_policy'])
    manifest.permissions=policy_permissions(policy,states,manifest.source_id,context['claim_ids'])
    manifest.permissions=[p for p in manifest.permissions if p.collection!='evidence_steps' or p.object_id in direction['evidence_ids']]
    manifest.permissions += [c.Permission.model_validate(p) for p in direction['link_permissions']]
    auth={'source_id':manifest.source_id,'authorization_id':policy.authorization_id,
          'authorization_quote':direction['authorization_quote'],'human_content_approval':False,
          'policy':direction['new_policy'],'claim_issue':manifest.claim_issue.model_dump(),
          'scope_amendment':authorization,'permissions':[p.model_dump() for p in manifest.permissions]}
    write(root/'scoped-authorization.json',auth)
    manifest.authorization=c.FileBinding.model_validate(binding(root/'scoped-authorization.json'))
    write(root/'manifest.json',manifest.model_dump(mode='json'))
    return manifest


def amendment_records(store, direction, old):
    """Fresh indexed original pins plus forward-only read-only context.

    Current payloads are observed, not taken from a historic snapshot. An
    unexpected direct consumer of a proposed write fails closed; no scan or
    expanding historical/reverse closure is used as a fallback.
    """
    from backend.pipeline.cvp_sql_flow import decoded_pins
    from backend.pipeline.viewpoint_validation_context import read_current_objects
    wanted=decoded_pins(old['sql_scope']['read_pins'])
    records=read_current_objects(store,wanted)
    if set(records)!=set(wanted):raise ValueError('amendment original indexed readset incomplete')
    with store.connect() as conn,conn.cursor() as cursor:
        store._assert_transaction_read_pins(cursor,wanted)
        write_ids=direction['evidence_ids']+[p['object_id'] for p in direction['link_permissions']]
        consumers=store._reference_consumers(cursor,write_ids,sorted(c.KNOWLEDGE_COLLECTIONS))
        if any((col,oid) not in records for col,oid,_ in consumers):
            raise ValueError('amendment has a new consumer outside original complete SQL proof')
    for key,payload in c.index(old['before_records']).items():
        row=records[c.split_key(key)]
        if row['retired'] or row['payload']!=payload:
            raise ValueError('amendment current original payload/retirement drift')
    extra=store.read_cvp_context(direction['context_claim_ids']) if direction['context_claim_ids'] else {}
    for key,row in extra.items():
        if key in records and records[key]!=row:
            # Different helper metadata shapes do not replace payload/pins.
            if any(records[key].get(f)!=row.get(f) for f in ('revision','content_sha256','payload','retired')):
                raise ValueError('amendment additional context generation conflict')
        else:records[key]=row
    return records


def execute(store, run_id, *, authorization, root, ports):
    direction=json.loads(c.bound_bytes(authorization))
    if direction['run_id']!=run_id:
        raise ValueError('scope amendment requires its original group')
    work=Path(direction['old_action_root']).parent.parent/'scope-amendment-once'
    if not work.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('scope amendment cannot move roots to acquire a budget')
    path=work/'manifest.json'
    manifest=c.Manifest.model_validate(body(read(path))) if path.exists() else amend_manifest(store,direction,authorization,work)
    context=json.loads(c.bound_bytes(manifest.claim_issue))
    if context.get('scope_amendment')!=authorization:
        raise ValueError('scope amendment authority cannot change on recovery')
    from backend.pipeline.cvp_sql_actions import execute_source_action
    return execute_source_action(store,run_id,manifest.model_dump(mode='json'),root=work/'action',ports=ports)
