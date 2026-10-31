"""Bounded group boundaries, legacy compatibility, and shared-write protection."""
import json

import pytest

from backend.pipeline import claim_management as cm, claim_direct_scope as direct
from backend.pipeline import claim_management_intake as intake, claim_management_batch as batch
from backend.pipeline.claim_review_units import CONTRACT, validate_members
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runner import binding
from backend.tests.test_claim_management_intake import claims, pass_review
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_direct_scope import chain
from backend.tests.test_claim_joint_update import shared
from backend.tests.test_claim_management_human_source_follow_up import authorize
from backend.pipeline import claim_review_reuse
from backend.pipeline import claim_review_units


def policy_file(root):
    policy = cm.Policy.model_validate_json(intake.DEFAULT_POLICY.read_text())
    policy.review_unit_contract = CONTRACT
    policy.joint_update_max_targets = 10
    path = root / 'bounded-policy.json'
    path.write_text(policy.model_dump_json())
    return path


@pytest.mark.parametrize('groups', [[[]], [['A'] * 2], [['A'], ['A']], [[str(i) for i in range(11)]]])
def test_membership_rejects_duplicates_and_oversized_units(groups):
    with pytest.raises(ValueError):
        validate_members(groups)


def test_old_policy_bytes_do_not_acquire_unit_contract():
    policy = cm.Policy.model_validate_json(intake.DEFAULT_POLICY.read_text())
    assert 'review_unit_contract' not in policy.model_dump(mode='json')
    assert validate_members([['A'], ['B', 'C']]) == [['A'], ['B', 'C']]


def test_write_capability_does_not_promote_related_claims(chain):
    store, request, output, root = chain
    path = policy_file(root)
    request.policy = cm.source.FileBinding.model_validate(binding(path))
    # Do not change authority or dispatch this synthetic request; inspect scope only.
    request.permissions += [cm.source.Permission(collection='claim_relations', object_id='AB', fields=['reason']),
                            cm.source.Permission(collection='evidence_steps', object_id='EB', fields=['statement'])]
    states = store.read_claim_context([('claims', 'A')])
    before = {cm.source.key(*key): value for key, value in states.items()}
    selected = direct.scope(request, before)
    assert selected['content_claim_keys'] == ['claims/A']
    assert selected['neighbour_claim_keys'] == ['claims/B']
    assert 'claims/C' not in selected['record_keys']
    # An actual EB change still has its owner checked, even without a target B.
    payload = dict(before['evidence_steps/EB']['payload'], statement='Changed shared evidence')
    candidate = cm.existing_candidate(before)
    candidate.records = [cm.source.Row(collection='evidence_steps', object_id='EB', payload=payload)]
    next(d for d in candidate.dispositions if d.record_key == 'evidence_steps/EB').action = 'update'
    impacted = direct.scope(request, before, candidate)
    assert 'claims/B' in impacted['impact_claim_keys']
    assert impacted['content_claim_keys'] == ['claims/A']
    assert 'evidence_steps/EB' in impacted['record_keys']


def test_nonblocking_leads_do_not_force_a_gpt_round(chain):
    _, request, _, root = chain
    request.policy = cm.source.FileBinding.model_validate(binding(policy_file(root)))
    context = {'F': {'target_key': 'claims/B', 'affected_target_keys': [], 'affected_write_keys': [],
                     'impact_reason': 'unrelated old issue'}}
    assert cm.pending_context_adjudications({}, cm.Candidate(records=[], dispositions=[]), context, request=request) == {}
    assert context['F']['target_key'] == 'claims/B'  # No fake resolution or deletion.


def test_three_member_unit_uses_one_existing_update_and_retains_all_results(claims):
    store, root = claims
    store.apply_plan(store.plan_package({'package_id': 'third-unit-member',
        'claims': [{'claim_id': 'E', 'statement': '甲是假设。', 'claim_type': 'exegesis',
                    'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['EE']}],
        'evidence_steps': [{'evidence_step_id': 'EE', 'statement': '甲是假设。',
                            'produced_claim_ids': ['E'], 'source_fragment_ids': ['FC'],
                            'support_eligibility': 'eligible_candidate', 'speaker': 'professor', 'stance': 'asserted'}]}))
    policy = policy_file(root.parent)
    prepared = intake.prepare(['C', 'D', 'E'], root, instruction='Review this fixed source unit',
                              groups=[['C', 'D', 'E']], policy=policy, store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root / 'batch', store=store)
    assert len(jobs) == 1
    request, execution = next(iter(jobs.values()))
    assert request.target_keys == ['claims/C', 'claims/D', 'claims/E']
    def review(packet):
        assert set(packet['claim_scope']['content_claim_keys']) == set(request.target_keys)
        return pass_review(packet)
    ports = SyntheticPorts(store, execution, opus=review)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['opus']
    assert all(store.claim_management_qualification(cid)['ready'] for cid in ['C', 'D', 'E'])
    assert execute_claim_management(store, request, root=execution, ports=ports)['status'] == 'completed'
    assert ports.calls == ['opus']


@pytest.mark.parametrize('drift', [None, 'evidence', 'new_relation', 'hold'])
def test_full_prior_bystander_pass_reuses_without_model_and_fails_after_drift(claims, drift):
    store, root = claims
    store.apply_plan(store.plan_package({'package_id': 'review-both', 'claim_relations': [
        {'claim_relation_id': 'R', 'from_id': 'D', 'to_id': 'C', 'relation_type': 'supports'}]}))
    prepared = intake.prepare(['C'], root, instruction='Review C', store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root / 'batch', store=store)
    request, execution = next(iter(jobs.values()))
    ports = SyntheticPorts(store, execution, opus=pass_review)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status'] == 'completed', result
    assert store.claim_management_qualification('D') is None
    run_id = store.claim_management_qualification('C')['run_id']
    claim_review_reuse.admit(store, 'D', run_id)
    assert store.claim_management_qualification('D')['ready']
    assert ports.calls == ['opus']

    if drift == 'evidence':
        e = store.get_record_state('evidence_steps', 'ED')['payload']
        store.apply_plan(store.plan_package({'package_id': 'evidence-drift', 'evidence_steps': [dict(e, statement='Different evidence')]}))
    elif drift == 'new_relation':
        store.apply_plan(store.plan_package({'package_id': 'relation-drift',
            'claims': [{'claim_id': 'X', 'statement': 'X', 'claim_type': 'exegesis', 'occurrences': [{'source_id': 'S'}]}],
            'claim_relations': [{'claim_relation_id': 'RX', 'from_id': 'D', 'to_id': 'X', 'relation_type': 'supports'}]}))
    elif drift == 'hold':
        c = store.get_record_state('claims', 'D')['payload']
        store.apply_plan(store.plan_package({'package_id': 'hold-drift', 'claims': [dict(c, review_status='human_hold')]}))
    if drift:
        qualification = store.claim_management_qualification('D')
        assert not qualification or not qualification.get('ready')
    assert ports.calls == ['opus']



def test_unit_prepare_excludes_a_previous_pass_from_new_targets(claims):
    store, root = claims
    store.apply_plan(store.plan_package({'package_id': 'first-review-context', 'claim_relations': [
        {'claim_relation_id': 'R', 'from_id': 'D', 'to_id': 'C', 'relation_type': 'supports'}]}))
    old = intake.prepare(['C'], root, instruction='Review C', store=store)
    _, jobs = batch.load(old['manifest']['path'], root/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    ports = SyntheticPorts(store, execution, opus=pass_review)
    assert execute_claim_management(store, request, root=execution, ports=ports)['status'] == 'completed'
    # No caller-provided pass: prepare discovers and verifies D's complete
    # historical review, and uses current C as a reference too.
    prepared = claim_review_units.prepare(['C', 'D'], root.parent/'fixed-unit',
        instruction='Reuse this already reviewed unit', policy=policy_file(root.parent), store=store)
    assert prepared['target_claim_ids'] == []
    assert prepared['reference_claim_ids'] == ['C', 'D']
    assert prepared['prepared_update'] is None
    assert claim_review_units.finish(prepared, store=store)['completed']
    assert ports.calls == ['opus']


def test_reference_only_reading_cannot_be_imported_as_a_complete_review(claims):
    store, root = claims
    store.apply_plan(store.plan_package({'package_id': 'reference-only', 'claim_relations': [
        {'claim_relation_id': 'R', 'from_id': 'D', 'to_id': 'C', 'relation_type': 'supports'}]}))
    prepared = intake.prepare(['C'], root, instruction='Review C only', groups=[['C']],
                              policy=policy_file(root.parent), store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    result = execute_claim_management(store, request, root=execution,
                                      ports=SyntheticPorts(store, execution, opus=pass_review))
    assert result['status'] == 'completed', result
    with pytest.raises(ValueError, match='reference-only'):
        claim_review_reuse.build(store, 'D', store.claim_management_qualification('C')['run_id'])


def test_actual_shared_write_harming_a_reference_still_blocks(shared):
    store, request, execution, root = shared
    evidence = store.get_record_state('evidence_steps', 'E')['payload']
    store.apply_plan(store.plan_package({'package_id': 'bound-shared-evidence',
        'source_fragments': [{'fragment_id': 'F', 'source_id': 'S', 'paragraph_key': 'S0001',
                              'verbatim_excerpt': '甲是假设，不是断言。'}],
        'evidence_steps': [dict(evidence, source_fragment_ids=['F'], support_eligibility='eligible_candidate',
                                speaker='professor', stance='asserted')]}))
    path = policy_file(root)
    policy = cm.Policy.model_validate_json(path.read_text())
    policy.initial_candidate = 'proposal'
    policy.budget.max_revisions = 0
    path.write_text(policy.model_dump_json())
    request.policy = cm.source.FileBinding.model_validate(binding(path))
    authorize(request, {'required_maintenance_keys': ['evidence_steps/E']})
    old = store.get_record_state('evidence_steps', 'E')
    cite = {'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}
    def propose(packet):
        return {'changes':[{'record_key':'evidence_steps/E',
                            'fields_json':json.dumps({'statement':'甲是假设，不是断言。'}),
                            'reason':'synthetic shared correction','citations':[cite]}],
                'findings':[], 'finding_resolutions':[], 'creations':[], 'retirements':[]}
    def review(packet):
        assert packet['claim_scope']['content_claim_keys'] == ['claims/A', 'claims/B']
        assert packet['claim_scope']['impact_claim_keys'] == ['claims/C']
        result = pass_review(packet)
        result['findings'] = [{'target_key':'claims/C','component':'shared_evidence','category':'evidence',
            'reason':'shared write loses C support','acceptance_condition':'preserve C',
            'citations':[cite],'human_question':None,'affected_target_keys':[],
            'affected_write_keys':['evidence_steps/E'],'impact_reason':'actual write harms C'}]
        return result
    ports = SyntheticPorts(store, execution, proposal=propose, opus=review)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status'] == 'paused', result
    assert store.get_record_state('evidence_steps', 'E') == old
    assert ports.calls == ['gpt', 'opus']
