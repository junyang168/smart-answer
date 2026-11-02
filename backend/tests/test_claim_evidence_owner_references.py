"""Creation checks can see declared owners without expanding Claim review."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm, claim_management_intake as intake
from backend.pipeline import claim_management_batch as batch, claim_review_units as units
from backend.pipeline.claim_direct_scope import evidence_owner_references
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_commit import binding
from backend.tests.test_claim_management_intake import claims, pass_review
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts


@pytest.mark.parametrize('enabled', [False, True])
def test_declared_owner_is_duplicate_context_only_and_has_no_new_review_or_write_rights(claims, enabled):
    store, root = claims
    evidence = deepcopy(store.get_record_state('evidence_steps', 'ED')['payload'])
    evidence['produced_claim_ids'] = ['D', 'B']
    store.apply_plan(store.plan_package({'package_id': 'shared-evidence-owner',
        'claims': [{'claim_id': 'B', 'claim_type': 'exegesis', 'statement': '甲不是断言。',
                    'evidence_step_ids': ['ED'], 'occurrences': [{'source_id': 'S', 'anchors': []}]}],
        'evidence_steps': [evidence], 'claim_relations': [
            {'claim_relation_id': 'R', 'from_id': 'C', 'to_id': 'D', 'relation_type': 'supports'}]}))
    policy = cm.Policy.model_validate_json(intake.DEFAULT_POLICY.read_text())
    policy.review_unit_contract = units.CONTRACT
    policy.joint_update_max_targets = 10
    policy.initial_candidate = 'proposal'
    path = root.parent / 'owner-lookup-policy.json'; path.write_text(policy.model_dump_json())
    prepared = units.prepare(['C'], root, instruction='Update C; verify declared owners before creating judgments',
                             store=store, policy=path)
    request, output = next(iter(batch.load(prepared['prepared_update']['manifest']['path'], root / 'batch', store=store)[1].values()))
    direction = json.loads(Path(request.authorization.path).read_text())
    if enabled:
        direction['include_existing_evidence_owners'] = True
    else:
        direction.pop('include_existing_evidence_owners', None)
    auth = root.parent / 'owner-lookup-authority.json'; auth.write_text(json.dumps(direction))
    request.authorization = cm.source.FileBinding.model_validate(binding(auth))
    original = store.get_record_state('claims', 'B')
    def check(packet):
        assert packet['action_obligations']['target_keys'] == ['claims/C']
        assert 'claims/B' not in packet['before']
        assert not any(p['object_id'] == 'B' for p in packet['request']['permissions'])
        if enabled:
            assert packet['existing_evidence_owner_claims']['claims/B']['payload']['statement'] == '甲不是断言。'
            assert set(packet['existing_evidence_owner_claims']) == {'claims/B'}
        else:
            assert 'existing_evidence_owner_claims' not in packet
    def propose(packet):
        check(packet)
        return {'changes': [], 'creations': [], 'retirements': [], 'findings': [], 'finding_resolutions': []}
    def review(packet):
        check(packet)
        assert set(packet['own_evidence_bindings']) == {'claims/C'}
        return pass_review(packet)
    ports = SyntheticPorts(store, output, proposal=propose, opus=review)
    result = execute_claim_management(store, request, root=output, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['gpt', 'opus']
    assert store.claim_management_qualification('C')['ready']
    assert store.claim_management_qualification('B') is None
    assert store.get_record_state('claims', 'B') == original


def test_owner_lookup_is_frozen_same_source_nonrecursive_and_cannot_be_forged():
    request = cm.Request(action_id='owners', operation='Update', allowed_operations=['Update'],
        target_keys=['claims/C'], source_bindings={'S': {'path': '/source', 'file_sha256': 'a' * 64}},
        policy={'path': '/policy', 'file_sha256': 'b' * 64},
        authorization={'path': '/authority', 'file_sha256': 'c' * 64})
    def state(cid, source='S'):
        return {'payload': {'claim_id': cid, 'statement': cid, 'occurrences': [{'source_id': source}]},
                'revision': 1, 'content_sha256': 'd' * 64, 'retired': False}
    before = {'claims/C': state('C'), 'evidence_steps/E': {'payload': {'produced_claim_ids': ['C', 'B', 'T']}}}
    transaction = {**before, 'claims/B': state('B'), 'claims/T': state('T', 'foreign'),
                   'claims/unrelated': state('unrelated')}
    refs = evidence_owner_references(request, before, transaction)
    assert refs == {'claims/B': transaction['claims/B']}
    refs['claims/B']['payload']['statement'] = 'forged'
    assert transaction['claims/B']['payload']['statement'] == 'B'
    materials = {'unit_request': request.model_dump(mode='json'), 'before': before,
                 'transaction_before': transaction, 'existing_evidence_owner_claims': refs}
    with pytest.raises(ValueError, match='reference facts drifted'):
        cm.model_packet(materials)
    transaction.pop('claims/B')
    with pytest.raises(ValueError, match='declared Evidence owner missing'):
        evidence_owner_references(request, before, transaction)
