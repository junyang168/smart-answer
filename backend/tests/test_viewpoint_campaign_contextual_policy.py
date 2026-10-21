from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.pipeline import viewpoint_cvp_campaign_runner as m
from backend.pipeline.viewpoint_semantic_contract import CONTRACT_PATH


def args():
    policy=m.PROJECT_ROOT/'backend/pipeline/policies/wang_cvp_resolution_policy_v5.json'
    return SimpleNamespace(cvp_policy=policy,cvp_policy_sha256=m.binding(policy)['file_sha256'],
        semantic_contract_sha256=m.binding(CONTRACT_PATH)['file_sha256'],
        accepted_grouping_sha='same',grouping_repository=Path('/frozen'))


def test_legacy_campaign_does_not_silently_upgrade():
    a=SimpleNamespace(accepted_grouping_sha='same',grouping_repository=Path('/frozen'))
    path,bindings=m.campaign_policy(a)
    assert path.endswith('policy_v3.json') and bindings=={}
    argv=m.runner_args(Path('/old'),a)
    assert argv[argv.index('--cvp-policy')+1]==path


def test_new_campaign_prepare_and_runner_select_same_physically_bound_v5():
    a=args();path,bindings=m.campaign_policy(a)
    assert path==str(a.cvp_policy) and set(bindings)=={'cvp_policy','semantic_contract'}
    argv=m.runner_args(Path('/new'),a)
    assert argv[argv.index('--cvp-policy')+1]==path
    # Preparation uses the same campaign_policy helper, not a second hardcoded
    # default. Full production dry-run is still required before launching.


def test_real_prepare_command_receives_v5_not_hardcoded_v3(tmp_path,monkeypatch):
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    a=args();a.output_root=tmp_path;a.random_spot_check_policy=None
    a.entrance_audit=tmp_path/'audit';a.wang_repository=tmp_path/'wang'
    a.global_lock=tmp_path/'lock';a.authorized_by='owner';a.completed_source_repair=None
    dump=tmp_path/'backup.dump';dump.write_bytes(b'existing physical backup')
    write_immutable(tmp_path/'backup.json',m.binding(dump))
    calls=[]
    monkeypatch.setattr(m,'build_selection',lambda *args:None)
    monkeypatch.setattr(m,'command',lambda argv:calls.append(argv))
    m.qualify({}, {'group_key':'frozen-group'}, {},tmp_path/'g038',a)
    prepare=calls[1]
    assert prepare[prepare.index('--cvp-policy')+1]==str(a.cvp_policy)


def test_policy_upgrade_never_relabels_old_model_cache(tmp_path):
    # The obsolete orchestration is no longer a supported solution.
    # Current content validators and SQL action tests carry its safety rules.
    from backend.pipeline import viewpoint_cvp_recovery_executor as worker
    with pytest.raises(RuntimeError,match='runtime is retired'):
        worker.resume_after_repair(None,None,None,None)


@pytest.mark.parametrize('field',['cvp_policy','cvp_policy_sha256','semantic_contract_sha256'])
def test_missing_wrong_or_changed_rule_identity_stops_before_models(field):
    a=args();setattr(a,field,'changed')
    with pytest.raises(ValueError):m.campaign_policy(a)
