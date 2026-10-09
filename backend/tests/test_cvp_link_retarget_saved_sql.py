"""Real saved g093 graph, simulated models, disposable PostgreSQL only."""
from copy import deepcopy
import os
from pathlib import Path
import uuid

import pytest

from backend.api.canonical_repository.postgres_store import canonical_json,record_content_sha
from backend.pipeline import cvp_link_retarget as flow
from backend.pipeline.cvp_sql_flow import binding
from backend.pipeline.source_reconciliation_runner import write
from backend.pipeline.viewpoint_resolution_runtime import read_artifact
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store
from backend.tests.test_cvp_link_target_selection import CLAIM,OLD,NEW,LINK,selected

pytestmark=pytest.mark.skipif(os.environ.get('WKP_CVP_REAL_ACCEPTANCE')!='1',reason='explicit saved shape/disposable SQL acceptance')
PACKET=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-164d6b5cc392e4333d9ed872dcde954da1678b76007aa83e2795d8dc195fcf2e/target-selection/a438954d5a2969d5157ebfa4bd00a2f8504a32c32f6cf14bc2347c8969acfd7d/packet.json')


@pytest.mark.parametrize('late_failure',[False,True])
def test_real_saved_graph_exact_three_record_transaction_and_recovery(postgres_store,tmp_path,monkeypatch,late_failure):
    store=postgres_store;saved=read_artifact(PACKET)
    monkeypatch.setattr('backend.pipeline.viewpoint_backup_archive.catalog',lambda p:'isolated-test-catalog')
    with store.connect() as conn:
        for row in saved['context_records']:
            p=row['payload']
            conn.execute('''INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,visibility,content_sha256,payload)
                            VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)''',
                         (row['collection'],row['object_id'],p['revision'],p.get('review_status','candidate'),
                          p.get('visibility','internal'),record_content_sha(p),canonical_json(p)))
            if row['collection']=='viewpoint_claim_links':
                conn.execute('''INSERT INTO wang_knowledge.edges(edge_collection,edge_id,from_id,to_id,relation_type,revision,payload)
                                VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)''',
                             (row['collection'],row['object_id'],p['viewpoint_id'],p['claim_id'],p['link_type'],p['revision'],canonical_json(p)))
    campaign=store.initialize_cvp_campaign({'accepted_grouping_sha256':uuid.uuid4().hex*2,
        'groups':[{'sequence':1,'group_key':'isolated','claim_ids':[CLAIM]}]})
    run=store.prepare_cvp_group(campaign_sha256=campaign,group_key='isolated',sequence=1,claim_ids=[CLAIM],materials={'fixture':True})
    with store.connect() as conn:
        conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=metadata||%s::jsonb WHERE run_id=%s',
                     (canonical_json({'reviewed':{'evidence':{'viewpoint_ids':[OLD]}}}),run))
    packet=flow.selection.build_packet(store,link_id=LINK,candidate_viewpoint_ids=[NEW])
    report=tmp_path/'original-rejected.json';write(report,{'fixture':'original rejection retained, not approval'})
    material={'comparison':packet,'run_id':run,'campaign_sha256':campaign,'original_reports':[binding(report)]}
    from backend.tests.test_cvp_link_retarget import direction_fixture
    direction=direction_fixture(material);auth=tmp_path/'direction.json';write(auth,direction)
    calls=[]
    class Ports:
        def adapter(self,stage):
            a=flow.model_adapter(stage);a._client.executable=None
            def generate(p):
                calls.append(stage)
                return selected() if stage=='proposal' else {'verdicts':[{'claim_id':CLAIM,'approved':True,
                    'problems':[],'reason':'SIMULATED isolated complete postimage review, not production approval'}]}
            a.generate=generate;return a
        def backup(self,root):
            path=root/'isolated.dump';path.write_bytes(b'ISOLATED TEST ONLY')
            return {'dump':binding(path),'restore_catalog_nonempty':True,
                    'restore_list_exit_code':0,'catalog_sha256':'isolated-test-catalog'}
    before=store.get_record('viewpoint_claim_links',LINK);claim=store.get_record('claims',CLAIM)
    guard=store._assert_transaction_edges_and_reviews
    if late_failure:
        def refuse(*a):raise ValueError('injected late retarget transaction failure')
        monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',refuse)
        with pytest.raises(ValueError,match='injected late'):
            flow.execute(store,run,authorization=binding(auth),root=tmp_path/'action',ports=Ports())
        assert store.get_record('viewpoint_claim_links',LINK)==before
        assert store.get_cvp_group(run)['checkpoint']=='reviewed'
        monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',guard)
    result=flow.execute(store,run,authorization=binding(auth),root=tmp_path/'action',ports=Ports())
    assert result['status']=='applied' and calls==['proposal','initial_review']
    history=store.get_change_history(result['change_set_id'])
    assert len(history['operations'])==3
    after=store.get_record('viewpoint_claim_links',LINK)
    assert after['revision']==before['revision']+1 and after['viewpoint_id']==NEW and after['link_type']=='supports'
    assert after['evidence_bindings']==before['evidence_bindings'] and store.get_record('claims',CLAIM)==claim
    for op in history['operations']:
        assert store.get_record(op['collection'],op['object_id'])==op['after']
    assert flow.execute(store,run,authorization=binding(auth),root=tmp_path/'action',ports=Ports())['status']=='already_applied'
    assert calls==['proposal','initial_review']
    assert store.get_cvp_group(run)['reviewed']['evidence']['viewpoint_ids']==sorted([OLD,NEW])
