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


def test_binding_batch_sizes_actual_shared_wire_and_splits_without_losing_paragraphs(tmp_path,monkeypatch):
    import json
    from backend.pipeline.claim_flash_source import shared_binding_input
    calls=[]
    def repair(req):
        calls.append(req)
        assert len(json.dumps(req,ensure_ascii=False).encode())<=f.GPT_BYTES
        for job in req['input']['jobs']:
            for key in job['material']['nearby_paragraph_keys']:
                assert req['input']['source_paragraphs'][key]['text']
        return {'items':[{'job_id':j['job_id']} for j in req['input']['jobs']]}
    ports=SimpleNamespace(identity={'gpt':{'model':'test'}},repair=repair)
    schema={'type':'object','properties':{'items':{'type':'array'}}}
    common={'paragraph_key':'S0001','text':'x'*4000}
    jobs=[{'job_id':str(i),'material':{'nearby_paragraphs':[common]}} for i in range(3)]
    monkeypatch.setattr(f,'GPT_BYTES',5000)
    result=list(f.gpt(tmp_path/'shared',ports,'source_binding_completion',jobs,schema,'find bindings',
        semantic_review=False,project_input=shared_binding_input))
    assert len(calls)==1 and len(result[0][0])==3
    jobs=[{'job_id':str(i),'material':{'nearby_paragraphs':[{'paragraph_key':f'S{i}','text':'x'*3000}]}} for i in range(3)]
    result=list(f.gpt(tmp_path/'split',ports,'source_binding_completion',jobs,schema,'find bindings',
        semantic_review=False,project_input=shared_binding_input))
    assert len(result)==3 and [j['job_id'] for batch,reply in result for j in batch]==['0','1','2']
    jobs.append({'job_id':'too-big','material':{'nearby_paragraphs':[{'paragraph_key':'S9','text':'x'*6000}]}})
    with pytest.raises(ValueError,match='payload limit'):
        list(f.gpt(tmp_path/'reject',ports,'source_binding_completion',jobs,schema,'find bindings',
            semantic_review=False,project_input=shared_binding_input))
    assert len(calls)==4  # Oversized late job rejected before any partial call.
