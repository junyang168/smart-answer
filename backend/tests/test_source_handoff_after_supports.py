from copy import deepcopy
import pytest
from backend.pipeline import cvp_source_impact_resolution as flow


@pytest.mark.parametrize('fault',[None,'foreign','missing','plan','current','pending','population','unfinished',
    'nohandoff','normal','normalpending','normalpopulation','domain','type','target','claim','approval','targets'])
def test_exact_immediate_committed_supports_handoff(monkeypatch,fault):
    handoff={'schema_version':flow.HANDOFF}
    old={'viewpoint_claim_link_id':'L','link_type':'equivalent_component','claim_id':'C','viewpoint_id':'V',
         'pinned_claim_revision':2,'validated_against_viewpoint_revision_id':'VR','effective_state':'active'}
    new={**old,'link_type':'supports'}
    prior={'plan':{'change_set_id':'prior'},'evidence':{'action':{'materials':{'claim_management_resolution':handoff}},
        'pending_cvp_management_record_keys':['claims/X'],'pending_cvp_claim_ids':[],'viewpoint_ids':['V']}}
    current={'plan':{'change_set_id':'now','operations':[{'collection':'viewpoint_claim_links',
        'object_id':'L','operation':'update','payload':new}]},'evidence':{
        'action':{'kind':'audit_supports_correction','materials':{'packet':{'targets':[{'before_link':old}]}}},
        'approval':{'gpt':{},'opus':{}},'pending_cvp_management_record_keys':['claims/X'],
        'pending_cvp_claim_ids':[],'viewpoint_ids':['V']}}
    value={'run_id':'R','reviewed':current,'reviewed_sha256':'N','change_set_ids':['prior','now']}
    row=({'run_id':'R','reviewed_sha256':'P'},)
    if fault=='foreign':row=({'run_id':'foreign','reviewed_sha256':'P'},)
    elif fault=='missing':row=None
    elif fault=='plan':prior['plan']['change_set_id']='other'
    elif fault=='pending':prior['evidence']['pending_cvp_management_record_keys']=[]
    elif fault=='population':prior['evidence']['viewpoint_ids']=['foreign']
    elif fault=='unfinished':prior['evidence']['pending_cvp_claim_ids']=['C']
    elif fault=='nohandoff':prior['evidence']['action']['materials']={}
    elif fault in {'normal','normalpending','normalpopulation'}:
        prior['evidence'].pop('action')
        prior['evidence'].pop('pending_cvp_management_record_keys')
        current['evidence'].pop('pending_cvp_management_record_keys')
        if fault=='normalpending':
            prior['evidence']['pending_cvp_management_record_keys']=['claims/X']
            current['evidence']['pending_cvp_management_record_keys']=['claims/X']
        if fault=='normalpopulation':prior['evidence']['viewpoint_ids']=['foreign']
    elif fault=='domain':current['plan']['operations'][0]['collection']='claims'
    elif fault=='type':new['link_type']='qualifies'
    elif fault=='target':new['viewpoint_id']='other'
    elif fault=='claim':new['pinned_claim_revision']=3
    elif fault=='approval':current['evidence']['approval'].pop('opus')
    elif fault=='targets':current['evidence']['action']['materials']['packet']['targets']=[]
    def review(store,v,sha,**kwargs):
        if fault=='current' and sha=='N':return {**deepcopy(current),'plan':{'change_set_id':'other'}}
        return current if sha=='N' else prior
    monkeypatch.setattr(flow,'applied_review',review)
    class Cursor:
        def execute(self,*args):return self
        def fetchone(self):return row
    before=deepcopy(value)
    if fault=='normal':assert flow.context(value,cursor=Cursor()) is None
    elif fault:
        with pytest.raises(ValueError):flow.context(value,cursor=Cursor())
    else:assert flow.context(value,cursor=Cursor())==handoff
    assert value==before
