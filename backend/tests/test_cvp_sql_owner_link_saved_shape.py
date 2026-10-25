"""Actual saved g057 record shapes, simulated Opus, disposable SQL only."""
from copy import deepcopy
import json
import os
from pathlib import Path
import uuid

import pytest

from backend.api.canonical_repository.postgres_store import canonical_json,record_content_sha,sha256_json
from backend.pipeline.cvp_sql_actions import execute_owner_link_action
from backend.pipeline.cvp_sql_flow import binding
from backend.pipeline.viewpoint_resolution_runtime import write_immutable
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store

pytestmark=pytest.mark.skipif(os.environ.get('WKP_CVP_REAL_ACCEPTANCE')!='1',reason='explicit saved g057 shape acceptance')


def test_generic_owner_link_uses_shared_transaction_once_without_parent_replay(postgres_store,tmp_path,monkeypatch):
    source=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/source-reconciliation/SR-081e65104c51b5d979a73b7db8a4b0a8e522c7096a35ff7bf176365682539e5e/authorized-link-corrections/LR-83d4023b6bc067b5b68b3d95f39910529cf41d16d392dc35168095cf17eacc4d/packet.json')
    saved=json.loads(source.read_text());store=postgres_store
    rows=deepcopy(saved['context_records'])
    with store.connect() as conn:
        for row in rows:
            p=row['payload']
            conn.execute("""INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,visibility,content_sha256,payload)
                            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)""",(row['collection'],row['object_id'],p['revision'],
                            p.get('review_status','candidate'),p.get('visibility','internal'),record_content_sha(p),canonical_json(p)))
            if row['collection']=='viewpoint_claim_links':
                conn.execute("""INSERT INTO wang_knowledge.edges(edge_collection,edge_id,from_id,to_id,relation_type,revision,payload)
                                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)""",('viewpoint_claim_links',row['object_id'],p['viewpoint_id'],p['claim_id'],p['link_type'],p['revision'],canonical_json(p)))
    cid=saved['before_link']['claim_id'];vid=saved['before_link']['viewpoint_id']
    campaign=store.initialize_cvp_campaign({'accepted_grouping_sha256':uuid.uuid4().hex*2,
        'groups':[{'sequence':1,'group_key':'fixture','claim_ids':[cid]}]})
    run=store.prepare_cvp_group(campaign_sha256=campaign,group_key='fixture',sequence=1,claim_ids=[cid],materials={'fixture':True})
    # Exact saved semantic target, not a new GPT request or a replay of an old
    # parent validator. Its original physical/model history is independently
    # covered by the historical admission and existing adjudication tests.
    item=saved['item'];adjudication={'items':[item],'artifact_sha256':'a'*64}
    monkeypatch.setattr('backend.pipeline.viewpoint_adjudication_history.validated_history',
        lambda root:(adjudication,{item['item_id']:{'verdict':'audit_objection_upheld'}}))
    from backend.pipeline import owner_approved_link_correction as old
    def forbidden(*a,**k):raise AssertionError('retired parent/freeze/runtime was used')
    monkeypatch.setattr(old,'validate_parent',forbidden)
    calls=[]
    class Ports:
        def adapter(self,stage):
            assert stage=='initial_review'
            obj=old.adapter()
            def generate(packet):
                calls.append(stage)
                return {'verdicts':[{'claim_id':cid,'approved':True,'problems':[],'reason':'SIMULATED isolated postimage review'}]}
            obj.generate=generate;return obj
        def backup(self,root):
            dump=root/'fixture.dump';dump.write_bytes(b'ISOLATED TEST ONLY')
            return {'dump':binding(dump),'restore_catalog_nonempty':True}
    direction={'schema_version':'cvp_sql_owner_link_direction.v2','run_id':run,'link_id':saved['before_link']['viewpoint_claim_link_id'],
        'adjudication_packet_sha256':'a'*64,'before_link_sha256':sha256_json(saved['before_link']),
        'after_link_sha256':sha256_json(saved['after_link']),'authorization_quote':'FIXTURE ONLY, no production permission',
        'human_content_approval':False,'automatic_future_repairs_authorized':False}
    direction_path=tmp_path/'direction.json';write_immutable(direction_path,direction)
    # Populate the original audit denominator; no content write or approval is
    # inferred from this synthetic pre-execution group state.
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=metadata||%s::jsonb WHERE run_id=%s",
                     (canonical_json({'import_certificate':{'viewpoint_ids':[vid]}}),run))
    result=execute_owner_link_action(store,run,tmp_path/'fixture-adjudication',link_id=direction['link_id'],
        authorization=binding(direction_path),root=tmp_path/'new-sql-owner-action',ports=Ports())
    assert result['status']=='applied'
    assert len(store.get_change_history(result['change_set_id'])['operations'])==3
    after=store.get_record('viewpoint_claim_links',direction['link_id'])
    assert after['link_type']=='equivalent_full' and after['component_locator'] is None
    assert store.get_record('claims',cid)==item['claim']
    execute_owner_link_action(store,run,tmp_path/'fixture-adjudication',link_id=direction['link_id'],
        authorization=binding(direction_path),root=tmp_path/'new-sql-owner-action',ports=Ports())
    assert calls==['initial_review']
