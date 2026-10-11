from copy import deepcopy
from pathlib import Path
import pytest
from backend.pipeline import cvp_sql_flow as flow


def fixture():
    group={'group_key':'g','sequence':2,'claim_ids':['C']}
    definition={'campaign_sha256':'campaign','groups':[group]}
    saved={**group,'run_id':'R','campaign_sha256':'campaign','checkpoint':'applied',
           'status':'running','reviewed':{'evidence':{}}}
    class Store:
        failures=[]
        def get_cvp_group(self, run):return deepcopy(saved) if run=='R' else None
        def fail_cvp_group(self, run, error):self.failures.append((run,error))
    return definition,saved,Store()


def test_bounded_revalidation_uses_same_run_and_actual_completion(monkeypatch):
    d,s,store=fixture();calls=[]
    def audit(actual,d,g,row,*,root):
        calls.append((g,row['run_id'],root));s.update(checkpoint='audited',status='succeeded')
    monkeypatch.setattr(flow,'run_group',audit)
    assert flow.revalidate_applied_run(store,d,'R',root=Path('/root'))['status']=='audited'
    assert len(calls)==1 and calls[0][1]=='R'
    assert flow.revalidate_applied_run(store,d,'R',root=Path('/root'))['status']=='already_audited'
    assert len(calls)==1


@pytest.mark.parametrize('mutation',['foreign','membership','sequence','prepared','claims_pending','management_pending','slice'])
def test_no_intake_or_incomplete_management_bypass(monkeypatch,mutation):
    d,s,store=fixture()
    if mutation=='foreign':s['campaign_sha256']='foreign'
    elif mutation=='membership':s['claim_ids']=['other']
    elif mutation=='sequence':s['sequence']=3
    elif mutation=='prepared':s['checkpoint']='prepared'
    elif mutation=='slice':s['reviewed']['evidence']['approval']={'source_write_slice':True}
    else:s['reviewed']['evidence']['pending_cvp_claim_ids' if mutation=='claims_pending' else 'pending_cvp_management_record_keys']=['pending']
    monkeypatch.setattr(flow,'run_group',lambda *a,**kw:pytest.fail('must not enter generation/audit'))
    with pytest.raises(ValueError):flow.revalidate_applied_run(store,d,'R',root=Path('/root'))


def test_audit_start_is_not_completion(monkeypatch):
    d,s,store=fixture()
    monkeypatch.setattr(flow,'run_group',lambda *a,**kw:None)
    with pytest.raises(ValueError,match='actual SQL audited'):
        flow.revalidate_applied_run(store,d,'R',root=Path('/root'))
    assert len(store.failures)==1
