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


def test_slice_impact_audit_never_enters_normal_generation_or_completion(monkeypatch):
    from backend.pipeline import claim_source_write_slice
    d,s,store=fixture()
    s['reviewed']['evidence']['approval']={'source_write_slice':True}
    s['reviewed']['evidence']['pending_cvp_management_record_keys']=['evidence_steps/E']
    result={'status':'independent_impact_audited_management_pending','group_complete':False}
    monkeypatch.setattr(claim_source_write_slice,'finish_impact_audit',lambda actual,saved:result)
    monkeypatch.setattr(flow,'run_group',lambda *a,**kw:pytest.fail('no normal replay/completion'))
    assert flow.revalidate_applied_run(store,d,'R',root=Path('/root'))==result
    assert s['checkpoint']=='applied' and s['status']=='running'
    assert s['reviewed']['evidence']['pending_cvp_management_record_keys']==['evidence_steps/E']


@pytest.mark.parametrize('schema',['wang_claim_sql_retirement_v1','wang_claim_relation_retirement_v1'])
def test_retirement_does_not_require_source_anchor_manifest(tmp_path,schema):
    from backend.pipeline.claim_owner_revision import dependency_direction
    from backend.pipeline.cvp_sql_flow import binding
    p=tmp_path/'materials.json';p.write_text('{}')
    value={'reviewed':{'evidence':{'approval':{},'package':{'payload':{
        'source_repair_application':{'schema_version':schema,'materials':binding(p)}}}}}}
    assert dependency_direction(value)==(None,None)
    p.write_text('{"changed":true}')
    with pytest.raises(ValueError):dependency_direction(value)


def test_unclassified_source_contract_still_requires_manifest(tmp_path):
    from backend.pipeline.claim_owner_revision import dependency_direction
    from backend.pipeline.cvp_sql_flow import binding
    p=tmp_path/'materials.json';p.write_text('{}')
    value={'reviewed':{'evidence':{'approval':{},'package':{'payload':{
        'source_repair_application':{'schema_version':'unknown','materials':binding(p)}}}}}}
    with pytest.raises(KeyError):dependency_direction(value)
