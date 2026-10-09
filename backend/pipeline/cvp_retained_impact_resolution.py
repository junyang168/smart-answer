"""No-change management closes only after exact full current independent audit.

Claim-first conservatively deferred every existing CVP disposition. That list
is not a list of mandatory mutations. Unchanged approved current facts can be
retained through actual review + full audit, without rewriting their content,
inventing new members, or clearing the original unresolved model responses.
"""
from pathlib import Path
from backend.api.canonical_repository.postgres_store import sha256_json,record_content_sha
from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable

VERSION='wang_cvp_retained_impact_resolution_v1'


def protected_link_ids(value):
    from backend.pipeline.cvp_sql_flow import check_binding
    scope=value.get('reviewed',{}).get('evidence',{}).get('approval',{}).get('retained_write_scope')
    if not scope:return set()
    check_binding(scope);body=read_artifact(scope['path']);check_binding(body['human_disposition'])
    direction=read_artifact(body['human_disposition']['path'])
    if (direction.get('human_exact_disposition') is not True
            or direction.get('decision')!='retain_existing_contextual_equivalence_unchanged'
            or direction.get('group_id')!=value['run_id']):
        raise ValueError('exact human retain disposition identity drift')
    ids={r['link_id'] for r in direction['items']}
    if ids!=set(body['human_retained_link_ids']):raise ValueError('human retain scope denominator drift')
    return ids


def guard_operations(value,operations):
    protected=protected_link_ids(value)
    if any(o.collection=='viewpoint_claim_links' and o.object_id in protected for o in operations):
        raise ValueError('explicit human NO-CHANGE decision forbids automatic retained link mutation')


def managed_receipt(value,managed):
    e=value['reviewed']['evidence'];a=e['approval']
    result={'change_set_id':value['reviewed']['plan']['change_set_id'],
            'material':a['pending_link_material'],'review':a['pending_link_review'],
            'record_keys':sorted(managed)}
    if a.get('prior_cvp_management'):
        result.update(prior_management=a['prior_cvp_management'],owner_statement_review=a['terminal_review'],
                      owner_claim_completion=a['owner_claim_completion'])
    return result


def qualification_only(operations,terminal_sha):
    if not operations or any(op['collection']!='evidence_steps' or op['operation']!='update' for op in operations):
        raise ValueError('retained impact closure requires qualification-only source transaction')
    for op in operations:
        ignored={'revision','review_status','support_eligibility','source_reconciliation_review_sha256',
                 'review_note','reviewed_at','reviewed_by'}
        before,after=op['before'],op['after']
        if (not before or not after
                or record_content_sha(before)!=op['before_sha256']
                or record_content_sha(after)!=op['after_sha256']
                or {k:v for k,v in before.items() if k not in ignored}
                   != {k:v for k,v in after.items() if k not in ignored}
                or after.get('support_eligibility')!='eligible_with_label'
                or before.get('support_eligibility')!='context_only'
                or before.get('review_status') not in {'candidate','approved','independent_terminal_reviewed'}
                or after.get('review_status')!='independent_terminal_reviewed'
                or after.get('source_reconciliation_review_sha256')!=terminal_sha):
            raise ValueError('source semantic/binding/approval change requires actual CVP management')


def claim_authority(value):
    from backend.pipeline.cvp_sql_flow import check_binding
    from backend.pipeline.claim_first_management import REPROPOSAL_VERSION,VERSION as CLAIM_VERSION
    evidence=value['reviewed']['evidence'];layer=value.get('claim_layer_audit')
    context=evidence['action']['materials'].get('claim_management_resolution',{})
    if (not layer or context.get('schema_version')!=REPROPOSAL_VERSION
            or layer.get('receipt')!=context.get('claim_layer_completion')
            or sorted(context['pending_existing_cvp_record_keys'])!=sorted(evidence['pending_cvp_management_record_keys'])):
        raise ValueError('retained impacts lack the original SQL-prepared Claim-first handoff')
    for key in ('claim_layer_completion','source_terminal_review','source_terminal_packet'):check_binding(context[key])
    receipt=read_artifact(context['claim_layer_completion']['path'])
    if (receipt.get('schema_version')!=CLAIM_VERSION
            or receipt.get('status')!='claim_layer_audited_cvp_pending'
            or receipt.get('claim_ids')!=value['claim_ids']
            or not receipt.get('change_set_ids')
            or len(set(receipt['change_set_ids']))!=len(receipt['change_set_ids'])
            or not set(receipt['change_set_ids'])<=set(value['change_set_ids'])
            or any(receipt.get(k) is not False for k in ('cvp_management_complete','group_complete','next_group_started'))):
        raise ValueError('original source completion receipt identity/scope drift')
    check_binding(receipt['audit'])
    for proof in receipt['source_proofs']:check_binding(proof)
    return context,receipt


def build(store,run_id,audit_path,output,*,semantic_resolution=None):
    from backend.pipeline.cvp_sql_flow import binding,check_binding,decoded_pins
    from backend.pipeline.viewpoint_cvp_campaign_runner import validate_audit
    saved=store.get_cvp_group(run_id);evidence=saved['reviewed']['evidence']
    pending=evidence.get('pending_cvp_management_record_keys',[])
    if not pending:return None
    approval=evidence['approval'];scope=approval.get('retained_write_scope')
    if not scope:raise ValueError('pending impacts lack independent retained/new-write authority')
    for proof in approval.values():check_binding(proof)
    scope_body=read_artifact(scope['path'])
    if (scope_body.get('model_verdicts_changed') is not False
            or scope_body.get('all_pending_impacts_preserved') is not True):
        raise ValueError('retained scope cannot fake model decisions or discard impacts')
    materials=evidence['action']['materials']
    context,claim_receipt=claim_authority(saved)
    # Narrow no-change closure: source transaction changed Evidence qualification
    # only. A rewritten Claim/source/binding cannot use this branch.
    histories=[store.get_change_history(cid) for cid in claim_receipt['change_set_ids']]
    if any(not h or h['status']!='applied' for h in histories):
        raise ValueError('retained impact source transaction is not committed')
    source_ops=[op for h in histories for op in h['operations']]
    qualification_only(source_ops,read_artifact(context['source_terminal_review']['path'])['artifact_sha256'])
    audit=read_artifact(audit_path)
    # The ordinary complete group audit, including EVERY retained CVP and its
    # complete current members, remains the content gate. No audit is erased.
    validate_audit(audit,saved['claim_ids'],evidence['viewpoint_ids'],owner_resolution_path=semantic_resolution)
    audit_pins={(p['collection'],p['object_id']):tuple(p['pin']) for p in
                audit['meta']['registry_read_scope']['read_pins']}
    original_pins=decoded_pins(claim_receipt['read_pins'])
    from backend.pipeline.cvp_pending_link_management import resolved_pending_operations
    managed=resolved_pending_operations(saved,store=store)
    from backend.pipeline.cvp_link_target_selection import _approved
    rows=[]
    for key in sorted(pending):
        col,oid=key.split('/',1);state=store.get_record_state(col,oid)
        pin=original_pins.get((col,oid))
        if key in managed:
            op=managed[key]
            if pin!=(op['before_revision'],op['before_sha256'],False):
                raise ValueError('pending managed link did not start at original deferred generation')
            pin=(op['after_revision'],op['after_sha256'],False)
        if (not state or state['retired'] or pin!=(state['revision'],state['content_sha256'],False)
                or audit_pins.get((col,oid))!=pin or record_content_sha(state['payload'])!=state['content_sha256']):
            raise ValueError('pending current fact changed, missing audit or stale dependency: '+key)
        _approved(state['payload'])
        rows.append({'collection':col,'object_id':oid,'pin':list(pin)})
    body={'schema_version':VERSION,'run_id':run_id,'reviewed_sha256':saved['reviewed_sha256'],
        'pending_record_keys':sorted(pending),'disposition':'retain_exact_current_after_complete_independent_audit',
        'read_pins':rows,'audit':binding(audit_path),'scope_authority':scope,
        'claim_layer_completion':context['claim_layer_completion'],
        'human_disposition':scope_body['human_disposition'],'registry_mutations':0,
        'model_verdicts_changed':False,'new_model_calls':0,
        'semantic_resolution':binding(semantic_resolution) if semantic_resolution else None}
    if managed:
        body['reviewed_link_management']=managed_receipt(saved,managed)
    body['artifact_sha256']=sha256_json(body);write_immutable(output,body)
    return Path(output)


def validate_for_completion(value,path,audit_binding,cursor,*,semantic_resolution=None):
    """Short SQL guard. Files were checked before transaction; no model calls."""
    from backend.pipeline.cvp_sql_flow import check_binding,decoded_pins
    body=read_artifact(path);evidence=value['reviewed']['evidence']
    if (body.get('schema_version')!=VERSION
            or body.get('artifact_sha256')!=sha256_json({k:v for k,v in body.items() if k!='artifact_sha256'})
            or body.get('run_id')!=value['run_id']
            or body.get('reviewed_sha256')!=value['reviewed_sha256']
            or body.get('pending_record_keys')!=sorted(evidence['pending_cvp_management_record_keys'])
            or body.get('audit')!=audit_binding
            or body.get('scope_authority')!=evidence['approval'].get('retained_write_scope')
            or body.get('disposition')!='retain_exact_current_after_complete_independent_audit'
            or body.get('registry_mutations')!=0 or body.get('new_model_calls')!=0
            or body.get('model_verdicts_changed') is not False):
        raise ValueError('retained management completion proof incomplete/foreign')
    for key in ('audit','scope_authority','claim_layer_completion','human_disposition'):
        check_binding(body[key])
    from backend.pipeline.cvp_sql_flow import binding
    if body.get('semantic_resolution')!=(binding(semantic_resolution) if semantic_resolution else None):
        raise ValueError('retained impact independent semantic resolution drift')
    if body.get('semantic_resolution'):check_binding(body['semantic_resolution'])
    context,receipt=claim_authority(value)
    scope=read_artifact(body['scope_authority']['path'])
    if (body['claim_layer_completion']!=context['claim_layer_completion']
            or body['human_disposition']!=scope.get('human_disposition')
            or scope.get('model_verdicts_changed') is not False
            or scope.get('all_pending_impacts_preserved') is not True):
        raise ValueError('retained management substituted source/scope/human authority')
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    pins=decoded_pins(body['read_pins'])
    original=decoded_pins(receipt['read_pins'])
    from backend.pipeline.cvp_pending_link_management import resolved_pending_operations
    managed=resolved_pending_operations(value,cursor=cursor)
    if managed:
        expected=managed_receipt(value,managed)
        if body.get('reviewed_link_management')!=expected:
            raise ValueError('pending link completion omitted/changed reviewed management proof')
        for key,op in managed.items():
            k=tuple(key.split('/',1))
            if original.get(k)!=(op['before_revision'],op['before_sha256'],False):
                raise ValueError('pending managed original version mismatch')
            original[k]=(op['after_revision'],op['after_sha256'],False)
    elif body.get('reviewed_link_management'):
        raise ValueError('foreign pending management resolution')
    if (len(pins)!=len(body['read_pins']) or {c+'/'+i for c,i in pins}!=set(body['pending_record_keys'])
            or any(not p or p[2] or original.get(k)!=p for k,p in pins.items())):
        raise ValueError('retained management omitted pending record pins')
    # Recheck the exact tiny committed source write set in the SAME completion
    # transaction; a forged standalone resolution cannot bypass this authority.
    for cid in receipt['change_set_ids']:
        cursor.execute('SELECT status FROM wang_knowledge.change_sets WHERE change_set_id=%s',(cid,))
        if cursor.fetchone()!=('applied',):raise ValueError('source transaction not committed')
        cursor.execute('''SELECT o.operation,o.collection,o.before_sha256,o.after_sha256,b.payload,a.payload
            FROM wang_knowledge.change_operations o
            LEFT JOIN wang_knowledge.object_versions b ON
                (b.collection,b.object_id,b.revision)=(o.collection,o.object_id,o.before_revision)
            LEFT JOIN wang_knowledge.object_versions a ON
                (a.collection,a.object_id,a.revision)=(o.collection,o.object_id,o.after_revision)
            WHERE o.change_set_id=%s ORDER BY o.operation_index''',(cid,))
        fields=('operation','collection','before_sha256','after_sha256','before','after')
        qualification_only([dict(zip(fields,r)) for r in cursor.fetchall()],
                          read_artifact(context['source_terminal_review']['path'])['artifact_sha256'])
    actual=PostgresKnowledgeStore._assert_transaction_read_pins(cursor,pins)
    from backend.pipeline.cvp_link_target_selection import _approved
    for record in actual.values():_approved(record['payload'])
    return body
