from copy import deepcopy
from types import SimpleNamespace
import pytest

from backend.pipeline import deferred_claim_cvp_continuation as d


def op(collection="viewpoint_claim_links",object_id="L",operation="create",claim_id="C"):
    return {"collection":collection,"object_id":object_id,"operation":operation,"before_revision":None,
        "after_revision":1,"payload":{"claim_id":claim_id}}


@pytest.mark.parametrize("change",["update","retire","foreign_claim","existing_cvp","duplicate","source","evidence","claim"])
def test_delta_never_replays_original_or_changes_claim_source_identity(change):
    item = op(); plan = {"operations":[item]}
    if change == "update": item.update(operation="update",before_revision=1,after_revision=2)
    if change == "retire": item["operation"] = "retire"
    if change == "foreign_claim": item["payload"]["claim_id"] = "OTHER"
    if change == "existing_cvp": item.update(collection="canonical_viewpoints",before_revision=1)
    if change == "duplicate": plan["operations"].append(deepcopy(item))
    if change in {"source","evidence","claim"}: item["collection"] = {"source":"source_fragments","evidence":"evidence_steps","claim":"claims"}[change]
    with pytest.raises(ValueError): d.validate_delta(plan,["C"])


def test_normal_delta_retains_existing_components_only_creates_fresh_coverage():
    d.validate_delta({"operations":[op(),op("canonical_viewpoints","V"),op("viewpoint_revisions","VR"),op("viewpoint_identity_decisions","D")]},["C"])


@pytest.mark.parametrize("problem",["rejected","escalated","deferred","foreign","core","route"])
def test_repaired_claim_not_whole_group_complete_until_normal_cvp_review_resolved(problem):
    p = SimpleNamespace(claim_decisions=[SimpleNamespace(claim_id="C",components=[SimpleNamespace(disposition="new_viewpoint")])],
        viewpoint_revisions=[],argument_route_candidates=[])
    report = {"approval_outcome":"pass","escalations":[],"_effective_proposal":p}
    if problem == "rejected": report["approval_outcome"] = "correct"
    if problem == "escalated": report["escalations"] = ["human hold"]
    if problem == "deferred": p.claim_decisions[0].components[0].disposition = "deferred"
    if problem == "foreign": p.claim_decisions[0].claim_id = "OTHER"
    if problem == "core": p.viewpoint_revisions = ["rewrite"]
    if problem == "route": p.argument_route_candidates = ["route"]
    with pytest.raises(ValueError): d.resolved_report(report,["C"])


def test_real_strict_cvp_response_has_no_route_field_and_passes_resolved_gate():
    from backend.api.canonical_repository.viewpoint_batch_resolution import CanonicalViewpointProposalResponse
    p=CanonicalViewpointProposalResponse.model_validate({
        "schema_version":"wang_canonical_viewpoint_proposal_v1","batch_id":"B",
        "claim_decisions":[{"claim_id":"C","components":[{
            "spans":[{"start_char":0,"end_char":2,"exact_text":"原文"}],
            "disposition":"member_existing","target_viewpoint_revision_id":"VR",
            "evidence_step_ids":["E"],"source_fragment_ids":["F"],"reason":"same source"}]}],
        "new_viewpoint_candidates":[],"viewpoint_revisions":[],
        "viewpoint_relations":[],"structures":[]})
    assert not hasattr(p,"argument_route_candidates")
    d.resolved_report({"approval_outcome":"pass","escalations":[],"_effective_proposal":p},["C"])
    with pytest.raises(ValueError):
        CanonicalViewpointProposalResponse.model_validate({**p.model_dump(),"argument_route_candidates":[{}]})


def test_reverse_boundary_requires_every_exact_normalized_postimage():
    # stored_operation_payload uses actual after_revision, not a draft version.
    item = {**op("claims","C","update"),"before_revision":1,"after_revision":2,"payload":{"claim_id":"C","statement":"corrected"},
        "before_sha256":"0"*64,"after_sha256":"1"*64}
    from backend.api.canonical_repository.postgres_store import ChangeOperation,stored_operation_payload
    before = {"claims/C":{"claim_id":"C","revision":1,"statement":"old"}}
    live = {"claims/C":stored_operation_payload(ChangeOperation(**item))}
    assert d.rewind({"operations":[item]},live,before) == before
    live["claims/C"]["statement"] = "unreviewed"
    with pytest.raises(ValueError,match="postimage"): d.rewind({"operations":[item]},live,before)


def test_sql_cut_includes_empty_collections_and_detects_first_insertion():
    from backend.pipeline import source_reconciliation as c
    before={"claims/C":{"claim_id":"C","revision":1}}
    cut=d.registry_collections(before)
    assert set(c.KNOWLEDGE_COLLECTIONS)<=set(cut)
    assert c.fingerprint(before,collections=cut)==c.fingerprint(before)
    later={**before,"impact_events/I":{"impact_event_id":"I","revision":1}}
    assert c.fingerprint(later,collections=cut)!=c.fingerprint(before)


@pytest.mark.parametrize("problem",[None,"current_changed","created_present","wrong_plan","wrong_package","uncertain_error","failed_exit","foreign_root","update"])
def test_only_proven_empty_cut_failure_can_recover_before_first_write(tmp_path,problem):
    from backend.pipeline.source_reconciliation_runner import write
    from backend.pipeline import source_reconciliation as c
    root=tmp_path/"source"; log=tmp_path/"failure/execution.log";log.parent.mkdir()
    before={"claims/C":{"claim_id":"C","revision":1}}
    plan={"operations":[op()],"change_set_id":"KCS"};package={"new":"V"}
    intent={"plan_sha256":c.sha256_json(plan),"package_sha256":c.sha256_json(package)}
    current=deepcopy(before)
    text=("self._assert_registry_fingerprint(\nbackend.api.canonical_repository.postgres_store.ChangeSetConflict: "
          f"CanonicalViewpoint Registry cut is stale: expected {c.fingerprint(before)}, found {c.fingerprint(before,collections=['claims'])}\n")
    argv=["--continue-saved","--source-root",str(root)];status=1
    if problem=="current_changed":current["claims/C"]["revision"]=2
    if problem=="created_present":current["viewpoint_claim_links/L"]={}
    if problem=="wrong_plan":intent["plan_sha256"]="wrong"
    if problem=="wrong_package":intent["package_sha256"]="wrong"
    if problem=="uncertain_error":text="timeout outcome unknown\n"
    if problem=="failed_exit":status=0
    if problem=="foreign_root":argv[-1]="other"
    if problem=="update":plan["operations"][0]["operation"]="update";intent["plan_sha256"]=c.sha256_json(plan)
    log.write_text(text);write(log.parent/"process.json",{"argv":argv});write(log.parent/"exit-result.json",{"exit_code":status})
    if problem is None:
        result=d.validate_prewrite_conflict(log,before,current,plan,intent,package,root)
        assert result["omitted_empty_collections"] and result["automatic_retry"] is False
    else:
        with pytest.raises(ValueError):d.validate_prewrite_conflict(log,before,current,plan,intent,package,root)
