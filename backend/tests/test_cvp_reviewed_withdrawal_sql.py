"""Live graph is read-only; all simulated approval and mutations are isolated."""
import json
import os
import uuid
from pathlib import Path

import pytest
from backend.pipeline import cvp_link_replacement as flow
from backend.pipeline.cvp_sql_flow import binding
from backend.pipeline.source_reconciliation_runner import write
from backend.api.canonical_repository.postgres_store import canonical_json, record_content_sha
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store


@pytest.mark.skipif(os.environ.get('WKP_CVP_REAL_WITHDRAWAL') != '1', reason='explicit saved graph / isolated SQL')
@pytest.mark.parametrize('late_failure', [False, True])
def test_saved_single_retirement_exact_payload_and_rollback(postgres_store, tmp_path, monkeypatch, late_failure):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    load_dotenv('.env')
    auth = Path(os.environ['WKP_CVP_WITHDRAWAL_DIRECTION'])
    d = json.loads(auth.read_text())
    m = flow.prepare_material(PostgresKnowledgeStore(), d['run_id'], binding(auth))
    s = postgres_store
    with s.connect() as conn:
        for row in m['records']:
            p = row['payload']
            conn.execute('INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,visibility,content_sha256,payload) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
                (row['collection'],row['object_id'],p['revision'],p.get('review_status','candidate'),p.get('visibility','internal'),record_content_sha(p),canonical_json(p)))
            if row['collection']=='viewpoint_claim_links':
                conn.execute('INSERT INTO wang_knowledge.edges(edge_collection,edge_id,from_id,to_id,relation_type,revision,payload) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
                    (row['collection'],row['object_id'],p['viewpoint_id'],p['claim_id'],p['link_type'],p['revision'],canonical_json(p)))
    cid=m['withdrawal_links'][0]['claim_id']
    campaign=s.initialize_cvp_campaign({'accepted_grouping_sha256':uuid.uuid4().hex*2,'groups':[{'sequence':1,'group_key':'isolated','claim_ids':[cid]}]})
    run=s.prepare_cvp_group(campaign_sha256=campaign,group_key='isolated',sequence=1,claim_ids=[cid],materials={'test':True})
    with s.connect() as conn:
        conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=metadata||%s::jsonb WHERE run_id=%s',
            (canonical_json({'reviewed':{'evidence':{'viewpoint_ids':[m['withdrawal_links'][0]['viewpoint_id']]}}}),run))
    d.update(run_id=run,campaign_sha256=campaign); local=tmp_path/'direction.json';write(local,d)
    original=flow.adapter
    def isolated_adapter(stage, **kwargs):
        obj=original(stage,**kwargs);obj.max_request_bytes=1000000;return obj
    monkeypatch.setattr(flow,'adapter',isolated_adapter)
    class Ports:
        def adapter(self,stage):
            a=isolated_adapter(stage,withdrawal_only=True);a._client.executable=None
            a.generate=lambda p: ({'selection':{'claim_id':cid,'diagnosis':'withdraw_only','reason':'ISOLATED TEST ONLY','old_target_reason':'ISOLATED TEST ONLY'}} if stage=='proposal' else
                {'verdicts':[{'claim_id':cid,'approved':True,'problems':[],'reason':'ISOLATED TEST ONLY'}]})
            return a
        def backup(self,root):
            p=root/'test.dump';p.write_bytes(b'ISOLATED TEST ONLY')
            return {'dump':binding(p),'restore_catalog_nonempty':True,'catalog_sha256':'test','restore_list_exit_code':0}
    monkeypatch.setattr('backend.pipeline.cvp_sql_actions.initialize_subscription_runtime',lambda p:None)
    monkeypatch.setattr('backend.pipeline.viewpoint_backup_archive.catalog',lambda p:'test')
    before={(r['collection'],r['object_id']):s.get_record_state(r['collection'],r['object_id']) for r in m['records']}
    guard=s._assert_transaction_edges_and_reviews
    if late_failure:
        monkeypatch.setattr(s,'_assert_transaction_edges_and_reviews',lambda *a:(_ for _ in ()).throw(ValueError('late rollback')))
        with pytest.raises(ValueError,match='late rollback'):
            flow.execute(s,run,authorization=binding(local),root=tmp_path/'action',ports=Ports())
        assert all(s.get_record_state(*k)==v for k,v in before.items())
        monkeypatch.setattr(s,'_assert_transaction_edges_and_reviews',guard)
    result=flow.execute(s,run,authorization=binding(local),root=tmp_path/'action',ports=Ports())
    ops=s.get_change_history(result['change_set_id'])['operations']
    assert len(ops)==1 and ops[0]['operation']=='retire'
    key=('viewpoint_claim_links',d['replacement_link_id'])
    for k,v in before.items():
        now=s.get_record_state(*k)
        if k==key:assert now['retired'] and now['payload']==v['payload'] and now['content_sha256']==v['content_sha256']
        else:assert now==v
    assert flow.execute(s,run,authorization=binding(local),root=tmp_path/'action',ports=Ports())['status']=='already_applied'
