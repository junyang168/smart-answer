from copy import deepcopy
import pytest
from backend.pipeline import claim_flash_common as f,evidence_refresh as e


@pytest.mark.parametrize('change',[None,'edge','duplicate','prompt','source','model'])
def test_only_an_edge_permutation_reuses_saved_answer(tmp_path,change):
    request={'identity':{'model':'old'},'system':'same prompt','schema':{},'input':{
        'task':'argument_completeness','jobs':[{'job_id':'C'}],
        'existing_edges':[{'a':'A','b':'B'},{'a':'B','b':'C'}],'fragments':{'F':'原文。'}}}
    original=deepcopy(request);path=tmp_path/'gpt'/e.digest(request)
    e.save(path/'request.json',request);e.save(path/'response.json',{'items':[]})
    request['input']['existing_edges'].reverse()
    if change=='edge':request['input']['existing_edges'][0]['b']='other'
    if change=='duplicate':request['input']['existing_edges'].append(request['input']['existing_edges'][0])
    if change=='prompt':request['system']='new prompt'
    if change=='source':request['input']['fragments']['F']='改動。'
    if change=='model':request['identity']['model']='other'
    before=deepcopy(request);result=f.reuse_argument_edge_order(tmp_path,request)
    assert request==before
    assert result==(request if change else original)
    assert len(list((tmp_path/'edge-order-reuse').glob('*.json')))==(0 if change else 1)
