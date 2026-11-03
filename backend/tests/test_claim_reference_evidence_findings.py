"""Reference owners remain references after an actual incident-edge write."""
from copy import deepcopy
import json

import pytest

from backend.pipeline import claim_management as cm, claim_direct_scope as direct
from backend.pipeline import claim_management_intake as intake, claim_management_batch as batch
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runner import binding
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_intake import claims, pass_review
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_direct_scope import chain
from backend.tests.test_claim_review_units import policy_file
from backend.tests.test_claim_management_human_source_follow_up import authorize


@pytest.fixture
def reference_evidence(chain):
    store, request, _, root = chain
    store.apply_plan(store.plan_package({'package_id': 'reference-premise',
        'evidence_steps': [{'evidence_step_id': 'ORPHAN', 'statement': '甲是假设。',
                            'produced_claim_ids': []}],
        'knowledge_relations': [{'relation_id': 'PREMISE', 'from_id': 'ORPHAN', 'to_id': 'EB',
                                 'relation_type': 'supports', 'reason': 'reference-only premise'}]}))
    request.policy = cm.source.FileBinding.model_validate(binding(policy_file(root)))
    if not any(p.key == 'claim_relations/AB' for p in request.permissions):
        request.permissions.append(cm.source.Permission(collection='claim_relations', object_id='AB', fields=['reason']))
    authorize(request, {'required_maintenance_keys': []})
    before = {cm.source.key(*k): cm.model_state(v)
              for k, v in store.read_claim_context([('claims', 'A')]).items()}
    candidate = cm.existing_candidate(before)
    payload = dict(before['claim_relations/AB']['payload'], reason='corrected argument role')
    candidate.records = [cm.source.Row(collection='claim_relations', object_id='AB', payload=payload)]
    next(d for d in candidate.dispositions if d.record_key == 'claim_relations/AB').action = 'update'
    finding = {'target_key': 'evidence_steps/ORPHAN', 'category': 'mapping',
        'component': 'independent_judgement_without_claim', 'affected_target_keys': [],
        'affected_write_keys': [], 'impact_reason': 'Private evidence of reference B; no effect on A or the AB write.'}
    return request, before, candidate, finding


def test_reference_private_evidence_is_not_promoted_by_relation_write(reference_evidence):
    request, before, candidate, finding = reference_evidence
    # Keep all shared-impact material in the actual review/transaction scope.
    actual = direct.scope(request, before, candidate)
    assert 'evidence_steps/ORPHAN' in actual['record_keys']
    assert actual['content_claim_keys'] == ['claims/A']
    assert 'claims/B' in actual['impact_claim_keys']
    assert not cm.finding_blocks(finding, request, candidate, records=before)


@pytest.mark.parametrize('basis', ['own_evidence', 'own_upstream', 'reverse_owner', 'required', 'created_claim',
                                 'actual_write', 'written_evidence_upstream', 'explicit_impact', 'write_impact'])
def test_real_target_and_write_obligations_still_block(reference_evidence, basis):
    request, before, candidate, finding = reference_evidence
    if basis == 'own_evidence':
        finding['target_key'] = 'evidence_steps/EA'
    elif basis == 'own_upstream':
        before['knowledge_relations/PREMISE']['payload']['to_id'] = 'EA'
    elif basis == 'reverse_owner':
        before['evidence_steps/ORPHAN']['payload']['produced_claim_ids'] = ['A']
    elif basis == 'required':
        authorize(request, {'required_maintenance_keys': ['evidence_steps/ORPHAN']})
    elif basis == 'created_claim':
        candidate.records.append(cm.source.Row(collection='claims', object_id='N', payload={
            'claim_id': 'N', 'statement': '甲是假设。', 'claim_type': 'exegesis',
            'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['ORPHAN']}))
        candidate.dispositions.append(cm.ObjectDisposition(record_key='claims/N', action='create', reason='new own judgment'))
    elif basis == 'actual_write':
        candidate.records.append(cm.source.Row(collection='evidence_steps', object_id='ORPHAN',
            payload=dict(before['evidence_steps/ORPHAN']['payload'], statement='changed premise')))
        next(d for d in candidate.dispositions if d.record_key == 'evidence_steps/ORPHAN').action = 'update'
        finding['affected_write_keys'] = ['evidence_steps/ORPHAN']
    elif basis == 'written_evidence_upstream':
        candidate.records.append(cm.source.Row(collection='evidence_steps', object_id='EB',
            payload=dict(before['evidence_steps/EB']['payload'], statement='changed actual evidence')))
        next(d for d in candidate.dispositions if d.record_key == 'evidence_steps/EB').action = 'update'
    elif basis == 'explicit_impact':
        finding['affected_target_keys'] = ['claims/A']
    elif basis == 'write_impact':
        finding['affected_write_keys'] = ['claim_relations/AB']
    assert cm.finding_blocks(finding, request, candidate, records=before)


def test_reference_lead_survives_real_commit_without_extra_model_round(claims):
    store, root = claims
    store.apply_plan(store.plan_package({'package_id': 'reference-edge-and-premise',
        'claim_relations': [{'claim_relation_id': 'CD', 'from_id': 'C', 'to_id': 'D',
                            'relation_type': 'supports', 'reason': 'old argument role'}],
        'evidence_steps': [{'evidence_step_id': 'ORPHAN', 'statement': '甲是假设。',
                            'produced_claim_ids': [], 'source_fragment_ids': ['FD'],
                            'support_eligibility': 'eligible_candidate', 'speaker': 'professor', 'stance': 'asserted'}],
        'knowledge_relations': [{'relation_id': 'PREMISE', 'from_id': 'ORPHAN', 'to_id': 'ED',
                                 'relation_type': 'supports', 'reason': 'reference-only premise'}]}))
    path = policy_file(root.parent)
    policy = cm.Policy.model_validate_json(path.read_text())
    policy.initial_candidate = 'proposal'
    path.write_text(policy.model_dump_json())
    prepared = intake.prepare(['C'], root, instruction='Refresh only C and correct its incident relation',
                              groups=[['C']], policy=path, store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    citation = {'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}
    def propose(packet):
        return {'changes': [{'record_key': 'claim_relations/CD',
            'fields_json': json.dumps({'reason': 'corrected argument role'}),
            'reason': 'synthetic relation correction', 'citations': [citation]}],
            'creations': [], 'retirements': [], 'findings': [], 'finding_resolutions': []}
    def review(packet):
        answer = pass_review(packet)
        answer['findings'] = [{'target_key': 'evidence_steps/ORPHAN', 'category': 'mapping',
            'component': 'independent_judgement_without_claim', 'reason': 'unrecorded private judgment of reference D',
            'acceptance_condition': 'Record for a later separately scoped operation', 'citations': [citation],
            'affected_target_keys': [], 'affected_write_keys': [],
            'impact_reason': 'Not used by target C or any new Claim and does not impair the CD reason correction.'}]
        return answer
    ports = SyntheticPorts(store, execution, proposal=propose, opus=review)
    original = deepcopy(store.get_record_state('evidence_steps', 'ORPHAN'))
    result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['gpt', 'opus']
    parent = store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    assert not parent['findings'] and len(parent['context_findings']) == 1
    assert store.get_record_state('evidence_steps', 'ORPHAN') == original
    assert store.claim_management_qualification('C')['ready']
    assert store.claim_management_qualification('D') is None
    assert store.get_record_state('claim_relations', 'CD')['payload']['reason'] == 'corrected argument role'
