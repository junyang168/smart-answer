from copy import deepcopy
import json
from pathlib import Path

import pytest
from backend.pipeline import viewpoint_content_blocked_fallback as m
from backend.pipeline.viewpoint_resolution_runtime import write_immutable

def fixture(tmp_path, monkeypatch, layer='4', response=None):
    response=response or {'verdict':'same','issue':'none','reason':'own full source supports identity','claim_ids_in_question':[]}
    if layer=='3': response={'verdict':'supported','issue':'none','reason':'own evidence supports claim','quote':'source'}
    schema={'type':'object','properties':{k:{'type':'string'} for k in response},'required':['verdict','issue','reason']}
    source=tmp_path/'source.json'; source.write_text('physical full source')
    receipt=tmp_path/'provider.json'
    write_immutable(receipt,{'model':'gemini-3.8-flash','provider_response':{'promptFeedback':{'blockReason':'PROHIBITED_CONTENT'}},'prompt':'full prompt','schema':schema})
    key='claim_id' if layer=='3' else 'viewpoint_id'
    old={'meta':{'model':'gemini-3.8-flash'},'layers':{
        '1':{'findings':[]},'2':{'findings':[]},
        '3':{'complete':True,'judged':1,'blocked':0,'disputed':0,'results':[{'claim_id':'c','verdict':'supported'}]},
        '4':{'complete':True,'judged':1,'blocked':0,'disputed':0,'results':[{'viewpoint_id':'v','verdict':'same'}]}}}
    old['layers'][layer].update(judged=0,blocked=1,results=[{key:'any-other-identity','revision_id':'any-version',
        'verdict':'blocked','reason':'PROHIBITED_CONTENT','provider_content_block':m.binding(receipt)}])
    path=tmp_path/'audit.json'; write_immutable(path,old)
    calls=[]
    class Client:
        def __init__(self,**kwargs):
            self.backend='codex_subscription';self.model='gpt-6.1-sol';self.reasoning_effort='high'
            self.last_raw_response=None
        def _verify_chatgpt_login(self):pass
        def generate_json(self,system,prompt,schema):
            calls.append(prompt);self.last_raw_response={'last_message':json.dumps(response),
                'stderr':'model: gpt-6.1-sol\nreasoning effort: high\n'+prompt}
            return response
    monkeypatch.setattr('backend.pipeline.codex_subscription_client.CodexSubscriptionClient',Client)
    monkeypatch.setattr(m,'CACHE',tmp_path/'shared-cache')
    monkeypatch.setattr(m,'capture',lambda l,i:('full prompt',schema,[m.binding(source)]))
    return path,old,calls,receipt

@pytest.mark.parametrize('layer',['3','4'])
def test_condition_not_identity_and_exact_cross_root_cache(tmp_path,monkeypatch,layer):
    path,old,calls,_=fixture(tmp_path,monkeypatch,layer)
    result=m.apply(path,tmp_path/'out'); assessment=json.loads(result.read_text())
    assert len(calls)==1 and m.validate(assessment)
    assert json.loads(path.read_text())==old
    assert assessment['layers'][layer]['results'][0]['revisit_required'] is True
    assert assessment['layers'][layer]['results'][0]['independent'] is False
    m.apply(path,tmp_path/'another-root'); assert len(calls)==1
    from backend.pipeline.viewpoint_g035_temporary_audit import validate_completion_disclosure
    row={'group_key':'future-group','revisit_required':True,'independent_audit_complete':False,
         'temporary_audit_exception_path':assessment['meta'][m.KEY]['path'],'bindings':m.validate(assessment)}
    validate_completion_disclosure(row,assessment)
    row['revisit_required']=False
    with pytest.raises(ValueError):validate_completion_disclosure(row,assessment)

@pytest.mark.parametrize('reason',['RATE_LIMIT','NETWORK_ERROR','OTHER','missing_source','DISPUTED',None])
def test_non_content_failures_never_trigger(reason):
    with pytest.raises(ValueError):m.content_block({'model':'gemini-3.8-flash','provider_response':{'promptFeedback':{'blockReason':reason}}})

def test_candidate_safety_is_actual_content_block():
    m.content_block({'model':'gemini-3.8-flash','provider_response':{'candidates':[{'finishReason':'SAFETY'}]}})


def test_seed_layer_projection_preserves_exact_semantics(tmp_path,monkeypatch):
    path,old,_,_=fixture(tmp_path,monkeypatch)
    old['schema_version']='library-audit.v1'
    path.write_text(json.dumps(old))
    full=json.loads(m.apply(path,tmp_path/'out').read_text())
    partial={'meta':full['meta'],'layers':{k:full['layers'][k] for k in ('3','4')}}
    assert m.validate(partial)
    changed=deepcopy(partial)
    changed['layers']['3']['results'][0]['verdict']='disputed'
    with pytest.raises(ValueError):m.validate(changed)
    changed=deepcopy(partial);changed['schema_version']='foreign-schema'
    with pytest.raises(ValueError):m.validate(changed)

@pytest.mark.parametrize('change',['missing_receipt','source_error','model_error','incomplete','prompt_drift','other_provider'])
def test_unsafe_input_stops_before_subscription(tmp_path,monkeypatch,change):
    path,a,calls,receipt=fixture(tmp_path,monkeypatch)
    if change=='missing_receipt':a['layers']['4']['results'][0].pop('provider_content_block')
    elif change=='source_error':a['layers']['3']['no_source_text']=1
    elif change=='model_error':a['layers']['3']['model_errors']=1
    elif change=='incomplete':a['layers']['3']['complete']=False
    elif change=='prompt_drift':monkeypatch.setattr(m,'capture',lambda l,i:('drift',{},[]))
    else:
        r=json.loads(receipt.read_text());r['model']='another-model';receipt.write_text(json.dumps(r))
        a['layers']['4']['results'][0]['provider_content_block']=m.binding(receipt)
    path.write_text(json.dumps(a))
    with pytest.raises(ValueError):m.apply(path,tmp_path/'out')
    assert not calls

def test_gpt_dispute_is_not_forged_pass(tmp_path,monkeypatch):
    response={'verdict':'disputed','issue':'over_merge','reason':'actual content issue','claim_ids_in_question':['c']}
    path,_,_,_=fixture(tmp_path,monkeypatch,response=response)
    a=json.loads(m.apply(path,tmp_path/'out').read_text())
    assert a['layers']['4']['disputed']==1 and a['layers']['4']['results'][0]['verdict']=='disputed'

@pytest.mark.parametrize('change',['flag','verdict','counter','raw','source','provider'])
def test_physical_and_derived_drift_rejected(tmp_path,monkeypatch,change):
    path,_,_,_=fixture(tmp_path,monkeypatch)
    a=json.loads(m.apply(path,tmp_path/'out').read_text())
    manifest=json.loads(Path(a['meta'][m.KEY]['path']).read_text())
    proof=json.loads(Path(manifest['proof_bindings'][0]['path']).read_text())
    if change=='flag':a['layers']['4']['results'][0]['revisit_required']=False
    elif change=='verdict':a['layers']['3']['results'][0]['verdict']='disputed'
    elif change=='counter':a['layers']['4']['judged']=12
    else:
        b=proof['source_bindings'][0] if change=='source' else proof['raw_binding'] if change=='raw' else proof['provider_binding']
        Path(b['path']).write_text('{}')
    with pytest.raises(ValueError):m.validate(a)

def test_failed_reserved_call_not_retried_in_new_root(tmp_path,monkeypatch):
    path,_,calls,_=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(m,'capture',lambda l,i:('full prompt',json.loads((tmp_path/'provider.json').read_text())['schema'],[]))
    class Failing:
        last_raw_response=None
        def __init__(self,**kwargs):pass
        def _verify_chatgpt_login(self):pass
        def generate_json(self,*args):calls.append('failed');raise RuntimeError('quota')
    monkeypatch.setattr('backend.pipeline.codex_subscription_client.CodexSubscriptionClient',Failing)
    with pytest.raises(RuntimeError):m.apply(path,tmp_path/'out')
    with pytest.raises(ValueError,match='already attempted'):m.apply(path,tmp_path/'other-root')
    assert calls==['failed']

def test_standalone_reader_import_and_full_context_no_trimming():
    import importlib.util
    import subprocess
    import sys
    root=Path(__file__).resolve().parents[2]
    help_result=subprocess.run([sys.executable,str(root/'scripts/audit-library.py'),'--help'],cwd='/tmp',capture_output=True,text=True)
    assert help_result.returncode==0 and '--preserve-full-context' in help_result.stdout
    spec=importlib.util.spec_from_file_location('full_context_test_reader',root/'scripts/audit-library.py')
    reader=importlib.util.module_from_spec(spec);sys.modules[spec.name]=reader;spec.loader.exec_module(reader)
    class Blocked:
        preserve_full_context=True
        def __init__(self):self.calls=[]
        def judge(self,prompt,schema):self.calls.append(prompt);return {'verdict':'blocked'}
    model=Blocked();answer,used=reader._judge_trimming_on_block(model,lambda ps:'|'.join(ps),['all','full','original'],{})
    assert answer['verdict']=='blocked' and used==3 and model.calls==['all|full|original']

@pytest.mark.parametrize('change',['none','sha','prompt','model','shape','quota'])
def test_legacy_exact_transport_cache_not_fake_provider_raw(tmp_path,change):
    import hashlib
    prompt='complete own source request';model='gemini-3.8-flash'
    path=tmp_path/(hashlib.sha256(f'{model}\n{prompt}'.encode()).hexdigest()+'.json')
    verdict={'verdict':'blocked','issue':'other','reason':'審計模型的安全過濾器擋下了這一條（PROHIBITED_CONTENT），沒有判讀。'}
    write_immutable(path,verdict)
    receipt={'model':model,'archive_kind':'legacy_exact_model_prompt_cache_v1',
        'prompt':prompt,'schema':{},'archive_binding':m.binding(path),'recorded_verdict':verdict}
    if change=='none':m.content_block(receipt);assert 'provider_response' not in receipt;return
    if change=='sha':path.write_text('{}')
    elif change=='prompt':receipt['prompt']='different full source'
    elif change=='model':receipt['model']='other-model'
    elif change=='shape':receipt['recorded_verdict']['verdict']='same'
    else:receipt['recorded_verdict']['reason']='quota'
    with pytest.raises(ValueError):m.content_block(receipt)

@pytest.mark.parametrize('change',['none','cache','prompt','trimmed'])
def test_saved_audit_full_current_request_cache_reconstruction(tmp_path,monkeypatch,change):
    import hashlib
    path,old,_,_=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr('dotenv.dotenv_values',lambda *args:{'DATA_BASE_DIR':str(tmp_path)})
    cache=tmp_path/'wang-knowledge-platform/staging/reports/library-audit/.judgements'
    prompts={}
    for layer,key in (('3','claim_id'),('4','viewpoint_id')):
        for row in old['layers'][layer]['results']:
            prompt=f'full prompt {layer} {row[key]}';prompts[layer,row[key]]=prompt
            p=cache/(hashlib.sha256(f'gemini-3.8-flash\n{prompt}'.encode()).hexdigest()+'.json')
            write_immutable(p,{k:row[k] for k in ('verdict','reason') if k in row})
            if change=='cache' and layer=='4':p.write_text('{"verdict":"same"}')
    if change=='trimmed':old['layers']['3']['results'][0]['paragraphs_dropped_to_pass_filter']=1;path.write_text(json.dumps(old))
    monkeypatch.setattr(m,'capture',lambda l,i:(prompts[l,i]+('changed' if change=='prompt' else ''),{},[]))
    if change=='none':assert m.verify_saved_full_audit(path);return
    with pytest.raises(ValueError):m.verify_saved_full_audit(path)
