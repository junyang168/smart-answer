from copy import deepcopy
import json,os
from pathlib import Path
import pytest
from backend.pipeline import cvp_human_retained_audit_resolution as resolution
from backend.pipeline.cvp_sql_flow import binding
from backend.api.canonical_repository.postgres_store import sha256_json
from backend.pipeline.viewpoint_audit_owner_resolution import audit_semantic_sha


@pytest.fixture
def case(tmp_path):
    def save(name,value):
        p=tmp_path/name;p.write_text(json.dumps(value));return binding(p)
    pins=[{'collection':col,'object_id':oid,'pin':[2,sha256_json(oid),False]} for col,oid in
        [('viewpoint_claim_links','L'),('viewpoint_identity_decisions','D'),('canonical_viewpoints','V'),('viewpoint_revisions','R'),('claims','C')]]
    boundary=save('boundary.json',{'current_read_pins':[{'collection':p['collection'],'object_id':p['object_id'],
        'revision':p['pin'][0],'content_sha256':p['pin'][1]} for p in pins if p['collection']!='claims'],
        'prior_current_approvals':[{'link_id':'L','claim_id':'C','original_decision':{'resolved_viewpoint_id':'V','identity_decision_id':'D'}}]})
    human=save('human.json',{'group_id':'run','human_exact_disposition':True,
        'decision':'retain_existing_contextual_equivalence_unchanged','items':[{'link_id':'L','viewpoint_revision_id':'R'}]})
    authority=save('scope.json',{'human_disposition':human,'retained_boundary':boundary,'human_retained_link_ids':['L'],
        'retained_link_pins':[{'object_id':'L','revision':2,'content_sha256':sha256_json('L')}]})
    receipt=save('claim.json',{'status':'claim_layer_audited_cvp_pending','read_pins':pins})
    audit={'meta':{'model':'gemini-3.8-flash','registry_read_scope':{'read_pins':pins}},'layers':{
        '3':{'complete':True,'judged':1,'disputed':0,'results':[{'claim_id':'C','verdict':'supported'}]},
        '4':{'complete':True,'judged':1,'disputed':1,'results':[{'viewpoint_id':'V','verdict':'disputed','issue':'over_merge','claim_ids_in_question':['C']}]} }}
    audit_binding=save('audit.json',audit)
    body={'schema_version':resolution.VERSION,'run_id':'run','audit':audit_binding,'human_scope_authority':authority,
        'claim_layer_completion':receipt,'audit_semantic_sha256':audit_semantic_sha(audit),
        'human_item_ids':['4:V:C'],'source_rejected_parts':[],'master_data_mutations':0,'new_model_calls':0,
        'independent_audit_verdicts_unchanged':True,'original_gpt_verdicts_unchanged':True}
    return locals()


def seal(body):body['artifact_sha256']=sha256_json({k:v for k,v in body.items() if k!='artifact_sha256'})


def test_explicit_human_retention_preserves_disputed_not_fabricated_pass(case):
    f=case;before=deepcopy(f['audit']);seal(f['body'])
    assert resolution.verify_resolution(f['audit'],f['body'])=={'3':set(),'4':{'V'}}
    assert f['audit']==before and f['audit']['layers']['4']['results'][0]['verdict']=='disputed'


@pytest.mark.parametrize('fault',[None,'missing','duplicate','foreign','retired','claim-drift'])
def test_normal_group_does_not_require_claim_first_receipt(case,fault):
    f=case;b=f['body'];b.pop('claim_layer_completion')
    path=f['tmp_path']/'scope.json';scope=json.loads(path.read_text())
    rows=deepcopy([p for p in f['pins'] if p['collection']=='claims'])
    if fault=='missing':rows=[]
    elif fault=='duplicate':rows*=2
    elif fault=='foreign':rows[0]['object_id']='FOREIGN'
    elif fault=='retired':rows[0]['pin'][2]=True
    elif fault=='claim-drift':rows[0]['pin'][0]+=1
    scope['human_retained_claim_pins']=rows;path.write_text(json.dumps(scope))
    b['human_scope_authority']=binding(path);seal(b)
    if fault:
        with pytest.raises(ValueError):resolution.verify_resolution(f['audit'],b)
    else:assert resolution.verify_resolution(f['audit'],b)=={'3':set(),'4':{'V'}}


def test_mixed_domains_rejoin_all_items_without_repeating_human_judgment(case,monkeypatch):
    from backend.pipeline import cvp_exact_human_audit_retention as exact
    f=case;a=f['audit'];b=f['body']
    a['layers']['4']['results'].append({'viewpoint_id':'OTHER','verdict':'disputed','issue':'different_proposition','claim_ids_in_question':['C2']})
    a['layers']['4']['disputed']=2
    path=f['tmp_path']/'audit.json';path.write_text(json.dumps(a));b['audit']=binding(path)
    b['audit_semantic_sha256']=audit_semantic_sha(a)
    packet={'items':[{'item_id':'4:OTHER:C2'}]};packet['artifact_sha256']=sha256_json(packet)
    b.update(exact_audit_packet=packet,exact_audit_human_items=[{'item_id':'4:OTHER:C2','direction':{}}])
    checks=[]
    monkeypatch.setattr(exact,'verify_human_entry',lambda audit,items,h:checks.append(h) or h['item_id'])
    seal(b)
    assert resolution.verify_resolution(a,b)=={'3':set(),'4':{'V','OTHER'}}
    assert len(checks)==1
    b['exact_audit_human_items']=[];seal(b)
    with pytest.raises(ValueError):resolution.verify_resolution(a,b)


@pytest.mark.parametrize('fault',[None,'new-pin','missing','retired','not-judged','foreign','proof'])
def test_narrow_action_readset_requires_exact_saved_full_claim_audit(case,monkeypatch,fault):
    from backend.pipeline import claim_first_management,viewpoint_content_blocked_fallback
    f=case;prior=deepcopy(f['audit']);receipt={'status':'claim_layer_audited_cvp_pending',
        'read_pins':f['pins'][:-1],'claim_ids':['C'],'source_proofs':['verified']}
    if fault=='missing':prior['meta']['registry_read_scope']['read_pins']=f['pins'][:-1]
    elif fault=='retired':prior['meta']['registry_read_scope']['read_pins'][-1]['pin'][2]=True
    elif fault=='not-judged':prior['layers']['3']['results']=[]
    elif fault=='foreign':receipt['claim_ids']=[]
    elif fault=='proof':receipt['source_proofs']=['foreign']
    path=f['tmp_path']/'prior-audit.json';path.write_text(json.dumps(prior));receipt['audit']=binding(path)
    checks=[]
    monkeypatch.setattr(claim_first_management,'validate_claim_audit_report',lambda a,ids:checks.append(('full',a,ids)))
    monkeypatch.setattr(viewpoint_content_blocked_fallback,'verify_saved_full_audit',lambda *a,**k:['verified'])
    cp=f['tmp_path']/'narrow-receipt.json';cp.write_text(json.dumps(receipt));f['body']['claim_layer_completion']=binding(cp)
    if fault=='new-pin':
        f['audit']['meta']['registry_read_scope']['read_pins'][-1]['pin'][0]=3
        (f['tmp_path']/'audit.json').write_text(json.dumps(f['audit']));f['body']['audit']=binding(f['tmp_path']/'audit.json')
        f['body']['audit_semantic_sha256']=audit_semantic_sha(f['audit'])
    seal(f['body'])
    if fault:
        with pytest.raises(ValueError):resolution.verify_resolution(f['audit'],f['body'])
    else:assert resolution.verify_resolution(f['audit'],f['body'])=={'3':set(),'4':{'V'}}
    assert checks


@pytest.mark.parametrize('human',[set(),{'4:V:C'},{'4:V:C','4:OTHER:C'}])
def test_partition_routes_only_named_human_targets_not_reuse_or_model(monkeypatch,human):
    packet={'schema_version':'fixture','items':[{'item_id':'4:V:C'},{'item_id':'4:OTHER:C'}]}
    packet['artifact_sha256']=sha256_json(packet);before=deepcopy(packet)
    monkeypatch.setattr(resolution,'human_items',lambda *args:(human,None))
    actual,accepted=resolution.partition_packet({},packet,{},None)
    assert accepted==human
    assert {i['item_id'] for i in actual['items']}=={'4:V:C','4:OTHER:C'}-human
    assert actual['artifact_sha256']==sha256_json({k:v for k,v in actual.items() if k!='artifact_sha256'})
    assert packet==before


@pytest.mark.parametrize('fault',['extra-claim','extra-view','foreign-pair','new-type','pin','claim-pin','group','mutation','call','gpt-decision','gemini-decision','empty','layer3'])
def test_human_decision_is_exact_membership_not_a_future_audit_waiver(case,fault):
    f=case;a=f['audit'];b=f['body']
    if fault=='extra-claim':a['layers']['4']['results'][0]['claim_ids_in_question']+=['foreign']
    elif fault=='extra-view':a['layers']['4']['results']+=[{'viewpoint_id':'foreign','verdict':'disputed','issue':'over_merge','claim_ids_in_question':['C']}]
    elif fault=='foreign-pair':b['human_item_ids']=['4:V:foreign']
    elif fault=='new-type':a['layers']['4']['results'][0]['issue']='scope_mismatch'
    elif fault=='pin':a['meta']['registry_read_scope']['read_pins'][0]['pin'][0]=3
    elif fault=='claim-pin':
        # Audit carries a new Claim generation; its original receipt does NOT.
        a['meta']['registry_read_scope']['read_pins']=deepcopy(a['meta']['registry_read_scope']['read_pins']);a['meta']['registry_read_scope']['read_pins'][-1]['pin'][0]=3
    elif fault=='group':b['run_id']='foreign'
    elif fault=='mutation':b['master_data_mutations']=1
    elif fault=='call':b['new_model_calls']=1
    elif fault=='gpt-decision':b['original_gpt_verdicts_unchanged']=False
    elif fault=='gemini-decision':b['independent_audit_verdicts_unchanged']=False
    elif fault=='empty':b['human_item_ids']=[]
    elif fault=='layer3':a['layers']['3']['results'][0]['verdict']='disputed';a['layers']['3']['disputed']=1
    f['tmp_path'].joinpath('audit.json').write_text(json.dumps(a));b['audit']=binding(f['tmp_path']/'audit.json');b['audit_semantic_sha256']=audit_semantic_sha(a);seal(b)
    with pytest.raises(ValueError):resolution.verify_resolution(a,b)


@pytest.mark.skipif(not os.environ.get('WKP_SAVED_HUMAN_AUDIT_ROOT'),reason='explicit actual saved resolution read-only')
def test_actual_saved_human_resolution_missing_narrow_claim_pins():
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    root=Path(os.environ['WKP_SAVED_HUMAN_AUDIT_ROOT'])
    audit=read_artifact(root/'audit.json');before=deepcopy(audit)
    result=resolution.verify_resolution(audit,read_artifact(root/'human-retained-source-final-resolution.json'))
    assert result['3']==set() and len(result['4'])==3
    assert audit==before and audit['layers']['4']['disputed']==3
    print(json.dumps({'complete_saved_resolution':True,'resolved_cvp_findings':len(result['4']),
        'original_disputed_unchanged':True,'new_model_calls':0,'registry_writes':0}))
