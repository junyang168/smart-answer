"""Exact human NO-CHANGE wins its named current memberships, not other findings.

Keep both the actual Gemini disputed and saved GPT upheld responses unchanged.
Other findings require independent saved rejection or actual reviewed repair;
this human decision never becomes a blanket group pass.
"""
from pathlib import Path
from backend.api.canonical_repository.postgres_store import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable

VERSION='wang_exact_human_retained_and_saved_source_audit_resolution_v1'


def claim_audit_pins(receipt,required):
    """A source-action readset is not the complete audited Claim population.

    Consume only the receipt's exact saved full Claim audit for members absent
    from that narrower readset. Never infer a pin from the new audit/current SQL.
    """
    pins={(p['collection'],p['object_id']):tuple(p['pin']) for p in receipt['read_pins']}
    missing={('claims',cid) for cid in required}-set(pins)
    if not missing:return pins
    from backend.pipeline.cvp_sql_flow import check_binding
    from backend.pipeline.claim_first_management import validate_claim_audit_report
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
    check_binding(receipt['audit']);saved=read_artifact(receipt['audit']['path'])
    validate_claim_audit_report(saved,receipt['claim_ids'])
    if verify_saved_full_audit(Path(receipt['audit']['path']),layers=('3',))!=receipt['source_proofs']:
        raise ValueError('human retained saved Claim audit raw/source proof drift')
    audited={(p['collection'],p['object_id']):tuple(p['pin'])
             for p in saved['meta']['registry_read_scope']['read_pins']}
    judged={p['claim_id'] for p in saved['layers']['3']['results'] if p['verdict']=='supported'}
    for key in missing:
        if (key[1] not in receipt['claim_ids'] or key[1] not in judged
                or key not in audited or audited[key][2] is not False):
            raise ValueError('human retained Claim lacks exact saved audited generation')
        pins[key]=audited[key]
    return pins


def partition_packet(audit,packet,value,store):
    """Human-decided targets do not enter another model/rejection-reuse queue.

    This is only routing. Full audit closure still requires create_if_complete,
    which verifies every actual finding, including the excluded human targets.
    """
    from backend.pipeline.viewpoint_audit_source_adjudication import restricted_packet
    human,_=human_items(audit,packet,value,store)
    if not human:return packet,set()
    remaining={i['item_id'] for i in packet['items']}-human
    if not remaining:
        body={k:v for k,v in packet.items() if k!='artifact_sha256'}
        body['items']=[]
        return {**body,'artifact_sha256':sha256_json(body)},human
    return restricted_packet(packet,remaining),human


def human_items(audit,packet,value,store):
    from backend.pipeline.cvp_retained_impact_resolution import protected_link_ids
    from backend.pipeline.cvp_sql_flow import check_binding
    protected=protected_link_ids(value)
    if not protected:return set(),None
    authority=value['reviewed']['evidence']['approval']['retained_write_scope'];check_binding(authority)
    scope=read_artifact(authority['path']);direction=read_artifact(scope['human_disposition']['path'])
    check_binding(scope['retained_boundary'])
    boundary=read_artifact(scope['retained_boundary']['path'])
    original={(p['collection'],p['object_id']):(p['revision'],p['content_sha256'],False)
        for p in boundary['current_read_pins']}
    audited={(p['collection'],p['object_id']):tuple(p['pin']) for p in audit['meta']['registry_read_scope']['read_pins']}
    wanted={p['link_id']:p for p in direction['items']}
    accepted=set();pairs={}
    for lid in protected:
        state=store.get_record_state('viewpoint_claim_links',lid);p=state['payload'];d=wanted[lid]
        keys=[('viewpoint_claim_links',lid),('viewpoint_identity_decisions',p['decision_id']),
            ('canonical_viewpoints',p['viewpoint_id']),('viewpoint_revisions',p['validated_against_viewpoint_revision_id'])]
        if (state['retired'] or state['revision']!=d['storage_revision'] or state['content_sha256']!=d['content_sha256']
                or p['link_type']!='equivalent_component' or p['effective_state']!='active'
                or p['validated_against_viewpoint_revision_id']!=d['viewpoint_revision_id']
                or any(k not in original or audited.get(k)!=original[k] for k in keys)):
            raise ValueError('human retained audit membership/head/CVR/approval generation drift')
        pairs[p['viewpoint_id'],p['claim_id']]=lid
    for item in packet['items']:
        layer,vid,cid=item['item_id'].split(':',2)
        lid=pairs.get((vid,cid))
        if (layer=='4' and lid and item['audit_finding'].get('issue') in {'over_merge','different_proposition'}
                and item.get('connections') and {p['viewpoint_claim_link_id'] for p in item['connections']}=={lid}):
            for source in item['sources']:check_binding({'path':source['path'],'file_sha256':source['file_sha256']})
            accepted.add(item['item_id'])
    # A view-level dispute is accepted only when EVERY questioned Claim is one
    # of the exact human memberships. An unknown additional question is not covered.
    for row in audit['layers']['4']['results']:
        if row['verdict']!='disputed':continue
        ids={'4:'+row['viewpoint_id']+':'+cid for cid in row.get('claim_ids_in_question',[])}
        if ids & accepted and not ids<=accepted:accepted-=ids
    return accepted,authority


def create_if_complete(audit_path,store,run_id,output):
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet,restricted_packet
    from backend.pipeline.viewpoint_adjudication_history import target_identity,validated_history
    from backend.pipeline.viewpoint_reused_source_resolution import exact_item,create as reuse,verify
    from backend.pipeline.viewpoint_audit_owner_resolution import audit_semantic_sha,binding
    audit=read_artifact(audit_path);value=store.get_cvp_group(run_id)
    packet=build_packet(audit_path,store,True);human,authority=human_items(audit,packet,value,store)
    if not human:return None
    remaining={i['item_id']:i for i in packet['items'] if i['item_id'] not in human}
    parts=[];roots=store.adjudication_roots([target_identity(i) for i in remaining.values()]) if remaining else []
    matches={}
    for root in roots:
        old,findings=validated_history(root)
        for old_item in old['items']:
            current=remaining.get(old_item['item_id'])
            if current and findings[old_item['item_id']]['verdict']=='audit_objection_rejected' and exact_item(old_item,current):
                matches.setdefault(str(root),set()).add(old_item['item_id'])
    if set().union(*matches.values())!=set(remaining):return None
    covered=set()
    for old_root,ids in sorted(matches.items()):
        ids-=covered
        if not ids:continue
        path=Path(output).parent/('human-retain-source-'+sha256_json(old_root)[:20])/'resolution.json'
        reuse(audit_path,Path(old_root),path,store,only_item_ids=ids)
        proof=read_artifact(path);verify(audit,proof,expected_item_ids=ids)
        parts.append({'binding':binding(path),'item_ids':sorted(ids)});covered|=ids
    body={'schema_version':VERSION,'run_id':run_id,'audit':binding(audit_path),
        'audit_semantic_sha256':audit_semantic_sha(audit),'human_scope_authority':authority,
        'claim_layer_completion':value['claim_layer_audit']['receipt'],
        'human_item_ids':sorted(human),'source_rejected_parts':parts,'master_data_mutations':0,
        'independent_audit_verdicts_unchanged':True,'original_gpt_verdicts_unchanged':True,
        'new_model_calls':0,'approval_basis':'exact_human_no_change_plus_saved_source_final'}
    body['artifact_sha256']=sha256_json(body);write_immutable(output,body)
    verify_resolution(audit,body)
    return Path(output)


def verify_resolution(audit,body):
    from backend.pipeline.cvp_sql_flow import check_binding
    from backend.pipeline.viewpoint_audit_owner_resolution import audit_semantic_sha
    from backend.pipeline.viewpoint_reused_source_resolution import verify
    if (body.get('schema_version')!=VERSION or body.get('artifact_sha256')!=sha256_json({k:v for k,v in body.items() if k!='artifact_sha256'})
            or body['audit_semantic_sha256']!=audit_semantic_sha(audit)
            or body.get('master_data_mutations')!=0 or body.get('new_model_calls')!=0
            or body.get('independent_audit_verdicts_unchanged') is not True
            or body.get('original_gpt_verdicts_unchanged') is not True):
        raise ValueError('human retained audit resolution identity/decision drift')
    check_binding(body['audit']);check_binding(body['human_scope_authority'])
    if read_artifact(body['audit']['path'])!=audit:raise ValueError('human resolution actual audit body drift')
    check_binding(body['claim_layer_completion'])
    claim_receipt=read_artifact(body['claim_layer_completion']['path'])
    scope=read_artifact(body['human_scope_authority']['path']);check_binding(scope['human_disposition'])
    check_binding(scope['retained_boundary']);boundary=read_artifact(scope['retained_boundary']['path'])
    direction=read_artifact(scope['human_disposition']['path'])
    if (direction['group_id']!=body['run_id'] or direction.get('human_exact_disposition') is not True
            or direction.get('decision')!='retain_existing_contextual_equivalence_unchanged'):
        raise ValueError('human disposition cannot substitute another group/content decision')
    links={lid:pin for lid,pin in [(p['object_id'],p) for p in scope['retained_link_pins']] if lid in scope['human_retained_link_ids']}
    basis={p['link_id']:p for p in boundary['prior_current_approvals']}
    pairs={('4:'+p['original_decision']['resolved_viewpoint_id']+':'+p['claim_id']) for lid,p in basis.items() if lid in links}
    human=set(body['human_item_ids'])
    if not human or not human<=pairs:raise ValueError('human resolution includes unnamed/foreign membership')
    audited={(p['collection'],p['object_id']):tuple(p['pin']) for p in audit['meta']['registry_read_scope']['read_pins']}
    original={(p['collection'],p['object_id']):(p['revision'],p['content_sha256'],False) for p in boundary['current_read_pins']}
    if claim_receipt.get('status')!='claim_layer_audited_cvp_pending':raise ValueError('human retained original Claim receipt foreign')
    required={p['claim_id'] for lid,p in basis.items() if lid in links
              and '4:'+p['original_decision']['resolved_viewpoint_id']+':'+p['claim_id'] in human}
    claim_pins=claim_audit_pins(claim_receipt,required)
    for lid,p in basis.items():
        if lid not in links:continue
        pair='4:'+p['original_decision']['resolved_viewpoint_id']+':'+p['claim_id']
        if pair not in human:continue
        vid=p['original_decision']['resolved_viewpoint_id'];decision=p['original_decision']['identity_decision_id']
        direction_item=next(i for i in direction['items'] if i['link_id']==lid)
        keys=[('viewpoint_claim_links',lid),('viewpoint_identity_decisions',decision),
              ('canonical_viewpoints',vid),('viewpoint_revisions',direction_item['viewpoint_revision_id'])]
        if (any(k not in original or audited.get(k)!=original[k] for k in keys)
                or audited.get(('claims',p['claim_id']))!=claim_pins.get(('claims',p['claim_id']))):
            raise ValueError('human no-change resolution current Claim/link/head/CVR approval drift')
    resolved={'3':set(),'4':set()};all_ids=set(human)
    for part in body['source_rejected_parts']:
        check_binding(part['binding']);ids=set(part['item_ids'])
        if ids&all_ids:raise ValueError('human/source resolution overlaps or repeats')
        result=verify(audit,read_artifact(part['binding']['path']),expected_item_ids=ids)
        for layer in resolved:resolved[layer]|=result[layer]
        all_ids|=ids
    expected=set()
    for layer,key in (('3','claim_id'),('4','viewpoint_id')):
        for row in audit['layers'][layer]['results']:
            if row['verdict']!='disputed':continue
            ids={layer+':'+row[key]+':'+cid for cid in ([row[key]] if layer=='3' else row.get('claim_ids_in_question',[]))}
            if not ids:raise ValueError('disputed Claim population missing')
            if ids<=human:
                if layer!='4' or row.get('issue') not in {'over_merge','different_proposition'}:
                    raise ValueError('human membership does not cover another issue kind')
                resolved[layer].add(row[key])
            expected|=ids
    if all_ids!=expected:raise ValueError('human/source resolution omitted additional findings')
    return resolved
