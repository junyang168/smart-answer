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


def test_startup_consumes_declared_completed_format_not_source_ID_or_old_writer(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    from backend.pipeline import viewpoint_cvp_campaign_runner as module
    from backend.pipeline import viewpoint_applied_source_epochs as epochs
    from backend.pipeline import viewpoint_grouping_repository as grouping
    from backend.api.canonical_repository import reviewed_candidate_contract as contract
    path=tmp_path/'unrelated-name/effective-package.json';path.parent.mkdir()
    package={'package_id':'original','source_repair_application':{
        'schema_version':'original_saved_format','before_binding':{'path':'unneeded-old-library','file_sha256':'a'*64},
        'bindings':[]}}
    path.write_text(json.dumps(package))
    assert epochs.has_applied_source_frame(path,package)
    # Its declared format still routes to a hard guard when a receipt is
    # missing: do not invoke a historical whole-library fallback.
    assert not (path.parent/'verified-result.json').exists()
    calls=[]
    def consume(bound,store):calls.append(bound);return package
    monkeypatch.setattr(epochs,'consume_applied_source_package',consume)
    monkeypatch.setattr(epochs,'compose',lambda paths:'original-composition')
    monkeypatch.setattr(contract,'validate_store_package_authorization',lambda *a:pytest.fail('old source writer invoked'))
    monkeypatch.setattr(grouping.SharedGroupingRepository,'load',lambda *a,**k:None)
    args=SimpleNamespace(completed_source_repair=[path],grouping_repository=tmp_path,
        accepted_grouping_sha='original')
    assert module.initialize_validation(args)['model_calls']==0
    assert calls==[module.binding(path)]


@pytest.mark.parametrize('drift',[None,'missing_frame','reader_failure','unrecognized'])
def test_completed_legacy_format_routes_to_exact_reader_never_new_writer(tmp_path,monkeypatch,drift):
    import json
    from types import SimpleNamespace
    from backend.pipeline import viewpoint_cvp_campaign_runner as module
    from backend.pipeline import viewpoint_applied_source_epochs as epochs
    from backend.pipeline import viewpoint_grouping_repository as grouping
    from backend.api.canonical_repository import reviewed_candidate_contract as contract
    path=tmp_path/'arbitrary-legacy/effective-package.json';path.parent.mkdir()
    package={'package_id':'original','source_repair_application':{'schema_version':'original_legacy_format',
        'before_binding':{'path':'original-before','file_sha256':'a'*64}}}
    if drift=='unrecognized':package['source_repair_application'].pop('before_binding')
    path.write_text(json.dumps(package));calls=[]
    def bound_package(bound):
        calls.append(bound)
        if drift=='reader_failure':raise ValueError('exact original review missing/changed')
        return package
    adapter=SimpleNamespace(COMPLETED_FRAME=None if drift=='missing_frame' else {'original':'pin'},bound_package=bound_package)
    monkeypatch.setattr(epochs,'implementation',lambda b:adapter)
    monkeypatch.setattr(epochs,'compose',lambda paths:'exact-composition')
    monkeypatch.setattr(contract,'validate_store_package_authorization',lambda *a:pytest.fail('old writer fallback'))
    monkeypatch.setattr(grouping.SharedGroupingRepository,'load',lambda *a,**k:None)
    if drift=='unrecognized':
        with pytest.raises(ValueError):epochs.consume_completed_source_frame(module.binding(path),None)
        assert calls==[]
    else:
        args=SimpleNamespace(completed_source_repair=[path],grouping_repository=tmp_path,accepted_grouping_sha='exact')
        if drift:
            with pytest.raises(ValueError):module.initialize_validation(args)
        else:assert module.initialize_validation(args)['Registry_writes']==0
        assert calls==([] if drift=='missing_frame' else [module.binding(path)])


@pytest.mark.parametrize('drift',[None,'pin','missing','duplicate_group','conflict','disclosure','audit'])
def test_completed_seed_consumes_unique_bytes_but_all_original_group_gates(tmp_path,monkeypatch,drift):
    import json
    from backend.pipeline import viewpoint_cvp_campaign_runner as module
    from backend.pipeline import viewpoint_g035_temporary_audit as disclosure
    original=good_audit();path=str(tmp_path/'audit.json')
    (tmp_path/'audit.json').write_text(json.dumps(original))
    bound=module.binding(path)
    rows=[{'group_key':g,'bindings':[deepcopy(bound)],'audit_path':path,
           'viewpoint_ids':['v'],'revisit_required':False,'independent_audit_complete':True}
          for g in ('a','b')]
    seed={'groups':rows};groups={g:{'claim_ids':['c']} for g in ('a','b')}
    if drift=='pin':rows[0]['bindings'][0]['file_sha256']='0'*64;rows[1]['bindings'][0]['file_sha256']='0'*64
    elif drift=='missing':(tmp_path/'audit.json').unlink()
    elif drift=='duplicate_group':rows.append(deepcopy(rows[0]))
    elif drift=='conflict':rows[1]['bindings'][0]['file_sha256']='0'*64
    reads=[];hashes=[];checks=[];actual_read=module.read;actual_binding=module.binding
    monkeypatch.setattr(module,'read',lambda p:reads.append(p) or actual_read(p))
    monkeypatch.setattr(module,'binding',lambda p:hashes.append(p) or actual_binding(p))
    def check_flags(row,audit):
        checks.append(('disclosure',row['group_key']))
        if drift=='disclosure':raise ValueError('temporary flags differ')
    def check_audit(audit,claims,views,**kwargs):
        assert audit==original and claims==['c'] and views==['v']
        checks.append(('audit',claims,views))
        if drift=='audit':raise ValueError('incomplete audit')
        audit['layers']['3']['results'][0]['verdict']='mutated isolated copy'
    monkeypatch.setattr(disclosure,'validate_completion_disclosure',check_flags)
    monkeypatch.setattr(module,'validate_audit',check_audit)
    if drift:
        with pytest.raises((ValueError,FileNotFoundError)):module.validate_completed_seed(seed,groups,['a','b'])
        if drift in {'conflict','duplicate_group'}:assert hashes==reads==checks==[]
    else:
        assert module.validate_completed_seed(seed,groups,['a','b'])=={
            'groups':2,'binding_references':2,'unique_physical_bindings':1,'unique_audit_documents':1}
        assert hashes==reads==[path] and len(checks)==4
        assert seed['groups']==rows and original==good_audit()
        # A later startup must authenticate again, not reuse prior approval.
        (tmp_path/'audit.json').write_text(json.dumps({'changed':True}))
        with pytest.raises(ValueError,match='SHA drift'):
            module.validate_completed_seed(seed,groups,['a','b'])


@pytest.mark.parametrize('stop_before_apply',[False,True])
def test_startup_gate_reuses_actual_owned_runtime_and_precedes_apply(tmp_path,monkeypatch,stop_before_apply):
    import json
    import os
    from types import SimpleNamespace
    from backend.pipeline import viewpoint_cvp_campaign_runner as module
    from backend.pipeline import viewpoint_validation_context as contexts
    schedule,manifest=fixture()
    schedule.update(completed_groups=[],upstream_manifest_path=str(tmp_path/'manifest.json'))
    for name,value in [('schedule',schedule),('manifest',manifest),('inventory',{'claims':[]}),
                       ('seed',{'groups':[]}),('audit',{})]:
        (tmp_path/(name+'.json')).write_text(json.dumps(value))
    output=tmp_path/'output';output.mkdir()
    args=SimpleNamespace(schedule=tmp_path/'schedule.json',
        schedule_sha256=module.binding(tmp_path/'schedule.json')['file_sha256'],
        accepted_grouping_sha='parent',lineage_inventory=tmp_path/'inventory.json',
        completed_proofs=tmp_path/'seed.json',entrance_audit=tmp_path/'audit.json',
        grouping_repository=tmp_path/'grouping',wang_repository=tmp_path/'wang',
        global_lock=tmp_path/'lock',authorized_by='offline-test',output_root=output,
        validation_runtime=True,dry_run=False)
    observed=[]
    monkeypatch.setenv('CODEX_EXECUTABLE','pytest-only-no-provider')
    def observe(stage):observed.append((stage,os.getpid(),contexts._pool.get()))
    monkeypatch.setattr(module,'campaign_policy',lambda _:({},{}))
    monkeypatch.setattr(module,'lineage_preflight',lambda *a:{})
    monkeypatch.setattr(module,'pause_at_safe_boundary',lambda *a,**k:False)
    monkeypatch.setattr(module.shutil,'disk_usage',lambda _:SimpleNamespace(free=10*1024**3))
    dump=tmp_path/'dump';dump.write_bytes(b'offline dump')
    def backup(_):
        (output/'backup.json').write_text(json.dumps(module.binding(dump)))
    monkeypatch.setattr(module,'ensure_campaign_backup',backup)
    monkeypatch.setattr(module,'initialize_validation',lambda _:observe('initialize') or {})
    def qualify(row,group,inventory,root,args):
        observe('qualify')
        inputs=root/'production/inputs';inputs.mkdir(parents=True)
        (inputs/'scope.json').write_text('{}')
    monkeypatch.setattr(module,'qualify',qualify)
    monkeypatch.setattr(module,'runner_args',lambda *a:['offline-only'])
    def command(argv):
        if '--dry-run' in argv:observe('dry-run')
        elif '--apply' in argv:
            observe('apply-boundary')
            raise ValueError('offline test stops BEFORE any real apply or provider')
        else:pytest.fail('no provider/audit subprocess')
    monkeypatch.setattr(module,'command',command)
    class Gate:
        released=False
        def ready(self,state,pins,backup):
            assert state['status']=='preflight_passed_no_model_calls' and pins and backup
            observe('ready')
        def wait_release(self,*a):
            observe('release')
            if stop_before_apply:raise ValueError('owner stop')
            self.released=True
    args._startup_gate=Gate()
    with contexts.validation_runtime() as pool:
        with pytest.raises(ValueError,match='owner stop' if stop_before_apply else 'offline test stops'):
            module.execute(args)
        assert all(pid==os.getpid() and owned is pool for _,pid,owned in observed)
    stages=[stage for stage,_,_ in observed]
    assert stages==['initialize','qualify','dry-run','ready','release']+([] if stop_before_apply else ['apply-boundary'])
    assert len(list((output/'validation-startups').iterdir()))==1


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
