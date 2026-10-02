import copy
import hashlib
import json

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import (
    CvpProductionBlocked, validate_grouping_envelope,
)
from backend.pipeline.viewpoint_reviewed_grouping import admit_reviewed_grouping


def seal(body):
    return body | {"artifact_sha256": sha256_json(body)}


def inputs(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("原母本", encoding="utf-8")
    asset = tmp_path / "diagram.svg"
    asset.write_text("<svg>原圖</svg>", encoding="utf-8")
    groups = [{"group_key": "p001", "claim_ids": ["C1"], "rationale": "原論證邊界", "unit_id": "u1"}]
    manifest = seal({
        "schema_version": "wang_exegesis_grouping_manifest_v1", "status": "complete",
        "scope": "Matthew only", "groups": groups, "unresolved": [], "unprocessed_unit_ids": [],
        "units": [{"unit_id": "u1", "passage_key": "Matt.1.1-Matt.1.17"}],
        "sources": [{"source_id": "S1", "path": str(source), "file_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                     "linked_files": [{"path": str(asset), "file_sha256": hashlib.sha256(asset.read_bytes()).hexdigest()}]}],
        "claim_packets": [{"claim_id": "C1", "source_id": "S1", "statement": "原主張",
                           "claim_revision": 2, "evidence_steps": [{"evidence_step_id": "E1",
                           "fragments": [{"fragment_id": "F1", "verbatim_excerpt": "<svg>原圖</svg>"}]}]}],
    })
    validation = seal({"missing": 0, "duplicate": 0, "foreign": 0, "semantic_passed": True,
                       "group_ceiling_pass": True, "primary_unchanged": True, "provenance_preserved": True})
    physical = seal({"manifest_sha256": manifest["artifact_sha256"], "verbatim_errors": [], "svg_crossing_quotes": []})
    acceptance = seal({
        "status": "accepted", "semantic_passed": True, "primary_unchanged": True,
        "claim_source_revision_evidence_secondary_graph_unchanged": True,
        "current_database_graph_verified_read_only": True, "physical_source_shas_verified": True,
        "manifest_sha256": manifest["artifact_sha256"], "job_validation_sha256": validation["artifact_sha256"],
        "physical_review_evidence_sha256": physical["artifact_sha256"],
        "missing": 0, "duplicate": 0, "foreign": 0, "unresolved": 0, "claims": 1, "groups": 1,
        "model": "claude-opus-5-5", "reviewer_model": "gpt-6.1-sol",
    })
    artifacts = {"manifest": manifest, "validation": validation, "acceptance": acceptance, "physical_review": physical}
    bindings = {}
    for role, artifact in artifacts.items():
        path = tmp_path / f"{role}.json"
        path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
        bindings[role] = {"path": str(path), "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "artifact_sha256": artifact["artifact_sha256"]}
    scope = {"scope_label": "Matthew", "claims": [{"claim_id": "C1", "source_id": "S1", "statement": "原主張",
             "pinned_claim_revision": 2, "evidence": [{"evidence_step_id": "E1", "source_fragment_id": "F1",
             "verbatim_excerpt": "<svg>原圖</svg>", "source_modality": "visual", "visual_locator": "SVG01"}]}]}
    scope["packet_sha256"] = sha256_json(scope)
    freeze = {"scope_packet_sha256": scope["packet_sha256"], "artifact_sha256": "freeze", "cvp_policy_sha256": "policy"}
    return dict(**artifacts, expected_manifest_sha256=manifest["artifact_sha256"], scope_packet=scope, freeze=freeze, bindings=bindings)


def test_lossless_admission_retains_boundaries_and_order(tmp_path):
    args = inputs(tmp_path)
    envelope = admit_reviewed_grouping(**args)
    assert envelope["grouping"]["groups"] == [{key: group[key] for key in ("group_key", "claim_ids", "rationale")} for group in args["manifest"]["groups"]]
    assert envelope["grouping_reexecuted"] is False
    assert envelope["coverage_repairs"] == []
    assert envelope["upstream_reviewer_model"] == "gpt-6.1-sol"
    validate_grouping_envelope(envelope, freeze=args["freeze"])


@pytest.mark.parametrize("mutation", ["statement", "revision", "quote", "graph", "missing", "duplicate"])
def test_current_scope_drift_fails(tmp_path, mutation):
    args = inputs(tmp_path)
    claim = args["scope_packet"]["claims"][0]
    if mutation == "statement":
        claim["statement"] = "替換主張"
    elif mutation == "revision":
        claim["pinned_claim_revision"] += 1
    elif mutation == "quote":
        claim["evidence"][0]["verbatim_excerpt"] = "改寫圖表"
    elif mutation == "graph":
        claim["evidence"][0]["source_fragment_id"] = "F2"
    elif mutation == "missing":
        args["scope_packet"]["claims"] = []
    else:
        args["scope_packet"]["claims"].append(copy.deepcopy(claim))
    with pytest.raises(CvpProductionBlocked):
        admit_reviewed_grouping(**args)


@pytest.mark.parametrize("role", ["manifest", "validation", "acceptance", "physical_review"])
def test_runtime_reopens_upstream_files(tmp_path, role):
    args = inputs(tmp_path)
    envelope = admit_reviewed_grouping(**args)
    (tmp_path / f"{role}.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CvpProductionBlocked, match="SHA drift"):
        validate_grouping_envelope(envelope, freeze=args["freeze"])


def test_self_consistent_boundary_edit_cannot_replace_accepted_groups(tmp_path):
    args = inputs(tmp_path)
    envelope = admit_reviewed_grouping(**args)
    envelope["grouping"]["groups"][0]["rationale"] = "新邊界"
    envelope.pop("artifact_sha256")
    envelope = seal(envelope)
    with pytest.raises(CvpProductionBlocked, match="boundaries/order"):
        validate_grouping_envelope(envelope, freeze=args["freeze"])


def test_omitted_upstream_binding_fails(tmp_path):
    args = inputs(tmp_path)
    args["bindings"].pop("physical_review")
    with pytest.raises(CvpProductionBlocked, match="all four"):
        admit_reviewed_grouping(**args)


@pytest.mark.parametrize("name", ["source.md", "diagram.svg"])
def test_runtime_reopens_physical_source_and_linked_asset(tmp_path, name):
    args = inputs(tmp_path)
    envelope = admit_reviewed_grouping(**args)
    (tmp_path / name).write_text("changed", encoding="utf-8")
    with pytest.raises(CvpProductionBlocked, match="physical source/linked asset SHA drift"):
        validate_grouping_envelope(envelope, freeze=args["freeze"])
