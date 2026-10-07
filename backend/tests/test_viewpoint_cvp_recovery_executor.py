import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.pipeline import viewpoint_cvp_recovery_executor as m
from backend.pipeline import viewpoint_cvp_recovery_supervisor as supervisor


@pytest.fixture(autouse=True)
def prohibit_real_model_diagnostics(monkeypatch):
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    monkeypatch.setattr(CodexSubscriptionClient, 'generate_json',
        lambda *a, **k: pytest.fail('unit tests must never call a real model'))
    monkeypatch.setattr(m, 'verify_runtime', lambda client, cache: {
        'identity': {'model': client.model, 'cli': {'path': client.executable}},
        'new_model_calls': 0, 'test_double': True})


def test_physical_proof_binding_accepts_markdown_but_rejects_tampering(tmp_path):
    source = tmp_path / 'source.md'
    source.write_text('# Physical original\nNot a JSON receipt.\n')
    proof = m.binding(source)
    assert m.verify_binding(proof, parse_json=False) is None
    source.write_text('# Changed original\n')
    with pytest.raises(ValueError, match='physical artifact SHA drift'):
        m.verify_binding(proof, parse_json=False)


def test_structured_receipt_still_requires_json(tmp_path):
    source = tmp_path / 'receipt.json'
    source.write_text('not JSON')
    with pytest.raises(json.JSONDecodeError):
        m.verify_binding(m.binding(source))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "wkp-357-worktree-per-session"
    root.mkdir()
    monkeypatch.setattr(m, "PROJECT_ROOT", root)
    monkeypatch.setattr(m, "PYTHON", sys.executable)
    monkeypatch.setattr(m, "TESTS", ("backend/tests/test_gate.py",))
    (root / "scripts").mkdir()
    (root / "backend/tests").mkdir(parents=True)
    (root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    (root / "scripts/audit-library.py").write_text('def _whole_sources_for_claim():\n    return "old"\n')
    (root / "backend/tests/test_gate.py").write_text("def test_gate():\n    assert True\n")
    for args in (("init", "-q"), ("config", "user.email", "test@example.test"),
                 ("config", "user.name", "Recovery Test"), ("config", "commit.gpgsign", "false"),
                 ("add", "."), ("commit", "-qm", "baseline")):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


def proposal(repo):
    return {"decision": "code_bug", "reason": "source reader defect",
            "changes": [
                {"path": "scripts/audit-library.py", "before_sha256": m.binding(repo / "scripts/audit-library.py")["file_sha256"],
                 "after_text": 'def _whole_sources_for_claim():\n    return "new"\n'},
                {"path": "backend/tests/test_cvp_recovery_case_source.py", "before_sha256": None,
                 "after_text": "def test_case():\n    assert True\n"}]}


def attempt_dir(tmp_path, p):
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    (attempt / "proposal.json").write_text(json.dumps(p))
    return attempt


def test_actual_git_handoff_commits_only_validated_patch(repo, tmp_path):
    p, baseline = proposal(repo), m.snapshot()
    attempt = attempt_dir(tmp_path, p)
    receipt = m.apply_and_commit(p, baseline, attempt)
    assert receipt["commit"] != baseline["head"]
    assert receipt["production_restarted"] is False
    assert m.git("status", "--porcelain") == ""
    assert set(m.git("show", "--pretty=", "--name-only", "HEAD").splitlines()) == set(receipt["files"])
    assert json.loads((attempt / "regression.json").read_text())["exit_code"] == 0


@pytest.mark.parametrize("name", ["../outside.py", "/outside.py", "backend/tests/test_gate.py",
                                  "backend/pipeline/viewpoint_cvp_recovery_executor.py", "scripts//audit-library.py"])
def test_scope_escape_and_existing_gate_edits_rejected(repo, name):
    p = proposal(repo)
    p["changes"][0]["path"] = name
    with pytest.raises(ValueError):
        m.validate_changes(p, m.snapshot())


def test_symlink_rejected(repo, tmp_path):
    target = repo / "backend/tests/test_cvp_recovery_case_source.py"
    target.symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink"):
        m.safe_path(str(target.relative_to(repo)))


@pytest.mark.parametrize("mutation", ["head", "test", "source", "commands", "duplicate"])
def test_drift_or_untrusted_instructions_never_applied(repo, tmp_path, mutation):
    p, baseline = proposal(repo), m.snapshot()
    if mutation == "head":
        subprocess.run(["git", "commit", "--allow-empty", "-qm", "other session"], cwd=repo, check=True)
    elif mutation == "test":
        (repo / "backend/tests/test_gate.py").write_text("changed\n")
    elif mutation == "source":
        p["changes"][0]["before_sha256"] = "wrong"
    elif mutation == "commands":
        p["commands"] = ["echo UNTRUSTED"]
    else:
        p["changes"].append(copy.deepcopy(p["changes"][0]))
    attempt = attempt_dir(tmp_path, p)
    with pytest.raises(ValueError):
        m.apply_and_commit(p, baseline, attempt)
    assert not (attempt / "patch-apply.json").exists()
    assert (repo / "scripts/audit-library.py").read_text().endswith('return "old"\n')


def test_failure_keeps_patch_and_raw_test_logs_without_commit(repo, tmp_path):
    p, baseline = proposal(repo), m.snapshot()
    p["changes"][1]["after_text"] = "def test_case():\n    assert False\n"
    attempt = attempt_dir(tmp_path, p)
    with pytest.raises(ValueError, match="regression failed"):
        m.apply_and_commit(p, baseline, attempt)
    assert m.git("rev-parse", "HEAD") == baseline["head"]
    assert m.git("diff", "--cached", "--name-only") == ""
    assert (attempt / "accepted-patch.json").exists()
    assert "FAILED" in (attempt / "regression.stdout").read_text()
    assert not (attempt / "start-campaign.json").exists()


def test_protected_content_policy_cannot_be_replaced(repo):
    source = repo / "scripts/audit-library.py"
    source.write_text('def _viewpoint_prompt():\n    return "strict"\n')
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "guard"], cwd=repo, check=True)
    p = proposal(repo)
    p["changes"][0]["after_text"] = 'def _viewpoint_prompt():\n    return "weak"\n'
    with pytest.raises(ValueError, match="protected"):
        m.validate_changes(p, m.snapshot())


def test_only_source_call_changes_not_verdict_population_or_imports():
    before = 'import os\ndef audit_viewpoints():\n    sources = _paragraphs_for_fragments(ids, fragments, sources, limit=2)\n    return judge(sources)\n'
    after = before.replace('_paragraphs_for_fragments(ids, fragments, sources, limit=2)',
                           '_whole_sources_for_claim(ids, fragments, sources)')
    m.validate_source_reader_patch(before, after)
    for bad in (after.replace('return judge(sources)', 'return "same"'),
                after.replace('import os', 'import subprocess'),
                after + 'def bypass():\n    return True\n'):
        with pytest.raises(ValueError, match="protected"):
            m.validate_source_reader_patch(before, bad)


def test_owner_disposition_and_same_attempt_never_edit_or_restart(repo, tmp_path, monkeypatch):
    p = {"decision": "needs_owner", "reason": "true content dispute", "changes": []}
    attempt = attempt_dir(tmp_path, p)
    monkeypatch.setattr(m, "apply_and_commit", lambda *a: pytest.fail("no code edits"))
    result = m.execute(p, {}, attempt, {"fingerprint": "once"}, tmp_path, "codex")
    assert result["status"] == "needs_owner"
    with pytest.raises(ValueError, match="already attempted"):
        m.execute(p, {}, attempt, {"fingerprint": "once"}, tmp_path, "codex")


def test_content_dispute_blocks_completion_even_after_code_tests_pass(tmp_path):
    root = tmp_path / "campaign"
    batch = root / "g008/production/run/batches/one"
    batch.mkdir(parents=True)
    (root / "progress.json").write_text(json.dumps({"sequence": 8, "group_key": "group"}))
    (batch.parent.parent / "scope-run.json").write_text('{"disposition_status":"complete"}')
    from backend.api.canonical_repository.viewpoint_foundation import sha256_json
    package={"viewpoint_claim_links":[{"viewpoint_claim_link_id":"link","viewpoint_id":"cv","effective_state":"active",
        "validated_against_viewpoint_revision_id":"cvr"}]}
    (batch / "cvp-change-package.json").write_text(json.dumps(package))
    (batch / "cvp-change-plan.json").write_text(json.dumps({"source_sha256":sha256_json(package),
        "change_set_id":"KCS-test","fingerprint_sha256":"plan","operations": [
        {"collection": "viewpoint_claim_links","operation":"create","payload": package['viewpoint_claim_links'][0]}]}))
    receipt={"readback_status":"verified","cvp_changeset_id":"KCS-test","cvp_changeset_sha256":"plan",
        "committed_viewpoint_revisions":[{"viewpoint_id":"cv","viewpoint_revision_id":"cvr"}]}
    (batch / "cvp-readback-receipt.json").write_text(json.dumps({**receipt,"artifact_sha256":sha256_json(receipt)}))
    audit = {"meta": {"model": "gemini-3.8-flash"}, "layers": {
        "3": {"complete": True, "judged": 1, "results": [{"claim_id": "cl", "verdict": "supported"}]},
        "4": {"complete": True, "judged": 1, "disputed": 1,
              "results": [{"viewpoint_id": "cv", "verdict": "disputed", "issue": "scope_mismatch"}]}}}
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(audit))
    output = tmp_path / "verified"
    with pytest.raises(ValueError, match="layer 4"):
        m.verify_group_audit(root, {}, {}, {}, {"group": {"claim_ids": ["cl"]}}, path, output)
    assert not (output / "completion.json").exists()


def test_readonly_proposal_hands_off_to_parent_once(tmp_path, monkeypatch):
    project = tmp_path / supervisor.EXPECTED_WORKTREE
    project.mkdir()
    root = tmp_path / "campaign"
    root.mkdir()
    (root / "progress.json").write_text('{"status":"blocked","error":"bug"}')
    monkeypatch.setattr(supervisor, "PROJECT_ROOT", project)
    monkeypatch.setattr(supervisor.subprocess, "check_output", lambda *a, **k: "")
    monkeypatch.setattr(supervisor.executor, "snapshot", lambda *a: {"head": "frozen"})
    monkeypatch.setattr(supervisor.CodexSubscriptionClient, "_verify_chatgpt_login", lambda *a: None)
    calls = []
    def execute(*args):
        calls.append(args)
        return {"status": "resumed", "production_complete": False}
    monkeypatch.setattr(supervisor.executor, "execute", execute)
    def run(argv, **kw):
        assert argv[argv.index("--sandbox") + 1] == "read-only"
        assert "--add-dir" not in argv
        Path(argv[argv.index("--output-last-message") + 1]).write_text(
            '{"decision":"code_bug","reason":"bug","changes":[]}')
        return subprocess.CompletedProcess(argv, 0)
    recovery = tmp_path / "recovery"
    event = supervisor.failure_event(root)
    assert supervisor.dispatch(event, recovery, "codex", run=run) == "resumed"
    assert supervisor.dispatch(event, recovery, "codex", run=run) == "same_failure_already_attempted"
    assert len(calls) == 1


def test_continuation_keeps_original_members_capacity_order_and_pins(tmp_path, monkeypatch):
    root, output = tmp_path / "old", tmp_path / "new"
    root.mkdir()
    manifest = {"artifact_sha256": "accepted", "units": [{"unit_id": "u", "passage_key": "Mt2"}],
                "groups": [{"group_key": k, "unit_id": "u", "claim_ids": [c]} for k, c in [("a", "cl1"), ("b", "cl2")]],
                "claim_packets": [{"claim_id": "cl1"}, {"claim_id": "cl2"}]}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    schedule = {"upstream_manifest_sha256": "accepted", "upstream_manifest_path": str(manifest_path),
                "schedule": [{"sequence": i, "group_key": k, "claim_count": 1, "passage_key": "Mt2", "state": "pending"}
                             for i, k in enumerate(["a", "b"], 1)],
                "completed_groups": [], "precedence_annotations": [{"prerequisite": "a", "dependent": "b"}]}
    seeds = tmp_path / "seeds.json"
    seeds.write_text('{"groups":[]}')
    cfg = {"completed_proofs": m.binding(seeds), "authorized_by": "owner",
           "lineage_inventory": {"path": "inventory"}, "entrance_audit": {"path": "entrance"},
           "grouping_repository": "grouping", "wang_repository": "wang", "global_lock": "lock",
           "completed_source_repairs": [{"path": "source-repair"}], "random_spot_check_policy": {"path": "spot-policy"}}
    cfg['validation_checkpoint_sequence']=1
    monkeypatch.setattr(m, "load_campaign", lambda *a: (cfg, schedule, manifest, m.validate_schedule(schedule, manifest)))
    audit = {"meta": {"model": "gemini-3.8-flash"}, "layers": {
        "3": {"complete": True, "judged": 1, "results": [{"claim_id": "cl1", "verdict": "supported"}]},
        "4": {"complete": True, "judged": 1, "results": [{"viewpoint_id": "cv1", "verdict": "same"}]}}}
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit))
    proof = {"group_key": "a", "viewpoint_ids": ["cv1"], "bindings": [m.binding(audit_path)], "audit_path": str(audit_path)}
    argv = m.continuation_inputs(root, output, proof)
    derived = m.read(output / "schedule.json")
    assert [r["group_key"] for r in derived["schedule"]] == ["a", "b"]
    assert [r["state"] for r in derived["schedule"]] == ["already_complete", "pending"]
    assert derived["precedence_annotations"] == schedule["precedence_annotations"]
    for old, new in zip(schedule["schedule"], derived["schedule"]):
        assert {k: v for k, v in old.items() if k != "state"} == {k: v for k, v in new.items() if k != "state"}
    assert "--completed-source-repair" in argv and "--random-spot-check-policy" in argv
    assert argv[argv.index('--stop-after-sequence')+1]=='1'
    # An edited physical audit cannot be used to seed completion.
    audit_path.write_text("{}")
    with pytest.raises(ValueError, match="SHA drift"):
        m.continuation_inputs(root, tmp_path / "tampered", proof)


def test_long_continuation_root_is_bounded_and_full_identity_bound(tmp_path):
    root = tmp_path / ('a' * 244)
    attempt = tmp_path / 'attempt'
    result = m.continuation_root(root, attempt)
    assert len(result.name.encode('utf-8')) <= 240
    assert result == m.continuation_root(root, attempt)
    assert result != m.continuation_root(tmp_path / 'other' / root.name, attempt)
    assert result != m.continuation_root(root, tmp_path / 'other-attempt')
    result.mkdir()


@pytest.fixture
def mocked_performance_acceptance(monkeypatch):
    # These legacy orchestration tests isolate backup/runtime/process gates.
    # The REAL measured-acceptance schema/negative gates are tested separately;
    # no actual production acceptance artifact is created by this fixture.
    from backend.pipeline import viewpoint_scalability_acceptance as performance
    monkeypatch.setattr(performance,'require_scalability_acceptance',lambda _:{'path':'/pytest-only-acceptance.json','status':'measured_pass','test_double':True})


def test_prepared_seed_handoff_cannot_skip_dryrun_or_change_output(tmp_path, monkeypatch, mocked_performance_acceptance):
    output = tmp_path / 'new'
    args = ['python', '-m', 'backend.pipeline.viewpoint_cvp_campaign_runner',
            '--output-root', str(output), '--validation-runtime']
    calls = []
    def fail_preflight(argv, attempt, name, **kwargs):
        calls.append((argv, name))
        raise ValueError('actual preflight failed')
    monkeypatch.setattr(m, 'run_logged', fail_preflight)
    monkeypatch.setattr(supervisor, 'process_table', lambda: {})
    (tmp_path/'attempt').mkdir()
    with pytest.raises(ValueError, match='output identity drift'):
        m.start_prepared_campaign(tmp_path/'old', tmp_path/'foreign', tmp_path/'attempt',
                                  tmp_path/'recovery', 'codex', args, {})
    assert calls == []
    with pytest.raises(ValueError, match='actual preflight failed'):
        m.start_prepared_campaign(tmp_path/'old', output, tmp_path/'attempt',
                                  tmp_path/'recovery', 'codex', args, {})
    assert len(calls) == 1 and calls[0][1] == 'start-campaign'
    assert '--startup-handoff' in calls[0][0] and '--dry-run' not in calls[0][0]
    assert (tmp_path/'attempt/startup-abort.json').exists()


def test_repaired_owner_checkpoint_never_preflights_or_starts_next_group(tmp_path,monkeypatch):
    from backend.pipeline import viewpoint_group_audit_population as population
    root=tmp_path/'campaign';attempt=tmp_path/'attempt';root.mkdir();attempt.mkdir()
    (root/'progress.json').write_text('{"sequence":1,"group_key":"g"}')
    run=root/'g001/production/run';batch=run/'batches/B';batch.mkdir(parents=True)
    (run/'scope-run.json').write_text('{"disposition_status":"complete"}')
    (batch/'cvp-change-plan.json').write_text('{}')
    (tmp_path/'audit.json').write_text('{}')
    monkeypatch.setattr(m,'load_campaign',lambda *_:({'validation_checkpoint_sequence':1},{'schedule':[{}]}, {}, {}))
    monkeypatch.setattr(population,'group_audit_targets',lambda _:['CV'])
    proof={'group_key':'g','bindings':[],'viewpoint_ids':['CV']}
    monkeypatch.setattr(m,'verify_group_audit',lambda *a,**k:proof)
    monkeypatch.setattr(m,'continuation_inputs',lambda *a,**k:pytest.fail('no next-group seed/start'))
    monkeypatch.setattr(m,'run_logged',lambda *a,**k:pytest.fail('no next-group preflight/provider/start'))
    result=m.resume_after_repair(root,attempt,tmp_path/'recovery','unused',saved_audit_path=tmp_path/'audit.json')
    assert result['status']=='owner_validation_checkpoint'
    assert result['completed_group_proof']==proof and result['no_next_group_started']
    assert not result['production_complete'] and not result['production_restarted']


@pytest.mark.parametrize('rejected', [False, True])
def test_saved_audit_component_is_verified_once_not_twice(tmp_path, monkeypatch, rejected):
    from backend.pipeline import viewpoint_group_audit_population as population
    from backend.pipeline import viewpoint_g034_component_completion as component
    root=tmp_path/'campaign'; attempt=tmp_path/'attempt'
    root.mkdir(); attempt.mkdir()
    (root/'progress.json').write_text('{"sequence":1,"group_key":"g"}')
    run=root/'g001/production/run'; batch=run/'batches/B'; batch.mkdir(parents=True)
    (run/'scope-run.json').write_text('{"disposition_status":"incomplete"}')
    (batch/'cvp-change-plan.json').write_text('{}')
    (batch/'cvp-change-package.json').write_text('{}')
    (batch/'cvp-readback-receipt.json').write_text('{"readback_status":"verified"}')
    audit=tmp_path/'audit.json'; audit.write_text('{"meta":{},"layers":{"1":{},"2":{}}}')
    proof_path=tmp_path/'component.json'; proof_path.write_text('{}')
    monkeypatch.setattr(m,'load_campaign',lambda *_:({'validation_checkpoint_sequence':1,'authorized_by':'owner'},
        {'schedule':[{}]}, {}, {'g':{'claim_ids':['C']}}))
    monkeypatch.setattr(population,'group_audit_targets',lambda _:['CV'])
    calls=[]
    def verify(campaign, path, ids):
        calls.append((campaign,path,ids))
        if rejected: raise ValueError('component proof drift')
        return {'viewpoint_ids':['CV-new'], 'bindings':[]}
    monkeypatch.setattr(component,'verify_proof',verify)
    audits=[]
    monkeypatch.setattr(m,'validate_audit',lambda *a,**kw:audits.append(a))
    monkeypatch.setattr(m,'scope_audit_disposition',lambda **kw:{'status':'passed'})
    monkeypatch.setattr(m,'accepted_grouping_audit_scope',lambda *a:{})
    from backend.pipeline import viewpoint_g035_temporary_audit as exception
    monkeypatch.setattr(exception,'validate',lambda _:[])
    monkeypatch.setattr(m,'run_logged',lambda *a,**kw:pytest.fail('no provider/startup'))
    options=dict(saved_audit_path=audit,component_resolution_path=proof_path)
    if rejected:
        with pytest.raises(ValueError,match='component proof drift'):
            m.resume_after_repair(root,attempt,tmp_path/'recovery','unused',**options)
        assert not audits and not (attempt/'verified-group/completion.json').exists()
    else:
        result=m.resume_after_repair(root,attempt,tmp_path/'recovery','unused',**options)
        assert result['status']=='owner_validation_checkpoint'
        assert result['completed_group_proof']['viewpoint_ids']==['CV','CV-new']
        assert len(audits)==1
    assert calls==[(root,proof_path,['C'])]


def test_new_audit_rejects_bad_component_before_provider(tmp_path, monkeypatch):
    from backend.pipeline import viewpoint_g034_component_completion as component
    root=tmp_path/'campaign'; attempt=tmp_path/'attempt'; root.mkdir(); attempt.mkdir()
    (root/'progress.json').write_text('{"sequence":1,"group_key":"g"}')
    run=root/'g001/production/run'; run.mkdir(parents=True)
    (run/'scope-run.json').write_text('{"disposition_status":"incomplete"}')
    monkeypatch.setattr(m,'load_campaign',lambda *_:({}, {}, {}, {'g':{'claim_ids':['C']}}))
    def reject(*args):raise ValueError('component proof drift')
    monkeypatch.setattr(component,'verify_proof',reject)
    monkeypatch.setattr(m,'run_logged',lambda *a,**kw:pytest.fail('no provider/startup'))
    with pytest.raises(ValueError,match='component proof drift'):
        m.resume_after_repair(root,attempt,tmp_path/'recovery','unused',
                              component_resolution_path=tmp_path/'component.json')


@pytest.mark.parametrize("active", [False, True])
def test_restart_requires_backup_dryrun_and_unique_real_process(tmp_path, monkeypatch, active, mocked_performance_acceptance):
    root, attempt, recovery = [tmp_path / n for n in ("old", "fingerprint", "recovery")]
    root.mkdir(); attempt.mkdir()
    (root / "progress.json").write_text('{"sequence":8,"group_key":"g"}')
    monkeypatch.setattr(m, "load_campaign", lambda *a: ({}, {}, {}, {}))
    output = m.continuation_root(root, attempt)
    def inputs(*args):
        output.mkdir()
        return ["python", "-m", "backend.pipeline.viewpoint_cvp_campaign_runner", "--output-root", output, '--validation-runtime']
    monkeypatch.setattr(m, "continuation_inputs", inputs)
    calls = []
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"test dump (mock pg_restore)")
    def run(argv, directory, name, **kw):
        calls.append((name, argv))
        if name == "start-campaign":
            assert "--dry-run" not in argv and '--startup-handoff' in argv
            assert kw["env"]["CODEX_EXECUTABLE"] == "chosen-codex"
            (output / "progress.json").write_text('{"status":"preflight_passed_no_model_calls"}')
            (output / "backup.json").write_text(json.dumps(m.binding(backup)))
        if name == 'backup-restore-list':
            (directory/'backup-restore-list.stdout').write_text('verified catalog')
            (directory/'backup-restore-list.json').write_text(json.dumps({'exit_code':0,'argv':[str(a) for a in argv]}))
    monkeypatch.setattr(m, "run_logged", run)
    monkeypatch.setattr(m, "verify_runtime", lambda client, cache: {
        "identity": {"model": client.model, "cli": {"path": client.executable}}, "new_model_calls": 0})
    command = (f'python -m backend.pipeline.viewpoint_cvp_campaign_runner --output-root {output}'
               f' --validation-runtime --startup-handoff {attempt}/startup-request.json')
    monkeypatch.setattr(supervisor, "process_table", lambda: {10:command} if active or calls else {})
    from backend.pipeline import viewpoint_startup_handoff as startup
    releases=[]
    def ready(request,pid,alive):
        assert pid==10 and alive(pid)
        startup.save(attempt/'startup-ready.json',{'test_double':True})
        return {'status':'preflight_passed_no_model_calls',
                'progress':{'sequence':9,'group_key':'next'},'backup':m.binding(backup)}
    monkeypatch.setattr(startup,'wait_ready',ready)
    monkeypatch.setattr(startup,'release',lambda *a:releases.append(a))
    def consumed(request,pid,alive):
        assert alive(pid)
        startup.save(attempt/'startup-release-consumed.json',{'test_double':True})
    monkeypatch.setattr(startup,'wait_consumed',consumed)
    if active:
        with pytest.raises(ValueError, match="producer active"):
            m.resume_after_repair(root, attempt, recovery, "chosen-codex")
        assert calls == [] and releases == []
    else:
        result = m.resume_after_repair(root, attempt, recovery, "chosen-codex")
        assert result["campaign_pid"] == 10 and not result["production_complete"]
        assert [name for name, _ in calls] == ["start-campaign", "backup-restore-list", "attach-supervisor"]
        assert len(releases)==1 and result['owned_runtime_startups']==1
        assert "-Logfile" not in calls[0][1]  # screen 4.00.03 on this machine
        assert "logfile flush 1" in (attempt / "campaign.screenrc").read_text()
        assert json.loads((attempt / "codex-runtime-preflight.json").read_text())["identity"]["model"] == "gpt-6.1-sol"


def test_bad_runtime_stops_before_background_launch_and_retains_reason(tmp_path, monkeypatch, mocked_performance_acceptance):
    root, attempt, recovery = [tmp_path / n for n in ('old', 'attempt', 'recovery')]
    root.mkdir(); attempt.mkdir()
    (root / 'progress.json').write_text('{"sequence":13,"group_key":"g"}')
    monkeypatch.setattr(m, 'load_campaign', lambda *a: ({}, {}, {}, {}))
    output = m.continuation_root(root, attempt)
    def inputs(*a):
        output.mkdir()
        return ['python', '-m', 'backend.pipeline.viewpoint_cvp_campaign_runner',
                '--output-root', output, '--validation-runtime']
    monkeypatch.setattr(m, 'continuation_inputs', inputs)
    backup = tmp_path / 'backup.dump'
    backup.write_bytes(b'backup')
    calls = []
    def run(argv, directory, name, **kw):
        calls.append(name)
        assert name not in {'start-campaign', 'attach-supervisor'}
        if name == 'campaign-preflight':
            (output / 'progress.json').write_text('{"status":"preflight_passed_no_model_calls"}')
            (output / 'backup.json').write_text(json.dumps(m.binding(backup)))
    monkeypatch.setattr(m, 'run_logged', run)
    monkeypatch.setattr(supervisor, 'process_table', lambda: {})
    def reject(*a):
        raise ValueError('stale executable; not an account entitlement claim')
    monkeypatch.setattr(m, 'verify_runtime', reject)
    with pytest.raises(ValueError, match='stale executable'):
        m.resume_after_repair(root, attempt, recovery, 'old-codex')
    assert calls == []
    failure = json.loads((attempt / 'codex-runtime-failure.json').read_text())
    assert failure['production_started'] is False
    assert not (attempt / 'continuation-receipt.json').exists()
