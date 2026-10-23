"""Saved real inputs, simulated review, disposable SQL: not content approval."""
from copy import deepcopy
from pathlib import Path
import os

import pytest
from dotenv import load_dotenv

from backend.pipeline import source_reconciliation as c, source_reconciliation_runner as r
from backend.pipeline import claim_first_management as m
from backend.pipeline.cvp_sql_actions import SOURCE_SCHEMA,validate_source_package
from backend.pipeline.cvp_sql_flow import binding,decoded_pins
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore,canonical_json
from backend.api.canonical_repository.transaction_validation import TransactionValidation
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store
from backend.tests.test_cvp_membership_joint_saved_postgres import ACTION,ROOT


@pytest.mark.skipif(os.getenv('WKP357_SAVED_CLAIM_FIRST_TEST')!='1',reason='real saved read-only input opt-in')
def test_saved_claim_first_two_ops_pending_and_late_rollback(postgres_store,tmp_path,monkeypatch):
    load_dotenv('.env');production=PostgresKnowledgeStore()
    frozen=r.body(r.read(ACTION/'scoped-materials.json'))
    correction=r.validate_raw(ACTION,'correction',r.body(r.read(ACTION/'correction.packet.json')))
    original=r.effective_proposal(frozen,correction['response']);unchanged=deepcopy((frozen,original,correction))
    root=tmp_path/'source';r.write(root/'scoped-materials.json',frozen)
    for stage in ['proposal','initial_review','correction']:
        for suffix in ['packet.json','raw.json','reservation.json']:
            r.write(root/(stage+'.'+suffix),r.body(r.read(ACTION/(stage+'.'+suffix))))
    projected,packet=m.final_packet(root,frozen,original,correction)
    assert len(projected.records)==2 and len(projected.dispositions)==124
    assert len(packet['source_first_pending_cvp']['pending_record_keys'])==24
    assert (frozen,original,correction)==unchanged
    from contextvars import ContextVar
    from backend.pipeline import viewpoint_resolution_runtime as runtime
    budget=runtime.read_artifact(ROOT/'execution/request-limit-binding.json')
    runtime.write_immutable(tmp_path/'request-limit-binding.json',budget)
    monkeypatch.setattr(runtime,'_authorized_limit',ContextVar('claim_first_test_limit',default=(tmp_path,budget)))
    from backend.pipeline.source_reconciliation_runtime import model_adapter
    class Ports:
        def claim_issue_adapter(self,stage,**kwargs):
            adapter=model_adapter(stage,issue=True,evidence_eligibility=True);adapter._client.executable=None
            pending=set(packet['source_first_pending_cvp']['pending_record_keys'])
            adapter.generate=lambda p:{'verdicts':[{'record_key':d['record_key'],
                'approved':d['record_key'] not in pending,
                'problems':[m.PENDING_PROBLEM] if d['record_key'] in pending else [],
                'reason':'SIMULATED ENGINEERING REVIEW ONLY, NOT PRODUCTION CONTENT APPROVAL'} for d in p['proposal']['dispositions']]}
            return adapter
    runner=r.Runner(root,Ports());runner.root=root
    raw=runner.call('final_review',packet)
    package=c.compile_package(frozen,projected,raw,frozen_sha256=c.sha256_json(frozen),claim_first=True)
    run_id=frozen['sql_scope']['run_id']
    package['source_repair_application']={'schema_version':SOURCE_SCHEMA,'run_root':str(root),'run_id':run_id,
        'materials':binding(root/'scoped-materials.json'),'materials_sha256':c.sha256_json(frozen),
        'terminal_stage':'final_review','review_sha256':raw['artifact_sha256'],
        'proposal_sha256':c.sha256_json(projected.model_dump())}
    validate_source_package(package)
    store=postgres_store;records={}
    for pin in frozen['sql_scope']['read_pins']:
        key=(pin['collection'],pin['object_id']);records[key]=production.get_record_state(*key)
    with store.connect() as conn:
        for (col,oid),row in records.items():
            payload=row['payload'];conn.execute('''INSERT INTO wang_knowledge.objects
              (collection,object_id,revision,review_status,visibility,content_sha256,payload,retired_at)
              VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,CASE WHEN %s THEN now() ELSE NULL END)''',
              (col,oid,row['revision'],payload.get('review_status','candidate'),payload.get('visibility','internal'),
               row['content_sha256'],canonical_json(payload),row['retired']))
        meta={k:v for k,v in production.get_cvp_group(run_id).items() if k not in {'status','error'}}
        conn.execute("INSERT INTO wang_knowledge.pipeline_runs(run_id,subject_kind,subject_id,stage,trigger,status,metadata) VALUES (%s,'batch','isolated','cvp_group','cli','running',%s::jsonb)",(run_id,canonical_json(meta)))
    plan=store.plan_package(package,source_kind=SOURCE_SCHEMA,transactional=True)
    assert len(plan.operations)==2 and all(o.collection=='evidence_steps' for o in plan.operations)
    pins=decoded_pins(frozen['sql_scope']['read_pins'])
    def review():
        return store.review_cvp_group(run_id,plan=plan,evidence={'read_pins':frozen['sql_scope']['read_pins'],
            'pending_cvp_claim_ids':production.get_cvp_group(run_id)['claim_ids'],
            'package':{'sha256':c.sha256_json(package),'payload':package},
            'authorization':frozen['manifest']['authorization'],
            'backup':{'isolated_test_only':True},'sources':[frozen['manifest']['before_source']],
            'approval':{'terminal_review':binding(root/'final_review.raw.json')}})
    seal=review();validate=store._assert_transaction_edges_and_reviews
    monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',lambda *a,**kw:(_ for _ in ()).throw(ValueError('injected late rollback')))
    with pytest.raises(ValueError,match='late rollback'):
        store.apply_plan(plan,transaction_context=TransactionValidation(run_id,seal,pins),metadata={'reason':'isolated Claim-first test','actor':'pytest'})
    for key,row in records.items():assert store.get_record_state(*key)==row
    monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',validate)
    seal=review();result=store.apply_plan(plan,transaction_context=TransactionValidation(run_id,seal,pins),metadata={'reason':'isolated Claim-first test','actor':'pytest'})
    history=store.get_change_history(result['change_set_id']);assert len(history['operations'])==2
    changed={(o.collection,o.object_id) for o in plan.operations}
    for key,row in records.items():
        current=store.get_record_state(*key)
        if key not in changed:assert current==row
        else:assert current['payload']==next(o['after'] for o in history['operations'] if (o['collection'],o['object_id'])==key)
    assert store.get_cvp_group(run_id)['checkpoint']=='applied'
    assert store.get_cvp_group(run_id)['reviewed']['evidence']['pending_cvp_claim_ids']
