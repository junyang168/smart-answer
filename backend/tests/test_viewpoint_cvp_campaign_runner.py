from copy import deepcopy

import pytest

from backend.pipeline.viewpoint_cvp_campaign_runner import validate_audit, validate_schedule

@pytest.mark.parametrize('mutation', [None, 'missing', 'wrong_sha', 'ambiguous'])
def test_legacy_merge_discovery_binds_original_archived_input(tmp_path, mutation):
    import json
    from backend.pipeline.viewpoint_cvp_campaign_runner import discover_selection, binding
    candidate = tmp_path/'reviewed/source.reviewed-candidate.json'
    candidate.parent.mkdir()
    candidate.write_text('{}')
    current = tmp_path/'cross-section/source.cross-section.json'
    current.parent.mkdir()
    current.write_text('{"generation":"later"}')
    archived = current.parent/'generations/source.cross-section.old.json'
    archived.parent.mkdir()
    archived.write_text('{"generation":"reviewed"}')
    pin = binding(archived)['file_sha256']
    review_path = tmp_path/'reviews/source.independent-review.json'
    review_path.parent.mkdir()
    review_path.write_text(json.dumps({'source': {'package_path':str(current), 'package_sha256':pin}}))
    if mutation == 'missing': archived.unlink()
    elif mutation == 'wrong_sha': archived.write_text('{"generation":"unreviewed"}')
    elif mutation == 'ambiguous': archived.with_name('source.cross-section.copy.json').write_bytes(archived.read_bytes())
    inventory = {'C1': {'variants': [{'path':str(candidate), 'file_sha256':binding(candidate)['file_sha256'],
                 'contract':'requires_historical_proof_adapter', 'changed_substantive_fields':['evidence_step_ids']}]}}
    if mutation:
        with pytest.raises(ValueError, match='SHA-bound historical review input'):
            discover_selection({'claim_ids':['C1']}, inventory)
    else:
        selected, sealed = discover_selection({'claim_ids':['C1']}, inventory)
        assert selected[0]['review_input_binding'] == binding(archived)
        assert selected[0]['legacy_reviewed_candidate_binding'] == binding(candidate)
        assert sealed == []  # Discovery alone never grants sealed approval.

@pytest.mark.parametrize('role,suffix',[('adjudication','ai-adjudication'),('adjudication','adjudication'),
    ('overrides','consensus-overrides'),('overrides','overrides')])
def test_sealed_artifact_filename_variants_require_exact_bytes(tmp_path,role,suffix):
    from backend.pipeline.viewpoint_cvp_campaign_runner import discover_bound_sealed_artifact,binding
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    p=tmp_path/('adjudications' if role=='adjudication' else 'overrides')/('source.'+suffix+'.json')
    write_immutable(p,{'approved':'frozen'})
    assert discover_bound_sealed_artifact(tmp_path,'source',role,binding(p)['file_sha256'])==p
    with pytest.raises(ValueError):discover_bound_sealed_artifact(tmp_path,'source',role,'a'*64)

@pytest.mark.parametrize('mode',['missing','ambiguous','other_generation','no_pin'])
def test_sealed_filename_discovery_never_uses_unbound_or_ambiguous_generation(tmp_path,mode):
    from backend.pipeline.viewpoint_cvp_campaign_runner import discover_bound_sealed_artifact,binding
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    p=tmp_path/'adjudications/source.adjudication.json';q=p.with_name('source.ai-adjudication.json')
    write_immutable(p,{'approved':'frozen'});sha=binding(p)['file_sha256']
    if mode=='missing':p.unlink()
    elif mode=='ambiguous':write_immutable(q,{'approved':'frozen'})
    elif mode=='other_generation':write_immutable(q,{'approved':'other'})
    else:sha=None
    with pytest.raises(ValueError):discover_bound_sealed_artifact(tmp_path,'source','adjudication',sha)


def fixture():
    manifest = {"artifact_sha256": "parent", "units": [{"unit_id": "u", "passage_key": "Matt.1.1"}],
                "groups": [{"group_key": "a", "unit_id": "u", "claim_ids": ["c1"]},
                           {"group_key": "b", "unit_id": "u", "claim_ids": ["c2"]}],
                "claim_packets": [{"claim_id": "c1"}, {"claim_id": "c2"}]}
    schedule = {"upstream_manifest_sha256": "parent", "precedence_annotations": [{"prerequisite": "a", "dependent": "b"}],
                "schedule": [{"sequence": i, "group_key": key, "claim_count": 1,
                              "passage_key": "Matt.1.1", "state": "pending"} for i, key in enumerate(("a", "b"), 1)]}
    return schedule, manifest


def test_exact_intact_schedule():
    schedule, manifest = fixture()
    assert set(validate_schedule(schedule, manifest)) == {"a", "b"}


@pytest.mark.parametrize("mutation", ["duplicate", "count", "passage", "sha", "order", "claim_overlap", "capacity"])
def test_bad_schedule_stops(mutation):
    schedule, manifest = fixture()
    if mutation == "duplicate":
        schedule["schedule"][1]["group_key"] = "a"
    elif mutation == "count":
        schedule["schedule"][0]["claim_count"] = 2
    elif mutation == "passage":
        schedule["schedule"][0]["passage_key"] = "Matt.2.1"
    elif mutation == "sha":
        schedule["upstream_manifest_sha256"] = "other"
    elif mutation == "order":
        schedule["precedence_annotations"] = [{"prerequisite": "b", "dependent": "a"}]
    elif mutation == "claim_overlap":
        manifest["groups"][1]["claim_ids"] = ["c1"]
    else:
        manifest["groups"][0]["claim_ids"] = [f"c{i}" for i in range(21)]
        schedule["schedule"][0]["claim_count"] = 21
    with pytest.raises(ValueError):
        validate_schedule(schedule, manifest)


def good_audit():
    return {"meta": {"model": "gemini-3.8-flash"}, "layers": {
        "3": {"complete": True, "judged": 1, "results": [{"claim_id": "c", "verdict": "supported"}]},
        "4": {"complete": True, "judged": 1, "results": [{"viewpoint_id": "v", "verdict": "same"}]}}}


def test_complete_exact_audit():
    validate_audit(good_audit(), ["c"], ["v"])


@pytest.mark.parametrize("field,value", [("complete", False), ("judged", 0), ("disputed", 1), ("blocked", 1), ("model_errors", 1), ("no_source_text", 1)])
def test_incomplete_or_failed_audit_stops(field, value):
    audit = deepcopy(good_audit())
    audit["layers"]["4"][field] = value
    with pytest.raises(ValueError):
        validate_audit(audit, ["c"], ["v"])


def test_wrong_audit_scope_stops():
    with pytest.raises(ValueError):
        validate_audit(good_audit(), ["other"], ["v"])


def test_wrong_audit_model_stops():
    audit = good_audit()
    audit["meta"]["model"] = "other"
    with pytest.raises(ValueError):
        validate_audit(audit, ["c"], ["v"])


def test_campaign_invokes_source_investigation_for_deferred_items(tmp_path,monkeypatch):
    from backend.pipeline import viewpoint_cvp_campaign_runner as module
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    batch=tmp_path/'production/run/batches/b'
    write_immutable(batch/'batch-disposition-applied.json',{'claims':[{'components':[{'disposition':'deferred'}]}]})
    calls=[]
    def command(args):
        calls.append(args)
        write_immutable(tmp_path/'deferred-source-check/handoff.json',{'status':'investigated_not_resolved'})
    monkeypatch.setattr(module,'command',command)
    assert module.investigate_deferred(tmp_path)['status']=='investigated_not_resolved'
    assert len(calls)==2
    assert 'backend.pipeline.viewpoint_evidence_hold_trace' in calls[0]
    assert 'backend.pipeline.viewpoint_deferred_source_check' in calls[1]


def test_campaign_does_not_investigate_non_deferred_failure(tmp_path,monkeypatch):
    from backend.pipeline import viewpoint_cvp_campaign_runner as module
    monkeypatch.setattr(module,'command',lambda args:pytest.fail('no source investigation without deferred item'))
    assert module.investigate_deferred(tmp_path) is None


@pytest.mark.parametrize('drift',[None,'selection','population','status','artifact'])
def test_existing_bridge_is_exchange_input_not_authority_to_skip_prepare(tmp_path,monkeypatch,drift):
    from types import SimpleNamespace
    from backend.pipeline import viewpoint_cvp_campaign_runner as module
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    from backend.api.canonical_repository.viewpoint_foundation import sha256_json
    write_immutable(tmp_path/'review-selection.json',{'claims':[{'claim_id':'C'}]})
    body={'status':'verified','selection_file_sha256':module.binding(tmp_path/'review-selection.json')['file_sha256'],
          'proofs':[{'claim_id':'C'}]}
    if drift=='selection':body['selection_file_sha256']='wrong'
    if drift=='population':body['proofs']=[{'claim_id':'foreign'}]
    if drift=='status':body['status']='held'
    write_immutable(tmp_path/'historical-bridges.json',body|{'artifact_sha256':'wrong' if drift=='artifact' else sha256_json(body)})
    monkeypatch.setattr(module,'campaign_policy',lambda args:('policy',{}))
    monkeypatch.setattr(module,'build_selection',lambda *args,**kwargs:None)
    monkeypatch.setattr(module,'ensure_campaign_backup',lambda args:{'path':'dump','file_sha256':'backup'})
    calls=[];monkeypatch.setattr(module,'command',lambda argv:calls.append(argv))
    args=SimpleNamespace(grouping_repository=tmp_path,accepted_grouping_sha='sha',entrance_audit=tmp_path/'audit',
        wang_repository=tmp_path,global_lock=tmp_path/'lock',authorized_by='user',completed_source_repair=[])
    if drift:
        with pytest.raises(ValueError):module.qualify({}, {'group_key':'g','claim_ids':['C']},{},tmp_path,args)
        assert not calls
    else:
        module.qualify({}, {'group_key':'g','claim_ids':['C']},{},tmp_path,args)
        assert len(calls)==1 and calls[0][2]=='backend.pipeline.viewpoint_reviewed_cvp_prepare'
