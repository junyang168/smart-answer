from copy import deepcopy
import hashlib
import json

import pytest

from backend.pipeline import viewpoint_g035_temporary_audit as m
from backend.pipeline.viewpoint_resolution_runtime import write_immutable

def original():
    return {'meta':{'model':'gemini-3.8-flash'},'layers':{
        '1':{'findings':[]},'2':{'findings':[]},
        '3':{'complete':True,'judged':1,'results':[{'claim_id':m.CLAIM,'verdict':'supported'}]},
        '4':{'complete':False,'judged':2,'blocked':1,'disputed':1,'model_errors':0,'no_source_text':0,
             'results':[{'viewpoint_id':m.TARGET,'revision_id':m.REVISION,'verdict':'blocked','issue':'other','reason':'PROHIBITED_CONTENT'},
                        {'viewpoint_id':'v2','verdict':'same'},
                        {'viewpoint_id':'v3','revision_id':'r3','verdict':'disputed','issue':'over_merge','claim_ids_in_question':[m.CLAIM]}]}}}

def response():
    return {'verdict':'same','issue':'none','reason':'来源明确支持','claim_ids_in_question':[]}

def make(tmp_path,monkeypatch):
    root=tmp_path/'case'; diag=root/'diagnostic'; output=root/'output'
    old=root/'old-audit.json'; auth=root/'authorization.json'
    write_immutable(old,original())
    schema={'type':'object','properties':{k:{'type':'string'} for k in response()},'required':['verdict','issue','reason']}
    prompt='complete physical original and current Claim/CVP/link'
    write_immutable(root/'owner-identical-retry/request.json',{'contents':[{'parts':[{'text':prompt}]}],'generationConfig':{'responseSchema':schema}})
    raw={'stderr':'model: gpt-6.1-sol\nreasoning effort: high\n'+prompt,'last_message':json.dumps(response())}
    write_immutable(diag/'raw-gpt.json',raw)
    rawsha=m.binding(diag/'raw-gpt.json')['file_sha256']
    write_immutable(diag/'result.json',{'status':'returned','model':'gpt-6.1-sol','backend':'codex_subscription','reasoning_effort':'high','response':response(),'raw_artifact_sha256':rawsha})
    write_immutable(diag/'request.json',{'user_prompt':prompt,'schema':dict(schema,additionalProperties=False,required=list(schema['properties']))})
    write_immutable(diag/'preflight.json',{'model':'gpt-6.1-sol','backend':'codex_subscription','reasoning_effort':'high','no_api_billing':True,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()})
    write_immutable(auth,{'authorization_quote':m.QUOTE,'viewpoint_id':m.TARGET,'revision_id':m.REVISION,'claim_id':m.CLAIM,'group_key':'p020:p020:whole','revisit_required':True,'temporary_model':'gpt-6.1-sol','backend':'codex_subscription','reasoning_effort':'high','applies_to_future_groups':False,'other_audit_disputes_waived':False,'original_audit_sha256':m.binding(old)['file_sha256'],'saved_gpt_raw_artifact_sha256':rawsha})
    monkeypatch.setattr(m,'current_input',lambda:(prompt,schema))
    path=m.create(old,auth,diag,output,[])
    return path,root

def test_exact_exception_keeps_every_other_verdict_and_revisit_flag(tmp_path,monkeypatch):
    path,root=make(tmp_path,monkeypatch); result=json.loads(path.read_text())
    assert m.validate(result)
    assert result['layers']['4']['results'][1:]==original()['layers']['4']['results'][1:]
    assert result['layers']['4']['disputed']==1
    assert result['layers']['4']['independent_judged']==2
    assert result['layers']['4']['results'][0]['revisit_required'] is True
    assert json.loads((root/'old-audit.json').read_text())==original()
    projected={'meta':result['meta'],'layers':{k:result['layers'][k] for k in ('3','4')}}
    assert m.validate(projected)

@pytest.mark.parametrize('change',['other_target','revision','two_blocked','not_filter','model_error','source_missing','wrong_judged','already_complete','duplicate'])
def test_not_exact_provider_gap_stops(change):
    a=original(); r=a['layers']['4']; row=r['results'][0]
    if change=='other_target':row['viewpoint_id']='foreign'
    elif change=='revision':row['revision_id']='foreign'
    elif change=='two_blocked':r['results'][1]['verdict']='blocked'
    elif change=='not_filter':row['reason']='quota'
    elif change=='model_error':r['model_errors']=1
    elif change=='source_missing':r['no_source_text']=1
    elif change=='wrong_judged':r['judged']=0
    elif change=='already_complete':r['complete']=True
    else:r['results'][1]['viewpoint_id']=m.TARGET
    with pytest.raises(ValueError):m.derive(a,response(),{})

@pytest.mark.parametrize('key,value',[('verdict','disputed'),('issue','other'),('reason',''),('claim_ids_in_question',[m.CLAIM])])
def test_disputed_or_unresolved_gpt_never_fills_gap(key,value):
    r=response();r[key]=value
    with pytest.raises(ValueError):m.derive(original(),r,{})

@pytest.mark.parametrize('change',['other_verdict','removed_flag','other_layer','counter','missing_proof','marker_stripped'])
def test_derived_tampering_stops(tmp_path,monkeypatch,change):
    path,_=make(tmp_path,monkeypatch);a=json.loads(path.read_text())
    if change=='other_verdict':a['layers']['4']['results'][2]['verdict']='same'
    elif change=='removed_flag':a['layers']['4']['results'][0]['revisit_required']=False
    elif change=='other_layer':a['layers']['3']['judged']=2
    elif change=='counter':a['layers']['4']['disputed']=0
    elif change=='missing_proof':a['meta'].pop(m.KEY)
    else:a['meta']={'model':'gemini-3.8-flash'}
    with pytest.raises(ValueError):m.validate(a)

@pytest.mark.parametrize('name',['authorization.json','old-audit.json','diagnostic/raw-gpt.json','diagnostic/request.json','diagnostic/result.json','diagnostic/preflight.json','owner-identical-retry/request.json'])
def test_physical_binding_drift_stops(tmp_path,monkeypatch,name):
    path,root=make(tmp_path,monkeypatch);(root/name).write_text('{}')
    with pytest.raises(ValueError):m.validate(json.loads(path.read_text()))

def test_foreign_current_input_stops(tmp_path,monkeypatch):
    make(tmp_path,monkeypatch)
    monkeypatch.setattr(m,'current_input',lambda:('changed source',{}))
    root=tmp_path/'case'
    with pytest.raises(ValueError,match='drift'):
        m.create(root/'old-audit.json',root/'authorization.json',root/'diagnostic',root/'new-output',[])

def test_normal_audit_unchanged():
    a={'meta':{'model':'gemini-3.8-flash'},'layers':{}}
    assert m.validate(a)==[]

@pytest.mark.parametrize('change',['none','group','flag','independence','path','binding'])
def test_seed_cannot_lose_revisit_disclosure(tmp_path,monkeypatch,change):
    path,_=make(tmp_path,monkeypatch);a=json.loads(path.read_text())
    row={'group_key':'p020:p020:whole','revisit_required':True,'independent_audit_complete':False,
         'temporary_audit_exception_path':a['meta'][m.KEY]['path'],'bindings':m.validate(a)}
    if change=='none':m.validate_completion_disclosure(row,a);return
    if change=='group':row['group_key']='other'
    elif change=='flag':row['revisit_required']=False
    elif change=='independence':row['independent_audit_complete']=True
    elif change=='path':row['temporary_audit_exception_path']='foreign'
    else:row['bindings']=[]
    with pytest.raises(ValueError):m.validate_completion_disclosure(row,a)
