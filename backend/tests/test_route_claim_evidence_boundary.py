"""Intermediate Claim evidence and CVP conclusion evidence are distinct scopes."""
from copy import deepcopy
import json

import pytest

from backend.api.canonical_repository.viewpoint_batch_resolution import (
    ArgumentRouteProposalV2Response, ArgumentRouteReviewV2Response, BatchResolutionError,
    RouteComponentBinding, canonicalize_review, validate_route_proposal,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
from backend.pipeline.viewpoint_route_resolution import build_registry_route_packet
from backend.pipeline.viewpoint_route_cvp_resolution import run_cvp_route_scope
from backend.pipeline.viewpoint_route_evidence_recovery import prepare_evidence_input_recovery
from backend.pipeline.viewpoint_resolution_runtime import call_model, write_immutable
from test_viewpoint_batch_resolution import (
    _claim, _evidence, _registry_link, _approved_viewpoint, _routes_v2,
    _StubAdapter, ROCK_STATEMENT,
)


def sample(full_component=True):
    claim = _claim('C1', ROCK_STATEMENT)
    extra = _evidence('C1');extra.update(evidence_step_id='E-premise',source_fragment_id='F-premise',
        citation_status='unresolved',support_eligibility='eligible_candidate',valid_for_identity_review=False)
    claim = ReviewClaim.model_validate(claim.model_dump(mode='json') | {'evidence':[
        *[e.model_dump(mode='json') for e in claim.evidence],extra]})
    link = _registry_link(claim, **({'component_locator':None} if full_component else {}))
    packet = build_registry_route_packet(scope_label='matt16-13-18',approved_viewpoints=[_approved_viewpoint()],
        claims=[claim],viewpoint_claim_links=[link],existing_routes=[],complete_cvp=True,
        claim_evidence_for_intermediate_steps=True)
    key = packet['claim_components'][0]['claim_component_key']
    proposal = _routes_v2().model_dump(mode='json')
    for binding in proposal['source_route_attestations'][0]['step_bindings']:
        binding['claim_component_keys']=[key]
    proposal['source_route_attestations'][0]['terminal_claim_component_key']=key
    node = proposal['argument_route_candidates'][0]['ordered_inference_nodes'][0]
    binding = proposal['source_route_attestations'][0]['step_bindings'][0]
    assert node['role'] != 'conclusion'
    binding['evidence_step_ids']=['E-premise'];binding['source_fragment_ids']=['F-premise']
    return claim, packet, proposal


def check(packet, proposal):
    return validate_route_proposal(routes=ArgumentRouteProposalV2Response.model_validate(proposal),
        scope_label=packet['scope_label'],claims=[ReviewClaim.model_validate(c) for c in packet['claims']],
        approved_viewpoint_revision_ids=packet['approved_viewpoint_revision_ids'],known_route_revision_ids=[],
        component_bindings=[RouteComponentBinding.model_validate(c) for c in packet['claim_components']],
        complete_cvp=True,claim_evidence_for_intermediate_steps=True)


def test_full_claim_component_collision_keeps_link_spans_and_evidence_but_binds_own_premise():
    claim,packet,proposal=sample()
    assert len(packet['claim_components'])==1
    component=packet['claim_components'][0]
    assert component['evidence_step_ids']==['C1-E1']
    assert component['spans'][0]['exact_text']==claim.statement
    assert check(packet,proposal)['full_count']==1
    old=build_registry_route_packet(scope_label='matt16-13-18',approved_viewpoints=[_approved_viewpoint()],
        claims=[claim],viewpoint_claim_links=[_registry_link(claim,component_locator=None)],
        existing_routes=[],complete_cvp=True)
    assert {k:v for k,v in old.items() if k!='packet_sha256'}=={
        k:v for k,v in packet.items() if k not in {'packet_sha256','evidence_binding_contract','evidence_binding_note'}}


def test_partial_component_preserves_approved_spans_without_blocking_own_claim_premises():
    claim,packet,proposal=sample(full_component=False)
    positive=next(c for c in packet['claim_components'] if c['disposition']=='member_existing')
    key=positive['claim_component_key']
    for b in proposal['source_route_attestations'][0]['step_bindings']:b['claim_component_keys']=[key]
    proposal['source_route_attestations'][0]['terminal_claim_component_key']=key
    assert positive['spans'][0]['exact_text']!=claim.statement
    assert positive['evidence_step_ids']==['C1-E1']
    assert check(packet,proposal)['full_count']==1


@pytest.mark.parametrize('fault',['conclusion','foreign_claim','wrong_pair','withheld','unbound_anchor','terminal'])
def test_new_scope_does_not_legalize_wrong_conclusion_foreign_or_unusable_evidence(fault):
    claim,packet,proposal=sample()
    if fault=='conclusion':
        b=proposal['source_route_attestations'][0]['step_bindings'][-1]
        b.update(evidence_step_ids=['E-premise'],source_fragment_ids=['F-premise'])
    elif fault=='foreign_claim':
        foreign=_claim('C2','同篇另一主张');packet['claims'].append(foreign.model_dump(mode='json'))
        b=proposal['source_route_attestations'][0]['step_bindings'][0]
        b.update(evidence_step_ids=['C2-E1'],source_fragment_ids=['C2-F1'])
    elif fault=='wrong_pair':
        proposal['source_route_attestations'][0]['step_bindings'][0]['source_fragment_ids']=['C1-F1']
    elif fault=='withheld':packet['claims'][0]['evidence'][-1]['support_eligibility']='withheld_missing_anchor'
    elif fault=='unbound_anchor':packet['claims'][0]['evidence'][-1]['anchor_state']='unresolved'
    else:
        packet['claim_components'][0].update(disposition='no_registry_assertion',target_viewpoint_revision_id=None)
    with pytest.raises(BatchResolutionError):check(packet,proposal)


def saved_parent(root):
    _,packet,proposal=sample()
    old={k:v for k,v in packet.items() if k not in {'packet_sha256','evidence_binding_contract','evidence_binding_note'}}
    old['packet_sha256']=sha256_json(old)
    materials={'artifact_sha256':'material-sha'}
    for name,payload in [('route-packet.json',old),('cvp-route-materials.json',materials)]:
        write_immutable(root/name,payload)
    proposer=_StubAdapter(proposal,model_id='gpt')
    call_model(proposer,old,root/'raw-route-proposal-000.json')
    write_immutable(root/'route-proposal-000.json',{'proposal':proposal,'proposal_sha256':sha256_json(proposal)})
    review=ArgumentRouteReviewV2Response.model_validate({
        'route_proposal_sha256':sha256_json(proposal),'route_evidence_packet_sha256':old['packet_sha256'],
        'change_reviews':[
            {'target_kind':'route','target_key':'ROUTE-GREEK','decision':'correct','reason':'修订标签',
             'correction':'收窄标签','finding_codes':['LABEL']},
            {'target_kind':'member_source','target_key':'CVR-1::S1','decision':'defer','reason':'前提被连接证据上限排除',
             'finding_codes':['MEMBER_SOURCE_COMPONENT_COVERAGE_GAP']}],
        'cross_source_composition_found':False,'reason':'保留异议'}).model_dump(mode='json')
    review=ArgumentRouteReviewV2Response.model_validate(canonicalize_review(review)[0]).model_dump(mode='json')
    review_packet={'route_evidence_context':old,'route_proposal_sha256':sha256_json(proposal)}
    write_immutable(root/'route-review-packet-000.json',review_packet)
    reviewer=_StubAdapter(review,model_id='opus');call_model(reviewer,review_packet,root/'raw-route-review-000.json')
    write_immutable(root/'route-review-000.json',{'review':review,'review_sha256':sha256_json(review)})
    return packet,materials,proposer,reviewer,proposal,review


def test_fixed_parent_recovery_preserves_defer_and_uses_original_budget_without_initial_proposal(tmp_path):
    packet,materials,proposer,old_reviewer,original,review=saved_parent(tmp_path)
    parent_bytes={p.name:p.read_bytes() for p in tmp_path.glob('*.json')}
    seed=prepare_evidence_input_recovery(tmp_path,packet,materials,proposer=proposer,reviewer=old_reviewer,
        max_correction_rounds=4)
    assert proposer.calls==old_reviewer.calls==1
    assert any(v['decision']=='defer' for v in seed['review']['change_reviews'])
    revised=deepcopy(original);revised['argument_route_candidates'][0]['route_label']='收窄标签'
    correction={'schema_version':'wang_argument_route_reconsideration_v2',
        'route_proposal_sha256':sha256_json(original),'route_review_sha256':sha256_json(review),
        'finding_dispositions':[{'target_kind':'route','target_key':'ROUTE-GREEK',
            'disposition':'accepted','reason':'按异议修改'}],'revised_proposal':revised}
    final=ArgumentRouteReviewV2Response.model_validate({
        'route_proposal_sha256':sha256_json(revised),'route_evidence_packet_sha256':packet['packet_sha256'],
        'change_reviews':[{'target_kind':kind,'target_key':key,'decision':'pass','reason':'完整审阅'}
                          for kind,key in [('route','ROUTE-GREEK'),('attestation','ATTEST-1')]],
        'cross_source_composition_found':False,'reason':'通过'}).model_dump(mode='json')
    reviewer=_StubAdapter(final);reconsiderer=_StubAdapter(correction)
    result=run_cvp_route_scope(route_packet=packet,existing_routes=[],output_dir=tmp_path/'repair',
        proposer=proposer,reviewer=reviewer,reconsiderer=reconsiderer,max_correction_rounds=4,recovery_seed=seed)
    assert proposer.calls==1 and reconsiderer.calls==reviewer.calls==1
    assert result['input_recovery_corrections']==1
    assert reviewer.payloads[0]['route_proposal_context']['argument_route_candidates'][0]['route_label']=='收窄标签'
    assert reviewer.payloads[0]['input_recovery_context']['original_review']==review
    assert all((tmp_path/name).read_bytes()==data for name,data in parent_bytes.items())
    assert prepare_evidence_input_recovery(tmp_path,packet,materials,proposer=proposer,reviewer=old_reviewer,
        max_correction_rounds=4)==seed


@pytest.mark.parametrize('fault',['material','packet','raw','unknown','semantic_defer','budget'])
def test_recovery_cannot_hide_drift_unknown_calls_or_unrelated_disagreement(tmp_path,fault):
    packet,materials,proposer,reviewer,_,_=saved_parent(tmp_path)
    if fault=='material':materials={**materials,'other':'drift'}
    elif fault=='packet':packet['claim_components'][0]['evidence_step_ids'].append('foreign')
    elif fault=='raw':
        path=tmp_path/'raw-route-review-000.json';value=json.loads(path.read_text());value['response']['reason']='changed'
        path.write_text(json.dumps(value))
    elif fault=='unknown':(tmp_path/'raw-route-reconsideration-000.call-intent.json').write_text('{}')
    elif fault=='budget':(tmp_path/'route-proposal-validation-000.json').write_text('{}')
    else:
        # A signed provider response with an unrelated defer is not an evidence-input defect.
        path=tmp_path/'route-review-000.json';value=json.loads(path.read_text());value['review']['change_reviews'][-1]['finding_codes']=['SEMANTIC_DISAGREEMENT']
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError):prepare_evidence_input_recovery(tmp_path,packet,materials,
        proposer=proposer,reviewer=reviewer,max_correction_rounds=4)
