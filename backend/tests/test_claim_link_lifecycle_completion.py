"""Whole Claim -> owned links -> audit -> SQL completion, never provider calls.

Actual saved artifacts authenticate content. Synthetic audit rows in disposable
SQL test transaction accounting only and are NEVER production audit approval.
"""
from copy import deepcopy
from pathlib import Path
import hashlib
import os
import pytest
from backend.pipeline import cvp_link_representation as links
from backend.pipeline import cvp_source_impact_resolution as impacts
from backend.api.canonical_repository.postgres_store import sha256_json
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store


@pytest.fixture
def actual():
    run=os.environ.get('WKP_CLAIM_LINK_LIFECYCLE_ACTUAL')
    if not run:pytest.skip('explicit real saved lifecycle; production SELECT only')
    from dotenv import load_dotenv
    from backend.tests.test_statement_impact_completion import ReadOnly
    load_dotenv('.env');store=ReadOnly();value=store.get_cvp_group(run)
    return store,value


def test_domain_classifier_is_not_approval():
    value={'reviewed':{'evidence':{'action':{'materials':{'generation':True,
        'claim':{'claim_id':'C'},'viewpoint_ids':['V']}}}}}
    assert links.generation_handoff(value)=={'schema_version':links.GENERATION_HANDOFF,'claim_id':'C','viewpoint_ids':['V']}
    assert links.generation_handoff({}) is None
    keys=['claim_relations/R','viewpoint_claim_links/L']
    facts={'source_handoff':links.generation_handoff(value),'pending_record_keys':keys,
        'items':[{'record_key':keys[0],'kind':'unchanged_unapproved_unconsumed_policy_relation_context'},
                 {'record_key':keys[1],'kind':'approved_current_cvp_impact_independently_audited'}]}
    assert impacts.audit_record_keys(keys,facts)==[keys[1]]
    for fault in ('omit','duplicate','unknown','reorder'):
        bad=deepcopy(facts)
        if fault=='omit':bad['items'].pop()
        elif fault=='duplicate':bad['items'][0]=deepcopy(bad['items'][1])
        elif fault=='unknown':bad['items'][0]['kind']='approved_relation'
        else:bad['items'].reverse()
        with pytest.raises(ValueError):impacts.audit_record_keys(keys,bad)


@pytest.mark.parametrize('fault',[None,'roster','campaign','commit','missing_pending','extra_pending','receipt',
    'source_sha','current_plan','foreign_source','approval','material','viewpoints'])
def test_actual_all_handoffs_use_one_exact_authority(actual,fault):
    store,value=actual;original=deepcopy(value)
    root=Path(value['claim_layer_audit']['receipt']['path']).parent.parent
    files={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    if fault=='roster':value['claim_ids'].append('foreign')
    elif fault=='campaign':value['campaign_sha256']='foreign'
    elif fault=='commit':value['change_set_ids'][-2]='foreign'
    elif fault=='missing_pending':value['reviewed']['evidence']['pending_cvp_management_record_keys'].pop()
    elif fault=='extra_pending':value['reviewed']['evidence']['pending_cvp_management_record_keys'].append('claims/foreign')
    elif fault=='receipt':value['claim_layer_audit']['receipt']['file_sha256']='foreign'
    elif fault=='source_sha':value['reviewed']['evidence']['package']['sha256']='foreign'
    elif fault=='current_plan':value['reviewed']['plan']['operations'].pop()
    elif fault=='foreign_source':value['reviewed']['evidence']['action']['materials']['sources'][0]['file_sha256']='foreign'
    elif fault=='approval':value['reviewed']['evidence']['approval']['opus']['file_sha256']='foreign'
    elif fault=='material':value['reviewed']['evidence']['action']['materials']['claim']['statement']+='foreign'
    elif fault=='viewpoints':value['reviewed']['evidence']['viewpoint_ids'].pop()
    if fault:
        with pytest.raises((ValueError,KeyError)):impacts.prepare(store,value)
    else:
        facts=impacts.prepare(store,value)
        with store.connect() as conn,conn.cursor() as cur:
            assert links.committed_generation_facts(None,value,cursor=cur)==facts
        assert len(facts['items'])==len(facts['pending_record_keys'])==4
        assert len(impacts.audit_record_keys(facts['pending_record_keys'],facts))==2
    assert store.get_cvp_group(original['run_id'])==original
    assert files=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def domain_audit(value,facts,pins):
    """Explicit SQL test fixture, not a provider response or production proof."""
    return {'fixture':'SYNTHETIC TRANSACTION DOMAIN ONLY','meta':{'model':'gemini-3.8-flash',
        'registry_read_scope':{'kind':'sql_transaction_group','complete_target_members':True,
            'claim_ids':value['claim_ids'],'viewpoint_ids':facts['source_handoff']['viewpoint_ids'],'read_pins':pins}},
        'layers':{'1':{'counts':{'pass':1}},'2':{},
            '3':{'complete':True,'judged':len(value['claim_ids']),'results':[{'claim_id':i,'verdict':'supported'} for i in value['claim_ids']]},
            '4':{'complete':True,'judged':len(facts['source_handoff']['viewpoint_ids']),
                'results':[{'viewpoint_id':i,'verdict':'same'} for i in facts['source_handoff']['viewpoint_ids']]}}}


def domain_proof(value,facts,audit,tmp_path,name):
    from backend.pipeline.cvp_sql_flow import binding
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    ap=tmp_path/(name+'-audit.json');write_immutable(ap,audit)
    proof={'schema_version':impacts.VERSION,'run_id':value['run_id'],'reviewed_sha256':value['reviewed_sha256'],
        'audit':binding(ap),'audit_proofs':[],'semantic_resolution':None,**facts}
    proof['artifact_sha256']=sha256_json(proof)
    pp=tmp_path/(name+'-proof.json');write_immutable(pp,proof)
    return ap,pp,proof


@pytest.mark.parametrize('fault',[None,'old_link','missing_link','missing_claim','candidate_consumed','missing_item','binding','pin'])
def test_actual_short_transaction_refuses_incomplete_current_audit(actual,tmp_path,fault):
    store,value=actual;facts=impacts.prepare(store,value)
    independent=[p for p in facts['read_pins'] if p['collection'] not in {'claim_relations','knowledge_relations'}]
    audit=domain_audit(value,facts,deepcopy(independent));bad=deepcopy(facts)
    if fault=='old_link':next(p for p in audit['meta']['registry_read_scope']['read_pins'] if p['collection']=='viewpoint_claim_links')['pin'][0]-=1
    elif fault=='missing_link':audit['meta']['registry_read_scope']['read_pins']=[p for p in independent if p['collection']!='viewpoint_claim_links']
    elif fault=='missing_claim':audit['meta']['registry_read_scope']['read_pins']=[p for p in independent if p['collection']!='claims']
    elif fault=='candidate_consumed':audit['meta']['registry_read_scope']['read_pins']=facts['read_pins']
    elif fault=='missing_item':bad['items'].pop()
    elif fault=='binding':bad['bindings'].pop()
    elif fault=='pin':bad['read_pins'][0]['pin'][0]+=1
    ap,pp,proof=domain_proof(value,bad,audit,tmp_path,'proof')
    with store.connect() as conn,conn.cursor() as cur:
        if fault:
            with pytest.raises(ValueError):impacts.validate_for_completion(value,pp,proof['audit'],cur)
        else:impacts.validate_for_completion(value,pp,proof['audit'],cur)
        conn.rollback()


@pytest.mark.parametrize('fault',[None,'hold','candidate_consumer','source_history','link_history','roster'])
def test_actual_disposable_sql_whole_completion_late_rollback_and_repeat(actual,postgres_store,tmp_path,monkeypatch,fault):
    from psycopg.sql import SQL,Identifier,Placeholder
    from psycopg.types.json import Jsonb
    from backend.pipeline.cvp_sql_flow import pins,pin_rows,decoded_pins,binding
    live,value=actual;facts=impacts.prepare(live,value);target=postgres_store;run=value['run_id']
    current=live.read_cvp_context(value['claim_ids'],viewpoint_ids=facts['source_handoff']['viewpoint_ids'])
    ids=sorted({i for c,i in current}|{p['object_id'] for p in facts['read_pins']})
    with live.connect() as src,target.connect() as dst:
        histories={r[0] for r in src.execute('SELECT DISTINCT change_set_id FROM wang_knowledge.object_versions WHERE object_id=ANY(%s)',(ids,)).fetchall()}|set(value['change_set_ids'])
        selections=[('change_sets','change_set_id=ANY(%s)',(sorted(histories),)),
            ('objects','object_id=ANY(%s)',(ids,)),('object_versions','object_id=ANY(%s)',(ids,)),
            ('change_operations','change_set_id=ANY(%s)',(sorted(histories),)),
            ('edges','edge_id=ANY(%s)',(ids,)),('review_events','object_id=ANY(%s)',(ids,)),
            ('pipeline_runs',"run_id=%s OR metadata->>'parent_run_id'=%s OR metadata->>'owner_run_id'=%s",(run,run,run))]
        for table,where,args in selections:
            result=src.execute(SQL('SELECT * FROM wang_knowledge.{} WHERE '+where).format(Identifier(table)),args)
            cols=[c.name for c in result.description]
            insert=SQL('INSERT INTO wang_knowledge.{} ({}) VALUES ({})').format(Identifier(table),SQL(',').join(map(Identifier,cols)),SQL(',').join(Placeholder()*len(cols)))
            for row in result.fetchall():dst.execute(insert,tuple(Jsonb(x) if isinstance(x,(dict,list)) and name not in {'source_ids','output_paths'} else x for name,x in zip(cols,row)))
    with target.connect() as conn:
        required=target._existing_cvp_context(conn,{'claims':{i:{'claim_id':i} for i in value['claim_ids']},
            'canonical_viewpoints':{i:{'viewpoint_id':i} for i in facts['source_handoff']['viewpoint_ids']}},historical_pins_only=True)
    audit=domain_audit(value,facts,pin_rows(pins(required)))
    ap,pp,proof=domain_proof(value,facts,audit,tmp_path,'isolated')
    kwargs={'audit':audit,'audit_binding':binding(ap),'read_pins':pins(required),'retained_impacts_resolution_path':pp}
    def snapshot():
        with target.connect() as conn:
            return conn.execute('SELECT collection,object_id,revision,content_sha256,retired_at FROM wang_knowledge.objects ORDER BY collection,object_id').fetchall(),conn.execute('SELECT count(*) FROM wang_knowledge.object_versions').fetchone()
    if fault:
        from backend.api.canonical_repository.postgres_store import canonical_json,ChangeSetConflict
        with target.connect() as conn:
            if fault=='hold':conn.execute("UPDATE wang_knowledge.objects SET review_status='human_hold' WHERE collection='claims' AND object_id=%s",(facts['source_handoff']['claim_id'],))
            elif fault=='candidate_consumer':
                oid=next(i['record_key'].split('/',1)[1] for i in facts['items'] if i['record_key'].startswith('claim_relations/'))
                from backend.api.canonical_repository.postgres_store import record_content_sha
                consumer={'schema_version':1,'relation_id':'test-consumer','from_id':oid,'to_id':facts['source_handoff']['claim_id'],
                    'relation_type':'supports','reason':'ISOLATED TEST ONLY','revision':1,'review_status':'system_approved','visibility':'internal'}
                conn.execute("INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,visibility,content_sha256,payload) VALUES('knowledge_relations','test-consumer',1,'system_approved','internal',%s,%s::jsonb)",
                    (record_content_sha(consumer),canonical_json(consumer)))
                conn.execute("INSERT INTO wang_knowledge.edges(edge_collection,edge_id,from_id,to_id,relation_type,revision,payload) VALUES('knowledge_relations','test-consumer',%s,%s,'supports',1,'{}'::jsonb)",(oid,facts['source_handoff']['claim_id']))
            elif fault=='source_history':conn.execute("UPDATE wang_knowledge.change_operations SET before_sha256='foreign' WHERE change_set_id=%s",(value['change_set_ids'][-2],))
            elif fault=='link_history':conn.execute("UPDATE wang_knowledge.object_versions SET payload=jsonb_set(payload,'{link_type}','\"supports\"') WHERE collection='viewpoint_claim_links' AND change_set_id=%s",(value['change_set_ids'][-1],))
            elif fault=='roster':
                changed=deepcopy(value);changed['claim_ids'].append('foreign')
                conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',(canonical_json(changed),run))
        original=target.get_cvp_group(run);objects=snapshot()
        with pytest.raises((ValueError,ChangeSetConflict)):target.complete_cvp_group(run,**kwargs)
        assert target.get_cvp_group(run)==original and snapshot()==objects
        return
    original=target.get_cvp_group(run);objects=snapshot();gate=target._assert_claim_issue_committed
    monkeypatch.setattr(target,'_assert_claim_issue_committed',lambda *a:(_ for _ in ()).throw(RuntimeError('late lifecycle rollback sentinel')))
    with pytest.raises(RuntimeError,match='rollback sentinel'):target.complete_cvp_group(run,**kwargs)
    assert target.get_cvp_group(run)==original and snapshot()==objects
    monkeypatch.setattr(target,'_assert_claim_issue_committed',gate)
    target.complete_cvp_group(run,**kwargs);completed=target.get_cvp_group(run)
    assert completed['checkpoint']=='audited' and completed['status']=='succeeded'
    assert completed['reviewed']['evidence']['pending_cvp_management_record_keys']==facts['pending_record_keys']
    target.complete_cvp_group(run,**kwargs)
    assert target.get_cvp_group(run)==completed and snapshot()==objects
