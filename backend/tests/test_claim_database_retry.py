"""Fresh mid-unit generation: no rejected draft replay, no lost obligations."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm, claim_database_retry as retry
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.claim_review_recovery import resume_rejected_output
from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import operation_request, seed_claims, SyntheticPorts
from backend.tests.test_claim_owner_call_extension import authorize


def test_only_mid_unit_runtime_is_compacted_and_all_history_stays_archived():
    current = {
        'claims/C': {'payload': {'statement': 'current judgment'}},
        'source_fragments/F': {'payload': {'source_id': 'S', 'paragraph_key': 'S0074', 'verbatim_excerpt': 'current quote'}},
        'evidence_steps/E': {'payload': {'source_fragment_ids': ['F'], 'produced_claim_ids': ['C']}},
    }
    old = deepcopy(current)
    old['source_fragments/F']['payload']['verbatim_excerpt'] = 'OLD_SOURCE_DO_NOT_REPLAY'
    bad = {'response': {'changes': ['BAD_ANSWER_DO_NOT_REPLAY']},
           'rejection_detail': 'changed record repeated or absent from frozen input',
           'repeated_change_keys': ['source_fragments/F']}
    goal = {'target_keys': ['claims/C'], 'required_questions': ['own_source'],
            'original_objects': old, 'unaccepted_model_inputs': [bad]}
    material = {'policy': {'retry_context_contract': retry.CONTRACT}, 'round_number': 0,
                'before': current, 'sources': {'S': {'text': 'complete source'}},
                'original_goal': goal, 'previous_candidate': {'bad': 'BAD_DRAFT_DO_NOT_REPLAY'}}
    assert retry.project(material, material) == material  # First review unchanged.
    material['round_number'] = 1
    material['continuation'] = {'original_goal': goal, 'previous_candidates': {'bad': 'OLD_DRAFT'},
        'human_source_verification': {'answers': ['owner source clarification']}}
    saved = deepcopy(material)
    packet = retry.project(material, material)
    text = json.dumps(packet)
    assert 'OLD_SOURCE_DO_NOT_REPLAY' not in text and 'BAD_ANSWER_DO_NOT_REPLAY' not in text
    assert 'BAD_DRAFT_DO_NOT_REPLAY' not in text and 'previous_candidate' not in packet
    assert packet['before'] == current and packet['sources'] == material['sources']
    assert packet['original_goal']['required_questions'] == ['own_source']
    assert packet['continuation']['human_source_verification'] == material['continuation']['human_source_verification']
    check = packet['retry_context']['current_record_checks']['source_fragments/F']
    assert check['paragraph_key'] == 'S0074' and check['verbatim_excerpt'] == 'current quote'
    assert check['current_owners'] == [{'evidence_key': 'evidence_steps/E', 'claim_ids': ['C']}]
    assert material == saved  # Original raw/history/goal never rewritten.
    material['policy'] = {}
    assert retry.project(material, material) == material  # Frozen legacy policy.


def test_changed_original_target_judgment_is_not_hidden_by_compaction():
    m = {'policy': {'retry_context_contract': retry.CONTRACT}, 'round_number': 1,
         'before': {'claims/C': {'payload': {'statement': 'new'}}},
         'original_goal': {'target_keys': ['claims/C'], 'original_objects': {
             'claims/C': {'payload': {'statement': 'original'}}}}}
    assert retry.project(m, m)['original_goal']['original_target_judgments'] == {'claims/C': {'statement': 'original'}}


def test_unwritten_proposed_fixes_return_to_the_fresh_database_todo_list():
    previous = {'materials': {'unresolved': {'old': {'target_key': 'claims/C'}}},
                'candidate': {'dispositions': [{'action': 'update'}]}, 'change_set_ids': []}
    policy = {'retry_context_contract': retry.CONTRACT}
    current = {'new': {'target_key': 'claims/D'}}
    assert set(retry.unresolved_for_retry(policy, current, previous)) == {'old', 'new'}
    previous['candidate']['dispositions'][0]['action'] = 'retain'
    assert retry.unresolved_for_retry(policy, current, previous) == current
    previous['candidate']['dispositions'][0]['action'] = 'update'
    previous['change_set_ids'] = ['already_applied']
    assert retry.unresolved_for_retry(policy, current, previous) == current
    assert retry.unresolved_for_retry({}, current, previous) == current


@pytest.mark.parametrize('role', ['gpt', 'opus'])
def test_self_check_is_opt_in_and_not_an_additional_call(role):
    path = cm.DEFAULT_POLICY.parent
    old = cm.Policy.model_validate_json((path / 'wang_claim_management_joint_update_v9.json').read_text())
    new = cm.Policy.model_validate_json((path / 'wang_claim_management_joint_update_v10.json').read_text())
    assert 'retry_context_contract' not in old.model_dump(mode='json')
    assert new.models == old.models and new.budget == old.budget
    assert '数字相近不表示物件相关' not in claim_management_model_adapter(role, old)._base_prompt
    prompt = claim_management_model_adapter(role, new)._base_prompt
    assert '数字相近不表示物件相关' in prompt
    assert '不拼接' in prompt and '每个修改 record_key 恰好一次' in prompt


def configure(action, ids, *, proposal=False):
    store = action[0]; seed_claims(store, ids)
    request, root = operation_request(action, 'Update', ids)
    policy = cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent / 'wang_claim_management_joint_update_v10.json').read_text())
    policy.evidence_review_contract = None
    policy.budget.gpt_calls = 2
    policy.budget.opus_calls = 3
    policy.budget.max_revisions = 2
    policy.initial_candidate = 'proposal' if proposal else 'existing'
    authorize(request, policy, root.with_suffix('.policy.json'))
    return store, request, root


def cite():
    return {'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}


def change(key, statement):
    return {'record_key': key, 'fields_json': json.dumps({'statement': statement}),
            'reason': 'source-supported correction', 'citations': [cite()]}


def decisions(packet, *, persistent=()):
    return [{'finding_id': fid, 'disposition': 'persistent' if f['target_key'] in persistent else 'resolved',
             'reason': 'check the complete current source', 'citations': [cite()],
             'affected_target_keys': [f['target_key']], 'affected_write_keys': [], 'impact_reason': 'own judgment'}
            for fid, f in packet['unresolved'].items()]


def proposal(packet, changes):
    return {'changes': changes, 'creations': [], 'retirements': [], 'findings': [],
            'finding_resolutions': decisions(packet)}


def test_rejected_reply_is_not_replayed_and_next_round_rereads_database(action):
    store, request, root = configure(action, ['C'], proposal=True)
    attempts = []
    def propose(packet):
        attempts.append(packet)
        if len(attempts) == 1:
            assert 'retry_context' not in packet
            row = change('claims/C', 'BAD_DUPLICATE_DRAFT')
            return proposal(packet, [row, deepcopy(row)])
        assert 'BAD_DUPLICATE_DRAFT' not in json.dumps(packet)
        assert 'previous_candidate' not in packet
        assert packet['before']['claims/C']['payload']['topic_terms'] == ['fresh database edit']
        return proposal(packet, [change('claims/C', '甲是假设，不是断言。')])
    def opus(packet):
        assert packet['candidate']['records'][0]['payload']['statement'] == '甲是假设，不是断言。'
        return {'complete_review': True, 'findings': [], 'prior_findings': decisions(packet)}
    ports = SyntheticPorts(store, root, proposal=propose, opus=opus)
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['reason'] == 'engineering_required' and ports.calls == ['gpt']
    parent = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    rid = next(iter(parent['latest_rounds'].values()))
    raw_before = (root / rid / 'gpt.raw.json').read_bytes()
    resume_rejected_output(store, rid, role='gpt', error=parent['result']['detail'])
    # A lawful independent edit after recovery, before the next round starts.
    row = store.get_record_state('claims', 'C')['payload']; row['topic_terms'] = ['fresh database edit']
    store.apply_plan(store.plan_package({'claims': [row]}))
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['gpt', 'gpt', 'opus']
    assert (root / rid / 'gpt.raw.json').read_bytes() == raw_before
    assert store.get_record_state('claims', 'C')['payload']['topic_terms'] == ['fresh database edit']
    assert store.claim_management_qualification('C')['ready']


def test_fresh_retry_does_not_drop_other_fixes_that_were_only_in_the_draft(action):
    store, request, root = configure(action, ['C', 'D'])
    reviews = []; proposals = []
    def opus(packet):
        reviews.append(packet)
        if len(reviews) == 1:
            findings = [{'target_key': key, 'category': 'content', 'component': 'statement',
                'reason': 'complete source judgment omitted', 'acceptance_condition': 'restore the source judgment',
                'citations': [cite()], 'affected_target_keys': [key], 'affected_write_keys': [],
                'impact_reason': 'own judgment'} for key in request.target_keys]
            return {'complete_review': True, 'findings': findings, 'prior_findings': []}
        return {'complete_review': True, 'findings': [],
                'prior_findings': decisions(packet, persistent=['claims/D'] if len(reviews) == 2 else [])}
    def propose(packet):
        proposals.append(packet)
        assert 'previous_candidate' not in packet
        assert {f['target_key'] for f in packet['unresolved'].values()} == {'claims/C', 'claims/D'}
        assert packet['before']['claims/C']['payload']['statement'].endswith('C')
        answer = proposal(packet, [change('claims/C', '甲是假设，不是断言。'),
            change('claims/D', '甲是假设。' if len(proposals) == 1 else '甲是假设，不是断言。')])
        if len(proposals) == 1:
            # Keep the ordinary disagreement/exit policy unchanged: GPT also
            # acknowledges D is still unfinished, while C is fixed in draft.
            for resolution in answer['finding_resolutions']:
                if packet['unresolved'][resolution['finding_id']]['target_key'] == 'claims/D':
                    resolution['disposition'] = 'persistent'
        return answer
    ports = SyntheticPorts(store, root, proposal=propose, opus=opus)
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['opus', 'gpt', 'opus', 'gpt', 'opus']
    assert all(store.get_record_state('claims', key)['payload']['statement'] == '甲是假设，不是断言。' for key in ['C', 'D'])
    assert all(store.claim_management_qualification(key)['ready'] for key in ['C', 'D'])
