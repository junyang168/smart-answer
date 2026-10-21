from copy import deepcopy
import pytest
from backend.pipeline.viewpoint_g034_prayer_link_review import postimage, LINK, CLAIM, EVIDENCE

def fixture():
    text='登山寶訓以門徒的義为主题展开：五章十七至四十八節处理門徒与舊約及胜过文士和法利賽人的義，随后讨论施捨与禱告等实践。'
    old={'viewpoint_claim_link_id':LINK,'claim_id':CLAIM,'viewpoint_id':'CV-x',
        'validated_against_viewpoint_revision_id':'CVR-x','pinned_claim_revision':3,
        'revision':1,'link_type':'supports','decision_id':'VID-old','review_status':'system_approved',
        'component_locator':{'claim_sha256':'bound','statement_component':text[15:50]+text[53:57],
        'canonical_spans':[{'start_char':15,'end_char':50,'exact_text':text[15:50]},
            {'start_char':53,'end_char':57,'exact_text':text[53:57]}]},'evidence_bindings':[]}
    return old,{'claim_id':CLAIM,'statement':text},{'evidence_step_id':EVIDENCE,
        'support_eligibility':'eligible_with_label','editorial_annotations':[{'attribution':'editorial'}],
        'source_fragment_ids':['F1']}

def test_exact_prayer_component_added_with_label_and_fresh_pending_authority():
    old,c,e=fixture(); original=deepcopy(old); new=postimage(old,c,e)
    assert old==original
    assert new['link_type']=='supports' and new['revision']==2
    assert new['component_locator']['statement_component']==c['statement'][15:57]
    assert new['decision_id']!=old['decision_id']
    assert new['evidence_bindings']==[{'evidence_step_id':EVIDENCE,'source_fragment_id':'F1'}]

@pytest.mark.parametrize('mode',['foreign_link','equivalent','unreviewed','label_removed','claim_drift','locator_drift'])
def test_scope_or_evidence_drift_rejected(mode):
    old,c,e=fixture()
    if mode=='foreign_link': old['viewpoint_claim_link_id']='other'
    if mode=='equivalent': old['link_type']='equivalent_component'
    if mode=='unreviewed': e['support_eligibility']='withheld_unreviewed'
    if mode=='label_removed': e['editorial_annotations']=[]
    if mode=='claim_drift': c['statement']=c['statement'].replace('禱告','禁食')
    if mode=='locator_drift': old['component_locator']['canonical_spans'][0]['end_char']=51
    with pytest.raises(ValueError): postimage(old,c,e)
