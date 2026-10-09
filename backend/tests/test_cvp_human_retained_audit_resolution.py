from copy import deepcopy
import json
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
