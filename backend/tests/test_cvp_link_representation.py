from copy import deepcopy
import pytest
from backend.pipeline import cvp_link_representation as f
import json
import uuid
from pathlib import Path
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store

def material():
    claim={'claim_id':'C','revision':3,'statement':'查验并执行标准，不自行制定。'}
    old={'viewpoint_claim_link_id':'L','claim_id':'C','revision':4,'viewpoint_id':'V',
        'link_type':'supports','effective_state':'active','pinned_claim_revision':3,
        'component_locator':{'statement_component':'查验'},'evidence_bindings':[{'own':'E'}],
        'decision_id':'D','validated_against_viewpoint_revision_id':'R'}
    return {'before_link':old,'item':{'claim':claim}}

def answer():
    return {'selection':{'claim_id':'C','diagnosis':'representation_revision',
        'link_type':'equivalent_component','spans':[{'start_char':0,'end_char':7,'exact_text':'查验并执行标准'}],
        'reason':'TEST ONLY: conclusion rather than argument'}}

def test_only_role_locator_and_revision_change():
    m=material();original=deepcopy(m);new=f.postimage(m,answer())
    assert m==original
    assert {k for k in new if new[k]!=m['before_link'].get(k)}=={'link_type','component_locator','revision'}
    assert new['revision']==5 and new['evidence_bindings']==m['before_link']['evidence_bindings']

@pytest.mark.parametrize('change',[{'claim_id':'OTHER'},{'diagnosis':'unresolved'},
    {'spans':[]},{'link_type':'equivalent_full'},
    {'spans':[{'start_char':-1,'end_char':7,'exact_text':'查验并执行标准'}]},
    {'spans':[{'start_char':0,'end_char':99,'exact_text':'查验并执行标准'}]},
    {'spans':[{'start_char':0,'end_char':7,'exact_text':'invented'}]},
    {'spans':[{'start_char':0,'end_char':7,'exact_text':'查验并执行标准'},
              {'start_char':0,'end_char':2,'exact_text':'查验'}]},
    {'viewpoint_id':'OTHER'},{'evidence_bindings':[]},{'effective_state':'retired'}])
def test_foreign_unresolved_empty_partial_full_bad_spans_extra_fields_refused(change):
    a=answer();a['selection'].update(change)
    with pytest.raises(Exception):f.postimage(material(),a)

def test_full_requires_exact_full_span():
    m=material();a=answer();text=m['item']['claim']['statement']
    a['selection'].update(link_type='equivalent_full',spans=[{'start_char':0,'end_char':len(text),'exact_text':text}])
    assert f.postimage(m,a)['component_locator'] is None

@pytest.mark.parametrize('stage',['proposal','initial_review','correction','final_review'])
def test_stage_models_remain_subscription_high(stage):
    a=f.adapter(stage);proposal=stage in {'proposal','correction'}
    assert a.model_id==('gpt-6.1-sol' if proposal else 'claude-opus-5-5')
    assert a.backend==('codex_subscription' if proposal else 'claude_subscription')

def test_direction_requires_exact_fields_and_seal():
    m={**material(),'run_id':'run','campaign_sha256':'campaign','adjudication_root':'root','original_reports':[]}
    d={'schema_version':f.DIRECTION,'run_id':'run','campaign_sha256':'campaign',
       'link_id':'L','before_link_sha256':f.sha256_json(m['before_link']),
       'adjudication_root':'root','original_reports':[],'allowed_fields':['link_type','component_locator'],
       'authorized_by':'owner','human_content_approval':False,'automatic_future_repairs_authorized':False,
       'authorization_quote':'TEST ONLY'}
    d['artifact_sha256']=f.sha256_json(d);f.validate_direction(d,m)
    for key in ['link_id','before_link_sha256','human_content_approval','allowed_fields','authorization_quote','artifact_sha256']:
        bad=deepcopy(d);bad[key]='wrong'
        with pytest.raises(ValueError):f.validate_direction(bad,m)

@pytest.mark.parametrize('late_failure',[False,True])
@pytest.mark.parametrize('correction',[False,True])
def test_real_saved_scoped_graph_isolated_three_ops_rollback_and_cached_resume(postgres_store,tmp_path,monkeypatch,late_failure,correction):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore,record_content_sha,canonical_json
    from backend.pipeline.cvp_sql_flow import binding
    from backend.pipeline.source_reconciliation_runner import write
    load_dotenv('.env')
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
    auth=root/'owner-g098-cl007-link-representation.json';d=json.loads(auth.read_text())
    m=f.prepare_material(PostgresKnowledgeStore(),d['run_id'],binding(auth));s=postgres_store
    # Real saved/current material, isolated database and simulated model answers
    # only. This is not approval of any production role/span postimage.
    rows={ (r['collection'],r['object_id']):r['payload'] for r in m['records']+m['provenance_templates']}
    with s.connect() as conn:
        for (col,oid),p in rows.items():
            conn.execute('INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,visibility,content_sha256,payload) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
                (col,oid,p['revision'],p.get('review_status','candidate'),p.get('visibility','internal'),record_content_sha(p),canonical_json(p)))
            if col=='viewpoint_claim_links':
                conn.execute('INSERT INTO wang_knowledge.edges(edge_collection,edge_id,from_id,to_id,relation_type,revision,payload) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
                    (col,oid,p['viewpoint_id'],p['claim_id'],p['link_type'],p['revision'],canonical_json(p)))
    cid=m['before_link']['claim_id'];campaign=s.initialize_cvp_campaign({'accepted_grouping_sha256':uuid.uuid4().hex*2,
        'groups':[{'sequence':1,'group_key':'isolated','claim_ids':[cid]}]})
    run=s.prepare_cvp_group(campaign_sha256=campaign,group_key='isolated',sequence=1,claim_ids=[cid],materials={'test':True})
    m.update(run_id=run,campaign_sha256=campaign);d.update(run_id=run,campaign_sha256=campaign)
    d['artifact_sha256']=f.sha256_json({k:v for k,v in d.items() if k!='artifact_sha256'})
    path=tmp_path/'direction.json';write(path,{k:v for k,v in d.items() if k!='artifact_sha256'});m['authorization']=binding(path)
    from backend.pipeline.cvp_sql_flow import pins,pin_rows
    m['read_pins']=pin_rows(pins({k:s.get_record_state(*k) for k in rows}))
    monkeypatch.setattr(f,'prepare_material',lambda *a:m)
    with s.connect() as conn:
        conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=metadata||%s::jsonb WHERE run_id=%s',
            (canonical_json({'reviewed':{'evidence':{'viewpoint_ids':[m['before_link']['viewpoint_id']]}}}),run))
    calls=[];original=f.adapter
    def test_adapter(stage):
        obj=original(stage);obj.max_request_bytes=1000000;return obj
    monkeypatch.setattr(f,'adapter',test_adapter)
    class Ports:
        def adapter(self,stage):
            a=test_adapter(stage);a._client.executable=None
            def answer(packet):
                calls.append(stage)
                if stage in {'proposal','correction'}:
                    text=m['item']['claim']['statement']
                    return {'selection':{'claim_id':cid,'diagnosis':'representation_revision','link_type':'equivalent_full',
                        'spans':[{'start_char':0,'end_char':len(text),'exact_text':text}],'reason':'ISOLATED TEST ONLY'}}
                refused=correction and stage=='initial_review'
                return {'verdicts':[{'claim_id':cid,'approved':not refused,'problems':['ISOLATED REFUSAL'] if refused else [],'reason':'ISOLATED TEST ONLY'}]}
            a.generate=answer;return a
        def backup(self,root):
            p=root/'test.dump';p.write_bytes(b'ISOLATED TEST ONLY')
            return {'dump':binding(p),'restore_catalog_nonempty':True,'catalog_sha256':'test','restore_list_exit_code':0}
    monkeypatch.setattr('backend.pipeline.viewpoint_backup_archive.catalog',lambda p:'test')
    before={k:s.get_record_state(*k) for k in rows};guard=s._assert_transaction_edges_and_reviews
    if late_failure:
        monkeypatch.setattr(s,'_assert_transaction_edges_and_reviews',lambda *a:(_ for _ in ()).throw(ValueError('injected late validation')))
        with pytest.raises(ValueError,match='injected late validation'):f.execute(s,run,authorization=binding(path),root=tmp_path/'action',ports=Ports())
        assert all(s.get_record_state(*k)==v for k,v in before.items())
        monkeypatch.setattr(s,'_assert_transaction_edges_and_reviews',guard)
    result=f.execute(s,run,authorization=binding(path),root=tmp_path/'action',ports=Ports())
    assert len(s.get_change_history(result['change_set_id'])['operations'])==3
    expected_calls=['proposal','initial_review']+(['correction','final_review'] if correction else [])
    assert calls==expected_calls
    for k,v in before.items():
        if k!=('viewpoint_claim_links',m['before_link']['viewpoint_claim_link_id']):assert s.get_record_state(*k)==v
    assert f.execute(s,run,authorization=binding(path),root=tmp_path/'action',ports=Ports())['status']=='already_applied'
    assert calls==expected_calls
