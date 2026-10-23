from copy import deepcopy
from pathlib import Path
import uuid
import pytest

from backend.api.canonical_repository.postgres_store import sha256_json,canonical_json,record_content_sha
from backend.pipeline import claim_relation_retirement as r
from backend.pipeline.source_reconciliation_runner import write
from backend.pipeline.cvp_sql_flow import binding
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store


def material():
    relation={'relation_id':'AR-test','from_id':'E-a','to_id':'E-b','relation_type':'supports','revision':1,'review_status':'candidate','visibility':'internal','schema_version':1}
    return {'run_id':'run','campaign_sha256':'a'*64,'before_relation':relation,
            'sources':[],'read_pins':[],
            'records':[{'collection':c,'object_id':i,'payload':p,'retired':False} for c,i,p in
                [('knowledge_relations','AR-test',relation),('evidence_steps','E-a',{'evidence_step_id':'E-a'}),('evidence_steps','E-b',{'evidence_step_id':'E-b'})]],
            'pending_cvp_claim_ids':['CL-test'],'pending_cvp_management_record_keys':['knowledge_relations/AR-test']}


def direction(m):
    d={'schema_version':r.SCHEMA,'authorized_by':'owner','authorization_quote':'这个应该 retire',
       'disposition':'logical_retirement_only','new_model_calls':0,'relation_id':'AR-test',
       'run_id':m['run_id'],'campaign_sha256':m['campaign_sha256'],'material_sha256':sha256_json(m)}
    return {**d,'artifact_sha256':sha256_json(d)}


def test_exact_human_retire_keeps_endpoints_and_pending():
    m=material();r.validate_material(m,direction(m))


@pytest.mark.parametrize('mutation',['seal','scope','hold','retired','endpoint','duplicate','model'])
def test_retirement_rejects_nonexact_scope(mutation):
    m=material();d=direction(m)
    if mutation=='seal':d['artifact_sha256']='f'*64
    elif mutation=='scope':d['relation_id']='other'
    elif mutation=='hold':m['records'][1]['payload']['review_status']='human_hold'
    elif mutation=='retired':m['records'][1]['retired']=True
    elif mutation=='endpoint':m['records'].pop()
    elif mutation=='duplicate':m['records'].append(deepcopy(m['records'][0]))
    elif mutation=='model':d['new_model_calls']=1
    if mutation not in ['seal','scope','model']:d=direction(m)
    else:d['artifact_sha256']=sha256_json({k:v for k,v in d.items() if k!='artifact_sha256'}) if mutation!='seal' else d['artifact_sha256']
    with pytest.raises(ValueError):r.validate_material(m,d)


@pytest.mark.parametrize('late_failure',[False,True])
def test_real_sql_exact_retirement_rollback_and_cached_no_replay(postgres_store,tmp_path,monkeypatch,late_failure):
    s=postgres_store;m=material()
    for row in m['records']:
        p=row['payload'];p.setdefault('revision',1);p.setdefault('schema_version',1);p.setdefault('review_status','candidate');p.setdefault('visibility','internal')
    with s.connect() as conn:
        for row in m['records']:
            p=row['payload']
            conn.execute('INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,visibility,content_sha256,payload) VALUES (%s,%s,1,%s,%s,%s,%s::jsonb)',
                         (row['collection'],row['object_id'],p['review_status'],p['visibility'],record_content_sha(p),canonical_json(p)))
        p=m['before_relation']
        conn.execute('INSERT INTO wang_knowledge.edges(edge_collection,edge_id,from_id,to_id,relation_type,review_status,revision,payload) VALUES (%s,%s,%s,%s,%s,%s,1,%s::jsonb)',
                     ('knowledge_relations',p['relation_id'],p['from_id'],p['to_id'],p['relation_type'],p['review_status'],canonical_json(p)))
    campaign=s.initialize_cvp_campaign({'accepted_grouping_sha256':uuid.uuid4().hex*2,'groups':[{'group_key':'g','sequence':1,'claim_ids':['CL-test']}]})
    run=s.prepare_cvp_group(campaign_sha256=campaign,group_key='g',sequence=1,claim_ids=['CL-test'],materials={'fixture':True})
    m['run_id']=run;m['campaign_sha256']=campaign
    source=tmp_path/'source.txt';source.write_text('own original source',encoding='utf-8')
    m['sources']=[binding(source)]
    from backend.pipeline.cvp_sql_flow import pins,pin_rows
    m['read_pins']=pin_rows(pins({(row['collection'],row['object_id']):s.get_record_state(row['collection'],row['object_id']) for row in m['records']}))
    auth=tmp_path/'direction.json';write(auth,{k:v for k,v in direction(m).items() if k!='artifact_sha256'})
    monkeypatch.setattr(r,'prepare_material',lambda *a:m)
    import backend.pipeline.cvp_sql_commit as flow
    dump=tmp_path/'dump';dump.write_bytes(b'test backup')
    monkeypatch.setattr(flow,'verified_group_backup',lambda *a:{'restore_catalog_nonempty':True,'dump':binding(dump)})
    before=[s.get_record_state(x['collection'],x['object_id']) for x in m['records']]
    original=s._assert_transaction_edges_and_reviews
    if late_failure:
        monkeypatch.setattr(s,'_assert_transaction_edges_and_reviews',lambda *a:(_ for _ in ()).throw(ValueError('late guard')))
        with pytest.raises(ValueError,match='late guard'):r.execute(s,run,authorization=binding(auth),root=tmp_path/'action',ports=None)
        assert [s.get_record_state(x['collection'],x['object_id']) for x in m['records']]==before
        monkeypatch.setattr(s,'_assert_transaction_edges_and_reviews',original)
    result=r.execute(s,run,authorization=binding(auth),root=tmp_path/'action',ports=None)
    state=s.get_record_state('knowledge_relations','AR-test')
    assert state['retired'] and state['revision']==2 and state['payload']==before[0]['payload']
    assert [s.get_record_state(x['collection'],x['object_id']) for x in m['records'][1:]]==before[1:]
    history=s.get_change_history(result['change_set_id']);assert len(history['operations'])==1
    replay=r.execute(s,run,authorization=binding(auth),root=tmp_path/'action',ports=None)
    assert replay['status']=='already_applied'
    assert len(s.get_change_history(result['change_set_id'])['operations'])==1
    assert s.get_cvp_group(run)['reviewed']['evidence']['pending_cvp_management_record_keys']==m['pending_cvp_management_record_keys']
