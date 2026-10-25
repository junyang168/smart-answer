from copy import deepcopy
import hashlib
import json

import pytest


@pytest.mark.parametrize('mutation',[None,'text','heading','removed','extra','index','physical','body','order'])
def test_reviewed_physical_rows_allow_only_additive_coordinate_metadata(tmp_path,mutation):
    from backend.pipeline.viewpoint_historical_review_bridge import verify_review_source_sha
    from backend.pipeline.source_projection import project_script
    old=[{'text':'教授原文','index':1,'end_index':2},{'text':'編輯標題','type':'subtitle'}]
    new=deepcopy(old);new[0].update(start_index=1,start_time=0,end_time=4,start_timeline='00:00:00')
    a=tmp_path/'old.json';b=tmp_path/'current.json';a.write_text(json.dumps(old))
    review={'source':{'transcript_paths':{'S':str(a)},'transcript_sha256':{'S':hashlib.sha256(a.read_bytes()).hexdigest()}}}
    if mutation=='text':new[0]['text']='另一句'
    if mutation=='heading':new[1]['text']='新標題'
    if mutation=='removed':del new[0]['end_index']
    if mutation=='extra':new[0]['interpretation']='新增內容'
    if mutation=='index':new[0]['start_index']=3
    if mutation=='order':new.reverse()
    b.write_text(json.dumps({'script':new}))
    source={'source_id':'S','source_path':str(b),'source_file_sha256':hashlib.sha256(b.read_bytes()).hexdigest(),
            'source_body_sha256':project_script(new).body_sha256}
    if mutation=='physical':a.write_text('[]')
    if mutation=='body':source['source_body_sha256']='bad'
    if mutation:
        with pytest.raises(ValueError):verify_review_source_sha(review,source)
    else:
        proof=verify_review_source_sha(review,source)
        assert proof['original_rows_and_fields_unchanged'] is True
        assert proof['reviewed_source']['file_sha256']==review['source']['transcript_sha256']['S']


@pytest.mark.parametrize('mutation',[None,'content','delete','reorder','kind','action','ambiguous','pin','forward','status'])
def test_applied_additive_reverse_pointer_does_not_grant_claim_eligibility(mutation):
    from backend.pipeline.viewpoint_historical_review_bridge import validate_applied_reverse_index
    from backend.api.canonical_repository.postgres_store import record_content_sha
    old={'statement':'原論證','produced_claim_ids':['C1']};new=old|{'produced_claim_ids':['C1','C2']}
    claim={'claim_id':'C2','evidence_step_ids':['E']};sha=record_content_sha(claim)
    action={'claim_id':'C2','evidence_step_id':'E','claim_revision':2,'claim_content_sha256':sha,
            'evidence_content_sha256':'old','mismatch_type':'claim_only','effective_decision':'include',
            'decision_basis':'project_existing_claim_binding_to_reverse_index'}
    actions=[action]
    after={'revision':3,'payload':new,'source_kind':'wkp364_claim_evidence_pair_adjudication',
           'metadata':{'claim_evidence_reciprocity_repair':{'store_guard':{'expected_pair_adjudication_authorization':{'actions':actions}}}}}
    if mutation=='content':new['statement']='變更'
    if mutation=='delete':new['produced_claim_ids']=['C2']
    if mutation=='reorder':new['produced_claim_ids']=['C2','C1']
    if mutation=='kind':after['source_kind']='unknown'
    if mutation=='action':action['decision_basis']='model_include'
    if mutation=='ambiguous':actions.append(deepcopy(action))
    if mutation=='pin':action['claim_content_sha256']='bad'
    if mutation=='forward':claim['evidence_step_ids']=[];sha=record_content_sha(claim);action['claim_content_sha256']=sha
    version=(claim,sha,'draft' if mutation=='status' else 'applied')
    chain=[{'revision':2,'payload':old,'content_sha256':'old'},after]
    if mutation:
        with pytest.raises(ValueError):validate_applied_reverse_index('E',old,new,chain,{'verified':True},{},lambda *a:version)
    else:
        proof=validate_applied_reverse_index('E',old,new,chain,{'verified':True},{},lambda *a:version)
        assert proof['added_claim_ids']==['C2']
        assert proof['added_claim_eligibility_granted'] is False


@pytest.mark.parametrize('mutation', [None,'foreign','ambiguous','retired','type','generation','physical','record'])
def test_adjudicated_registered_sermon_alias_is_exact_and_source_bound(tmp_path, mutation):
    from backend.pipeline.viewpoint_historical_review_bridge import verify_fragment_own_source
    from backend.api.canonical_repository.postgres_store import record_content_sha
    source_file=tmp_path/'source.json';source_file.write_text('original')
    source={'source_id':'SRC1','transcript_id':'legacy1','source_type':'sermon_transcript','source_sha256':'generation'}
    state={'retired':False,'payload':source,'content_sha256':record_content_sha(source)}
    fragment={'source_id':'SRC1','source_sha256':'generation'}
    claim={'occurrences':[{'source_id':'legacy1'}]}
    review={'source':{'transcript_paths':{'SRC1':str(source_file)},
             'transcript_sha256':{'SRC1':hashlib.sha256(source_file.read_bytes()).hexdigest()}}}
    if mutation=='foreign': claim['occurrences'][0]['source_id']='another'
    if mutation=='retired': state['retired']=True
    if mutation=='type': source['source_type']='notes_manuscript';state['content_sha256']=record_content_sha(source)
    if mutation=='generation': fragment['source_sha256']='other'
    if mutation=='physical': source_file.write_text('changed')
    if mutation=='record': state['content_sha256']='wrong'
    class Store:
        def get_record_state(self,c,oid): return state if oid=='SRC1' else {'retired':False,'payload':{'source_id':'SRC2'}}
        def list_records(self,c): return [source]+([source|{'source_id':'SRC2'}] if mutation=='ambiguous' else [])
    if mutation:
        with pytest.raises(ValueError): verify_fragment_own_source(fragment,claim,review,Store())
    else:
        assert verify_fragment_own_source(fragment,claim,review,Store()) is None


@pytest.mark.parametrize('mutation', [None, 'no_replay', 'hold', 'wrong_anchor',
                                     'reciprocal', 'content', 'order', 'history'])
def test_exact_sealed_anchor_exclusion_is_not_a_deletion_waiver(tmp_path, monkeypatch, mutation):
    from backend.pipeline import viewpoint_historical_review_bridge as m
    from backend.pipeline import legacy_candidate_reconciliation as legacy
    from backend.pipeline import legacy_auto_applied_review_reconciliation as replay
    p = tmp_path/'final.json'; p.write_text('{}')
    old = {'produced_claim_ids':['C0','C1','C2'], 'statement':'unchanged'}
    new = old | {'produced_claim_ids':['C1','C2']}
    final = {'evidence_steps':{'E':deepcopy(new)}, 'claims':{'C0':{'evidence_step_ids':[]}}}
    bundle = {'reason':None,'scoped_adjudication_structurally_validated':True,
              'validated_adjudication_claim_ids':['C0'],
              'review_rows':{'C0':{'decision':'changes_suggested'}},
              'adjudication_results':{'C0':{'status':'auto_applied','openai':{
                  'decision':'accept','patch':{'excluded_anchor_indexes':[0]}}}},
              'package_path':str(p),'package_sha256':'sha','review_path':str(p),
              'review_sha256':'sha','adjudication_path':str(p),'adjudication_sha256':'sha'}
    if mutation == 'hold': bundle['adjudication_results']['C0']['human_review_reason']='held'
    if mutation == 'wrong_anchor': bundle['adjudication_results']['C0']['openai']['patch']['excluded_anchor_indexes']=[1]
    if mutation == 'reciprocal': final['claims']['C0']['evidence_step_ids']=['E']
    if mutation == 'content': new['statement']='changed'; final['evidence_steps']['E']=deepcopy(new)
    if mutation == 'order': new['produced_claim_ids']=['C2','C1']; final['evidence_steps']['E']=deepcopy(new)
    monkeypatch.setattr(m,'_normalize_records',lambda payload:(final,{}))
    monkeypatch.setattr(m,'_normalize_claim_layer',lambda payload:{'candidate_claims':[
        {'claim_id':'C0','anchors':[{'evidence_id':'E'},{'evidence_id':'other'}]}]})
    monkeypatch.setattr(m,'_read_binding',lambda b:{})
    monkeypatch.setattr(legacy,'inspect_legacy_bundle',lambda *a,**k:bundle)
    monkeypatch.setattr(replay,'verify_historical_replay',lambda *a,**k:
        None if mutation=='no_replay' else {'replay_contract':'sealed_batched_exact_full_package_v1',
                                          'historical_replay_sha256':'finalsha'})
    def history(*args):
        if mutation=='history': raise m.CvpProductionBlocked(['history drift'])
        return {'verified':True}
    monkeypatch.setattr(m,'verify_legacy_evidence_history',history)
    item={'path':str(p),'payload':{},'application':{'artifact_sha256':'sealed'}}
    if mutation:
        with pytest.raises(m.CvpProductionBlocked): m.verify_sealed_reverse_index('E',old,new,item)
    else:
        proof=m.verify_sealed_reverse_index('E',old,new,item)
        assert proof['removed_claim_ids']==['C0']
        assert proof['authority_kind']=='exact_replayed_applied_anchor_exclusion'
        assert proof['unrelated_human_findings_released'] is False

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


def migration_inputs(tmp_path):
    from dataclasses import asdict
    from pathlib import Path
    from backend.pipeline.legacy_candidate_reconciliation import plan_review_migration
    args = inputs(tmp_path)
    before = deepcopy(args['versions'][-1])
    before['payload']['review_status'] = 'candidate'
    before['content_sha256'] = record_content_sha(before['payload'])
    review = json.loads(Path(args['review_binding']['path']).read_text())
    guard = [{'collection': 'claims', 'object_id': 'C1', 'revision': 2,
              'content_sha256': before['content_sha256']}]
    row = dict(claim_id='C1', source_id='S1', reason='coordinate_only_historical_review_graph_verified',
               historical_decision_reason='pass_ready_for_legacy_migration', revision=2,
               content_sha256=before['content_sha256'], review_decision='pass',
               adjudication_status='not_required', target_review_status='ai_consensus_reviewed',
               reviewer_id='claude-sonnet-5', adjudicated_at='2026-09-08T00:00:00Z',
               spot_check_selected=False, graph_guard_sha256=sha256_json(guard),
               coordinate_chain_sha256='chain', coordinate_proof_code_sha256='code',
               reviewed_claim_content_sha256='reviewed', reviewed_claim_substantive_sha256='meaning',
               current_anchor_count=1, current_fragment_count=1, source_revision=1,
               source_content_sha256='source', package_sha256=args['review_input_binding']['file_sha256'],
               review_sha256=args['review_binding']['file_sha256'],
               review_fingerprint=review['reviewer']['fingerprint_sha256'],
               adjudication_sha256='adjudication', reviewed_candidate_sha256='candidate')
    freeze = sha256_json([row])
    plan = asdict(plan_review_migration([row], {'C1': before}, freeze_sha256=freeze,
                 source_kind='legacy_coordinate_only_review_reconciliation_v1'))
    payload = dict(plan['operations'][0]['payload'], revision=3)
    after = dict(revision=3, payload=payload, content_sha256=record_content_sha(payload),
                 change_set_id=plan['change_set_id'], source_kind=plan['source_kind'],
                 source_sha256=plan['source_sha256'], status='applied', metadata={'freeze_sha256': freeze})
    root = tmp_path / 'migration'
    root.mkdir()
    write_binding(root/'migration-plans.json', dict(plans=[plan], freeze_sha256=freeze,
                  claim_related_graph_guards={'C1': guard}))
    write_binding(root/'preflight.json', dict(rows=[row], snapshot_sha256=freeze))
    return dict(claim_id='C1', before=before, after=after, review_binding=args['review_binding'],
                review_input_binding=args['review_input_binding'], review=review, root=root)


def test_exact_applied_coordinate_review_migration(tmp_path):
    from backend.pipeline.viewpoint_historical_review_bridge import verify_coordinate_review_migration
    proof = verify_coordinate_review_migration(**migration_inputs(tmp_path))
    assert proof['authority_kind'] == 'exact_applied_coordinate_review_migration'
    assert proof['human_holds_released'] is False


def test_bridge_consumes_migration_after_coordinate_proof(tmp_path, monkeypatch):
    import backend.pipeline.viewpoint_historical_review_bridge as bridge
    args = inputs(tmp_path)
    migration = migration_inputs(tmp_path)
    args['versions'][0]['payload']['review_status'] = 'candidate'
    args['versions'][0]['content_sha256'] = record_content_sha(args['versions'][0]['payload'])
    # The coordinate plan itself was built before changing its review status.
    plan_path = args['versions'][1]['metadata']['source_plan_path']
    from pathlib import Path
    plan = json.loads(Path(plan_path).read_text())
    plan['changed_records'][0]['before_sha256'] = args['versions'][0]['content_sha256']
    plan['changed_records'][0]['after_sha256'] = migration['before']['content_sha256']
    plan.pop('artifact_sha256')
    plan['artifact_sha256'] = sha256_json(plan)
    b = write_binding(Path(plan_path), plan)
    migration['before']['metadata']['source_plan_file_sha256'] = b['file_sha256']
    args['versions'] = [args['versions'][0], migration['before'], migration['after']]
    args['current_claim'] = migration['after']['payload']
    original = bridge.verify_coordinate_review_migration
    monkeypatch.setattr(bridge, 'verify_coordinate_review_migration',
        lambda *a, **kw: original(*a, **kw, root=migration['root']))
    proof = bridge.verify_historical_review_bridge(**args)
    assert proof['coordinate_repairs'][0]['after_revision'] == 2
    assert proof['review_migrations'][0]['change_set_id'] == migration['after']['change_set_id']
    assert proof['model_calls_executed'] == proof['master_data_mutations'] == 0


@pytest.mark.parametrize('mutation', ['content', 'reviewer', 'human_hold', 'review_sha',
                                     'freeze', 'missing', 'ambiguous', 'plan', 'graph', 'source_sha'])
def test_coordinate_migration_never_whitelists_status(tmp_path, mutation):
    from backend.pipeline.viewpoint_historical_review_bridge import verify_coordinate_review_migration
    args = migration_inputs(tmp_path)
    root = args['root']
    if mutation == 'content':
        args['after']['payload']['statement'] = '不同内容'
    elif mutation == 'reviewer':
        args['after']['payload']['reviewed_by'] = 'unreviewed-model'
    elif mutation == 'human_hold':
        args['after']['payload']['review_status'] = 'human_review_required'
    elif mutation == 'review_sha':
        args['review_binding']['file_sha256'] = 'other'
    elif mutation == 'freeze':
        args['after']['metadata']['freeze_sha256'] = 'other'
    elif mutation == 'source_sha':
        args['after']['source_sha256'] = 'other'
    elif mutation == 'missing':
        (root/'migration-plans.json').unlink()
    elif mutation == 'ambiguous':
        copy = root/'copy'
        copy.mkdir()
        (copy/'migration-plans.json').write_bytes((root/'migration-plans.json').read_bytes())
    else:
        path = root/'migration-plans.json'
        data = json.loads(path.read_text())
        if mutation == 'plan':
            data['plans'][0]['operations'][0]['payload']['statement'] = 'different'
        else:
            data['claim_related_graph_guards']['C1'] = []
        path.write_text(json.dumps(data))
    with pytest.raises(CvpProductionBlocked):
        verify_coordinate_review_migration(**args)


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


def random_sample_inputs(tmp_path):
    args = human_inputs(tmp_path)
    args.pop('human_closure_binding')
    policy = {'schema_version': 'wang_resolved_random_spot_check_policy_v1',
        'authorized_by': 'human_user', 'user_message': 'AI 已通过、无问题的随机抽查不再询问',
        'rule': 'pass_no_issues_random_sample_only', 'claim_ids': ['C1'],
        'grants_human_content_approval': False}
    policy['artifact_sha256'] = sha256_json(policy)
    args['random_spot_check_policy_binding'] = write_binding(tmp_path/'policy.json', policy)
    return args


def test_explicit_owner_policy_releases_random_pass_without_fabricating_human_approval(tmp_path):
    proof = verify_historical_review_bridge(**random_sample_inputs(tmp_path))
    assert proof['random_spot_check_waived_by_owner_policy'] is True
    assert proof['human_content_approval'] is False
    assert 'human_spot_check_confirmed' not in proof
    assert proof['approval_status'] == 'not_human_approved'
    assert proof['model_calls_executed'] == 0


@pytest.mark.parametrize('mutation', ['actor', 'scope', 'rule', 'human_approval', 'physical_sha',
                                     'issues', 'decision', 'reason', 'candidate_status', 'fingerprint'])
def test_random_sample_policy_does_not_waive_actual_content_or_lineage_problems(tmp_path, mutation):
    args = random_sample_inputs(tmp_path)
    if mutation in {'actor', 'scope', 'rule', 'human_approval'}:
        p = json.loads((tmp_path/'policy.json').read_text())
        key = {'actor': 'authorized_by', 'scope': 'claim_ids', 'rule': 'rule',
               'human_approval': 'grants_human_content_approval'}[mutation]
        p[key] = [] if mutation == 'scope' else 'bad'
        p.pop('artifact_sha256'); p['artifact_sha256'] = sha256_json(p)
        args['random_spot_check_policy_binding'] = write_binding(tmp_path/'policy.json', p)
    elif mutation == 'physical_sha':
        args['random_spot_check_policy_binding']['file_sha256'] = 'bad'
    elif mutation == 'candidate_status':
        args['current_claim']['review_status'] = 'candidate'
        args['versions'][-1]['content_sha256'] = record_content_sha(args['current_claim'])
    else:
        r = json.loads((tmp_path/'review.json').read_text())
        row = r['claim_reviews'][0]
        row[{'issues':'issues', 'decision':'decision', 'reason':'human_review_reason',
             'fingerprint':'reviewer_fingerprint'}[mutation]] = ['problem'] if mutation == 'issues' else 'problem'
        args['review_binding'] = write_binding(tmp_path/'review.json', r)
    with pytest.raises(CvpProductionBlocked):
        verify_historical_review_bridge(**args)
