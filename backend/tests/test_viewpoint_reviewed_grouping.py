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


@pytest.mark.parametrize('mutation',[None,'statement','revision','quote','graph','source','scripture','evidence_statement'])
def test_new_source_admission_checks_exact_reviewed_projection_not_stale_words(tmp_path,monkeypatch,mutation):
    from backend.pipeline import viewpoint_reviewed_grouping as grouping
    from backend.pipeline import viewpoint_applied_source_epochs as epochs
    args=inputs(tmp_path)
    old=copy.deepcopy(args['manifest'])
    current=args['scope_packet']['claims'][0]
    current.update(statement='已审核新主張',pinned_claim_revision=3)
    current['evidence'][0]['verbatim_excerpt']='已校对原話'
    expected=copy.deepcopy(old['claim_packets'][0])
    expected.update(statement=current['statement'],claim_revision=3)
    expected['evidence_steps'][0]['fragments'][0]['verbatim_excerpt']='已校对原話'
    # Isolate the already-tested physical/database proof I/O; this branch
    # receives only the independently authenticated expected projection.
    monkeypatch.setattr(grouping,'validate_accepted_grouping',lambda **kw:None)
    monkeypatch.setattr(grouping,'validate_grouping_envelope',lambda *a,**kw:None)
    monkeypatch.setattr(epochs,'grouping_current_claim_packets',lambda manifest,bound,ids:{'C1':expected})
    from backend.pipeline.viewpoint_g041_source_epoch import GROUPING_EPOCH_KEY
    bound={'path':'exact-applied-proof','file_sha256':'a'*64}
    args['freeze'][GROUPING_EPOCH_KEY]=bound
    args['scope_packet'][GROUPING_EPOCH_KEY]=bound
    if mutation=='statement':current['statement']='未审新内容'
    elif mutation=='revision':current['pinned_claim_revision']=4
    elif mutation=='quote':current['evidence'][0]['verbatim_excerpt']='未审引文'
    elif mutation=='graph':current['evidence'][0]['source_fragment_id']='F-foreign'
    elif mutation=='source':current['source_id']='foreign'
    elif mutation=='scripture':current['scripture_refs']=['Matt.2.1']
    elif mutation=='evidence_statement':current['evidence'][0]['evidence_statement']='未审解释'
    args['scope_packet'].pop('packet_sha256')
    args['scope_packet']['packet_sha256']=sha256_json(args['scope_packet'])
    args['freeze']['scope_packet_sha256']=args['scope_packet']['packet_sha256']
    if mutation:
        with pytest.raises(CvpProductionBlocked):admit_reviewed_grouping(**args)
    else:
        result=admit_reviewed_grouping(**args)
        assert result['grouping']['groups']==[{k:g[k] for k in ('group_key','claim_ids','rationale')} for g in old['groups']]
        assert result['grouping_reexecuted'] is False
    assert args['manifest']==old


def test_new_source_admission_cannot_ignore_missing_proof(tmp_path,monkeypatch):
    from backend.pipeline import viewpoint_reviewed_grouping as grouping
    from backend.pipeline import viewpoint_applied_source_epochs as epochs
    from backend.pipeline.viewpoint_g041_source_epoch import GROUPING_EPOCH_KEY
    args=inputs(tmp_path)
    monkeypatch.setattr(grouping,'validate_accepted_grouping',lambda **kw:None)
    monkeypatch.setattr(epochs,'grouping_current_claim_packets',lambda *a:{})
    args['scope_packet']['claims'][0]['statement']='无精确证明的新内容'
    args['scope_packet'][GROUPING_EPOCH_KEY]=args['freeze'][GROUPING_EPOCH_KEY]={'path':'unavailable'}
    args['scope_packet'].pop('packet_sha256')
    args['scope_packet']['packet_sha256']=sha256_json(args['scope_packet'])
    with pytest.raises(CvpProductionBlocked,match='projection drift'):admit_reviewed_grouping(**args)


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


@pytest.mark.parametrize('mutation',[None,'missing_freeze','missing_packet','missing_envelope','changed_source','linked_asset','upstream','missing_proof','old_pin'])
def test_exact_applied_epoch_is_carried_through_admission_and_runtime(tmp_path,monkeypatch,mutation):
    from backend.pipeline import viewpoint_g041_source_epoch as epoch
    args=inputs(tmp_path);source=tmp_path/'source.md'
    old=args['manifest']['sources'][0]['file_sha256']
    source.write_text('owner reviewed corrected source',encoding='utf-8')
    new=hashlib.sha256(source.read_bytes()).hexdigest()
    proof={'path':'/exact/applied-package','file_sha256':'exact'}
    def transition(manifest,binding):
        assert manifest==args['manifest']
        if binding!=proof or mutation=='missing_proof':raise ValueError('exact applied proof missing')
        return {str(source):('wrong' if mutation=='old_pin' else old,new)}
    monkeypatch.setattr(epoch,'grouping_physical_transition',transition)
    args['scope_packet'][epoch.GROUPING_EPOCH_KEY]=proof
    args['scope_packet'].pop('packet_sha256');args['scope_packet']['packet_sha256']=sha256_json(args['scope_packet'])
    args['freeze']['scope_packet_sha256']=args['scope_packet']['packet_sha256']
    args['freeze'][epoch.GROUPING_EPOCH_KEY]=proof
    if mutation=='missing_freeze':args['freeze'].pop(epoch.GROUPING_EPOCH_KEY)
    if mutation=='missing_packet':args['scope_packet'].pop(epoch.GROUPING_EPOCH_KEY)
    if mutation in {'missing_freeze','missing_packet','missing_proof','old_pin'}:
        with pytest.raises(CvpProductionBlocked):admit_reviewed_grouping(**args)
        return
    envelope=admit_reviewed_grouping(**args)
    if mutation is None:
        assert envelope[epoch.GROUPING_EPOCH_KEY]==proof
        validate_grouping_envelope(envelope,freeze=args['freeze'])
        return
    if mutation=='missing_envelope':
        envelope.pop(epoch.GROUPING_EPOCH_KEY);envelope.pop('artifact_sha256');envelope=seal(envelope)
    elif mutation=='changed_source':source.write_text('unapproved',encoding='utf-8')
    elif mutation=='linked_asset':(tmp_path/'diagram.svg').write_text('drift',encoding='utf-8')
    else:(tmp_path/'acceptance.json').write_text('{}',encoding='utf-8')
    with pytest.raises(CvpProductionBlocked):validate_grouping_envelope(envelope,freeze=args['freeze'])
