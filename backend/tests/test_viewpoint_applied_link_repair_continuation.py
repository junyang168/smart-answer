from copy import deepcopy
import pytest
from backend.pipeline.viewpoint_applied_link_repair_continuation import verify_transition
from backend.pipeline.viewpoint_authorized_source_repair_357 import fingerprint

def case():
    old={'viewpoint_claim_link_id':'L','revision':1,'link_type':'equivalent_component','component_locator':{'text':'old'}}
    new=dict(old,revision=2,link_type='supports',component_locator=None)
    before={('viewpoint_claim_links','L'):old,('claims','C'):{'statement':'unchanged'}}
    live=deepcopy(before);live['viewpoint_claim_links','L']=new
    live['viewpoint_identity_candidates','I']={'revision':1,'identity_candidate_id':'I'}
    live['viewpoint_identity_decisions','D']={'revision':1,'identity_decision_id':'D'}
    plan={'operations':[{'collection':c,'object_id':i,'operation':'update' if i=='L' else 'create',
                        'payload':live[c,i],'after_revision':live[c,i]['revision']} for c,i in live if c!='claims']}
    collections=sorted({c for c,i in live})
    return live,{'collections':collections,'pre_fingerprint_sha256':fingerprint(before,collections)}, {'targets':[{'before_link':old}]},plan,before

def test_reverse_exact_applied_transition_no_writes():
    live,before,packet,plan,original=case();copy=deepcopy(live)
    assert verify_transition(live,before,packet,plan)==original and live==copy

@pytest.mark.parametrize('change',['foreign_claim','extra_record','changed_link','dropped_create','extra_op','wrong_operation','duplicate'])
def test_reverse_rejects_any_unproved_registry_mutation(change):
    live,before,packet,plan,_=case()
    if change=='foreign_claim':live['claims','C']['statement']='changed'
    elif change=='extra_record':live['claims','X']={'statement':'new'}
    elif change=='changed_link':live['viewpoint_claim_links','L']['revision']=3
    elif change=='dropped_create':del live['viewpoint_identity_decisions','D']
    elif change=='extra_op':plan['operations'].append(plan['operations'][0])
    elif change=='duplicate':plan['operations'][1]=plan['operations'][0]
    else:plan['operations'][1]['operation']='update'
    with pytest.raises(ValueError):verify_transition(live,before,packet,plan)
