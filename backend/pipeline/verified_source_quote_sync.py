"""Deterministic citation maintenance after a user verified a transcription edit.

This copies exact source words and derived bindings. It neither edits Claim
judgments nor supplies content review/qualification; unresolved issues survive.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from backend.pipeline import claim_management as cm

VERSION = 'wang_verified_source_quote_sync_v1'


def compile_sync(verification, before, current, document, fragments, claims, *, linked_claim_ids=()):
    sid = document['source_id']
    if (verification.get('schema_version') != 'wang_human_source_transcription_correction_v1'
            or verification.get('verified_by') != 'user'
            or verification.get('claim_content_approval') is not False
            or verification.get('source_id') != sid):
        raise ValueError('exact human source verification required; not Claim approval')
    key = verification['paragraph_key']; old, new = verification['before'], verification['after']
    if not old or not new or old == new or set(before['paragraphs']) != set(current['paragraphs']):
        raise ValueError('quote synchronization requires a bounded literal correction')
    expected = deepcopy(before['paragraphs'])
    if expected[key]['text'].count(old) != 1:
        raise ValueError('human correction is absent or ambiguous in its original paragraph')
    expected[key]['text'] = expected[key]['text'].replace(old, new)
    if expected != current['paragraphs']:
        raise ValueError('source change exceeds the verified literal correction')
    if (document['source_body_sha256'] != current['body_sha256']
            or document['source_file_sha256'] != current['file']['file_sha256']):
        raise ValueError('current registered source version drift')
    aliases = {sid, document.get('transcript_id'), document.get('canonical_source_id')} - {None, ''}
    records, edits, retained, pending_linked = [], [], [], []
    def digest(text):return hashlib.sha256(text.encode()).hexdigest()
    for fragment in fragments:
        if fragment['source_id'] != sid:raise ValueError('foreign source fragment in sync scope')
        pkey = fragment['paragraph_key']; excerpt = fragment['verbatim_excerpt']
        previous, now = before['paragraphs'][pkey], current['paragraphs'][pkey]
        post = deepcopy(fragment)
        if pkey == key and old in excerpt:
            if excerpt.count(old) != 1 or excerpt not in previous['text']:
                raise ValueError('ambiguous old fragment quote')
            post['verbatim_excerpt'] = excerpt.replace(old, new)
        elif excerpt not in previous['text'] and excerpt not in now['text']:
            raise ValueError('fragment was not an exact quote before this correction')
        if post['verbatim_excerpt'] not in now['text']:
            raise ValueError('new fragment quote does not match current source')
        if fragment.get('source_sha256') not in {before['body_sha256'],current['body_sha256']}:
            raise ValueError('fragment belongs to an unrelated source version')
        if fragment.get('source_segment_index') is not None and fragment['source_segment_index'] != now['index']:
            raise ValueError('fragment coordinate drift')
        for field, values in [('paragraph_text_sha256',{digest(previous['text']),digest(now['text'])}),
                              ('verbatim_excerpt_sha256',{digest(excerpt)})]:
            if fragment.get(field) not in values:raise ValueError('stored fragment hash drift: '+field)
        post.update(source_sha256=current['body_sha256'],paragraph_text_sha256=digest(now['text']),
                    verbatim_excerpt_sha256=digest(post['verbatim_excerpt']))
        oid=fragment['fragment_id']
        if post != fragment:
            records.append({'collection':'source_fragments','object_id':oid,'payload':post})
            if post['verbatim_excerpt'] != excerpt:edits.append('source_fragments/'+oid)
        else:retained.append('source_fragments/'+oid)
    for claim in claims:
        post=deepcopy(claim); touched=False
        for occurrence in post.get('occurrences',[]):
            if not ({occurrence.get('source_id'),occurrence.get('transcript_id')} & aliases):continue
            for anchor in occurrence.get('anchors',[]):
                highlight=anchor.get('proposed_highlight') or {}; text=highlight.get('text','')
                if old not in text:continue
                if anchor.get('paragraph_key') != key or text.count(old) != 1 or text not in before['paragraphs'][key]['text']:
                    raise ValueError('Claim highlight does not uniquely match the verified paragraph')
                changed=text.replace(old,new)
                if changed not in current['paragraphs'][key]['text']:raise ValueError('new Claim highlight source mismatch')
                highlight['text']=changed;touched=True
        if touched:
            oid=claim['claim_id']
            if oid in linked_claim_ids:pending_linked.append(oid);continue
            # The compiler changes only source quotation text inside occurrences.
            for field in set(claim)|set(post):
                if field!='occurrences' and claim.get(field)!=post.get(field):raise ValueError('sync changed Claim judgment')
            records.append({'collection':'claims','object_id':oid,'payload':post});edits.append('claims/'+oid)
    return {'schema_version':VERSION,'records':records,'literal_quote_edits':edits,'retained_keys':retained,
            'pending_linked_claim_ids':pending_linked,'claim_qualification_granted':False,'semantic_review_complete':False}


def load_verified_transition(store, verification_binding, transition_binding):
    """Authenticate the human record against the actual committed source edit."""
    import json
    from backend.pipeline.source_reconciliation_runner import binding
    verification=json.loads(cm.source.verify_file(cm.source.FileBinding.model_validate(verification_binding)).read_text())
    transition=cm.SourceTransition.model_validate_json(cm.source.verify_file(cm.source.FileBinding.model_validate(transition_binding)).read_text())
    sid=verification['source_id']; state=store.get_record_state('source_documents',sid)
    history=store.get_change_history(transition.source_change_set_ids[sid])
    ops=[o for o in (history or {}).get('operations',[]) if o['collection']=='source_documents' and o['object_id']==sid]
    if (not history or history['status']!='applied' or len(ops)!=1 or ops[0]['operation']!='update'
            or state['revision']!=ops[0]['after_revision'] or state['content_sha256']!=ops[0]['after_sha256']
            or history.get('metadata',{}).get('human_source_verification')!=verification_binding
            or transition.before_sources[sid].model_dump()!=verification['before_source']):
        raise ValueError('human verification is not the exact current committed source transition')
    old_binding=transition.before_sources[sid]; doc=state['payload']; new_binding=binding(Path(doc['source_path']))
    if (new_binding['file_sha256']!=verification['proposed_current_source']['file_sha256']
            or ops[0]['before']['source_file_sha256']!=old_binding.file_sha256):
        raise ValueError('human source files drifted')
    raw=cm.source.verify_file(old_binding).read_bytes(); current=cm.source.verify_file(cm.source.FileBinding.model_validate(new_binding)).read_bytes()
    if raw.count(verification['before'].encode())!=1 or raw.replace(verification['before'].encode(),verification['after'].encode())!=current:
        raise ValueError('physical source contains changes outside the human correction')
    return verification,state,cm.source.source_snapshot(old_binding,source=doc),cm.source.source_snapshot(new_binding,source=doc)


def validate_package(package):
    """Store boundary: recompute exact copies; old AI status grants no new review."""
    import json
    from backend.pipeline.source_reconciliation_runner import binding,body,read
    app=package['source_repair_application']
    def load(bound):
        return cm.source.verify_file(cm.source.FileBinding.model_validate(bound))
    if set(app)!={'schema_version','verification','source_transition','source_document','before_records','linked_claim_ids'} or app['schema_version']!=VERSION:
        raise ValueError('quote sync authority fields drift')
    v=json.loads(load(app['verification']).read_text())
    transition=cm.SourceTransition.model_validate_json(load(app['source_transition']).read_text())
    doc=body(read(load(app['source_document'])))
    originals=body(read(load(app['before_records'])))
    prior=transition.before_sources[v['source_id']]
    current=binding(Path(doc['source_path']))
    if prior.model_dump()!=v['before_source'] or current['file_sha256']!=v['proposed_current_source']['file_sha256']:
        raise ValueError('quote sync human source authority drift')
    raw=load(prior.model_dump()).read_bytes()
    if raw.count(v['before'].encode())!=1 or raw.replace(v['before'].encode(),v['after'].encode())!=load(current).read_bytes():
        raise ValueError('quote sync source exceeds human verified edit')
    rows=lambda col:[p for key,p in originals.items() if key.startswith(col+'/')]
    compiled=compile_sync(v,cm.source.source_snapshot(prior,source=doc),cm.source.source_snapshot(current,source=doc),
        doc,rows('source_fragments'),rows('claims'),linked_claim_ids=app['linked_claim_ids'])
    identity={'verification':app['verification'],'source_transition':app['source_transition']}
    expected={'package_id':'SOURCE-QUOTE-SYNC-'+cm.sha256_json(identity)[:24],'source_repair_application':app}
    for row in compiled['records']:expected.setdefault(row['collection'],[]).append(row['payload'])
    if package!=expected:raise ValueError('quote sync package exceeds exact human verified quotation copies')


def persist_verified_correction(store, verification_binding, transition_binding):
    """Register an already committed human correction, without replaying copies."""
    verification,state,before,current=load_verified_transition(store,verification_binding,transition_binding)
    # The source correction is already committed and independently checked by
    # load_verified_transition; persist the human fact even if quote sync pauses.
    from backend.pipeline import claim_human_source as human
    from backend.api.canonical_repository.postgres_store import POSTGRES_APPLY_ADVISORY_LOCK_KEY
    if (verification.get('schema_version') != 'wang_human_source_transcription_correction_v1'
            or verification.get('verified_by') != 'user' or verification.get('claim_content_approval') is not False):
        raise ValueError('source correction is not a human fact')
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (POSTGRES_APPLY_ADVISORY_LOCK_KEY,))
        store._assert_transaction_read_pins(cursor, {('source_documents',verification['source_id']):(state['revision'],state['content_sha256'],False)})
        human.register(cursor, [human.correction_entry(verification_binding,verification,current)],
            origin=transition_binding['file_sha256'])
    return verification,state,before,current


def synchronize(store, verification_binding, transition_binding, *, root):
    """Apply only compiled quotation/binding postimages in the common SQL store.

    Caller holds the existing maintenance/registry locks. A completed receipt
    is replayed with zero writes only while its exact postimages remain current.
    """
    from pathlib import Path
    from backend.pipeline.source_reconciliation_runner import binding,body,read,write
    from backend.pipeline.cvp_sql_commit import pin_rows, decoded_pins
    verification,state,before,current=persist_verified_correction(store,verification_binding,transition_binding)
    root=Path(root);root.mkdir(parents=True,exist_ok=True);receipt=root/'complete.json'
    identity={'verification':verification_binding,'source_transition':transition_binding}
    if receipt.exists():
        result=body(read(receipt))
        if result['inputs']!=identity:raise ValueError('quote sync root cannot change its authority')
        history=store.get_change_history(result['change_set_id']) if result['change_set_id'] else None
        if result['change_set_id'] and (not history or history['status']!='applied'):raise ValueError('quote sync commit is absent')
        with store.connect() as conn,conn.cursor() as cursor:
            store._assert_transaction_read_pins(cursor,decoded_pins(result['current_pins']))
        return result
    from backend.api.canonical_repository.postgres_store import record_content_sha
    from backend.pipeline.cvp_sql_commit import plan_from_document
    doc=state['payload'];sid=doc['source_id'];aliases={sid,doc.get('transcript_id'),doc.get('canonical_source_id')}-{None,''}
    intent_path=root/'intent.json'
    if intent_path.exists():
        intent=body(read(intent_path))
        if intent['inputs']!=identity:raise ValueError('quote sync intent authority changed')
        for bound in intent['artifacts'].values():
            cm.source.verify_file(cm.source.FileBinding.model_validate(bound))
        compiled=body(read(root/'compiled.json'));originals=body(read(root/'before-records.json'))
        plan=plan_from_document(body(read(root/'plan.json')))
    else:
        with store.connect() as conn,conn.cursor() as cursor:
            fragments=[p for col,oid,p in store._reference_consumers(cursor,[sid],['source_fragments']) if p.get('source_id')==sid]
            claims=[p for col,oid,p in store._reference_consumers(cursor,sorted(aliases),['claims'])]
        linked={p['claim_id'] for p in store.claim_links([p['claim_id'] for p in claims])}
        compiled=compile_sync(verification,before,current,doc,fragments,claims,linked_claim_ids=linked)
        originals={'source_fragments/'+p['fragment_id']:p for p in fragments}
        originals.update({'claims/'+p['claim_id']:p for p in claims})
        package={'package_id':'SOURCE-QUOTE-SYNC-'+cm.sha256_json(identity)[:24]}
        for row in compiled['records']:package.setdefault(row['collection'],[]).append(row['payload'])
        write(root/'before-records.json',originals);write(root/'source-document.json',doc)
        package['source_repair_application']={'schema_version':VERSION,'verification':verification_binding,
            'source_transition':transition_binding,'source_document':binding(root/'source-document.json'),
            'before_records':binding(root/'before-records.json'),'linked_claim_ids':sorted(linked)}
        plan=store.plan_package(package,source_kind=VERSION)
        write(root/'compiled.json',compiled);write(root/'plan.json',plan.as_dict())
        write(intent_path,{'inputs':identity,'artifacts':{name:binding(root/name) for name in ('compiled.json','before-records.json','source-document.json','plan.json')}})
    expected={(r['collection'],r['object_id']) for r in compiled['records']}
    if {(o.collection,o.object_id) for o in plan.operations}!=expected:
        raise ValueError('quote sync plan changed its exact compiled write set')
    for op in plan.operations:
        old=originals[op.collection+'/'+op.object_id]
        if op.before_sha256!=record_content_sha(old) or op.before_revision!=old['revision']:
            raise ValueError('quote sync original record drift')
        if op.operation!='update' or any(old.get(k)!=op.payload.get(k) for k in cm.source.PROVENANCE):
            raise ValueError('quote sync cannot create objects or rewrite review provenance')
        changed={k for k in set(old)|set(op.payload) if old.get(k)!=op.payload.get(k)}
        allowed=({'verbatim_excerpt',*cm.FRAGMENT_HASH_FIELDS} if op.collection=='source_fragments' else {'occurrences'})|{'revision'}
        if changed-allowed:raise ValueError('quote sync exceeded its literal/binding field boundary')
    if store.get_change_set_status(plan.fingerprint_sha256)!='applied' and store.claim_links([o.object_id for o in plan.operations if o.collection=='claims']):
        raise ValueError('Claim acquired a CVP link before quote sync')
    if binding(Path(doc['source_path']))!=current['file']:raise ValueError('source changed before quote synchronization')
    store.apply_plan(plan,metadata={'actor':'verified-source-quote-sync','reason':'Copy exact user-verified source quotation and derived bindings; no Claim qualification',
        'human_source_verification':verification_binding,'source_transition':transition_binding,'compiled':binding(root/'compiled.json'),
        'claim_content_approval':False,'claim_qualification_granted':False},
        expected_current_source_records={sid:(state['revision'],state['content_sha256'])})
    expected_pins={('source_documents',sid):(state['revision'],state['content_sha256'],False)}
    expected_pins.update({(o.collection,o.object_id):(o.after_revision,o.after_sha256,False) for o in plan.operations})
    with store.connect() as conn,conn.cursor() as cursor:
        store._assert_transaction_read_pins(cursor,expected_pins)
    if binding(Path(doc['source_path']))!=current['file']:raise ValueError('source changed during quote synchronization')
    result={'schema_version':VERSION,'inputs':identity,'change_set_id':plan.change_set_id if plan.operations else None,
        'source_quotes_synchronized':not compiled['pending_linked_claim_ids'],'pending_linked_claim_ids':compiled['pending_linked_claim_ids'],
        'literal_quote_edits':compiled['literal_quote_edits'],'updated_fragments':sum(o.collection=='source_fragments' for o in plan.operations),
        'updated_claim_highlights':sum(o.collection=='claims' for o in plan.operations),'current_pins':pin_rows(expected_pins),
        'claim_qualification_granted':False,'semantic_review_complete':False,'model_calls':0,'whole_registry_backups':0}
    write(receipt,result)
    return result


def main():
    """Resume exact quotation propagation from a saved human verification."""
    import argparse
    import json
    import os
    from pathlib import Path
    from dotenv import load_dotenv
    from backend.pipeline.source_reconciliation_runner import binding
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts, maintenance_lock
    from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verification',type=Path,required=True)
    parser.add_argument('--source-transition',type=Path,required=True)
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2]/'.env')
    data=Path(os.environ['DATA_BASE_DIR'])
    if not data.is_absolute() or not args.root.is_absolute():
        raise ValueError('absolute external data and artifact paths required')
    ports=ProductionPorts()
    with maintenance_lock(),exclusive_cvp_run_lock({'global_lock_path':str(data/'wang-knowledge-platform/staging/cvp-production-357/registry-cvp.lock')}):
        ports._environment_guard(require_backup=False)
        result=synchronize(ports.store,binding(args.verification),binding(args.source_transition),root=args.root)
    print(json.dumps({k:v for k,v in result.items() if k!='current_pins'},ensure_ascii=False))


if __name__=='__main__':
    main()
