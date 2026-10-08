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
