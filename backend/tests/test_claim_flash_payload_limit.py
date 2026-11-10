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


def test_flash_jev_batches_questions_above_old_byte_guard_without_changing_legacy(tmp_path):
    calls=[]
    def judge(request):
        calls.append(request)
        return {'model':e.MODEL,'answers':{name:{'type':'choice','choice':'yes','probabilities':{'yes':1.,'no':0.}}
            for name in request['questions']}}
    ports=SimpleNamespace(judge=judge)
    spec={'type':'choice','instructions':'Does the provided text contain evidence?','criteria':{'yes':'Yes','no':'No'}}
    questions={'one':spec,'two':spec}
    assert f.JEV_BYTES==128000
    result=f.jev(tmp_path/'flash',ports,{'text':'x'*53000},questions)
    assert result=={'one':'yes','two':'yes'} and len(calls)==1
    assert calls[0]['state']=={'text':'x'*53000} and calls[0]['questions']==questions
    with pytest.raises(ValueError,match='bounded request size'):
        e.recorded_call(tmp_path/'legacy-jev','jev',calls[0],judge)
    assert len(calls)==1


def test_creation_flash_uses_the_same_128k_transport_limit(tmp_path):
    from backend.pipeline import claim_creation_flash as creation
    calls=[]
    def repair(request):calls.append(request);return {'ok':True}
    ports=SimpleNamespace(identity={'gpt':{'model':'test'}},repair=repair)
    contract={'schema':{'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok']}}
    result=creation.call(tmp_path,ports,'test',{'text':'x'*90000},contract,'one task',lambda:None)
    assert result=={'ok':True} and len(calls)==1
