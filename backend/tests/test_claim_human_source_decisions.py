"""Human answers survive an action and are reused without repeating the question."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm, claim_human_source as human
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runner import binding
from backend.api.canonical_repository.postgres_store import POSTGRES_APPLY_ADVISORY_LOCK_KEY, ChangeSetConflict
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import seed_claims, operation_request, SyntheticPorts, passing
from backend.tests.test_claim_management_human_source_follow_up import authorize
from backend.tests.test_verified_source_quote_sync import verified_transition


def facts(request, root, *, answer='The professor confirmed this is a hypothesis.', supersedes=None):
    record = {'schema_version': 'wang_claim_human_source_verification_v1', 'verified_by': 'user',
              'human_content_approval': False, 'source_bindings': request.model_dump(mode='json')['source_bindings'],
              'user_confirmation': '人工核听并确认',
              'answers': [{'finding_id': 'source-question', 'question': 'What did the professor mean here?',
                           'answer': answer, 'citation': {'source_id': 'S', 'paragraph_key': 'S0001',
                                                      'exact_quote': '甲是假设，不是断言。'}}]}
    if supersedes is not None:
        record['supersedes'] = {'S/S0001': supersedes}
    path = root / ('facts-' + cm.sha256_json(record)[:12] + '.json')
    path.write_text(json.dumps(record, ensure_ascii=False))
    sources = {'S': cm.source.source_snapshot(request.source_bindings['S'])}
    return binding(path), human.confirmation_entries(binding(path), record['source_bindings'], sources)


def save(store, entries):
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (POSTGRES_APPLY_ADVISORY_LOCK_KEY,))
        human.register(cursor, entries, origin='synthetic-human-workflow')


def current(store):
    with store.connect() as conn, conn.cursor() as cursor:
        return human.snapshot(cursor, ['S'])


def proposal_policy(request, root):
    policy = cm.Policy.model_validate_json(Path(request.policy.path).read_text())
    policy.initial_candidate = 'proposal'
    policy.independent_audit = False
    policy.budget.gemini_calls = 0
    path = root / 'proposal-policy.json'
    path.write_text(policy.model_dump_json())
    request.policy = cm.source.FileBinding.model_validate(binding(path))


@pytest.mark.parametrize('independent_audit', [False, True])
def test_next_action_automatically_reads_registered_human_facts_after_restart(action, independent_audit):
    store, _, _, _, root, _ = action
    seed_claims(store, ['C'])
    first, output = operation_request(action, 'Update', ['C'])
    bound, entries = facts(first, root)
    authorize(first, {'human_source_verification': bound})
    original = store.get_record_state('claims', 'C')
    result = execute_claim_management(store, first, root=output, ports=SyntheticPorts(store, root))
    assert result['status'] == 'completed', result
    assert current(store) == entries
    # The saved human fact is durable SQL evidence, not a path rediscovered by
    # the next operator. Its original file may be archived/moved afterwards.
    Path(bound['path']).unlink()
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    restarted = PostgresKnowledgeStore(store.database_url)
    second, output2 = operation_request(action, 'Update', ['C'])
    proposal_policy(second, root)
    if independent_audit:
        policy = cm.Policy.model_validate_json(Path(second.policy.path).read_text())
        policy.independent_audit = True; policy.budget.gemini_calls = 1
        Path(second.policy.path).write_text(policy.model_dump_json())
        second.policy = cm.source.FileBinding.model_validate(binding(Path(second.policy.path)))
    authorize(second)  # No human-evidence field or predecessor link supplied.

    def check(packet):
        values = packet['sources']['S']['human_source_decisions']
        assert len(values) == 1 and values[0]['decision_id'] == entries[0]['decision_id']
        assert values[0]['applicability'] == 'current'
        assert values[0]['record']['answers'][0]['answer'].startswith('The professor confirmed')
        assert not values[0]['claim_content_approval']
        assert 'human_source_verification' not in packet['sources']['S']

    def propose(packet):
        check(packet)
        candidate = cm.existing_candidate(packet['before']); candidate.operation = 'Update'
        return candidate.model_dump(mode='json')

    def opus(packet):
        check(packet)
        return passing(packet)

    def gemini(packet):
        values = packet['human_source_decisions']['S']
        assert len(values) == 1 and values[0]['applicability'] == 'current'
        assert values[0]['decision_id'] == entries[0]['decision_id']
        return passing(packet)

    ports = SyntheticPorts(restarted, root, proposal=propose, opus=opus, gemini=gemini)
    result = execute_claim_management(restarted, second, root=output2, ports=ports)
    assert result['status'] == 'completed', result
    expected_calls = ['gpt', 'opus'] + (['gemini'] if independent_audit else [])
    assert ports.calls == expected_calls
    assert restarted.claim_management_qualification('C')['ready']
    assert restarted.get_record_state('claims', 'C') == original
    assert execute_claim_management(restarted, second, root=output2, ports=ports) == result
    assert ports.calls == expected_calls


def test_paragraph_scope_survives_unrelated_edits_but_not_changed_or_missing_text():
    original = {'S': {'file': {'file_sha256': 'a' * 64}, 'paragraphs': {
        'S0001': {'text': '已核实。'}, 'S0002': {'text': '另一段。'}}}}
    value = human.entry({'path': '/evidence', 'file_sha256': 'b' * 64}, {}, 'S', original['S'], ['S0001'], 'confirmation')
    for fault in ['unchanged', 'other_paragraph', 'same_paragraph', 'missing']:
        sources = deepcopy(original)
        if fault != 'unchanged':
            sources['S']['file']['file_sha256'] = 'c' * 64
        if fault == 'other_paragraph': sources['S']['paragraphs']['S0002']['text'] = '只改其他段。'
        if fault == 'same_paragraph': sources['S']['paragraphs']['S0001']['text'] += '新增限定。'
        if fault == 'missing': sources['S']['paragraphs'].pop('S0001')
        human.project(sources, [value])
        row = sources['S']['human_source_decisions'][0]
        assert row['applicability'] == ('current' if fault in {'unchanged', 'other_paragraph'} else 'needs_reconfirmation')
    foreign = {'OTHER': deepcopy(original['S'])}
    human.project(foreign, [value])
    assert 'human_source_decisions' not in foreign['OTHER']


def test_human_answers_in_different_paragraphs_have_independent_applicability(action):
    _, request, _, _, root, _ = action
    bound, _ = facts(request, root)
    record = json.loads(Path(bound['path']).read_text())
    second = deepcopy(record['answers'][0]); second['finding_id'] = 'second-question'
    second['citation'].update(paragraph_key='S0002', exact_quote='另一段。')
    record['answers'].append(second)
    Path(bound['path']).write_text(json.dumps(record))
    sources = {'S': cm.source.source_snapshot(request.source_bindings['S'])}
    sources['S']['paragraphs']['S0002'] = {'index': 2, 'text': '另一段。'}
    entries = human.confirmation_entries(binding(Path(bound['path'])), record['source_bindings'], sources)
    assert len(entries) == 2
    sources['S']['paragraphs']['S0002']['text'] = '改过的第二段。'
    human.project(sources, entries)
    statuses = {next(iter(v['paragraph_sha256'])): v['applicability'] for v in sources['S']['human_source_decisions']}
    assert statuses == {'S0001': 'current', 'S0002': 'needs_reconfirmation'}


def test_explicit_replacement_keeps_history_and_old_retry_does_not_reactivate(action):
    store, request, _, _, root, _ = action
    _, old = facts(request, root)
    save(store, old); save(store, old)
    _, new = facts(request, root, answer='A later explicit human correction.', supersedes=[old[0]['decision_id']])
    save(store, new)
    save(store, old)
    assert current(store) == new
    with store.connect() as conn:
        rows = conn.execute('SELECT decision_id,superseded_by FROM wang_knowledge.human_source_decisions ORDER BY decision_id').fetchall()
    assert len(rows) == 2
    assert dict(rows)[old[0]['decision_id']] == new[0]['decision_id']
    with pytest.raises(ChangeSetConflict):
        with store.connect() as conn, conn.cursor() as cursor:
            human.assert_current(cursor, ['S'], old)


def test_new_action_cannot_use_an_explicit_answer_that_was_already_replaced(action):
    store, _, _, _, root, _ = action; seed_claims(store, ['C'])
    request, output = operation_request(action, 'Update', ['C'])
    bound, old = facts(request, root); save(store, old)
    _, new = facts(request, root, answer='Explicit corrected answer.', supersedes=[old[0]['decision_id']])
    save(store, new)
    authorize(request, {'human_source_verification': bound})
    ports = SyntheticPorts(store, root)
    with pytest.raises(ChangeSetConflict, match='replaced human source decision'):
        execute_claim_management(store, request, root=output, ports=ports)
    assert ports.calls == []
    assert store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id)) is None
    assert current(store) == new


@pytest.mark.parametrize('write', [False, True])
def test_new_human_fact_after_opus_blocks_stale_commit_and_qualification(action, write):
    store, _, _, _, root, _ = action
    seed_claims(store, ['C'])
    request, output = operation_request(action, 'Update', ['C'])
    proposal_policy(request, root); authorize(request)
    _, entries = facts(request, root)
    original = store.get_record_state('claims', 'C')

    def propose(packet):
        candidate = cm.existing_candidate(packet['before']); candidate.operation = 'Update'
        if write:
            payload = deepcopy(packet['before']['claims/C']['payload']); payload['statement'] = '教授以甲作假设。'
            candidate.records = [cm.source.Row(collection='claims', object_id='C', payload=payload)]
            candidate.dispositions[0].action = 'update'
            candidate.dispositions[0].reason = 'synthetic source faithful update'
            candidate.dispositions[0].citations = [cm.Citation(source_id='S', paragraph_key='S0001', exact_quote='甲是假设，不是断言。')]
        return candidate.model_dump(mode='json')

    def opus(packet):
        save(store, entries)  # New human input arrives while the model is working.
        return passing(packet)

    ports = SyntheticPorts(store, root, proposal=propose, opus=opus)
    result = execute_claim_management(store, request, root=output, ports=ports)
    assert result['status'] == 'paused' and result['reason'] == 'engineering_required', result
    assert 'human source decisions changed' in result['detail']
    assert store.get_record_state('claims', 'C') == original
    assert ports.calls == ['gpt', 'opus']
    assert not (store.claim_management_qualification('C') or {}).get('ready')


def test_new_human_input_invalidates_a_completed_new_review_without_rewriting_history(action):
    store, _, _, _, root, _ = action; seed_claims(store, ['C'])
    request, output = operation_request(action, 'Update', ['C'])
    result = execute_claim_management(store, request, root=output, ports=SyntheticPorts(store, root))
    assert result['status'] == 'completed' and store.claim_management_qualification('C')['ready']
    parent = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    _, entries = facts(request, root); save(store, entries)
    assert not (store.claim_management_qualification('C') or {}).get('ready')
    assert store.get_cvp_group(parent['run_id']) == parent


def test_verified_literal_correction_is_automatically_saved_and_reused(verified_transition):
    from backend.pipeline.verified_source_quote_sync import synchronize
    store, bound, transition, root, current_file = verified_transition
    result = synchronize(store, bound, transition, root=root)
    entries = current(store)
    assert len(entries) == 1 and entries[0]['kind'] == 'transcription_correction'
    assert entries[0]['record']['before'] == '旧说法'
    assert entries[0]['record']['after'] == '新说法'
    sources = {'S': cm.source.source_snapshot(binding(current_file))}
    human.project(sources, entries)
    assert sources['S']['human_source_decisions'][0]['applicability'] == 'current'
    assert synchronize(store, bound, transition, root=root) == result
    assert current(store) == entries


def test_source_impact_qualification_also_checks_new_human_evidence(action):
    from backend.tests.test_claim_management_source_impact import source_request, impact_result
    store, original, _, _, root, _ = action
    assert execute_claim_management(store, original, root=root/'original-root',
        ports=SyntheticPorts(store, root))['status'] == 'completed'
    request, output = source_request(action, targets=['C'])
    result = execute_claim_management(store, request, root=output,
        ports=SyntheticPorts(store, root, opus=impact_result, gemini=impact_result))
    assert result['status'] == 'completed' and store.claim_management_qualification('C')['ready']
    _, entries = facts(request, root); save(store, entries)
    qualification = store.claim_management_qualification('C')
    assert qualification['ready'] is False and qualification['reason'] == 'human_source_decisions_changed'


@pytest.mark.parametrize('role', ['gpt', 'opus'])
def test_compact_runtime_prompt_preserves_human_evidence_rules(role):
    from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
    policy = cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v3.json').read_text())
    adapter = claim_management_model_adapter(role, policy)
    assert 'human_source_decisions' in adapter._base_prompt
    assert 'applicability=current' in adapter._base_prompt
    assert 'needs_reconfirmation' in adapter._base_prompt
    assert '保持教授逐字原话不变' in adapter._base_prompt
    assert '不为已回答的同一事实重复要求人工' in adapter._base_prompt
