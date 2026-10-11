"""Closure routing and actual saved revalidation; no provider calls."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import hashlib
import os
from unittest.mock import patch

import pytest
from backend.pipeline import cvp_sql_actions as actions
from backend.pipeline import cvp_source_impact_resolution as impact
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore,canonical_json,sha256_json,ChangeSetConflict
from backend.pipeline.cvp_sql_flow import binding
from backend.pipeline.viewpoint_resolution_runtime import write_immutable,read_artifact
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store
from backend.tests.test_cvp_sql_transaction_postgres import task,apply


def test_ordinary_source_revision_keeps_separate_pending_obligations():
    from backend.pipeline import claim_owner_revision as owner,claim_evidence_completion as ec
    original={'viewpoint_ids':['V'],'pending_cvp_claim_ids':[],
              'pending_cvp_management_record_keys':['knowledge_relations/AR-unresolved']}
    saved={'claim_ids':['C-original'],'reviewed':{'evidence':original}}
    material={'sql_scope':{'read_pins':[]},'manifest':{},'before_records':[],
              'claim_issue_context':{'claim_ids':['C'],'issues':[]}}
    action={'action_id':'A','materials':material,'materials_sha256':'x','authorization':{}}
    class Stage:
        def __init__(self,*a):pass
        def reviewed(self,material):return NS(model_dump=lambda:{}),{'artifact_sha256':'r','stage':'final_review'}
    captured={}
    def commit(*a,**kw):captured.update(kw);return {'captured':True}
    with patch.object(actions.content,'Manifest',NS(model_validate=lambda d:NS(model_dump=lambda **kw:d))),\
         patch.object(actions.content,'compile_package',return_value={}),patch.object(actions.content,'index',return_value={}),\
         patch.object(owner,'validate_joint',return_value=False),patch.object(ec,'direction_for',return_value=None),\
         patch.multiple(actions,source_action_id=lambda _: 'A',prepare_source_action=lambda *a,**kw:action,
            resume_bound_action=lambda *a:None,initialize_subscription_runtime=lambda *a:None,ScopedSourceModelStages=Stage,
            validate_source_package=lambda *a:None,binding=lambda *a:{'mock':'binding'},
            action_sources=lambda *a:[{'mock':'source'}],commit_package=commit),\
         patch('backend.pipeline.cvp_sql_flow.verified_group_backup',return_value={'mock':'backup'}):
        actions.execute_source_action(NS(get_cvp_group=lambda _:saved,bind_claim_issue_action=lambda *a,**kw:None),
            'G',{'source_id':'S'},root=Path('/NO_FILES_REVIEW_ONLY'),ports=NS())
    assert captured['pending_cvp_management_record_keys']==original['pending_cvp_management_record_keys']


def test_sql_review_cannot_drop_prior_obligations(postgres_store):
    store=postgres_store;plan,tx=task(store);apply(store,plan,tx)
    saved=store.get_cvp_group(tx.run_id);meta={k:v for k,v in saved.items() if k not in {'run_id','status','error'}}
    meta['reviewed']['evidence']['pending_cvp_management_record_keys']=['knowledge_relations/old']
    with store.connect() as conn:
        conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',(canonical_json(meta),tx.run_id))
    before=store.get_cvp_group(tx.run_id)
    evidence=deepcopy(meta['reviewed']['evidence']);evidence.pop('pending_cvp_management_record_keys')
    with pytest.raises(ChangeSetConflict,match='erase existing pending'):
        store.review_cvp_group(tx.run_id,plan=plan,evidence=evidence)
    assert store.get_cvp_group(tx.run_id)==before


@pytest.mark.parametrize('origin',['gpt_proposal','opus_initial','own_source_upheld','claim_statement'])
@pytest.mark.parametrize('separate',[[],['knowledge_relations/unresolved']])
def test_joint_subject_origin_does_not_erase_separate_pending(origin,separate,monkeypatch):
    from backend.pipeline import claim_dependency_handoff as handoff
    material={'claim_issue_context':{'claim_ids':['C'],'issues':[{'claim_id':'C','origin':origin}]},
              'sql_scope':{'run_id':'G'}}
    proof={'path':'/exact-bound-materials'}
    app={'materials':proof,'run_id':'G','materials_sha256':sha256_json(material)}
    value={'run_id':'G','reviewed':{'evidence':{'action':{'kind':'source_claim_revision',
        'materials':material,'materials_sha256':sha256_json(material)},'pending_cvp_claim_ids':['C'],
        'pending_cvp_management_record_keys':separate,
        'package':{'payload':{'source_repair_application':app,'viewpoint_claim_links':[{'claim_id':'C'}]}}}}}
    monkeypatch.setattr(handoff,'check_binding',lambda p:None)
    monkeypatch.setattr(handoff,'read',lambda p:material)
    monkeypatch.setattr(handoff,'body',lambda p:p)
    before=deepcopy(value)
    assert handoff.subject(value)==(proof,'C',material)
    assert value==before  # Detection is not approval or pending resolution.
    value['reviewed']['evidence']['pending_cvp_claim_ids'].append('foreign')
    assert handoff.subject(value) is None


ACTUAL='CVPG-e60b6130e0f7c81e810f4195c47f9eda0830bb1110e48c7999cc6fcfbe4e1e2a'

@pytest.fixture
def actual():
    if os.getenv('WKP357_CLOSED_SOURCE_REVALIDATION')!='1':pytest.skip('explicit saved production readonly scope')
    from dotenv import load_dotenv
    load_dotenv('.env');store=PostgresKnowledgeStore();return store,store.get_cvp_group(ACTUAL)


@pytest.mark.parametrize('fault',[None,'pending','review','closure','missing_commit','approval','writeset',
                                 'old_generation','receipt','audit_generation','current_links','source_target'])
def test_completed_source_facts_exact_and_no_writes(actual,monkeypatch,fault):
    store,value=actual;original=deepcopy(value)
    old=read_artifact(value['completion']['retained_impacts_resolution']['path'])
    files={p['path']:hashlib.sha256(Path(p['path']).read_bytes()).hexdigest() for p in old['bindings']+old['audit_proofs']}
    if fault=='pending':value['reviewed']['evidence']['pending_cvp_management_record_keys'].pop()
    if fault=='review':value['reviewed_sha256']='foreign'
    if fault=='closure':
        read=impact.read_artifact
        def modified(path):
            body=read(path)
            if str(path)==value['completion']['retained_impacts_resolution']['path']:
                body['model_verdicts_changed']=True;body['artifact_sha256']=sha256_json({k:v for k,v in body.items() if k!='artifact_sha256'})
            return body
        monkeypatch.setattr(impact,'read_artifact',modified)
    with store.connect() as conn,conn.cursor() as cur:
        conn.execute('SET TRANSACTION READ ONLY')
        class Readonly:
            def execute(self,q,args=None):
                result=cur.execute(q.removesuffix(' FOR UPDATE'),args)
                rows=result.fetchall() if result.description else []
                if fault=='missing_commit' and q.startswith('SELECT v.change_set_id'):rows=[]
                if fault=='approval' and q.startswith('SELECT status,fingerprint') and rows:
                    r=list(rows[0]);r[3]={**r[3],'approval':{}};rows=[tuple(r)]
                if fault=='writeset' and q.startswith('SELECT operation,collection'):rows=rows[:-1]
                if fault=='old_generation' and q.startswith('SELECT payload,content_sha256') and rows:
                    rows=[(rows[0][0],'foreign')]
                if fault=='receipt' and q.startswith('SELECT metadata FROM') and args!= (ACTUAL,) and rows and rows[0][0].get('claim_layer_audit'):
                    r=deepcopy(rows[0][0]);r['claim_layer_audit']={};rows=[(r,)]
                if fault=='current_links' and q.startswith('SELECT object_id,payload'):rows=[]
                if fault=='audit_generation':
                    if q.startswith('SELECT payload,revision') and rows:
                        r=list(rows[0]);r[1]+=1;rows=[tuple(r)]
                if fault=='source_target' and q.startswith('SELECT revision,content_sha256') and args[0]=='evidence_steps':
                    rows=[]
                self.rows=rows
                return self
            def fetchone(self):return self.rows[0] if self.rows else None
            def fetchall(self):return self.rows
            def __getattr__(self,name):return getattr(cur,name)
        if fault:
            with pytest.raises((ValueError,KeyError,ChangeSetConflict)):impact.completed_source_facts(value,cursor=Readonly())
        else:
            facts=impact.completed_source_facts(value,cursor=Readonly())
            assert facts['pending_record_keys']==old['pending_record_keys'] and len(facts['items'])==6
            assert facts['audited_claim_transitions'][0]['claim_id']=='CL-0029'
            assert facts['audited_claim_transitions'][0]['before_pin'][0]==3
            assert facts['audited_claim_transitions'][0]['current_pin'][0]==4
            assert len(facts['read_pins'])==8 and not facts['model_verdicts_changed']
    assert store.get_cvp_group(ACTUAL)==original
    assert files=={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in files}


def _clone_closed_scope(production,target,target_run=ACTUAL):
    """Only indexed scope rows into disposable SQL; never writes production."""
    from psycopg.rows import dict_row
    from psycopg import sql
    value=production.get_cvp_group(target_run)
    facts=impact.prepare(production,value)
    transition=next(iter(facts['audited_claim_transitions']),None)
    parents=[target_run]+([transition['source_run_id']] if transition else [])
    with production.connect() as conn,conn.cursor(row_factory=dict_row) as cur:
        runs=cur.execute("SELECT * FROM wang_knowledge.pipeline_runs WHERE run_id=ANY(%s) OR metadata->>'parent_run_id'=ANY(%s)",(parents,parents)).fetchall()
        ids=list({cid for r in runs for cid in r['metadata'].get('change_set_ids',[])})
        before=cur.execute("SELECT * FROM wang_knowledge.object_versions WHERE collection='claims' AND object_id=%s AND revision=%s",(transition['claim_id'],transition['before_pin'][0])).fetchall() if transition else []
        ids+= [r['change_set_id'] for r in before if r['change_set_id'] not in ids]
        changes=cur.execute('SELECT * FROM wang_knowledge.change_sets WHERE change_set_id=ANY(%s)',(ids,)).fetchall()
        operations=cur.execute('SELECT * FROM wang_knowledge.change_operations WHERE change_set_id=ANY(%s)',(ids,)).fetchall()
        versions=cur.execute('SELECT * FROM wang_knowledge.object_versions WHERE change_set_id=ANY(%s)',(ids,)).fetchall()
        keys={(r['collection'],r['object_id']) for r in operations}|{(p['collection'],p['object_id']) for p in facts['read_pins']}
        objects=[cur.execute('SELECT * FROM wang_knowledge.objects WHERE collection=%s AND object_id=%s',k).fetchone() for k in sorted(keys)]
        edges=cur.execute('SELECT * FROM wang_knowledge.edges WHERE edge_id=ANY(%s)',([i for c,i in keys],)).fetchall()
    from psycopg.types.json import Jsonb
    with target.connect() as conn:
        for table,rows in [('change_sets',changes),('objects',objects),('edges',edges),('object_versions',versions),('change_operations',operations),('pipeline_runs',runs)]:
            for r in rows:
                if r is None:raise ValueError('isolated scope missing current row')
                fields=list(r);values=[Jsonb(v) if isinstance(v,dict) or (isinstance(v,list) and k not in {'source_ids','output_paths'}) else v for k,v in r.items()]
                conn.execute(sql.SQL('INSERT INTO wang_knowledge.{} ({}) VALUES ({})').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,fields)),sql.SQL(',').join(sql.Placeholder() for _ in fields)),values)
    return value


@pytest.mark.parametrize('late_failure',[False,True])
def test_actual_closed_source_transaction_and_rollback(actual,postgres_store,tmp_path,late_failure):
    production,_=actual;store=postgres_store;value=_clone_closed_scope(production,store)
    before=store.get_cvp_group(ACTUAL);facts=impact.prepare(store,value)
    audit=tmp_path/'synthetic-scope-only-audit.json'
    # This tests typed SQL proof integration, NOT a provider judgement or
    # ordinary group's full-audit validation (tested separately by its caller).
    write_immutable(audit,{'meta':{'registry_read_scope':{'read_pins':facts['read_pins']}}})
    proof={'schema_version':impact.VERSION,'run_id':ACTUAL,'reviewed_sha256':value['reviewed_sha256'],
           'audit':binding(audit),'audit_proofs':[],'semantic_resolution':None,**facts}
    proof['artifact_sha256']=sha256_json(proof);path=tmp_path/'proof.json';write_immutable(path,proof)
    try:
        with store.connect() as conn,conn.cursor() as cur:
            impact.validate_for_completion(value,path,binding(audit),cur)
            cur.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=jsonb_set(metadata,'{isolated_typed_validation}','true') WHERE run_id=%s",(ACTUAL,))
            if late_failure:raise ValueError('injected late proof failure')
    except ValueError as exc:
        assert late_failure and 'injected late' in str(exc)
    after=store.get_cvp_group(ACTUAL)
    if late_failure:assert after==before
    else:
        assert after['isolated_typed_validation'] is True
        with store.connect() as conn,conn.cursor() as cur:impact.validate_for_completion(value,path,binding(audit),cur)
    assert production.get_cvp_group(ACTUAL)==value


@pytest.mark.parametrize('fault',[None,'pending','claim_population','source_review','source_commit','candidate_approval','candidate_consumer','current_pin','missing_source','changed_sql_completion'])
def test_actual_closed_statement_revalidation(actual,monkeypatch,fault):
    store,_=actual
    run='CVPG-6b3b1929820ae88b774aece78956585cc7a28085f543c314c0e9461a0067dc5f'
    value=store.get_cvp_group(run);original=deepcopy(value)
    prior=read_artifact(value['completion']['retained_impacts_resolution']['path'])
    files={p['path']:hashlib.sha256(Path(p['path']).read_bytes()).hexdigest() for p in prior['bindings']}
    if fault=='pending':value['reviewed']['evidence']['pending_cvp_management_record_keys'].pop()
    if fault=='claim_population':value['reviewed']['evidence']['pending_cvp_claim_ids'].pop()
    if fault=='source_review':
        read=impact.closed_statement_context
        def altered(v):
            c=read(v)
            if c:c={**c,'source_reviewed_sha256':'foreign'}
            return c
        monkeypatch.setattr(impact,'closed_statement_context',altered)
    with store.connect() as conn,conn.cursor() as cur:
        conn.execute('SET TRANSACTION READ ONLY')
        class Readonly:
            def execute(self,q,args=None):
                result=cur.execute(q.removesuffix(' FOR UPDATE'),args)
                rows=result.fetchall() if result.description else []
                if fault=='source_commit' and q.startswith('SELECT status,fingerprint'):rows=[]
                if fault=='current_pin' and q.startswith('SELECT revision,content_sha256'):rows=[]
                if fault=='missing_source' and q.startswith('SELECT metadata FROM') and args!=(run,):rows=[]
                if fault=='candidate_approval' and q.startswith('SELECT 1 FROM wang_knowledge.review_events'):rows=[(1,)]
                if fault=='candidate_consumer' and q.startswith('SELECT 1 FROM wang_knowledge.edges'):rows=[(1,)]
                if fault=='changed_sql_completion' and q.startswith('SELECT metadata FROM') and args==(run,) and rows:
                    r=deepcopy(rows[0][0]);r['completion']['audit']={'path':'foreign'};rows=[(r,)]
                self.rows=rows;return self
            def fetchone(self):return self.rows[0] if self.rows else None
            def fetchall(self):return self.rows
            def __getattr__(self,k):return getattr(cur,k)
        if fault:
            with pytest.raises((ValueError,KeyError,ChangeSetConflict)):impact.completed_source_facts(value,cursor=Readonly())
        else:
            facts=impact.completed_source_facts(value,cursor=Readonly())
            assert len(facts['items'])==16 and len(facts['read_pins'])==26
            assert facts['pending_record_keys']==prior['pending_record_keys']
            keys=impact.audit_record_keys(facts['pending_record_keys'],facts)
            assert len(keys)==7 and not any(k.startswith('knowledge_relations/') for k in keys)
            assert not facts['model_verdicts_changed']
    assert store.get_cvp_group(run)==original
    assert files=={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in files}


@pytest.mark.parametrize('late_failure',[False,True])
def test_closed_statement_transaction_and_late_rollback(actual,postgres_store,tmp_path,late_failure):
    production,_=actual;run='CVPG-6b3b1929820ae88b774aece78956585cc7a28085f543c314c0e9461a0067dc5f'
    value=_clone_closed_scope(production,postgres_store,run)
    before=postgres_store.get_cvp_group(run);facts=impact.prepare(postgres_store,value)
    candidate_keys={i['record_key'] for i in facts['items'] if i['kind']=='unchanged_unapproved_internal_relation_context'}
    # Transaction scope fixture only; normal full-provider audit remains mandatory.
    audit=tmp_path/'scope-only.json'
    write_immutable(audit,{'meta':{'registry_read_scope':{'read_pins':[p for p in facts['read_pins']
        if p['collection']+'/'+p['object_id'] not in candidate_keys]}}})
    body={'schema_version':impact.VERSION,'run_id':run,'reviewed_sha256':value['reviewed_sha256'],
          'audit':binding(audit),'audit_proofs':[],'semantic_resolution':None,**facts}
    body['artifact_sha256']=sha256_json(body);proof=tmp_path/'proof.json';write_immutable(proof,body)
    try:
        with postgres_store.connect() as conn,conn.cursor() as cur:
            impact.validate_for_completion(value,proof,binding(audit),cur)
            cur.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=jsonb_set(metadata,'{isolated_closed_validation}','true') WHERE run_id=%s",(run,))
            if late_failure:raise ValueError('injected late failure')
    except ValueError as exc:assert late_failure and 'injected late' in str(exc)
    if late_failure:assert postgres_store.get_cvp_group(run)==before
    else:
        assert postgres_store.get_cvp_group(run)['isolated_closed_validation'] is True
        with postgres_store.connect() as conn,conn.cursor() as cur:impact.validate_for_completion(value,proof,binding(audit),cur)
    assert production.get_cvp_group(run)==value
