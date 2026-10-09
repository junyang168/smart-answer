from copy import deepcopy
import json
import os
from pathlib import Path
import uuid

import pytest

from backend.api.canonical_repository.postgres_store import canonical_json, record_content_sha
from backend.pipeline import cvp_link_retirement as flow
from backend.pipeline.cvp_sql_flow import binding
from backend.pipeline.source_reconciliation_runner import write
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store

ROOT=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
AUTH=ROOT/'owner-g093-reversed-support-retirement.json'
SAVED=ROOT/'execution/CVPG-164d6b5cc392e4333d9ed872dcde954da1678b76007aa83e2795d8dc195fcf2e/target-selection/a438954d5a2969d5157ebfa4bd00a2f8504a32c32f6cf14bc2347c8969acfd7d/packet.json'


def fixture():
    d=json.loads(AUTH.read_text()); packet=json.loads((Path(d['adjudication_root'])/'packet.json').read_text())
    item=packet['items'][0]; old=next(l for l in item['connections'] if l['viewpoint_claim_link_id']==d['link_id'])
    kept=next(l for l in item['connections'] if l['viewpoint_claim_link_id']==d['retained_link_id'])
    return {'run_id':d['run_id'],'campaign_sha256':d['campaign_sha256'],'before_link':old,
            'retained_equivalent_link':kept,'adjudication_packet_sha256':packet['artifact_sha256'],
            'original_reports':d['original_reports'],'records':[]},d


def test_actual_application_is_retired_not_deleted_or_rewritten():
    m,d=fixture(); before=deepcopy(m)
    flow.validate_material(m,d)
    assert m==before
    assert m['before_link']['component_locator']['statement_component']=='因此不必擔心或議論。'
    assert d['human_content_approval'] is True


@pytest.mark.parametrize('field',['run_id','campaign_sha256','link_id','before_link_sha256','retained_link_id',
    'retained_link_sha256','adjudication_packet_sha256','original_reports','disposition','authorized_by',
    'human_content_approval','automatic_future_repairs_authorized','authorization_quote'])
def test_no_scope_drift_or_future_permission(field):
    m,d=fixture(); d[field]='' if field=='authorization_quote' else 'wrong'
    with pytest.raises(ValueError):flow.validate_material(m,d)


@pytest.mark.parametrize('field,value',[('link_type','equivalent_component'),('effective_state','held'),
                                      ('supporting_relation_ids',['relation'])])
def test_no_equivalent_hold_or_relation_retirement(field,value):
    m,d=fixture(); m['before_link'][field]=value;d['before_link_sha256']=flow.sha256_json(m['before_link'])
    with pytest.raises(ValueError):flow.validate_material(m,d)


@pytest.mark.skipif(os.environ.get('WKP_CVP_REAL_ACCEPTANCE')!='1',reason='explicit isolated SQL acceptance')
@pytest.mark.parametrize('late_failure',[False,True])
def test_actual_saved_retirement_single_write_and_late_rollback(postgres_store,tmp_path,monkeypatch,late_failure):
    store=postgres_store; saved=json.loads(SAVED.read_text())
    monkeypatch.setattr('backend.pipeline.viewpoint_backup_archive.catalog',lambda p:'isolated-test-catalog')
    with store.connect() as conn:
        for r in saved['context_records']:
            p=r['payload']
            conn.execute('INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,visibility,content_sha256,payload) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
                (r['collection'],r['object_id'],p['revision'],p.get('review_status','candidate'),p.get('visibility','internal'),record_content_sha(p),canonical_json(p)))
            if r['collection']=='viewpoint_claim_links':
                conn.execute('INSERT INTO wang_knowledge.edges(edge_collection,edge_id,from_id,to_id,relation_type,revision,payload) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
                    (r['collection'],r['object_id'],p['viewpoint_id'],p['claim_id'],p['link_type'],p['revision'],canonical_json(p)))
    d=json.loads(AUTH.read_text()); cid=store.get_record('viewpoint_claim_links',d['link_id'])['claim_id']
    campaign=store.initialize_cvp_campaign({'accepted_grouping_sha256':uuid.uuid4().hex*2,
        'groups':[{'sequence':1,'group_key':'isolated','claim_ids':[cid]}]})
    run=store.prepare_cvp_group(campaign_sha256=campaign,group_key='isolated',sequence=1,claim_ids=[cid],materials={'test':True})
    with store.connect() as conn:
        conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=metadata||%s::jsonb WHERE run_id=%s',
            (canonical_json({'reviewed':{'evidence':{'viewpoint_ids':['CV-a797c5fbc4c09e117142']}}}),run))
    d.update(run_id=run,campaign_sha256=campaign);auth=tmp_path/'direction.json';write(auth,d)
    class Ports:
        def adapter(self,*args):raise AssertionError('human disposition must not call any model')
        def backup(self,root):
            p=root/'test.dump';p.write_bytes(b'ISOLATED TEST ONLY')
            return {'dump':binding(p),'restore_catalog_nonempty':True,'catalog_sha256':'isolated-test-catalog','restore_list_exit_code':0}
    old=store.get_record_state('viewpoint_claim_links',d['link_id']);kept=store.get_record('viewpoint_claim_links',d['retained_link_id'])
    claim=store.get_record('claims',cid);guard=store._assert_transaction_edges_and_reviews
    if late_failure:
        def refuse(*a):raise ValueError('injected late retirement validation')
        monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',refuse)
        with pytest.raises(ValueError,match='injected late'):
            flow.execute(store,run,authorization=binding(auth),root=tmp_path/'action',ports=Ports())
        assert store.get_record_state('viewpoint_claim_links',d['link_id'])==old
        monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',guard)
    result=flow.execute(store,run,authorization=binding(auth),root=tmp_path/'action',ports=Ports())
    h=store.get_change_history(result['change_set_id']); assert len(h['operations'])==1
    op=h['operations'][0]; assert op['operation']=='retire' and op['before_sha256']==op['after_sha256']
    actual=store.get_record_state('viewpoint_claim_links',d['link_id'])
    assert actual['retired'] and actual['revision']==old['revision']+1 and actual['payload']==old['payload']
    assert op['after']=={**old['payload'],'revision':old['revision']+1}
    assert store.get_record('viewpoint_claim_links',d['retained_link_id'])==kept and store.get_record('claims',cid)==claim
    assert flow.execute(store,run,authorization=binding(auth),root=tmp_path/'action',ports=Ports())['status']=='already_applied'
