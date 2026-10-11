"""Close typed source impacts after ordinary coverage and full group audit.

No pending list or model verdict is rewritten. Exact committed source
management and unchanged, fully audited CVP impacts have separate typed
proofs. Unapproved unused candidate relations remain unresolved context;
unknown, changed or unaudited dependencies cannot acquire closure authority.
"""
from pathlib import Path
from backend.api.canonical_repository.postgres_store import sha256_json,record_content_sha
from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable

VERSION='wang_sequential_source_impact_resolution_v1'
HANDOFF='wang_sequential_source_cvp_handoff_v1'
SLICE_IMPACTS='wang_source_slice_current_impact_resolution_v1'
CLOSED_STATEMENT='wang_completed_statement_impact_revalidation_v1'


def closed_statement_context(value):
    """Classify an existing SQL completion; its authority is verified below."""
    proof=value.get('completion',{}).get('retained_impacts_resolution')
    if not proof:return None
    from backend.pipeline.cvp_sql_flow import check_binding
    check_binding(proof);old=read_artifact(proof['path'])
    if old.get('schema_version')==SLICE_IMPACTS:
        return {'schema_version':CLOSED_STATEMENT,'source_reviewed_sha256':old['source_reviewed_sha256'],
                'source_change_set_id':old['source_change_set_id'],'claim_layer_completion':old['claim_layer_completion'],
                'claim_ids':old['claim_ids'],'viewpoint_ids':old['viewpoint_ids']}
    candidate=old.get('source_handoff',{})
    return candidate if candidate.get('schema_version')==CLOSED_STATEMENT else None


def audited_claim_transition(cursor,cid,old_pin):
    """One actual source commit and its SQL-recorded Claim-layer receipt.

    Not an ancestor search or permission to infer a new Claim judgement from
    a CVP audit. The complete old/current payloads are bound by actual SQL.
    """
    from backend.pipeline.cvp_sql_flow import check_binding,decoded_pins
    from backend.pipeline.claim_first_management import validate_claim_audit_report
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    row=cursor.execute("SELECT payload,revision,content_sha256,retired_at IS NOT NULL FROM wang_knowledge.objects WHERE collection='claims' AND object_id=%s",(cid,)).fetchone()
    if (not row or row[3] or record_content_sha(row[0])!=row[2]
            or row[0].get('review_status') in {'human_hold','human_review_required','rejected','needs_review'}
            or row[0].get('effective_state')=='held'):
        raise ValueError('source dependency current Claim missing/held/retired')
    current=(row[1],row[2],False)
    if current==tuple(old_pin):return None
    record=cursor.execute("SELECT v.change_set_id,c.metadata FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c USING(change_set_id) WHERE v.collection='claims' AND v.object_id=%s AND v.revision=%s AND v.content_sha256=%s AND c.status='applied'",(cid,row[1],row[2])).fetchone()
    if not record or not record[1].get('run_id') or not record[1].get('reviewed_sha256'):
        raise ValueError('source dependency lacks unique committed generation')
    parent=cursor.execute("SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s AND stage='cvp_group'",(record[1]['run_id'],)).fetchone()
    if not parent:raise ValueError('source dependency committed parent missing')
    owner={**parent[0],'run_id':record[1]['run_id']}
    reviewed=applied_review(None,owner,record[1]['reviewed_sha256'],cursor=cursor)
    plan=reviewed['plan'];e=reviewed['evidence'];package=e['package']['payload']
    app=package.get('source_repair_application',{})
    if (plan['change_set_id']!=record[0] or not app or app.get('run_id')!=owner['run_id']
            or sha256_json(package)!=e['package']['sha256'] or plan['source_sha256']!=e['package']['sha256']):
        raise ValueError('source dependency actual source approval drift')
    ops=plan['operations'];fields=('operation','collection','object_id','before_revision','after_revision','before_sha256','after_sha256')
    actual=cursor.execute('SELECT operation,collection,object_id,before_revision,after_revision,before_sha256,after_sha256 FROM wang_knowledge.change_operations WHERE change_set_id=%s ORDER BY operation_index',(record[0],)).fetchall()
    if actual!=[tuple(o[k] for k in fields) for o in ops]:raise ValueError('source dependency complete write set drift')
    claims=[o for o in ops if o['collection']=='claims' and o['object_id']==cid]
    if (len(claims)!=1 or claims[0]['operation']!='update'
            or tuple(old_pin)!=(claims[0]['before_revision'],claims[0]['before_sha256'],False)
            or current!=(claims[0]['after_revision'],claims[0]['after_sha256'],False)
            or claims[0]['payload']!=row[0]):
        raise ValueError('source dependency is not the exact immediately approved Claim transition')
    before=cursor.execute("SELECT payload,content_sha256 FROM wang_knowledge.object_versions WHERE collection='claims' AND object_id=%s AND revision=%s",(cid,old_pin[0])).fetchone()
    if not before or before[1]!=old_pin[1] or record_content_sha(before[0])!=old_pin[1]:
        raise ValueError('source dependency old complete Claim generation missing')
    for proof in [app['materials'],*e['sources']]:check_binding(proof)
    for proof in e['approval'].values():
        if isinstance(proof,dict) and 'path' in proof:check_binding(proof)
    marker=owner.get('claim_layer_audit',{})
    if marker.get('status')!='claim_layer_audited_cvp_pending' or not marker.get('receipt'):
        raise ValueError('source dependency new generation has no recorded complete Claim-layer audit')
    proof=marker['receipt'];check_binding(proof);receipt=read_artifact(proof['path'])
    if (receipt.get('status')!='claim_layer_audited_cvp_pending' or cid not in receipt.get('claim_ids',[])
            or receipt.get('claim_ids')!=owner['claim_ids'] or record[0] not in receipt.get('change_set_ids',[])
            or decoded_pins(receipt['read_pins']).get(('claims',cid))!=current):
        raise ValueError('source dependency current Claim audit receipt drift')
    check_binding(receipt['audit']);audit=read_artifact(receipt['audit']['path'])
    validate_claim_audit_report(audit,receipt['claim_ids'])
    if decoded_pins(audit['meta']['registry_read_scope']['read_pins']).get(('claims',cid))!=current:
        raise ValueError('source dependency independently audited another Claim generation')
    for b in receipt['source_proofs']:check_binding(b)
    current_ops={(o['collection'],o['object_id']):(o['after_revision'],o['after_sha256'],False) for o in ops}
    PostgresKnowledgeStore._assert_transaction_read_pins(cursor,current_ops)
    reviewed_links={p['viewpoint_claim_link_id']:p for p in package.get('viewpoint_claim_links',[]) if p['claim_id']==cid}
    links=cursor.execute("SELECT object_id,payload FROM wang_knowledge.objects WHERE collection='viewpoint_claim_links' AND retired_at IS NULL AND payload->>'claim_id'=%s AND payload->>'effective_state'='active'",(cid,)).fetchall()
    if (not links or {i for i,p in links}!=set(reviewed_links)
            or any(p!=reviewed_links[i] or p.get('pinned_claim_revision')!=row[0]['revision']
                   or p.get('review_status')!='system_approved' for i,p in links)):
        raise ValueError('source dependency current active connections lack complete joint approval')
    return {'claim_id':cid,'before_pin':list(old_pin),'current_pin':list(current),
            'source_change_set_id':record[0],'source_run_id':owner['run_id'],
            'reviewed_sha256':record[1]['reviewed_sha256'],'claim_layer_receipt':proof,
            'revisit_required':receipt.get('revisit_required',False),'independent_complete':receipt.get('independent_complete',True)}


def completed_source_facts(value,*,store=None,cursor=None):
    """Reuse only SQL-recorded closed source obligations; audit CVPs afresh.

    Broad historical readsets are not current business obligations. Old
    verdicts/pins remain in the bound receipt, never rewritten to current.
    """
    if cursor is None:
        if store is None:return None
        with store.connect() as conn,conn.cursor() as cur:return completed_source_facts(value,cursor=cur)
    from backend.pipeline.cvp_sql_flow import check_binding,decoded_pins,pin_rows
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    saved=cursor.execute("SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s AND stage='cvp_group'",(value['run_id'],)).fetchone()
    if (not saved or saved[0].get('reviewed_sha256')!=value['reviewed_sha256']
            or sha256_json(value['reviewed'])!=value['reviewed_sha256']):
        raise ValueError('completed source facts current review drift')
    completion=saved[0].get('completion',{});prior=completion.get('retained_impacts_resolution')
    if not prior:return None
    check_binding(prior);old=read_artifact(prior['path'])
    if old.get('schema_version') not in {VERSION,SLICE_IMPACTS}:return None
    # A legitimately committed NEW source/management review must use its own
    # strict pending workflow, not inherit a previous generation's closure.
    if old.get('reviewed_sha256')!=value['reviewed_sha256']:return None
    pending=value['reviewed']['evidence'].get('pending_cvp_management_record_keys',[])
    handoff=context(value,cursor=cursor)
    statement=handoff and handoff.get('schema_version')==CLOSED_STATEMENT
    if statement and handoff!=closed_statement_context({**value,'completion':completion}):
        raise ValueError('closed statement handoff differs from actual SQL completion')
    if (old.get('artifact_sha256')!=sha256_json({k:v for k,v in old.items() if k!='artifact_sha256'})
            or old.get('run_id')!=value['run_id'] or old.get('reviewed_sha256')!=value['reviewed_sha256']
            or old.get('audit')!=completion.get('audit')
            or (old.get('source_handoff')!=handoff and old.get('schema_version')!=SLICE_IMPACTS)
            or old.get('pending_record_keys')!=pending or old.get('model_verdicts_changed') is not False
            or old.get('original_pending_list_preserved') is not True):
        raise ValueError('completed source facts lack exact recorded prior closure')
    for b in old['bindings']+old.get('audit_proofs',[]):check_binding(b)
    if statement:
        return completed_statement_facts(value,old,prior,handoff,cursor)
    source=applied_review(None,value,handoff['prior_reviewed_sha256'],cursor=cursor)
    policy_slice=handoff.get('source_slice_mode') in {'policy_evidence_binding','policy_claim_source_revision'}
    if policy_slice:
        from backend.pipeline.claim_source_write_slice import validate_package
        package=source['evidence']['package']['payload'];validate_package(package)
        if (package['source_repair_application']['source_write_slice']['mode']!=handoff['source_slice_mode']
                or handoff.get('source_only_approval') is not True
                or handoff.get('existing_cvp_content_approved') is not False
                or pending!=handoff['pending_existing_cvp_record_keys']):
            raise ValueError('closed policy slice original source domain drift')
        fields=('operation','collection','object_id','before_revision','after_revision','before_sha256','after_sha256')
        actual=cursor.execute('SELECT operation,collection,object_id,before_revision,after_revision,before_sha256,after_sha256 FROM wang_knowledge.change_operations WHERE change_set_id=%s ORDER BY operation_index',
                              (source['plan']['change_set_id'],)).fetchall()
        if actual!=[tuple(o[k] for k in fields) for o in source['plan']['operations']]:
            raise ValueError('closed policy slice actual full write set drift')
        expected=[(read_policy_slice_item if handoff['source_slice_mode']=='policy_claim_source_revision' else read_question_context)(cursor,k) for k in pending]
        if expected!=old['items']:raise ValueError('closed policy pending context/approval/consumer drift')
    # These actual source writes and all explicit source obligations must be
    # unchanged. Unrelated broad material pins remain historical only.
    old_pins=decoded_pins(old['read_pins']);critical={}
    for op in source['plan']['operations']:
        key=(op['collection'],op['object_id'])
        if key[0] not in ({'claims','evidence_steps','source_fragments'} if policy_slice else {'evidence_steps','source_fragments'}):
            raise ValueError('completed source facts not an Evidence-only source domain')
        critical[key]=(op['after_revision'],op['after_sha256'],False)
    for item in old['items']:
        if 'pin' in item:
            key=tuple(item['record_key'].split('/',1));critical[key]=tuple(item['pin'])
    transitions=[]
    for dep in handoff.get('audited_dependency_impacts',[]):
        cid=dep['claim_id'];transition=audited_claim_transition(cursor,cid,dep['claim_pin'])
        critical['claims',cid]=tuple(transition['current_pin'] if transition else dep['claim_pin'])
        if transition:transitions.append(transition)
    for key,pin in critical.items():
        if key[0]!='claims' and old_pins.get(key)!=pin:raise ValueError('completed source facts original target pin drift')
    PostgresKnowledgeStore._assert_transaction_read_pins(cursor,critical)
    return {'source_handoff':handoff,'pending_record_keys':list(pending),'items':old['items'],
            'read_pins':pin_rows(critical),'bindings':old['bindings'],
            'prior_completed_resolution':prior,'audited_claim_transitions':transitions,
            'model_verdicts_changed':False,'original_pending_list_preserved':True}


def completed_statement_facts(value,old,prior,handoff,cursor):
    from backend.pipeline.cvp_sql_flow import check_binding,pin_rows
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    if (handoff['claim_ids']!=value['claim_ids']
            or value['reviewed']['evidence'].get('pending_cvp_claim_ids')!=value['claim_ids']):
        raise ValueError('closed statement original Claim population drift')
    source=applied_review(None,value,handoff['source_reviewed_sha256'],cursor=cursor)
    if source['plan']['change_set_id']!=handoff['source_change_set_id']:
        raise ValueError('closed statement original source commit drift')
    fields=('operation','collection','object_id','before_revision','after_revision','before_sha256','after_sha256')
    operations=cursor.execute('SELECT operation,collection,object_id,before_revision,after_revision,before_sha256,after_sha256 FROM wang_knowledge.change_operations WHERE change_set_id=%s ORDER BY operation_index',
                              (handoff['source_change_set_id'],)).fetchall()
    if operations!=[tuple(o[k] for k in fields) for o in source['plan']['operations']]:
        raise ValueError('closed statement complete source write set drift')
    from backend.pipeline.claim_source_write_slice import validate_package
    validate_package(source['evidence']['package']['payload'])
    check_binding(handoff['claim_layer_completion'])
    original_receipt=read_artifact(handoff['claim_layer_completion']['path'])
    if original_receipt['claim_ids']!=value['claim_ids'] or handoff['source_change_set_id'] not in original_receipt['change_set_ids']:
        raise ValueError('closed statement Claim-layer lineage drift')
    critical={(o['collection'],o['object_id']):(o['after_revision'],o['after_sha256'],False)
              for o in source['plan']['operations']}
    for item in old['items']:
        if 'pin' not in item:raise ValueError('closed statement pending target has no exact pin')
        critical[tuple(item['record_key'].split('/',1))]=tuple(item['pin'])
    actual=PostgresKnowledgeStore._assert_transaction_read_pins(cursor,critical)
    # Candidate context must still have no newly approved/business consumer.
    from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS
    for item in old['items']:
        if item['kind']!='unchanged_unapproved_internal_relation_context':continue
        key=tuple(item['record_key'].split('/',1));row=actual[key];oid=key[1]
        if row['payload'].get('review_status')!='candidate' or row['retired_at']:
            raise ValueError('closed candidate context generation changed')
        edge=cursor.execute('SELECT payload,revision,retired_at IS NOT NULL,review_status FROM wang_knowledge.edges WHERE edge_collection=%s AND edge_id=%s',key).fetchone()
        if edge!=(row['payload'],row['revision'],False,'candidate'):
            raise ValueError('closed candidate context indexed edge drift')
        refs=PostgresKnowledgeStore._reference_consumers(cursor,[oid],sorted(KNOWLEDGE_COLLECTIONS))
        if (any((c,i)!=key and oid in PostgresKnowledgeStore._record_reference_ids(p,exclude_provenance=True) for c,i,p in refs)
                or cursor.execute('SELECT 1 FROM wang_knowledge.review_events WHERE collection=%s AND object_id=%s LIMIT 1',key).fetchone()
                or cursor.execute('SELECT 1 FROM wang_knowledge.edges WHERE (from_id=%s OR to_id=%s) AND retired_at IS NULL LIMIT 1',(oid,oid)).fetchone()):
            raise ValueError('closed candidate context acquired a business consumer/approval')
    return {'source_handoff':handoff,'pending_record_keys':list(old['pending_record_keys']),
            'items':old['items'],'read_pins':pin_rows(critical),'bindings':old['bindings'],
            'prior_completed_resolution':prior,'audited_claim_transitions':[],
            'model_verdicts_changed':False,'original_pending_list_preserved':True}


def closed_statement_saved_resolution(store,value,audit_path):
    """Reuse the existing generic disjoint-rejection verifier, not a new call."""
    from backend.pipeline.cvp_sql_flow import binding
    from backend.pipeline.claim_source_write_slice import calculate_saved_impact_decisions,verify_saved_statement_impact_resolution
    facts=completed_source_facts(value,store=store)
    if not facts or facts['source_handoff'].get('schema_version')!=CLOSED_STATEMENT:return None
    source,receipt,root=statement_slice_context(store,value)
    decisions=calculate_saved_impact_decisions(store,source,Path(audit_path))
    if decisions['unadjudicated_targets']:return None
    result={'schema_version':'wang_source_slice_saved_impact_resolution_v1','audit':binding(audit_path),
        'source_package':source['reviewed']['evidence']['package']['payload'],'decisions':decisions,
        'bindings':[binding(audit_path),*[b for part in decisions['parts'] for k,b in part.items() if k.startswith('prior_')]],
        'new_model_calls':0,'registry_writes':0,'model_verdicts_changed':False}
    result['artifact_sha256']=sha256_json(result)
    verify_saved_statement_impact_resolution(read_artifact(audit_path),result)
    return result


def statement_slice_context(store,value):
    """Recover the named committed slice after later sequential actions.

    The SQL Claim-layer receipt names its original action. This is a bounded
    lookup over the group's explicit commits, not a second action or ancestry.
    """
    from copy import deepcopy
    from backend.pipeline.cvp_sql_flow import check_binding
    from backend.pipeline.claim_source_write_slice import validate_package
    marker=value.get('claim_layer_audit',{});proof=marker.get('receipt')
    if not proof or marker.get('status')!='claim_layer_audited_cvp_pending':
        raise ValueError('statement impact closure lacks actual SQL Claim-layer receipt')
    check_binding(proof);receipt=read_artifact(proof['path'])
    original=receipt.get('change_set_ids',[]);current=value['change_set_ids']
    if (not original or len(original)!=len(set(original)) or current[:len(original)]!=original
            or receipt.get('claim_ids')!=value['claim_ids'] or receipt.get('group_complete') is not False
            or marker.get('cvp_management_complete') is not False):
        raise ValueError('statement impact closure original commit/population drift')
    root=Path(proof['path']).parent.parent;matches=[]
    for cid in original:
        history=store.get_change_history(cid)
        if not history or history['status']!='applied' or history['metadata'].get('run_id')!=value['run_id']:
            raise ValueError('statement impact original commit missing/foreign')
        review=applied_review(store,value,history['metadata']['reviewed_sha256'])
        app=review['evidence']['package']['payload'].get('source_repair_application',{})
        if (app.get('source_write_slice',{}).get('mode')=='owner_statement_completion'
                and Path(app.get('run_root','')).resolve()==root.resolve()):
            matches.append((history,review))
    if len(matches)!=1:raise ValueError('statement impact has no unique actual committed source slice')
    history,review=matches[0];validate_package(review['evidence']['package']['payload'])
    pending=review['evidence'].get('pending_cvp_management_record_keys',[])
    if pending!=value['reviewed']['evidence'].get('pending_cvp_management_record_keys',[]):
        raise ValueError('sequential action changed original pending obligations')
    for cid in current[len(original):]:
        h=store.get_change_history(cid)
        if (not h or h['status']!='applied' or h['metadata'].get('run_id')!=value['run_id']
                or h['source_kind']!='wang_claim_relation_retirement_v1'
                or len(h['operations'])!=1 or h['operations'][0]['operation']!='retire'
                or 'knowledge_relations/'+h['operations'][0]['object_id'] not in pending):
            raise ValueError('unsupported sequential change after statement slice')
    # Read-only reconstruction; SQL metadata, old answers and budgets stay put.
    source=deepcopy(value);source['reviewed']=review
    source['reviewed_sha256']=history['metadata']['reviewed_sha256'];source['change_set_ids']=list(original)
    return source,receipt,root


def current_receipt_pins(store,old_pins,commits):
    """Only exact human withdrawal of unused candidate context may bridge pins."""
    from backend.pipeline.cvp_sql_flow import decoded_pins,pin_rows
    result=decoded_pins(old_pins)
    for item in commits['items']:
        if item['kind']!='exact_human_logical_retirement':continue
        key=tuple(item['record_key'].split('/',1));old=result.get(key)
        if old==tuple(item['pin']):continue
        h=store.get_change_history(item['change_set_id']);op=h['operations'][0]
        p=op['before']
        if (old!=(op['before_revision'],op['before_sha256'],False)
                or p.get('review_status')!='candidate' or p.get('visibility')!='internal'
                or p.get('reason')!='' or key[0]!='knowledge_relations'
                or record_content_sha(p)!=op['before_sha256']
                or op['before_sha256']!=op['after_sha256']):
            raise ValueError('receipt transition is not unchanged unapproved candidate withdrawal')
        with store.connect() as conn,conn.cursor() as cur:
            from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS
            refs=store._reference_consumers(cur,[key[1]],sorted(KNOWLEDGE_COLLECTIONS))
            if any((c,i)!=key and key[1] in store._record_reference_ids(v,exclude_provenance=True) for c,i,v in refs):
                raise ValueError('withdrawn candidate has an unaudited business consumer')
            if cur.execute('SELECT 1 FROM wang_knowledge.edges WHERE (from_id=%s OR to_id=%s) AND retired_at IS NULL LIMIT 1',
                           (key[1],key[1])).fetchone():
                raise ValueError('withdrawn candidate has an unaudited edge consumer')
            if cur.execute('SELECT 1 FROM wang_knowledge.review_events WHERE collection=%s AND object_id=%s LIMIT 1',key).fetchone():
                raise ValueError('withdrawn relation was independently reviewed, not unused candidate context')
        result[key]=tuple(item['pin'])
    with store.connect() as conn,conn.cursor() as cur:
        store._assert_transaction_read_pins(cur,result)
    return pin_rows(result)


def build_statement_impact_resolution(store,value,output):
    """Close an actually audited statement slice; never delete candidate context."""
    from backend.pipeline.cvp_sql_flow import binding,check_binding,decoded_pins,validate_transaction_audit,pins,pin_rows
    from backend.pipeline import claim_source_write_slice as slice_flow
    from backend.pipeline.claim_first_management import validate_claim_audit_report
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
    from backend.pipeline.viewpoint_cvp_campaign_runner import validate_audit
    from backend.pipeline.cvp_link_target_selection import _approved
    source,claim_receipt,root=statement_slice_context(store,value)
    commits=verify_pending_commits(store,value)
    check_binding(claim_receipt['audit']);validate_claim_audit_report(read_artifact(claim_receipt['audit']['path']),value['claim_ids'])
    for b in claim_receipt['source_proofs']:check_binding(b)
    source_pins=current_receipt_pins(store,claim_receipt['read_pins'],commits)
    scope=slice_flow.impact_audit_scope(store,source)
    candidates=[]
    for path in root.glob('cvp-impact-audit-*/impact-audit-receipt.json'):
        r=read_artifact(path)
        if (r.get('claim_ids')==scope['claim_ids'] and r.get('viewpoint_ids')==scope['viewpoint_ids']
                and r.get('normal_review_archives')==scope['normal_review_archives']
                and r.get('pending_record_keys')==scope['pending_record_keys']):candidates.append((path,r))
    if len(candidates)!=1:raise ValueError('statement impact full audited population missing/ambiguous')
    path,impact=candidates[0]
    if (impact.get('group_complete') is not False or impact.get('model_verdicts_changed') is not False
            or impact.get('cvp_record_keys')!=scope['cvp_record_keys']
            or impact.get('source_record_keys')!=scope['source_record_keys']):
        raise ValueError('statement impact receipt domain drift')
    if current_receipt_pins(store,impact['read_pins'],commits)!=scope['read_pins']:
        raise ValueError('statement impact audit producer scope changed beyond exact candidate withdrawal')
    audit_binding=impact['audit'];check_binding(audit_binding);audit=read_artifact(audit_binding['path'])
    validate_transaction_audit(audit,scope['claim_ids'],scope['viewpoint_ids'])
    proofs=verify_saved_full_audit(Path(audit_binding['path']),layers=('3','4'))
    independent=decoded_pins(audit['meta']['registry_read_scope']['read_pins'])
    with store.connect() as conn,conn.cursor() as cur:store._assert_transaction_read_pins(cur,independent)
    decisions=slice_flow.calculate_saved_impact_decisions(store,source,Path(audit_binding['path']))
    saved_decisions=read_artifact(path.parent/'saved-impact-decisions.json')
    if saved_decisions!={**decisions,'impact_audit_receipt':binding(path)}:
        raise ValueError('statement impact saved decision receipt changed')
    package=source['reviewed']['evidence']['package']['payload']
    semantic={'schema_version':'wang_source_slice_saved_impact_resolution_v1','audit':audit_binding,
        'source_package':package,'decisions':decisions,'bindings':[audit_binding,*proofs,
            *[b for part in decisions['parts'] for k,b in part.items() if k.startswith('prior_')]],
        'new_model_calls':0,'registry_writes':0,'model_verdicts_changed':False}
    semantic={**semantic,'artifact_sha256':sha256_json(semantic)}
    slice_flow.verify_saved_statement_impact_resolution(audit,semantic)
    semantic_path=Path(output).parent/'statement-impact-semantic-resolution.json'
    write_immutable(semantic_path,semantic)
    validate_audit(audit,scope['claim_ids'],scope['viewpoint_ids'],owner_resolution_path=semantic_path)
    items=list(commits['items'])+list(commits['unapproved_context_items'])
    accounted={i['record_key'] for i in items};current_pins=decoded_pins(source_pins)
    current_pins.update(decoded_pins(scope['read_pins']));current_pins.update(independent)
    for key in scope['cvp_record_keys']:
        col,oid=key.split('/',1);row=store.get_record_state(col,oid)
        if not row or row['retired'] or record_content_sha(row['payload'])!=row['content_sha256']:
            raise ValueError('statement pending CVP current generation drift')
        _approved(row['payload']);pin=(row['revision'],row['content_sha256'],False)
        if col=='viewpoint_structure_revisions':
            # Historical approved editorial structure is not a new generation
            # and has no Claim-version basis to silently rebind.
            p=row['payload']
            if any(f.get('basis_claim_ids') for f in p.get('focal_viewpoints',[])):
                raise ValueError('historical structure has Claim basis requiring separate management')
            matches=[]
            for hinfo in scope['normal_review_archives']:
                review=applied_review(store,value,hinfo['reviewed_sha256'])
                for op in review['plan']['operations']:
                    if (op['collection'],op['object_id'])==(col,oid):matches.append(op)
            if (len(matches)!=1 or matches[0]['operation']!='create'
                    or matches[0]['after_revision']!=pin[0] or matches[0]['after_sha256']!=pin[1]):
                raise ValueError('historical structure lacks exact committed original independent approval')
            kind='historical_approved_structure_unchanged_no_claim_basis'
        else:
            if independent.get((col,oid))!=pin:
                raise ValueError('pending CVP generation not in complete actual independent audit')
            kind='approved_current_cvp_impact_independently_audited'
        items.append({'record_key':key,'kind':kind,'pin':list(pin)})
        accounted.add(key);current_pins[col,oid]=pin
    pending=value['reviewed']['evidence']['pending_cvp_management_record_keys']
    if accounted!=set(pending) or len(items)!=len(pending):
        raise ValueError('statement impact closure lost a pending obligation')
    if any(tuple(i['record_key'].split('/',1)) in independent for i in commits['unapproved_context_items']):
        raise ValueError('unapproved context was consumed as an independent semantic dependency')
    with store.connect() as conn,conn.cursor() as cur:store._assert_transaction_read_pins(cur,current_pins)
    body={'schema_version':SLICE_IMPACTS,'run_id':value['run_id'],'reviewed_sha256':value['reviewed_sha256'],
        'source_reviewed_sha256':source['reviewed_sha256'],'source_change_set_id':source['reviewed']['plan']['change_set_id'],
        'change_set_ids':value['change_set_ids'],'claim_layer_completion':value['claim_layer_audit']['receipt'],
        'impact_audit_receipt':binding(path),'audit':audit_binding,'semantic_resolution':binding(semantic_path),
        'claim_ids':scope['claim_ids'],'viewpoint_ids':scope['viewpoint_ids'],
        'pending_record_keys':pending,'items':sorted(items,key=lambda i:i['record_key']),
        'read_pins':pin_rows(current_pins),'partial_commits':commits,
        'bindings':[*proofs,*claim_receipt['source_proofs'],*commits['bindings']],
        'new_model_calls':0,'registry_writes':0,'model_verdicts_changed':False,
        'original_pending_list_preserved':True,'unapproved_candidate_semantics_resolved':False}
    write_immutable(output,{**body,'artifact_sha256':sha256_json(body)})
    return Path(output),semantic_path,audit,pin_rows(current_pins)


def validate_statement_impact_completion(value,path,audit_binding,cursor,*,semantic_resolution=None):
    """Recheck exact current/committed authority in the completion transaction."""
    from backend.pipeline.cvp_sql_flow import check_binding,decoded_pins,binding
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.claim_relation_retirement import validate_package as retirement_package
    from backend.pipeline.cvp_link_target_selection import _approved
    body=read_artifact(path)
    pending=value['reviewed']['evidence'].get('pending_cvp_management_record_keys',[])
    if (body.get('schema_version')!=SLICE_IMPACTS
            or body.get('artifact_sha256')!=sha256_json({k:v for k,v in body.items() if k!='artifact_sha256'})
            or body.get('run_id')!=value['run_id'] or body.get('reviewed_sha256')!=value['reviewed_sha256']
            or body.get('change_set_ids')!=value['change_set_ids'] or body.get('claim_ids')!=value['claim_ids']
            or body.get('pending_record_keys')!=pending or body.get('audit')!=audit_binding
            or body.get('claim_layer_completion')!=value.get('claim_layer_audit',{}).get('receipt')
            or body.get('semantic_resolution')!=(binding(semantic_resolution) if semantic_resolution else None)
            or body.get('new_model_calls')!=0 or body.get('registry_writes')!=0
            or body.get('model_verdicts_changed') is not False
            or body.get('original_pending_list_preserved') is not True
            or body.get('unapproved_candidate_semantics_resolved') is not False):
        raise ValueError('statement impact completion proof missing/foreign/changed authority')
    for b in [body['claim_layer_completion'],body['impact_audit_receipt'],body['audit'],
              body['semantic_resolution'],*body['bindings']]:check_binding(b)
    source=applied_review(None,value,body['source_reviewed_sha256'],cursor=cursor)
    if source['plan']['change_set_id']!=body['source_change_set_id']:
        raise ValueError('statement impact source commit identity drift')
    package=source['evidence']['package']['payload'];app=package.get('source_repair_application',{})
    direction=app.get('source_write_slice',{})
    claim_receipt=read_artifact(body['claim_layer_completion']['path'])
    semantic=read_artifact(body['semantic_resolution']['path'])
    impact=read_artifact(body['impact_audit_receipt']['path'])
    if (direction.get('mode')!='owner_statement_completion'
            or direction.get('run_id')!=value['run_id'] or semantic['source_package']!=package
            or body['claim_layer_completion']['path']!=str(Path(app['run_root'])/'claim-first-audit/claim-layer-completion.json')
            or claim_receipt['claim_ids']!=value['claim_ids']
            or value['change_set_ids'][:len(claim_receipt['change_set_ids'])]!=claim_receipt['change_set_ids']
            or source['evidence'].get('pending_cvp_management_record_keys')!=pending
            or impact['pending_record_keys']!=pending or impact['claim_ids']!=value['claim_ids']
            or impact['viewpoint_ids']!=body['viewpoint_ids'] or impact['audit']!=audit_binding):
        raise ValueError('statement impact source/Claim/audit lineage drift')
    required=source['evidence'].get('pending_cvp_claim_ids',[])
    if (not required or required!=value['reviewed']['evidence'].get('pending_cvp_claim_ids',[])
            or not set(required)<=set(body['claim_ids'])):
        raise ValueError('statement impact did not cover original pending Claim population')
    pins=decoded_pins(body['read_pins'])
    if len(pins)!=len(body['read_pins']):raise ValueError('statement impact duplicate current pins')
    actual={k:{**r,'retired':r.get('retired_at') is not None}
            for k,r in PostgresKnowledgeStore._assert_transaction_read_pins(cursor,pins).items()}
    items=body['items']
    if (len(items)!=len(pending) or {i['record_key'] for i in items}!=set(pending)):
        raise ValueError('statement impact incomplete pending population')
    audit=read_artifact(body['audit']['path'])
    independent=decoded_pins(audit['meta']['registry_read_scope']['read_pins'])
    for i in items:
        key=tuple(i['record_key'].split('/',1));row=actual.get(key)
        if not row or tuple(i['pin'])!=pins.get(key):raise ValueError('statement impact item pin drift')
        kind=i['kind']
        if kind=='unchanged_unapproved_internal_relation_context':
            if key in independent:raise ValueError('candidate context became audited semantic dependency')
            history_row=cursor.execute('''SELECT c.change_set_id,c.status,c.source_kind,o.operation,
                o.before_revision,o.before_sha256,o.after_revision,o.after_sha256,v.payload
                FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c ON c.change_set_id=v.change_set_id
                JOIN wang_knowledge.change_operations o ON o.change_set_id=v.change_set_id AND o.collection=v.collection
                AND o.object_id=v.object_id AND o.after_revision=v.revision
                WHERE v.collection=%s AND v.object_id=%s AND v.revision=%s''',(*key,row['revision'])).fetchall()
            if len(history_row)!=1:raise ValueError('candidate context creation missing/ambiguous at completion')
            h=history_row[0]
            history={'change_set_id':h[0],'status':h[1],'source_kind':h[2],'operations':[{
                'collection':key[0],'object_id':key[1],'operation':h[3],'before_revision':h[4],
                'before':None if h[4] is None and h[5] is None else {},'after_revision':h[6],
                'after_sha256':h[7],'after':h[8]}]}
            edge=cursor.execute('SELECT payload,review_status,revision,retired_at IS NOT NULL FROM wang_knowledge.edges WHERE edge_collection=%s AND edge_id=%s',key).fetchone()
            from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS
            refs=PostgresKnowledgeStore._reference_consumers(cursor,[key[1]],sorted(KNOWLEDGE_COLLECTIONS))
            refs=[(c,j) for c,j,p in refs if (c,j)!=key and key[1] in PostgresKnowledgeStore._record_reference_ids(p,exclude_provenance=True)]
            refs+=cursor.execute('SELECT edge_collection,edge_id FROM wang_knowledge.edges WHERE (from_id=%s OR to_id=%s) AND retired_at IS NULL',(key[1],key[1])).fetchall()
            reviews=cursor.execute('SELECT review_event_id FROM wang_knowledge.review_events WHERE collection=%s AND object_id=%s',key).fetchall()
            expected=candidate_relation_context(i['record_key'],row,history,references=refs,reviews=reviews,
                edge={'payload':edge[0],'review_status':edge[1],'revision':edge[2],'retired':edge[3]} if edge else None)
            if i!=expected:raise ValueError('candidate context completion classification drift')
        elif kind=='exact_human_logical_retirement':
            reviewed=applied_review(None,value,i['reviewed_sha256'],cursor=cursor)
            if (reviewed['plan']['change_set_id']!=i['change_set_id']
                    or retirement_package(reviewed['evidence']['package']['payload'])!=(key,)
                    or not row['retired']):
                raise ValueError('statement impact retirement lost exact committed owner authority')
        elif kind=='independently_committed_source_evidence':
            if (key[0]!='evidence_steps' or row['retired'] or row['payload'].get('review_status')!='independent_terminal_reviewed'
                    or row['payload'].get('support_eligibility') not in {'eligible','eligible_with_label'}):
                raise ValueError('statement impact Evidence lost actual reviewed generation')
            creator=cursor.execute('SELECT metadata FROM wang_knowledge.change_sets WHERE change_set_id=%s AND status=\'applied\'',(i['change_set_id'],)).fetchone()
            if not creator or creator[0].get('reviewed_sha256')!=i['reviewed_sha256']:
                raise ValueError('statement impact Evidence committed approval drift')
            current=cursor.execute('SELECT change_set_id FROM wang_knowledge.object_versions WHERE collection=%s AND object_id=%s AND revision=%s',(*key,row['revision'])).fetchone()
            if current!=(i['change_set_id'],):raise ValueError('statement Evidence generation not from actual reviewed commit')
        elif kind=='approved_current_cvp_impact_independently_audited':
            if key[0] not in {'canonical_viewpoints','viewpoint_revisions','viewpoint_claim_links'} or independent.get(key)!=pins[key] or row['retired']:
                raise ValueError('statement CVP impact missing from exact full independent audit')
            _approved(row['payload'])
        elif kind=='historical_approved_structure_unchanged_no_claim_basis':
            if (key[0]!='viewpoint_structure_revisions' or row['retired']
                    or any(f.get('basis_claim_ids') for f in row['payload'].get('focal_viewpoints',[]))):
                raise ValueError('statement historical structure acquired unaudited Claim basis')
            _approved(row['payload'])
            match=cursor.execute('''SELECT c.metadata,o.operation,o.after_revision,o.after_sha256
                FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c ON c.change_set_id=v.change_set_id
                JOIN wang_knowledge.change_operations o ON o.change_set_id=v.change_set_id AND o.collection=v.collection
                AND o.object_id=v.object_id AND o.after_revision=v.revision WHERE v.collection=%s AND v.object_id=%s AND v.revision=%s
                AND c.status='applied' AND c.source_kind='cvp_batch_resolution' ''',(*key,row['revision'])).fetchall()
            if len(match)!=1 or match[0][1:]!=('create',row['revision'],row['content_sha256']) or match[0][0].get('run_id')!=value['run_id']:
                raise ValueError('historical structure original committed approval missing')
            applied_review(None,value,match[0][0]['reviewed_sha256'],cursor=cursor)
        else:raise ValueError('statement impact unknown closure kind')
    return body


def candidate_relation_context(key, row, history, *, references, reviews, edge):
    """Classify unchanged unapproved context, never approve or retire it.

    A candidate's unresolved semantic judgment remains unresolved. This proof
    only establishes that it is not an approved business dependency; it is not
    a replacement for source/CVP completion or a license to clear pending.
    """
    col,oid=key.split('/',1)
    p=row.get('payload',{}) if row else {}
    required={'schema_version','relation_id','from_id','to_id','relation_type',
              'reason','review_status','visibility','revision'}
    if (col!='knowledge_relations' or not row or set(p)!=required
            or p.get('relation_id')!=oid or row['retired'] or row['revision']!=1
            or p.get('revision')!=1 or p.get('review_status')!='candidate'
            or p.get('visibility')!='internal' or p.get('reason')!=''
            or p.get('relation_type')!='supports' or not p.get('from_id') or not p.get('to_id')
            or record_content_sha(p)!=row['content_sha256']):
        raise ValueError('candidate context requires exact original internal unapproved relation')
    if (references or reviews or edge is None or edge['payload']!=p
            or edge['review_status']!='candidate' or edge['retired']
            or edge['revision']!=row['revision']):
        raise ValueError('candidate context has a consumer, approval or inconsistent indexed edge')
    ops=[o for o in history['operations'] if (o['collection'],o['object_id'])==(col,oid)]
    if len(ops)!=1:raise ValueError('candidate context creation missing/ambiguous')
    op=ops[0]
    if (history['status']!='applied' or history['source_kind']!='knowledge_package'
            or op['operation']!='create' or op['before'] is not None
            or op['before_revision'] is not None or op['after_revision']!=1
            or op['after']!=p or op['after_sha256']!=row['content_sha256']):
        raise ValueError('candidate context differs from actual committed original creation')
    return {'record_key':key,'kind':'unchanged_unapproved_internal_relation_context',
        'pin':[row['revision'],row['content_sha256'],False],
        'creation_change_set_id':history['change_set_id'],
        'semantic_judgment_resolved':False,'approved_dependency':False,
        'content_write_authority':False,'pending_obligation_preserved':True}


def read_candidate_relation_context(store,key,row):
    """Indexed exact creation and consumers; no inventory/ancestor scan."""
    from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS
    col,oid=key.split('/',1)
    with store.connect() as conn,conn.cursor() as cur:
        version=cur.execute('SELECT change_set_id FROM wang_knowledge.object_versions WHERE collection=%s AND object_id=%s AND revision=%s',
                            (col,oid,row['revision'])).fetchone()
        refs=cur.execute('SELECT edge_collection,edge_id FROM wang_knowledge.edges WHERE (from_id=%s OR to_id=%s) AND retired_at IS NULL',
                         (oid,oid)).fetchall()
        # Edge endpoints alone miss IDs inside supporting_relation_ids and
        # similar business fields. Use the store's exact GIN-indexed reader,
        # then its business-reference extractor (not prose substring matching).
        for c,i,p in store._reference_consumers(cur,[oid],sorted(KNOWLEDGE_COLLECTIONS)):
            if (c,i)!=(col,oid) and oid in store._record_reference_ids(p,exclude_provenance=True):
                refs.append((c,i))
        reviews=cur.execute('SELECT review_event_id FROM wang_knowledge.review_events WHERE collection=%s AND object_id=%s',
                            (col,oid)).fetchall()
        edge=cur.execute('SELECT payload,review_status,revision,retired_at IS NOT NULL FROM wang_knowledge.edges WHERE edge_collection=%s AND edge_id=%s',
                         (col,oid)).fetchone()
    if not version:raise ValueError('candidate context original creation missing')
    return candidate_relation_context(key,row,store.get_change_history(version[0]),references=refs,reviews=reviews,
        edge={'payload':edge[0],'review_status':edge[1],'revision':edge[2],'retired':edge[3]} if edge else None)


def pending_commit_item(key, row, history, review):
    """Exact committed generation; this is not whole-group resolution."""
    col,oid=key.split('/',1)
    if (not row or record_content_sha(row['payload'])!=row['content_sha256']
            or row['payload'].get('review_status') in {'human_hold','human_review_required','rejected','needs_review'}
            or row['payload'].get('effective_state')=='held'):
        raise ValueError('pending committed item missing/SHA/hold')
    plan=review['plan']
    identity=lambda o:(o['collection'],o['object_id'],o['operation'],o['before_revision'],o['after_revision'],o['before_sha256'],o['after_sha256'])
    planned=[identity(o) for o in plan['operations']]
    actual=[identity(o) for o in history['operations']]
    if (history['status']!='applied' or history['change_set_id']!=plan['change_set_id']
            or history['fingerprint_sha256']!=plan['fingerprint_sha256']
            or history['source_sha256']!=plan['source_sha256']
            or history['metadata']['reviewed_sha256']!=sha256_json(review)
            or history['metadata']['approval']!=review['evidence']['approval']
            or len(planned)!=len(set(planned)) or sorted(actual)!=sorted(planned)):
        raise ValueError('pending item actual complete committed write set drift')
    matches=[o for o in history['operations'] if (o['collection'],o['object_id'])==(col,oid)]
    if len(matches)!=1:raise ValueError('pending item missing/ambiguous operation')
    op=matches[0]
    if (op['after_revision']!=row['revision'] or op['after_sha256']!=row['content_sha256']
            or not op['after'] or record_content_sha(op['after'])!=row['content_sha256']):
        raise ValueError('pending item current generation differs from committed afterimage')
    if col=='knowledge_relations':
        from backend.pipeline.claim_relation_retirement import SCHEMA
        if (history['source_kind']!=SCHEMA or op['operation']!='retire' or not row['retired']
                or len(actual)!=1 or row['payload']!=op['before']
                or {k:v for k,v in op['before'].items() if k!='revision'}!={k:v for k,v in op['after'].items() if k!='revision'}):
            raise ValueError('pending relation is not exact logical retirement')
        kind='exact_human_logical_retirement'
    elif col=='evidence_steps':
        if (op['operation']!='update' or row['retired'] or row['payload']!=op['after']
                or row['payload'].get('review_status')!='independent_terminal_reviewed'
                or row['payload'].get('support_eligibility') not in {'eligible','eligible_with_label'}):
            raise ValueError('pending Evidence lacks current independent eligible postimage')
        kind='independently_committed_source_evidence'
    else:raise ValueError('pending committed item outside source domain')
    return {'record_key':key,'kind':kind,'pin':[row['revision'],row['content_sha256'],row['retired']],
            'change_set_id':history['change_set_id'],'reviewed_sha256':sha256_json(review)}


def verify_pending_commits(store, value):
    """Saved-only partial accounting. Unknown/unapproved items stay pending.

    One indexed current-generation lookup, one named committed review per
    eligible item. No ancestor search, models, business writes or audit waiver.
    """
    from backend.pipeline.cvp_sql_flow import check_binding,pins,pin_rows,decoded_pins
    pending=value['reviewed']['evidence'].get('pending_cvp_management_record_keys',[])
    if not pending or len(pending)!=len(set(pending)):
        raise ValueError('pending commit accounting missing/duplicate population')
    items=[];current={};bindings=[];candidate_context=[]
    for key in pending:
        col,oid=key.split('/',1)
        row=store.get_record_state(col,oid)
        if not row or record_content_sha(row['payload'])!=row['content_sha256']:
            raise ValueError('pending commit accounting missing/current SHA drift')
        current[col,oid]=row
        if row['payload'].get('review_status')=='human_hold' or row['payload'].get('effective_state')=='held':
            raise ValueError('pending commit accounting cannot clear hold')
        if (col=='knowledge_relations' and not row['retired']
                and row['payload'].get('review_status')=='candidate'):
            candidate_context.append(read_candidate_relation_context(store,key,row))
        eligible=(col=='knowledge_relations' and row['retired']) or (
            col=='evidence_steps' and not row['retired'] and row['payload'].get('review_status')=='independent_terminal_reviewed'
            and row['payload'].get('support_eligibility') in {'eligible','eligible_with_label'})
        if not eligible:continue
        with store.connect() as conn,conn.cursor() as cur:
            found=cur.execute('SELECT change_set_id FROM wang_knowledge.object_versions WHERE collection=%s AND object_id=%s AND revision=%s',
                (col,oid,row['revision'])).fetchone()
        if not found:raise ValueError('pending current generation has no committed history')
        history=store.get_change_history(found[0]);parent=store.get_cvp_group(history['metadata']['run_id'])
        if not parent or parent['campaign_sha256']!=value['campaign_sha256']:
            raise ValueError('pending item foreign campaign commit')
        if col=='knowledge_relations' and parent['run_id']!=value['run_id']:
            raise ValueError('pending retirement belongs to another group')
        review=applied_review(store,parent,history['metadata']['reviewed_sha256'])
        item=pending_commit_item(key,row,history,review)
        package=review['evidence']['package']['payload']
        if col=='knowledge_relations':
            from backend.pipeline.claim_relation_retirement import validate_package
            if validate_package(package)!=((col,oid),):raise ValueError('pending retirement authority target drift')
            own=[review['evidence']['approval']['human_exact_disposition']]
        else:
            from backend.pipeline.cvp_sql_actions import validate_source_package
            validate_source_package(package)
            app=package['source_repair_application']
            if app['run_id']!=parent['run_id']:raise ValueError('pending source package foreign run')
            own=[app['materials'],review['evidence']['approval']['terminal_review']]
        for b in own:check_binding(b)
        item['bindings']=own;bindings.extend(own);items.append(item)
    read_pins=pin_rows(pins(current))
    with store.connect() as conn,conn.cursor() as cur:
        store._assert_transaction_read_pins(cur,decoded_pins(read_pins))
    verified={i['record_key'] for i in items}
    return {'schema_version':'wang_partial_source_pending_commits_v1','run_id':value['run_id'],
        'reviewed_sha256':value['reviewed_sha256'],'pending_record_keys':list(pending),'items':items,
        'remaining_record_keys':[k for k in pending if k not in verified],'read_pins':read_pins,
        'bindings':bindings,'new_model_calls':0,'registry_writes':0,'group_complete':False,
        'unapproved_context_items':candidate_context,
        'content_write_authority':False,'model_verdicts_changed':False,'original_pending_list_preserved':True}


def audit_record_keys(pending, facts):
    """Source impacts have their own exact proof, not CVP audit root IDs."""
    if facts is None:return list(pending)
    from backend.pipeline.cvp_link_representation import GENERATION_HANDOFF
    generation=facts.get('source_handoff',{}).get('schema_version')==GENERATION_HANDOFF
    closed=facts.get('source_handoff',{}).get('schema_version')==CLOSED_STATEMENT
    item_keys=[x['record_key'] for x in facts['items']]
    if (list(pending)!=facts['pending_record_keys'] or len(item_keys)!=len(pending)
            or (set(item_keys)!=set(pending) if closed else item_keys!=list(pending))):
        raise ValueError('source audit root classification lost pending obligations')
    if generation:
        allowed={'approved_current_cvp_impact_independently_audited','unchanged_unapproved_unconsumed_policy_relation_context'}
        if any(i['kind'] not in allowed for i in facts['items']):raise ValueError('joint generation unknown pending domain')
        return [i['record_key'] for i in facts['items'] if i['kind']=='approved_current_cvp_impact_independently_audited']
    if closed:
        allowed={'approved_current_cvp_impact_independently_audited','exact_human_logical_retirement','historical_approved_structure_unchanged_no_claim_basis','independently_committed_source_evidence','unchanged_unapproved_internal_relation_context'}
        if any(i['kind'] not in allowed for i in facts['items']):raise ValueError('closed statement unknown pending domain')
        return [i['record_key'] for i in facts['items'] if i['kind']=='approved_current_cvp_impact_independently_audited']
    if any(x['kind'] not in {'declared_source_group_not_cvp','independently_retained_source_relation','exact_human_logical_retirement','unchanged_unapproved_unconsumed_question_context','unchanged_unapproved_unconsumed_policy_relation_context','unchanged_independently_approved_readonly_claim_context'} for x in facts['items']):
        raise ValueError('source audit root classification contains unknown domain')
    return []


def validate_pre_model_startup_recovery(audit_dir, expected, *, corrected_record_keys=()):
    """Consume a sealed constructor failure, never an unknown model intent."""
    from backend.pipeline.cvp_sql_flow import binding,check_binding
    receipt=read_artifact(audit_dir/'source-domain-startup-recovery.json')
    if (receipt.get('schema_version')!='wang_source_audit_constructor_recovery_v1'
            or receipt.get('artifact_sha256')!=sha256_json({k:v for k,v in receipt.items() if k!='artifact_sha256'})
            or receipt.get('intent')!=binding(audit_dir/'audit-intent.json')
            or read_artifact(audit_dir/'audit-intent.json')!=expected
            or receipt.get('vendor_model_constructed') is not False
            or receipt.get('original_record_keys')==[] or receipt.get('corrected_record_keys')!=list(corrected_record_keys)
            or receipt.get('auditor')!=binding(Path(__file__).resolve().parents[2]/'scripts/audit-library.py')
            or {p.name for p in audit_dir.iterdir()}!={'audit-intent.json','source-domain-startup-recovery.json'}):
        raise ValueError('source audit startup recovery proof missing/drift/possible model execution')
    check_binding(receipt['failure_log'])
    log=Path(receipt['failure_log']['path']).read_text()
    if ('raise ValueError(\'independent SQL pending record is outside CVP management\')' not in log
            or 'File ' not in log or 'in __init__' not in log
            or any(x not in log for x in receipt['original_record_keys'])):
        raise ValueError('source audit recovery is not the exact pre-model constructor failure')
    return receipt


def context(value,store=None,*,cursor=None):
    from backend.pipeline.cvp_link_representation import generation_handoff
    generation=generation_handoff(value)
    if generation:return generation
    e=value.get('reviewed',{}).get('evidence',{})
    c=e.get('action',{}).get('materials',{}).get('claim_management_resolution',{})
    direct=c if (c.get('schema_version')==HANDOFF or
        (c.get('schema_version')=='wang_post_claim_cvp_reproposal_v1'
         and c.get('source_slice_mode') in {'policy_evidence_binding','policy_claim_source_revision'})) else None
    if direct is not None:return direct
    closed=closed_statement_context(value)
    if closed:return closed
    if e.get('action',{}).get('kind')!='audit_supports_correction':return None
    if cursor is None:
        if store is None:return None
        with store.connect() as conn,conn.cursor() as cur:return context(value,cursor=cur)
    # Only the immediately preceding explicit commit. No recursive history
    # search, inferred approval, changed pending population or new call budget.
    ids=value.get('change_set_ids',[])
    if len(ids)<2:raise ValueError('source handoff correction lacks preceding commit')
    current=applied_review(None,value,value['reviewed_sha256'],cursor=cursor)
    if current!=value['reviewed'] or current['plan']['change_set_id']!=ids[-1]:
        raise ValueError('source handoff correction is not the actual current commit')
    ops=current['plan']['operations']
    if not ops or any(op['collection'] not in {'viewpoint_claim_links','viewpoint_identity_candidates','viewpoint_identity_decisions'}
        or op['operation']!=('update' if op['collection']=='viewpoint_claim_links' else 'create') for op in ops):
        raise ValueError('source handoff correction exceeds link-only domain')
    targets=e['action'].get('materials',{}).get('packet',{}).get('targets',[])
    links={op['object_id']:op['payload'] for op in ops if op['collection']=='viewpoint_claim_links'}
    if (not targets or len(links)!=len(targets) or set(e.get('approval',{}))!={'gpt','opus'}
            or any(t.get('before_link',{}).get('viewpoint_claim_link_id') not in links for t in targets)):
        raise ValueError('source handoff correction lacks exact dual-reviewed targets')
    for target in targets:
        old=target['before_link'];new=links[old['viewpoint_claim_link_id']]
        if (old.get('link_type')!='equivalent_component' or new.get('link_type')!='supports'
                or any(new.get(k)!=old.get(k) for k in ('claim_id','viewpoint_id',
                    'pinned_claim_revision','validated_against_viewpoint_revision_id','effective_state'))):
            raise ValueError('source handoff correction changed protected link semantics')
    row=cursor.execute('SELECT metadata FROM wang_knowledge.change_sets WHERE change_set_id=%s',(ids[-2],)).fetchone()
    if not row or row[0].get('run_id')!=value['run_id'] or not row[0].get('reviewed_sha256'):
        raise ValueError('source handoff preceding commit missing/foreign')
    prior=applied_review(None,value,row[0]['reviewed_sha256'],cursor=cursor)
    if prior['plan']['change_set_id']!=ids[-2]:raise ValueError('source handoff preceding plan drift')
    pe=prior['evidence'];handoff=context({'reviewed':prior})
    if (pe.get('pending_cvp_management_record_keys')!=e.get('pending_cvp_management_record_keys')
            or pe.get('pending_cvp_claim_ids') or e.get('pending_cvp_claim_ids')
            or pe.get('viewpoint_ids')!=e.get('viewpoint_ids')):
        raise ValueError('source handoff correction lost original obligations/population')
    if handoff is None:
        # An exact immediately committed ordinary CVP generation has no source
        # handoff to recover. Do not misclassify a later link-only correction as
        # source management; an action or pending source obligation still fails.
        if 'action' not in pe and not pe.get('pending_cvp_management_record_keys'):
            return None
        raise ValueError('source handoff correction lost original obligations/population')
    return handoff


def policy_relation_context_item(key,row,creation,*,references,reviews,edge):
    """Original internal extraction only; the unresolved relation is NOT approved."""
    from backend.api.canonical_repository.knowledge_models import ClaimRelationRecord,KnowledgeRelationRecord
    col,oid=key.split('/',1);p=row['payload']
    if col not in {'claim_relations','knowledge_relations'}:
        raise ValueError('policy relation context foreign domain')
    id_field='claim_relation_id' if col=='claim_relations' else 'relation_id'
    (ClaimRelationRecord if col=='claim_relations' else KnowledgeRelationRecord).model_validate(p)
    allowed={'schema_version',id_field,'from_id','to_id','relation_type','reason','review_status','visibility','revision'}
    if (set(p)!=allowed or p.get(id_field)!=oid or row['retired'] or row['revision']!=1
            or p.get('revision')!=1 or p.get('schema_version')!=1
            or p.get('review_status')!='candidate' or p.get('visibility')!='internal'
            or record_content_sha(p)!=row['content_sha256'] or references or reviews
            or not edge or edge!= {'payload':p,'review_status':'candidate','revision':1,'retired':False}):
        raise ValueError('policy relation context changed/approved/held/consumed')
    if (creation.get('source_kind')!='knowledge_package' or creation.get('status')!='applied'
            or creation.get('operation')!='create' or creation.get('before_revision') is not None
            or creation.get('after_revision')!=1 or creation.get('after')!=p
            or creation.get('after_sha256')!=row['content_sha256']):
        raise ValueError('policy relation lacks exact original committed extraction')
    return {'record_key':key,'kind':'unchanged_unapproved_unconsumed_policy_relation_context',
        'pin':[1,row['content_sha256'],False],'creation_change_set_id':creation['change_set_id'],
        'semantic_judgment_resolved':False,'approved_dependency':False,
        'content_write_authority':False,'pending_obligation_preserved':True}


def read_policy_slice_item(cursor,key):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS
    col,oid=key.split('/',1)
    st=cursor.execute('SELECT payload,revision,content_sha256,retired_at IS NOT NULL FROM wang_knowledge.objects WHERE collection=%s AND object_id=%s',(col,oid)).fetchone()
    if not st:raise ValueError('policy pending current row missing')
    row=dict(zip(('payload','revision','content_sha256','retired'),st))
    if col=='claims':
        from backend.api.canonical_repository.knowledge_models import ClaimRecord
        p=row['payload']
        ClaimRecord.model_validate(p)
        reviews=cursor.execute('SELECT reviewer_kind,decision FROM wang_knowledge.review_events WHERE collection=%s AND object_id=%s AND object_revision=%s',(col,oid,row['revision'])).fetchall()
        if (row['retired'] or p.get('claim_id')!=oid or p.get('review_status')!='ai_consensus_reviewed'
                or p.get('revision')!=row['revision'] or reviews!=[('ai','ai_consensus_reviewed')]
                or p.get('visibility')!='internal' or record_content_sha(p)!=row['content_sha256']
                ):
            raise ValueError('policy pending Claim lacks exact current independent source approval')
        return {'record_key':key,'kind':'unchanged_independently_approved_readonly_claim_context',
            'pin':[row['revision'],row['content_sha256'],False],
            'content_write_authority':False,'pending_obligation_preserved':True,
            'original_annotation_resolved':False}
    refs=[(c,i) for c,i,p in PostgresKnowledgeStore._reference_consumers(cursor,[oid],sorted(KNOWLEDGE_COLLECTIONS))
        if (c,i)!=(col,oid) and oid in PostgresKnowledgeStore._record_reference_ids(p,exclude_provenance=True)]
    refs+=cursor.execute('SELECT edge_collection,edge_id FROM wang_knowledge.edges WHERE (from_id=%s OR to_id=%s) AND retired_at IS NULL',(oid,oid)).fetchall()
    reviews=cursor.execute('SELECT 1 FROM wang_knowledge.review_events WHERE collection=%s AND object_id=%s LIMIT 1',(col,oid)).fetchall()
    edge=cursor.execute('SELECT payload,review_status,revision,retired_at IS NOT NULL FROM wang_knowledge.edges WHERE edge_collection=%s AND edge_id=%s',(col,oid)).fetchone()
    versions=cursor.execute('''SELECT v.change_set_id,s.source_kind,s.status,o.operation,o.before_revision,
        o.after_revision,v.payload,o.after_sha256 FROM wang_knowledge.object_versions v
        JOIN wang_knowledge.change_sets s USING(change_set_id)
        JOIN wang_knowledge.change_operations o ON o.change_set_id=v.change_set_id AND o.collection=v.collection
          AND o.object_id=v.object_id AND o.after_revision=v.revision
        WHERE v.collection=%s AND v.object_id=%s AND v.revision=%s''',(col,oid,row['revision'])).fetchall()
    if len(versions)!=1:raise ValueError('policy relation original extraction missing/ambiguous')
    creation=dict(zip(('change_set_id','source_kind','status','operation','before_revision','after_revision','after','after_sha256'),versions[0]))
    return policy_relation_context_item(key,row,creation,references=refs,reviews=reviews,
        edge=dict(zip(('payload','review_status','revision','retired'),edge)) if edge else None)


def question_context_item(key,row,creation,*,references,reviews,edges):
    """Account for unused candidate context, NOT its asserted answer or speaker.

    An unresolved question is not an approved QA product. Its unchanged
    extraction and every original pending judgment remain available for later
    question management; they cannot supply a Claim or CVP premise.
    """
    from backend.api.canonical_repository.knowledge_models import QuestionRecord
    col,oid=key.split('/',1);p=row['payload']
    QuestionRecord.model_validate(p)
    allowed={'schema_version','question_id','text','revision','questioner','question_type','visibility',
        'answer_state','review_status','answer_claim_ids','source_fragment_id','source_fragment_ids'}
    if (set(p)-allowed or p.get('schema_version')!=1 or col!='questions' or row['retired'] or row['revision']!=1 or p.get('revision')!=1
            or p.get('question_id')!=oid or p.get('review_status')!='candidate'
            or p.get('visibility')!='internal' or record_content_sha(p)!=row['content_sha256']
            or references or reviews or edges):
        raise ValueError('question context is changed, approved, held or consumed')
    if (creation.get('source_kind')!='knowledge_package' or creation.get('status')!='applied'
            or creation.get('operation')!='create' or creation.get('before_revision') is not None
            or creation.get('after_revision')!=1 or creation.get('after')!=p
            or creation.get('after_sha256')!=row['content_sha256']):
        raise ValueError('question context lacks exact committed original extraction')
    return {'record_key':key,'kind':'unchanged_unapproved_unconsumed_question_context',
        'pin':[1,row['content_sha256'],False],'creation_change_set_id':creation['change_set_id'],
        'semantic_judgment_resolved':False,'approved_dependency':False,
        'content_write_authority':False,'pending_obligation_preserved':True}


def read_question_context(cursor,key):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS
    col,oid=key.split('/',1)
    st=cursor.execute('SELECT payload,revision,content_sha256,retired_at IS NOT NULL FROM wang_knowledge.objects WHERE collection=%s AND object_id=%s',
                      (col,oid)).fetchone()
    if not st:raise ValueError('question context current row missing')
    row=dict(zip(('payload','revision','content_sha256','retired'),st))
    refs=[(c,i) for c,i,p in PostgresKnowledgeStore._reference_consumers(cursor,[oid],sorted(KNOWLEDGE_COLLECTIONS))
          if (c,i)!=(col,oid) and oid in PostgresKnowledgeStore._record_reference_ids(p,exclude_provenance=True)]
    reviews=cursor.execute('SELECT 1 FROM wang_knowledge.review_events WHERE collection=%s AND object_id=%s LIMIT 1',(col,oid)).fetchall()
    edges=cursor.execute('SELECT 1 FROM wang_knowledge.edges WHERE (from_id=%s OR to_id=%s) AND retired_at IS NULL LIMIT 1',(oid,oid)).fetchall()
    rows=cursor.execute('''SELECT v.change_set_id,s.source_kind,s.status,o.operation,o.before_revision,
        o.after_revision,v.payload,o.after_sha256 FROM wang_knowledge.object_versions v
        JOIN wang_knowledge.change_sets s USING(change_set_id)
        JOIN wang_knowledge.change_operations o ON o.change_set_id=v.change_set_id
          AND o.collection=v.collection AND o.object_id=v.object_id AND o.after_revision=v.revision
        WHERE v.collection=%s AND v.object_id=%s AND v.revision=%s''',(col,oid,row['revision'])).fetchall()
    if len(rows)!=1:raise ValueError('question context creation missing/ambiguous')
    creation=dict(zip(('change_set_id','source_kind','status','operation','before_revision','after_revision','after','after_sha256'),rows[0]))
    return question_context_item(key,row,creation,references=refs,reviews=reviews,edges=edges)


def prepare_question_slice(store,value,*,cursor=None):
    """Exact source-first authority plus nonconsumption, never a semantic pass."""
    from copy import deepcopy
    from backend.pipeline.cvp_sql_flow import check_binding,decoded_pins,pin_rows
    from backend.pipeline.claim_first_management import coverage_context
    handoff=context(value,store,cursor=cursor);pending=value['reviewed']['evidence']['pending_cvp_management_record_keys']
    policy_claim=handoff.get('source_slice_mode')=='policy_claim_source_revision'
    domains={'claims','claim_relations','knowledge_relations'} if policy_claim else {'questions'}
    if (not pending or len(pending)!=len(set(pending))
            or pending!=handoff['pending_existing_cvp_record_keys']
            or any(k.split('/',1)[0] not in domains for k in pending)):
        raise ValueError('question slice pending population/domain drift')
    source=applied_review(store,value,handoff['prior_reviewed_sha256'],cursor=cursor)
    check_binding(handoff['claim_layer_completion']);receipt=read_artifact(handoff['claim_layer_completion']['path'])
    original=deepcopy(value);original['reviewed']=source;original['reviewed_sha256']=handoff['prior_reviewed_sha256']
    original['change_set_ids']=receipt['change_set_ids']
    if (value['change_set_ids'][:len(original['change_set_ids'])]!=original['change_set_ids']
            or source['plan']['change_set_id']!=original['change_set_ids'][-1]):
        raise ValueError('question slice source lineage drift')
    if cursor is None:
        if coverage_context(store,original,{'claim_ids':value['claim_ids']})!=handoff:
            raise ValueError('question slice exact completed source handoff drift')
        with store.connect() as conn,conn.cursor() as cur:
            return prepare_question_slice(store,value,cursor=cur)
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.claim_source_write_slice import validate_package
    validate_package(source['evidence']['package']['payload'])
    actual=cursor.execute('''SELECT collection,object_id,operation,before_revision,after_revision,
        before_sha256,after_sha256 FROM wang_knowledge.change_operations
        WHERE change_set_id=%s ORDER BY operation_index''',(source['plan']['change_set_id'],)).fetchall()
    fields=('collection','object_id','operation','before_revision','after_revision','before_sha256','after_sha256')
    planned=[tuple(o[k] for k in fields) for o in source['plan']['operations']]
    if not planned or len(planned)!=len(set(planned)) or sorted(actual)!=sorted(planned):
        raise ValueError('question slice actual source write set drift')
    if (receipt['claim_ids']!=value['claim_ids'] or receipt.get('group_complete') is not False
            or source['evidence'].get('pending_cvp_management_record_keys')!=pending
            or handoff.get('source_only_approval') is not True
            or handoff.get('existing_cvp_content_approved') is not False):
        raise ValueError('question slice source receipt authority drift')
    bindings=[handoff[k] for k in ('claim_layer_completion','source_terminal_packet','source_terminal_review')]
    bindings.extend([receipt['audit'],*receipt['source_proofs']])
    for b in bindings:check_binding(b)
    pins=decoded_pins(receipt['read_pins']);items=[(read_policy_slice_item if policy_claim else read_question_context)(cursor,k) for k in pending]
    for item in items:
        key=tuple(item['record_key'].split('/',1))
        if pins.get(key)!=tuple(item['pin']):raise ValueError('question slice lacks original audited source pin')
    PostgresKnowledgeStore._assert_transaction_read_pins(cursor,pins)
    return {'source_handoff':handoff,'pending_record_keys':pending,'items':items,'read_pins':pin_rows(pins),
        'bindings':bindings,'model_verdicts_changed':False,'original_pending_list_preserved':True}


def applied_review(store,value,sha,*,cursor=None):
    """One named SQL review archive and its actual commit, never ancestry."""
    if cursor is None:
        with store.connect() as conn,conn.cursor() as cur:
            return applied_review(store,value,sha,cursor=cur)
    ident='CVPR-'+sha256_json({'parent':value['run_id'],'reviewed_sha256':sha})
    row=cursor.execute("SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s",(ident,)).fetchone()
    if not row:
        # The most recent committed review is still the group's current row;
        # it is archived only when another review replaces it. Same SQL-bound
        # proof, not a fallback to status labels or arbitrary old approvals.
        current=cursor.execute("SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s AND stage='cvp_group' AND metadata->>'reviewed_sha256'=%s",
                               (value['run_id'],sha)).fetchone()
        if current:
            row=({**current[0],'parent_run_id':value['run_id']},)
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
    handoff=context(value,store)
    if not handoff:return None
    from backend.pipeline.cvp_link_representation import GENERATION_HANDOFF,committed_generation_facts
    if handoff['schema_version']==GENERATION_HANDOFF:
        return committed_generation_facts(store,value)
    completed=completed_source_facts(value,store=store)
    if completed is not None:return completed
    if handoff.get('source_slice_mode') in {'policy_evidence_binding','policy_claim_source_revision'}:
        return prepare_question_slice(store,value)
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
    if facts['source_handoff'].get('schema_version')==CLOSED_STATEMENT:
        retained=facts['source_handoff']['viewpoint_ids']
        current=store.read_cvp_context(value['claim_ids'],viewpoint_ids=retained)
        views=sorted(set(views)|set(retained)|{r['payload']['viewpoint_id'] for (col,_),r in current.items()
            if col=='viewpoint_claim_links' and not r['retired'] and r['payload']['claim_id'] in value['claim_ids']})
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
            or body.get('audit')!=audit_binding or body.get('source_handoff')!=context(value,cursor=cursor)
            or body.get('pending_record_keys')!=value['reviewed']['evidence']['pending_cvp_management_record_keys']
            or body.get('model_verdicts_changed') is not False or body.get('original_pending_list_preserved') is not True
            or body.get('semantic_resolution')!=(binding(semantic_resolution) if semantic_resolution else None)):
        raise ValueError('source impact completion proof drift')
    for b in body['bindings']+body['audit_proofs']:check_binding(b)
    from backend.pipeline.cvp_link_representation import GENERATION_HANDOFF,committed_generation_facts
    if body['source_handoff']['schema_version']==GENERATION_HANDOFF:
        expected=committed_generation_facts(None,value,cursor=cursor)
        if any(body.get(k)!=v for k,v in expected.items()):
            raise ValueError('joint generation completion facts drift')
        audit=read_artifact(audit_binding['path'])
        independent=decoded_pins(audit['meta']['registry_read_scope']['read_pins'])
        pin=decoded_pins(expected['read_pins'])[('claims',expected['source_handoff']['claim_id'])]
        if independent.get(('claims',expected['source_handoff']['claim_id']))!=pin:
            raise ValueError('joint generation current Claim absent from full independent audit')
        for item in body['items']:
            key=tuple(item['record_key'].split('/',1))
            if item['kind']=='approved_current_cvp_impact_independently_audited':
                if independent.get(key)!=tuple(item['pin']):raise ValueError('joint generation current link absent from complete audit')
            elif key in independent:raise ValueError('joint generation candidate relation consumed by semantic audit')
        return body
    if body.get('prior_completed_resolution'):
        expected=completed_source_facts(value,cursor=cursor)
        if expected is None or any(body.get(k)!=v for k,v in expected.items()):
            raise ValueError('completed source impact revalidation proof drift')
        audit=read_artifact(audit_binding['path'])
        independent=decoded_pins(audit['meta']['registry_read_scope']['read_pins'])
        if body['source_handoff'].get('schema_version')==CLOSED_STATEMENT:
            for item in body['items']:
                key=tuple(item['record_key'].split('/',1))
                if item['kind']=='approved_current_cvp_impact_independently_audited' and independent.get(key)!=tuple(item['pin']):
                    raise ValueError('closed statement CVP target missing from current complete audit')
                if item['kind']=='unchanged_unapproved_internal_relation_context' and key in independent:
                    raise ValueError('closed candidate context consumed as an approved audit dependency')
        for transition in body['audited_claim_transitions']:
            if independent.get(('claims',transition['claim_id']))!=tuple(transition['current_pin']):
                raise ValueError('completed source dependency absent from current complete independent audit')
        if body['source_handoff'].get('source_slice_mode') in {'policy_evidence_binding','policy_claim_source_revision'}:
            for item in body['items']:
                key=tuple(item['record_key'].split('/',1))
                if item['kind']=='unchanged_independently_approved_readonly_claim_context':
                    if independent.get(key)!=tuple(item['pin']):raise ValueError('closed policy Claim absent from current full independent audit')
                elif key in independent:raise ValueError('closed unapproved context consumed by current audit')
        return body
    if body['source_handoff'].get('source_slice_mode') in {'policy_evidence_binding','policy_claim_source_revision'}:
        expected=prepare_question_slice(None,value,cursor=cursor)
        if any(body.get(k)!=v for k,v in expected.items()):
            raise ValueError('question context completion proof changed')
        audit=read_artifact(audit_binding['path'])
        independent=decoded_pins(audit['meta']['registry_read_scope']['read_pins'])
        for item in body['items']:
            key=tuple(item['record_key'].split('/',1))
            if item['kind']=='unchanged_independently_approved_readonly_claim_context':
                if independent.get(key)!=tuple(item['pin']):
                    raise ValueError('policy source Claim missing from complete current independent audit')
            elif key in independent:
                raise ValueError('unapproved context was consumed by independent semantic audit')
        return body
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
