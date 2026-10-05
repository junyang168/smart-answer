from copy import deepcopy
import pytest
from backend.pipeline.viewpoint_g034_theme_support_repair import postimage, LINK, CLAIM, CV

def old_link():
    return {'viewpoint_claim_link_id':LINK,'claim_id':CLAIM,'viewpoint_id':CV,
        'revision':1,'link_type':'equivalent_component','effective_state':'active','decision_id':'VID-old',
        'review_status':'system_approved','pinned_claim_revision':3,'validated_against_viewpoint_revision_id':'CVR-old',
        'component_locator':{'claim_sha256':'bound','canonical_spans':[{'start_char':0,'end_char':15,
            'exact_text':'登山寶訓以門徒的義为主题展开：'}],'statement_component':'登山寶訓以門徒的義为主题展开：'},
        'evidence_bindings':[{'evidence_step_id':'E1','source_fragment_id':'F1'}]}

def test_only_type_revision_and_fresh_authority_change():
    old=old_link();before=deepcopy(old);new=postimage(old,[])
    assert old==before
    assert new['link_type']=='supports' and new['revision']==2
    assert new['component_locator']==old['component_locator']
    assert {k:v for k,v in new.items() if k not in {'revision','link_type','decision_id'}}=={k:v for k,v in old.items() if k not in {'revision','link_type','decision_id'}}

def test_different_component_existing_support_is_preserved():
    old=old_link();other=deepcopy(old);other.update(viewpoint_claim_link_id='VCL-other',link_type='supports')
    other['component_locator']={'claim_sha256':'bound','canonical_spans':[{'start_char':15,'end_char':16,'exact_text':'字'}],'statement_component':'字'}
    before=deepcopy(other);assert postimage(old,[other])['link_type']=='supports';assert other==before

def test_duplicate_exact_component_is_rejected():
    other=old_link();other.update(viewpoint_claim_link_id='VCL-other',link_type='supports')
    with pytest.raises(ValueError,match='duplicate'):postimage(old_link(),[other])

@pytest.mark.parametrize('field,value',[('claim_id','foreign'),('viewpoint_id','foreign'),
    ('viewpoint_claim_link_id','foreign'),('revision',2),('effective_state','retired'),('link_type','supports')])
def test_scope_drift_rejected(field,value):
    old=old_link();old[field]=value
    with pytest.raises(ValueError):postimage(old,[])

def test_locator_drift_rejected():
    old=old_link();old['component_locator']['canonical_spans'][0]['end_char']=16
    with pytest.raises(ValueError,match='component drift'):postimage(old,[])
