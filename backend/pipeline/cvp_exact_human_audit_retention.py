"""Human rejection of a named existing-link objection, without a content write.

Partial routing is not completion. Rejoin every actual audit item with exact
saved source rejections before allowing the common SQL completion transaction.
"""
from pathlib import Path
from backend.api.canonical_repository.postgres_store import sha256_json, record_content_sha
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable

DIRECTION = 'wang_exact_human_audit_link_retention_v1'
RESOLUTION = 'wang_exact_human_audit_link_retention_resolution_v1'
NAME = 'audit-link-retention-bindings.json'
ADDITIONS = 'audit-link-retention-additions'
APPROVED = {'approved', 'system_approved', 'human_approved', 'ai_consensus_reviewed'}


def target(item):
    return {k:v for k,v in item.items() if k != 'audit_finding'}


def required_keys(items):
    result=set()
    for i in items:
        result.add(('claims',i['claim']['claim_id']))
        for l in i['connections']:
            result|={('viewpoint_claim_links',l['viewpoint_claim_link_id']),
                ('viewpoint_identity_decisions',l['decision_id']),('canonical_viewpoints',l['viewpoint_id']),
                ('viewpoint_revisions',l['validated_against_viewpoint_revision_id'])}
        for e in i['evidence']:
            result|={('evidence_steps',e['step']['evidence_step_id']),('source_fragments',e['fragment']['fragment_id'])}
        result|={('source_documents',s['source_id']) for s in i['sources']}
    return result


def validate_direction(d):
    if (d.get('schema_version') != DIRECTION or d.get('artifact_sha256') != sha256_json(
            {k:v for k,v in d.items() if k != 'artifact_sha256'})
            or d.get('authorized_by') != 'owner' or not d.get('owner_direction')
            or d.get('decision') != 'reject_objection_retain_exact_current_link'
            or d.get('master_data_mutations') != 0 or d.get('additional_model_calls') != 0
            or not d.get('items') or len({i['item_id'] for i in d['items']}) != len(d['items'])):
        raise ValueError('exact human audit retention direction invalid')
    for i in d['items']:
        links=i.get('connections',[])
        selected=i.get('retained_link_ids')
        ids=[l['viewpoint_claim_link_id'] for l in links]
        if (not i['item_id'].startswith('4:') or not links or len(ids)!=len(set(ids))
                or any(l['link_type'] not in {'equivalent_full','equivalent_component','supports','qualifies'} for l in links)
                or (selected is None and len(links)!=1)
                or (selected is not None and (len(selected)!=1 or selected[0] not in ids
                    or d.get('exact_finding_only') is not True))):
            raise ValueError('human retention requires one exact existing link per item')
    if (len(d.get('read_pins',[])) != len(required_keys(d['items']))
            or {(p['collection'],p['object_id']) for p in d.get('read_pins',[])} != required_keys(d['items'])
            or any(len(p['pin'])!=3 or p['pin'][2] is not False for p in d['read_pins'])):
        raise ValueError('human retention exact live dependency population missing')


def build_direction(audit_path, store, owner_direction, selected_link_ids, run_id):
    """Capture a human-selected no-change scope from actual audited SQL data."""
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet
    from backend.pipeline.viewpoint_audit_owner_resolution import binding
    selected=set(selected_link_ids)
    if not selected or len(selected)!=len(selected_link_ids):
        raise ValueError('human retention selected link population invalid')
    audit=read_artifact(audit_path);packet=build_packet(audit_path,store,True)
    items=[];found=set()
    for i in packet['items']:
        ids=selected&{l['viewpoint_claim_link_id'] for l in i['connections']}
        if ids:
            if len(ids)!=1 or ids&found:
                raise ValueError('human retention selected link ambiguous')
            items.append({**i,'retained_link_ids':sorted(ids)});found|=ids
    if found!=selected:raise ValueError('human retention selected link missing from actual audit')
    keys=required_keys(items)
    pins=[p for p in audit['meta']['registry_read_scope']['read_pins'] if (p['collection'],p['object_id']) in keys]
    body={'schema_version':DIRECTION,'campaign_sha256':store.get_cvp_group(run_id)['campaign_sha256'],
        'run_id':run_id,'authorized_by':'owner','owner_direction':owner_direction,
        'decision':'reject_objection_retain_exact_current_link','master_data_mutations':0,
        'additional_model_calls':0,'exact_finding_only':True,'audit':binding(audit_path),
        'items':items,'read_pins':pins}
    body['artifact_sha256']=sha256_json(body);validate_direction(body)
    return body


def verify_item(item, d, audit):
    validate_direction(d)
    old=next((i for i in d['items'] if i['item_id'] == item['item_id']),None)
    # Explicit selected links are authority metadata, not model packet fields.
    original_target={k:v for k,v in target(old or {}).items() if k!='retained_link_ids'}
    if old is None or original_target != target(item):
        raise ValueError('human retention current target drift')
    if d.get('exact_finding_only') and old['audit_finding']!=item['audit_finding']:
        raise ValueError('human retention outside exact authorized finding')
    from backend.pipeline.cvp_sql_flow import check_binding
    # A human-approved new postimage is an existing current link after commit.
    # Derive retention from that same explicit decision, not a fresh model pass.
    if d.get('accepted_postimage_authority'):
        check_binding(d['accepted_postimage_authority'])
        authority=read_artifact(d['accepted_postimage_authority']['path'])
        if (authority.get('schema_version')!='wang_cvp_link_representation_human_postimage_v1'
                or authority.get('artifact_sha256')!=sha256_json({k:v for k,v in authority.items() if k!='artifact_sha256'})
                or authority.get('authorized_by')!='owner' or authority.get('human_content_approval') is not True
                or authority.get('run_id')!=d['run_id'] or authority.get('campaign_sha256')!=d['campaign_sha256']
                or not d.get('applied_change_set_id')):
            raise ValueError('human audit postimage authority drift')
        accepted=authority['accepted_after_link']
        selected=[l for l in item['connections'] if l['viewpoint_claim_link_id']==accepted['viewpoint_claim_link_id']]
        ignored={'decision_id','review_status'}
        if len(selected)!=1 or {k:v for k,v in selected[0].items() if k not in ignored}!={k:v for k,v in accepted.items() if k not in ignored}:
            raise ValueError('human audit accepted postimage changed')
    check_binding(d['audit'])
    original=read_artifact(d['audit']['path'])
    from backend.pipeline.viewpoint_g035_temporary_audit import validate
    validate(original)
    row=next((r for r in original['layers']['4']['results']
              if r['viewpoint_id'] == item['viewpoint']['viewpoint_id']),None)
    if row != old['audit_finding'] or old['claim']['claim_id'] not in row['claim_ids_in_question']:
        raise ValueError('human retention original objection drift')
    pins={(p['collection'],p['object_id']):tuple(p['pin']) for p in audit['meta']['registry_read_scope']['read_pins']}
    oldpins={(p['collection'],p['object_id']):tuple(p['pin']) for p in original['meta']['registry_read_scope']['read_pins']}
    for p in d['read_pins']:
        if (pins.get((p['collection'],p['object_id'])) != tuple(p['pin'])
                or oldpins.get((p['collection'],p['object_id'])) != tuple(p['pin'])):
            raise ValueError('human retention audited generation drift')
    current_row=next((r for r in audit['layers']['4']['results'] if r['viewpoint_id']==item['viewpoint']['viewpoint_id']),None)
    if (current_row != item['audit_finding'] or item['claim']['claim_id'] not in current_row.get('claim_ids_in_question',[])
            or current_row.get('issue') not in {'different_proposition','over_merge','scope_mismatch','other'}):
        raise ValueError('human retention actual current role finding drift')
    for s in item['sources']:
        check_binding({'path':s['path'],'file_sha256':s['file_sha256']})
    return item['item_id']


def direction_bindings(root):
    path=Path(root)/NAME
    base=read_artifact(path)['directions'] if path.exists() else []
    additions=Path(root)/ADDITIONS
    extra=[read_artifact(p)['direction'] for p in sorted(additions.glob('*.json'))] if additions.exists() else []
    result=base+extra
    if len({b['path'] for b in result})!=len(result):
        raise ValueError('duplicate human retention binding')
    return result


def check_explicit_bindings(root, explicit):
    from backend.pipeline.cvp_sql_flow import check_binding
    if len({b['path'] for b in explicit})!=len(explicit):
        raise ValueError('duplicate explicit human retention direction')
    for b in direction_bindings(root):
        check_binding(b)
        if b not in explicit:
            raise ValueError('audit link retention requires every original exact recovery direction')


def persist_bindings(root, explicit):
    """Append owner directions without overwriting the original binding or raw."""
    check_explicit_bindings(root,explicit)
    path=Path(root)/NAME
    if not path.exists():
        write_immutable(path,{'directions':explicit})
        return
    old=direction_bindings(root)
    for b in explicit:
        if b not in old:
            d=read_artifact(b['path']);validate_direction(d)
            write_immutable(Path(root)/ADDITIONS/(sha256_json(b)+'.json'),{'direction':b})


def human_items(audit, packet, root, store):
    from backend.pipeline.cvp_sql_flow import check_binding
    accepted=set();used=[]
    for b in direction_bindings(root):
        check_binding(b);d=read_artifact(b['path']);validate_direction(d)
        for item in packet['items']:
            if item['item_id'] not in {i['item_id'] for i in d['items']}:continue
            if d['campaign_sha256'] != store.get_cvp_group(d['run_id'])['campaign_sha256']:
                raise ValueError('human retention foreign campaign')
            verify_item(item,d,audit)
            legacy_claim_context=[]
            with store.connect() as c:
                for p in d['read_pins']:
                    row=c.execute('SELECT revision,content_sha256,payload,retired_at FROM wang_knowledge.objects WHERE collection=%s AND object_id=%s',
                        (p['collection'],p['object_id'])).fetchone()
                    closed_context=False
                    if row and p['collection']=='claims' and row[2].get('review_status')=='human_review_required':
                        from backend.pipeline.cvp_retained_relation_only_claim import authority
                        proof=authority(store,item,audit,{'revision':row[0],'content_sha256':row[1],
                            'payload':row[2],'retired':row[3] is not None})
                        legacy_claim_context.append(proof);closed_context=True
                    if (not row or (row[0],row[1],row[3] is not None) != tuple(p['pin'])
                            or record_content_sha(row[2]) != row[1]
                            or (row[2].get('review_status') in {'human_hold','human_review_required','withheld_unreviewed','rejected','superseded'} and not closed_context)
                            or (p['collection'] in {'claims','viewpoint_claim_links','viewpoint_identity_decisions','canonical_viewpoints','viewpoint_revisions'}
                                and row[2].get('review_status') not in APPROVED and not closed_context)):
                        raise ValueError('human retention current SQL/approval drift')
                    history=c.execute('''SELECT v.payload,v.content_sha256,o.after_sha256,c.status
                        FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c
                        ON c.change_set_id=v.change_set_id JOIN wang_knowledge.change_operations o
                        ON o.change_set_id=v.change_set_id AND o.collection=v.collection AND o.object_id=v.object_id
                        AND o.after_revision=v.revision WHERE v.collection=%s AND v.object_id=%s AND v.revision=%s''',
                        (p['collection'],p['object_id'],p['pin'][0])).fetchall()
                    if len(history)!=1 or history[0]!=(row[2],row[1],row[1],'applied'):
                        raise ValueError('human retention lacks exact committed SQL history')
                    selected=next(i for i in d['items'] if i['item_id']==item['item_id']).get('retained_link_ids',[])
                    if d.get('accepted_postimage_authority') and p['collection']=='viewpoint_claim_links' and p['object_id'] in selected:
                        commit=c.execute('SELECT change_set_id FROM wang_knowledge.object_versions WHERE collection=%s AND object_id=%s AND revision=%s',
                            (p['collection'],p['object_id'],p['pin'][0])).fetchall()
                        decision=c.execute("SELECT payload FROM wang_knowledge.objects WHERE collection='viewpoint_identity_decisions' AND object_id=%s AND retired_at IS NULL",
                            (row[2]['decision_id'],)).fetchone()
                        authority=read_artifact(d['accepted_postimage_authority']['path'])
                        if (commit!=[(d['applied_change_set_id'],)] or not decision
                                or decision[0].get('review_artifact_sha256')!=authority['artifact_sha256']
                                or decision[0].get('reviewer_kind')!='human_editor'
                                or decision[0].get('approval_basis')!='human_exception_review'
                                or decision[0].get('review_status')!='human_approved'):
                            raise ValueError('human postimage lacks actual committed exception review')
            if item['item_id'] in accepted:raise ValueError('overlapping human retention directions')
            accepted.add(item['item_id']);used.append({'direction':b,'item_id':item['item_id'],
                **({'legacy_claim_context_authority':legacy_claim_context} if legacy_claim_context else {})})
    return accepted,used


def partition(audit, packet, root, store):
    ids,_=human_items(audit,packet,root,store)
    body={k:v for k,v in packet.items() if k != 'artifact_sha256'}
    body['items']=[i for i in packet['items'] if i['item_id'] not in ids]
    return {**body,'artifact_sha256':sha256_json(body)},ids


def complete(audit_path, root, store, run_id, output):
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet
    from backend.pipeline.viewpoint_adjudication_history import target_identity, validated_history
    from backend.pipeline.viewpoint_reused_source_resolution import exact_item, create, subset_key
    from backend.pipeline.viewpoint_audit_owner_resolution import binding, audit_semantic_sha
    audit=read_artifact(audit_path);packet=build_packet(audit_path,store,True)
    human,used=human_items(audit,packet,root,store)
    if not human:return None
    remaining={i['item_id']:i for i in packet['items'] if i['item_id'] not in human};parts=[];covered=set()
    from backend.pipeline.cvp_sql_adjudication_input_repair import effective_roots
    for prior in effective_roots(store.adjudication_roots([target_identity(i) for i in remaining.values()])):
        old,findings=validated_history(prior)
        ids={i['item_id'] for i in old['items'] if i['item_id'] in remaining and i['item_id'] not in covered
            and exact_item(i,remaining[i['item_id']]) and findings[i['item_id']]['verdict']=='audit_objection_rejected'}
        if not ids:continue
        path=Path(output).parent/('human-known-'+subset_key(prior,ids))/'resolution.json'
        create(audit_path,prior,path,store,only_item_ids=ids);parts.append({'binding':binding(path),'item_ids':sorted(ids)});covered|=ids
    if covered != set(remaining):return None
    body={'schema_version':RESOLUTION,'run_id':run_id,'audit':binding(audit_path),
        'audit_semantic_sha256':audit_semantic_sha(audit),'packet':packet,'human':used,'parts':parts,
        'master_data_mutations':0,'new_model_calls':0,'independent_verdicts_unchanged':True}
    body['artifact_sha256']=sha256_json(body);verify(audit,body);write_immutable(output,body);return Path(output)


def verify(audit, body):
    from backend.pipeline.cvp_sql_flow import check_binding
    from backend.pipeline.viewpoint_audit_owner_resolution import audit_semantic_sha
    from backend.pipeline.viewpoint_reused_source_resolution import verify as verify_source
    if (body.get('schema_version') != RESOLUTION or body.get('artifact_sha256') != sha256_json({k:v for k,v in body.items() if k!='artifact_sha256'})
            or body['audit_semantic_sha256'] != audit_semantic_sha(audit) or body['master_data_mutations'] != 0
            or body['new_model_calls'] != 0 or body['independent_verdicts_unchanged'] is not True):
        raise ValueError('human audit retention resolution drift')
    check_binding(body['audit'])
    if read_artifact(body['audit']['path']) != audit:raise ValueError('human audit body drift')
    if body['packet'].get('artifact_sha256') != sha256_json({k:v for k,v in body['packet'].items() if k!='artifact_sha256'}):
        raise ValueError('human audit packet seal drift')
    items={i['item_id']:i for i in body['packet']['items']};covered=set()
    for h in body['human']:
        check_binding(h['direction']);ident=verify_item(items[h['item_id']],read_artifact(h['direction']['path']),audit)
        for proof in h.get('legacy_claim_context_authority',[]):
            from backend.pipeline.cvp_retained_relation_only_claim import verify as verify_context
            verify_context(proof)
            claim=items[h['item_id']]['claim']
            if (proof['claim_id']!=claim['claim_id'] or proof['claim_revision']!=claim['revision']
                    or proof['claim_sha256']!=record_content_sha(claim)
                    or proof['claim_id'] in audit['meta']['registry_read_scope']['claim_ids']):
                raise ValueError('closed legacy Claim context receipt target drift')
        if ident in covered:raise ValueError('duplicate human resolution')
        covered.add(ident)
    for part in body['parts']:
        check_binding(part['binding']);ids=set(part['item_ids'])
        if covered&ids:raise ValueError('human/source resolution overlap')
        verify_source(audit,read_artifact(part['binding']['path']),expected_item_ids=ids);covered|=ids
    expected={f'{layer}:{row[key]}:{cid}' for layer,key in [('3','claim_id'),('4','viewpoint_id')]
        for row in audit['layers'][layer]['results'] if row['verdict']=='disputed'
        for cid in ([row[key]] if layer=='3' else row.get('claim_ids_in_question',[]))}
    if covered != expected or set(items) != expected:raise ValueError('human audit resolution population incomplete')
    return {layer:{i.split(':')[1] for i in covered if i.startswith(layer+':')} for layer in ('3','4')}
