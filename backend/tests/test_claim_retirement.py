from copy import deepcopy
import json
import os
from pathlib import Path
import uuid
import pytest

from backend.pipeline import claim_retirement as flow
from backend.api.canonical_repository.postgres_store import canonical_json, record_content_sha
from backend.pipeline.cvp_sql_flow import binding, pins, pin_rows
from backend.pipeline.source_reconciliation_runner import write
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store


def fixture():
    records={}
    def row(c,i,p):
        records[c,i]={'collection':c,'object_id':i,'payload':p,'revision':1,'retired':False,'content_sha256':record_content_sha(p)}
    row('claims','claim',{'claim_id':'claim','revision':1,'statement':'important','review_status':'approved','occurrences':[{'source_id':'source'}]})
    row('claims','replacement',{'claim_id':'replacement','revision':1,'statement':'actual teaching','review_status':'approved','occurrences':[{'source_id':'source'}]})
    row('viewpoint_claim_links','old',{'viewpoint_claim_link_id':'old','claim_id':'claim','viewpoint_id':'view','link_type':'supports','effective_state':'active'})
    row('viewpoint_claim_links','kept',{'viewpoint_claim_link_id':'kept','claim_id':'replacement','viewpoint_id':'view','link_type':'equivalent_component','effective_state':'active'})
    row('evidence_steps','evidence',{'evidence_step_id':'evidence','produced_claim_ids':['claim']})
    m={'run_id':'run','campaign_sha256':'campaign','claim_id':'claim','replacement_claim_id':'replacement','link_ids':['old'],
       'evidence_ids':['evidence'],'before_pins':[], 'read_pins':pin_rows(pins(records)), 'records':list(records.values()),'historical_candidates':[],'sources':[]}
    m['before_pins']=pin_rows({k:v for k,v in pins(records).items() if k != ('viewpoint_claim_links','kept')})
    d={k:deepcopy(m[k]) for k in ('run_id','campaign_sha256','claim_id','replacement_claim_id','link_ids','evidence_ids','before_pins')}
    d.update(schema_version=flow.SCHEMA,authorized_by='owner',human_exact_disposition=True,
             disposition='retire_claim_and_all_active_links',automatic_future_retirements_authorized=False,
             authorization_quote='retire',reason='rhetorical emphasis already captured')
    return m,d


def test_exact_logical_retirement_preserves_input_and_complete_keys():
    m,d=fixture();original=deepcopy(m);flow.validate_material(m,d)
    assert m==original
    assert flow.retirement_keys(m)==(('claims','claim'),('evidence_steps','evidence'),('viewpoint_claim_links','old'))


@pytest.mark.parametrize('field',['run_id','campaign_sha256','claim_id','replacement_claim_id','link_ids','before_pins',
    'authorized_by','human_exact_disposition','disposition','automatic_future_retirements_authorized','authorization_quote','reason','schema_version'])
def test_authority_scope_drift_rejected(field):
    m,d=fixture();d[field]=''
    with pytest.raises(ValueError):flow.validate_material(m,d)


@pytest.mark.parametrize('change',['hold','last_member','retired_replacement','missing_link','changed_pin','relation_bound','candidate','shared_evidence','foreign_source'])
def test_safety_counterexamples(change):
    m,d=fixture()
    if change=='hold':m['records'][0]['payload']['review_status']='human_hold'
    if change=='last_member':m['records'].pop(3)
    if change=='retired_replacement':m['records'][1]['retired']=True
    if change=='missing_link':m['records'].pop(2)
    if change=='changed_pin':m['records'][0]['revision']=2
    if change=='relation_bound':m['records'][2]['payload']['supporting_relation_ids']=['relation']
    if change=='candidate':m['historical_candidates']=[{'payload':{'identity_candidate_id':'foreign'}}]
    if change=='shared_evidence':m['records'][4]['payload']['produced_claim_ids'].append('replacement')
    if change=='foreign_source':m['records'][1]['payload']['occurrences']=[{'source_id':'foreign'}]
    with pytest.raises(ValueError):flow.validate_material(m,d)


def test_original_campaign_member_not_silently_removed():
    m,d=fixture()
    class Store:
        def get_cvp_group(self,run):return {'campaign_sha256':'campaign'}
        def cvp_campaign_progress(self,campaign):return [{'claim_ids':['claim']}]
        def claim_links(self,*a):raise AssertionError('must fail before reading/rewriting original members')
    with pytest.raises(ValueError,match='original campaign Claim'):
        flow.prepare_material(Store(),'run',d)


def test_package_no_extra_fields_or_writes(tmp_path):
    m,d=fixture();auth=tmp_path/'direction.json';write(auth,d);write(tmp_path/'materials.json',m)
    p=flow.package_for(m,binding(auth),tmp_path);assert flow.validate_package(p)==flow.retirement_keys(m)
    p['claims']=[]
    with pytest.raises(ValueError):flow.validate_package(p)


ROOT=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
@pytest.mark.skipif(os.environ.get('WKP_CLAIM_RETIREMENT_REAL')!='1',reason='explicit real saved input / isolated SQL')
@pytest.mark.parametrize('late_failure',[False,True])
def test_actual_saved_commit_rollback_and_once(postgres_store,tmp_path,monkeypatch,late_failure):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    load_dotenv('.env');live=PostgresKnowledgeStore();d=json.loads((ROOT/'owner-g109-claim-retirement.json').read_text())
    m=flow.prepare_material(live,d['run_id'],d);store=postgres_store
    with store.connect() as conn:
        for r in m['records']:
            p=r['payload']
            conn.execute('INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,visibility,content_sha256,payload,retired_at) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,CASE WHEN %s THEN now() ELSE NULL END)',
                (r['collection'],r['object_id'],r['revision'],p.get('review_status','candidate'),p.get('visibility','internal'),r['content_sha256'],canonical_json(p),r['retired']))
            if r['collection']=='viewpoint_claim_links' and not r['retired']:
                conn.execute('INSERT INTO wang_knowledge.edges(edge_collection,edge_id,from_id,to_id,relation_type,revision,payload) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
                    (r['collection'],r['object_id'],p['viewpoint_id'],p['claim_id'],p['link_type'],r['revision'],canonical_json(p)))
    campaign=store.initialize_cvp_campaign({'accepted_grouping_sha256':uuid.uuid4().hex*2,
        'groups':[{'sequence':1,'group_key':'isolated','claim_ids':[d['replacement_claim_id']]}]})
    run=store.prepare_cvp_group(campaign_sha256=campaign,group_key='isolated',sequence=1,claim_ids=[d['replacement_claim_id']],materials={'test':True})
    with store.connect() as conn:
        conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=metadata||%s::jsonb WHERE run_id=%s',
            (canonical_json({'reviewed':{'evidence':{'viewpoint_ids':['CV-cb1ffc7cb499756c5de0']}}}),run))
    d.update(run_id=run,campaign_sha256=campaign);auth=tmp_path/'direction.json';write(auth,d)
    monkeypatch.setattr('backend.pipeline.viewpoint_backup_archive.catalog',lambda p:'test-catalog')
    class Ports:
        def adapter(self,*a):raise AssertionError('human retirement must not call model')
        def backup(self,root):
            p=root/'test.dump';p.write_bytes(b'isolated only')
            return {'dump':binding(p),'restore_catalog_nonempty':True,'catalog_sha256':'test-catalog','restore_list_exit_code':0}
    before={k:store.get_record_state(*k) for k in flow.retirement_keys(m)};guard=store._assert_transaction_edges_and_reviews
    if late_failure:
        def fail(*a):raise ValueError('late retirement rollback')
        monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',fail)
        with pytest.raises(ValueError,match='late retirement rollback'):
            flow.execute(store,run,authorization=binding(auth),root=tmp_path/'action',ports=Ports())
        assert all(store.get_record_state(*k)==r for k,r in before.items())
        monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',guard)
    result=flow.execute(store,run,authorization=binding(auth),root=tmp_path/'action',ports=Ports())
    ops=store.get_change_history(result['change_set_id'])['operations'];assert len(ops)==3
    for k,r in before.items():
        current=store.get_record_state(*k);assert current['retired'] and current['revision']==r['revision']+1
        assert current['payload']==r['payload'] and current['content_sha256']==r['content_sha256']
    for row in m['records']:
        key=(row['collection'],row['object_id'])
        if key not in before:assert store.get_record_state(*key)=={k:row[k] for k in ('payload','revision','retired','content_sha256')}
    assert flow.execute(store,run,authorization=binding(auth),root=tmp_path/'action',ports=Ports())['status']=='already_applied'
