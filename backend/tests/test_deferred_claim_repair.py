"""Synthetic-only deferred Claim contract and downstream impact tests."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from backend.pipeline import source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as r
from backend.pipeline import source_reconciliation_runtime as runtime
from backend.pipeline import deferred_claim_repair as d
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha, sha256_json
from backend.pipeline.viewpoint_resolution_runtime import model_request_identity


def put(path,value):
    r.write(path,value)
    return c.FileBinding.model_validate(r.binding(path))


@pytest.fixture
def case(tmp_path,monkeypatch):
    path = tmp_path / "source.json"
    path.write_text(json.dumps({"script":[{"text":"甲成立，也支持乙。"}]},ensure_ascii=False))
    bound = c.FileBinding.model_validate(r.binding(path))
    projection = c.source_snapshot(bound)
    states = {
        "source_documents/S":{"source_id":"S","source_type":"sermon_transcript","locator_space":"spoken_body_v1",
            "source_path":str(path),"source_file_sha256":bound.file_sha256,"source_body_sha256":projection["body_sha256"],"source_sha256":projection["body_sha256"]},
        "claims/C":{"claim_id":"C","statement":"甲与乙成立。","revision":1,"review_status":"candidate",
            "evidence_step_ids":["E"],"eligible_evidence_step_ids":["E"],"context_evidence_step_ids":[],"withheld_evidence_step_ids":[],
            "occurrences":[{"source_id":"S","anchors":[]}]},
        "evidence_steps/E":{"evidence_step_id":"E","statement":"甲成立。","source_fragment_ids":["F"],"produced_claim_ids":["C"],"valid_for_identity_review":True,"support_eligibility":"eligible_candidate","revision":1},
        "source_fragments/F":{"fragment_id":"F","source_id":"S","paragraph_key":"S0001","verbatim_excerpt":"甲成立"}}
    item = {"item_id":"C:1","claim_id":"C","claim_revision_sha256":semantic_record_sha(states["claims/C"]),"statement":"甲与乙成立。",
        "component":{"disposition":"deferred"},"evidence":[{"evidence_step_id":"E","source_fragment_id":"F","evidence_statement":"甲成立。","verbatim_excerpt":"甲成立","paragraph_key":"S0001"}],
        "source_binding":{"path":str(path),"file_sha256":bound.file_sha256,"body_sha256":projection["body_sha256"]},
        "source_paragraphs":[{"paragraph_key":"S0001","text":"甲成立，也支持乙。","text_sha256":sha256_json("甲成立，也支持乙。")}]}
    packet_body = {"schema_version":"wang_deferred_source_investigation_v1","items":[item]}
    packet = {**packet_body,"artifact_sha256":sha256_json(packet_body)}
    finding = {"item_id":"C:1","diagnosis":"source_support_without_bound_evidence","evidence_step_ids":["E"],"paragraph_key":"S0001","exact_quote":"也支持乙","reason":"旧引文缺乙"}
    monkeypatch.setattr(d,"diagnostic_adapter",lambda provider,model: runtime.model_adapter("proposal" if provider == "codex" else "initial_review"))
    raws = []
    for label,provider,model in (("gpt","codex","gpt-6.1-sol"),("opus","claude","claude-opus-5-5")):
        response = {"findings":[finding]}
        raws.append(put(tmp_path / (label+".json"),{**model_request_identity(d.diagnostic_adapter(provider,model),packet),
            "response":response,"response_sha256":sha256_json(response)}))
    repair = c.DeferredRepair(claim_ids=["C"],investigation_packet=put(tmp_path / "packet.json",packet_body),
        handoff=put(tmp_path / "handoff.json",{"schema_version":"wang_deferred_source_handoff_v1","status":"investigated_not_resolved",
            "packet_sha256":packet["artifact_sha256"],"group_complete":False,"master_data_mutations":0,
            "actions":[{"item_id":"C:1","models_agree":True,"next_action":"claim_evidence_repair_authorization_required","gpt":finding,"opus":finding}]}),raw_gpt=raws[0],raw_opus=raws[1])
    return states,repair,bound


def manifest_for(case):
    states,repair,source = case
    return c.Manifest(source_id="S",before_source=source,current_sources=[source],authorization=source,
        authorized_paragraph_keys=["S0001"],permissions=d.permissions(states,"S",["C"],repair.kind),
        groups=[c.Group(group_key="g",claim_ids=["C"])],campaign_claim_ids=["C"],accepted_membership=source,
        created_at="2026-10-07T00:00:00Z",deferred_repair=repair)


def test_saved_actual_diagnosis_is_not_revision_approval(case):
    states,repair,_ = case
    assert d.verify_diagnosis(repair,states,"S")["diagnosis_is_not_revision_approval"]


@pytest.mark.parametrize("mutation",["claim","source","paragraph","evidence","hold","reply","handoff","denominator"])
def test_saved_diagnostic_or_current_state_drift_rejected(case,mutation):
    states,repair,_ = case
    if mutation == "claim": states["claims/C"]["statement"] += "新判断"
    if mutation == "source": states["source_documents/S"]["source_file_sha256"] = "0"*64
    if mutation == "evidence": states["evidence_steps/E"]["produced_claim_ids"] = []
    if mutation == "hold": states["claims/C"]["review_status"] = "human_review_required"
    if mutation == "denominator": repair.claim_ids.append("other")
    if mutation in {"reply","handoff","paragraph"}:
        b = repair.raw_opus if mutation == "reply" else repair.handoff if mutation == "handoff" else repair.investigation_packet
        obj = r.body(r.read(Path(b.path)))
        if mutation == "reply": obj["response"]["findings"][0]["diagnosis"] = "needs_human_judgment"
        if mutation == "handoff": obj["actions"][0]["models_agree"] = False
        if mutation == "paragraph": obj["items"][0]["source_paragraphs"][0]["text"] = "外来结论"
        Path(b.path).write_text(json.dumps({**obj,"artifact_sha256":sha256_json(obj)}))
        b.file_sha256 = c.file_sha256(Path(b.path))
    with pytest.raises((ValueError,KeyError)): d.verify_diagnosis(repair,states,"S")


def test_complete_cvp_member_and_reverse_consumer_context(case):
    states,_,_ = case
    states.update({"claims/D":{"claim_id":"D","evidence_step_ids":["E2"]},
        "evidence_steps/E2":{"evidence_step_id":"E2","source_fragment_ids":["F2"]},
        "source_fragments/F2":{"fragment_id":"F2","source_id":"S"},
        "viewpoint_claim_links/L":{"viewpoint_claim_link_id":"L","claim_id":"C","viewpoint_id":"V"},
        "viewpoint_claim_links/L2":{"viewpoint_claim_link_id":"L2","claim_id":"D","viewpoint_id":"V"},
        "canonical_viewpoints/V":{"viewpoint_id":"V","current_revision_id":"VR"},
        "viewpoint_revisions/VR":{"viewpoint_revision_id":"VR","viewpoint_id":"V"},
        "questions/Q":{"question_id":"Q","answer_claim_ids":["C"]},"unknown/U":{"id":"U","claim_id":"C"}})
    inv = c.claim_inventory(states,"S",["C"])
    assert {"viewpoint_claim_links/L2","questions/Q","canonical_viewpoints/V"} <= set(inv["affected_keys"])
    assert {"claims/D","evidence_steps/E2","source_fragments/F2","source_documents/S"} <= set(inv["context_keys"])
    assert inv["unknown_keys"] == ["unknown/U"]


@pytest.mark.parametrize("kind",["evidence_binding","claim_statement"])
def test_real_evidence_hold_is_not_promoted(case,kind):
    states,_,_ = case
    states["evidence_steps/E"]["valid_for_identity_review"] = False
    with pytest.raises(ValueError,match="no eligible"): d.permissions(states,"S",["C"],kind)


def test_registry_evidence_gate_not_optional_claim_bucket(case):
    states,_,_ = case
    del states["claims/C"]["eligible_evidence_step_ids"]
    assert any(p.collection == "evidence_steps" for p in d.permissions(states,"S",["C"]))
    states["evidence_steps/E"]["support_eligibility"] = "context_only"
    with pytest.raises(ValueError,match="no eligible"): d.permissions(states,"S",["C"])


@pytest.mark.parametrize("cause",["human_judgment_required","reviewed_cvp_repair_required","unknown"])
def test_non_claim_deferral_cannot_enter_claim_writer(cause):
    with pytest.raises(ValueError): d.route([{"models_agree":True,"next_action":cause}])


def test_semantic_claim_route_does_not_force_evidence_only_repair(case):
    states,repair,_ = case
    repair.kind = "claim_statement"
    perms = d.permissions(states,"S",["C"],repair.kind)
    assert d.CONTENT_FIELDS <= set(perms[0].fields)
    assert d.route([{"models_agree":True,"next_action":"claim_source_review_required"}]) == "claim_statement"


@pytest.mark.parametrize("change",["statement","source","predicate","locator","old_pin","drop_quote","other_claim"])
def test_evidence_only_postimage_boundaries(case,change):
    states,repair,_ = case
    states["viewpoint_claim_links/L"] = {"viewpoint_claim_link_id":"L","claim_id":"C","viewpoint_id":"V","pinned_claim_revision":1,
        "link_type":"equivalent_component","effective_state":"active","component_locator":{"canonical_spans":[{"start_char":0,"end_char":1,"exact_text":"甲"}]}}
    m = manifest_for(case)
    frozen = {"manifest":m.model_dump(),"before_records":c.rows(states),"deferred_diagnosis":d.verify_diagnosis(repair,states,"S")}
    post = deepcopy(states); post["claims/C"]["revision"] = 2; post["viewpoint_claim_links/L"]["pinned_claim_revision"] = 2
    if change == "statement": post["claims/C"]["statement"] += "扩大"
    if change == "source": post["source_documents/S"]["source_body_sha256"] = "0"*64
    if change == "predicate": post["viewpoint_claim_links/L"]["link_type"] = "supports"
    if change == "locator": post["viewpoint_claim_links/L"]["component_locator"]["canonical_spans"] = []
    if change == "old_pin": post["viewpoint_claim_links/L"]["pinned_claim_revision"] = 1
    if change == "drop_quote": post["evidence_steps/E"]["source_fragment_ids"] = []
    if change == "other_claim": post["claims/other"] = {"claim_id":"other"}
    with pytest.raises(ValueError): d.validate_postimages(frozen,post)


def test_old_manifest_dump_and_old_prompt_do_not_upgrade(case):
    m = manifest_for(case); m.deferred_repair = None
    assert "deferred_repair" not in m.model_dump()
    old = runtime.model_adapter("proposal"); new = runtime.model_adapter("proposal",deferred=True)
    assert old.prompt_sha256 != new.prompt_sha256
    assert old.model_id == new.model_id == "gpt-6.1-sol"
    assert old.backend == new.backend == "codex_subscription"


def test_legal_quote_addition_retains_old_evidence(case):
    states,repair,_ = case
    frozen = {"manifest":manifest_for(case).model_dump(),"before_records":c.rows(states),"deferred_diagnosis":d.verify_diagnosis(repair,states,"S")}
    post = deepcopy(states); post["claims/C"]["revision"] = 2
    post["evidence_steps/E"]["source_fragment_ids"].append("F2")
    post["source_fragments/F2"] = {"fragment_id":"F2","source_id":"S","paragraph_key":"S0001","verbatim_excerpt":"也支持乙"}
    post["claims/C"]["occurrences"][0]["anchors"].append({"evidence_id":"E","paragraph_key":"S0001","proposed_highlight":{"text":"也支持乙"}})
    d.validate_postimages(frozen,post)
    post["claims/C"]["occurrences"][0]["anchors"] = []
    with pytest.raises(ValueError,match="still not bound"): d.validate_postimages(frozen,post)


def test_quote_hashes_are_computed_before_opus_without_changing_words(case):
    states,_,bound = case
    frozen = {"manifest":manifest_for(case).model_dump(),"before_records":c.rows(states),"after":[c.source_snapshot(bound)]}
    raw = {"records":[{"collection":"source_fragments","object_id":"F2","payload":{"fragment_id":"F2","source_id":"S",
        "paragraph_key":"S0001","verbatim_excerpt":"也支持乙","source_sha256":"","paragraph_text_sha256":"","verbatim_excerpt_sha256":""}}],
        "dispositions":[],"sentence_dispositions":[],"unadmitted_candidates":[]}
    original = deepcopy(raw)
    effective = d.normalize_proposal(frozen,raw)
    assert raw == original
    p = effective.records[0].payload
    assert p["verbatim_excerpt"] == "也支持乙"
    assert len(p["verbatim_excerpt_sha256"]) == 64
    assert effective == r.effective_proposal(frozen,raw)


def test_normalization_only_declares_explicit_new_quote_not_missing_consumer(case):
    states,_,bound = case
    frozen = {"manifest":manifest_for(case).model_dump(),"before_records":c.rows(states),"after":[c.source_snapshot(bound)]}
    raw = {"records":[{"collection":"source_fragments","object_id":"F2","payload":{"fragment_id":"F2","source_id":"S",
        "paragraph_key":"S0001","verbatim_excerpt":"也支持乙"}}],"dispositions":[],"sentence_dispositions":[],"unadmitted_candidates":[]}
    effective = d.normalize_proposal(frozen,raw)
    assert [(x.record_key,x.action) for x in effective.dispositions] == [("source_fragments/F2","update")]
    assert not any(x.record_key == "claims/C" for x in effective.dispositions)


def test_false_redundant_bucket_cannot_promote_context_evidence(case):
    states,_,bound = case
    del states["claims/C"]["eligible_evidence_step_ids"]
    claim = deepcopy(states["claims/C"]); claim["eligible_evidence_step_ids"] = []
    frozen = {"manifest":manifest_for(case).model_dump(),"before_records":c.rows(states),"after":[c.source_snapshot(bound)]}
    raw = {"records":[{"collection":"claims","object_id":"C","payload":claim}],"dispositions":[],"sentence_dispositions":[],"unadmitted_candidates":[]}
    with pytest.raises(ValueError,match="qualification"): d.normalize_proposal(frozen,raw)


def test_problem_budget_ignores_root_and_incidental_packet_context(case):
    _,repair,_ = case
    packet = d.artifact(repair.investigation_packet)
    changed = deepcopy(packet); changed["artifact_sha256"] = "0"*64; changed["unrelated_context"] = "new root"
    assert d.problem_identity(packet,"evidence_binding") == d.problem_identity(changed,"evidence_binding")
    changed["items"][0]["claim_revision_sha256"] = "1"*64
    assert d.problem_identity(packet,"evidence_binding") != d.problem_identity(changed,"evidence_binding")


def test_proposal_wire_has_no_open_objects_and_roundtrips_all_legacy_fields():
    from backend.api.canonical_repository.viewpoint_resolution import _strict_json_schema
    schema = _strict_json_schema(d.JsonProposal.model_json_schema())
    def check(s):
        if isinstance(s,dict):
            if s.get("type") == "object":
                assert isinstance(s.get("properties"),dict) and s["additionalProperties"] is False
            for v in s.values(): check(v)
        if isinstance(s,list):
            for v in s: check(v)
    check(schema)
    payload = {"statement":"他說：『不是。』","occurrences":[{"legacy":True,"anchors":[]}],"unknown_legacy":None}
    wire = {"records":[{"collection":"claims","object_id":"C","payload_json":json.dumps(payload,ensure_ascii=False)}],
        "dispositions":[],"sentence_dispositions":[],"unadmitted_candidates_json":[]}
    assert d.decode_proposal(wire)["records"][0]["payload"] == payload
    wire["records"][0]["payload_json"] = '{"statement":"甲","statement":"乙"}'
    with pytest.raises(ValueError,match="duplicate"): d.decode_proposal(wire)


@pytest.mark.parametrize("change",["last_message","stdout","timeout","raw","second_budget","state"])
def test_only_explicit_schema_400_can_authorize_mechanical_handoff(case,tmp_path,change):
    states,repair,_ = case
    root = tmp_path / "failed"; root.mkdir()
    r.write(root / "frozen.json",{"before_records":c.rows(states),"manifest":manifest_for(case).model_dump()})
    r.write(root / "proposal.reservation.json",{"intent":True})
    raw = {"last_message":None,"stdout":"","stderr":'ERROR: '+json.dumps({"status":400,"error":{"code":"invalid_json_schema","param":"text.format.schema"}})}
    if change == "last_message": raw["last_message"] = "{}"
    if change == "stdout": raw["stdout"] = "assistant result"
    if change == "timeout": raw["stderr"] = "timeout"
    if change == "raw": r.write(root / "proposal.raw.json",{"response":{}})
    if change == "state": states["claims/C"]["statement"] += "changed"
    if change == "second_budget":
        f = r.read(root / "frozen.json"); f["manifest"]["deferred_repair"]["provider_schema_rejection"] = {"path":"x","file_sha256":"0"*64}
        (root / "frozen.json").unlink(); r.write(root / "frozen.json",r.body(f))
    bound = put(root / "proposal.failure.json",{"provider_raw":raw})
    with pytest.raises(ValueError): d.verify_schema_rejection(bound,states)
