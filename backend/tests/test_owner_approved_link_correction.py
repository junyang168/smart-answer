from copy import deepcopy
from pathlib import Path
import pytest
from backend.pipeline import owner_approved_link_correction as d
from backend.pipeline import source_reconciliation as c
from backend.pipeline.source_reconciliation_runner import write,read,body,Runner,Stop


def test_old_audit_frames_built_once_return_isolated_exact_rows(monkeypatch):
    from types import SimpleNamespace
    before={"claims/C":{"claim_id":"C","revision":1,"review_status":"old","nested":{"long":"原文","a":"保留"}}}
    count=[];actual=d.jsonb_order
    def instrument(value):count.append(1);return actual(value)
    monkeypatch.setattr(d,"jsonb_order",instrument)
    queries=[]
    retired=[{"object_id":"R","payload":{"claim_id":"R"},"retired":True}]
    sql=SimpleNamespace(_query=lambda q:queries.append(q) or deepcopy(retired))
    store=d.HistoricalAuditStore(before,sql)
    first=store.collection("claims");n=len(count)
    first[0]["payload"]["nested"]["a"]="tampered"
    second=store.collection("claims")
    assert len(count)==n and second[0]["payload"]["nested"]["a"]=="保留"
    assert before["claims/C"]["nested"]["a"]=="保留"
    assert store.collection("claims",include_retired=True)[1]["object_id"]=="R"
    store.collection("claims",include_retired=True)
    assert len(queries)==1 and len(count)==n
    with pytest.raises(ValueError):store.collection("foreign")


def fixture():
    claim = {"claim_id":"C","revision":3,"statement":"完整判斷","review_status":"independent_terminal_reviewed"}
    old = {"viewpoint_claim_link_id":"L","claim_id":"C","pinned_claim_revision":3,"viewpoint_id":"V",
        "validated_against_viewpoint_revision_id":"VR","decision_id":"D","revision":1,
        "link_type":"equivalent_component","effective_state":"active","evidence_bindings":[],
        "component_locator":{"claim_sha256":c.semantic_record_sha(claim),"statement_component":claim["statement"],
            "canonical_spans":[{"start_char":0,"end_char":4,"exact_text":claim["statement"]}]}}
    states = {"claims/C":claim,"viewpoint_claim_links/L":old}
    new = d.target_postimage(old,claim,states,"equivalent_full")
    packet = {"before_link":old,"after_link":new,"item":{"claim":claim},"context_records":c.rows(states),"sources":[],
        "authorization":{"schema_version":d.VERSION,"authorization_quote":"同意，仅这一连接",
            "human_content_approval":False,"automatic_future_repairs_authorized":False,
            "before_link_sha256":c.sha256_json(old),"after_link_sha256":c.sha256_json(new)}}
    return claim,old,states,packet


def test_whole_storage_shape_does_not_delete_or_change_claim_or_original_locator():
    claim,old,states,packet = fixture(); saved = deepcopy((claim,old,states))
    d.validate_target(packet)
    assert packet["after_link"]["component_locator"] is None
    assert packet["after_link"]["revision"] == 2
    assert (claim,old,states) == saved


@pytest.mark.parametrize("field",["claim_id","viewpoint_id","validated_against_viewpoint_revision_id","pinned_claim_revision",
    "decision_id","evidence_bindings","effective_state","revision","component_locator"])
def test_approval_cannot_expand_exact_link_scope(field):
    *_,packet = fixture()
    packet["after_link"][field] = "drift"
    with pytest.raises(ValueError): d.validate_target(packet)


@pytest.mark.parametrize("problem",["partial","hash","claim_version","human_hold","inactive","existing_full","supports"])
def test_only_exact_full_locator_without_conflicting_membership_or_hold(problem):
    claim,old,states,_ = fixture()
    if problem == "partial": old["component_locator"]["canonical_spans"][0]["end_char"] = 3
    if problem == "hash": old["component_locator"]["claim_sha256"] = "0"*64
    if problem == "claim_version": old["pinned_claim_revision"] = 2
    if problem == "human_hold": claim["review_status"] = "human_review_required"
    if problem == "inactive": old["effective_state"] = "retired"
    if problem == "existing_full": states["viewpoint_claim_links/OTHER"] = {**old,"viewpoint_claim_link_id":"OTHER","link_type":"equivalent_full"}
    with pytest.raises(ValueError): d.target_postimage(old,claim,states,"supports" if problem == "supports" else "equivalent_full")


@pytest.mark.parametrize("field,value",[("human_content_approval",True),("automatic_future_repairs_authorized",True),
    ("authorization_quote",""),("before_link_sha256","drift"),("after_link_sha256","drift")])
def test_execution_approval_is_not_content_approval_or_future_blanket_authority(field,value):
    *_,packet = fixture(); packet["authorization"][field] = value
    with pytest.raises(ValueError): d.validate_target(packet)


def test_new_review_actual_request_lossless_and_closed_response_schema():
    *_,packet = fixture(); obj = d.adapter()
    proof = obj.request_encoding_proof(packet)
    assert proof["exact_roundtrip_verified"]
    assert proof["restored_payload_sha256"] == c.sha256_json(packet)
    assert obj.model_id == "claude-opus-5-5" and obj.backend == "claude_subscription"
    assert obj.request_bytes(packet) < 500000


def test_rewind_rejects_drift_and_preserves_other_records():
    before = {"viewpoint_claim_links/L":{"revision":1},"claims/C":{"revision":3,"statement":"unchanged"}}
    op = {"operation":"update","collection":"viewpoint_claim_links","object_id":"L",
        "before_revision":1,"after_revision":2,"before_sha256":"0"*64,"after_sha256":"1"*64,"payload":{"revision":2}}
    plan = {"operations":[op],"change_set_id":"KCS-test"}
    after = {**before,"viewpoint_claim_links/L":{"revision":2}}
    assert d.rewind(plan,after,before) == before
    after["viewpoint_claim_links/L"]["unapproved"] = True
    with pytest.raises(ValueError): d.rewind(plan,after,before)


def test_legacy_complete_without_child_keeps_old_exact_shape():
    states = {"claims/C":{"revision":1}}
    assert d.source_projection({},states,None) is states


def test_multiple_child_proofs_cannot_reset_or_choose_favorable_result():
    with pytest.raises(ValueError,match="competing"):
        d.source_projection({"authorized_link_corrections":[{},{}]}, {}, None)


def completed_projection_fixture(monkeypatch):
    from backend.pipeline import source_reconciliation_epoch as epoch
    before={'viewpoint_claim_links/L':{'revision':1},
        'claims/C':{'revision':3,'statement':'unchanged','nested':{'text':'完整來源'}}}
    plan={'change_set_id':'child','operations':[
        {'operation':'update','collection':'viewpoint_claim_links','object_id':'L',
         'before_revision':1,'after_revision':2,'before_sha256':'0'*64,'after_sha256':'1'*64,
         'payload':{'revision':2}},
        *({'operation':'create','collection':collection,'object_id':oid,
           'before_revision':None,'after_revision':1,'before_sha256':None,
           'after_sha256':'2'*64,'payload':{'revision':1}}
          for collection,oid in [('viewpoint_identity_candidates','I'),('viewpoint_identity_decisions','D')])
    ]}
    for key,payload in before.items():
        collection,oid=c.split_key(key)
        payload[c.KNOWLEDGE_COLLECTIONS[collection][1]]=oid
    for row in plan['operations']:
        row['payload'][c.KNOWLEDGE_COLLECTIONS[row['collection']][1]]=row['object_id']
    current=deepcopy(before)
    for row in plan['operations']:
        current[c.key(row['collection'],row['object_id'])]=deepcopy(row['payload'])
    current['claims/unrelated_future']={'revision':1,'statement':'must remain exactly unchanged'}
    checks=[]
    monkeypatch.setattr(d,'consume_completed_child',lambda *a:({},
        {'context_records':c.rows(before)},plan,{'change_set_id':'parent'}))
    monkeypatch.setattr(epoch,'_assert_database',lambda p,s:checks.append(('parent SQL',p['change_set_id'])))
    monkeypatch.setattr(d,'sql_applied',lambda p,s:checks.append(('child SQL',p['change_set_id'])) or True)
    def forbidden(*a,**k):pytest.fail('completed child entered full historical reconstruction')
    for name in ('child_proof','validate_child','validate_parent','historical_audit_proofs','rewind'):
        monkeypatch.setattr(d,name,forbidden)
    return before,current,checks


def test_completed_projection_consumes_saved_result_preserves_entire_current_population(monkeypatch):
    before,current,checks=completed_projection_fixture(monkeypatch)
    original=deepcopy(current)
    projected=d.source_projection({'authorized_link_corrections':[{'path':'saved'}]},current,object())
    assert projected=={**before,'claims/unrelated_future':original['claims/unrelated_future']}
    assert checks==[('parent SQL','parent'),('child SQL','child')]
    projected['viewpoint_claim_links/L']['revision']=99
    assert current==original


@pytest.mark.parametrize('drift',['retained','written','missing','parent SQL','child SQL'])
def test_completed_child_checks_actual_affected_state_and_sql_every_time(monkeypatch,drift):
    from backend.pipeline import source_reconciliation_epoch as epoch
    before,current,checks=completed_projection_fixture(monkeypatch)
    complete={'authorized_link_corrections':[{'path':'saved'}]}
    d.source_projection(complete,current,object())
    if drift=='retained':current['claims/C']['revision']=4
    if drift=='written':current['viewpoint_claim_links/L']['revision']=3
    if drift=='missing':del current['viewpoint_identity_candidates/I']
    if drift=='parent SQL':
        def rejected(*a):raise ValueError('actual parent SQL drift')
        monkeypatch.setattr(epoch,'_assert_database',rejected)
    if drift=='child SQL':monkeypatch.setattr(d,'sql_applied',lambda *a:False)
    with pytest.raises(ValueError):d.source_projection(complete,current,object())


def completed_child_files(tmp_path,monkeypatch):
    """Consumer framing fixture; real model/plan reconstruction tested separately."""
    from backend.pipeline import source_reconciliation_epoch as epoch
    parent=tmp_path/'parent';parent.mkdir()
    write(parent/'effective-package.json',{'original_parent':'complete'})
    *_,packet=fixture()
    packet['source_root']=str(parent)
    ident='LR-'+c.sha256_json({'parent':d.binding(parent/'effective-package.json'),
        'before_link':packet['before_link'],'after_link':packet['after_link']})
    root=parent/'authorized-link-corrections'/ident;root.mkdir(parents=True)
    adjudication=parent/'audits'/'original'/'own-source-adjudication';adjudication.mkdir(parents=True)
    packet['adjudication_root']=str(adjudication)
    packet['handoff']={'verdict':'audit_objection_upheld','actual':'saved'}
    for name in ('packet.json','raw-gpt.json','gpt-final-handoff.json'):
        write(adjudication/name,packet['handoff'] if name=='gpt-final-handoff.json' else {'actual':name})
    packet['handoff']=read(adjudication/'gpt-final-handoff.json')
    packet['prior_bindings']=[d.binding(adjudication/name) for name in ('packet.json','raw-gpt.json','gpt-final-handoff.json')]
    audit=adjudication.parent/'independent-audit'/'audit.json';audit.parent.mkdir()
    write(audit,{'original':'audit'});packet['original_audit']=d.binding(audit)
    packet['old_audit_source_proofs']=[d.binding(audit)]
    package={'actual':'unchanged-approved-package'}
    plan={'change_set_id':'child','operations':[]}
    dump=tmp_path/'dump';dump.write_text('recoverable backup')
    write(root/'backup.json',{'dump':d.binding(dump),'restore_catalog_nonempty':True,'restore_list_exit_code':0})
    write(root/'apply-intent.json',{'plan_sha256':c.sha256_json(plan),'package_sha256':c.sha256_json(package),
        'backup':d.binding(root/'backup.json')})
    write(root/'apply-receipt.json',{'status':'applied','change_set_id':'child','summary':{
        'operations':3,'created':2,'updated':1,'retired':0,'revived':0,'invalidated_dependencies':0}})
    for name,value in {'packet.json':packet,'initial_review.packet.json':packet,
            'initial_review.raw.json':{'original':'Opus'},'initial_review.reservation.json':{'original':'once'},
            'before.json':{'records':'must NEVER be parsed'},'preflight.json':{'actual':'passed'},
            'effective-package.json':package,'plan.json':plan}.items():write(root/name,value)
    write(parent/'readback.json',{'post_fingerprint_sha256':'a'*64,'post_records':[]})
    proof={'schema_version':d.VERSION,'status':'applied_readback_verified','master_data_mutations':3,
        'source_root':str(parent),'source_update_id':'update','change_set_id':'child',
        'before_sha256':'a'*64,'post_sha256':'b'*64,'bindings':[d.binding(root/n) for n in d.PROOF_FILES]}
    write(root/'verified-result.json',proof)
    bound=d.binding(root/'verified-result.json')
    complete={'update_id':'update','authorized_link_corrections':[bound]}
    monkeypatch.setattr(epoch,'load_completed',lambda _:({}, {'update_id':'update'}, {}, deepcopy(complete)))
    monkeypatch.setattr(d,'review_raw',lambda *a:{'actual':'Opus'})
    monkeypatch.setattr(d,'package_for',lambda *a:package)
    monkeypatch.setattr(d,'assert_plan',lambda *a:None)
    actual_read=d.read
    def no_snapshot_read(path):
        if Path(path).name in {'before.json','frozen.json','readback.json'}:
            pytest.fail('completed child JSON-parsed a full historical snapshot')
        return actual_read(path)
    monkeypatch.setattr(d,'read',no_snapshot_read)
    def forbidden(*a,**k):pytest.fail('completed child rebuilt already completed history')
    for name in ('child_proof','validate_parent','validate_child','historical_audit_proofs'):
        monkeypatch.setattr(d,name,forbidden)
    return root,bound,complete,(proof,packet,plan,{})


def test_completed_child_consumes_entire_saved_proof_and_packet_without_snapshot_rebuild(tmp_path,monkeypatch):
    root,bound,complete,reference=completed_child_files(tmp_path,monkeypatch)
    result=d.consume_completed_child(bound,complete)
    assert tuple(body(v) for v in result)==reference


@pytest.mark.parametrize('drift',['proof_status','proof_missing_pin','proof_source','parent','parent_postimage',
    'raw_file','before_file','intent','backup','invalidated_dependencies'])
def test_completed_child_refuses_incomplete_or_changed_original_proofs_without_fallback(tmp_path,monkeypatch,drift):
    root,bound,complete,_=completed_child_files(tmp_path,monkeypatch)
    proof=body(read(root/'verified-result.json'))
    if drift.startswith('proof_'):
        if drift=='proof_status':proof['status']='not complete'
        if drift=='proof_missing_pin':proof['bindings'].pop()
        if drift=='proof_source':proof['source_update_id']='different'
        write(root/'verified-result.json',proof,immutable=False)
        bound=d.binding(root/'verified-result.json');complete['authorized_link_corrections']=[bound]
    elif drift=='parent':complete['authorized_link_corrections']=[]
    elif drift=='parent_postimage':
        write(root.parent.parent/'readback.json',{'post_fingerprint_sha256':'c'*64,'post_records':[]},immutable=False)
    else:
        name={'raw_file':'initial_review.raw.json','before_file':'before.json','intent':'apply-intent.json',
              'backup':'backup.json','invalidated_dependencies':'apply-receipt.json'}[drift]
        value=body(read(root/name))
        if drift=='invalidated_dependencies':value['summary']['invalidated_dependencies']=1
        else:value['changed']='not original'
        write(root/name,value,immutable=False)
        # Even a resealed completion cannot quietly discard unbound effects.
        if drift=='invalidated_dependencies':
            proof['bindings']=[d.binding(root/n) for n in d.PROOF_FILES]
            write(root/'verified-result.json',proof,immutable=False)
            bound=d.binding(root/'verified-result.json');complete['authorized_link_corrections']=[bound]
    with pytest.raises(ValueError):d.consume_completed_child(bound,complete)


def test_sql_gate_rejects_missing_root_before_any_write():
    with pytest.raises(ValueError,match="root"):
        d.validate_apply_plan(None,{})


@pytest.mark.parametrize("failure",["rejected","problem","missing","duplicate","foreign","provider_tamper"])
def test_sole_opus_actual_answer_must_cover_exact_target_without_partial_pass(tmp_path,failure):
    *_,packet = fixture(); obj = d.adapter(); proof = obj.request_encoding_proof(packet)
    expected = {"model_id":obj.model_id,"backend":obj.backend,"prompt_sha256":obj.prompt_sha256,
        "generation_config_sha256":obj.generation_config_sha256,"request_payload_sha256":c.sha256_json(packet),
        "request_encoding_proof":proof,"request_bytes":obj.request_bytes(packet),"stage":"initial_review","automatic_retry":False}
    verdict = {"claim_id":"C","approved":True,"reason":"exact source matches","problems":[]}
    response = {"verdicts":[verdict]}
    if failure == "rejected": verdict["approved"] = False
    if failure == "problem": verdict["problems"] = ["new scope"]
    if failure == "missing": response["verdicts"] = []
    if failure == "duplicate": response["verdicts"].append(deepcopy(verdict))
    if failure == "foreign": verdict["claim_id"] = "OTHER"
    raw = {**expected,"response":response,"response_sha256":c.sha256_json(response),"provider_raw":None,
        "provider_raw_sha256":c.sha256_json(None),"completed_at":"2026-10-07T08:00:00+00:00"}
    if failure == "provider_tamper": raw["provider_raw"] = "changed"
    write(tmp_path/"initial_review.reservation.json",expected); write(tmp_path/"initial_review.raw.json",raw)
    with pytest.raises(ValueError): d.review_raw(tmp_path,packet)


def test_call_intent_survives_no_answer_and_never_repeats(tmp_path):
    from types import SimpleNamespace
    obj = d.adapter(); count = []
    def generate(packet):
        count.append(1)
        raise RuntimeError("timeout, outcome unknown")
    obj.generate = generate
    class Ports:
        def adapter(self,stage): return obj
        def before_model_call(self,stage): pass
    runner = Runner(tmp_path.parent,Ports()); runner.root = tmp_path
    with pytest.raises(Stop): runner.call("initial_review",{"target":"L"})
    assert (tmp_path/"initial_review.reservation.json").exists()
    assert not (tmp_path/"initial_review.raw.json").exists()
    with pytest.raises(Stop,match="reserved call"): runner.call("initial_review",{"target":"L"})
    assert count == [1]


def test_new_identity_approval_is_single_fresh_opus_not_old_or_human():
    *_,packet = fixture()
    old = packet["before_link"]
    packet["context_records"] += c.rows({
        "viewpoint_identity_decisions/D":{"identity_decision_id":"D","identity_candidate_id":"I"},
        "viewpoint_identity_candidates/I":{"identity_candidate_id":"I","coverage_snapshot_id":"COV"}})
    raw = {"artifact_sha256":"a"*64,"completed_at":"2026-10-07T08:00:00+00:00"}
    package = d.package_for(packet,raw,None)
    decision = package["viewpoint_identity_decisions"][0]
    assert decision["approval_basis"] == "owner_scoped_independent_terminal_review"
    assert decision["reviewer_model_ids"] == ["claude-opus-5-5"]
    assert decision["semantic_call_artifact_sha256s"] == ["a"*64]
    assert decision["review_status"] == "system_approved"
    assert decision["identity_decision_id"] != old["decision_id"]
    from backend.api.canonical_repository.knowledge_models import ViewpointIdentityDecisionRecord
    ViewpointIdentityDecisionRecord.model_validate(decision)
    candidate = package["viewpoint_identity_candidates"][0]
    identity = {"claims":candidate["candidate_claim_ids"],"viewpoints":candidate["candidate_viewpoint_ids"],
        "relations":candidate.get("seed_relation_ids",[]),"action":candidate["proposed_action"],
        "blockers":candidate.get("blocker_codes",[]),"coverage_snapshot_id":candidate.get("coverage_snapshot_id"),
        "generation_fingerprint":candidate["generation_fingerprint"]}
    assert candidate["identity_candidate_id"] == "VIC-"+c.sha256_json(identity)[:20]
    assert decision["input_sha256"] == candidate["generation_fingerprint"]


def test_historical_jsonb_wire_order_restores_signature_without_semantic_change():
    value = {"zzzz":1,"中文":2,"aa":3,"a":4,"rows":[{"long_key":1,"k":2}]}
    actual = d.jsonb_order(value)
    assert list(actual) == ["a","aa","rows","zzzz","中文"]
    assert list(actual["rows"][0]) == ["k","long_key"]
    assert actual == value and c.sha256_json(actual) == c.sha256_json(value)
    assert value["rows"][0] == {"long_key":1,"k":2}


def test_saved_gpt_transport_uses_exact_original_jsonb_record_order_too():
    value = {"long_key":1,"k":2,"nested":{"zz":3,"a":4}}
    store = d.HistoricalStore({"claims/C":value})
    assert store.get_record_state("claims","C")["payload"] == value
    assert list(store.get_record_state("claims","C")["payload"]) == ["k","nested","long_key"]
    assert list(store.list_records("claims")[0]["nested"]) == ["a","zz"]


def test_planner_dispatch_requires_real_bound_package_authority(monkeypatch):
    from backend.api.canonical_repository.reviewed_candidate_contract import validate_store_package_authorization
    called = []
    monkeypatch.setattr(d,"validate_reviewed_package",lambda p: called.append(p))
    package = {"source_repair_application":{"schema_version":d.VERSION},"viewpoint_identity_decisions":[]}
    validate_store_package_authorization(package)
    assert called == [package]


def test_planner_authority_rejects_supplied_approval_without_exact_root():
    with pytest.raises(ValueError,match="authority"):
        d.validate_reviewed_package({"source_repair_application":{"schema_version":d.VERSION,"approved":True}})


def test_different_source_kind_cannot_bypass_sql_authority_before_database_access():
    from types import SimpleNamespace
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    store = object.__new__(PostgresKnowledgeStore)
    plan = SimpleNamespace(source_kind="other",operations=[SimpleNamespace(collection="viewpoint_identity_decisions",
        payload={"policy_version":d.VERSION})])
    with pytest.raises(ValueError,match="bypass"):
        store.apply_plan(plan)
