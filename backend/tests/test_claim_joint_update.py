"""Joint ordinary Update on disposable SQL; synthetic replies are routing tests."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

import pytest

from backend.pipeline import claim_management as cm
from backend.pipeline import claim_joint_update as joint
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_flow import binding, pins
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts, operation_request, seed_claims, passing, rejected
from backend.tests.test_claim_management_human_source_follow_up import authorize


def bind(request, root, *, cap=2, required=(), legacy=False, initial=None):
    policy = cm.Policy.model_validate_json(cm.DEFAULT_POLICY.read_text())
    if not legacy:
        policy.joint_update_contract = joint.CONTRACT
        policy.joint_update_max_targets = cap
    policy.initial_candidate = initial
    path = root / ('joint-policy-' + uuid.uuid4().hex + '.json')
    path.write_text(policy.model_dump_json())
    request.policy = cm.source.FileBinding.model_validate(binding(path))
    authorize(request, {'required_maintenance_keys': list(required)})
    return policy


def joint_pass(packet):
    result = passing(packet)
    for resolution in result['prior_findings']:
        resolution.update(affected_target_keys=[], affected_write_keys=[], impact_reason='resolved by source-supported correction')
    return result


def joint_reject(packet, key='claims/A', *, affected=None, writes=(), category='content'):
    result = rejected(packet, key=key, category=category)
    for finding in result['findings']:
        finding.update(affected_target_keys=affected if affected is not None else [key],
            affected_write_keys=list(writes), impact_reason='synthetic exact impact judgment')
    return result


def resolve(packet, *, disposition='resolved', affected=(), writes=()):
    return [cm.FindingResolution(finding_id=fid, disposition=disposition,
        reason='synthetic own-source adjudication', citations=[cm.Citation.model_validate(f['citations'][0])],
        affected_target_keys=list(affected), affected_write_keys=list(writes), impact_reason='synthetic exact impact judgment')
        for fid, f in packet['unresolved'].items()]


def update(packet, keys=('evidence_steps/E',)):
    candidate = cm.existing_candidate(packet['before'])
    for key in keys:
        payload = deepcopy(packet['before'][key]['payload'])
        payload['statement'] = '甲是假设，不是断言。'
        col, oid = cm.source.split_key(key)
        candidate.records.append(cm.source.Row(collection=col, object_id=oid, payload=payload))
        disposition = next(d for d in candidate.dispositions if d.record_key == key)
        disposition.action = 'update'
        disposition.citations = [cm.Citation(source_id='S', paragraph_key='S0001', exact_quote='甲是假设，不是断言。')]
    candidate.finding_resolutions = resolve(packet)
    return candidate.model_dump(mode='json')


@pytest.fixture
def shared(action):
    store, _, _, _, tmp, _ = action
    store.apply_plan(store.plan_package({'package_id': 'joint-shared',
        'claims': [{'claim_id': cid, 'statement': '甲是假设。' + cid, 'claim_type': 'exegesis',
                    'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['E']} for cid in ['A', 'B', 'C']],
        'evidence_steps': [{'evidence_step_id': 'E', 'statement': '甲是假设。', 'produced_claim_ids': ['A', 'B', 'C']}]}))
    request, root = operation_request(action, 'Update', ['A', 'B'])
    request.permissions.append(cm.source.Permission(collection='evidence_steps', object_id='E', fields=['statement']))
    bind(request, tmp, required=['evidence_steps/E'])
    return store, request, root, tmp


def parent(store, request):
    return store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))


@pytest.mark.parametrize('mode', ['both_retain', 'e_only', 'claim_and_e'])
def test_joint_common_postimage_exact_maintenance_and_no_replay(shared, mode):
    store, request, root, tmp = shared
    before = {cid: store.get_record_state('claims', cid) for cid in ['A', 'B', 'C']}
    def opus(packet):
        assert set(['claims/A', 'claims/B', 'claims/C', 'evidence_steps/E']) <= set(packet['denominator'])
        assert packet['original_goal']['required_maintenance_keys'] == ['evidence_steps/E']
        return joint_pass(packet) if mode == 'both_retain' or packet['round_number'] else joint_reject(packet)
    keys = ['evidence_steps/E', 'claims/A'] if mode == 'claim_and_e' else ['evidence_steps/E']
    ports = SyntheticPorts(store, tmp, opus=opus, proposal=lambda packet: update(packet, keys))
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'completed', result
    saved = parent(store, request)
    assert len(saved['original_goal']['units']) == len(saved['latest_rounds']) == 1
    assert saved['original_goal']['joint_update_contract'] == joint.CONTRACT
    assert ports.calls == (['opus'] if mode == 'both_retain' else ['opus', 'gpt', 'opus'])
    child = store.get_cvp_group(next(iter(saved['latest_rounds'].values())))
    if mode != 'both_retain':
        assert len(child['change_set_ids']) == 1
        history = store.get_change_history(child['change_set_ids'][0])
        assert [(o['collection'], o['object_id']) for o in history['operations']].count(('evidence_steps', 'E')) == 1
    assert store.get_record_state('claims', 'B') == before['B']
    assert store.get_record_state('claims', 'C') == before['C']
    assert store.claim_management_qualification('A')['ready']
    assert store.claim_management_qualification('B')['ready']
    assert store.claim_management_qualification('C') is None
    assert execute_claim_management(store, request, root=root, ports=ports) == result
    assert ports.calls.count('gpt') == (0 if mode == 'both_retain' else 1)


@pytest.mark.parametrize('bad', ['legacy', 'over_capacity', 'creation', 'hidden_claim', 'unreachable_permission',
                                  'unreachable_required', 'missing_required_list'])
def test_joint_admission_is_durable_zero_calls_zero_business_writes(shared, bad):
    store, request, root, tmp = shared
    if bad == 'creation':
        request.creation_permissions = [cm.CreationPermission(collection='claims', source_id='S', fields=['statement'])]
    elif bad == 'hidden_claim':
        request.permissions.append(cm.source.Permission(collection='claims', object_id='C', fields=['statement']))
    elif bad == 'unreachable_permission':
        seed_claims(store, ['UNRELATED'])
        request.permissions.append(cm.source.Permission(collection='claims', object_id='UNRELATED', fields=['statement']))
    required = ['evidence_steps/E']
    if bad == 'unreachable_required':
        required = ['claims/ABSENT']
    bind(request, tmp, cap=1 if bad == 'over_capacity' else 2, legacy=bad == 'legacy', required=required)
    if bad == 'missing_required_list':
        authorize(request)
    before = store.get_record_state('evidence_steps', 'E')
    ports = SyntheticPorts(store, tmp)
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'paused' and result['reason'] == 'engineering_required', result
    saved = parent(store, request)
    assert saved['admission_refusal'] and not saved['latest_rounds'] and not saved['call_reservations']
    assert store.get_record_state('evidence_steps', 'E') == before
    assert execute_claim_management(store, request, root=root, ports=ports) == result
    assert ports.calls == []
    with pytest.raises(RuntimeError, match='admission refused'):
        store.prepare_claim_management_round(saved['run_id'], unit_id=next(iter(saved['original_goal']['units'])),
            round_number=0, materials={}, roles=['opus'])


def test_targets_are_not_write_permissions_and_empty_required_is_explicit(shared):
    store, request, root, tmp = shared
    request.permissions = []
    bind(request, tmp, required=[])
    ports = SyntheticPorts(store, tmp, opus=joint_pass)
    assert execute_claim_management(store, request, root=root, ports=ports)['status'] == 'completed'
    saved = parent(store, request)
    assert saved['original_goal']['required_maintenance_keys'] == []
    child = store.get_cvp_group(next(iter(saved['latest_rounds'].values())))
    candidate = cm.Candidate.model_validate(update({**child['materials'], 'unresolved': {}}, ['claims/A']))
    with pytest.raises(ValueError, match='read-only'):
        cm.validate_candidate(request, child['materials']['before'], candidate, child['materials']['sources'])


@pytest.mark.parametrize('bad', ['retire', 'restore', 'create', 'merge', 'omit_disposition', 'omit_verdict', 'required_rejected'])
def test_joint_required_and_existing_only_contract(shared, bad):
    store, request, root, tmp = shared
    bind(request, tmp, initial='proposal')
    def propose(packet):
        candidate = cm.existing_candidate(packet['before'])
        candidate.finding_resolutions = []
        if bad in {'retire', 'restore', 'create'}:
            next(d for d in candidate.dispositions if d.record_key == 'evidence_steps/E').action = bad
        elif bad == 'merge':
            candidate.operation = 'Merge'
        elif bad == 'omit_disposition':
            candidate.dispositions = [d for d in candidate.dispositions if d.record_key != 'evidence_steps/E']
        return candidate.model_dump(mode='json')
    def opus(packet):
        result = joint_pass(packet)
        if bad == 'omit_verdict':
            result['verdicts'] = [v for v in result['verdicts'] if v['record_key'] != 'evidence_steps/E']
        elif bad == 'required_rejected':
            result = joint_reject(packet, 'evidence_steps/E', affected=[])
        return result
    ports = SyntheticPorts(store, tmp, proposal=propose, opus=opus)
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'paused', result
    assert not store.claim_management_qualification('A') or not store.claim_management_qualification('A')['ready']
    assert store.get_record_state('evidence_steps', 'E')['revision'] == 1


@pytest.mark.parametrize('failure', ['late_validation', 'committed_reply_lost'])
def test_joint_transaction_rolls_back_all_or_recovers_one_committed_changeset(shared, monkeypatch, failure):
    store, request, root, tmp = shared
    ports = SyntheticPorts(store, tmp, opus=lambda p: joint_pass(p) if p['round_number'] else joint_reject(p),
        proposal=lambda p: update(p, ['claims/A', 'claims/B', 'evidence_steps/E']))
    method = '_assert_transaction_edges_and_reviews' if failure == 'late_validation' else 'apply_plan'
    original = getattr(store, method)
    def fail(*args, **kwargs):
        if failure == 'committed_reply_lost':
            original(*args, **kwargs)
        raise RuntimeError('synthetic joint transaction failure')
    monkeypatch.setattr(store, method, fail)
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'paused', result
    expected = 1 if failure == 'late_validation' else 2
    assert [store.get_record_state(col, oid)['revision'] for col, oid in [('claims', 'A'), ('claims', 'B'), ('evidence_steps', 'E')]] == [expected] * 3
    monkeypatch.setattr(store, method, original)
    assert execute_claim_management(store, request, root=root, ports=ports)['status'] == 'completed'
    assert ports.calls == ['opus', 'gpt', 'opus']
    assert store.claim_management_qualification('A')['ready'] and store.claim_management_qualification('B')['ready']
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM wang_knowledge.change_sets WHERE source_kind=%s', (cm.VERSION,)).fetchone() == (1,)


@pytest.mark.parametrize('case', ['unrelated', 'caused_by_e', 'scope_disagreement', 'missing_write_scope', 'potential_not_actual'])
def test_readonly_owner_scope_requires_gpt_and_distinguishes_actual_write_impact(shared, case):
    store, request, root, tmp = shared
    bind(request, tmp, required=[], initial='proposal')
    def propose(packet):
        candidate = cm.Candidate.model_validate(update(packet))
        candidate.finding_resolutions = resolve(packet, disposition='persistent')
        return candidate.model_dump(mode='json')
    def opus(packet):
        if not packet['round_number']:
            result = joint_reject(packet, 'claims/C', affected=[], writes=['evidence_steps/E'] if case == 'caused_by_e' else [])
            if case == 'missing_write_scope':
                result['findings'][0].pop('affected_write_keys')
            if case == 'potential_not_actual':
                result['findings'][0]['affected_write_keys'] = ['claims/A']
            return result
        result = joint_pass(packet)
        for r in result['prior_findings']:
            r.update(disposition='persistent', affected_target_keys=[], affected_write_keys=['evidence_steps/E'] if case == 'scope_disagreement' else [])
        for v in result['verdicts']:
            if v['record_key'] == 'claims/C':
                v.update(approved=False, problems=['independent context issue remains'])
        return result
    ports = SyntheticPorts(store, tmp, proposal=propose, opus=opus)
    result = execute_claim_management(store, request, root=root, ports=ports)
    if case in {'unrelated', 'caused_by_e'}:
        assert result['status'] == 'completed', result
        assert result['context_findings']
        assert store.claim_management_qualification('A')['ready']
        assert not store.claim_management_qualification('C')['ready']
        assert ports.calls == ['gpt', 'opus', 'gpt', 'opus']
    else:
        assert result['status'] == 'paused' and result['reason'] == 'engineering_required', result
        assert store.get_record_state('evidence_steps', 'E')['revision'] == 1


@pytest.mark.parametrize('race', ['owner', 'relation', 'shared_fragment'])
def test_joint_unbound_reverse_dependency_blocks_before_commit(shared, monkeypatch, race):
    store, request, root, tmp = shared
    original = store.review_cvp_group
    def inject(*args, **kwargs):
        if race == 'owner':
            store.apply_plan(store.plan_package({'package_id': 'racing-owner', 'questions': [
                {'question_id': 'Q', 'question': 'synthetic', 'answer_claim_ids': ['A']}]}))
        elif race == 'relation':
            store.apply_plan(store.plan_package({'package_id': 'racing-relation', 'knowledge_relations': [
                {'relation_id': 'R', 'from_id': 'A', 'to_id': 'C', 'relation_type': 'supports'}]}))
        else:
            store.apply_plan(store.plan_package({'package_id': 'racing-fragment', 'source_fragments': [
                {'fragment_id': 'F', 'source_id': 'S', 'paragraph_key': 'S0001', 'verbatim_excerpt': '甲是假设，不是断言。'}],
                'observations': [{'observation_id': 'O', 'statement': '甲是假设。', 'source_fragment_ids': ['F']}],
                'evidence_steps': [{**store.get_record_state('evidence_steps', 'E')['payload'], 'source_fragment_ids': ['F']}]}))
        return original(*args, **kwargs)
    monkeypatch.setattr(store, 'review_cvp_group', inject)
    ports = SyntheticPorts(store, tmp, opus=lambda p: joint_pass(p) if p['round_number'] else joint_reject(p), proposal=update)
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'paused', result
    assert store.get_record_state('claims', 'A')['revision'] == 1
    assert store.get_record_state('evidence_steps', 'E')['payload']['statement'] == '甲是假设。'


def test_preflight_full_pins_transitive_conflicts_and_readonly_no_auto_targets(shared, action):
    store, request, root, tmp = shared
    # E is read-only but the two writable Claim rows are in each other's pins.
    request.permissions = [p for p in request.permissions if p.collection == 'claims']
    bind(request, tmp, required=[])
    report = joint.preflight(store, request)
    assert [g['target_keys'] for g in report['groups']] == [['claims/A', 'claims/B']]
    assert report['groups'][0]['status'] == 'prepared_scope_only'
    assert any(p['collection'] == 'claims' and p['object_id'] == 'C' for p in report['read_pins'])
    request.permissions = []
    bind(request, tmp, required=[])
    assert len(joint.preflight(store, request)['groups']) == 2
    request.target_keys = ['claims/A', 'claims/B', 'claims/C']
    request.permissions = [cm.source.Permission(collection='claims', object_id=cid, fields=['statement']) for cid in ['A', 'B', 'C']]
    bind(request, tmp, required=[], cap=2)
    report = joint.preflight(store, request)
    assert len(report['groups']) == 1 and report['groups'][0]['status'] == 'engineering_required'
    assert report['model_calls'] == report['content_writes'] == 0


def test_same_source_independent_claims_share_only_reads(action):
    store, _, _, _, tmp, _ = action
    ids = ['C' + str(n) for n in range(30)]
    seed_claims(store, ids)
    request, root = operation_request(action, 'Update', ids)
    bind(request, tmp, required=[], cap=2)
    report = joint.preflight(store, request)
    assert len(report['groups']) == 30
    assert all(g['status'] == 'prepared_scope_only' and len(g['target_keys']) == 1 for g in report['groups'])


def test_batch_summary_rechecks_current_qualification_not_historical_completed(shared):
    store, request, root, tmp = shared
    assert execute_claim_management(store, request, root=root, ports=SyntheticPorts(store, tmp, opus=joint_pass))['status'] == 'completed'
    store.apply_plan(store.plan_package({'package_id': 'later-e-write', 'evidence_steps': [
        {**store.get_record_state('evidence_steps', 'E')['payload'], 'statement': '甲是假设，不是断言。'}]}))
    summary = joint.current_batch_results(store, [request.action_id])[0]
    assert summary['historical_result']['status'] == 'completed'
    assert all(not q['ready'] for q in summary['targets'].values())
    assert summary['affected_consumers'] == {'C': None}


def test_new_and_legacy_serialization_and_schemas_are_separate(shared):
    store, request, root, tmp = shared
    policy = cm.Policy.model_validate_json(cm.DEFAULT_POLICY.read_text())
    assert 'joint_update_contract' not in policy.model_dump() and 'joint_update_max_targets' not in policy.model_dump()
    assert 'affected_write_keys' not in json.dumps(cm.Review.model_json_schema())
    assert 'affected_write_keys' in json.dumps(cm.JointReview.model_json_schema())
    finding = cm.Finding.model_validate({'target_key': 'claims/A', 'component': 'statement', 'category': 'content',
        'citations': [{'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}],
        'reason': 'synthetic', 'acceptance_condition': 'synthetic'})
    assert 'affected_write_keys' not in finding.model_dump()
    assert cm.Candidate.model_validate(cm.Candidate(records=[], dispositions=[], findings=[finding]).model_dump()).digest == cm.Candidate(records=[], dispositions=[], findings=[finding]).digest
    for cap in [0, -1, True, 1.5]:
        with pytest.raises(ValueError):
            cm.Policy.model_validate({**policy.model_dump(), 'joint_update_max_targets': cap})


def test_joint_human_follow_up_preserves_all_targets_required_items_and_saved_candidate(shared):
    store, request, root, tmp = shared
    def uncertain(packet):
        if not packet['round_number']:
            return joint_reject(packet, category='source_uncertain')
        result = joint_pass(packet)
        for r in result['prior_findings']:
            r.update(disposition='persistent', affected_target_keys=['claims/A'], affected_write_keys=[])
        for v in result['verdicts']:
            if v['record_key'] == 'claims/A':
                v.update(approved=False, problems=['source uncertainty remains'])
        return result
    def proposed(packet):
        candidate = cm.Candidate.model_validate(update(packet))
        candidate.finding_resolutions = resolve(packet, disposition='persistent', affected=['claims/A'])
        return candidate.model_dump(mode='json')
    ports = SyntheticPorts(store, tmp, opus=uncertain, proposal=proposed)
    assert execute_claim_management(store, request, root=root, ports=ports)['reason'] == 'human_required'
    previous = parent(store, request)
    old_calls = deepcopy(previous['call_reservations'])
    fid, finding = next(iter(previous['findings'].items()))
    verification = {'schema_version': 'wang_claim_human_source_verification_v1', 'verified_by': 'user',
        'human_content_approval': False, 'predecessor_action_id': request.action_id,
        'source_bindings': request.model_dump(mode='json')['source_bindings'], 'user_confirmation': 'synthetic speaker confirmation',
        'answers': [{'finding_id': fid, 'question': finding['human_question'], 'answer': 'professor speaking', 'citation': finding['citations'][0]}]}
    answer = tmp / 'joint-human-answer.json'
    answer.write_text(json.dumps(verification))
    successor = request.model_copy(deep=True)
    successor.action_id += '-human'
    successor.predecessor_action_id = request.action_id
    successor.authorization = cm.source.FileBinding(path=str(tmp / 'joint-human-direction.json'), file_sha256='a' * 64)
    policy = bind(successor, tmp, required=['evidence_steps/E'], initial='proposal')
    policy.budget.gpt_calls = policy.budget.opus_calls = 1
    Path(successor.policy.path).write_text(policy.model_dump_json())
    successor.policy = cm.source.FileBinding.model_validate(binding(Path(successor.policy.path)))
    authorize(successor, {'required_maintenance_keys': ['evidence_steps/E'], 'human_source_verification': binding(answer)})
    def continue_saved(packet):
        assert packet['original_goal']['target_keys'] == ['claims/A', 'claims/B']
        assert packet['original_goal']['required_maintenance_keys'] == ['evidence_steps/E']
        candidate = cm.Candidate.model_validate(packet['previous_candidate'])
        assert candidate.records[0].object_id == 'E'
        candidate.finding_resolutions = resolve(packet)
        return candidate.model_dump(mode='json')
    new_ports = SyntheticPorts(store, tmp, proposal=continue_saved, opus=joint_pass)
    output = root / 'human'
    result = execute_claim_management(store, successor, root=output, ports=new_ports)
    assert result['status'] == 'completed', result
    assert new_ports.calls == ['gpt', 'opus']
    assert store.claim_management_qualification('A')['ready'] and store.claim_management_qualification('B')['ready']
    assert parent(store, request)['call_reservations'] == old_calls
    saved = parent(store, successor)
    unit = next(iter(saved['original_goal']['units']))
    child = store.get_cvp_group(saved['latest_rounds'][unit])
    # The old None == None hole must fail even with an otherwise exact scope.
    continuation = deepcopy(saved['continuation'])
    continuation['previous_candidates'].pop(unit)
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=jsonb_set(metadata,'{continuation}',%s::jsonb) WHERE run_id=%s",
            (json.dumps(continuation), saved['run_id']))
    frozen = deepcopy(child['materials'])
    frozen['continuation'] = continuation
    frozen['previous_candidate'] = None
    with pytest.raises(RuntimeError, match='saved candidate'):
        store.prepare_claim_management_round(saved['run_id'], unit_id=unit, round_number=1, materials=frozen, roles=['gpt', 'opus'])


@pytest.mark.parametrize('bad', ['missing_required', 'missing_disposition', 'missing_verdict', 'missing_readback'])
def test_joint_finish_cannot_use_target_active_as_maintenance_completion(shared, bad):
    store, request, root, tmp = shared
    assert execute_claim_management(store, request, root=root, ports=SyntheticPorts(store, tmp, opus=joint_pass))['status'] == 'completed'
    saved = parent(store, request)
    child = store.get_cvp_group(next(iter(saved['latest_rounds'].values())))
    goal = deepcopy(saved['original_goal'])
    observed = {k: cm.model_state(v) for k, v in store.read_claim_context([('claims', 'A')]).items()}
    observed = {cm.source.key(*k): v for k, v in observed.items()}
    if bad == 'missing_required':
        goal.pop('required_maintenance_keys')
    elif bad == 'missing_disposition':
        child['candidate']['dispositions'] = [d for d in child['candidate']['dispositions'] if d['record_key'] != 'evidence_steps/E']
    elif bad == 'missing_readback':
        observed.pop('evidence_steps/E')
    else:
        goal['required_maintenance_keys'].append('evidence_steps/ABSENT')
    with pytest.raises(ValueError):
        joint.verify_completion(goal, child, observed)


def test_joint_full_before_after_denominator_keeps_removed_owner_and_rejects_new_unbound_endpoint(shared):
    store, request, root, tmp = shared
    records = store.read_claim_context([('claims', 'A')])
    before = {cm.source.key(*k): cm.model_state(v) for k, v in records.items()}
    # Removing C from the proposed E must not remove C from the review.
    candidate = cm.Candidate.model_validate(update({'before': before, 'unresolved': {}}))
    candidate.records[0].payload['produced_claim_ids'] = ['A', 'B']
    assert 'claims/C' in cm.audit_keys(request, before, candidate)
    candidate.records[0].payload['produced_claim_ids'] = ['A', 'B', 'UNBOUND']
    with pytest.raises(ValueError, match='missing dependency'):
        cm.audit_keys(request, before, candidate)


def test_joint_optional_gemini_uses_same_contract_without_enabling_default(shared):
    store, request, root, tmp = shared
    policy = bind(request, tmp)
    policy.independent_audit = True
    policy.budget.gemini_calls = 2
    Path(request.policy.path).write_text(policy.model_dump_json())
    request.policy = cm.source.FileBinding.model_validate(binding(Path(request.policy.path)))
    authorize(request, {'required_maintenance_keys': []})
    def independent(packet):
        assert packet['joint_update_writes'] == ['evidence_steps/E']
        assert packet['original_goal']['joint_update_contract'] == joint.CONTRACT
        return joint_pass(packet)
    ports = SyntheticPorts(store, tmp, opus=lambda p: joint_pass(p) if p['round_number'] else joint_reject(p),
        proposal=update, gemini=independent)
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['opus', 'gpt', 'opus', 'gemini']
    assert store.claim_management_qualification('A')['ready'] and store.claim_management_qualification('B')['ready']
    assert not cm.Policy.model_validate_json(cm.DEFAULT_POLICY.read_text()).independent_audit_required


def test_legacy_provider_schema_hashes_stay_exact():
    # Captured from d543548 before this implementation, not regenerated by the
    # new validator: saved provider intents must remain verifiable byte for byte.
    assert cm.sha256_json(cm.JsonCandidate.model_json_schema()) == 'f2dfd1fd2ed700c516f418666b5af468fd14029b3e80e09ed13e34ec75698d3c'
    assert cm.sha256_json(cm.AdjudicatingJsonCandidate.model_json_schema()) == 'd10de9b992054a63d3c48c3b7bc692c4781a7677bf6d09d9955b1218cc52be83'
    assert cm.sha256_json(cm.Review.model_json_schema()) == '694b5a85dfbd683622b4af77f43d1bd283ff523c3c8ba706b81c249bfd3f188b'


def test_supported_capacity_is_not_hardcoded_to_two(shared):
    store, request, root, tmp = shared
    request.target_keys.append('claims/C')
    request.permissions.append(cm.source.Permission(collection='claims', object_id='C', fields=['statement']))
    bind(request, tmp, required=[], cap=3)
    assert execute_claim_management(store, request, root=root, ports=SyntheticPorts(store, tmp, opus=joint_pass))['status'] == 'completed'
    assert all(store.claim_management_qualification(cid)['ready'] for cid in ['A', 'B', 'C'])


def test_joint_missing_permission_object_is_a_saved_refusal_not_a_dropped_grant(shared):
    store, request, root, tmp = shared
    request.permissions.append(cm.source.Permission(collection='observations', object_id='ABSENT', fields=['statement']))
    bind(request, tmp)
    ports = SyntheticPorts(store, tmp)
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['reason'] == 'engineering_required' and parent(store, request)['admission_refusal']
    assert not ports.calls and not parent(store, request)['latest_rounds']


def test_joint_contract_capacity_one_is_usable_with_exact_empty_maintenance(action):
    store, _, _, _, tmp, _ = action
    seed_claims(store, ['A'])
    request, root = operation_request(action, 'Update', ['A'])
    bind(request, tmp, required=[], cap=1)
    assert execute_claim_management(store, request, root=root, ports=SyntheticPorts(store, tmp, opus=joint_pass))['status'] == 'completed'
    assert parent(store, request)['original_goal']['required_maintenance_keys'] == []
    shipped = cm.Policy.model_validate_json((Path(cm.__file__).parent / 'policies/wang_claim_management_joint_update_v1.json').read_text())
    assert shipped.joint_update_max_targets == 1


def test_preflight_shared_fragment_joins_only_selected_targets_and_assigns_required_once(shared, action):
    store, request, root, tmp = shared
    store.apply_plan(store.plan_package({'package_id': 'joint-shared-fragment',
        'source_fragments': [{'fragment_id': 'F', 'source_id': 'S', 'paragraph_key': 'S0001', 'verbatim_excerpt': '甲是假设，不是断言。'}],
        'evidence_steps': [{**store.get_record_state('evidence_steps', 'E')['payload'], 'source_fragment_ids': ['F']},
            {'evidence_step_id': 'E2', 'statement': '甲是假设。', 'source_fragment_ids': ['F'], 'produced_claim_ids': ['D']}],
        'claims': [{'claim_id': 'D', 'statement': '甲是假设。', 'claim_type': 'exegesis',
            'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['E2']}]}))
    seed_claims(store, ['X'])
    request.target_keys = ['claims/A', 'claims/D', 'claims/X']
    request.permissions = [cm.source.Permission(collection='source_fragments', object_id='F', fields=['verbatim_excerpt'])]
    bind(request, tmp, required=['source_fragments/F'])
    report = joint.preflight(store, request)
    assert [g['target_keys'] for g in report['groups']] == [['claims/A', 'claims/D'], ['claims/X']]
    assert sum('source_fragments/F' in g['required_maintenance_keys'] for g in report['groups']) == 1
    assert all('claims/B' not in g['target_keys'] and 'claims/C' not in g['target_keys'] for g in report['groups'])
    request.permissions = []
    bind(request, tmp, required=['source_fragments/F'])
    report = joint.preflight(store, request)
    assert len(report['groups']) == 3
    assert sum('source_fragments/F' in g['required_maintenance_keys'] for g in report['groups']) == 1


def test_already_bound_legacy_multitarget_action_is_not_rejected_retroactively(shared, monkeypatch):
    store, request, root, tmp = shared
    bind(request, tmp, legacy=True)
    records = store.read_claim_context([('claims', 'A'), ('claims', 'B')])
    before = {cm.source.key(*k): cm.model_state(v) for k, v in records.items()}
    # Emulate an action prepared by the pre-refactor implementation. Only new
    # admission is bypassed here; all actual saved stages use the old contract.
    with monkeypatch.context() as patch:
        patch.setattr(joint, 'admission_error', lambda *args: None)
        saved = store.prepare_claim_management_action(request, goal=cm.original_goal(request, before),
            read_pins=pins(records), root=root)
    assert 'joint_update_contract' not in saved['original_goal']
    ports = SyntheticPorts(store, tmp)
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['opus']


def test_incidental_transaction_peer_keeps_pin_without_expanding_content_scope(shared, monkeypatch):
    store, request, root, tmp = shared
    seed_claims(store, ['X'])
    store.apply_plan(store.plan_package({'package_id': 'incidental-peer-question', 'questions': [
        {'question_id': 'Q', 'question': 'unrelated context', 'answer_claim_ids': ['X']}]}))
    reader = store.read_claim_context
    def with_incidental_peer(keys):
        result = reader(keys)
        # The existing CVP reader also pins other members without making them
        # content roots. Their independent reverse closure is not this audit.
        result['claims', 'X'] = store.get_record_state('claims', 'X')
        return result
    monkeypatch.setattr(store, 'read_claim_context', with_incidental_peer)
    ports = SyntheticPorts(store, tmp, opus=lambda p: joint_pass(p) if p['round_number'] else joint_reject(p), proposal=update)
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'completed', result
    saved = parent(store, request)
    child = store.get_cvp_group(next(iter(saved['latest_rounds'].values())))
    assert 'claims/X' in child['materials']['transaction_before']
    assert 'claims/X' not in child['materials']['before']
    assert store.claim_management_qualification('A')['ready']


def test_impact_scope_order_is_not_a_model_disagreement(shared):
    store, request, root, tmp = shared
    previous = {'issue': joint_reject({'candidate_sha256': 'a' * 64, 'denominator': [],
        'unresolved': {}, 'required_questions': [], 'original_goal': {'required_questions': []}}, affected=['claims/A', 'claims/B'])['findings'][0]}
    candidate = cm.existing_candidate({})
    candidate.finding_resolutions = resolve({'unresolved': previous}, disposition='persistent', affected=['claims/A', 'claims/B'])
    review = cm.Review(proposal_sha256=candidate.digest, verdicts=[], findings=[], answered_questions=[],
        prior_findings=resolve({'unresolved': previous}, disposition='persistent', affected=['claims/B', 'claims/A']))
    joint.validate_scope_agreement(candidate, review, previous, request)


def test_pre_round_engineering_pause_is_not_a_permanent_admission_refusal(shared):
    store, request, root, tmp = shared
    records = store.read_claim_context([('claims', 'A'), ('claims', 'B')])
    before = {cm.source.key(*k): cm.model_state(v) for k, v in records.items()}
    saved = store.prepare_claim_management_action(request, goal=cm.original_goal(request, before), read_pins=pins(records), root=root)
    store.pause_claim_management_action(saved['run_id'], reason='engineering_required', detail='synthetic pre-round interruption')
    ports = SyntheticPorts(store, tmp, opus=joint_pass)
    assert execute_claim_management(store, request, root=root, ports=ports)['status'] == 'completed'
    assert ports.calls == ['opus']


def test_direct_joint_completion_rechecks_physical_sources(shared):
    store, request, root, tmp = shared
    assert execute_claim_management(store, request, root=root, ports=SyntheticPorts(store, tmp, opus=joint_pass))['status'] == 'completed'
    saved = parent(store, request)
    path = Path(request.source_bindings['S'].path)
    path.write_text(json.dumps({'script': [{'text': '甲是假设，不是断言。'}, {'text': '新增限定。'}]}))
    with pytest.raises(ValueError):
        store.finish_claim_management_action(saved['run_id'])
    assert parent(store, request)['result']['status'] == 'completed'  # historical fact
    assert not store.claim_management_qualification('A')['ready']


def test_preflight_uses_registered_source_type_for_complete_notes_manuscript(action):
    store, _, _, _, tmp, _ = action
    manuscript = tmp / 'professor-notes.md'
    manuscript.write_text('# 赦罪的权柄\n\n人子在地上有赦罪的权柄。\n\n医治是可见的凭据。\n')
    store.apply_plan(store.plan_package({'package_id': 'joint-notes-preflight',
        'source_documents': [{'source_id': 'N', 'source_type': 'notes_manuscript', 'title': 'professor notes',
            'source_path': str(manuscript), 'source_file_sha256': binding(manuscript)['file_sha256']}],
        'claims': [{'claim_id': cid, 'statement': '人子有赦罪的权柄。', 'claim_type': 'exegesis',
            'occurrences': [{'source_id': 'N', 'anchors': []}], 'evidence_step_ids': ['NE']} for cid in ['NA', 'NB']],
        'evidence_steps': [{'evidence_step_id': 'NE', 'statement': '人子有赦罪的权柄。', 'produced_claim_ids': ['NA', 'NB']}]}))
    request, root = operation_request(action, 'Update', ['NA', 'NB'])
    request.source_bindings = {'N': cm.source.FileBinding.model_validate(binding(manuscript))}
    bind(request, tmp, required=['evidence_steps/NE'])
    report = joint.preflight(store, request)
    assert len(report['groups']) == 1 and report['groups'][0]['status'] == 'prepared_scope_only'
    assert report['model_calls'] == report['content_writes'] == 0
    assert any(p['collection'] == 'source_documents' and p['object_id'] == 'N' for p in report['read_pins'])
    # A correct parser must still reject physical source version drift.
    manuscript.write_text(manuscript.read_text() + '\n新增限定。\n')
    with pytest.raises(ValueError):
        joint.preflight(store, request)


@pytest.mark.parametrize('target_count', [2, 3])
def test_ordinary_joint_policy_enables_two_and_stops_larger_groups_before_calls(shared, target_count):
    store, request, root, tmp = shared
    if target_count == 3:
        request.target_keys.append('claims/C')
        request.permissions.append(cm.source.Permission(collection='claims', object_id='C', fields=['statement']))
    policy_path = Path(cm.__file__).parent / 'policies/wang_claim_management_joint_update_v2.json'
    policy = cm.Policy.model_validate_json(policy_path.read_text())
    assert policy.joint_update_contract == joint.CONTRACT and policy.joint_update_max_targets == 2
    assert not policy.independent_audit_required and policy.budget.gemini_calls == 0
    request.policy = cm.source.FileBinding.model_validate(binding(policy_path))
    authorize(request, {'required_maintenance_keys': ['evidence_steps/E']})
    ports = SyntheticPorts(store, tmp, opus=joint_pass)
    result = execute_claim_management(store, request, root=root, ports=ports)
    if target_count == 2:
        assert result['status'] == 'completed', result
        assert ports.calls == ['opus']
        assert store.claim_management_qualification('A')['ready'] and store.claim_management_qualification('B')['ready']
    else:
        assert result['reason'] == 'engineering_required', result
        assert ports.calls == [] and parent(store, request)['admission_refusal']


@pytest.fixture
def readonly_finding(shared):
    store, request, root, tmp = shared
    store.apply_plan(store.plan_package({'package_id': 'readonly-finding',
        'observations': [{'observation_id': oid, 'statement': '甲是假设。'} for oid in ['O', 'OTHER']],
        'knowledge_relations': [{'relation_id': oid, 'from_id': obs, 'to_id': 'E',
            'relation_type': 'supports', 'reason': 'synthetic context'} for oid, obs in [('R', 'O'), ('R2', 'OTHER')]]}))
    policy = bind(request, tmp, required=['evidence_steps/E'], initial='proposal')
    policy.budget.max_revisions = 0
    path = tmp/'readonly-policy.json'; path.write_text(policy.model_dump_json())
    request.policy = cm.source.FileBinding.model_validate(binding(path))
    authorize(request, {'required_maintenance_keys': ['evidence_steps/E']})
    def proposal(packet):
        candidate = cm.existing_candidate(packet['before'])
        candidate.findings = [cm.Finding(target_key='observations/O', component='statement', category='mapping',
            reason='readonly observation needs source-supported correction', acceptance_condition='correct O',
            citations=[cm.Citation(source_id='S', paragraph_key='S0001', exact_quote='甲是假设，不是断言。')],
            affected_target_keys=['claims/A'], affected_write_keys=[], impact_reason='A depends on O')]
        return candidate.model_dump(mode='json')
    def opus(packet):
        result = joint_pass(packet)
        for item in result['prior_findings']:
            item.update(disposition='persistent', affected_target_keys=['claims/A'], affected_write_keys=[])
        for verdict in result['verdicts']:
            if verdict['record_key'] == 'observations/O':
                verdict.update(approved=False, problems=['readonly correction needs authority'])
        return result
    ports = SyntheticPorts(store, tmp, proposal=proposal, opus=opus)
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['reason'] == 'budget_exhausted', result
    assert ports.calls == ['gpt', 'opus']
    previous = parent(store, request)
    successor = request.model_copy(deep=True)
    successor.action_id += '-permission-follow-up'
    successor.predecessor_action_id = request.action_id
    successor.permissions.append(cm.source.Permission(collection='observations', object_id='O', fields=['statement']))
    successor.authorization = cm.source.FileBinding(path=str(tmp/'permission-follow-up.json'), file_sha256='a'*64)
    reduced = policy.model_copy(deep=True)
    reduced.budget.gpt_calls = reduced.budget.opus_calls = 1
    path = tmp/'remaining-permission-policy.json'; path.write_text(reduced.model_dump_json())
    successor.policy = cm.source.FileBinding.model_validate(binding(path))
    authorize(successor, {'required_maintenance_keys': ['evidence_steps/E']})
    return store, request, previous, successor, reduced, tmp


def test_joint_readonly_finding_permission_uses_only_original_remaining_budget(readonly_finding):
    store, original, previous, successor, policy, tmp = readonly_finding
    proof = cm.continuation_budget(previous, successor, policy)
    assert proof['remaining_generations'] == {'gpt': 1, 'opus': 1, 'gemini': 0}
    calls = deepcopy(previous['call_reservations'])
    ports = SyntheticPorts(store, tmp, proposal=lambda packet: update(packet, keys=['observations/O']), opus=joint_pass)
    result = execute_claim_management(store, successor, root=tmp/'permission-follow-up', ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['gpt', 'opus']
    assert store.get_record_state('observations', 'O')['payload']['statement'] == '甲是假设，不是断言。'
    assert parent(store, original)['call_reservations'] == calls
    assert store.claim_management_qualification('A')['ready'] and store.claim_management_qualification('B')['ready']


@pytest.mark.parametrize('fault', ['unreported_object', 'drop_old_grant', 'hidden_claim', 'increase_budget'])
def test_joint_readonly_finding_permission_cannot_expand_unrelated_authority(readonly_finding, fault):
    _, _, previous, successor, policy, _ = readonly_finding
    if fault == 'unreported_object':
        successor.permissions.append(cm.source.Permission(collection='observations', object_id='OTHER', fields=['statement']))
    elif fault == 'drop_old_grant':
        successor.permissions = [p for p in successor.permissions if p.key != 'evidence_steps/E']
    elif fault == 'hidden_claim':
        successor.permissions.append(cm.source.Permission(collection='claims', object_id='C', fields=['statement']))
    else:
        policy.budget.gpt_calls += 1
    authorize(successor, {'required_maintenance_keys': ['evidence_steps/E']})
    with pytest.raises(ValueError, match='authority|reset or increase'):
        cm.continuation_budget(previous, successor, policy)
