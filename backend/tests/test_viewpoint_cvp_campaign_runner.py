from copy import deepcopy

import pytest

from backend.pipeline.viewpoint_cvp_campaign_runner import validate_audit, validate_schedule


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
    assert len(calls)==1 and 'backend.pipeline.viewpoint_deferred_source_check' in calls[0]


def test_campaign_does_not_investigate_non_deferred_failure(tmp_path,monkeypatch):
    from backend.pipeline import viewpoint_cvp_campaign_runner as module
    monkeypatch.setattr(module,'command',lambda args:pytest.fail('no source investigation without deferred item'))
    assert module.investigate_deferred(tmp_path) is None
