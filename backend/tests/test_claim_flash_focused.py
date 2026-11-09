from copy import deepcopy
import json
import pytest
from backend.pipeline import claim_flash_common as f,claim_flash_evidence as eo,evidence_refresh as e


def job():
    return {'job_id':'evidence_steps/E','record':{'statement':'原摘要。','speaker':'professor','stance':'asserted'},
            'sources':[{'paragraph_key':'S0001','quote':'原文。'}],'components':[],
            'jev':{'record_fidelity':'faithful','component_0':'supported','speaker_consistency':'matched',
                   'stance_classification':'quoted','stance_consistency':'different'}}


def test_only_flagged_fields_are_writable_and_all_context_is_preserved():
    request={'system':eo.PROMPT,'identity':{},'input':{'task':'evidence_fidelity_repair','jobs':[job()]},'schema':{}}
    before=deepcopy(request);focused=eo.focused_request(request)
    assert request==before
    assert focused['input']['jobs'][0]['writable_fields']==['stance']
    assert focused['input']['jobs'][0]['review_focus']=={'stance_consistency':'different'}
    assert focused['input']['jobs'][0]['sources']==job()['sources']
    good={'items':[{'job_id':job()['job_id'],'action':'repair','stance':'quoted','reason':'原文只是引述。'}]}
    eo.validate_focused_reply(focused,good)
    bad=deepcopy(good);bad['items'][0]['statement']='擅自重寫已通過摘要。'
    with pytest.raises(ValueError,match='outside'):eo.validate_focused_reply(focused,bad)


def test_unknown_call_requires_exact_owner_recovery_and_is_reissued_only_once(tmp_path):
    class Ports:
        identity={'gpt':{'backend':'test','model':'test','effort':'medium'}}
        def __init__(self):self.root=tmp_path/'providers';self.calls=[];self.fail=True
        def repair(self,req):
            self.calls.append(req)
            if self.fail:raise TimeoutError('original call ended without reply')
            return {'items':[{'job_id':j['job_id'],'action':'repair','stance':'quoted','reason':'僅核對採納立場。'} for j in req['input']['jobs']]}
    ports=Ports();root=tmp_path/'fidelity'
    def run():return list(f.gpt(root,ports,'evidence_fidelity_repair',[job()],eo.Edits.model_json_schema(),eo.PROMPT,semantic_review=False))
    with pytest.raises(TimeoutError):run()
    old=next(root.glob('gpt/*/started.json')).parent;key=old.name
    ports.focus_evidence_findings=True;ports.focused_review_instruction='應該只讓GPT檢查有問題的'
    with pytest.raises(ValueError,match='explicit bounded'):run()
    assert len(ports.calls)==1
    exit_path=ports.root/'gpt'/key/'transport/exit.json'
    e.save(exit_path,{'error_type':'TimeoutExpired','returncode':None})
    e.save(old/'owner-recovery.json',{'owner_direction':'重跑一下吧','max_reissues':1,
        'original_request_sha256':key,'exit_path':str(exit_path),'exit_sha256':e.digest(json.loads(exit_path.read_text()))})
    ports.fail=False;result=run()
    assert len(ports.calls)==2 and ports.calls[-1]['focused_contract']==eo.FOCUSED_CONTRACT
    assert ports.calls[-1]['execution_recovery']['original_request_sha256']==key
    assert not (old/'response.json').exists() and (old/'started.json').exists()
    assert run()==result and len(ports.calls)==2


def test_successful_legacy_reply_is_reused_before_focusing_new_requests(tmp_path):
    class Ports:
        identity={'gpt':{'backend':'test','model':'test','effort':'medium'}}
        focus_evidence_findings=False
        focused_review_instruction='只查Jev疑點'
        def __init__(self):self.calls=0
        def repair(self,req):
            self.calls+=1
            return {'items':[{'job_id':j['job_id'],'action':'keep','reason':'舊完整判定保留。'} for j in req['input']['jobs']]}
    ports=Ports()
    def run():return list(f.gpt(tmp_path/'fidelity',ports,'evidence_fidelity_repair',[job()],eo.Edits.model_json_schema(),eo.PROMPT,semantic_review=False))
    original=run();ports.focus_evidence_findings=True
    assert run()==original and ports.calls==1
