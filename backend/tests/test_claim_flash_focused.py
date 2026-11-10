from copy import deepcopy
import json
import pytest
from backend.pipeline import claim_flash_common as f,claim_flash_evidence as eo,evidence_refresh as e
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


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


def test_outer_evidence_runner_admits_only_the_authorized_timeout_then_commits(sample):
    from backend.tests.test_claim_flash_evidence import Ports as BasePorts,saved_inventory
    store,root,_=sample;records,sources=f.load_source(store,'S')
    plan={'source_id':'S','source_body_sha256':sources['S']['body_sha256'],'groups':[{
        'group_id':'G','paragraph_keys':['S0001'],'lower_record_keys':['evidence_steps/E'],'reference_record_keys':[]}]}
    previous=saved_inventory(root/'prior',sources)
    class Ports(BasePorts):
        def __init__(self):super().__init__();self.root=root/'providers';self.fail=True
        def repair(self,request):
            if self.fail and request['input']['task']=='evidence_fidelity_repair':raise TimeoutError('ended timeout')
            return super().repair(request)
    ports=Ports();eroot=root/'evidence'
    with pytest.raises(TimeoutError):eo.run(store,'S',plan,eroot,ports,previous,apply=True)
    old=next(p.parent for p in eroot.rglob('started.json') if not (p.parent/'response.json').exists())
    exit_path=ports.root/'gpt'/old.name/'transport/exit.json'
    e.save(exit_path,{'error_type':'TimeoutExpired','returncode':None})
    e.save(old/'owner-recovery.json',{'owner_direction':'重跑一下吧','max_reissues':1,
        'original_request_sha256':old.name,'exit_path':str(exit_path),'exit_sha256':e.digest(json.loads(exit_path.read_text()))})
    ports.focus_evidence_findings=True;ports.focused_review_instruction='只查有疑點的部分';ports.fail=False
    result=eo.run(store,'S',plan,eroot,ports,previous,apply=True)
    assert result['status']=='completed'
    assert store.get_record_state('evidence_steps','E')['payload']['statement']=='沒有聽從祂的話，顯明沒有真正相信祂。'
    assert not (old/'response.json').exists()


def test_fused_gpt_uses_shared_jev_role_definitions_and_legacy_prompt_is_unchanged(monkeypatch):
    from backend.pipeline import claim_flash_questions as q
    for focused in (False,True):
        prompt=eo.completion_prompt(focused)
        assert 'record.statement' in prompt and '記錄本身提出問題' in prompt
        for field in ('speaker','stance'):
            for option,meaning in q.ROLES[field][1].items():
                assert option in prompt and meaning in prompt
    # Updating the authoritative definition changes both consumers, rather
    # than allowing a copied GPT option list to drift away from Jev.
    monkeypatch.setitem(q.ROLES['stance'][1],'asserted','共用定義測試')
    assert q.role('stance','測試')['criteria']['asserted']=='共用定義測試'
    assert '共用定義測試' in eo.completion_prompt(True)
    legacy={'system':eo.PROMPT,'identity':{},'input':{'task':'evidence_fidelity_repair','jobs':[job()]},'schema':{}}
    assert eo.focused_request(legacy)['system']==eo.FOCUSED_PROMPT


@pytest.mark.parametrize('mode',['joint_repair','speaker_only','stance_only_scope','legacy_scope'])
def test_statement_subject_repair_can_update_its_dependent_role_only_when_authorized(mode):
    value=job();value['record'].update(statement='教授承認自己先前沒有想到。',speaker='professor',stance='asserted')
    value['jev']={'record_fidelity':'unsupported','component_0':'partial','speaker_classification':'professor',
                  'speaker_consistency':'matched','stance_classification':'asserted','stance_consistency':'matched'}
    if mode=='stance_only_scope':value['jev']={'stance_consistency':'different'}
    data={'task':'evidence_fidelity_repair','jobs':[value]}
    if mode!='legacy_scope':data['source_completion_contract']=eo.COMPLETION_CONTRACT
    request=eo.focused_request({'input':data,'schema':{}})
    item={'job_id':value['job_id'],'action':'repair','statement':'聽眾表示自己先前沒有想到。',
          'speaker':'audience','reason':'原文的說話者標記明確，摘要與角色須一致修正。'}
    if mode=='speaker_only':item.pop('statement')
    if mode=='joint_repair':eo.validate_focused_reply(request,{'items':[item]})
    else:
        with pytest.raises(ValueError,match='outside'):eo.validate_focused_reply(request,{'items':[item]})
