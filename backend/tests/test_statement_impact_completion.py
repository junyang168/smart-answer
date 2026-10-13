"""Statement slice closure uses real saved approval; no new vendor calls."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import pytest
from dotenv import load_dotenv
from backend.pipeline import cvp_source_impact_resolution as impact
from backend.pipeline import claim_source_write_slice as slice_flow
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store


class SelectOnly:
    """Allow production SELECT FOR UPDATE, but reject every SQL mutation."""
    def __init__(self, wrapped):self.wrapped=wrapped
    def __getattr__(self,name):return getattr(self.wrapped,name)
    def __enter__(self):self.wrapped.__enter__();return self
    def __exit__(self,*args):return self.wrapped.__exit__(*args)
    def cursor(self,*args,**kwargs):return SelectOnly(self.wrapped.cursor(*args,**kwargs))
    def execute(self,query,*args,**kwargs):
        rendered=query if isinstance(query,str) else query.as_string(self.wrapped.connection)
        if not rendered.lstrip().upper().startswith('SELECT '):
            raise AssertionError('saved-only fixture forbids SQL mutation: '+rendered[:80])
        result=self.wrapped.execute(query,*args,**kwargs)
        return self if result is self.wrapped else SelectOnly(result)


class ReadOnly(PostgresKnowledgeStore):
    def connect(self):return SelectOnly(super().connect())


@pytest.fixture
def actual_completion(tmp_path,monkeypatch):
    run=os.environ.get('WKP_STATEMENT_IMPACT_ACTUAL')
    if not run:pytest.skip('explicit actual saved statement impact fixture')
    load_dotenv('.env');store=ReadOnly();value=store.get_cvp_group(run)
    original=deepcopy(value)
    root=Path(value['claim_layer_audit']['receipt']['path']).parent.parent
    files={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
    from backend.pipeline import source_reconciliation_runner as source,viewpoint_resolution_runtime as runtime
    write=runtime.write_immutable
    def bounded_write(path,*args,**kwargs):
        if not Path(path).resolve().is_relative_to(tmp_path.resolve()):
            raise AssertionError('closure may not change any original action/model file')
        return write(path,*args,**kwargs)
    def forbidden(*a,**kw):raise AssertionError('saved closure must not call/write source models')
    monkeypatch.setattr(source,'write',forbidden)
    monkeypatch.setattr(runtime,'write_immutable',bounded_write)
    monkeypatch.setattr(impact,'write_immutable',bounded_write)
    output=tmp_path/'resolution.json'
    proof,semantic,audit,pins=impact.build_statement_impact_resolution(store,value,output)
    assert store.get_cvp_group(run)==original
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==sha for p,sha in files.items())
    yield store,value,proof,semantic,audit,pins
    assert store.get_cvp_group(run)==original
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==sha for p,sha in files.items())


def test_actual_complete_statement_impact_saved_only(actual_completion):
    store,value,proof,semantic,audit,pins=actual_completion
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.pipeline.cvp_sql_flow import binding
    body=read_artifact(proof)
    assert len(body['items'])==16 and body['pending_record_keys']==value['reviewed']['evidence']['pending_cvp_management_record_keys']
    assert len([i for i in body['items'] if i['kind']=='unchanged_unapproved_internal_relation_context'])==6
    assert body['new_model_calls']==body['registry_writes']==0
    assert body['unapproved_candidate_semantics_resolved'] is False
    assert slice_flow.verify_saved_statement_impact_resolution(audit,read_artifact(semantic))=={
        '3':set(),'4':{'CV-b387f72e4d5e17e6819b'}}
    # READ ONLY rejects any accidental DML even in the real completion validator.
    with store.connect() as conn,conn.cursor() as cur:
        result=impact.validate_statement_impact_completion(value,proof,binding(Path(body['audit']['path'])),cur,
            semantic_resolution=semantic)
        assert result==body


@pytest.mark.parametrize('fault',['missing_source','foreign_source','changed_source','held_source',
    'changed_pending','foreign_claim','changed_commits','missing_marker','approved_relation','new_revision'])
def test_real_closure_refuses_scope_drift(actual_completion,tmp_path,fault):
    store,value,proof,semantic,audit,pins=actual_completion
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.pipeline.cvp_sql_flow import binding
    body=read_artifact(proof);current=deepcopy(value)
    if fault=='missing_source':body['source_change_set_id']='missing'
    elif fault=='foreign_source':body['source_reviewed_sha256']='foreign'
    elif fault=='changed_source':body['source_change_set_id']=value['change_set_ids'][0]
    elif fault=='held_source':body['items'][0]['kind']='human_hold'
    elif fault=='changed_pending':body['pending_record_keys']=body['pending_record_keys'][:-1]
    elif fault=='foreign_claim':body['claim_ids']=['foreign']
    elif fault=='changed_commits':body['change_set_ids']=body['change_set_ids'][:-1]
    elif fault=='missing_marker':body['claim_layer_completion']={}
    elif fault=='approved_relation':
        next(i for i in body['items'] if i['kind']=='unchanged_unapproved_internal_relation_context')['kind']='approved_current_cvp_impact_independently_audited'
    elif fault=='new_revision':body['read_pins'][0]['pin'][0]+=1
    from backend.api.canonical_repository.postgres_store import sha256_json
    body['artifact_sha256']=sha256_json({k:v for k,v in body.items() if k!='artifact_sha256'})
    changed=tmp_path/'changed.json'
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    write_immutable(changed,body)
    with store.connect() as conn,conn.cursor() as cur,pytest.raises((ValueError,KeyError,RuntimeError)):
        impact.validate_statement_impact_completion(current,changed,binding(Path(body['audit']['path'])),cur,
            semantic_resolution=semantic)


@pytest.mark.parametrize('field,value',[('new_model_calls',1),('registry_writes',1),
    ('model_verdicts_changed',True),('original_pending_list_preserved',False),
    ('unapproved_candidate_semantics_resolved',True),('run_id','foreign'),
    ('change_set_ids',['foreign']),('pending_record_keys',[]),('claim_ids',[]),
    ('schema_version','foreign')])
def test_completion_header_never_creates_authority(tmp_path,field,value):
    from backend.api.canonical_repository.postgres_store import sha256_json
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    body={'schema_version':impact.SLICE_IMPACTS,'run_id':'run','reviewed_sha256':'sha',
        'change_set_ids':['commit'],'claim_ids':['claim'],'pending_record_keys':['knowledge_relations/ar'],
        'audit':{},'claim_layer_completion':{},'semantic_resolution':None,'new_model_calls':0,
        'registry_writes':0,'model_verdicts_changed':False,'original_pending_list_preserved':True,
        'unapproved_candidate_semantics_resolved':False}
    current={'run_id':'run','reviewed_sha256':'sha','change_set_ids':['commit'],'claim_ids':['claim'],
        'claim_layer_audit':{'receipt':{}},'reviewed':{'evidence':{'pending_cvp_management_record_keys':['knowledge_relations/ar']}}}
    body[field]=value;body['artifact_sha256']=sha256_json(body)
    path=tmp_path/'bad.json';write_immutable(path,body)
    with pytest.raises(ValueError,match='authority'):
        impact.validate_statement_impact_completion(current,path,{},None)


def test_actual_isolated_statement_completion_rollback_and_exact_once(actual_completion,postgres_store,monkeypatch):
    live,value,proof,semantic,audit,pins=actual_completion
    from psycopg.sql import SQL,Identifier,Placeholder
    from psycopg.types.json import Jsonb
    from backend.pipeline.cvp_sql_flow import decoded_pins,binding
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    target=postgres_store;run=value['run_id'];ids=sorted({r['object_id'] for r in pins})
    with live.connect() as src,target.connect() as dst:
        histories={r[0] for r in src.execute('SELECT DISTINCT change_set_id FROM wang_knowledge.object_versions WHERE object_id=ANY(%s)',(ids,)).fetchall()}|set(value['change_set_ids'])
        selections=[('change_sets','change_set_id=ANY(%s)',(sorted(histories),)),
            ('objects','object_id=ANY(%s)',(ids,)),('object_versions','object_id=ANY(%s)',(ids,)),
            ('change_operations','change_set_id=ANY(%s)',(sorted(histories),)),
            ('edges','edge_id=ANY(%s)',(ids,)),('review_events','object_id=ANY(%s)',(ids,)),
            ('pipeline_runs',"run_id=%s OR metadata->>'parent_run_id'=%s OR metadata->>'owner_run_id'=%s",(run,run,run))]
        for table,where,args in selections:
            rows=src.execute(SQL('SELECT * FROM wang_knowledge.{} WHERE '+where).format(Identifier(table)),args)
            cols=[c.name for c in rows.description]
            insert=SQL('INSERT INTO wang_knowledge.{} ({}) VALUES ({})').format(Identifier(table),
                SQL(',').join(map(Identifier,cols)),SQL(',').join(Placeholder()*len(cols)))
            for row in rows.fetchall():dst.execute(insert,tuple(
                Jsonb(x) if isinstance(x,(dict,list)) and name not in {'source_ids','output_paths'} else x
                for name,x in zip(cols,row)))
    from backend.pipeline.cvp_taxonomy_dependency import prepare
    taxonomy=prepare(target,run,Path(read_artifact(proof)['audit']['path']),
        target.read_cvp_context(value['claim_ids'],viewpoint_ids=read_artifact(proof)['viewpoint_ids']))
    def snapshot():
        with target.connect() as conn:
            return conn.execute('SELECT collection,object_id,revision,content_sha256,retired_at FROM wang_knowledge.objects ORDER BY collection,object_id').fetchall(),conn.execute('SELECT count(*) FROM wang_knowledge.object_versions').fetchone()
    before=snapshot();original=target.get_cvp_group(run)
    kwargs=dict(audit=audit,audit_binding=binding(Path(read_artifact(proof)['audit']['path'])),
        read_pins=decoded_pins(pins),owner_resolution_path=semantic,
        retained_impacts_resolution_path=proof,taxonomy_dependency_path=taxonomy)
    committed=target._assert_claim_issue_committed
    def late(*args,**kw):raise RuntimeError('late completion rollback sentinel')
    monkeypatch.setattr(target,'_assert_claim_issue_committed',late)
    with pytest.raises(RuntimeError,match='rollback sentinel'):target.complete_cvp_group(run,**kwargs)
    assert target.get_cvp_group(run)==original and snapshot()==before
    monkeypatch.setattr(target,'_assert_claim_issue_committed',committed)
    target.complete_cvp_group(run,**kwargs)
    completed=target.get_cvp_group(run)
    assert completed['checkpoint']=='audited' and completed['status']=='succeeded' and completed.get('error') is None
    assert snapshot()==before
    target.complete_cvp_group(run,**kwargs)
    assert target.get_cvp_group(run)==completed and snapshot()==before
