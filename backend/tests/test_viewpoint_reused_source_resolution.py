from copy import deepcopy
import json
import pytest
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import write_immutable,read_artifact
from backend.pipeline.viewpoint_audit_owner_resolution import binding,validate_resolution
from backend.pipeline import viewpoint_reused_source_resolution as m

def seal(body):return body|{'artifact_sha256':sha256_json(body)}

def fixture(tmp_path,monkeypatch):
    finding={'viewpoint_id':'v','revision_id':'r','verdict':'disputed','reason':'exact original finding','claim_ids_in_question':['c']}
    audit={'meta':{'model':'gemini-3.8-flash'},'layers':{
        '3':{'complete':True,'judged':1,'disputed':0,'results':[{'claim_id':'c','verdict':'supported'}]},
        '4':{'complete':True,'judged':1,'disputed':1,'results':[finding]}}}
    audit_path=tmp_path/'audit.json';write_immutable(audit_path,audit)
    source=tmp_path/'source';source.write_text('own original quote')
    item={'item_id':'4:v:c','audit_finding':finding,'claim':{'claim_id':'c'},'connections':[{'link_id':'l'}],
          'sources':[{'source_id':'s','path':str(source),'file_sha256':binding(source)['file_sha256'],
                      'paragraphs':[{'paragraph_key':'S0001','text':'own original quote'}]}]}
    second=deepcopy(item);second['item_id']='4:upheld:d'
    old_audit=tmp_path/'old-audit';old_audit.write_text('immutable old audit')
    prior=tmp_path/'prior';packet=seal({'items':[item,second],'audit_binding':binding(old_audit)})
    write_immutable(prior/'packet.json',packet)
    answers=[{'item_id':i['item_id'],'verdict':'audit_objection_rejected' if n==0 else 'audit_objection_upheld',
              'source_id':'s','paragraph_key':'S0001','exact_quote':'own original quote','reason':'responds exactly'} for n,i in enumerate(packet['items'])]
    response={'findings':answers}
    write_immutable(prior/'raw-gpt.json',seal({'model_id':'gpt-6.1-sol','backend':'codex_subscription',
        'request_payload_sha256':sha256_json(packet),'response_sha256':sha256_json(response),'response':response}))
    write_immutable(prior/'gpt-final-handoff.json',seal({'packet_sha256':packet['artifact_sha256'],'maximum_model_calls':1,
        'actions':[{'item_id':a['item_id'],'verdict':a['verdict'],'gpt':a} for a in answers]}))
    current=seal({'items':[item],'audit_binding':binding(audit_path)})
    monkeypatch.setattr('backend.pipeline.viewpoint_audit_source_adjudication.build_packet',lambda *args:current)
    path=tmp_path/'out/resolution.json';m.create(audit_path,prior,path,object())
    return audit,path,prior,current

def test_exact_unchanged_rejected_subset_no_new_model_calls(tmp_path,monkeypatch):
    audit,path,prior,_=fixture(tmp_path,monkeypatch)
    assert m.verify(audit,read_artifact(path))=={'3':set(),'4':{'v'}}
    assert validate_resolution(audit,path)=={'v'}
    assert read_artifact(prior/'gpt-final-handoff.json')['actions'][1]['verdict']=='audit_objection_upheld'
    from backend.pipeline.viewpoint_cvp_campaign_runner import validate_audit
    validate_audit(audit,['c'],['v'],owner_resolution_path=path)

@pytest.mark.parametrize('change',['original_raw','original_audit','source','current_finding','metadata','new_calls'])
def test_no_proof_drift_or_forged_reuse(change,tmp_path,monkeypatch):
    audit,path,prior,_=fixture(tmp_path,monkeypatch);r=read_artifact(path)
    if change=='original_raw':(prior/'raw-gpt.json').write_text('{}')
    elif change=='original_audit':(tmp_path/'old-audit').write_text('changed')
    elif change=='source':(tmp_path/'source').write_text('changed')
    elif change=='current_finding':audit['layers']['4']['results'][0]['reason']='changed'
    elif change=='metadata':r['master_data_mutations']=1
    else:r['new_model_calls']=1
    with pytest.raises(ValueError):m.verify(audit,r)

def test_changed_connection_or_upheld_not_reusable(tmp_path,monkeypatch):
    audit,path,prior,current=fixture(tmp_path,monkeypatch)
    changed=deepcopy(current);changed['items'][0]['connections'][0]['link_id']='changed'
    changed=seal({k:v for k,v in changed.items() if k!='artifact_sha256'})
    monkeypatch.setattr('backend.pipeline.viewpoint_audit_source_adjudication.build_packet',lambda *args:changed)
    with pytest.raises(ValueError,match='changed/upheld'):m.create(tmp_path/'audit.json',prior,tmp_path/'other/resolution.json',object())
