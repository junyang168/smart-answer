"""Actual PostgreSQL commit/rollback tests in a socket-only disposable DB."""
from copy import deepcopy
import uuid

import pytest

from backend.api.canonical_repository.postgres_store import (
    ChangeSetConflict, PostgresKnowledgeStore, canonical_json, record_content_sha,
)
from backend.api.canonical_repository.transaction_validation import TransactionValidation
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import (
    _temporary_postgres, postgres_store,
)


def task(store, *, existing=False):
    oid = "SRC-" + uuid.uuid4().hex
    package = {"schema_version":"wang_shared_knowledge_v1.3", "package_id":"PKG-"+oid,
               "source_documents":[{"source_id":oid,"title":oid,"source_type":"sermon_transcript"}]}
    if existing:
        store.apply_plan(store.plan_package(package))
        package["source_documents"][0]["title"] += " revised"
    plan = store.plan_package(package)
    current = store.get_record_state("source_documents",oid)
    pin = (current["revision"],current["content_sha256"],current["retired"]) if current else None
    campaign = store.initialize_cvp_campaign({"accepted_grouping_sha256":uuid.uuid4().hex*2,
        "groups":[{"sequence":1,"group_key":"group","claim_ids":["CL-1"]},
                  {"sequence":2,"group_key":"next","claim_ids":["CL-2"]}]})
    run = store.prepare_cvp_group(campaign_sha256=campaign,group_key="group",sequence=1,
                                  claim_ids=["CL-1"],materials={"fixture":"SQL protocol only"})
    evidence = {"package":{"payload":package},"approval":{"fixture":True},"authorization":{"fixture":True},
                "backup":{"fixture":True},"sources":[oid],
                "read_pins":[{"collection":"source_documents","object_id":oid,"pin":list(pin) if pin else None}]}
    reviewed = store.review_cvp_group(run,plan=plan,evidence=evidence)
    return plan, TransactionValidation(run,reviewed,{("source_documents",oid):pin})


def apply(store,plan,context):
    return store.apply_plan(plan,transaction_context=context,metadata={"reason":"isolated SQL test","actor":"pytest"})


def assert_rolled_back(store,plan,context):
    assert store.get_change_set_status(plan.fingerprint_sha256) is None
    assert store.get_cvp_group(context.run_id)["checkpoint"] == "reviewed"
    assert store.get_record("source_documents",plan.operations[0].object_id) is None
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM wang_knowledge.object_versions WHERE change_set_id=%s",(plan.change_set_id,)).fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM wang_knowledge.change_operations WHERE change_set_id=%s",(plan.change_set_id,)).fetchone() == (0,)


def test_actual_commit_contains_object_history_and_checkpoint(postgres_store):
    store=postgres_store;plan,context=task(store)
    result=apply(store,plan,context)
    assert result["status"] == "applied"
    assert store.get_cvp_group(context.run_id)["checkpoint"] == "applied"
    history=store.get_change_history(plan.change_set_id)
    assert len(history["operations"]) == 1
    assert history["operations"][0]["before"] is None
    assert history["operations"][0]["after"]["source_id"] == plan.operations[0].object_id
    assert history["metadata"]["reason"] == "isolated SQL test"


def test_last_validation_failure_rolls_back_all_tables(postgres_store,monkeypatch):
    store=postgres_store;plan,context=task(store)
    def fail(*args): raise ValueError("last validation failure")
    monkeypatch.setattr(store,"_assert_transaction_edges_and_reviews",fail)
    with pytest.raises(ValueError,match="last validation"):apply(store,plan,context)
    assert_rolled_back(store,plan,context)


def test_history_failure_rolls_back_object_and_checkpoint(postgres_store,monkeypatch):
    store=postgres_store;plan,context=task(store)
    def fail(*args): raise ValueError("history failure")
    monkeypatch.setattr(store,"_assert_change_set_write_ledger",fail)
    with pytest.raises(ValueError,match="history failure"):apply(store,plan,context)
    assert_rolled_back(store,plan,context)


def test_wrong_where_additional_row_rolls_back_both(postgres_store,monkeypatch):
    store=postgres_store;plan,context=task(store)
    other="OTHER-"+uuid.uuid4().hex
    package={"schema_version":"wang_shared_knowledge_v1.3","package_id":"fixture-other",
             "source_documents":[{"source_id":other,"source_type":"sermon_transcript","title":other}]}
    store.apply_plan(store.plan_package(package));before=deepcopy(store.get_record_state("source_documents",other))
    original=store._assert_transaction_edges_and_reviews
    def wrong(cursor,write_plan):
        cursor.execute("UPDATE wang_knowledge.objects SET visibility='oops' WHERE collection='source_documents'")
        original(cursor,write_plan)
    monkeypatch.setattr(store,"_assert_transaction_edges_and_reviews",wrong)
    with pytest.raises(ValueError,match="actual transaction write set"):apply(store,plan,context)
    assert_rolled_back(store,plan,context)
    assert store.get_record_state("source_documents",other)==before


def test_committed_reply_lost_recovers_before_stale_preimage(postgres_store,monkeypatch):
    store=postgres_store;plan,context=task(store)
    apply(store,plan,context)
    def no(*args):raise AssertionError("old preimage or global snapshot must not be read")
    monkeypatch.setattr(store,"_assert_transaction_read_pins",no)
    monkeypatch.setattr(store,"_assert_registry_fingerprint",no)
    assert apply(store,plan,context)["status"]=="already_applied"
    assert len(store.get_change_history(plan.change_set_id)["operations"])==1


def test_changed_current_dependency_stops_before_any_write(postgres_store):
    store=postgres_store;plan,context=task(store,existing=True)
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET revision=revision+1 WHERE object_id=%s",(plan.operations[0].object_id,))
    with pytest.raises(ChangeSetConflict,match="dependency drift"):apply(store,plan,context)
    assert store.get_change_set_status(plan.fingerprint_sha256) is None
    assert store.get_cvp_group(context.run_id)["checkpoint"]=="reviewed"


def test_next_group_cannot_start_before_full_audit(postgres_store):
    store=postgres_store;plan,context=task(store);apply(store,plan,context)
    campaign=store.get_cvp_group(context.run_id)["campaign_sha256"]
    with pytest.raises(ChangeSetConflict,match="previous original group"):
        store.prepare_cvp_group(campaign_sha256=campaign,group_key="next",sequence=2,claim_ids=["CL-2"],materials={})


def test_derived_invalidations_have_complete_actual_changelog(postgres_store):
    store=postgres_store;plan,context=task(store,existing=True)
    oid=plan.operations[0].object_id
    dependency={"dependency_id":"DEP-"+uuid.uuid4().hex,"revision":1,"status":"current",
                "dependency_manifest":[{"collection":"source_documents","record_id":oid}],
                "review_status":"system_verified","visibility":"internal"}
    with store.connect() as conn:
        conn.execute("""INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload)
                       VALUES ('product_dependencies',%s,1,%s,%s::jsonb)""",
                     (dependency["dependency_id"],record_content_sha(dependency),canonical_json(dependency)))
    apply(store,plan,context)
    history=store.get_change_history(plan.change_set_id)
    assert [op["operation"] for op in history["operations"]]==["update","invalidate","create"]
    assert [op["collection"] for op in history["operations"]]==["source_documents","product_dependencies","impact_events"]
    assert history["operations"][1]["before_sha256"]==record_content_sha(dependency)
    assert all(op["after"] is not None for op in history["operations"])


def test_untracked_trigger_rejected_before_business_write(postgres_store):
    store=postgres_store;plan,context=task(store)
    with store.connect() as conn:
        conn.execute("""CREATE FUNCTION wang_knowledge.fixture_trigger() RETURNS trigger
                        LANGUAGE plpgsql AS $$ BEGIN
                          UPDATE wang_knowledge.objects SET visibility='unapproved'
                          WHERE object_id <> NEW.object_id;
                          RETURN NEW; END $$""")
        conn.execute("""CREATE TRIGGER fixture_hidden_write AFTER INSERT ON wang_knowledge.objects
                        FOR EACH ROW EXECUTE FUNCTION wang_knowledge.fixture_trigger()""")
    with pytest.raises(ValueError,match="untracked database triggers"):apply(store,plan,context)
    assert_rolled_back(store,plan,context)


def test_campaign_membership_cannot_change(postgres_store):
    store=postgres_store;_,context=task(store)
    campaign=store.get_cvp_group(context.run_id)["campaign_sha256"]
    with pytest.raises(ChangeSetConflict,match="original admitted campaign"):
        store.prepare_cvp_group(campaign_sha256=campaign,group_key="other",sequence=1,claim_ids=["CL-1"],materials={})


def test_output_root_change_cannot_reset_once_intents(postgres_store,tmp_path):
    store=postgres_store;_,context=task(store)
    original=tmp_path/'original'
    assert store.bind_cvp_execution_root(context.run_id,identity='proposal',root=original)==original
    assert store.bind_cvp_execution_root(context.run_id,identity='proposal',root=original)==original
    with pytest.raises(ChangeSetConflict,match='once intents'):
        store.bind_cvp_execution_root(context.run_id,identity='proposal',root=tmp_path/'another')


def test_direct_hold_column_drift_cannot_hide_in_old_payload(postgres_store):
    store=postgres_store;plan,context=task(store,existing=True)
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET review_status='human_hold' WHERE object_id=%s",(plan.operations[0].object_id,))
    with pytest.raises(ChangeSetConflict):apply(store,plan,context)
    assert store.get_cvp_group(context.run_id)['checkpoint']=='reviewed'


def test_observation_only_group_records_checkpoint_without_fake_changeset(postgres_store):
    store=postgres_store;plan,context=task(store)
    from dataclasses import replace
    empty=replace(plan,operations=(),review_events=())
    # Prepared material is reused; a no-change approval is explicit, not an
    # outcome inferred from a missing apply receipt.
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=metadata-'reviewed'-'reviewed_sha256'||'{\"checkpoint\":\"prepared\"}'::jsonb WHERE run_id=%s",(context.run_id,))
    evidence=store.get_cvp_group(context.run_id)
    approval={'package':{'payload':{}},'approval':{'fixture':True},'authorization':{'fixture':True},
              'backup':{'fixture':True},'sources':['fixture'],'read_pins':[]}
    digest=store.review_cvp_group(context.run_id,plan=empty,evidence=approval)
    empty_context=TransactionValidation(context.run_id,digest,{})
    assert apply(store,empty,empty_context)['status']=='no_content_write'
    assert apply(store,empty,empty_context)['status']=='already_applied'
    assert store.get_cvp_group(context.run_id)['checkpoint']=='applied'
    assert store.get_change_set_status(empty.fingerprint_sha256) is None
    with store.connect() as conn:assert conn.execute('SELECT count(*) FROM wang_knowledge.objects').fetchone()==(0,)


def test_changed_source_invalidates_only_affected_completed_group_in_same_transaction(postgres_store,monkeypatch):
    store=postgres_store;plan,context=task(store,existing=True);apply(store,plan,context)
    completed=store.get_cvp_group(context.run_id);campaign=completed['campaign_sha256']
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=metadata||%s::jsonb WHERE run_id=%s",
            (canonical_json({'checkpoint':'audited','revisit_required':True,'independent_audit_complete':False}),context.run_id))
    next_run=store.prepare_cvp_group(campaign_sha256=campaign,group_key='next',sequence=2,
                                    claim_ids=['CL-2'],materials={'fixture':'source affecting earlier group'})
    key=('source_documents',plan.operations[0].object_id);state=store.get_record_state(*key)
    payload={**state['payload'],'title':'later approved source revision'}
    package={'schema_version':'wang_shared_knowledge_v1.3','package_id':'later-source','source_documents':[payload]}
    later=store.plan_package(package,transactional=True)
    # A pin to the earlier original Claim is the declaration that it belongs
    # to this affected graph. This fixture tests the transaction protocol only.
    read_pins={key:(state['revision'],state['content_sha256'],False),('claims','CL-1'):None}
    evidence=deepcopy(completed['reviewed']['evidence']);evidence['package']={'payload':package}
    evidence['read_pins']=[{'collection':c,'object_id':oid,'pin':list(pin) if pin else None}
                           for (c,oid),pin in sorted(read_pins.items())]
    digest=store.review_cvp_group(next_run,plan=later,evidence=evidence)
    current=TransactionValidation(next_run,digest,read_pins)
    original=store._assert_transaction_edges_and_reviews
    monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',lambda *args:(_ for _ in ()).throw(ValueError('late failure')))
    with pytest.raises(ValueError,match='late failure'):apply(store,later,current)
    assert store.get_cvp_group(context.run_id)['checkpoint']=='audited'
    monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',original)
    apply(store,later,current)
    previous=store.get_cvp_group(context.run_id)
    assert previous['checkpoint']=='applied' and previous['revisit_required'] is True
    assert previous['independent_audit_complete'] is False
    assert previous['pending_audit_from_change_set_ids']==[later.change_set_id]


def test_indexable_reference_batches_match_recursive_equality_for_nested_object_payloads(postgres_store):
    import json
    ids=['needle-'+str(i) for i in range(19)]+['quoted"\\needle']
    payloads=[{'source_id':'query-'+str(i),'title':'reference test','source_type':'sermon_transcript',
        'extension':{'record_ids':[[ids[i]]], 'nested':[{'record_id':ids[i]}],
                     'plain_text':'prefix '+ids[i]+' suffix'}} for i in range(len(ids))]
    postgres_store.apply_plan(postgres_store.plan_package({'package_id':'reference-query-counterexamples',
                                                         'source_documents':payloads}))
    with postgres_store.connect() as conn,conn.cursor() as cursor:
        paths=['$.** ? (@ == '+json.dumps(i)+')' for i in ids]
        cursor.execute("SELECT collection,object_id,payload FROM wang_knowledge.objects WHERE retired_at IS NULL "
            "AND collection=ANY(%s) AND payload @? ANY(%s::jsonpath[]) ORDER BY collection,object_id",(['source_documents'],paths))
        old=cursor.fetchall()
        new=postgres_store._reference_consumers(cursor,ids+ids[:3],['source_documents'])
        assert new==old and len(new)==len(ids)
        assert postgres_store._reference_consumers(cursor,[],['source_documents'])==[]
        assert postgres_store._reference_consumers(cursor,ids,[])==[]
        assert postgres_store._reference_consumers(cursor,['absent'],['source_documents'])==[]
        for payload in [{'ids':[['needle']]},{'nested':[{'record_id':'needle'}]},{'v':['needle']}, {'v':'needle'},{'v':'prefix needle'}]:
            cursor.execute('SELECT %s::jsonb @? %s::jsonpath, %s::jsonb @? %s::jsonpath',
                (json.dumps(payload),'$.** ? (@ == "needle")',json.dumps(payload),'$.**.* ? (@ == "needle")'))
            left,right=cursor.fetchone();assert left==right
