"""Real unresolved final -> human no-change -> new-only plan, no calls/writes."""
import os
from pathlib import Path
from copy import deepcopy
import pytest
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store


@pytest.mark.skipif(os.environ.get('WKP_G107_HUMAN_FLOW')!='1',reason='explicit live SQL readonly/saved raw acceptance')
def test_real_saved_human_no_change_full_shared_compiler(monkeypatch,postgres_store):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline import cvp_sql_flow as sql,viewpoint_resolution_runtime as rt
    from backend.pipeline import viewpoint_batch_resolution_runner as runner,deferred_claim_cvp_continuation as dc
    from backend.pipeline import cvp_retained_review_separation as flow
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
    run='CVPG-dc7a68b9483efcd5e134b8162cf1753ad8943916b84379ea19863c57488ed446'
    root=campaign/'execution'/run/'normal-review'
    before=store.get_cvp_group(run);materials=before['materials']
    originals={str(p):p.read_bytes() for p in root.rglob('*.json')}
    captured={}
    def no_write(path,value,**kw):
        path=Path(path)
        if path.exists():assert rt.read_artifact(path)==value
        else:captured[str(path)]=deepcopy(value)
    monkeypatch.setattr(rt,'write_immutable',no_write)
    monkeypatch.setattr(sql,'write_immutable',no_write)
    monkeypatch.setattr(runner,'_write_immutable',no_write)
    monkeypatch.setattr(runner,'_write_derived',no_write)
    monkeypatch.setattr(runner,'_write_current_state',lambda *a,**k:None)
    monkeypatch.setattr(rt,'validating_saved_artifacts',lambda:True)
    adapters=dc.adapters
    monkeypatch.setattr(dc,'adapters',lambda cfg:adapters(cfg,cached_only=True))
    authority=flow.write_scope_authority
    monkeypatch.setattr(flow,'write_scope_authority',lambda r,s,m,p:authority(r,s,m,p,
        human_binding=sql.binding(campaign/'owner-g107-retained-equivalence-no-change.json')))
    actual_binding=sql.binding
    def virtual_binding(path):
        import json,hashlib
        path=Path(path)
        if str(path) in captured:
            data=(json.dumps(captured[str(path)],ensure_ascii=False,indent=2)+'\n').encode()
            return {'path':str(path),'file_sha256':hashlib.sha256(data).hexdigest()}
        return actual_binding(path)
    monkeypatch.setattr(sql,'binding',virtual_binding)
    monkeypatch.setattr(sql,'stable_decided_at',lambda _: '2026-10-10T19:00:00Z')
    token=ENABLED.set(True)
    try:
        with rt.authorized_request_limit(campaign/'execution',before['campaign_sha256'],campaign/'owner-request-limit-600k.json'):
            package,approval,disposition=sql.reviewed_cvp_package(root,materials,store)
    finally:ENABLED.reset(token)
    current={l['viewpoint_claim_link_id']:l for l in materials['packet']['existing_links']}
    assert all(next(l for l in package['viewpoint_claim_links'] if l['viewpoint_claim_link_id']==lid)==old
               for lid,old in current.items())
    plan=store.plan_package(package,transactional=True,source_kind='cvp_batch_resolution')
    assert all(o.operation=='create' and o.collection in sql.NORMAL_COLLECTIONS for o in plan.operations)
    assert not any(o.collection=='viewpoint_claim_links' and o.object_id in current for o in plan.operations)
    scope=captured[str(root/'retained-write-scope-authority.json')]
    assert len(scope['human_retained_claim_pins'])==1
    assert scope['human_retained_link_ids']==['VCL-6d1c026e928f08a2469c']
    assert store.get_cvp_group(run)==before
    assert all(Path(p).read_bytes()==data for p,data in originals.items())
    assert set(originals)=={str(p) for p in root.rglob('*.json')}
    final=rt.read_artifact(root/'raw-final-review.json')
    assert sum(v['decision']=='defer' for v in final['response']['change_reviews'])==1
    print({'real_saved_human_flow':True,'read_pins':len(materials['read_pins']),
        'new_only_operations':len(plan.operations),'model_calls':0,'registry_writes':0,'artifact_writes':0})
    # Use the identical real two-create package in a disposable SQL database.
    # The real database connection remains READ ONLY throughout.
    from backend.api.canonical_repository.postgres_store import normalize_package,canonical_json
    from backend.api.canonical_repository.transaction_validation import TransactionValidation
    from psycopg.sql import SQL,Identifier,Placeholder
    from psycopg.types.json import Jsonb
    with store.connect() as conn:
        records=store._existing_cvp_context(conn,normalize_package(package),[])
    for key in sql.decoded_pins(materials['read_pins']):records[key]=store.get_record_state(*key)
    ids=[oid for _,oid in records];target=postgres_store
    with store.connect() as src,target.connect() as dst:
        for table,where in [('objects','object_id=ANY(%s)'),('edges','edge_id=ANY(%s)')]:
            cur=src.execute(SQL('SELECT * FROM wang_knowledge.{} WHERE '+where).format(Identifier(table)),(ids,))
            columns=[c.name for c in cur.description]
            insert=SQL('INSERT INTO wang_knowledge.{} ({}) VALUES ({})').format(Identifier(table),
                SQL(',').join(map(Identifier,columns)),SQL(',').join(Placeholder()*len(columns)))
            for row in cur.fetchall():dst.execute(insert,tuple(Jsonb(v) if isinstance(v,(dict,list)) else v for v in row))
        meta={k:v for k,v in before.items() if k not in {'status','error'}}
        dst.execute("INSERT INTO wang_knowledge.pipeline_runs(run_id,subject_kind,subject_id,stage,trigger,status,metadata) VALUES (%s,'batch','isolated','cvp_group','cli','running',%s::jsonb)",(run,canonical_json(meta)))
    plan=target.plan_package(package,transactional=True,source_kind='cvp_batch_resolution')
    readpins=sql.pins(records)
    for op in plan.operations:readpins.setdefault((op.collection,op.object_id),None)
    evidence={'read_pins':sql.pin_rows(readpins),'package':{'payload':package},'approval':approval,
        'authorization':{'isolated_test_only':True},'backup':{'isolated_only':True},'sources':materials['sources']}
    seal=target.review_cvp_group(run,plan=plan,evidence=evidence)
    context=TransactionValidation(run,seal,readpins)
    guard=target._assert_transaction_edges_and_reviews
    def late(*a,**kw):
        guard(*a,**kw);raise ValueError('injected late human flow rollback')
    monkeypatch.setattr(target,'_assert_transaction_edges_and_reviews',late)
    with pytest.raises(ValueError,match='late human flow rollback'):
        target.apply_plan(plan,transaction_context=context,metadata={'reason':'isolated actual human flow','actor':'pytest'})
    assert target.get_change_set_status(plan.fingerprint_sha256) is None
    for key,state in records.items():assert target.get_record_state(*key)==state
    monkeypatch.setattr(target,'_assert_transaction_edges_and_reviews',guard)
    result=target.apply_plan(plan,transaction_context=context,metadata={'reason':'isolated actual human flow','actor':'pytest'})
    history=target.get_change_history(result['change_set_id'])
    assert len(history['operations'])==2
    for op in history['operations']:
        assert target.get_record_state(op['collection'],op['object_id'])['payload']==op['after']
    for key,state in records.items():assert target.get_record_state(*key)==state
    assert target.apply_plan(plan,transaction_context=context,metadata={'reason':'isolated actual human flow','actor':'pytest'})['status']=='already_applied'
    print({'isolated_actual_two_ops':True,'late_rollback':True,'history_current_exact':True,'already_applied_no_replay':True})
