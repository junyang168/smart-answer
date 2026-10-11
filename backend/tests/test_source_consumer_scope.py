from copy import deepcopy
import os
from pathlib import Path

import pytest
from backend.pipeline import source_consumer_scope as m
from backend.pipeline import source_reconciliation as c
from backend.api.canonical_repository.postgres_store import record_content_sha


def fixture(monkeypatch):
    from backend.pipeline import claim_evidence_completion
    monkeypatch.setattr(claim_evidence_completion, 'direction_for', lambda *a, **kw:
        {'unchanged_held_context_claim_ids': ['C'], 'withdraw_relation_ids': ['R']})
    states = {'claims/C': {'claim_id': 'C', 'revision': 2, 'group_id': 'G',
                         'review_status': 'human_review_required'},
              'knowledge_relations/R': {'relation_id': 'R', 'revision': 1}}
    frozen = {'before_records': c.rows(states), 'claim_issue_context': {},
              'inventory': {'affected_keys': ['knowledge_relations/R'], 'context_keys': ['claims/C']},
              'manifest': {'groups': [{'group_key': 'campaign-group'}]},
              'sql_scope': {'read_pins': [{'collection': col, 'object_id': oid,
                  'pin': [p['revision'], record_content_sha(p), False]}
                  for k, p in states.items() for col, oid in [c.split_key(k)]]}}
    proposal = c.Proposal(records=[], sentence_dispositions=[], unadmitted_candidates=[], dispositions=[
        c.Disposition(record_key='knowledge_relations/R', action='unresolved', reason='real relation issue'),
        c.Disposition(record_key='claims/C', action='retain', reason='held context unchanged'),
        c.Disposition(record_key='groups/G', action='unresolved', reason='pending group annotation'),
        c.Disposition(record_key='knowledge_relation_withdrawal_review/R', action='impact_only', reason='review only')])
    return frozen, proposal


def test_separate_without_projecting_or_approving(monkeypatch):
    f, p = fixture(monkeypatch)
    old = deepcopy(p.model_dump())
    receipt = m.receipt(f, p)
    assert p.model_dump() == old
    assert receipt['missing_business_keys'] == []
    assert receipt['unresolved_keys'] == ['knowledge_relations/R', 'groups/G']
    assert receipt['content_write_authority'] is False
    assert [i['kind'] for i in receipt['items']] == ['business_consumer', 'unchanged_readonly_context',
        'group_impact_annotation_not_cvp', 'independent_review_annotation_not_business_record']
    # Classification cannot weaken the original strict final/SQL gate.
    with pytest.raises(ValueError):
        c.validate_proposal(f, p)


@pytest.mark.parametrize('fault', ['unknown_group', 'unknown_review', 'missing_pin', 'retired',
                                  'mutated_context', 'writable_context', 'duplicate'])
def test_unproven_extras_refused(monkeypatch, fault):
    f, p = fixture(monkeypatch)
    if fault == 'unknown_group': p.dispositions[2].record_key = 'groups/foreign'
    if fault == 'unknown_review': p.dispositions[3].record_key = 'knowledge_relation_withdrawal_review/foreign'
    if fault == 'missing_pin': f['sql_scope']['read_pins'] = []
    if fault == 'retired': f['sql_scope']['read_pins'][0]['pin'][2] = True
    if fault == 'mutated_context': p.dispositions[1].action = 'update'
    if fault == 'writable_context': p.records = [c.Row(collection='claims', object_id='C', payload={'claim_id':'C'})]
    if fault == 'duplicate': p.dispositions.append(p.dispositions[1])
    with pytest.raises(ValueError): m.receipt(f, p)


@pytest.mark.skipif(os.environ.get('WKP357_REAL_SAVED_READONLY') != '1', reason='actual saved readonly opt-in')
def test_actual_correction_is_classified_without_calls_writes_or_verdict_changes():
    from dotenv import load_dotenv
    load_dotenv('.env')
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.source_reconciliation_runner import read, body, effective_proposal, validate_raw
    base = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-e60b6130e0f7c81e810f4195c47f9eda0830bb1110e48c7999cc6fcfbe4e1e2a/claim-issues')
    roots = list(base.glob('*/evidence-completion-once/action/*'))
    assert len(roots) == 1
    root = roots[0]
    hashes = {p: c.file_sha256(p) for p in root.iterdir() if p.is_file()}
    frozen = body(read(root/'scoped-materials.json'))
    c.guard_files(frozen)
    store = PostgresKnowledgeStore()
    for pin in frozen['sql_scope']['read_pins']:
        state = store.get_record_state(pin['collection'], pin['object_id'])
        assert [state['revision'], state['content_sha256'], state['retired']] == pin['pin']
    for stage in ('proposal', 'initial_review', 'correction'):
        validate_raw(root, stage, body(read(root/(stage+'.packet.json'))))
    proposal = effective_proposal(frozen, read(root/'correction.raw.json')['response'])
    before = deepcopy(proposal.model_dump())
    result = m.receipt(frozen, proposal)
    assert result['missing_business_keys'] == []
    assert len([i for i in result['items'] if i['kind'] == 'unchanged_readonly_context']) == 1
    assert len([i for i in result['items'] if i['kind'] == 'group_impact_annotation_not_cvp']) == 4
    assert len([i for i in result['items'] if i['kind'] == 'independent_review_annotation_not_business_record']) == 1
    assert 'knowledge_relations/AR-0014' in result['unresolved_keys']
    assert 'knowledge_relations/AR-0054' in result['unresolved_keys']
    assert proposal.model_dump() == before
    assert hashes == {p: c.file_sha256(p) for p in root.iterdir() if p.is_file()}
