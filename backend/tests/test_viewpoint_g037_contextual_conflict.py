from copy import deepcopy
import pytest
from backend.pipeline.viewpoint_g037_contextual_conflict import exact_conflict,TARGET


def fixture():
    item={'item_id':TARGET,'claim':{'revision':3,'statement':'same meaning'},
          'sources':[{'file_sha256':'frozen'}],'audit_finding':{'reason':'first'}}
    old={'items':[item]}
    current=deepcopy(old);current['items'][0]['audit_finding']={'reason':'second'}
    return old,{TARGET:{'verdict':'audit_objection_rejected'}},current,{TARGET:{'verdict':'audit_objection_upheld'}}


def test_only_exact_unchanged_contradiction_allowed():
    exact_conflict(*fixture())


@pytest.mark.parametrize('change',['population','claim','source','identity','verdict','reverse'])
def test_changed_target_or_nonconflict_does_not_create_budget(change):
    old,of,current,cf=fixture()
    if change=='population':current['items'].append(deepcopy(current['items'][0]))
    elif change=='claim':current['items'][0]['claim']['revision']=4
    elif change=='source':current['items'][0]['sources'][0]['file_sha256']='changed'
    elif change=='identity':current['items'][0]['item_id']='different'
    elif change=='verdict':cf[TARGET]['verdict']='unresolved'
    elif change=='reverse':of[TARGET]['verdict'],cf[TARGET]['verdict']='audit_objection_upheld','audit_objection_rejected'
    with pytest.raises(ValueError):exact_conflict(old,of,current,cf)
