"""Exact human retention is not a rewritten model pass or fresh old approval."""
from copy import deepcopy
import pytest

from backend.api.canonical_repository.postgres_store import sha256_json,record_content_sha
from backend.api.canonical_repository.viewpoint_batch_changeset import (
    compile_cvp_batch_package,retained_write_components,RETAINED_WRITE_SCOPE_VERSION,CvpBatchChangeSetError)
from backend.api.canonical_repository.viewpoint_batch_resolution import CoverageCanonicalViewpointReviewResponse
from test_viewpoint_batch_resolution import _claim,_proposal,_component,ROCK_STATEMENT,REGISTRY_CONTEXT_ROCK
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store


def review_for(p,claims,reject=()):
    return CoverageCanonicalViewpointReviewResponse.model_validate({
        'proposal_sha256':sha256_json(p.model_dump(mode='json')),
        'change_reviews':[{'claim_id':d.claim_id,'component_index':i,
            'decision':'reject' if (d.claim_id,i) in reject else 'pass',
            'reason':'actual synthetic finding preserved' if (d.claim_id,i) in reject else 'new item approved',
            'finding_codes':['member_target_adds_load_bearing_content'] if (d.claim_id,i) in reject else [],
            'coverage_requirement':'cvp_required','cvp_covered':True,
            'coverage_claim_sha256':next(c.claim_revision_sha256 for c in claims if c.claim_id==d.claim_id),
            'coverage_evidence_step_ids':[d.claim_id+'-E1'],'coverage_reason':'complete own evidence'}
            for d in p.claim_decisions for i in range(len(d.components))],
        'novelty_review':{'status':'pass','reason':'complete'}})


@pytest.fixture
def batch():
    c1=_claim('C1',ROCK_STATEMENT);c2=_claim('C2',ROCK_STATEMENT)
    old_p=_proposal(new_viewpoint_candidates=[],claim_decisions=[{'claim_id':'C1','components':[
        _component(ROCK_STATEMENT,'磐石不是彼得这个人','member_existing',target_viewpoint_revision_id='CVR-1')]}])
    kwargs=dict(deterministic_validation_sha256='validation',scope_manifest_sha256='manifest',
        registry_context=REGISTRY_CONTEXT_ROCK,proposal_artifact_sha256='proposal-raw',
        review_artifact_sha256='review-raw',proposer_model_id='gpt-6.1-sol',reviewer_model_id='claude-opus-5-5',
        decided_at='2026-10-09T22:00:00Z')
    old=compile_cvp_batch_package(proposal=old_p,review=review_for(old_p,[c1]),claims=[c1],**kwargs)['viewpoint_claim_links'][0]
    p=_proposal(claim_decisions=[old_p.claim_decisions[0].model_dump(mode='json'),{'claim_id':'C2','components':[
        _component(ROCK_STATEMENT,'磐石不是彼得这个人','new_viewpoint',claim_id='C2',local_new_viewpoint_key='ROCK-NOT-PETER')]}])
    r=review_for(p,[c1,c2],reject=[('C1',0)])
    scope={'schema_version':RETAINED_WRITE_SCOPE_VERSION,'proposal_sha256':sha256_json(p.model_dump(mode='json')),
        'review_sha256':sha256_json(r.model_dump(mode='json')),'human_disposition':{'path':'human','file_sha256':'sealed'},
        'human_retained_link_ids':[old['viewpoint_claim_link_id']],
        'retained_link_pins':[{'collection':'viewpoint_claim_links','object_id':old['viewpoint_claim_link_id'],
                              'revision':old['revision'],'content_sha256':record_content_sha(old)}],
        'model_verdicts_changed':False}
    return p,r,[c1,c2],[old],scope,kwargs


def test_new_writes_pass_old_human_retention_has_no_fresh_identity_provenance(batch):
    p,r,claims,old,scope,kw=batch;before=deepcopy(r.model_dump(mode='json'))
    with pytest.raises(CvpBatchChangeSetError):
        compile_cvp_batch_package(proposal=p,review=r,claims=claims,current_claim_links=old,**kw)
    result=compile_cvp_batch_package(proposal=p,review=r,claims=claims,current_claim_links=old,retained_write_scope=scope,**kw)
    assert r.model_dump(mode='json')==before and r.change_reviews[0].decision=='reject'
    assert old[0] in result['viewpoint_claim_links']
    assert len(result['canonical_viewpoints'])==1
    assert len(result['viewpoint_identity_candidates'])==len(result['viewpoint_identity_decisions'])==1
    assert result['viewpoint_identity_candidates'][0]['candidate_claim_ids']==['C2']
    assert result['viewpoint_identity_decisions'][0]['claim_link_decisions']==[{'claim_id':'C2','link_type':'equivalent_component'}]


@pytest.mark.parametrize('fault',['new-reject','new-defer','missing-human','extra-human','duplicate-human',
    'proposal-sha','review-sha','fake-pass','missing-pin','foreign-pin','version','sha','target','type','span','evidence',
    'claim-version','link-hold','link-retired','novelty','missing-review'])
def test_no_change_decision_never_waives_new_content_or_version_or_coverage(batch,fault):
    p,r,claims,old,s,kw=batch
    if fault=='new-reject':r.change_reviews[1].decision='reject'
    elif fault=='new-defer':r.change_reviews[1].decision='defer'
    elif fault=='old-defer':r.change_reviews[0].decision='defer'
    elif fault=='missing-human':s['human_retained_link_ids']=[]
    elif fault=='extra-human':s['human_retained_link_ids'].append('foreign')
    elif fault=='duplicate-human':s['human_retained_link_ids']*=2
    elif fault=='proposal-sha':s['proposal_sha256']='foreign'
    elif fault=='review-sha':s['review_sha256']='foreign'
    elif fault=='fake-pass':s['model_verdicts_changed']=True
    elif fault=='missing-pin':s['retained_link_pins']=[]
    elif fault=='foreign-pin':s['retained_link_pins'][0]['object_id']='foreign'
    elif fault=='version':s['retained_link_pins'][0]['revision']+=1
    elif fault=='sha':s['retained_link_pins'][0]['content_sha256']='foreign'
    elif fault=='target':p.claim_decisions[0].components[0].target_viewpoint_revision_id='foreign'
    elif fault=='type':p.claim_decisions[0].components[0].disposition='support_existing'
    elif fault=='span':old[0]['component_locator']['canonical_spans'][0]['end_char']+=1
    elif fault=='evidence':old[0]['evidence_bindings']=[]
    elif fault=='claim-version':old[0]['pinned_claim_revision']+=1
    elif fault=='link-hold':old[0]['review_status']='human_review_required'
    elif fault=='link-retired':old[0]['effective_state']='retired'
    elif fault=='novelty':r.novelty_review.status='missed_novelty';r.novelty_review.missed_claim_ids=['C2']
    elif fault=='missing-review':r.change_reviews.pop()
    # Update semantic receipt pins for most tampering: it must still fail on
    # the substantive changed state, not only an easy outer digest mismatch.
    if fault not in ('proposal-sha','review-sha'):
        s['proposal_sha256']=sha256_json(p.model_dump(mode='json'))
        r.proposal_sha256=s['proposal_sha256'];s['review_sha256']=sha256_json(r.model_dump(mode='json'))
    if fault in ('span','evidence','claim-version','link-hold','link-retired'):
        s['retained_link_pins'][0]['content_sha256']=record_content_sha(old[0])
    with pytest.raises(ValueError):
        compile_cvp_batch_package(proposal=p,review=r,claims=claims,current_claim_links=old,retained_write_scope=s,**kw)


def test_exact_human_retention_resolves_old_uncertainty_without_changing_defer(batch):
    p,r,claims,old,s,kw=batch
    r.change_reviews[0].decision='defer'
    s['review_sha256']=sha256_json(r.model_dump(mode='json'))
    result=compile_cvp_batch_package(proposal=p,review=r,claims=claims,current_claim_links=old,retained_write_scope=s,**kw)
    assert r.change_reviews[0].decision=='defer'
    assert old[0] in result['viewpoint_claim_links']


@pytest.mark.skipif(__import__('os').environ.get('WKP_CVP_RETAINED_WRITE_REAL_ACCEPTANCE')!='1',
                   reason='explicit actual saved responses, live SQL read-only, zero calls/writes')
def test_actual_final_rejects_preserved_and_new_write_scope_uses_exact_human_retain(monkeypatch,postgres_store):
    from pathlib import Path
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline import cvp_sql_flow as sql,cvp_retained_review_separation as flow
    from backend.pipeline import viewpoint_batch_resolution_runner as runner,viewpoint_resolution_runtime as rt
    from backend.pipeline.cvp_batch_identity_integration import ENABLED
    load_dotenv('.env')
    class ReadOnly(PostgresKnowledgeStore):
        def connect(self):
            c=super().connect();c.execute('SET TRANSACTION READ ONLY');return c
    store=ReadOnly()
    def read_guard(cursor,pins):
        class ReadCursor:
            def execute(self,q,params):
                assert q.endswith(' FOR UPDATE');cursor.execute(q.removesuffix(' FOR UPDATE'),params)
            def fetchone(self):return cursor.fetchone()
        return PostgresKnowledgeStore._assert_transaction_read_pins(ReadCursor(),pins)
    monkeypatch.setattr(store,'_assert_transaction_read_pins',read_guard)
    campaign=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
    run='CVPG-0490be727b9eef01497d20f558f7b437fc7beacd434324c8d71c1a82ce024c83'
    aid='CVPA-7f7bf996ebe7b3fc0bb8aca982ca47a14c287aaf644dc42c031f8de9386763c8'
    root=campaign/'execution'/run/'required-coverage'/aid/'model-stages'
    before=store.get_cvp_group(run);materials=store.get_cvp_action(run,aid)['materials']
    raw_before={p.name:p.read_bytes() for p in root.glob('raw*.json')}
    captured={};authenticated=[]
    def no_write(path,value,**kwargs):
        path=Path(path)
        if path.name in ('reviewed-package.json','retained-write-scope-authority.json'):
            captured[path.name]=deepcopy(value);return
        assert path.exists() and rt.read_artifact(path)==value
    monkeypatch.setattr(runner,'_write_immutable',no_write)
    monkeypatch.setattr(sql,'write_immutable',no_write)
    monkeypatch.setattr(rt,'write_immutable',no_write)
    monkeypatch.setattr(runner,'_write_derived',lambda *a,**k:None)
    monkeypatch.setattr(runner,'_write_current_state',lambda *a,**k:None)
    monkeypatch.setattr(sql,'stable_decided_at',lambda _: '2026-10-09T22:00:00Z')
    actual_binding=sql.binding
    def preflight_binding(path):
        import json,hashlib
        path=Path(path)
        if path.name in captured:
            data=(json.dumps(captured[path.name],ensure_ascii=False,indent=2)+'\n').encode()
            return {'path':str(path),'file_sha256':hashlib.sha256(data).hexdigest()}
        return actual_binding(path)
    monkeypatch.setattr(sql,'binding',preflight_binding)
    real_authority=flow.write_scope_authority
    monkeypatch.setattr(flow,'write_scope_authority',lambda root,s,m,r:
        real_authority(root,s,m,r,human_binding=sql.binding(campaign/'owner-g094-retained-equivalence-no-change.json')))
    def saved_only(adapter,payload,path):
        assert path.exists(),'new model call forbidden'
        selected=rt.response_adapter(adapter,rt.read_artifact(path))
        response=rt.read_saved_model_response(selected,payload,path)
        authenticated.append(path.name)
        return rt.bound_model_response(selected,payload,response,path,persist=False),0,0.0
    monkeypatch.setattr(runner,'_call',saved_only)
    token=ENABLED.set(True);saved_token=rt._saved_validation.set(True)
    try:package,approval,disposition=sql.reviewed_cvp_package(root,materials,store,require_resolved=True)
    finally:ENABLED.reset(token);rt._saved_validation.reset(saved_token)
    assert len(authenticated)==5
    current={l['viewpoint_claim_link_id']:l for l in materials['packet']['existing_links']}
    assert len(current)==25
    for lid,old in current.items():
        assert next(l for l in package['viewpoint_claim_links'] if l['viewpoint_claim_link_id']==lid)==old
    rejected_pairs={(current[lid]['claim_id'],current[lid]['viewpoint_id'])
        for lid in captured['retained-write-scope-authority.json']['human_retained_link_ids']}
    assert all((d['claim_id'],record['resolved_viewpoint_id']) not in rejected_pairs
        for record in package['viewpoint_identity_decisions'] for d in record['claim_link_decisions'])
    plan=store.plan_package(package,transactional=True,source_kind='cvp_batch_resolution')
    assert all(o.operation=='create' and o.collection in sql.NORMAL_COLLECTIONS for o in plan.operations)
    assert not any(o.collection=='viewpoint_claim_links' and o.object_id in current for o in plan.operations)
    assert store.get_cvp_group(run)==before
    assert raw_before=={p.name:p.read_bytes() for p in root.glob('raw*.json')}
    assert len(materials['claim_management_resolution']['pending_existing_cvp_record_keys'])==24
    final=rt.read_artifact(root/'raw-final-review.json')
    assert sum(r['decision']=='reject' for r in final['response']['change_reviews'])==3
    # Same actual package, disposable SQL. No synthetic semantic approval.
    # A late validator failure must roll back all 33 proposed creates, then a
    # safe retry writes exactly once and preserves EVERY existing input row.
    from backend.api.canonical_repository.postgres_store import normalize_package,canonical_json
    from backend.api.canonical_repository.transaction_validation import TransactionValidation
    with store.connect() as conn:
        records=store._existing_cvp_context(conn,normalize_package(package),[])
    for key in sql.decoded_pins(materials['read_pins']):
        records[key]=store.get_record_state(*key)
    target=postgres_store
    from psycopg.sql import SQL,Identifier,Placeholder
    from psycopg.types.json import Jsonb
    object_ids=[oid for _,oid in records]
    with store.connect() as src,target.connect() as dest:
        for table,where in [('objects','object_id=ANY(%s)'),('edges','edge_id=ANY(%s)')]:
            cursor=src.execute(SQL('SELECT * FROM wang_knowledge.{} WHERE '+where).format(Identifier(table)),(object_ids,))
            columns=[c.name for c in cursor.description]
            insert=SQL('INSERT INTO wang_knowledge.{} ({}) VALUES ({})').format(Identifier(table),
                SQL(',').join(map(Identifier,columns)),SQL(',').join(Placeholder()*len(columns)))
            for row in cursor.fetchall():
                dest.execute(insert,tuple(Jsonb(x) if isinstance(x,(dict,list)) else x for x in row))
        meta={k:v for k,v in before.items() if k not in {'status','error'}}
        dest.execute("INSERT INTO wang_knowledge.pipeline_runs(run_id,subject_kind,subject_id,stage,trigger,status,metadata) VALUES (%s,'batch','isolated','cvp_group','cli','running',%s::jsonb)",(run,canonical_json(meta)))
    plan=target.plan_package(package,transactional=True,source_kind='cvp_batch_resolution')
    pins=sql.pins(records)
    for o in plan.operations:pins.setdefault((o.collection,o.object_id),None)
    evidence={'read_pins':sql.pin_rows(pins),'package':{'payload':package},'approval':approval,
        'authorization':{'synthetic_sql_test_only':True},'backup':{'isolated_only':True},
        'sources':materials['sources'],'pending_cvp_management_record_keys':materials['claim_management_resolution']['pending_existing_cvp_record_keys']}
    evidence['action']=target.prepare_cvp_action(run,action_id=aid,kind='required_cvp_coverage',
        authorization={'isolated_sql_only':True},materials=materials)
    seal=target.review_cvp_group(run,plan=plan,evidence=evidence)
    guard=target._assert_transaction_edges_and_reviews
    def late(*a,**kw):
        guard(*a,**kw);raise ValueError('injected late split-scope rollback')
    monkeypatch.setattr(target,'_assert_transaction_edges_and_reviews',late)
    ctx=TransactionValidation(run,seal,pins)
    with pytest.raises(ValueError,match='late split-scope'):
        target.apply_plan(plan,transaction_context=ctx,metadata={'reason':'isolated exact saved split scope','actor':'pytest'})
    assert target.get_change_set_status(plan.fingerprint_sha256) is None
    for key,row in records.items():assert target.get_record_state(*key)==row
    monkeypatch.setattr(target,'_assert_transaction_edges_and_reviews',guard)
    result=target.apply_plan(plan,transaction_context=ctx,metadata={'reason':'isolated exact saved split scope','actor':'pytest'})
    history=target.get_change_history(result['change_set_id'])
    assert len(history['operations'])==33
    for key,row in records.items():assert target.get_record_state(*key)==row
    for op in history['operations']:
        assert target.get_record_state(op['collection'],op['object_id'])['payload']==op['after']
    assert target.apply_plan(plan,transaction_context=ctx,metadata={'reason':'isolated exact saved split scope','actor':'pytest'})['status']=='already_applied'
    assert len(target.get_cvp_group(run)['reviewed']['evidence']['pending_cvp_management_record_keys'])==24
    print('ACTUAL_HUMAN_RETAIN_ACCEPTANCE',{'authenticated':authenticated,'retained_links':25,
        'operations':len(plan.operations),'collections':{c:sum(o.collection==c for o in plan.operations)
        for c in sorted({o.collection for o in plan.operations})},'pending_existing_impacts':24,'raws_unchanged':True})
