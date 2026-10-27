from copy import deepcopy
import pytest
from backend.pipeline.claim_management import inherited_readonly_context


@pytest.mark.parametrize('defect',[None,'in_scope','target_impact','write_impact','changed','writable','creation','not_refresh','no_reason'])
def test_only_exact_nonblocking_outside_context_keeps_its_classification(defect):
    note={'target_key':'claims/OTHER','affected_target_keys':[],'affected_write_keys':[],
          'impact_reason':'independent context outside target own evidence','reason':'needs its own Update'}
    value={'request':{'permissions':[],'creation_permissions':[]},'original_goal':{'original_objects':{'claims/TARGET':{}}},
           'continuation':{'read_only_refresh':True,'prior_findings':{'F':deepcopy(note)}},'findings':{'F':note}}
    if defect=='in_scope':value['original_goal']['original_objects']['claims/OTHER']={}
    if defect=='target_impact':note['affected_target_keys']=['claims/TARGET']
    if defect=='write_impact':note['affected_write_keys']=['claims/OTHER']
    if defect=='changed':note['reason']='new objection'
    if defect=='writable':value['request']['permissions']=[{'collection':'claims','object_id':'TARGET'}]
    if defect=='creation':value['request']['creation_permissions']=[{'collection':'claims'}]
    if defect=='not_refresh':value['continuation']['read_only_refresh']=False
    if defect=='no_reason':note['impact_reason']=''
    snapshot=deepcopy(value)
    assert inherited_readonly_context(value)==({'F':note} if defect is None else {})
    assert value==snapshot  # no content judgment or ledger write in the classifier
