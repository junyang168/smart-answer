"""Scope clarification never promotes a member or clears an actual exception."""
from copy import deepcopy
import json
import pytest

from backend.pipeline import viewpoint_route_connection_recovery as recovery
from backend.pipeline import viewpoint_route_review_scope as scope
from backend.pipeline.viewpoint_route_cvp_resolution import run_cvp_route_scope
from backend.pipeline.viewpoint_route_resolution import build_registry_route_packet
from backend.pipeline.viewpoint_resolution_runtime import write_immutable
from test_route_claim_evidence_boundary import sample
from test_route_connection_context import seal
from test_viewpoint_batch_resolution import _registry_link, _approved_viewpoint
from test_viewpoint_observation_support import Answer, approved, observation_proposal


def saved_partial(tmp_path,monkeypatch):
    monkeypatch.setattr(recovery,'_fixed_implementation',lambda require_committed:('test-commit',{}))
    claim,_,routes=sample()
    link=_registry_link(claim,component_locator=None,link_type='extends')
    packet=build_registry_route_packet(scope_label=routes['scope_label'],approved_viewpoints=[_approved_viewpoint()],
        claims=[claim],viewpoint_claim_links=[link],existing_routes=[],complete_cvp=True,
        observation_supports=True,claim_evidence_for_intermediate_steps=True)
    judgments=[{'viewpoint_claim_link_id':link['viewpoint_claim_link_id'],'claim_id':claim.claim_id,
        'viewpoint_revision_id':'CVR-1','link_type':'extends','reasons':['Claim scope is broader.']}]
    packet=seal(packet|{'approved_connection_judgments':judgments},'packet_sha256')
    proposal=observation_proposal(packet)
    proposal['viewpoints_with_no_route']=[]
    proposal['argument_route_candidates']=routes['argument_route_candidates']
    proposal['source_route_attestations']=routes['source_route_attestations']
    key=packet['claim_components'][0]['claim_component_key']
    att=proposal['source_route_attestations'][0]
    att['completeness']='partial';att['terminal_claim_component_key']=key
    for binding in att['step_bindings']:binding['claim_component_keys']=[key]
    material=seal({'viewpoint_id':'CV-1','viewpoint_revision_id':'CVR-1','links':[link],
        'viewpoint':{'current_revision_id':'CVR-1'},'claims':packet['claims'],'physical_sources':[],
        'record_pins':[],'approved_connection_judgments':judgments})
    write_immutable(tmp_path/'cvp-route-materials.json',material)
    def review(p):
        result=approved(p)
        result['cvp_re_review_exceptions']=[{'viewpoint_revision_id':'CVR-1','finding_code':'MORE_MEMBERS',
            'reason':'Current partial is valid; a narrower Claim could add coverage.',
            'triggering_target_kind':'attestation','triggering_target_key':att['local_attestation_key'],
            'evidence_claim_component_keys':[key]}]
        return result
    proposer,reviewer,corrector=Answer(proposal,model_id='gpt'),Answer(review,model_id='opus'),Answer({},model_id='gpt')
    with monkeypatch.context() as old:
        old.setattr(scope,'with_review_scope',lambda packet:packet)
        run_cvp_route_scope(route_packet=packet,existing_routes=[],output_dir=tmp_path,
            proposer=proposer,reviewer=reviewer,reconsiderer=corrector,max_correction_rounds=4)
    return packet,material,proposer,reviewer,corrector


@pytest.mark.parametrize('still_blocking',[False,True])
def test_same_material_full_review_keeps_parent_and_real_opus_decision(tmp_path,monkeypatch,still_blocking):
    packet,m,p,r,c=saved_partial(tmp_path,monkeypatch)
    before={x.name:x.read_bytes() for x in tmp_path.glob('*.json')}
    seed=recovery.prepare_connection_continuation(tmp_path,packet,m,proposer=p,reviewer=r,
        reconsiderer=c,max_correction_rounds=4,clarify_review_scope=True)
    assert seed['receipt']['review_scope_contract']==scope.CONTRACT
    assert seed['receipt']['mode']=='review' and seed['receipt']['added_link_ids']==[]
    if not still_blocking:
        def approve(current):
            assert current[scope.MARKER]==scope.CONTRACT
            answer=approved(current)
            answer['reason']='Current partial remains valid. Follow-up: extract narrower Claim from this source.'
            return answer
        r._response=approve
    result=run_cvp_route_scope(route_packet=packet,existing_routes=[],output_dir=tmp_path/recovery.DIRECTORY,
        proposer=p,reviewer=r,reconsiderer=c,max_correction_rounds=4,connection_seed=seed)
    assert bool(result['cvp_re_review_exceptions'])==still_blocking
    assert result['_effective_proposal'].source_route_attestations[0].completeness=='partial'
    assert p.calls==1 and r.calls==2 and c.calls==0
    assert all((tmp_path/name).read_bytes()==raw for name,raw in before.items())
    followup=tmp_path/recovery.DIRECTORY
    # No second clarification can re-sample a reviewer who kept the exception.
    if still_blocking:
        write_immutable(followup/'cvp-route-materials.json',m)
        with pytest.raises(ValueError):
            recovery.prepare_connection_continuation(followup,packet,m,proposer=p,reviewer=r,
                reconsiderer=c,max_correction_rounds=4,clarify_review_scope=True)
        assert r.calls==2


def test_scope_clarification_rejects_material_changes(tmp_path,monkeypatch):
    packet,m,p,r,c=saved_partial(tmp_path,monkeypatch)
    changed=seal(m|{'claims':[]})
    with pytest.raises(ValueError,match='cannot change materials'):
        recovery.prepare_connection_continuation(tmp_path,packet,changed,proposer=p,reviewer=r,
            reconsiderer=c,max_correction_rounds=4,clarify_review_scope=True)
    assert p.calls==r.calls==1 and c.calls==0


@pytest.mark.parametrize('fault',['correct','full','route','no_exception'])
def test_only_passing_partial_exceptions_qualify(fault):
    proposal={'source_route_attestations':[{'local_attestation_key':'A','completeness':'partial'}]}
    review={'change_reviews':[{'target_kind':'attestation','target_key':'A','decision':'pass'}],
        'cvp_re_review_exceptions':[{'triggering_target_kind':'attestation','triggering_target_key':'A'}]}
    if fault=='correct':review['change_reviews'][0]['decision']='correct'
    elif fault=='full':proposal['source_route_attestations'][0]['completeness']='full'
    elif fault=='route':review['cvp_re_review_exceptions'][0]['triggering_target_kind']='route'
    else:review['cvp_re_review_exceptions']=[]
    with pytest.raises(ValueError):scope.validate_scope_continuation(proposal,review)
