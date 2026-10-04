from copy import deepcopy
import pytest
from backend.pipeline.viewpoint_g033_identity_repair import LINKS, RELATIONS, validate_proposal, package_for, VIEWPOINT, OLD_REVISION, POLICY

def case():
    payload={'before_links':[], 'claims':[]}
    links=[]
    for n,lid in enumerate(sorted(LINKS)):
        cid='C'+str(n); text='門徒要發光。'
        payload['before_links'].append({'viewpoint_claim_link_id':lid,'claim_id':cid})
        payload['claims'].append({'claim':{'claim_id':cid,'statement':text},'claim_semantic_sha256':'sha'+cid})
        links.append({'link_id':lid,'link_type':'equivalent_component','reason':'own Claim',
            'component_locator':{'claim_sha256':'sha'+cid,'statement_component':'發光','canonical_spans':[{'start_char':3,'end_char':5,'exact_text':'發光'}]}})
    p={'needs_new_scope':False,'reason':'remove unsupported identity','core_proposition':'門徒要發光。',
        'proposition_signature':{'subject':'門徒','predicate':'要','object':'發光','polarity':'affirmed','modality':'asserted'},
        'links':links,'relations':[{'relation_id':rid,'relation_type':'applies','reason':'actual application'} for rid in sorted(RELATIONS)]}
    return payload,p

def test_exact_population_and_own_spans():
    payload,p=case(); before=deepcopy(payload)
    assert validate_proposal(payload,p)['core_proposition']==p['core_proposition']
    assert payload==before

@pytest.mark.parametrize('change',['missing_link','duplicate_link','foreign_link','missing_relation','foreign_relation','new_scope','orphan','wrong_sha','wrong_span','full_locator','empty_reason','extra_claim_edit','extra_scope'])
def test_scope_and_drift_refused(change):
    payload,p=case()
    if change=='missing_link': p['links'].pop()
    if change=='duplicate_link': p['links'][1]=deepcopy(p['links'][0])
    if change=='foreign_link': p['links'][0]['link_id']='foreign'
    if change=='missing_relation': p['relations'].pop()
    if change=='foreign_relation': p['relations'][0]['relation_id']='foreign'
    if change=='new_scope': p['needs_new_scope']=True
    if change=='orphan':
        for i in p['links']: i.update(link_type='supports',component_locator=None)
    if change=='wrong_sha': p['links'][0]['component_locator']['claim_sha256']='foreign'
    if change=='wrong_span': p['links'][0]['component_locator']['canonical_spans'][0].update(start_char=0,end_char=2)
    if change=='full_locator': p['links'][0]['link_type']='equivalent_full'
    if change=='empty_reason': p['relations'][0]['reason']=''
    if change=='extra_claim_edit': p['links'][0]['claim_statement']='rewrite'
    if change=='extra_scope': p['scope']={}
    with pytest.raises(ValueError): validate_proposal(payload,p)

def test_new_revision_preserves_old_identity_and_exact_semantic_postimage(tmp_path):
    import json
    payload,p=case(); targets=[]; states={}
    for old,chosen in zip(payload['before_links'],p['links'],strict=True):
        old.update(revision=1,viewpoint_id=VIEWPOINT,decision_id='D'+old['claim_id'],evidence_bindings=['same'])
        after=deepcopy(old); after.update(revision=2,validated_against_viewpoint_revision_id='NEW',link_type=chosen['link_type'],component_locator=chosen['component_locator'])
        targets.append({'before_link':old,'after_link':after,'reason':chosen['reason']})
        states['viewpoint_identity_decisions',old['decision_id']]={'identity_candidate_id':'I'+old['claim_id']}
        states['viewpoint_identity_candidates','I'+old['claim_id']]={'seed_relation_ids':[]}
    packet={'policy':{'policy_id':POLICY},'targets':targets,'after_revision':{'viewpoint_revision_id':'NEW','revision':1,'revision_number':1,'supersedes_revision_id':OLD_REVISION,'proposition_signature':p['proposition_signature']},
        'after_relations':[{'viewpoint_relation_id':'REL','reason':'reviewed'}], 'context':{'before_viewpoint':{'viewpoint_id':VIEWPOINT,'revision':1,'current_revision_id':OLD_REVISION}}}
    for label in ('gpt','opus'): (tmp_path/f'raw-{label}.json').write_text(json.dumps({'artifact_sha256':label}))
    before=deepcopy(packet); pkg=package_for(states,packet,{'gpt':{},'opus':{}},tmp_path)
    assert packet==before
    assert set(pkg)=={'package_id','viewpoint_claim_links','viewpoint_identity_candidates','viewpoint_identity_decisions','viewpoint_revisions','canonical_viewpoints','viewpoint_relations'}
    assert pkg['viewpoint_revisions'][0]['supersedes_revision_id']==OLD_REVISION
    assert pkg['canonical_viewpoints'][0]['revision']==2
    for link,target,decision in zip(pkg['viewpoint_claim_links'],targets,pkg['viewpoint_identity_decisions'],strict=True):
        assert {k:v for k,v in link.items() if k!='decision_id'}=={k:v for k,v in target['after_link'].items() if k!='decision_id'}
        assert decision['claim_link_decisions'][0]['link_type']==link['link_type']
        assert decision['approval_basis']=='dual_model_consensus'
