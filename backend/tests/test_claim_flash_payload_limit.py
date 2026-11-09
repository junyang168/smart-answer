from types import SimpleNamespace
import pytest
from backend.pipeline import claim_flash_common as f,evidence_refresh as e


def test_flash_128k_reaches_transport_but_legacy_and_jev_keep_limits(tmp_path):
    assert f.GPT_BYTES==128000
    calls=[]
    def repair(request):calls.append(request);return {'items':[{'job_id':'one'}]}
    ports=SimpleNamespace(identity={'gpt':{'model':'test'}},repair=repair)
    schema={'type':'object','properties':{'items':{'type':'array'}}}
    jobs=[{'job_id':'one','text':'x'*100000}]
    result=list(f.gpt(tmp_path/'flash',ports,'test',jobs,schema,'single task',semantic_review=False))
    assert len(result)==len(calls)==1
    with pytest.raises(ValueError,match='bounded request size'):
        e.recorded_call(tmp_path/'legacy','gpt',calls[0],repair)
    with pytest.raises(ValueError,match='bounded request size'):
        e.recorded_call(tmp_path/'jev','jev',{'text':'x'*40001},repair,gpt_max_bytes=128000)
    with pytest.raises(ValueError,match='payload limit'):
        list(f.gpt(tmp_path/'oversized',ports,'test',[{'job_id':'one','text':'x'*128000}],schema,'single task',semantic_review=False))
    assert len(calls)==1
