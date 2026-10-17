"""Fixed historical contract: reviewed graph -> evidence subset -> two locators.

Not an ancestor walker. Exactly four contiguous committed generations, one
subset-only remediation and the two named sealed cleanup transforms. No new
review, source reassignment outside the original exact alias contract or write.
"""
import json
from pathlib import Path
from backend.api.canonical_repository.postgres_store import canonical_json, record_content_sha, sha256_json


def require(value, reason):
    if not value:raise ValueError('reviewed evidence-subset locator authority: '+reason)


def subset(before, after):
    old=before.get('evidence_step_ids',[]);new=after.get('evidence_step_ids',[])
    require(old and new and len(old)==len(set(old)) and len(new)==len(set(new))
        and set(new)<set(old) and new==[i for i in old if i in set(new)], 'not ordered strict evidence subset')
    ignored={'revision','evidence_step_ids'}
    require({k:v for k,v in before.items() if k not in ignored}==
        {k:v for k,v in after.items() if k not in ignored}, 'evidence reduction changed Claim meaning')
    require(before.get('review_status')==after.get('review_status')=='ai_consensus_reviewed', 'unapproved Claim')
    return new


def exact_operation(history, cid, before, after):
    rows=[o for o in history['operations'] if o['collection']=='claims' and o['object_id']==cid]
    require(history['status']=='applied' and len(rows)==1, 'missing/ambiguous committed target')
    o=rows[0]
    require(o['operation']=='update' and o['before']==before and o['after']==after
        and o['before_revision']==before['revision'] and o['after_revision']==after['revision']
        and o['before_sha256']==record_content_sha(before) and o['after_sha256']==record_content_sha(after),
        'committed version/postimage drift')


def sealed_plan(history):
    from backend.pipeline.cvp_sql_flow import check_binding,plan_from_document
    from backend.api.canonical_repository.postgres_store import validate_change_set_plan_integrity
    b={'path':history['metadata']['source_plan_path'],'file_sha256':history['metadata']['source_plan_file_sha256']}
    require(Path(b['path']).stat().st_size<=2*1024**2,'unbounded exact source plan');check_binding(b)
    d=json.loads(Path(b['path']).read_text());require(d.get('artifact_sha256')==sha256_json(
        {k:v for k,v in d.items() if k!='artifact_sha256'}),'source plan seal drift')
    p=plan_from_document(d['plan']);validate_change_set_plan_integrity(p)
    require((p.change_set_id,p.fingerprint_sha256,p.source_sha256,p.source_kind)==
        (history['change_set_id'],history['fingerprint_sha256'],history['source_sha256'],history['source_kind']),
        'source plan SQL identity drift')
    fields=('operation','collection','object_id','before_revision','after_revision','before_sha256','after_sha256')
    planned=p.as_dict()['operations'];actual=history['operations']
    require(len(planned)==len(actual) and all(all(a[k]==v[k] for k in fields)
        and canonical_json(dict(a['payload'],revision=a['after_revision']))==canonical_json(v['after'])
        for a,v in zip(planned,actual)), 'complete actual operations differ from sealed plan')
    return d,b


def physical(source):
    from backend.pipeline.cvp_sql_flow import check_binding
    from backend.pipeline.knowledge_source import markdown_source_document
    from backend.pipeline.source_contract_cleanup import build_projection
    from backend.pipeline.source_editorial_transition import verify_current_source_binding
    b=verify_current_source_binding({'path':source['source_path'],'file_sha256':source['source_file_sha256']})
    if source['source_type']=='notes_manuscript':payload=markdown_source_document(source)[0]
    else:
        require(source['source_type']=='sermon_transcript','unknown source type')
        payload=json.loads(Path(source['source_path']).read_text());payload={'script':payload} if isinstance(payload,list) else payload
    index,projection=build_projection(payload['script'])
    require(projection.body_sha256==source['source_body_sha256'],'physical source body changed')
    return index,projection,b


def authority(store,cid,row):
    """Return None for other historical kinds; never recursively fall back."""
    if row.get('retired') or row['revision']<4:return None
    with store.connect() as c:
        versions=c.execute("SELECT v.revision,v.change_set_id,v.content_sha256,v.payload,c.source_kind"
            " FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c ON c.change_set_id=v.change_set_id"
            " WHERE v.collection='claims' AND v.object_id=%s AND v.revision BETWEEN %s AND %s ORDER BY v.revision",
            (cid,row['revision']-3,row['revision'])).fetchall()
    if len(versions)!=4:return None
    kinds=[v[4] for v in versions]
    if kinds!=['review_decision','library_audit_evidence_remediation',
            'wkp368_source_contract_cleanup','wkp368_source_duplicate_cleanup']:return None
    histories=[store.get_change_history(v[1]) for v in versions]
    require(all(h and h['source_kind']==kind for h,kind in zip(histories,kinds)), 'typed history changed')
    require(all(v[0]==row['revision']-3+i and v[2]==record_content_sha(v[3]) for i,v in enumerate(versions))
        and versions[-1][3]==row['payload'] and versions[-1][2]==row['content_sha256'], 'current generation drift')
    approved,reduced,notes_current,current=[v[3] for v in versions]
    for i in range(1,4):exact_operation(histories[i],cid,versions[i-1][3],versions[i][3])
    kept=subset(approved,reduced)
    with store.connect() as c:
        events=c.execute("SELECT review_event_id,reviewer_kind,decision,artifact FROM wang_knowledge.review_events"
            " WHERE collection='claims' AND object_id=%s AND object_revision=%s ORDER BY review_event_id",
            (cid,approved['revision'])).fetchall()
    require(len(events)==1 and events[0][1] in {'ai','human','system'}
        and events[0][2]=='ai_consensus_reviewed' and events[0][3]
        and events[0][0]==histories[0]['metadata'].get('review_event_id')
        and events[0][3].get('change_set_id',versions[0][1])==versions[0][1], 'original exact committed review missing')
    reviewed=[o for o in histories[0]['operations'] if o['collection']=='claims' and o['object_id']==cid]
    require(histories[0]['status']=='applied' and len(reviewed)==1 and reviewed[0]['after']==approved
        and reviewed[0]['after_sha256']==versions[0][2], 'SQL review postimage not exact')
    nd,nb=sealed_plan(histories[2]);dd,db=sealed_plan(histories[3])
    require(nd['schema_version']=='wang_source_contract_cleanup_source_plan_v1'
        and dd['schema_version']=='wang_source_duplicate_cleanup_plan_v1','wrong typed source plans')
    from backend.pipeline.source_contract_cleanup import migrate_claim_occurrence_anchors,remap_claim_occurrence_source
    ni,np,ns=physical(nd['source']);m,findings=migrate_claim_occurrence_anchors(reduced,ni,
        source_id=nd['source']['source_id'],transcript_id=nd['source']['transcript_id'])
    require(m and findings and all(f['status']=='changed' for f in findings)
        and dict(m,revision=notes_current['revision'])==notes_current,'notes locator full postimage differs')
    changed=[(a,b) for a,b in zip(notes_current['occurrences'],current['occurrences']) if a!=b]
    require(len(changed)==1, 'multiple/ambiguous source identity transforms')
    oldocc,newocc=changed[0]
    require(oldocc['transcript_id']==newocc['transcript_id'],'transcript identity changed')
    groups=[g for g in dd['groups'] if g['alias_source_id']==oldocc['source_id']
        and g['canonical_source_id']==newocc['source_id'] and g['transcript_id']==oldocc['transcript_id']]
    require(len(groups)==1 and groups[0].get('coordinate_ready') is True
        and not any(groups[0].get('coordinate_blockers',{}).values()),'alias not exact declared ready group')
    group=groups[0];frozen=[s for s in dd['frozen_sources'] if s['transcript_id']==group['transcript_id']]
    require(len(frozen)==1,'missing/ambiguous frozen own source')
    source={**frozen[0],'source_type':'sermon_transcript','source_id':group['canonical_source_id']}
    si,sp,ss=physical(source)
    alias_ops=[o for o in histories[3]['operations'] if o['collection']=='source_documents'
        and o['object_id']==group['alias_source_id']]
    require(len(alias_ops)==1 and alias_ops[0]['operation']=='retire', 'alias was not actually retired')
    alias=alias_ops[0]['before'];alias_live=store.get_record_state('source_documents',group['alias_source_id'])
    require(alias['source_path']==source['source_path'] and alias['source_type']=='sermon_transcript'
        and alias['transcript_id']==source['transcript_id'] and alias['source_sha256']==source['source_file_sha256']
        and alias_live and alias_live['retired'] and alias_live['revision']==alias_ops[0]['after_revision']
        and {k:v for k,v in alias_live['payload'].items() if k!='revision'}==
            {k:v for k,v in alias_ops[0]['after'].items() if k!='revision'}
        and alias_live['content_sha256']==alias_ops[0]['after_sha256']==record_content_sha(alias),
        'alias retirement/source identity drift')
    remapped=remap_claim_occurrence_source(notes_current,old_source_id=group['alias_source_id'],canonical_source_id=group['canonical_source_id'])
    require(remapped,'missing exact alias remap')
    m,findings=migrate_claim_occurrence_anchors(remapped,si,source_id=group['canonical_source_id'],transcript_id=group['transcript_id'])
    require(m and findings and all(f['status']=='changed' for f in findings)
        and dict(m,revision=current['revision'])==current,'alias locator full postimage differs')
    # The retained Evidence was already present at the directly approved
    # generation. Only its exact declared alias/coordinate transformation is
    # consumed; no new evidence, changed quote or eligibility is admitted.
    dependency=[]
    from backend.pipeline.source_contract_cleanup import migrate_source_fragment
    duplicate_ops={(o['collection'],o['object_id']):o for o in histories[3]['operations']}
    with store.connect() as c:
        for eid in kept:
            ev=c.execute("SELECT revision,content_sha256,payload,retired_at FROM wang_knowledge.objects WHERE collection='evidence_steps' AND object_id=%s",(eid,)).fetchone()
            require(ev and ev[3] is None and ev[1]==record_content_sha(ev[2])
                and ev[2].get('support_eligibility') in {'eligible','eligible_candidate'}
                and cid in ev[2].get('produced_claim_ids',[]) and ev[0]==1,'retained reviewed Evidence changed/held')
            origin=c.execute("SELECT v.payload,v.content_sha256,c.status FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c ON c.change_set_id=v.change_set_id WHERE v.collection='evidence_steps' AND v.object_id=%s AND v.revision=1",(eid,)).fetchall()
            require(origin==[(ev[2],ev[1],'applied')],'retained Evidence missing exact creation history')
            dependency.append({'collection':'evidence_steps','object_id':eid,'pin':[ev[0],ev[1],False]})
            fids=set(ev[2].get('source_fragment_ids') or [])
            if ev[2].get('source_fragment_id'):fids.add(ev[2]['source_fragment_id'])
            require(fids,'retained Evidence missing own fragments')
            for fid in sorted(fids):
                op=duplicate_ops.get(('source_fragments',fid));live=store.get_record_state('source_fragments',fid)
                require(op and op['operation']=='update' and op['before']['source_id']==group['alias_source_id']
                    and op['after']['source_id']==group['canonical_source_id']
                    and live and not live['retired'] and live['payload']==op['after']
                    and live['content_sha256']==op['after_sha256']==record_content_sha(op['after']),
                    'retained fragment not exact declared alias update')
                original=c.execute("SELECT v.payload,v.content_sha256,c.status FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c ON c.change_set_id=v.change_set_id WHERE v.collection='source_fragments' AND v.object_id=%s AND v.revision=%s",
                    (fid,op['before_revision'])).fetchall()
                require(original==[(op['before'],op['before_sha256'],'applied')], 'original reviewed fragment history drift')
                base={**op['before'],'source_id':group['canonical_source_id']}
                moved,resolution=migrate_source_fragment(base,si,source_sha256=sp.body_sha256)
                require(moved and resolution.status=='resolved' and dict(moved,revision=op['after_revision'])==op['after'],
                    'retained fragment deterministic postimage drift')
                dependency.append({'collection':'source_fragments','object_id':fid,'pin':[live['revision'],live['content_sha256'],False]})
    doc=store.get_record_state('source_documents',group['canonical_source_id'])
    require(doc and not doc['retired'] and doc['payload']['source_path']==source['source_path']
        and doc['payload']['source_file_sha256']==source['source_file_sha256']
        and doc['payload']['source_body_sha256']==sp.body_sha256,'current canonical physical source drift')
    dependency.append({'collection':'source_documents','object_id':group['canonical_source_id'],
        'pin':[doc['revision'],doc['content_sha256'],False]})
    return {'claim_id':cid,'revision':row['revision'],'content_sha256':row['content_sha256'],
        'authority_kind':'fixed_reviewed_evidence_subset_notes_then_alias_locator',
        'review_event_id':events[0][0],'reviewed_revision':approved['revision'],
        'review_artifact_sha256':sha256_json(events[0][3]),'decision':events[0][2],
        'exact_applied_change_set_ids':[v[1] for v in versions],
        'bindings':[nb,db,ns,ss],'current_dependency_pins':dependency,
        'model_calls':0,'master_data_mutations':0}
