from copy import deepcopy
import pytest
from backend.pipeline import viewpoint_historical_review_bridge as m
from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked


def fixture(monkeypatch):
    old={'evidence_step_id':'E1','statement':'same argument','produced_claim_ids':['C1'],'source_fragment_ids':['F1']}
    new=deepcopy(old); new['produced_claim_ids'].append('C2')
    final={'evidence_steps':{'E1':deepcopy(new)},'claims':{'C2':{'evidence_step_ids':['E1']}}}
    monkeypatch.setattr(m,'_normalize_records',lambda p:(p,None))
    item={'payload':final,'application':{'artifact_sha256':'authenticated-seal'}}
    return old,new,item


def test_reuses_exact_sealed_additive_graph_not_initial_reverse_index(monkeypatch):
    old,new,item=fixture(monkeypatch)
    result=m.verify_sealed_reverse_index('E1',old,new,item)
    assert result['added_claim_ids']==['C2'] and result['evidence_content_unchanged']


@pytest.mark.parametrize('case',['unsealed','wording','foreign_current','missing_forward','deleted','reordered','duplicate'])
def test_reverse_index_cannot_hide_content_or_unapproved_graph(monkeypatch,case):
    old,new,item=fixture(monkeypatch)
    if case=='unsealed': item=None
    elif case=='wording': new['statement']='different'; item['payload']['evidence_steps']['E1']=deepcopy(new)
    elif case=='foreign_current': new['produced_claim_ids'].append('unknown')
    elif case=='missing_forward': item['payload']['claims']['C2']['evidence_step_ids']=[]
    elif case=='deleted': new['produced_claim_ids']=['C2']; item['payload']['evidence_steps']['E1']=deepcopy(new)
    elif case=='duplicate': new['produced_claim_ids'].append('C2'); item['payload']['evidence_steps']['E1']=deepcopy(new)
    else:
        old['produced_claim_ids']=['C1','C0']; new['produced_claim_ids']=['C0','C1','C2']; item['payload']['evidence_steps']['E1']=deepcopy(new)
    with pytest.raises(CvpProductionBlocked): m.verify_sealed_reverse_index('E1',old,new,item)
