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
