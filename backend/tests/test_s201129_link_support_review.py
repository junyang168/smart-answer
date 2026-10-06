from copy import deepcopy
import pytest
from backend.pipeline import viewpoint_s201129_link_support_review as repair


def target():
    claim = {'claim_id':repair.CLAIM,'revision':3,
        'statement':'馬太福音五章三十二節關於被休婦人及娶她之人的兩個表達，都應按被動語態理解。'}
    old = {'viewpoint_claim_link_id':repair.LINK,'claim_id':repair.CLAIM,
        'viewpoint_id':repair.CV,'revision':2,'link_type':'equivalent_component',
        'component_locator':{'statement_component':'bad old slice'},'decision_id':'old',
        'validated_against_viewpoint_revision_id':'CVR-test','pinned_claim_revision':3,
        'occurrence_refs':['OCC-test'],'evidence_bindings':[],
        'supporting_relation_ids':[],'effective_state':'active',
        'visibility':'internal','review_status':'system_approved',
        'schema_version':'wang_viewpoint_claim_link_v1'}
    return {'claim':claim,'before_link':old,'after_link':repair.derive(old,claim)}


def test_support_keeps_full_conjunction_and_only_changes_authorized_fields():
    row=target()
    repair.validate_target({'targets':[row]})
    after=row['after_link'];before=row['before_link']
    assert after['link_type']=='supports'
    assert after['component_locator']['statement_component']==row['claim']['statement']
    assert len(after['component_locator']['canonical_spans'])==1
    assert {k for k in before if before[k]!=after[k]}=={'revision','link_type','component_locator'}


@pytest.mark.parametrize('field,value',[
    ('link_type','equivalent_component'),('viewpoint_id','CV-foreign'),
    ('claim_id','DK-foreign'),('revision',4),('decision_id','unreviewed'),
    ('component_locator',None),('effective_state','retired'),
])
def test_out_of_scope_postimage_rejected(field,value):
    row=target();row['after_link'][field]=value
    with pytest.raises(ValueError):repair.validate_target({'targets':[row]})


def test_claim_subject_deletion_and_new_claim_version_rejected():
    row=target()
    for key,value in [('statement',row['claim']['statement'].replace('及娶她之人','')),('revision',4)]:
        changed=deepcopy(row);changed['claim'][key]=value
        with pytest.raises(ValueError):repair.derive(changed['before_link'],changed['claim'])


def test_partial_or_duplicate_population_rejected():
    for rows in [[],[target(),target()]]:
        with pytest.raises(ValueError):repair.validate_target({'targets':rows})
