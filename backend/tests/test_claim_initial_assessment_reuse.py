"""An unfinished operation may retain exact individual initial approvals."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm, claim_management_intake as intake
from backend.pipeline import claim_review_units as units, claim_management_batch as batch
from backend.pipeline import claim_evidence_review as evidence
from backend.pipeline.claim_model_output import review as expand_review
from backend.pipeline.cvp_sql_actions import execute_claim_management, claim_management_review_packet
from backend.pipeline.cvp_sql_commit import binding
from backend.api.canonical_repository.claim_identity_allocation import reserve, allocated_ids
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_management_intake import pass_review
from backend.tests.test_claim_management_intake import claims


@pytest.fixture
def interrupted_unit(action, request, monkeypatch):
    legacy_aliases = getattr(request, 'param', False) is True
    owner_context = getattr(request, 'param', False) == 'owner_refs'
    store, _, _, _, tmp, _ = action
    ids = ['C' + str(n) for n in range(9)]
    store.apply_plan(store.plan_package({'package_id': 'initial-nine',
        'claims': [{'claim_id': c, 'statement': '甲是假设。', 'claim_type': 'exegesis',
                    'evidence_step_ids': ['E' + c], 'occurrences': [{'source_id': 'S', 'anchors': []}]} for c in ids],
        'evidence_steps': [{'evidence_step_id': 'E' + c, 'statement': '甲是假设。',
            'produced_claim_ids': [c], 'source_fragment_ids': ['F' + c],
            'speaker': 'professor', 'stance': 'asserted', 'support_eligibility': 'eligible_candidate'} for c in ids],
        'source_fragments': [{'fragment_id': 'F' + c, 'source_id': 'S', 'paragraph_key': 'S0001',
                             'verbatim_excerpt': '甲是假设，不是断言。'} for c in ids]}))
    if owner_context:
        store.apply_plan(store.plan_package({'package_id': 'readonly-neighbour-owner',
            'claims': [{'claim_id': cid, 'statement': '甲是假设。', 'claim_type': 'exegesis',
                'evidence_step_ids': ['ED'], 'occurrences': [{'source_id': 'S', 'anchors': []}]} for cid in ['D', 'B']],
            'evidence_steps': [{'evidence_step_id': 'ED', 'statement': '甲是假设。',
                'source_fragment_ids': ['FD'], 'produced_claim_ids': ['D', 'B']}],
            'source_fragments': [{'fragment_id': 'FD', 'source_id': 'S', 'paragraph_key': 'S0001',
                                 'verbatim_excerpt': '甲是假设，不是断言。'}],
            'claim_relations': [{'claim_relation_id': 'R', 'from_id': 'C8', 'to_id': 'D', 'relation_type': 'supports'}]}))
    policy = cm.Policy.model_validate_json(intake.DEFAULT_POLICY.read_text())
    policy.review_unit_contract = units.CONTRACT
    policy.joint_update_max_targets = 10
    policy.budget.gpt_calls = 1
    path = tmp / 'bounded.json'; path.write_text(policy.model_dump_json())
    prepared = units.prepare(ids, tmp / 'unit', instruction='Review nine existing Claims',
                             policy=path, creation_count=2, store=store)
    request, root = next(iter(batch.load(prepared['prepared_update']['manifest']['path'], tmp / 'batch', store=store)[1].values()))
    # Simulate a historical request prepared before per-round reuse existed.
    legacy_auth = json.loads(Path(request.authorization.path).read_text())
    legacy_auth.pop('reuse_initial_claim_assessments', None)
    legacy_auth.pop('include_existing_evidence_owners', None)
    legacy_file = tmp / 'legacy-authority.json'; legacy_file.write_text(json.dumps(legacy_auth))
    request.authorization = cm.source.FileBinding.model_validate(binding(legacy_file))
    citation = {'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}
    def propose(packet):
        new = allocated_ids(packet['original_goal']['claim_creation_allocations'])
        result = {'changes': [{'record_key': 'evidence_steps/EC0',
            'fields_json': json.dumps({'produced_claim_ids': ['C0', *new]}), 'reason': 'preserve old and new owners', 'citations': [citation]}],
            'creations': [{'collection': 'claims', 'object_id': cid, 'fields_json': json.dumps({
                'statement': text, 'claim_type': 'exegesis', 'evidence_step_ids': ['EC0'],
                'occurrences': [{'source_id': 'S', 'anchors': []}]}), 'reason': 'independent judgment', 'citations': [citation]}
                for cid, text in zip(new, ['甲是假设。', '甲不是断言。'])],
            'retirements': [], 'findings': [], 'finding_resolutions': [
                {'finding_id': fid, 'disposition': 'resolved', 'reason': 'both judgments have Claims',
                 'citations': [citation], 'affected_target_keys': ['claims/C0'],
                 'affected_write_keys': ['evidence_steps/EC0'], 'impact_reason': 'reciprocal evidence owners'}
                for fid in packet['unresolved']]}
        if legacy_aliases:
            result['changes'][0]['fields_json'] = json.dumps({'produced_claim_ids': ['C0', new[1]]})
            first = json.loads(result['creations'][0]['fields_json'])
            first['evidence_step_ids'] = ['TEMP-E']
            first['occurrences'][0]['anchors'] = [{'paragraph_key': 'S0001', 'evidence_id': 'TEMP-E',
                                                  'proposed_highlight': {'text': citation['exact_quote']}}]
            result['creations'][0]['fields_json'] = json.dumps(first)
            result['creations'].append({'collection': 'evidence_steps', 'object_id': 'TEMP-E',
                'fields_json': json.dumps({'statement': citation['exact_quote'], 'source_fragment_ids': ['FC0'],
                    'produced_claim_ids': [new[0]], 'speaker': 'professor', 'stance': 'asserted',
                    'support_eligibility': 'eligible_candidate'}), 'reason': 'own evidence', 'citations': [citation]})
        return result
    def first(packet):
        result = pass_review(packet)
        result['findings'] = [{'target_key': 'evidence_steps/EC0', 'component': 'independent_judgment',
            'category': 'mapping', 'reason': 'two independent judgments need Claims',
            'acceptance_condition': 'create both judgments', 'citations': [citation],
            'affected_target_keys': ['claims/C0'], 'affected_write_keys': [], 'impact_reason': 'own data'}]
        return result
    ports = SyntheticPorts(store, root, proposal=propose, opus=first)
    with monkeypatch.context() as patch:
        if legacy_aliases:
            from backend.pipeline import claim_metadata_projection
            patch.setattr(claim_metadata_projection, 'EVIDENCE_REFERENCES', {'evidence_step_ids'})
        stopped = execute_claim_management(store, request, root=root, ports=ports)
    assert stopped['detail'] == 'active review unit exceeds ten Claims; split the unit before review'
    assert ports.calls == ['opus', 'gpt']
    parent = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    initial = next(v['round_id'] for v in parent['call_reservations'].values() if v['role'] == 'opus' and v['state'] == 'completed')
    old_calls = deepcopy(parent['call_reservations'])
    store.pause_claim_management_action(parent['run_id'], reason='budget_exhausted', detail='old GPT consumed, owner permits one bounded correction pair')
    parent = store.get_cvp_group(parent['run_id'])
    parent = {k:v for k,v in parent.items() if k not in {'run_id', 'status', 'error'}}
    successor = request.model_copy(deep=True)
    successor.action_id += '-initial-reuse'; successor.predecessor_action_id = request.action_id
    output = tmp / 'successor'
    policy.initial_candidate = 'proposal'; policy.budget.gpt_calls = policy.budget.opus_calls = 1
    pp = tmp / 'follow-policy.json'; pp.write_text(policy.model_dump_json())
    successor.policy = cm.source.FileBinding.model_validate(binding(pp))
    context = store.read_claim_context([('claims', c) for c in ids])
    before = {cm.source.key(*k): cm.model_state(v) for k,v in context.items()}
    auth = json.loads(Path(request.authorization.path).read_text())
    auth.update(request=successor.model_dump(mode='json', exclude={'authorization', 'policy'}),
                policy=binding(pp), reuse_initial_review_round=initial)
    auth['claim_creation_allocations'] = {'S': reserve(store, action_id=successor.action_id, source_id='S',
        target_id='C0', root=output, count=2)}
    auth['owner_call_extension'] = {'basis': 'remaining_findings', 'predecessor_action_id': request.action_id,
        'prior_metadata_sha256': cm.sha256_json(parent), 'budget': policy.budget.model_dump(mode='json'),
        'authorization_quote': 'repair capacity using unchanged individual initial passes, one extra pair',
        'current_objects_sha256': cm.sha256_json(before), 'related_action_metadata_sha256': {}}
    ap = tmp / 'follow-authority.json'; ap.write_text(json.dumps(auth))
    successor.authorization = cm.source.FileBinding.model_validate(binding(ap))
    return store, request, successor, output, root, initial, old_calls, propose


def test_initial_passes_are_references_but_do_not_complete_the_pending_operation(interrupted_unit):
    store, original, request, output, root, initial, old_calls, propose = interrupted_unit
    assert store.claim_management_qualification('C1') is None
    raw = next(root.glob(initial + '/opus.raw.json')); saved = raw.read_bytes()
    def final(packet):
        assert set(packet['own_evidence_bindings']) == {'claims/C0', *('claims/' + c for c in allocated_ids(packet['original_goal']['claim_creation_allocations']))}
        assert packet['initial_review_references']['claim_keys'] == ['claims/C' + str(n) for n in range(1, 9)]
        assert len(packet['claim_scope']['content_claim_keys']) == 3
        assert 'initial_assessment_reuse' not in packet  # no old full material duplicated into the model
        result = pass_review(packet)
        result['prior_findings'] = [{'finding_id': fid, 'disposition': 'resolved', 'reason': 'both created',
            'citations': [f['citations'][0]], 'affected_target_keys': ['claims/C0'],
            'affected_write_keys': ['evidence_steps/EC0'], 'impact_reason': 'owners retained'} for fid, f in packet['unresolved'].items()]
        return result
    ports = SyntheticPorts(store, output, proposal=propose, opus=final)
    result = execute_claim_management(store, request, root=output, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['gpt', 'opus'] and raw.read_bytes() == saved
    assert all(store.claim_management_qualification('C' + str(n))['ready'] for n in range(9))
    old = store.get_cvp_group('CMA-' + cm.sha256_json(original.action_id))
    assert all(old['call_reservations'][k] == v for k,v in old_calls.items() if v['state'] != 'planned')
    new = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    for key in new['created_keys']:
        if key.startswith('claims/'):
            assert store.claim_management_qualification(key.split('/', 1)[1])['ready']


@pytest.mark.parametrize('change', ['claim', 'evidence', 'fragment', 'source', 'finding', 'raw'])
def test_initial_reuse_refuses_changed_basis_new_objections_and_tampered_proof(interrupted_unit, change):
    store, _, request, _, root, initial, _, _ = interrupted_unit
    proof = evidence.freeze_initial_review_reuse(store, request)
    from backend.pipeline.source_reconciliation_runner import body, read
    old = body(read(root / initial / 'materials.json'))
    m = deepcopy(old); m['initial_assessment_reuse'] = proof
    m['unit_request']['predecessor_action_id'] = proof['review_action_id']
    candidate = cm.existing_candidate(m['before'])
    assert 'claims/C1' in evidence.reused_assessments(m, candidate)
    if change == 'source':
        m['sources']['S']['paragraphs']['S0001']['text'] += ' changed'
    elif change == 'raw':
        Path(proof['raw']['path']).write_text('{}')
    elif change == 'finding':
        m['unresolved']['fresh'] = {'target_key': 'claims/C1', 'affected_target_keys': []}
    else:
        key = {'claim': 'claims/C1', 'evidence': 'evidence_steps/EC1', 'fragment': 'source_fragments/FC1'}[change]
        payload = deepcopy(m['before'][key]['payload'])
        payload['verbatim_excerpt' if change == 'fragment' else 'statement'] += ' changed'
        col, oid = key.split('/', 1)
        candidate.records.append(cm.source.Row(collection=col, object_id=oid, payload=payload))
    if change in {'source', 'raw'}:
        with pytest.raises(ValueError): evidence.reused_assessments(m, candidate)
    else:
        assert 'claims/C1' not in evidence.reused_assessments(m, candidate)


def test_reused_claim_can_be_reopened_but_cannot_be_given_a_second_full_assessment(interrupted_unit):
    store, _, request, _, root, initial, _, _ = interrupted_unit
    from backend.pipeline.source_reconciliation_runner import body, read
    m = body(read(root / initial / 'materials.json'))
    m['initial_assessment_reuse'] = evidence.freeze_initial_review_reuse(store, request)
    m['unit_request']['predecessor_action_id'] = request.predecessor_action_id
    candidate = cm.existing_candidate(m['before'])
    packet = claim_management_review_packet(m, candidate)
    raw = pass_review(packet)
    decoded = expand_review(raw, m, candidate)
    assert 'not a new model vote' in next(v.reason for v in decoded.verdicts if v.record_key == 'claims/C1')
    duplicate = deepcopy(raw)
    duplicate['binding_assessments'].append(evidence.reused_assessments(m, candidate)['claims/C1'].model_dump(mode='json'))
    with pytest.raises(ValueError, match='references, not fresh assessments'):
        expand_review(duplicate, m, candidate)
    raw['findings'] = [{'target_key': 'claims/C1', 'component': 'statement', 'category': 'content',
        'reason': 'synthetic serious contradiction noticed in reference context',
        'acceptance_condition': 'resolve this contradiction',
        'citations': [{'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}],
        'affected_target_keys': ['claims/C1'], 'affected_write_keys': [], 'impact_reason': 'target meaning'}]
    decoded = expand_review(raw, m, candidate)
    assert not next(v.approved for v in decoded.verdicts if v.record_key == 'claims/C1')


def test_new_bounded_request_automatically_reuses_initial_passes_in_the_original_action(claims):
    store, root = claims
    policy = cm.Policy.model_validate_json(intake.DEFAULT_POLICY.read_text())
    policy.review_unit_contract = units.CONTRACT; policy.joint_update_max_targets = 10
    path = root.parent / 'auto-policy.json'; path.write_text(policy.model_dump_json())
    prepared = units.prepare(['C', 'D'], root, instruction='Refresh both, preserving unchanged individual passes',
                             store=store, policy=path)
    request, output = next(iter(batch.load(prepared['prepared_update']['manifest']['path'], root / 'batch', store=store)[1].values()))
    citation = {'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}
    def propose(packet):
        cid = allocated_ids(packet['original_goal']['claim_creation_allocations'])[0]
        return {'changes': [{'record_key': 'evidence_steps/EC', 'fields_json': json.dumps({'produced_claim_ids': ['C', cid]}),
                             'reason': 'preserve both owners', 'citations': [citation]}],
                'creations': [{'collection': 'claims', 'object_id': cid, 'fields_json': json.dumps({
                    'statement': '甲不是断言。', 'claim_type': 'exegesis', 'evidence_step_ids': ['EC'],
                    'occurrences': [{'source_id': 'S', 'anchors': []}]}), 'reason': 'missing judgment', 'citations': [citation]}],
                'retirements': [], 'findings': [], 'finding_resolutions': [{'finding_id': fid, 'disposition': 'resolved',
                    'reason': 'judgment created', 'citations': [citation], 'affected_target_keys': ['claims/C'],
                    'affected_write_keys': ['evidence_steps/EC'], 'impact_reason': 'own reciprocal evidence'} for fid in packet['unresolved']]}
    def review(packet):
        result = pass_review(packet)
        if not packet['round_number']:
            assert set(packet['own_evidence_bindings']) == {'claims/C', 'claims/D'}
            result['findings'] = [{'target_key': 'evidence_steps/EC', 'component': 'judgment', 'category': 'mapping',
                'reason': 'missing independent judgment', 'acceptance_condition': 'create it', 'citations': [citation],
                'affected_target_keys': ['claims/C'], 'affected_write_keys': [], 'impact_reason': 'own data'}]
        else:
            assert packet['initial_review_references']['claim_keys'] == ['claims/D']
            assert 'claims/D' not in packet['own_evidence_bindings']
            result['prior_findings'] = [{'finding_id': fid, 'disposition': 'resolved', 'reason': 'judgment created',
                'citations': [citation], 'affected_target_keys': ['claims/C'], 'affected_write_keys': ['evidence_steps/EC'],
                'impact_reason': 'own evidence'} for fid in packet['unresolved']]
        return result
    ports = SyntheticPorts(store, output, proposal=propose, opus=review)
    outcome = execute_claim_management(store, request, root=output, ports=ports)
    assert outcome['status'] == 'completed', outcome
    assert ports.calls == ['opus', 'gpt', 'opus']
    assert store.claim_management_qualification('C')['ready'] and store.claim_management_qualification('D')['ready']


@pytest.mark.parametrize('interrupted_unit', [False, True, 'owner_refs'], indirect=True)
def test_capacity_stop_uses_saved_gpt_and_only_the_original_unused_opus(interrupted_unit):
    from backend.pipeline.claim_review_recovery import resume_saved_pre_review
    from backend.pipeline.source_reconciliation_runner import read, body
    store, request, _, _, root, _, _, _ = interrupted_unit
    parent_id = 'CMA-' + cm.sha256_json(request.action_id)
    parent = store.get_cvp_group(parent_id)
    rid = next(iter(parent['latest_rounds'].values()))
    saved = (root / rid / 'gpt.raw.json').read_bytes()
    calls = deepcopy(parent['call_reservations'])
    store.pause_claim_management_action(parent_id, reason='engineering_required',
        detail='active review unit exceeds ten Claims; split the unit before review')
    proof = resume_saved_pre_review(store, rid)
    assert resume_saved_pre_review(store, rid) == proof
    record = body(read(Path(proof['path'])))
    assert record['model_calls'] == 0 and record['call_reservations_unchanged']
    assert store.get_cvp_group(parent_id)['call_reservations'] == calls
    def final(packet):
        assert len(packet['own_evidence_bindings']) <= 10
        if 'evidence_steps/ED' in packet['before']:
            assert 'claims/B' not in packet['before']
            assert packet['existing_evidence_owner_claims']['claims/B']['payload']['statement'] == '甲是假设。'
        result = pass_review(packet)
        result['prior_findings'] = [{'finding_id': fid, 'disposition': 'resolved', 'reason': 'both judgments created',
            'citations': [f['citations'][0]], 'affected_target_keys': ['claims/C0'],
            'affected_write_keys': ['evidence_steps/EC0'], 'impact_reason': 'evidence owners'} for fid, f in packet['unresolved'].items()]
        return result
    ports = SyntheticPorts(store, root, proposal=lambda _: pytest.fail('saved GPT must not be repeated'), opus=final)
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['opus'] and (root / rid / 'gpt.raw.json').read_bytes() == saved
    assert all(store.claim_management_qualification('C' + str(n))['ready'] for n in range(9))
    after = store.get_cvp_group(parent_id)
    assert sum(s['role'] == 'gpt' and s['state'] == 'completed' for s in after['call_reservations'].values()) == 1
