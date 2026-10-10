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
    scope=read_artifact(authority['path']);check_binding(scope['human_disposition'])
    direction=read_artifact(scope['human_disposition']['path'])
    from backend.pipeline.cvp_retained_review_separation import merge_human_directions
    if direction.get('source_decision_bindings') and merge_human_directions(direction['source_decision_bindings'])!=direction:
        raise ValueError('human retained audit decision bundle drift')
    check_binding(scope['retained_boundary'])
    boundary=read_artifact(scope['retained_boundary']['path'])
    original={(p['collection'],p['object_id']):(p['revision'],p['content_sha256'],False)
        for p in boundary['current_read_pins']}
    audited={(p['collection'],p['object_id']):tuple(p['pin']) for p in audit['meta']['registry_read_scope']['read_pins']}
    wanted={p['link_id']:p for p in direction['items']}
    accepted=set();pairs={}
    for lid in protected:
        state=store.get_record_state('viewpoint_claim_links',lid)
        if not state:raise ValueError('human retained current link missing')
        p=state['payload'];d=wanted[lid]
        keys=[('viewpoint_claim_links',lid),('viewpoint_identity_decisions',p['decision_id']),
            ('canonical_viewpoints',p['viewpoint_id']),('viewpoint_revisions',p['validated_against_viewpoint_revision_id'])]
        if (state['retired'] or state['revision']!=d['storage_revision'] or state['content_sha256']!=d['content_sha256']
                or p['link_type']!='equivalent_component' or p['effective_state']!='active'
                or p['validated_against_viewpoint_revision_id']!=d['viewpoint_revision_id']
                or any(k not in original or audited.get(k)!=original[k] for k in keys)):
            raise ValueError('human retained audit membership/head/CVR/approval generation drift')
        claim_pins={(q['collection'],q['object_id']):tuple(q['pin'])
                    for q in scope.get('human_retained_claim_pins',[])}
        if claim_pins and audited.get(('claims',p['claim_id']))!=claim_pins.get(('claims',p['claim_id'])):
            raise ValueError('human retained audit Claim generation drift')
        pairs[p['viewpoint_id'],p['claim_id']]=lid
    for item in packet['items']:
        layer,vid,cid=item['item_id'].split(':',2)
        lid=pairs.get((vid,cid))
        if (layer=='4' and lid and item['audit_finding'].get('issue') in {'over_merge','different_proposition'}
                and item.get('connections') and {p['viewpoint_claim_link_id'] for p in item['connections']}=={lid}):
            for source in item['sources']:check_binding({'path':source['path'],'file_sha256':source['file_sha256']})
            accepted.add(item['item_id'])
    # Route only named items. Completion below rejoins ALL other questions;
    # an additional Claim must not send the already human-decided item back to a model.
    return accepted,authority


def create_if_complete(audit_path,store,run_id,output,*,execution_root=None):
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet,restricted_packet
    from backend.pipeline.viewpoint_adjudication_history import target_identity,validated_history
    from backend.pipeline.viewpoint_reused_source_resolution import exact_item,create as reuse,verify
    from backend.pipeline.viewpoint_audit_owner_resolution import audit_semantic_sha,binding
    audit=read_artifact(audit_path);value=store.get_cvp_group(run_id)
    packet=build_packet(audit_path,store,True);human,authority=human_items(audit,packet,value,store)
    if not human:return None
    exact=set();exact_used=[]
    if execution_root is not None:
        from backend.pipeline.cvp_exact_human_audit_retention import human_items as exact_items
        exact,exact_used=exact_items(audit,packet,execution_root,store)
        if exact&human:raise ValueError('conflicting human authority domains for the same audit item')
    remaining={i['item_id']:i for i in packet['items'] if i['item_id'] not in human|exact}
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
        'human_item_ids':sorted(human),'source_rejected_parts':parts,'master_data_mutations':0,
        'independent_audit_verdicts_unchanged':True,'original_gpt_verdicts_unchanged':True,
        'new_model_calls':0,'approval_basis':'exact_human_no_change_plus_saved_source_final'}
    if value.get('claim_layer_audit'):
        body['claim_layer_completion']=value['claim_layer_audit']['receipt']
    if exact_used:
        body['exact_audit_human_items']=exact_used
        body['exact_audit_packet']=packet
    body['artifact_sha256']=sha256_json(body)
    verify_resolution(audit,body)
    write_immutable(output,body)
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
    claim_receipt=None
    if body.get('claim_layer_completion'):
        check_binding(body['claim_layer_completion'])
        claim_receipt=read_artifact(body['claim_layer_completion']['path'])
    scope=read_artifact(body['human_scope_authority']['path']);check_binding(scope['human_disposition'])
    check_binding(scope['retained_boundary']);boundary=read_artifact(scope['retained_boundary']['path'])
    direction=read_artifact(scope['human_disposition']['path'])
    from backend.pipeline.cvp_retained_review_separation import merge_human_directions
    if direction.get('source_decision_bindings') and merge_human_directions(direction['source_decision_bindings'])!=direction:
        raise ValueError('human retained resolution decision bundle drift')
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
    required={p['claim_id'] for lid,p in basis.items() if lid in links
              and '4:'+p['original_decision']['resolved_viewpoint_id']+':'+p['claim_id'] in human}
    if claim_receipt is not None:
        if claim_receipt.get('status')!='claim_layer_audited_cvp_pending':raise ValueError('human retained original Claim receipt foreign')
        claim_pins=claim_audit_pins(claim_receipt,required)
    else:
        rows=scope.get('human_retained_claim_pins',[])
        claim_pins={(p['collection'],p['object_id']):tuple(p['pin']) for p in rows}
        if (len(rows)!=len(claim_pins) or set(claim_pins)!={('claims',cid) for cid in
                {p['claim_id'] for lid,p in basis.items() if lid in links}}
                or any(len(pin)!=3 or pin[2] is not False for pin in claim_pins.values())):
            raise ValueError('normal human retention lacks exact original reviewed Claim pins')
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
    if body.get('exact_audit_human_items'):
        from backend.pipeline.cvp_exact_human_audit_retention import verify_human_entry
        packet=body['exact_audit_packet']
        if packet.get('artifact_sha256')!=sha256_json({k:v for k,v in packet.items() if k!='artifact_sha256'}):
            raise ValueError('mixed human resolution packet seal drift')
        items={i['item_id']:i for i in packet['items']}
        for h in body['exact_audit_human_items']:
            ident=verify_human_entry(audit,items,h)
            if ident in all_ids:raise ValueError('mixed human resolution overlap')
            all_ids.add(ident)
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
            if ids & human and (layer!='4' or row.get('issue') not in {'over_merge','different_proposition'}):
                raise ValueError('human membership does not cover another issue kind')
            if ids<=all_ids:
                resolved[layer].add(row[key])
            expected|=ids
    if all_ids!=expected:raise ValueError('human/source resolution omitted additional findings')
    return resolved
