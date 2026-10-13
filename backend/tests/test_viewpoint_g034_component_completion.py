from copy import deepcopy
import pytest
from backend.pipeline.viewpoint_g034_component_completion import CLAIM, LINK, PRAYER_LINK, CV, validate_coverage, verify_ledger
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


def data():
    claim={'claim_id':CLAIM,'revision':3,'statement':'字'*57}
    links=[]
    for lid,start,end in [(LINK,0,15),(PRAYER_LINK,15,57)]:
        links.append({'viewpoint_claim_link_id':lid,'claim_id':CLAIM,'viewpoint_id':CV,'revision':2,
            'link_type':'supports','effective_state':'active','pinned_claim_revision':3,
            'component_locator':{'canonical_spans':[{'start_char':start,'end_char':end,'exact_text':claim['statement'][start:end]}]}})
    return claim,links


def test_exact_complete_components():
    validate_coverage(*data())


@pytest.mark.parametrize('field,value',[('claim_id','foreign'),('viewpoint_id','foreign'),('revision',3),
    ('link_type','equivalent_component'),('effective_state','retired'),('pinned_claim_revision',4)])
def test_component_drift_rejected(field,value):
    claim,links=data();links[0][field]=value
    with pytest.raises(ValueError):validate_coverage(claim,links)


def test_gap_overlap_and_extra_component_rejected():
    for start in (14,16):
        claim,links=data();links[1]['component_locator']['canonical_spans'][0]['start_char']=start
        with pytest.raises(ValueError):validate_coverage(claim,links)
    claim,links=data();links.append(deepcopy(links[0]));links[-1]['viewpoint_claim_link_id']='extra'
    with pytest.raises(ValueError):validate_coverage(claim,links)


def ledger():
    ids=[CLAIM]+[f'CL-{i}' for i in range(9)]
    rows=[{'claim_id':cid,'resolution_status':'resolved','apply_status':'applied'} for cid in ids]
    rows[0].update(resolution_status='deferred',components=[
        {'disposition':'new_viewpoint'},{'disposition':'support_existing'},
        {'disposition':'deferred','spans':[{'start_char':51,'end_char':53,'exact_text':'禱告'}]}])
    obj={'status':'incomplete','findings':[],'blocked_claims':[],'excluded_claims':[],
         'unresolved_claim_ids':[],'claim_dispositions':rows}
    obj['artifact_sha256']=sha256_json(obj)
    return obj,ids


def test_only_original_single_prayer_deferral_accepted():
    obj,ids=ledger();assert verify_ledger(obj,ids)['claim_id']==CLAIM


@pytest.mark.parametrize('mutation',['extra','unapplied','deleted','findings','wrong_prayer','sha'])
def test_other_incomplete_states_are_never_promoted(mutation):
    obj,ids=ledger()
    if mutation=='extra':obj['claim_dispositions'][1]['resolution_status']='deferred'
    elif mutation=='unapplied':obj['claim_dispositions'][1]['apply_status']='not_applied'
    elif mutation=='deleted':obj['claim_dispositions'].pop()
    elif mutation=='findings':obj['findings']=['new failure']
    elif mutation=='wrong_prayer':obj['claim_dispositions'][0]['components'][2]['spans'][0]['start_char']=50
    if mutation!='sha':obj['artifact_sha256']=sha256_json({k:v for k,v in obj.items() if k!='artifact_sha256'})
    else:obj['artifact_sha256']='bad'
    with pytest.raises(ValueError):verify_ledger(obj,ids)
