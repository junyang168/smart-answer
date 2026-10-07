from copy import deepcopy
import pytest
from backend.pipeline import owner_approved_link_correction as d
from backend.pipeline import source_reconciliation as c
from backend.pipeline.source_reconciliation_runner import write,read,body,Runner,Stop


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
