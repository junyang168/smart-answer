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


def saved_completion(tmp_path):
    from backend.pipeline.source_reconciliation_runner import write, binding
    from backend.api.canonical_repository.viewpoint_foundation import sha256_json
    source=tmp_path/'source'; root=source/'normal-cvp'
    original=tmp_path/'campaign/g001/production/run/batches/B'
    names=('context.json','packet.json','before.json','effective-package.json',
           'plan.json','backup.json','apply-intent.json','apply-receipt.json','readback.json')
    for directory,files in ((root,names),(source,('complete.json','effective-package.json',
            'frozen.json','plan.json','apply-intent.json','readback.json','backup.json')),
            (original,('cvp-change-plan.json','cvp-change-package.json','cvp-readback-receipt.json')),
            (original.parent.parent,('scope-run.json','scope-disposition-ledger.json')),
            (root/'review',('raw-proposal.json','raw-consolidation.json','raw-review.json',
                            'raw-proposal.call-intent.json','raw-proposal.call-failure.json'))):
        for name in files:write(directory/name,{'saved':name})
    item={**op(), 'before_sha256':None,'after_sha256':'1'*64,
          'payload':{'claim_id':'C','viewpoint_id':'V','viewpoint_claim_link_id':'L'}}
    plan={'operations':[item],'change_set_id':'KCS','source_sha256':'s','fingerprint_sha256':'f'}
    write(root/'plan.json',plan,immutable=False)
    write(root/'context.json',{'campaign_root':str(tmp_path/'campaign'),'claim_ids':['C'],
        'group_key':'G','repair_claim_ids':['C'],'original_batch':str(original),
        'claim_package':binding(source/'effective-package.json')},immutable=False)
    write(root/'apply-receipt.json',{'status':'applied','change_set_id':'KCS'},immutable=False)
    write(root/'readback.json',{'status':'verified','post_fingerprint_sha256':'P'},immutable=False)
    pins=[binding(p) for directory in (root,source,original,original.parent.parent,root/'review')
          for p in directory.glob('*.json')]
    proof={'schema_version':d.VERSION,'campaign_root':str(tmp_path/'campaign'),
        'group_key':'G','claim_ids':['C'],'resolved_claim_ids':['C'],'status':'complete',
        'historical_scope_run_preserved':True,'viewpoint_ids':['V'],
        'current_registry_sha256':'P','bindings':pins,'new_model_calls':0,'registry_writes':0}
    path=root/'component-completion.json';write(path,proof)
    return path,proof,plan


def test_completed_consumer_returns_entire_saved_result_without_history_or_model_rebuild(tmp_path,monkeypatch):
    from backend.api.canonical_repository.postgres_store import ChangeOperation,stored_operation_payload,record_content_sha
    from backend.pipeline import viewpoint_validation_context as v
    path,proof,plan=saved_completion(tmp_path)
    def forbidden(*a,**k):pytest.fail('completed consumption entered historical/model reconstruction')
    monkeypatch.setattr(d,'build_proof',forbidden)
    monkeypatch.setattr(d,'package_from_saved_or_new',forbidden)
    monkeypatch.setattr(d.epoch,'verified_applied',forbidden)
    payload=stored_operation_payload(ChangeOperation(**plan['operations'][0]))
    row={'payload':payload,'revision':1,'content_sha256':record_content_sha(payload),'retired':False}
    calls=[]
    monkeypatch.setattr(d,'committed',lambda p,s:calls.append(('commit',p)) or s.applied)
    monkeypatch.setattr(v,'read_current_objects',lambda s,keys:calls.append(('current',keys)) or {('viewpoint_claim_links','L'):row})
    store=SimpleNamespace(applied=True)
    for _ in range(2):
        assert d.verify_proof(proof['campaign_root'],path,['C'],store)==proof
    assert [c[0] for c in calls]==['commit','current','commit','current']
    store.applied=False
    with pytest.raises(ValueError,match='actually committed'):d.verify_proof(proof['campaign_root'],path,['C'],store)
    store.applied=True;row['payload']={**payload,'viewpoint_id':'OTHER'}
    with pytest.raises(ValueError,match='postimage drift'):d.verify_proof(proof['campaign_root'],path,['C'],store)


@pytest.mark.parametrize('problem',['retired','revision','sha','missing'])
def test_completed_consumer_does_not_turn_single_user_into_current_write_approval(tmp_path,monkeypatch,problem):
    from backend.api.canonical_repository.postgres_store import ChangeOperation,stored_operation_payload,record_content_sha
    from backend.pipeline import viewpoint_validation_context as v
    path,proof,plan=saved_completion(tmp_path)
    payload=stored_operation_payload(ChangeOperation(**plan['operations'][0]))
    row={'payload':payload,'revision':1,'content_sha256':record_content_sha(payload),'retired':False}
    if problem=='retired':row['retired']=True
    if problem=='revision':row['revision']=2
    if problem=='sha':row['content_sha256']='foreign'
    def current(store,keys):
        if problem=='missing':raise ValueError('current proof readset incomplete')
        return {('viewpoint_claim_links','L'):row}
    monkeypatch.setattr(d,'committed',lambda p,s:True)
    monkeypatch.setattr(v,'read_current_objects',current)
    with pytest.raises(ValueError):d.verify_proof(proof['campaign_root'],path,['C'],object())


@pytest.mark.parametrize('problem',['seal','member','campaign','bindings','missing_raw','raw_changed',
                                   'rejected_receipt','readback','views','resolved'])
def test_completed_consumer_keeps_identity_original_raw_failures_and_readback_gates(tmp_path,problem):
    from backend.pipeline.source_reconciliation_runner import write
    path,proof,plan=saved_completion(tmp_path)
    if problem=='seal':path.write_text('{}')
    elif problem=='member':proof['claim_ids']=['OTHER']
    elif problem=='campaign':proof['campaign_root']='/foreign'
    elif problem=='bindings':proof['bindings']=proof['bindings'][1:]
    elif problem=='missing_raw':(path.parent/'review/raw-proposal.call-failure.json').unlink()
    elif problem=='raw_changed':(path.parent/'review/raw-review.json').write_text('{}')
    elif problem=='rejected_receipt':write(path.parent/'apply-receipt.json',{'status':'rejected'},immutable=False)
    elif problem=='readback':proof['current_registry_sha256']='foreign'
    elif problem=='views':proof['viewpoint_ids']=[]
    elif problem=='resolved':proof['resolved_claim_ids']=[]
    if problem not in {'seal','missing_raw','raw_changed','rejected_receipt'}:write(path,proof,immutable=False)
    with pytest.raises(ValueError):d.consume_completed_proof(str(tmp_path/'campaign'),path,['C'])


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


@pytest.mark.parametrize("sealed",[False,True])
def test_legacy_raw_recovery_marker_requires_exact_complete_reconstruction(tmp_path,sealed):
    from backend.pipeline.source_reconciliation_runner import write
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    path=tmp_path/"reservation.json";expected={"failure_log":{"path":"L","file_sha256":"s"},"old_intent_preserved":True}
    if sealed:write(path,expected)
    else:write_immutable(path,expected)
    original=path.read_bytes()
    assert d.recovery_reservation(path,expected)==expected
    assert path.read_bytes()==original
    for changed in [{}, {**expected,"old_intent_preserved":False}, {**expected,"extra":"drift"}]:
        with pytest.raises(ValueError):d.recovery_reservation(path,changed)


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
