"""Close typed source impacts after ordinary coverage and full group audit.

No pending list or model verdict is rewritten. Only exact human-retired
Evidence relations, independently retained relations, and declared source
group annotations belong here. CVP/link/unknown impacts stay in their domain.
"""
from pathlib import Path
from backend.api.canonical_repository.postgres_store import sha256_json,record_content_sha
from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable

VERSION='wang_sequential_source_impact_resolution_v1'
HANDOFF='wang_sequential_source_cvp_handoff_v1'


def context(value):
    e=value.get('reviewed',{}).get('evidence',{})
    c=e.get('action',{}).get('materials',{}).get('claim_management_resolution',{})
    return c if c.get('schema_version')==HANDOFF else None


def applied_review(store,value,sha,*,cursor=None):
    """One named SQL review archive and its actual commit, never ancestry."""
    if cursor is None:
        with store.connect() as conn,conn.cursor() as cur:
            return applied_review(store,value,sha,cursor=cur)
    ident='CVPR-'+sha256_json({'parent':value['run_id'],'reviewed_sha256':sha})
    row=cursor.execute("SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s",(ident,)).fetchone()
    if not row or row[0].get('parent_run_id')!=value['run_id'] or row[0].get('reviewed_sha256')!=sha:
        raise ValueError('source impact original SQL review archive missing/foreign')
    review=row[0]['reviewed'];p=review['plan']
    actual=cursor.execute('SELECT status,fingerprint_sha256,source_sha256,metadata FROM wang_knowledge.change_sets WHERE change_set_id=%s',
                          (p['change_set_id'],)).fetchone()
    if (sha256_json(review)!=sha or row[0]['checkpoint'] not in {'applied','audited'}
            or p['change_set_id'] not in value['change_set_ids'] or not actual
            or actual[:3]!=('applied',p['fingerprint_sha256'],p['source_sha256'])
            or actual[3].get('run_id')!=value['run_id'] or actual[3].get('reviewed_sha256')!=sha
            or actual[3].get('approval')!=review['evidence']['approval']):
        raise ValueError('source impact archive has no exact committed approval')
    return review


def prepare(store,value):
    from backend.pipeline.cvp_sql_flow import check_binding,decoded_pins
    from backend.pipeline.source_reconciliation_runner import body,read
    from backend.pipeline.claim_evidence_binding import coverage_context
    from backend.pipeline.source_consumer_scope import declared_group_keys
    handoff=context(value)
    if not handoff:return None
    pending=value['reviewed']['evidence'].get('pending_cvp_management_record_keys',[])
    if (not pending or len(pending)!=len(set(pending)) or sorted(pending)!=sorted(handoff['pending_existing_cvp_record_keys'])
            or handoff['original_group_claim_ids']!=value['claim_ids']
            or any(k.split('/',1)[0] not in {'knowledge_relations','groups'} for k in pending)):
        raise ValueError('source impact pending domain/denominator drift')
    source=applied_review(store,value,handoff['prior_reviewed_sha256'])
    original={**value,'reviewed':source,'reviewed_sha256':handoff['prior_reviewed_sha256']}
    rebuilt=coverage_context(store,original,{'claim_ids':value['claim_ids']})
    if rebuilt!=handoff:raise ValueError('source impact saved handoff drift')
    app=source['evidence']['package']['payload']['source_repair_application']
    check_binding(app['materials']);material=body(read(Path(app['materials']['path'])))
    before={(r['collection'],r['object_id']):r['payload'] for r in material['before_records']}
    original_pins=decoded_pins(material['sql_scope']['read_pins'])
    review=read(Path(app['run_root'])/'final_review.raw.json')
    verdicts={r['record_key']:r for r in review['response']['verdicts']}
    groups=declared_group_keys(material);items=[];pins=dict(original_pins);bindings=[app['materials'],handoff['source_terminal_review'],handoff['shared_owner_audit']]
    for op in source['plan']['operations']:
        pins[op['collection'],op['object_id']]=(op['after_revision'],op['after_sha256'],False)
    for key in pending:
        col,oid=key.split('/',1)
        if col=='groups':
            if oid not in groups:raise ValueError('undeclared source group annotation')
            items.append({'record_key':key,'kind':'declared_source_group_not_cvp'})
            continue
        row=store.get_record_state(col,oid)
        if not row:raise ValueError('pending source relation missing')
        pin=(row['revision'],row['content_sha256'],row['retired'])
        if record_content_sha(row['payload'])!=pin[1]:raise ValueError('source relation SHA drift')
        if not row['retired']:
            if (original_pins.get((col,oid))!=pin or before.get((col,oid))!=row['payload']
                    or verdicts.get(key,{}).get('approved') is not True
                    or row['payload'].get('review_status') in {'human_hold','rejected','needs_review'}):
                raise ValueError('retained source relation lacks exact independent postimage approval')
            items.append({'record_key':key,'kind':'independently_retained_source_relation','pin':list(pin)})
        else:
            matches=[]
            for cid in value['change_set_ids']:
                h=store.get_change_history(cid)
                for op in h['operations']:
                    if (op['collection'],op['object_id'])==(col,oid) and op['operation']=='retire' and op['after_revision']==pin[0]:
                        matches.append((h,op))
            if len(matches)!=1:raise ValueError('retired source relation lacks unique group withdrawal')
            h,op=matches[0]
            from backend.pipeline.claim_relation_retirement import SCHEMA,validate_package
            if (h['source_kind']!=SCHEMA or h['status']!='applied' or h['metadata'].get('run_id')!=value['run_id']
                    or op['before']!=row['payload'] or not op['after']
                    or {k:v for k,v in op['before'].items() if k!='revision'}!={k:v for k,v in op['after'].items() if k!='revision'}
                    or op['after']['revision']!=pin[0] or op['after_sha256']!=pin[1]):
                raise ValueError('source relation withdrawal is not exact human logical retirement')
            retired=applied_review(store,value,h['metadata']['reviewed_sha256'])
            validate_package(retired['evidence']['package']['payload'])
            authority=retired['evidence']['approval']['human_exact_disposition'];check_binding(authority);bindings.append(authority)
            items.append({'record_key':key,'kind':'exact_human_logical_retirement','pin':list(pin),'change_set_id':h['change_set_id'],
                          'reviewed_sha256':h['metadata']['reviewed_sha256']})
        pins[col,oid]=pin
    return {'source_handoff':handoff,'pending_record_keys':list(pending),'items':items,
            'read_pins':[{'collection':c,'object_id':i,'pin':list(p)} for (c,i),p in sorted(pins.items())],
            'bindings':bindings,'model_verdicts_changed':False,'original_pending_list_preserved':True}


def build(store,run_id,audit_path,output,*,semantic_resolution=None):
    from backend.pipeline.cvp_sql_flow import binding,validate_transaction_audit
    from backend.pipeline.viewpoint_cvp_campaign_runner import validate_audit
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
    value=store.get_cvp_group(run_id);facts=prepare(store,value)
    if facts is None:raise ValueError('source impact resolution lacks typed handoff')
    audit=read_artifact(audit_path);views=value['reviewed']['evidence']['viewpoint_ids']
    validate_transaction_audit(audit,value['claim_ids'],views)
    validate_audit(audit,value['claim_ids'],views,owner_resolution_path=semantic_resolution)
    proofs=verify_saved_full_audit(Path(audit_path),layers=('3','4'))
    receipt={'schema_version':VERSION,'run_id':run_id,'reviewed_sha256':value['reviewed_sha256'],
             'audit':binding(audit_path),'audit_proofs':proofs,'semantic_resolution':binding(semantic_resolution) if semantic_resolution else None,**facts}
    write_immutable(output,{**receipt,'artifact_sha256':sha256_json(receipt)})
    return output


def validate_for_completion(value,path,audit_binding,cursor,*,semantic_resolution=None):
    from backend.pipeline.cvp_sql_flow import check_binding,decoded_pins,binding
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    body=read_artifact(path)
    if (body.get('schema_version')!=VERSION or body.get('artifact_sha256')!=sha256_json({k:v for k,v in body.items() if k!='artifact_sha256'})
            or body.get('run_id')!=value['run_id'] or body.get('reviewed_sha256')!=value['reviewed_sha256']
            or body.get('audit')!=audit_binding or body.get('source_handoff')!=context(value)
            or body.get('pending_record_keys')!=value['reviewed']['evidence']['pending_cvp_management_record_keys']
            or body.get('model_verdicts_changed') is not False or body.get('original_pending_list_preserved') is not True
            or body.get('semantic_resolution')!=(binding(semantic_resolution) if semantic_resolution else None)):
        raise ValueError('source impact completion proof drift')
    for b in body['bindings']+body['audit_proofs']:check_binding(b)
    source=applied_review(None,value,body['source_handoff']['prior_reviewed_sha256'],cursor=cursor)
    if (source['evidence']['package']['sha256']!=body['source_handoff']['source_package_sha256']
            or source['plan']['change_set_id']!=body['source_handoff']['source_change_set_id']):
        raise ValueError('source impact prior commit differs')
    from backend.pipeline.source_reconciliation_runner import read,body as unseal
    from backend.pipeline.source_consumer_scope import declared_group_keys
    app=source['evidence']['package']['payload']['source_repair_application']
    check_binding(app['materials']);material=unseal(read(Path(app['materials']['path'])))
    required_bindings=[app['materials'],body['source_handoff']['source_terminal_review'],body['source_handoff']['shared_owner_audit']]
    groups=declared_group_keys(material)
    original=decoded_pins(material['sql_scope']['read_pins'])
    verdicts={v['record_key']:v for v in read(Path(app['run_root'])/'final_review.raw.json')['response']['verdicts']}
    items=body['items'];pending=body['pending_record_keys'];pins=decoded_pins(body['read_pins'])
    expected=dict(original)
    for op in source['plan']['operations']:
        expected[op['collection'],op['object_id']]=(op['after_revision'],op['after_sha256'],False)
    for item in items:
        if item['kind']=='exact_human_logical_retirement':expected[tuple(item['record_key'].split('/',1))]=tuple(item['pin'])
    if (len(items)!=len(pending) or [i['record_key'] for i in items]!=pending
            or any(i['kind'] not in {'declared_source_group_not_cvp','independently_retained_source_relation','exact_human_logical_retirement'} for i in items)
            or len(pins)!=len(body['read_pins']) or pins!=expected):
        raise ValueError('source impact completion lost typed obligations')
    for item in items:
        col,oid=item['record_key'].split('/',1)
        if item['kind']=='declared_source_group_not_cvp':
            if col!='groups' or oid not in groups:raise ValueError('source group completion is not declared')
            continue
        pin=pins.get((col,oid))
        if col!='knowledge_relations' or not pin or list(pin)!=item.get('pin'):
            raise ValueError('source relation completion domain/pin drift')
        if item['kind']=='independently_retained_source_relation':
            if pin[2] or original.get((col,oid))!=pin or verdicts.get(item['record_key'],{}).get('approved') is not True:
                raise ValueError('source retained completion lost actual independent approval')
        if item['kind']=='exact_human_logical_retirement':
            retired=applied_review(None,value,item['reviewed_sha256'],cursor=cursor)
            from backend.pipeline.claim_relation_retirement import validate_package
            targets=validate_package(retired['evidence']['package']['payload'])
            required_bindings.append(retired['evidence']['approval']['human_exact_disposition'])
            if (retired['plan']['change_set_id']!=item['change_set_id'] or targets!=((col,oid),)
                    or not pin[2] or len(retired['plan']['operations'])!=1
                    or retired['plan']['operations'][0]['after_revision']!=pin[0]
                    or retired['plan']['operations'][0]['after_sha256']!=pin[1]):
                raise ValueError('source withdrawal commit drift')
    if body['bindings']!=required_bindings:
        raise ValueError('source impact completion omitted exact authority/source bindings')
    PostgresKnowledgeStore._assert_transaction_read_pins(cursor,pins)
    for dependency in body['source_handoff']['audited_dependency_impacts']:
        found=cursor.execute("""SELECT 1 FROM wang_knowledge.objects WHERE collection='viewpoint_claim_links'
            AND retired_at IS NULL AND payload->>'claim_id'=%s AND payload->>'effective_state'='active' LIMIT 1""",
            (dependency['claim_id'],)).fetchone()
        if found:raise ValueError('source dependency acquired unaudited CVP impact')
    return body
