"""Approved connection reasons are evidence context, never new route approval."""
from copy import deepcopy
import json

import pytest

from backend.api.canonical_repository.cvp_link_judgment import approved_link_judgment
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.postgres_store import canonical_json
from backend.pipeline.viewpoint_route_materials import prepare_cvp_route_materials, verify_cvp_route_materials
from backend.pipeline.viewpoint_route_resolution import build_registry_route_packet
from backend.pipeline.viewpoint_route_cvp_resolution import run_cvp_route_scope
from backend.pipeline import viewpoint_route_connection_recovery as recovery
from backend.pipeline.viewpoint_resolution_runtime import write_immutable
from test_cvp_management_runner import setup,postgres_store,_temporary_postgres
from test_cvp_management_maintenance import _initial
from test_viewpoint_observation_support import fixture_packet, observation_proposal, approved, Answer


def seal(value,field='artifact_sha256'):
    return {**value,field:sha256_json({k:v for k,v in value.items() if k != field})}


def test_current_link_uses_exact_committed_candidate_reason_and_pins_its_decision(setup):
    store,_,_,_,_,view,link,_=_initial(setup)
    origin=store.read_cvp_link_judgment_origins([link])[link['viewpoint_claim_link_id']]
    decision=store.get_record('viewpoint_identity_decisions',link['decision_id'])
    judgment=approved_link_judgment(link,decision,origin)
    assert judgment['reasons'] != [decision['reason']]
    candidate=origin['reviewed']['evidence']['cvp_management']['candidate']
    assert judgment['reasons']==[candidate['component_dispositions'][0]['reason']]
    material=prepare_cvp_route_materials(store,view['viewpoint_id'],include_connection_judgments=True)
    assert material['approved_connection_judgments']==[judgment]
    assert ('viewpoint_identity_decisions',link['decision_id']) in {
        (p['collection'],p['object_id']) for p in material['record_pins']}
    verify_cvp_route_materials(store,material)
    for fault in ['postimage','approval','candidate','review']:
        o,d,l=deepcopy(origin),deepcopy(decision),deepcopy(link)
        if fault=='postimage':l['link_type']='qualifies'
        elif fault=='approval':o['reviewed']['evidence']['cvp_management']['approval']['approved']=False
        elif fault=='candidate':o['reviewed']['evidence']['cvp_management']['candidate']['component_dispositions'][0]['reason']='unapproved'
        else:d['review_artifact_sha256']='another-review'
        with pytest.raises(ValueError):approved_link_judgment(l,d,o)
    # A changed decision cannot escape the same SQL current-pin check as source evidence.
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET revision=revision+1 WHERE collection='viewpoint_identity_decisions' AND object_id=%s",(link['decision_id'],))
    with pytest.raises(Exception):verify_cvp_route_materials(store,material)


def saved_exception(tmp_path,monkeypatch,rounds=0,original_judgments=False):
    monkeypatch.setattr(recovery,'_fixed_implementation',lambda require_committed:('test-commit',{}))
    packet=fixture_packet()
    # Material pins and membership belong to the same exact saved job.
    link={'viewpoint_claim_link_id':'L1','claim_id':'C1','revision':1,'link_type':'equivalent_full',
          'effective_state':'active','validated_against_viewpoint_revision_id':'CVR-1'}
    m=seal({'viewpoint_id':'CV-1','viewpoint_revision_id':'CVR-1','links':[link],
            'record_pins':[{'collection':'claims','object_id':'C1','revision':1,'content_sha256':'claim'}],
            'viewpoint':{'current_revision_id':'CVR-1'},'claims':packet['claims'],'physical_sources':[]})
    judgments=[{'viewpoint_claim_link_id':'L1','claim_id':'C1','link_type':'equivalent_full',
        'viewpoint_revision_id':'CVR-1','reasons':['Same statement and same scope.']}]
    if original_judgments:
        packet=seal(packet|{'approved_connection_judgments':judgments,
            'connection_judgment_note':'Actual approved rationale; not route approval.'},'packet_sha256')
        m=seal(m|{'approved_connection_judgments':deepcopy(judgments)})
    write_immutable(tmp_path/'cvp-route-materials.json',m)
    calls=[]
    def review(p):
        r=approved(p);calls.append(p)
        if len(calls)<=rounds:
            r['change_reviews'][1].update(decision='correct',reason='clarify support scope',
                correction='preserve exact observation scope',finding_codes=['SCOPE'])
        else:
            r['cvp_re_review_exceptions']=[{'viewpoint_revision_id':'CVR-1','finding_code':'MEMBER_ROLE',
                'reason':'Review actual approved role and rationale before promoting the member.',
                'triggering_target_kind':'observation','triggering_target_key':'OBS-1',
                'evidence_claim_component_keys':[packet['claim_components'][0]['claim_component_key']]}]
        return r
    def correct(p):
        revised=deepcopy(p['route_proposal']);revised['observation_supports'][0]['support_scope_note']+='（限定）'
        return {'route_proposal_sha256':p['route_proposal_sha256'],'route_review_sha256':p['route_review_sha256'],
            'revised_proposal':revised,'finding_dispositions':[{'target_kind':'observation','target_key':'OBS-1',
            'disposition':'accepted','reason':'限定原文观察范围'}]}
    proposer,reviewer,reconsiderer=Answer(observation_proposal(packet),model_id='gpt'),Answer(review,model_id='opus'),Answer(correct,model_id='gpt')
    run_cvp_route_scope(route_packet=packet,existing_routes=[],output_dir=tmp_path,
        proposer=proposer,reviewer=reviewer,reconsiderer=reconsiderer,max_correction_rounds=rounds)
    judgments=[{'viewpoint_claim_link_id':'L1','claim_id':'C1','link_type':'equivalent_full',
        'viewpoint_revision_id':'CVR-1','reasons':['Same statement and same scope.']}]
    new_packet=seal(packet|{'approved_connection_judgments':judgments,
        'connection_judgment_note':'Actual approved rationale; not route approval.'},'packet_sha256')
    new_material=seal(m|{'approved_connection_judgments':judgments})
    return new_packet,new_material,proposer,reviewer,reconsiderer


@pytest.mark.parametrize('rounds',[0,2])
def test_continuation_authenticates_all_saved_rounds_keeps_budget_and_skips_initial_proposal(tmp_path,monkeypatch,rounds):
    packet,material,proposer,reviewer,corrector=saved_exception(tmp_path,monkeypatch,rounds)
    before={p.name:p.read_bytes() for p in tmp_path.glob('*.json')}
    seed=recovery.prepare_connection_continuation(tmp_path,packet,material,proposer=proposer,
        reviewer=reviewer,reconsiderer=corrector,max_correction_rounds=rounds)
    assert seed['receipt']['previous_corrections_consumed']==rounds
    assert seed['receipt']['mode']=='review' and seed['receipt']['new_initial_proposal_calls']==0
    assert proposer.calls==1 and corrector.calls==rounds
    assert reviewer.calls==rounds+1
    assert all((tmp_path/name).read_bytes()==content for name,content in before.items())
    reviewer._response=approved
    result=run_cvp_route_scope(route_packet=packet,existing_routes=[],output_dir=tmp_path/recovery.DIRECTORY,
        proposer=proposer,reviewer=reviewer,reconsiderer=corrector,max_correction_rounds=rounds,
        connection_seed=seed)
    assert result['cvp_re_review_exceptions']==[]
    assert result['corrections_consumed_including_parent']==rounds
    assert proposer.calls==1 and corrector.calls==rounds and reviewer.calls==rounds+2
    assert reviewer.payloads[-1]['connection_continuation_context']['original_review']==seed['review']
    recovery.verify_connection_reservation(tmp_path,original_material_sha256=seed['receipt']['old_material_sha256'])
    assert all((tmp_path/name).read_bytes()==content for name,content in before.items())


@pytest.mark.parametrize('fault',['claim','source_pin','unknown_call','old_answer','member','identity','missing_rationale'])
def test_continuation_refuses_drift_unknown_calls_and_unrelated_changes(tmp_path,monkeypatch,fault):
    packet,m,p,r,c=saved_exception(tmp_path,monkeypatch)
    if fault=='claim':m['claims'][0]['statement']='changed';m=seal(m)
    elif fault=='source_pin':m['record_pins'][0]['content_sha256']='changed';m=seal(m)
    elif fault=='unknown_call':write_immutable(tmp_path/'raw-unknown.call-intent.json',{})
    elif fault=='old_answer':
        path=tmp_path/'route-proposal-000.json';v=json.loads(path.read_text());v['proposal']['reason']='altered';path.write_text(json.dumps(v))
    elif fault=='member':m['links'][0]['link_type']='qualifies';m=seal(m)
    elif fault=='identity':m['viewpoint']['current_revision_id']='other';m=seal(m)
    else:packet.pop('approved_connection_judgments');packet=seal(packet,'packet_sha256')
    counts=(p.calls,r.calls,c.calls)
    with pytest.raises((ValueError,KeyError)):
        recovery.prepare_connection_continuation(tmp_path,packet,m,proposer=p,reviewer=r,reconsiderer=c,max_correction_rounds=0)
    assert (p.calls,r.calls,c.calls)==counts
    assert not (tmp_path/'connection-continuation-reservation.json').exists()


@pytest.mark.parametrize('original_judgments',[False,True])
def test_upstream_repaired_member_uses_one_original_correction_then_full_review(tmp_path,monkeypatch,original_judgments):
    packet,m,p,r,c=saved_exception(tmp_path,monkeypatch,original_judgments=original_judgments)
    added=deepcopy(m['links'][0]);added.update(viewpoint_claim_link_id='L2',link_type='extends')
    m['links'].append(added)
    m['record_pins'].append({'collection':'viewpoint_claim_links','object_id':'L2','revision':1,'content_sha256':'L2'})
    j=deepcopy(m['approved_connection_judgments'][0]);j.update(viewpoint_claim_link_id='L2',link_type='extends')
    m['approved_connection_judgments'].append(j);m=seal(m)
    packet['approved_connection_judgments']=m['approved_connection_judgments'];packet=seal(packet,'packet_sha256')
    seed=recovery.prepare_connection_continuation(tmp_path,packet,m,proposer=p,reviewer=r,
        reconsiderer=c,max_correction_rounds=1)
    assert seed['receipt']['mode']=='correction' and seed['receipt']['added_link_ids']==['L2']
    def answer(p):
        revised=deepcopy(p['route_proposal']);revised['observation_supports'][0]['support_scope_note']+='（核对新增成员）'
        return revised
    p._response=answer;r._response=approved
    result=run_cvp_route_scope(route_packet=packet,existing_routes=[],output_dir=tmp_path/recovery.DIRECTORY,
        proposer=p,reviewer=r,reconsiderer=c,max_correction_rounds=1,connection_seed=seed)
    assert result['corrections_consumed_including_parent']==1
    assert p.calls==2 and c.calls==0 and r.calls==2
    assert len(result['rounds'])==1
    # Same parent with no correction left cannot admit that material change.
    (tmp_path/'connection-continuation-reservation.json').unlink()
    with pytest.raises(ValueError,match='remaining original correction'):
        recovery.prepare_connection_continuation(tmp_path,packet,m,proposer=p,reviewer=r,
            reconsiderer=c,max_correction_rounds=0)
