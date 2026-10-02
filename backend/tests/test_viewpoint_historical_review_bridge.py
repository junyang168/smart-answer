from copy import deepcopy
import hashlib
import json

import pytest

from backend.api.canonical_repository.postgres_store import _normalize_records, record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked
from backend.pipeline.corpus_ai_review_runner import _normalize_claim_layer
from backend.pipeline.viewpoint_historical_review_bridge import verify_historical_review_bridge, _fragment_meaning


def write_binding(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {"path": str(path), "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def inputs(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("教授原話", encoding="utf-8")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    package = {"claims": [{"claim_id": "C1", "title": "教授主張", "claim_type": "explicit_claim",
               "evidence_step_ids": ["E1"], "occurrences": [{"source_id": "S1", "anchors": [{"paragraph_key": "S0002", "evidence_id": "E1", "proposed_highlight": {"text": "教授原話"}}]}]}],
               "evidence_steps": [{"evidence_step_id": "E1", "source_fragment_id": "F1", "statement": "原論證", "support_eligibility": "eligible_candidate"}],
               "source_fragments": [{"fragment_id": "F1", "source_id": "S1", "verbatim_excerpt": "教授原話", "source_sha256": source_sha, "anchor_state": "source_version_bound"}]}
    input_binding = write_binding(tmp_path / "input.json", package)
    reviewer = {"review_model_id": "claude-sonnet-5", "review_schema_version": "wang_corpus_independent_review_v1"}
    fingerprint = sha256_json(reviewer)
    review = {"reviewer": reviewer | {"fingerprint_sha256": fingerprint, "provider": "anthropic"},
              "source": {"package_sha256": input_binding["file_sha256"], "transcript_sha256": {"S1": source_sha}},
              "reviewed_claims": _normalize_claim_layer(package)["candidate_claims"],
              "claim_reviews": [{"claim_id": "C1", "decision": "pass", "issues": [], "routing_status": "ai_reviewed", "human_review_reason": "", "reviewer_fingerprint": fingerprint}]}
    review_binding = write_binding(tmp_path / "review.json", review)
    normalized, _ = _normalize_records(package)
    before = normalized["claims"]["C1"] | {"revision": 1, "review_status": "ai_consensus_reviewed"}
    after = deepcopy(before)
    after["revision"] = 2
    after["occurrences"][0]["anchors"][0]["paragraph_key"] = "S0001"
    plan = {"plan": {"change_set_id": "KCS-cleanup", "source_sha256": "cleanup-source"},
            "source": {"source_id": "S1", "source_path": str(source), "source_file_sha256": source_sha},
            "changed_records": [{"collection": "claims", "object_id": "C1", "before_sha256": record_content_sha(before), "after_sha256": record_content_sha(after)}]}
    plan["artifact_sha256"] = sha256_json(plan)
    plan_binding = write_binding(tmp_path / "plan.json", plan)
    versions = [dict(revision=1, content_sha256=record_content_sha(before), payload=before, change_set_id="KCS-base", status="applied", source_kind="knowledge_package"),
                dict(revision=2, content_sha256=record_content_sha(after), payload=after, change_set_id="KCS-cleanup", status="applied", source_kind="wkp368_source_contract_cleanup", source_sha256="cleanup-source", metadata={"source_plan_path": plan_binding["path"], "source_plan_file_sha256": plan_binding["file_sha256"]})]
    return dict(claim_id="C1", current_claim=after, review_binding=review_binding, review_input_binding=input_binding,
                versions=versions, current_evidence=normalized["evidence_steps"], current_fragments=normalized["source_fragments"])


def test_real_parser_and_coordinate_only_chain(tmp_path):
    proof = verify_historical_review_bridge(**inputs(tmp_path))
    assert proof["status"] == "verified"
    assert proof["review_model_id"] == "claude-sonnet-5"
    assert proof["coordinate_repairs"][0]["after_revision"] == 2
    assert proof["model_calls_executed"] == proof["master_data_mutations"] == 0
    assert proof["approval_status"] == "not_human_approved"


@pytest.mark.parametrize("mutation", ["statement", "quotation", "modality", "step", "gap", "unapplied", "wrong_repair", "history_sha", "wrong_review", "source_bytes"])
def test_no_semantic_drift_can_hide_as_coordinate_repair(tmp_path, mutation):
    args = inputs(tmp_path)
    if mutation == "statement":
        args["current_claim"]["statement"] = "不同主張"
        args["versions"][-1]["content_sha256"] = record_content_sha(args["current_claim"])
    elif mutation == "quotation":
        args["current_fragments"]["F1"]["verbatim_excerpt"] = "修改引文"
    elif mutation == "modality":
        args["current_fragments"]["F1"]["source_modality"] = "visual"
    elif mutation == "step":
        args["current_evidence"]["E1"]["statement"] = "新論證"
    elif mutation == "gap":
        args["versions"] = args["versions"][:1]
    elif mutation == "unapplied":
        args["versions"][-1]["status"] = "planned"
    elif mutation == "wrong_repair":
        args["versions"][-1]["source_kind"] = "other_changeset"
    elif mutation == "history_sha":
        args["versions"][0]["content_sha256"] = "wrong"
    elif mutation == "wrong_review":
        args["review_input_binding"]["file_sha256"] = "different-generation"
    else:
        (tmp_path / "source.md").write_text("改過的母本")
    with pytest.raises(CvpProductionBlocked):
        verify_historical_review_bridge(**args)


def test_spoken_empty_facts_normalization_never_erases_visual_evidence():
    assert _fragment_meaning({"visual_facts": []}) == _fragment_meaning({"visual_facts": None, "source_modality": "spoken"})
    assert _fragment_meaning({"visual_facts": [], "source_modality": "visual"}) != _fragment_meaning({"visual_facts": [], "source_modality": "spoken"})


def human_inputs(tmp_path):
    args = inputs(tmp_path)
    review = json.loads((tmp_path / "review.json").read_text())
    review["claim_reviews"][0].update(spot_check_selected=True, routing_status="human_spot_check")
    args["review_binding"] = write_binding(tmp_path / "review.json", review)
    row = {"claim_id": "C1", "decision": "pass", "pinned_claim_revision": args["current_claim"]["revision"],
           "claim_revision_sha256": record_content_sha(args["current_claim"]), "review_binding": args["review_binding"],
           "evidence_dependency_sha256": sha256_json({eid: record_content_sha(raw) for eid, raw in sorted(args["current_evidence"].items())}),
           "fragment_dependency_sha256": sha256_json({fid: record_content_sha(raw) for fid, raw in sorted(args["current_fragments"].items())})}
    closure = {"schema_version": "wang_claim_human_spot_check_closure_v1", "confirmed_by": "human_user", "user_message": "确认", "claims": [row]}
    closure["artifact_sha256"] = sha256_json(closure)
    args["human_closure_binding"] = write_binding(tmp_path / "closure.json", closure)
    return args


def test_human_spot_check_requires_explicit_current_version_confirmation(tmp_path):
    args = human_inputs(tmp_path)
    proof = verify_historical_review_bridge(**args)
    assert proof["human_spot_check_confirmed"] is True
    assert proof["approval_status"] == "not_human_approved"  # no CVP approval implied
    args.pop("human_closure_binding")
    with pytest.raises(CvpProductionBlocked, match="resolved independent pass"):
        verify_historical_review_bridge(**args)


@pytest.mark.parametrize("mutation", ["actor", "decision", "revision", "sha", "evidence", "fragments", "review", "duplicate"])
def test_human_confirmation_cannot_release_other_versions_or_evidence(tmp_path, mutation):
    args = human_inputs(tmp_path)
    closure = json.loads((tmp_path / "closure.json").read_text())
    row = closure["claims"][0]
    if mutation == "actor":
        closure["confirmed_by"] = "ai"
    elif mutation == "duplicate":
        closure["claims"].append(deepcopy(row))
    else:
        key = {"decision": "decision", "revision": "pinned_claim_revision", "sha": "claim_revision_sha256", "evidence": "evidence_dependency_sha256", "fragments": "fragment_dependency_sha256", "review": "review_binding"}[mutation]
        row[key] = "wrong"
    closure.pop("artifact_sha256")
    closure["artifact_sha256"] = sha256_json(closure)
    args["human_closure_binding"] = write_binding(tmp_path / "closure.json", closure)
    with pytest.raises(CvpProductionBlocked):
        verify_historical_review_bridge(**args)


def test_batched_review_without_sealed_lineage_is_rejected(tmp_path):
    args = inputs(tmp_path)
    review = json.loads((tmp_path / "review.json").read_text())
    review["reviewer"]["provider"] = "anthropic_batched"
    args["review_binding"] = write_binding(tmp_path / "review.json", review)
    with pytest.raises(CvpProductionBlocked, match="authenticated sealed lineage"):
        verify_historical_review_bridge(**args)


def pair_inputs(tmp_path):
    args = inputs(tmp_path)
    package = json.loads((tmp_path / "input.json").read_text())
    package["evidence_steps"].append({"evidence_step_id": "E2", "source_fragment_id": "F2",
        "statement": "已有原文論據", "produced_claim_ids": ["C1"], "support_eligibility": "eligible_candidate"})
    package["source_fragments"].append(dict(package["source_fragments"][0], fragment_id="F2"))
    args["review_input_binding"] = write_binding(tmp_path / "input.json", package)
    review = json.loads((tmp_path / "review.json").read_text())
    review["source"]["package_sha256"] = args["review_input_binding"]["file_sha256"]
    review["reviewed_claims"] = _normalize_claim_layer(package)["candidate_claims"]
    args["review_binding"] = write_binding(tmp_path / "review.json", review)
    normalized, _ = _normalize_records(package)
    args["current_evidence"] = normalized["evidence_steps"]
    args["current_fragments"] = normalized["source_fragments"]
    before = args["versions"][0]
    after = deepcopy(before["payload"])
    after["revision"] = 2
    after["evidence_step_ids"].append("E2")
    auth = {"actions": [{"claim_id": "C1", "evidence_step_id": "E2",
        "effective_decision": "include", "reviewed_decision": "include",
        "decision_basis": "independent_model_consensus_include",
        "claim_content_sha256": before["content_sha256"],
        "evidence_content_sha256": record_content_sha(normalized["evidence_steps"]["E2"])}]}
    auth["artifact_sha256"] = sha256_json(auth)
    guard = {"expected_pair_adjudication_authorization": auth,
        "plan_identity": {"change_set_id": "KCS-pair", "source_sha256": "pair-source",
                          "source_kind": "wkp364_claim_evidence_pair_adjudication"}}
    guard["guard_sha256"] = sha256_json(guard)
    args["versions"] = [before, dict(revision=2, payload=after,
        content_sha256=record_content_sha(after), change_set_id="KCS-pair", status="applied",
        source_kind="wkp364_claim_evidence_pair_adjudication", source_sha256="pair-source",
        metadata={"claim_evidence_reciprocity_repair": {
            "store_guard": guard, "pair_adjudication_authorization_sha256": auth["artifact_sha256"]}})]
    args["current_claim"] = after
    return args


def test_adjudicated_existing_evidence_index_is_authenticated_not_reapproved(tmp_path):
    proof = verify_historical_review_bridge(**pair_inputs(tmp_path))
    assert proof["evidence_index_repairs"][0]["authorized_added_evidence_ids"] == ["E2"]
    assert proof["model_calls_executed"] == proof["master_data_mutations"] == 0
    assert proof["approval_status"] == "not_human_approved"


@pytest.mark.parametrize("mutation", ["guard_sha", "decision", "basis", "claim_sha", "step_sha",
                                     "foreign_source", "step_text", "fragment_text", "nonreciprocal",
                                     "unapplied", "statement"])
def test_pair_index_repair_never_grants_unreviewed_content(tmp_path, mutation):
    args = pair_inputs(tmp_path)
    repair = args["versions"][-1]["metadata"]["claim_evidence_reciprocity_repair"]
    guard = repair["store_guard"]
    auth = guard["expected_pair_adjudication_authorization"]
    action = auth["actions"][0]
    if mutation == "guard_sha":
        guard["guard_sha256"] = "bad"
    elif mutation in {"decision", "basis", "claim_sha", "step_sha"}:
        key = {"decision": "reviewed_decision", "basis": "decision_basis",
               "claim_sha": "claim_content_sha256", "step_sha": "evidence_content_sha256"}[mutation]
        action[key] = "bad"
        auth.pop("artifact_sha256")
        auth["artifact_sha256"] = sha256_json(auth)
        repair["pair_adjudication_authorization_sha256"] = auth["artifact_sha256"]
        guard.pop("guard_sha256")
        guard["guard_sha256"] = sha256_json(guard)
    elif mutation in {"foreign_source", "fragment_text"}:
        args["current_fragments"]["F2"]["source_id" if mutation == "foreign_source" else "verbatim_excerpt"] = "bad"
    elif mutation == "step_text":
        args["current_evidence"]["E2"]["statement"] = "未審新論據"
    elif mutation == "nonreciprocal":
        args["current_evidence"]["E2"]["produced_claim_ids"] = []
    elif mutation == "unapplied":
        args["versions"][-1]["status"] = "planned"
    else:
        args["current_claim"]["statement"] = "換了主張"
        args["versions"][-1]["content_sha256"] = record_content_sha(args["current_claim"])
    with pytest.raises(CvpProductionBlocked):
        verify_historical_review_bridge(**args)
