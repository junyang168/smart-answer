"""Program-selected identities and reviewed retirement use the common SQL path."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from backend.pipeline import claim_management as cm, claim_management_intake as intake
from backend.pipeline import claim_management_batch as batch, claim_model_output as wire
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.api.canonical_repository.claim_identity_allocation import reserve
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts, seed_claims
from backend.tests.test_claim_management_intake import pass_review


@pytest.fixture
def shared(action):
    store, _, _, _, tmp, _ = action
    store.apply_plan(store.plan_package({'package_id': 'retirement-and-creation',
        'claims': [{'claim_id': c, 'statement': '甲是假设。', 'claim_type': 'exegesis',
                    'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['E']} for c in ['C', 'D']],
        'evidence_steps': [{'evidence_step_id': 'E', 'statement': '甲是假设。', 'produced_claim_ids': ['C', 'D'],
            'source_fragment_ids': ['F'], 'support_eligibility': 'eligible_candidate', 'speaker': 'professor', 'stance': 'asserted'}],
        'source_fragments': [{'fragment_id': 'F', 'source_id': 'S', 'paragraph_key': 'S0001', 'verbatim_excerpt': '甲是假设，不是断言。'}],
        'questions': [{'question_id': 'Q', 'text': '甲是什么？', 'question_type': 'rhetorical',
                       'source_fragment_id': 'F', 'answer_state': 'answered', 'answer_claim_ids': ['C', 'D']}],
        'claim_relations': [{'claim_relation_id': 'R', 'from_id': 'C', 'to_id': 'D', 'relation_type': 'supports'}]}))
    return store, tmp / 'managed-update'


CITATION = {'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}


def decisions(packet):
    return [{'finding_id': fid, 'disposition': 'resolved', 'reason': 'synthetic full-source resolution',
        'citations': [CITATION], 'affected_target_keys': ['claims/C'], 'affected_write_keys': [],
        'impact_reason': 'target objection settled'} for fid in packet['unresolved']]


def first_objection(packet, component):
    result = pass_review(packet)
    if packet['unresolved']:
        result['prior_findings'] = decisions(packet)
    else:
        result['findings'] = [{'target_key': 'claims/C', 'component': component, 'category': 'mapping',
            'citations': [CITATION], 'reason': 'synthetic source issue', 'acceptance_condition': component,
            'affected_target_keys': ['claims/C'], 'affected_write_keys': [], 'impact_reason': 'target needs maintenance'}]
    return result


def test_allocation_sees_nonlocal_retired_ids_and_other_collections(action):
    store, _, _, _, tmp, _ = action
    seed_claims(store, ['C-CL001', 'C-CL005'])
    store.apply_plan(store.plan_retirement([('claims', 'C-CL005')], reason='synthetic retired identity', package_id='retired-name'))
    doc = deepcopy(store.get_record_state('source_documents', 'S')['payload'])
    doc['source_id'] = 'C-CL006'
    store.apply_plan(store.plan_package({'package_id': 'foreign-collection-name', 'source_documents': [doc]}))
    kwargs = dict(action_id='first-allocation', source_id='S', target_id='C', root=tmp / 'first')
    assert reserve(store, **kwargs) == 'C-CL007'
    assert reserve(store, **kwargs) == 'C-CL007'
    assert reserve(store, action_id='second-allocation', source_id='S', target_id='C', root=tmp / 'second') == 'C-CL008'
    with pytest.raises(ValueError, match='cannot change'):
        reserve(store, **{**kwargs, 'root': tmp / 'moved'})


def test_concurrent_program_allocations_cannot_reserve_the_same_id(action):
    from concurrent.futures import ThreadPoolExecutor
    store, _, _, _, tmp, _ = action
    def allocate(number):
        return reserve(store, action_id='parallel-' + str(number), source_id='S-' + str(number),
                       target_id='C', root=tmp / str(number))
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert set(executor.map(allocate, [1, 2])) == {'C-CL001', 'C-CL002'}


@pytest.mark.parametrize('bad', ['unreserved_identity', 'readonly_retirement', 'modify_and_retire'])
def test_compact_v4_does_not_allow_identity_or_retirement_scope_expansion(shared, bad):
    store, root = shared
    saved = intake.prepare(['C'], root, instruction='Update', store=store)
    request, _ = next(iter(batch.load(saved['manifest']['path'], root / 'batch', store=store)[1].values()))
    records = store.read_claim_context([('claims', 'C')])
    before = {cm.source.key(*key): state for key, state in records.items()}
    goal = cm.original_goal(request, before)
    materials = {'policy': json.loads(Path(request.policy.path).read_text()), 'unit_request': request.model_dump(mode='json'),
                 'before': before, 'original_goal': goal, 'replacements': {},
                 'sources': cm.load_authority(request, source_documents=cm.source_documents(before))[1]}
    raw = {'changes': [], 'creations': [], 'retirements': [], 'findings': [], 'finding_resolutions': []}
    if bad == 'unreserved_identity':
        raw['creations'] = [{'collection': 'claims', 'object_id': 'ARBITRARY', 'reason': 'bad identity',
                              'citations': [CITATION], 'fields_json': json.dumps({'statement': '甲是假设。'})}]
    else:
        raw['retirements'] = [{'record_key': 'claims/D' if bad == 'readonly_retirement' else 'claims/C',
                              'reason': 'synthetic retirement', 'citations': [CITATION]}]
        if bad == 'modify_and_retire':
            raw['changes'] = [{'record_key': 'claims/C', 'fields_json': json.dumps({'statement': 'different'}),
                               'reason': 'cannot rewrite retirement history', 'citations': [CITATION]}]
    with pytest.raises(ValueError):
        candidate, _ = wire.candidate(raw, materials)
        cm.validate_candidate(request, before, candidate, materials['sources'])
    assert not store.get_record_state('claims', 'C')['retired']
    assert not store.get_record_state('claims', 'D')['retired']


def test_list_update_creates_reserved_claim_and_links_its_evidence_and_question(shared, monkeypatch):
    store, root = shared
    calls = []
    new_ids = []
    def proposal(packet):
        cid = packet['original_goal']['claim_creation_allocations']['S']
        new_ids.append(cid)
        return {'changes': [
            {'record_key': 'evidence_steps/E', 'fields_json': json.dumps({'produced_claim_ids': ['C', 'D', cid]}),
             'reason': 'own new judgment', 'citations': [CITATION]},
            {'record_key': 'questions/Q', 'fields_json': json.dumps({'answer_claim_ids': ['C', 'D', cid]}),
             'reason': 'direct answer retained with the old answers', 'citations': [CITATION]}],
            'creations': [{'collection': 'claims', 'object_id': cid, 'fields_json': json.dumps({
                'statement': '甲是假设，不是断言。', 'claim_type': 'exegesis', 'evidence_step_ids': ['E'],
                'occurrences': [{'source_id': 'S', 'anchors': []}]}), 'reason': 'independent own-source judgment', 'citations': [CITATION]}],
            'retirements': [], 'findings': [], 'finding_resolutions': decisions(packet)}
    def execute(manifest, output):
        for request, execution in batch.load(manifest, output, store=store)[1].values():
            port = SyntheticPorts(store, execution, proposal=proposal, opus=lambda packet: first_objection(packet, 'missing judgment'))
            calls.append(port)
            result = execute_claim_management(store, request, root=execution, ports=port)
            assert result['status'] == 'completed', result
    monkeypatch.setattr(batch, 'run', execute)
    result = intake.run(['C'], root, instruction='Extract the omitted independent judgment', store=store)
    cid = new_ids[0]
    assert result['all_actions_completed'] and result['created_keys'] == ['claims/' + cid]
    assert store.claim_management_qualification(cid)['ready']
    assert cid in store.get_record_state('evidence_steps', 'E')['payload']['produced_claim_ids']
    assert store.get_record_state('questions', 'Q')['payload']['answer_claim_ids'] == ['C', 'D', cid]
    assert calls[0].calls == ['opus', 'gpt', 'opus']
    assert intake.run(['C'], root, instruction='Extract the omitted independent judgment', store=store) == result
    assert len(calls) == 1


@pytest.mark.parametrize('late_failure', [False, True])
def test_retirement_evidence_and_relations_commit_together_and_resume(shared, monkeypatch, late_failure):
    store, root = shared
    before = {key: store.get_record_state(*key) for key in [('claims', 'C'), ('evidence_steps', 'E'), ('questions', 'Q'), ('claim_relations', 'R')]}
    prepared = intake.prepare(['C'], root, instruction='Retire the rejected quotation, preserve the other owner', store=store)
    request, execution = next(iter(batch.load(prepared['manifest']['path'], root / 'batch', store=store)[1].values()))
    def proposal(packet):
        return {'changes': [
            {'record_key': 'evidence_steps/E', 'fields_json': json.dumps({'produced_claim_ids': ['D']}),
             'reason': 'preserve active owner', 'citations': [CITATION]},
            {'record_key': 'questions/Q', 'fields_json': json.dumps({'answer_claim_ids': ['D']}),
             'reason': 'preserve the actual answer', 'citations': [CITATION]}],
            'creations': [], 'retirements': [{'record_key': key, 'reason': 'professor rejects this statement',
                'citations': [CITATION]} for key in ['claims/C', 'claim_relations/R']],
            'findings': [], 'finding_resolutions': decisions(packet)}
    def review(packet):
        if packet['unresolved']:
            assert 'retirement_reason' in packet['required_questions']
            assert 'claims/C' not in packet['own_evidence_bindings']
        return first_objection(packet, 'retire rejected quotation')
    ports = SyntheticPorts(store, execution, proposal=proposal, opus=review)
    original = store._assert_transaction_edges_and_reviews
    if late_failure:
        def fail(*args, **kwargs): raise RuntimeError('synthetic failure after planned retirement')
        monkeypatch.setattr(store, '_assert_transaction_edges_and_reviews', fail)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    if late_failure:
        assert result['status'] == 'paused'
        assert {key: store.get_record_state(*key) for key in before} == before
        monkeypatch.setattr(store, '_assert_transaction_edges_and_reviews', original)
        result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status'] == 'completed', result
    assert store.get_record_state('claims', 'C')['retired']
    assert store.get_record_state('claims', 'C')['payload'] == before['claims', 'C']['payload']
    assert store.get_record_state('claim_relations', 'R')['retired']
    assert store.get_record_state('evidence_steps', 'E')['payload']['produced_claim_ids'] == ['D']
    assert store.get_record_state('questions', 'Q')['payload']['answer_claim_ids'] == ['D']
    result = intake.run(['C'], root, instruction='Retire the rejected quotation, preserve the other owner', store=store)
    assert result['all_actions_completed'] and result['retired_keys'] == ['claims/C']
    assert not (result['claims']['C']['qualification'] or {}).get('ready')
    assert ports.calls == ['opus', 'gpt', 'opus']
