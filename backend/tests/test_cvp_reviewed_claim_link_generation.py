"""New Claim generation requires genuine joint link review, not reason parsing."""
from copy import deepcopy
from pathlib import Path
import os
import pytest
from backend.pipeline import cvp_link_representation as f
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import postgres_store, _temporary_postgres

ROOT=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
RUN='CVPG-521c60c1fdc0b9fb1fe5e9704781ee845fbe9edc587efbcfd6e2d8acd4f65bad'

def synthetic():
    claim={'claim_id':'C','revision':3,'statement':'结论和理由。'}
    links=[{'viewpoint_claim_link_id':i,'claim_id':'C','revision':1,'viewpoint_id':v,
        'validated_against_viewpoint_revision_id':v+'R','link_type':role,'effective_state':'active',
        'pinned_claim_revision':2,'component_locator':{'old':'locator'},'evidence_bindings':[{'own':'E'}],
        'decision_id':i+'D'} for i,v,role in [('L1','V1','equivalent_component'),('L2','V2','supports')]]
    m={'generation':True,'claim':claim,'before_links':links}
    answer={'selections':[{'viewpoint_claim_link_id':p['viewpoint_claim_link_id'],'claim_id':'C',
        'diagnosis':'representation_revision','link_type':p['link_type'],
        'spans':[{'start_char':0,'end_char':2,'exact_text':'结论'}],'reason':'SIMULATED TEST ONLY'} for p in links]}
    return m,answer

def test_full_link_generation_changes_only_reviewable_locator_revision_pin():
    m,a=synthetic();original=deepcopy(m)
    out=f.generation_postimages(m,a)
    assert m==original and len(out)==2
    for old,new in zip(m['before_links'],out):
        assert {k for k in new if new[k]!=old.get(k)}=={'component_locator','revision','pinned_claim_revision'}
        assert new['pinned_claim_revision']==3 and new['revision']==2

@pytest.mark.parametrize('fault',['duplicate','omit','foreign','role','claim','unresolved','span','target','evidence','state'])
def test_incomplete_or_semantic_changes_refused(fault):
    m,a=synthetic();s=a['selections'][0]
    if fault=='duplicate':a['selections'][1]=deepcopy(s)
    elif fault=='omit':a['selections'].pop()
    elif fault=='foreign':s['viewpoint_claim_link_id']='foreign'
    elif fault=='role':s['link_type']='supports'
    elif fault=='claim':s['claim_id']='foreign'
    elif fault=='unresolved':s['diagnosis']='unresolved'
    elif fault=='span':s['spans'][0]['exact_text']='fabricated'
    elif fault=='target':s['viewpoint_id']='foreign'
    elif fault=='evidence':s['evidence_bindings']=[]
    elif fault=='state':s['effective_state']='retired'
    with pytest.raises(Exception):f.generation_postimages(m,a)

@pytest.mark.parametrize('stage',['proposal','initial_review','correction','final_review'])
def test_generation_prompt_isolated_and_original_models_preserved(stage):
    old=f.adapter(stage);new=f.adapter(stage,generation=True)
    assert old.model_id==new.model_id and old.backend==new.backend
    assert old.prompt_sha256!=new.prompt_sha256

def real_material():
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.cvp_sql_flow import binding
    load_dotenv('.env');store=PostgresKnowledgeStore()
    return store,f.prepare_material(store,RUN,binding(ROOT/'g125-reviewed-claim-link-generation-direction.json'))

@pytest.mark.skipif(os.getenv('WKP357_LINK_GENERATION_SAVED')!='1',reason='explicit current production read-only proof')
def test_actual_source_claim_audit_and_complete_current_graph_immutable():
    from backend.pipeline.source_reconciliation import file_sha256
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    store,m=real_material();group=store.get_cvp_group(RUN)
    root=Path(read_artifact(m['authorization']['path'])['source_package']['path']).parent
    files={p:file_sha256(p) for p in root.rglob('*') if p.is_file()}
    assert len(m['before_links'])==2 and len(m['viewpoint_ids'])==4 and len(m['sources'])==2
    d=read_artifact(m['authorization']['path']);f.validate_direction(d,m)
    for field in ['claim_pin','link_pins','source_change_set_id','source_reviewed_sha256','allowed_fields','claim_id']:
        bad=deepcopy(d);bad[field]='foreign';bad['artifact_sha256']=f.sha256_json({k:v for k,v in bad.items() if k!='artifact_sha256'})
        with pytest.raises((ValueError,TypeError,KeyError)):f.validate_direction(bad,m)
    for fault in ['source_answer','missing_link','new_role','new_claim','old_generation']:
        bad=deepcopy(m)
        if fault=='source_answer':bad['original_source_responses']['correction']={}
        elif fault=='missing_link':bad['before_links'].pop()
        elif fault=='new_role':bad['before_links'][0]['link_type']='supports'
        elif fault=='new_claim':bad['claim']['statement']+='foreign'
        elif fault=='old_generation':bad['before_links'][0]['pinned_claim_revision']+=1
        with pytest.raises((ValueError,TypeError,KeyError)):f.validate_direction(d,bad)
    assert group==store.get_cvp_group(RUN)
    assert files=={p:file_sha256(p) for p in root.rglob('*') if p.is_file()}

@pytest.mark.skipif(os.getenv('WKP357_LINK_GENERATION_SAVED')!='1',reason='explicit current saved graph SQL isolation')
def test_actual_six_operation_joint_transaction_late_rollback_and_cache(postgres_store,tmp_path,monkeypatch):
    from backend.api.canonical_repository.postgres_store import canonical_json,record_content_sha
    from backend.api.canonical_repository.transaction_validation import TransactionValidation
    from backend.pipeline.cvp_sql_flow import decoded_pins,pin_rows
    from backend.pipeline.source_reconciliation_runner import write
    production,m=real_material();store=postgres_store
    rows={(x['collection'],x['object_id']):x['payload'] for x in m['records']+m['provenance_templates']}
    with store.connect() as conn:
        for (col,oid),p in rows.items():
            conn.execute('INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,visibility,content_sha256,payload) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
                (col,oid,p['revision'],p.get('review_status','candidate'),p.get('visibility','internal'),record_content_sha(p),canonical_json(p)))
            if col=='viewpoint_claim_links':
                conn.execute('INSERT INTO wang_knowledge.edges(edge_collection,edge_id,from_id,to_id,relation_type,revision,payload) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
                    (col,oid,p['viewpoint_id'],p['claim_id'],p['link_type'],p['revision'],canonical_json(p)))
        meta={k:v for k,v in production.get_cvp_group(RUN).items() if k not in {'status','error'}}
        conn.execute("INSERT INTO wang_knowledge.pipeline_runs(run_id,subject_kind,subject_id,stage,trigger,status,metadata) VALUES (%s,'batch','isolated','cvp_group','cli','running',%s::jsonb)",(RUN,canonical_json(meta)))
    # Simulated complete postimages ONLY for transaction tests, never production approval.
    text=m['claim']['statement'];response={'selections':[{'viewpoint_claim_link_id':p['viewpoint_claim_link_id'],
        'claim_id':m['claim']['claim_id'],'diagnosis':'representation_revision','link_type':p['link_type'],
        'spans':[{'start_char':0,'end_char':len(text),'exact_text':text}],'reason':'ISOLATED TEST ONLY'} for p in m['before_links']]}
    p={'artifact_sha256':'test-proposal','response':response};r={'artifact_sha256':'test-review','completed_at':'2026-10-11T18:45:01Z','response':{'verdicts':[{
        'claim_id':m['claim']['claim_id'],'approved':True,'problems':[],'reason':'ISOLATED TEST ONLY'}]}}
    write(tmp_path/'materials.json',m)
    monkeypatch.setattr(f,'effective_saved',lambda *a:(p,r))
    package=f.compile_package(m,p,r,tmp_path);f.validate_package(package)
    plan=store.plan_package(package,source_kind=f.SCHEMA,transactional=True)
    assert len(plan.operations)==6
    pins=decoded_pins(m['read_pins'])
    for op in plan.operations:
        pins.setdefault((op.collection,op.object_id),None)
    previous=production.get_cvp_group(RUN)['reviewed']['evidence']
    action=store.prepare_cvp_action(RUN,action_id=f.representation_action_id(__import__('json').loads(Path(m['authorization']['path']).read_text())),
        kind='owner_link_correction',authorization=m['authorization'],materials=m)
    def review():
        return store.review_cvp_group(RUN,plan=plan,evidence={'read_pins':pin_rows(pins),'viewpoint_ids':m['viewpoint_ids'],
            'pending_cvp_management_record_keys':previous['pending_cvp_management_record_keys'],
            'package':{'sha256':f.sha256_json(package),'payload':package},'authorization':m['authorization'],
            'backup':{'isolated_test_only':True},'sources':[{'path':s['path'],'file_sha256':s['file_sha256']} for s in m['sources']],
            'approval':{'isolated_test_only':True},'action':action})
    before={k:store.get_record_state(*k) for k in rows};seal=review();guard=store._assert_transaction_edges_and_reviews
    monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',lambda *a:(_ for _ in ()).throw(ValueError('injected late rollback')))
    with pytest.raises(ValueError,match='injected late rollback'):
        store.apply_plan(plan,transaction_context=TransactionValidation(RUN,seal,pins),metadata={'reason':'isolated generation','actor':'pytest'})
    assert all(store.get_record_state(*k)==v for k,v in before.items())
    monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',guard);seal=review()
    result=store.apply_plan(plan,transaction_context=TransactionValidation(RUN,seal,pins),metadata={'reason':'isolated generation','actor':'pytest'})
    history=store.get_change_history(result['change_set_id']);assert len(history['operations'])==6
    for k,v in before.items():
        if k[0]!='viewpoint_claim_links' or k[1] not in {p['viewpoint_claim_link_id'] for p in m['before_links']}:
            assert store.get_record_state(*k)==v
    # Already committed transaction cannot replay a second generation.
    again=store.apply_plan(plan,transaction_context=TransactionValidation(RUN,seal,pins),metadata={'reason':'isolated generation','actor':'pytest'})
    assert again['change_set_id']==result['change_set_id']
