"""Reuse complete approvals across an actual, separately reviewed neighbour delta."""
import json

import pytest

from backend.pipeline import claim_management as cm, claim_management_intake as intake
from backend.pipeline import claim_management_batch as batch, claim_review_reuse as reuse
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.tests.test_claim_management_intake import claims, pass_review
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_review_units import policy_file


def original_pass(store, root):
    store.apply_plan(store.plan_package({'package_id':'connected', 'claim_relations':[
        {'claim_relation_id':'R','from_id':'D','to_id':'C','relation_type':'supports'}]}))
    prepared = intake.prepare(['C'], root/'old', instruction='Review existing content', store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root/'old'/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    ports = SyntheticPorts(store, execution, opus=pass_review)
    assert execute_claim_management(store, request, root=execution, ports=ports)['status']=='completed'
    rid = store.claim_management_qualification('C')['run_id']
    reuse.admit(store, 'D', rid)
    assert store.claim_management_qualification('D')['ready']
    return rid


def update_neighbour(store, root, *, objection=False, bounded=True, refs='馬太福音13:1-52'):
    path = policy_file(root.parent)
    policy = cm.Policy.model_validate_json(path.read_text())
    policy.initial_candidate = 'proposal'
    if not bounded:
        policy.review_unit_contract = None
    path.write_text(policy.model_dump_json())
    prepared = intake.prepare(['C'], root/'peer', instruction='Correct C; retain D as a reference',
                              groups=[['C']] if bounded else None, policy=path, store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root/'peer'/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    def propose(packet):
        return {'changes':[{'record_key':'claims/C',
            'fields_json':json.dumps({'scripture_refs':[refs]}),
            'reason':'synthetic reviewed scope correction',
            'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}]}],
            'creations':[], 'retirements':[], 'findings':[], 'finding_resolutions':[]}
    def review(packet):
        if bounded:
            assert list(packet['own_evidence_bindings'])==['claims/C']
            assert 'claims/D' in packet['claim_scope']['neighbour_claim_keys']
        result = pass_review(packet)
        if objection:
            result['findings']=[{'target_key':'claims/D','component':'synthetic_reference_objection',
                'category':'content','reason':'unresolved reference concern','acceptance_condition':'adjudicate D',
                'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}],
                'affected_target_keys':[],'affected_write_keys':[], 'impact_reason':'unrelated to C only'}]
        return result
    ports = SyntheticPorts(store, execution, proposal=propose, opus=review)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status']=='completed', result
    rid = store.claim_management_qualification('C')['run_id']
    assert ports.calls==['gpt','opus']
    return rid, ports


def test_neighbour_delta_preserves_complete_reference_approval_without_re_review(claims):
    store, root = claims
    old = original_pass(store, root)
    peer, ports = update_neighbour(store, root)
    q = store.claim_management_qualification('D')
    assert q['ready'] and q['run_id']==old
    proof = store.get_cvp_group(old)['readonly_claim_qualifications']['D']
    assert proof['contract']==reuse.CONTEXT_CONTRACT
    assert list(proof['context_reviews'])==['claims/C']
    assert proof['context_reviews']['claims/C']['round_id']==peer
    assert store.get_cvp_group(peer)['reference_qualification_handoffs'][0]['status']=='current'
    with store.connect() as conn, conn.cursor() as cursor:
        assert reuse.revalidate_context(store, cursor, 'D', old, peer)==proof
    assert ports.calls==['gpt','opus']


def test_later_neighbour_review_replaces_flat_context_proof_without_replaying_original(claims):
    store,root=claims
    old=original_pass(store,root)
    first,_=update_neighbour(store,root)
    baseline=store.get_cvp_group(old)['readonly_claim_qualifications']['D']['original_read_pins']
    later,ports=update_neighbour(store,root/'later',refs='馬太福音13:1-51')
    assert later!=first and store.claim_management_qualification('D')['ready']
    proof=store.get_cvp_group(old)['readonly_claim_qualifications']['D']
    assert proof['original_read_pins']==baseline
    assert list(proof['context_reviews'])==['claims/C']
    assert proof['context_reviews']['claims/C']['round_id']==later
    assert ports.calls==['gpt','opus']


@pytest.mark.parametrize('drift', ['own_evidence','incident_relation','reference_objection','legacy_peer'])
def test_context_handoff_cannot_hide_changed_basis_or_missing_review(claims, drift):
    store, root = claims
    old = original_pass(store, root)
    if drift=='own_evidence':
        payload=store.get_record_state('evidence_steps','ED')['payload']
        store.apply_plan(store.plan_package({'package_id':'changed-own-evidence',
            'evidence_steps':[dict(payload,statement='changed original argument')]}))
    elif drift=='incident_relation':
        payload=store.get_record_state('claim_relations','R')['payload']
        store.apply_plan(store.plan_package({'package_id':'changed-relation',
            'claim_relations':[dict(payload,reason='changed original argument relation')]}))
    peer, ports = update_neighbour(store, root, objection=drift=='reference_objection', bounded=drift!='legacy_peer')
    q=store.claim_management_qualification('D')
    assert not q or not q.get('ready')
    assert store.get_cvp_group(old)['readonly_claim_qualifications']['D']['contract']=='claim_complete_review_reuse_v1'
    if drift!='legacy_peer':
        assert store.get_cvp_group(peer)['reference_qualification_handoffs'][0]['status']=='deferred'
    with store.connect() as conn, conn.cursor() as cursor:
        with pytest.raises(ValueError):
            reuse.revalidate_context(store, cursor, 'D', old, peer)
    assert ports.calls==['gpt','opus']


@pytest.mark.parametrize('drift', ['peer_evidence','new_edge','human_hold','approval_file','peer_readback','source_file'])
def test_transferred_proof_checks_current_sources_basis_and_original_artifacts(claims, drift):
    store, root = claims
    old=original_pass(store,root)
    peer,_=update_neighbour(store,root)
    assert store.claim_management_qualification('D')['ready']
    if drift=='peer_evidence':
        payload=store.get_record_state('evidence_steps','EC')['payload']
        store.apply_plan(store.plan_package({'package_id':'peer-drift',
            'evidence_steps':[dict(payload,statement='new unreviewed premise')]}))
    elif drift=='new_edge':
        store.apply_plan(store.plan_package({'package_id':'new-edge',
            'claim_relations':[{'claim_relation_id':'R2','from_id':'C','to_id':'D','relation_type':'qualifies'}]}))
    elif drift=='human_hold':
        payload=store.get_record_state('claims','D')['payload']
        store.apply_plan(store.plan_package({'package_id':'hold', 'claims':[dict(payload,review_status='human_hold')]}))
    elif drift=='approval_file':
        from pathlib import Path
        approval=store.get_cvp_group(peer)['reviewed']['evidence']['approval']
        Path(approval['path']).write_text('{}')
    elif drift=='peer_readback':
        from pathlib import Path
        bound=store.get_cvp_group(peer)['stage_results']['readback']
        receipt=json.loads(Path(bound['path']).read_text())
        Path(receipt['readback']['path']).write_text('{}')
    else:
        from pathlib import Path
        bound=next(iter(store.get_cvp_group(peer)['materials']['request']['source_bindings'].values()))
        Path(bound['path']).write_text('changed source')
    q=store.claim_management_qualification('D')
    assert not q or not q.get('ready')


def test_context_handoff_cannot_create_a_missing_original_qualification(claims):
    store,root=claims
    old=original_pass(store,root)
    peer,_=update_neighbour(store,root)
    with store.connect() as conn,conn.cursor() as cursor:
        with pytest.raises(ValueError,match='existing complete qualification'):
            reuse.revalidate_context(store,cursor,'never-reviewed',old,peer)
