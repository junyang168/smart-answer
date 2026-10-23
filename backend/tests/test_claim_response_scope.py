"""Wire domains and returned rejection accounting, without live model calls."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm, claim_model_output as wire
from backend.pipeline.cvp_sql_flow import binding
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
from backend.pipeline.source_reconciliation_runner import read, write
from backend.tests.test_claim_management import case
from backend.tests.test_claim_model_output import material, empty, change
from backend.tests.test_claim_joint_update import shared, action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_management_human_source_follow_up import authorize


def scoped(material, *, review=False):
    policy = cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v5.json').read_text())
    material['policy'] = policy.model_dump(mode='json')
    if review:
        candidate, _ = wire.candidate(empty(), material)
        material['candidate'] = candidate.model_dump(mode='json')
        material['denominator'] = list(material['before'])
    adapter = claim_management_model_adapter('opus' if review else 'gpt', policy)
    return wire.scoped_adapter(adapter, material), adapter


def issue():
    return dict(target_key='evidence_steps/E', component='speaker', category='attribution', reason='unconfirmed attribution',
        acceptance_condition='verify actual speaker', human_question=None, citations=change()['citations'],
        affected_target_keys=['claims/C'], affected_write_keys=[], impact_reason='target depends on this evidence')


@pytest.mark.parametrize('review', [False, True])
@pytest.mark.parametrize('bad', ['evidence_target', 'neighbour_target', 'unknown_object', 'unwritten', 'unknown_finding'])
def test_schema_rejects_wrong_domains_before_generation(material, review, bad):
    adapter, _ = scoped(material, review=review)
    f = issue()
    if bad == 'evidence_target': f['affected_target_keys'].append('evidence_steps/E')
    if bad == 'neighbour_target': f['affected_target_keys'].append('claims/NEIGHBOUR')
    if bad == 'unknown_object': f['target_key'] = 'evidence_steps/ABSENT'
    if bad == 'unwritten': f['affected_write_keys'] = ['source_fragments/F']
    raw = {'complete_review': True, 'findings': [f], 'prior_findings': []} if review else {**empty(), 'findings': [f]}
    if bad == 'unknown_finding':
        raw['prior_findings' if review else 'finding_resolutions'] = [dict(
            finding_id='invented', disposition='persistent', reason='unchanged', citations=change()['citations'],
            affected_target_keys=['claims/C'], affected_write_keys=[], impact_reason='depends on evidence')]
    with pytest.raises(ValueError): adapter._response_model.model_validate(raw)


def test_schema_keeps_required_evidence_distinct_from_claim_and_actual_write(material):
    adapter, base = scoped(material, review=True)
    raw = {'complete_review': True, 'findings': [issue()], 'prior_findings': []}
    assert adapter._response_model.model_validate(raw).findings[0].target_key == 'evidence_steps/E'
    assert wire.scoped_adapter(adapter, material) is adapter
    assert adapter.generation_config_sha256 != base.generation_config_sha256
    assert base._response_model is wire.FindingsReview
    # The actual outbound schema, size and encoding proof use the SAME enums.
    captured = {}
    class Client:
        def generate_json(self, **kwargs): captured.update(kwargs); return raw
    adapter._client = Client()
    assert adapter.generate(material) == raw
    schema = captured['json_schema']['schema']
    finding = schema['$defs']['ScopedFinding']['properties']
    assert finding['affected_target_keys']['items'] == {'const': 'claims/C', 'type': 'string'}
    assert finding['affected_write_keys']['maxItems'] == 0
    assert adapter.request_encoding_proof(material)['wire_request_bytes'] == adapter.request_bytes(material)


def test_legacy_output_schema_is_unchanged(material):
    material['policy']['model_output_contract'] = wire.CONTRACT
    policy = cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v4.json').read_text())
    adapter = claim_management_model_adapter('opus', policy)
    schema, identity = adapter._response_model.model_json_schema(), adapter.generation_config_sha256
    assert wire.scoped_adapter(adapter, material) is adapter
    assert adapter._response_model.model_json_schema() == schema and adapter.generation_config_sha256 == identity


def test_v2_resolves_projected_request_and_does_not_grant_readonly_writes(material):
    material['request'] = deepcopy(material['unit_request'])
    material['request']['permissions'] = []
    material['unit_request'] = {'__claim_request_ref__': 'request'}
    adapter, _ = scoped(material)
    assert adapter._response_model.model_validate(empty()).changes == []
    with pytest.raises(ValueError): adapter._response_model.model_validate({**empty(), 'changes': [change()]})


@pytest.mark.parametrize('changed', [False, True])
def test_scoped_contract_still_completes_exact_sql_transaction_and_readback(shared, changed):
    store, request, root, tmp = shared
    policy = cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v5.json').read_text())
    policy.initial_candidate = 'proposal'
    path = tmp/'success-policy.json'; path.write_text(policy.model_dump_json())
    request.policy = cm.source.FileBinding.model_validate(binding(path))
    authorize(request, {'required_maintenance_keys': ['evidence_steps/E']})
    c = change('evidence_steps/E', {'statement': '甲是假设，不是断言。'})
    c['citations'][0]['exact_quote'] = '甲是假设，不是断言。'
    ports = SyntheticPorts(store, tmp, proposal=lambda p: {**empty(), 'changes': [c] if changed else []},
        opus=lambda p: {'complete_review': True, 'findings': [], 'prior_findings': []})
    before = store.get_record_state('claims', 'C')
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'completed', result
    assert all(store.claim_management_qualification(cid)['ready'] for cid in ['A', 'B'])
    assert store.get_record_state('claims', 'C') == before
    assert execute_claim_management(store, request, root=root, ports=ports) == result
    assert ports.calls == ['gpt', 'opus']


@pytest.mark.parametrize('role', ['gpt', 'opus'])
def test_returned_invalid_answer_is_spent_unapproved_and_never_recalled(shared, role):
    store, request, root, tmp = shared
    policy = cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v5.json').read_text())
    policy.initial_candidate = 'proposal'
    path = tmp/'scoped-policy.json'; path.write_text(policy.model_dump_json())
    request.policy = cm.source.FileBinding.model_validate(binding(path))
    authorize(request, {'required_maintenance_keys': ['evidence_steps/E']})
    def propose(packet): return {'bad': 'shape'} if role == 'gpt' else empty()
    def reject(packet):
        # Same failure as the saved real Opus answer: Evidence in Claim targets.
        f = issue(); f['affected_target_keys'] = ['claims/A', 'evidence_steps/E']
        return {'complete_review': True, 'findings': [f], 'prior_findings': []}
    ports = SyntheticPorts(store, tmp, proposal=propose, opus=reject)
    before = {key: store.get_record_state(*key.split('/', 1)) for key in ['claims/A', 'claims/B', 'evidence_steps/E']}
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'paused' and result['reason'] == 'engineering_required', result
    parent = store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    rid = next(iter(parent['latest_rounds'].values()))
    slot = parent['call_reservations'][rid+':'+role]
    assert slot['state'] == 'started' and slot['rejection']['response_state'] == 'returned_rejected'
    assert slot['rejection']['accepted'] is False
    child = store.get_cvp_group(rid)
    assert not child['change_set_ids'] and not child.get('opus_approved')
    assert ('candidate' if role == 'gpt' else 'opus') not in child.get('stage_results', {})
    calls = list(ports.calls)
    assert execute_claim_management(store, request, root=root, ports=ports) == result
    assert ports.calls == calls == (['gpt'] if role == 'gpt' else ['gpt', 'opus'])
    assert before == {key: store.get_record_state(*key.split('/', 1)) for key in before}
    # Recording is idempotent; tampered raw cannot be recorded or release a call.
    assert store.record_claim_management_returned_rejection(rid, role=role, detail=result['detail']) == slot['rejection']
    raw_path = root/rid/(role+'.raw.json'); raw = read(raw_path)
    raw.pop('artifact_sha256'); raw['response'] = {'forged': True}; write(raw_path, raw, immutable=False)
    with pytest.raises(ValueError): store.record_claim_management_returned_rejection(rid, role=role, detail=result['detail'])
    assert store.get_cvp_group(parent['run_id']) == parent
