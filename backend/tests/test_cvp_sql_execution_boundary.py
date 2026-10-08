"""Owner-bounded SQL execution never qualifies the next group or loses its limit."""
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from backend.pipeline import cvp_sql_flow as flow


def test_same_root_cannot_drop_expand_or_change_campaign_boundary(tmp_path):
    definition={'groups':[{},{}]}
    flow.execution_boundary(tmp_path,'campaign',definition,1,persist=True)
    flow.execution_boundary(tmp_path,'campaign',definition,1)
    for campaign,limit in [('campaign',None),('campaign',2),('other',1)]:
        with pytest.raises(ValueError,match='immutable execution boundary'):
            flow.execution_boundary(tmp_path,campaign,definition,limit)
    with pytest.raises(ValueError,match='outside'):
        flow.execution_boundary(tmp_path,'campaign',definition,3)


@pytest.mark.parametrize('finished',[True,False])
def test_main_stops_only_after_actual_audited_boundary(monkeypatch,tmp_path,finished):
    definition={'groups':[{},{}], 'campaign_sha256':'campaign'}
    group={'sequence':1,'group_key':'first'}
    store=Mock()
    store.cvp_campaign_progress.return_value=[{'group_key':'first','run_id':'run',
                                             'checkpoint':'audited' if finished else 'applied'}]
    next_group=Mock(return_value=(definition,group,None))
    run=Mock()
    monkeypatch.setattr(flow,'PostgresKnowledgeStore',lambda _:store)
    monkeypatch.setattr(flow,'next_group',next_group)
    monkeypatch.setattr(flow,'run_group',run)
    from backend.pipeline import source_reconciliation_runtime
    monkeypatch.setattr(source_reconciliation_runtime,'maintenance_lock',nullcontext)
    args=['--sql-campaign','campaign','--output-root',str(tmp_path),'--execute','--stop-after-group','1']
    if finished:
        assert flow.main(args)==0
        store.fail_cvp_group.assert_not_called()
    else:
        with pytest.raises(ValueError,match='actual SQL audited'):
            flow.main(args)
        store.fail_cvp_group.assert_called_once()
    run.assert_called_once()
    # No second next-group lookup, preparation, model request or action for 2.
    next_group.assert_called_once()


def test_completed_boundary_returns_before_preparation(monkeypatch,tmp_path):
    monkeypatch.setattr(flow,'PostgresKnowledgeStore',lambda _:None)
    monkeypatch.setattr(flow,'next_group',lambda *_:({'groups':[{},{}]}, {'sequence':2},None))
    preparation=Mock(side_effect=AssertionError('must not qualify next group'))
    monkeypatch.setattr(flow,'prepare_group',preparation)
    assert flow.main(['--sql-campaign','c','--output-root',str(tmp_path),'--dry-run','--stop-after-group','1'])==0
    preparation.assert_not_called()


def test_acceptance_pause_preserves_original_boundary_and_never_prepares_next(monkeypatch,tmp_path):
    definition={'groups':[{},{}], 'campaign_sha256':'campaign'}
    group={'sequence':1,'group_key':'first'}
    store=Mock()
    store.cvp_campaign_progress.return_value=[{'group_key':'first','run_id':'run','checkpoint':'audited'}]
    next_group=Mock(return_value=(definition,group,None))
    monkeypatch.setattr(flow,'PostgresKnowledgeStore',lambda _:store)
    monkeypatch.setattr(flow,'next_group',next_group)
    monkeypatch.setattr(flow,'run_group',Mock())
    from backend.pipeline import source_reconciliation_runtime
    monkeypatch.setattr(source_reconciliation_runtime,'maintenance_lock',nullcontext)
    assert flow.main(['--sql-campaign','campaign','--output-root',str(tmp_path),'--execute',
                      '--stop-after-group','2','--pause-after-group','1'])==0
    assert flow.read_artifact(tmp_path/'execution-boundary.json')['stop_after_group']==2
    next_group.assert_called_once()


@pytest.mark.parametrize('limit,pause',[(None,'1'),('1','2'),('2','0'),('2','3')])
def test_acceptance_pause_cannot_expand_boundary(monkeypatch,tmp_path,limit,pause):
    monkeypatch.setattr(flow,'PostgresKnowledgeStore',lambda _:None)
    monkeypatch.setattr(flow,'next_group',lambda *_:({'groups':[{},{}]}, {'sequence':1},None))
    args=['--sql-campaign','c','--output-root',str(tmp_path),'--dry-run','--pause-after-group',pause]
    if limit:args+=['--stop-after-group',limit]
    with pytest.raises(ValueError,match='must narrow'):flow.main(args)


def test_verified_archive_logical_binding_not_confused_with_physical_path(monkeypatch,tmp_path):
    from backend.pipeline import viewpoint_backup_archive as archive
    physical=tmp_path/'physical.dump';physical.write_bytes(b'exact backup')
    logical=tmp_path/'logical.dump';logical.symlink_to(physical)
    checks=[]
    def verified(path):
        checks.append(path)
        assert logical.resolve()==physical
        return logical
    monkeypatch.setattr(archive,'logical_binding_path',verified)
    pin=flow.binding(logical)
    assert pin['path']==str(logical)
    flow.check_binding(pin)
    physical.write_bytes(b'drift')
    with pytest.raises(ValueError,match='binding drift'):flow.check_binding(pin)
    assert len(checks)==3


def test_completed_backup_recovered_from_exact_receipt_without_another_dump(monkeypatch,tmp_path):
    from backend.pipeline import viewpoint_backup_archive as archive
    from backend.api.canonical_repository.postgres_store import sha256_json
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    root=tmp_path/'execution';root.mkdir()
    label='before-source-357-'+sha256_json(str(root))[:32]
    local=tmp_path/'archives';attempt=local/('registry-backup-2026-10-08-'+label);attempt.mkdir(parents=True)
    dump=attempt/archive.NAME;dump.write_bytes(b'valid dump')
    pin=flow.binding(dump)
    body={'direct_ssd':True,'original_path':pin['path'],'file_sha256':pin['file_sha256'],
          'restore_catalog_sha256':'catalog'}
    write_immutable(attempt/archive.RECEIPT,{**body,'artifact_sha256':sha256_json(body)})
    monkeypatch.setattr(archive,'LOCAL',local)
    monkeypatch.setattr(archive,'catalog',lambda _: 'catalog')
    ports=Mock();ports.backup.side_effect=AssertionError('must not repeat backup')
    backup=flow.verified_group_backup(root,{'approved':'package'},ports)
    assert backup['dump']==pin
    assert flow.verified_group_backup(root,{'approved':'package'},ports)==backup
    with pytest.raises(ValueError,match='different reviewed package'):
        flow.verified_group_backup(root,{'changed':'package'},ports)
    ports.backup.assert_not_called()
