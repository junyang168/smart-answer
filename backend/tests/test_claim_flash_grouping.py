"""Counterexamples for grouping by source structure without losing arguments."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

from backend.pipeline import claim_flash_grouping as grouping
from backend.pipeline import claim_update_flash as flash, evidence_refresh as e
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


def row(payload):
    return {'payload': payload, 'retired': False, 'revision': 1}


def source(tmp_path, count=8, headings=None, text=None):
    headings = {0: '## 第一題', 4: '## 第二題'} if headings is None else headings
    script = []
    for i in range(count):
        if i in headings:
            script.append({'type': 'subtitle', 'index': f'subtitle-{i}', 'text': headings[i]})
        script.append({'type': 'content', 'index': str(i+1), 'text': text or f'教授原文第{i+1}段。'})
    path = tmp_path/'source.json'
    path.write_text(json.dumps({'script': script}, ensure_ascii=False))
    doc = {'source_id': 'S', 'source_type': 'sermon_transcript', 'source_path': str(path),
           'source_file_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    snap = e.source_snapshot({'path': str(path), 'file_sha256': doc['source_file_sha256']}, source=doc)
    return {'source_documents/S': row(doc)}, {'S': snap}


def lower(records, oid, positions, *, collection='evidence_steps'):
    fids = []
    for pos in positions:
        fid = f'{oid}-{pos}'; fids.append(fid)
        records['source_fragments/'+fid] = row({'fragment_id': fid, 'source_id': 'S',
                                              'paragraph_key': f'S{pos+1:04d}'})
    identity = {'evidence_steps': 'evidence_step_id', 'observations': 'observation_id',
                'questions': 'question_id'}[collection]
    records[collection+'/'+oid] = row({identity: oid, 'statement': oid,
                                     'source_fragment_ids': fids})


def claim(records, cid, pos, *, evidence=()):
    records['claims/'+cid] = row({'claim_id': cid, 'statement': cid,
        'evidence_step_ids': list(evidence), 'occurrences': [{'source_id': 'S',
            'anchors': [{'paragraph_key': f'S{pos+1:04d}'}]}]})


def plan(records, sources, **kwargs):
    return grouping.build(records, sources, 'S', **kwargs)


def test_headings_are_hints_not_evidence_or_new_paragraphs(tmp_path):
    records, sources = source(tmp_path)
    claim(records, 'A', 0); claim(records, 'B', 5)
    lower(records, 'E', [0]); lower(records, 'O', [6], collection='observations')
    before = deepcopy((records, sources))
    result = plan(records, sources, max_claims=1)
    assert [g['claim_ids'] for g in result['groups']] == [['A'], ['B']]
    assert [g['title'] for g in result['groups']] == ['第一題', '第二題']
    assert [pk for g in result['groups'] for pk in g['paragraph_keys']] == list(sources['S']['paragraphs'])
    assert result['pending'] == []
    assert before == (records, sources)
    assert not any('subtitle' in pk for g in result['groups'] for pk in g['paragraph_keys'])


def test_prefers_less_coupled_heading_cut_over_earlier_heading(tmp_path):
    records, sources = source(tmp_path, count=6, headings={0: '## A', 2: '## B', 4: '## C'})
    claim(records, 'A', 0); claim(records, 'B', 3); claim(records, 'C', 5)
    # Cutting at S0005 splits E's two supporting passages; cutting at S0003 does not.
    lower(records, 'E', [2, 4])
    result = plan(records, sources, max_claims=2)
    assert [g['claim_ids'] for g in result['groups']] == [['A'], ['B', 'C']]
    assert result['groups'][1]['heading_titles'] == ['B', 'C']
    assert result['coupling_at_boundaries'][0]['crossing_resources_and_links'] == 0


def test_disconnected_headings_are_not_merged_just_to_reduce_group_count(tmp_path):
    records, sources = source(tmp_path)
    claim(records, 'A', 0); claim(records, 'B', 5)
    result = plan(records, sources)
    assert [g['claim_ids'] for g in result['groups']] == [['A'], ['B']]
    assert all(not g['depends_on'] for g in result['groups'])


def test_shared_evidence_is_owned_once_and_remains_complete_for_both_claims(tmp_path):
    records, sources = source(tmp_path)
    lower(records, 'E', [0, 6])
    claim(records, 'A', 0, evidence=['E']); claim(records, 'B', 6, evidence=['E'])
    # They share an indivisible argument. It is kept together, not declared independent.
    result = plan(records, sources, max_claims=2)
    assert len(result['groups']) == 1
    assert result['groups'][0]['claim_ids'] == ['A', 'B']
    assert result['groups'][0]['lower_record_keys'] == ['evidence_steps/E']
    assert set(result['groups'][0]['fragment_keys']) == {'source_fragments/E-0', 'source_fragments/E-6'}


def test_shared_early_evidence_does_not_move_later_claim_out_of_its_occurrence(tmp_path):
    records, sources = source(tmp_path)
    lower(records, 'E', [0])
    claim(records, 'A', 0, evidence=['E']); claim(records, 'B', 5, evidence=['E'])
    result = plan(records, sources, max_claims=1)
    first, second = result['groups']
    assert first['claim_ids'] == ['A'] and second['claim_ids'] == ['B']
    assert second['reference_record_keys'] == ['evidence_steps/E']
    assert second['reference_paragraph_keys'] == ['S0001']
    assert second['depends_on'] == ['FG001']


def test_cross_group_evidence_link_is_explicit_not_recursive_graph_expansion(tmp_path):
    records, sources = source(tmp_path)
    lower(records, 'E1', [0]); lower(records, 'E2', [5]); lower(records, 'PRIVATE', [6])
    claim(records, 'A', 0, evidence=['E1']); claim(records, 'B', 5, evidence=['E2', 'PRIVATE'])
    records['claim_relations/R'] = row({'from_id': 'A', 'to_id': 'B', 'relation_type': 'supports'})
    result = plan(records, sources, max_claims=1)
    first, second = result['groups']
    assert first['reference_record_keys'] == ['claims/B']
    assert 'evidence_steps/PRIVATE' not in first['reference_record_keys']
    assert not first['reference_paragraph_keys']
    assert second['depends_on'] == ['FG001']
    assert len(result['cross_group_links']) == 1
    assert result['semantic_topic_review'] == 'not_claimed'


def test_readonly_shared_paragraph_still_creates_dependency(tmp_path):
    records, sources = source(tmp_path)
    claim(records, 'A', 0); claim(records, 'B', 5)
    lower(records, 'E', [2, 6])
    result = plan(records, sources, max_claims=1)
    first, second = result['groups']
    assert first['lower_record_keys'] == ['evidence_steps/E']
    assert first['reference_paragraph_keys'] == ['S0007']
    assert second['depends_on'] == ['FG001']
    assert result['cross_group_links'] == []


def test_oversized_heading_is_split_without_dropping_unclaimed_source(tmp_path):
    records, sources = source(tmp_path, headings={0: '## 同一論題'})
    for i in range(5):
        claim(records, 'C'+str(i), i)
    lower(records, 'ORPHAN', [7])
    result = plan(records, sources, max_claims=2)
    assert all(len(g['claim_ids']) <= 2 for g in result['groups'])
    assert sorted(cid for g in result['groups'] for cid in g['claim_ids']) == [f'C{i}' for i in range(5)]
    assert [pk for g in result['groups'] for pk in g['paragraph_keys']] == list(sources['S']['paragraphs'])
    assert sum('evidence_steps/ORPHAN' in g['lower_record_keys'] for g in result['groups']) == 1
    assert {g['title'] for g in result['groups']} == {'同一論題'}


def test_no_headings_uses_source_order_and_complete_lower_census(tmp_path):
    records, sources = source(tmp_path, headings={})
    claim(records, 'A', 0); claim(records, 'B', 5)
    lower(records, 'O', [7], collection='observations')
    lower(records, 'Q', [7], collection='questions')
    result = plan(records, sources, max_claims=1)
    assert result['heading_hints'] == []
    assert sum(len(g['lower_record_keys']) for g in result['groups']) == 2


def test_explicit_scope_excludes_reference_and_superseded_claims_from_targets(tmp_path):
    records, sources = source(tmp_path)
    claim(records, 'A', 0); claim(records, 'B', 5); claim(records, 'OLD', 2)
    records['claims/OLD']['payload']['review_status'] = 'superseded'
    lower(records, 'E', [5])
    result = plan(records, sources, claim_ids=['A'])
    assert result['target_claim_ids'] == ['A']
    assert [cid for g in result['groups'] for cid in g['claim_ids']] == ['A']
    assert sum(len(g['paragraph_keys']) for g in result['groups']) == 8
    with pytest.raises(ValueError, match='distinct active'):
        plan(records, sources, claim_ids=['OLD'])


def test_non_target_claim_in_same_group_is_reference_not_new_target(tmp_path):
    records, sources = source(tmp_path)
    claim(records, 'A', 0); claim(records, 'B', 1)
    records['claim_relations/R'] = row({'from_id': 'A', 'to_id': 'B', 'relation_type': 'supports'})
    result = plan(records, sources, claim_ids=['A'])
    assert result['groups'][0]['claim_ids'] == ['A']
    assert result['groups'][0]['reference_record_keys'] == ['claims/B']


@pytest.mark.parametrize('collection', ['knowledge_relations', 'claim_relation_constraints'])
def test_constraints_and_legacy_endpoint_aliases_contribute_coupling(tmp_path, collection):
    records, sources = source(tmp_path)
    claim(records, 'A', 0); claim(records, 'B', 5)
    records[collection+'/R'] = row({'source_id': 'A', 'target_id': 'B', 'relation_type': 'supports'})
    result = plan(records, sources, max_claims=1)
    assert len(result['cross_group_links']) == 1
    assert result['groups'][1]['depends_on'] == ['FG001']


def test_same_external_readonly_reference_prevents_independent_execution(tmp_path, monkeypatch):
    records, sources = source(tmp_path)
    claim(records, 'A', 0); claim(records, 'B', 5)
    for cid in ['A', 'B']:
        records['claim_relations/'+cid] = row({'from_id': cid, 'to_id': 'FOREIGN', 'relation_type': 'supports'})
    result = plan(records, sources, max_claims=1)
    assert result['groups'][0]['external_reference_ids'] == ['FOREIGN']
    assert result['groups'][1]['depends_on'] == ['FG001']
    assert not result['groups'][0]['reference_paragraph_keys']
    monkeypatch.setattr(grouping.f, 'load_source', lambda *a: (records, sources))
    first = grouping.save(None, 'S', tmp_path/'plan', max_claims=1)
    assert grouping.save(None, 'S', tmp_path/'plan', max_claims=1) == first


def test_obsolete_fragment_position_is_a_hint_not_a_ready_source_binding(tmp_path):
    records, sources = source(tmp_path)
    claim(records, 'A', 0); lower(records, 'E', [0])
    records['source_fragments/E-0']['payload']['anchor_state'] = 'source_deleted'
    result = plan(records, sources)
    assert result['status'] == 'needs_attention'
    assert result['pending'][0]['reason'] == 'source_position_requires_calibration'
    assert result['coverage']['assigned_lower_records'] == 1


@pytest.mark.parametrize('ids', [['A', 'A'], ['MISSING']])
def test_bad_target_scope_fails_before_planning(tmp_path, ids):
    records, sources = source(tmp_path); claim(records, 'A', 0)
    with pytest.raises(ValueError, match='distinct active'):
        plan(records, sources, claim_ids=ids)


@pytest.mark.parametrize('capacity', [0, 11, True])
def test_invalid_capacity_rejected(tmp_path, capacity):
    records, sources = source(tmp_path)
    with pytest.raises(ValueError, match='max_claims'):
        plan(records, sources, max_claims=capacity)


def test_too_many_claims_at_same_paragraph_does_not_fake_an_independent_split(tmp_path):
    records, sources = source(tmp_path)
    claim(records, 'A', 0); claim(records, 'B', 0)
    with pytest.raises(ValueError, match='indivisible'):
        plan(records, sources, max_claims=1)


def test_missing_source_records_remain_pending_not_silently_omitted(tmp_path, monkeypatch):
    records, sources = source(tmp_path); claim(records, 'A', 0, evidence=['MISSING'])
    lower(records, 'UNLOCATED', [])
    monkeypatch.setattr(grouping.f, 'load_source', lambda *a: (records, sources))
    result = grouping.save(None, 'S', tmp_path/'plan')
    assert result['status'] == 'needs_attention'
    assert {p['reason'] for p in result['pending']} == {'missing_owned_evidence', 'unlocated_group_member'}
    assert not (tmp_path/'plan/claim-groups.json').exists()


def test_plan_is_deterministic_and_saved_inputs_cannot_be_overwritten(tmp_path, monkeypatch):
    records, sources = source(tmp_path); claim(records, 'A', 0); claim(records, 'B', 5)
    monkeypatch.setattr(grouping.f, 'load_source', lambda *a: (records, sources))
    saved = grouping.save(None, 'S', tmp_path/'plan', max_claims=1)
    assert grouping.save(None, 'S', tmp_path/'plan', max_claims=1) == saved
    assert plan(dict(reversed(list(records.items()))), sources, max_claims=1) == saved
    with pytest.raises(ValueError, match='saved input differs'):
        grouping.save(None, 'S', tmp_path/'plan', max_claims=2)


def test_changed_physical_source_cannot_supply_new_heading_hints(tmp_path):
    records, sources = source(tmp_path)
    Path(sources['S']['file']['path']).write_text('{}')
    with pytest.raises(ValueError, match='physical file missing or changed'):
        plan(records, sources)


def test_cli_plan_does_not_construct_model_clients(tmp_path, monkeypatch, capsys):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    records, sources = source(tmp_path); claim(records, 'A', 0)
    monkeypatch.setattr(PostgresKnowledgeStore, '__init__', lambda self, *a, **kw: None)
    monkeypatch.setattr(grouping.f, 'load_source', lambda *a: (records, sources))
    monkeypatch.setattr(CodexSubscriptionClient, '__init__', lambda *a, **kw: pytest.fail('model constructed'))
    monkeypatch.setattr(sys, 'argv', ['flash', '--source-id', 'S', '--root', str(tmp_path/'plan'), '--plan-groups'])
    flash.main()
    assert json.loads(capsys.readouterr().out)['target_claim_ids'] == ['A']


def test_cli_plan_refuses_apply_before_database_or_models(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['flash', '--source-id', 'S', '--root', str(tmp_path), '--plan-groups', '--apply'])
    with pytest.raises(SystemExit) as err:
        flash.main()
    assert err.value.code == 2


def test_sql_plan_keeps_valid_context_and_writes_no_knowledge_content(sample):
    store, root, _ = sample
    store.apply_plan(store.plan_package({'package_id': 'valid-context', 'evidence_steps': [{
        'evidence_step_id': 'CONTEXT', 'statement': '課堂對話背景。', 'source_fragment_id': 'F',
        'step_type': 'dialogue_context', 'support_eligibility': 'context_only'}]}))
    before = {key: store.get_record_state(collection, oid) for key, collection, oid in [
        ('claim', 'claims', 'C'), ('evidence', 'evidence_steps', 'E'), ('context', 'evidence_steps', 'CONTEXT')]}
    result = grouping.save(store, 'S', root/'grouping')
    assert result['pending'] == [] and result['excluded_context_keys'] == []
    assert result['coverage']['assigned_claims'] == result['coverage']['target_claims'] == 1
    assert 'evidence_steps/CONTEXT' in result['groups'][0]['lower_record_keys']
    assert result['model_calls'] == result['content_writes'] == 0
    after = {key: store.get_record_state(collection, oid) for key, collection, oid in [
        ('claim', 'claims', 'C'), ('evidence', 'evidence_steps', 'E'), ('context', 'evidence_steps', 'CONTEXT')]}
    assert before == after


class GroupingPort:
    identity = {'gpt': {'backend': 'test', 'model': 'grouping-test', 'effort': 'medium'}}

    def __init__(self, starts=('S0001', 'S0005'), topics=('A', 'B')):
        self.starts, self.topics, self.calls = starts, topics, []

    def repair(self, request):
        self.calls.append(request)
        assert request['input']['task'] == 'semantic_grouping'
        assert len(request['input']['jobs']) == 1
        assert 'Jev 是待核對的線索' not in request['system']
        paragraphs = list(request['input']['jobs'][0]['source_paragraphs'])
        def last(i):
            following = self.starts[i+1] if i+1 < len(self.starts) else None
            return paragraphs[paragraphs.index(following)-1] if following in paragraphs else paragraphs[-1]
        return {'groups': [{'paragraph_ranges': [{'first': pk, 'last': last(i)}],
                            'title': '論證'+str(i), 'topic_key': topic}
                           for i, (pk, topic) in enumerate(zip(self.starts, self.topics))],
                'reason': '將共用證據的論證留在組內，減少跨界依賴。'}


def test_semantic_boundary_can_reduce_actual_coupling_and_preserves_full_census(tmp_path):
    records, sources = source(tmp_path)
    lower(records, 'E', [3, 6])
    claim(records, 'A', 0); claim(records, 'B', 3, evidence=['E']); claim(records, 'C', 6, evidence=['E'])
    structural = plan(records, sources, max_claims=2)
    port = GroupingPort(starts=('S0001', 'S0003'))
    before = deepcopy((records, sources))
    result = grouping.refine(records, sources, structural, tmp_path/'semantic', port)
    assert result['proposed_boundary_cost'] < result['structural_boundary_cost']
    assert result['proposed_boundary_cost'] == 0
    assert result['coverage'] == structural['coverage']
    assert [g['claim_ids'] for g in result['groups']] == [['A'], ['B', 'C']]
    assert result['semantic_topic_review'] == 'gpt_grouping' and result['model_calls'] == 1
    job = port.calls[0]['input']['jobs'][0]
    assert job['source_paragraphs'] == {pk: p['text'] for pk, p in sources['S']['paragraphs'].items()}
    assert set(job['claims']) == {'C1', 'C2', 'C3'}
    assert job['claims']['C1']['statement'] == 'A'
    assert 'evidence_steps' not in job and 'observations' not in job
    assert (records, sources) == before


def test_same_semantic_topic_preserves_dependency_even_without_graph_overlap(tmp_path):
    records, sources = source(tmp_path)
    claim(records, 'A', 0); claim(records, 'B', 5)
    result = grouping.refine(records, sources, plan(records, sources), tmp_path, GroupingPort(topics=('ONE', 'ONE')))
    assert result['groups'][1]['depends_on'] == ['FG001']


def test_return_to_same_argument_can_join_nonadjacent_ranges_without_borrowing_middle(tmp_path):
    records, sources = source(tmp_path, count=8, headings={0: '## A', 2: '## B', 5: '## 回到A'})
    lower(records, 'E', [0, 6])
    claim(records, 'A1', 0, evidence=['E']); claim(records, 'B', 3); claim(records, 'A2', 6, evidence=['E'])
    result = grouping.build(records, sources, 'S', max_claims=2, semantic_groups=[
        {'paragraph_ranges': [{'first': 'S0001', 'last': 'S0002'}, {'first': 'S0006', 'last': 'S0008'}],
         'title': '同一論證A', 'topic_key': 'A'},
        {'paragraph_ranges': [{'first': 'S0003', 'last': 'S0005'}], 'title': '獨立B', 'topic_key': 'B'}])
    assert result['groups'][0]['claim_ids'] == ['A1', 'A2']
    assert result['groups'][1]['claim_ids'] == ['B']
    assert result['groups'][0]['paragraph_keys'] == ['S0001', 'S0002', 'S0006', 'S0007', 'S0008']
    assert result['coupling']['shared_read_resources'] == 0
    assert result['groups'][1]['depends_on'] == []


@pytest.mark.parametrize('starts', [('S0002',), ('S0001', 'S0001'), ('S0001', 'S9999'), ('S0001', 'S0006', 'S0003')])
def test_bad_model_boundaries_are_rejected_without_silent_projection(tmp_path, starts):
    records, sources = source(tmp_path)
    claim(records, 'A', 0); claim(records, 'B', 5)
    port = GroupingPort(starts=starts, topics=tuple('TOPIC' for _ in starts))
    with pytest.raises(ValueError, match='cover the source'):
        grouping.refine(records, sources, plan(records, sources), tmp_path, port)
    assert len(port.calls) == 1


def test_model_cannot_exceed_group_capacity(tmp_path):
    records, sources = source(tmp_path)
    claim(records, 'A', 0); claim(records, 'B', 5)
    with pytest.raises(ValueError, match='exceeds Claim capacity'):
        grouping.refine(records, sources, plan(records, sources, max_claims=1), tmp_path,
                        GroupingPort(starts=('S0001',), topics=('ONE',)))


def test_oversized_semantic_task_is_not_sent_or_truncated(tmp_path):
    records, sources = source(tmp_path, count=1, headings={0: '## 一題'}, text='教授原話。'*12000)
    claim(records, 'A', 0); port = GroupingPort(starts=('S0001',), topics=('ONE',))
    with pytest.raises(ValueError, match='exceeds Flash payload'):
        grouping.refine(records, sources, plan(records, sources), tmp_path, port)
    assert port.calls == []


def test_semantic_save_resume_reuses_exact_answer_without_new_calls(tmp_path, monkeypatch):
    records, sources = source(tmp_path)
    claim(records, 'A', 0); claim(records, 'B', 5)
    monkeypatch.setattr(grouping.f, 'load_source', lambda *a: (records, sources))
    port = GroupingPort()
    first = grouping.save(None, 'S', tmp_path/'plan', ports=port)
    assert len(port.calls) == 1
    assert grouping.save(None, 'S', tmp_path/'plan', ports=port) == first
    assert len(port.calls) == 1
    assert json.loads((tmp_path/'plan/claim-groups.json').read_text()) == [['A'], ['B']]
    different = GroupingPort(); different.identity = {'gpt': {'model': 'different'}}
    with pytest.raises(ValueError, match='saved input differs'):
        grouping.save(None, 'S', tmp_path/'plan', ports=different)
    assert different.calls == []
    monkeypatch.setattr(grouping, 'SEMANTIC_CONTRACT', 'changed-input-contract')
    with pytest.raises(ValueError, match='saved input differs'):
        grouping.save(None, 'S', tmp_path/'plan', ports=port)
    assert len(port.calls) == 1


def test_more_coupled_model_proposal_is_saved_but_not_exported_for_execution(tmp_path, monkeypatch):
    records, sources = source(tmp_path)
    claim(records, 'A', 0); claim(records, 'B', 5)
    lower(records, 'E', [0, 3])
    monkeypatch.setattr(grouping.f, 'load_source', lambda *a: (records, sources))
    result = grouping.save(None, 'S', tmp_path/'plan', ports=GroupingPort(starts=('S0001', 'S0003')))
    assert result['status'] == 'needs_attention'
    assert result['coupling_assessment'] == 'increased_known_coupling'
    assert result['pending'][0]['reason'] == 'increased_known_coupling'
    assert (tmp_path/'plan/shared-groups.json').exists()
    assert not (tmp_path/'plan/claim-groups.json').exists()


def test_unknown_semantic_call_is_not_automatically_resent(tmp_path, monkeypatch):
    records, sources = source(tmp_path); claim(records, 'A', 0)
    monkeypatch.setattr(grouping.f, 'load_source', lambda *a: (records, sources))
    port = GroupingPort()
    def unknown(request):
        port.calls.append(request)
        raise RuntimeError('connection lost after sending')
    port.repair = unknown
    with pytest.raises(RuntimeError):
        grouping.save(None, 'S', tmp_path/'plan', ports=port)
    with pytest.raises(ValueError, match='no saved reply'):
        grouping.save(None, 'S', tmp_path/'plan', ports=port)
    assert len(port.calls) == 1
    assert not (tmp_path/'plan/shared-groups.json').exists()
