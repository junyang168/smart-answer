from copy import deepcopy
from pathlib import Path
import pytest

from backend.pipeline import cvp_link_retarget as flow
from backend.pipeline.source_reconciliation_runner import Stop,Runner,write
from backend.tests.test_cvp_link_target_selection import fixture,selected,CLAIM,OLD,NEW,LINK


def material_fixture(monkeypatch):
    store,_,_=fixture(monkeypatch)
    packet=flow.selection.build_packet(store,link_id=LINK,candidate_viewpoint_ids=[NEW])
    packet['old_target']['links'].append({**packet['before_link'],'claim_id':'retained-independent'})
    return {'run_id':'test-run','campaign_sha256':'campaign','comparison':packet,
            'original_reports':[{'path':'original-refusal','file_sha256':'a'*64}]}


def test_actual_propositions_retarget_only_connection_not_claim_or_source(monkeypatch):
    m=material_fixture(monkeypatch);p=m['comparison'];before=deepcopy(p)
    after=flow.postimage(p,selected())
    assert after['viewpoint_id']==NEW and after['link_type']=='supports'
    assert after['validated_against_viewpoint_revision_id']=='new-cvr'
    allowed={'revision','viewpoint_id','validated_against_viewpoint_revision_id','link_type','component_locator'}
    assert {k for k in after if after[k]!=p['before_link'].get(k)} <= allowed
    assert p==before and after['claim_id']==CLAIM and after.get('decision_id')==p['before_link'].get('decision_id')


@pytest.mark.parametrize('problem',['same-target','equivalent','unresolved','existing-link','orphan','relations'])
def test_target_scope_cannot_expand_or_retry_original_rejection(monkeypatch,problem):
    m=material_fixture(monkeypatch);p=m['comparison'];answer=selected()
    if problem=='same-target':answer['selection'].update(diagnosis='same_target',viewpoint_id=OLD)
    if problem=='equivalent':answer['selection']['link_type']='equivalent_full'
    if problem=='unresolved':answer['selection'].update(diagnosis='unresolved',viewpoint_id=None,link_type=None)
    if problem=='existing-link':p['candidates'][0]['links'][0]['claim_id']=CLAIM
    if problem=='orphan':p['old_target']['links']=p['old_target']['links'][:1]
    if problem=='relations':p['before_link']['supporting_relation_ids']=['relation']
    with pytest.raises((ValueError,Stop)):flow.postimage(p,answer)


def direction_fixture(material):
    p=material['comparison']
    return {'schema_version':flow.DIRECTION,'run_id':material['run_id'],
            'campaign_sha256':material['campaign_sha256'],'link_id':LINK,
            'before_link_sha256':flow.sha256_json(p['before_link']),
            'comparison_sha256':flow.sha256_json(p),'allowed_link_type':'supports',
            'candidate_viewpoint_ids':[NEW],'original_reports':material['original_reports'],
            'authorized_by':'owner','human_content_approval':False,
            'automatic_future_repairs_authorized':False,'authorization_quote':'TEST ONLY'}


@pytest.mark.parametrize('field',['schema_version','run_id','campaign_sha256','link_id','before_link_sha256',
    'comparison_sha256','allowed_link_type','candidate_viewpoint_ids','original_reports','authorized_by',
    'human_content_approval','automatic_future_repairs_authorized','authorization_quote'])
def test_direction_exact_not_future_permission(monkeypatch,field):
    material=material_fixture(monkeypatch);d=direction_fixture(material)
    flow.validate_direction(d,material)
    d[field]='' if field=='authorization_quote' else 'foreign'
    with pytest.raises(ValueError):flow.validate_direction(d,material)


def test_saved_review_identity_never_issues_call_and_rejects_tamper(monkeypatch,tmp_path):
    material=material_fixture(monkeypatch)
    class Ports:
        def adapter(self,stage):
            a=flow.model_adapter(stage);a._client.executable=None
            a.generate=lambda p:selected()
            return a
    runner=Runner(tmp_path,Ports());runner.root=tmp_path
    raw=runner.call('proposal',material)
    actual=flow.saved_raw(tmp_path,'proposal',material)
    assert actual==raw
    bad=deepcopy(material);bad['comparison']['claim']['statement']='changed'
    with pytest.raises(ValueError):flow.saved_raw(tmp_path,'proposal',bad)


def test_opus_rejection_never_becomes_content_approval():
    raw={'response':{'verdicts':[{'claim_id':CLAIM,'approved':False,'problems':['semantic difference'],'reason':'refused'}]}}
    with pytest.raises(Stop):flow.approved(raw,CLAIM)


@pytest.mark.parametrize('stage,model,backend',[('proposal','gpt-6.1-sol','codex_subscription'),
                                              ('initial_review','claude-opus-5-5','claude_subscription')])
def test_model_and_closed_stage_identity(stage,model,backend):
    a=flow.model_adapter(stage)
    assert (a.model_id,a.backend)==(model,backend)
    with pytest.raises(ValueError):flow.model_adapter('final_review')
