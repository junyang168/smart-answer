"""Real saved readset, simulated test-only models, disposable SQL only.

This is a structural/transaction regression, never production content approval.
No production model intent, Registry write, or old answer is replayed.
"""
from pathlib import Path
import os
import json
from copy import deepcopy

import pytest
from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, canonical_json
from backend.api.canonical_repository.transaction_validation import TransactionValidation
from backend.pipeline import cvp_membership_management as m, source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as r
from backend.pipeline.cvp_sql_flow import binding, decoded_pins, pin_rows, pins
from backend.pipeline.cvp_sql_actions import SOURCE_SCHEMA, validate_source_package
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store

ROOT=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
ACTION=ROOT/'execution/CVPG-0490be727b9eef01497d20f558f7b437fc7beacd434324c8d71c1a82ce024c83/claim-issues/670ae7487f38ca391a0b3ca9c1a7a5e42814a32d14da50ff4e951299559bfdd0/scope-amendment-once/action/CVPA-949e353397967feb493ede9b46da08843748930954cdd9338ca28088ca1480c5'


@pytest.mark.skipif(os.getenv('WKP357_SAVED_MEMBERSHIP_TEST')!='1',reason='explicit real saved read-only fixture opt-in')
def test_complete_saved_joint_package_real_sql_and_late_rollback(postgres_store,tmp_path,monkeypatch):
    load_dotenv('.env'); production=PostgresKnowledgeStore()
    frozen=r.body(r.read(ACTION/'scoped-materials.json'))
    correction=r.validate_raw(ACTION,'correction',r.body(r.read(ACTION/'correction.packet.json')))
    original=r.effective_proposal(frozen,correction['response'])
    handoff=m.retirement_handoff(frozen,original,correction)
    material=m.prepare_material(production,frozen,handoff)
    assert len(material['read_pins'])==325
    before=deepcopy((frozen,original,correction))
    # The candidate's EXACT prefix is merely a simulated engineering fixture;
    # real GPT/Opus must decide whether it expresses the whole current core.
    candidate=next(p for p in material['candidates'] if m.candidate_claim(material,p)['claim_id']=='DK-da9666998a59c56d3a70-P01-CL002')
    claim=m.candidate_claim(material,candidate)
    component=claim['statement'].split('，')[0]
    evidence=[{'evidence_step_id':e['evidence_step_id'],'source_fragment_id':e['source_fragment_id']}
              for e in candidate['projection']['evidence'] if e['valid_for_identity_review']
              and e['support_eligibility'] in {'eligible','eligible_with_label','eligible_candidate'}]
    proposal={'decision':'propose_members','members':[{'viewpoint_id':handoff['targets'][0]['viewpoint_id'],
        'claim_id':claim['claim_id'],'statement_component':component,
        'evidence_bindings':evidence,'reason':'SIMULATED ENGINEERING FIXTURE ONLY, NOT CONTENT APPROVAL'}],
        'reason':'SIMULATED ENGINEERING FIXTURE ONLY'}
    management=tmp_path/'membership'
    from contextvars import ContextVar
    from backend.pipeline import viewpoint_resolution_runtime as runtime
    budget=runtime.read_artifact(ROOT/'execution/request-limit-binding.json')
    runtime.write_immutable(tmp_path/'request-limit-binding.json',budget)
    monkeypatch.setattr(runtime,'_authorized_limit',ContextVar('isolated_test_request_limit',default=(tmp_path,budget)))
    r.write(management/'materials.json',material)
    class Ports:
        def adapter(self,stage):
            a=m.model_adapter(stage);a._client.executable=None
            a.generate=lambda packet:({'verdicts':[{'claim_id':claim['claim_id'],
                'approved':True,'problems':[],'reason':'SIMULATED ENGINEERING FIXTURE ONLY'}]}
                if 'review' in stage else proposal)
            return a
    stages=r.Runner(management,Ports());stages.root=management
    pro=stages.call('proposal',material)
    packet=m.review_packet(material,pro['response']);review=stages.call('initial_review',packet)
    member_package=m.compile_package(material,pro,review,management)
    m.validate_package(member_package)
    r.write(management/'approved-package.json',member_package)
    source=tmp_path/'source';receipt={'schema_version':m.VERSION,'handoff_id':handoff['handoff_id'],
        'package':binding(management/'approved-package.json'),'management_action_id':'SIMULATED',
        'status':'independently_reviewed_pending_joint_transaction','old_unresolved_is_not_pass':True,
        'original_correction_budget_exhausted':True}
    r.write(source/'membership-resolution.json',receipt)
    r.write(source/'scoped-materials.json',frozen)
    for stage in ['proposal','initial_review','correction']:
        for suffix in ['packet.json','raw.json','reservation.json']:
            r.write(source/(stage+'.'+suffix),r.body(r.read(ACTION/(stage+'.'+suffix))))
    resolved,final_packet=m.joint_review_packet(source,frozen,original,correction)
    assert resolved.records==original.records and (frozen,original,correction)==before
    from backend.pipeline.source_reconciliation_runtime import model_adapter
    from backend.pipeline.claim_issue_management import eligibility_profile
    class FinalPorts:
        def claim_issue_adapter(self,stage,**kwargs):
            a=model_adapter(stage,issue=True,evidence_eligibility=eligibility_profile(final_packet));a._client.executable=None
            a.generate=lambda p:{'verdicts':[{'record_key':d['record_key'],'approved':True,'problems':[],
                                            'reason':'SIMULATED ENGINEERING FIXTURE ONLY'} for d in p['proposal']['dispositions']]}
            return a
    final_stages=r.Runner(source,FinalPorts());final_stages.root=source
    final=final_stages.call('final_review',final_packet)
    package=c.compile_package(frozen,resolved,final,frozen_sha256=m.sha256_json(frozen))
    package=m.append_joint_package(package,source,frozen,original,correction)
    run_id=frozen['sql_scope']['run_id']
    package['source_repair_application']={'schema_version':SOURCE_SCHEMA,'run_root':str(source),
        'run_id':run_id,'materials':binding(source/'scoped-materials.json'),'materials_sha256':m.sha256_json(frozen),
        'terminal_stage':'final_review','review_sha256':final['artifact_sha256'],'proposal_sha256':m.sha256_json(resolved.model_dump())}
    validate_source_package(package)
    # Populate only disposable SQL, including exact retained retirement pins.
    records={}
    for p in material['read_pins']:
        key=(p['collection'],p['object_id']);row=production.get_record_state(*key)
        assert row is not None
        records[key]=row
    store=postgres_store
    with store.connect() as conn:
        for (col,oid),row in records.items():
            payload=row['payload']
            conn.execute("""INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,visibility,
                content_sha256,payload,retired_at) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,
                CASE WHEN %s THEN now() ELSE NULL END)""",
                (col,oid,row['revision'],payload.get('review_status','candidate'),payload.get('visibility','internal'),
                 row['content_sha256'],canonical_json(payload),row['retired']))
        meta={k:v for k,v in production.get_cvp_group(run_id).items() if k not in {'status','error'}}
        conn.execute("INSERT INTO wang_knowledge.pipeline_runs(run_id,subject_kind,subject_id,stage,trigger,status,metadata) VALUES (%s,'batch','isolated','cvp_group','cli','running',%s::jsonb)",
                     (run_id,canonical_json(meta)))
        prior={'kind':'group','campaign_sha256':meta['campaign_sha256'],'checkpoint':'audited',
               'claim_ids':['ISOLATED-unmodified-outside-source-readset'],
               'independent_audit_complete':True,
               'import_certificate':{'viewpoint_ids':[handoff['targets'][0]['viewpoint_id']]}}
        conn.execute("INSERT INTO wang_knowledge.pipeline_runs(run_id,subject_kind,subject_id,stage,trigger,status,metadata) VALUES ('CVPG-isolated-prior-member-denominator','batch','isolated','cvp_group','cli','succeeded',%s::jsonb)",(canonical_json(prior),))
    plan=store.plan_package(package,source_kind=SOURCE_SCHEMA,transactional=True)
    readset=pins(records)
    # The normal commit path includes all store-discovered exact dependencies.
    with store.connect() as conn:
        context_rows=store._existing_cvp_context(conn,normalize_for_context(package))
    readset.update(pins(context_rows))
    for op in plan.operations:
        if op.before_revision is None:readset[op.collection,op.object_id]=None
    evidence={'package':{'payload':package},'approval':{'terminal_review':binding(source/'final_review.raw.json')},
        'authorization':material['policy'],'backup':{'fixture':'disposable SQL only'},'sources':material['sources'],
        'read_pins':pin_rows(readset)}
    reviewed=store.review_cvp_group(run_id,plan=plan,evidence=evidence)
    context=TransactionValidation(run_id,reviewed,readset)
    late=store._assert_transaction_edges_and_reviews
    monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',lambda *_:(_ for _ in ()).throw(ValueError('injected joint late failure')))
    with pytest.raises(ValueError,match='joint late failure'):
        store.apply_plan(plan,transaction_context=context,metadata={'reason':'isolated test','actor':'pytest'})
    for key,row in records.items():assert store.get_record_state(*key)==row
    assert store.get_record_state('viewpoint_claim_links',member_package['viewpoint_claim_links'][0]['viewpoint_claim_link_id']) is None
    assert store.get_cvp_group('CVPG-isolated-prior-member-denominator')['checkpoint']=='audited'
    monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',late)
    result=store.apply_plan(plan,transaction_context=context,metadata={'reason':'isolated test','actor':'pytest'})
    history=store.get_change_history(result['change_set_id'])
    affected=store.get_cvp_group('CVPG-isolated-prior-member-denominator')
    assert affected['checkpoint']=='applied' and not affected['independent_audit_complete']
    assert affected['pending_audit_from_change_set_ids']==[result['change_set_id']]
    assert len(history['operations'])==len(plan.operations)
    assert len(plan.operations)==17  # Six updates + eight fresh provenance + three membership creates.
    for op in history['operations']:
        actual=store.get_record_state(op['collection'],op['object_id'])
        assert actual['payload']==op['after'] and actual['content_sha256']==op['after_sha256']


def normalize_for_context(package):
    from backend.api.canonical_repository.postgres_store import normalize_package
    return normalize_package(package)
