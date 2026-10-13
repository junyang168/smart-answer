from copy import deepcopy
import hashlib
import os
from pathlib import Path

import pytest

from backend.pipeline import source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as runner


@pytest.mark.parametrize('fault', [None, 'source', 'paragraph', 'body', 'paragraph_sha',
                                  'excerpt_sha', 'anchor_state', 'duplicate_quote', 'absent'])
def test_continuous_own_fragment_contains_anchor_exactly(fault):
    text = 'prefix quoted anchor suffix'
    quote = 'quoted anchor'
    fragment = {'source_id': 'SRC', 'paragraph_key': 'S1', 'source_sha256': 'body',
        'anchor_state': 'source_version_bound', 'verbatim_excerpt': text,
        'paragraph_text_sha256': hashlib.sha256(text.encode()).hexdigest(),
        'verbatim_excerpt_sha256': hashlib.sha256(text.encode()).hexdigest()}
    fields = {'source': 'source_id', 'paragraph': 'paragraph_key', 'body': 'source_sha256',
              'paragraph_sha': 'paragraph_text_sha256', 'excerpt_sha': 'verbatim_excerpt_sha256',
              'anchor_state': 'anchor_state'}
    if fault in fields: fragment[fields[fault]] = 'foreign'
    if fault == 'duplicate_quote': text += ' quoted anchor'
    if fault == 'absent': quote = 'absent'
    result = c.containing_anchor_fragments([fragment], quote, text, 'SRC', 'S1', 'body')
    assert result == ([] if fault else [fragment])
    if not fault:
        assert len(c.containing_anchor_fragments([fragment, deepcopy(fragment)], quote, text,
                                                 'SRC', 'S1', 'body')) == 2  # caller refuses ambiguity


@pytest.mark.parametrize('fault', [None, 'length', 'duplicate_local', 'duplicate_canonical',
                                  'foreign_canonical', 'unknown_local'])
def test_declared_local_anchor_identity_is_not_guessed(fault):
    claim = {'evidence_step_ids': ['L3-E045', 'L3-E046']}
    occurrence = {'local_source_evidence_ids': ['E045', 'E046'],
                  'canonical_evidence_step_ids': ['L3-E045', 'L3-E046']}
    anchor = {'evidence_id': 'E045'}
    if fault == 'length': occurrence['canonical_evidence_step_ids'].pop()
    if fault == 'duplicate_local': occurrence['local_source_evidence_ids'][1] = 'E045'
    if fault == 'duplicate_canonical': occurrence['canonical_evidence_step_ids'][1] = 'L3-E045'
    if fault == 'foreign_canonical': occurrence['canonical_evidence_step_ids'][1] = 'foreign'
    if fault == 'unknown_local': anchor['evidence_id'] = 'E999'
    original = deepcopy((claim, occurrence, anchor))
    if fault:
        with pytest.raises(ValueError): c.anchor_evidence_id(claim, occurrence, anchor)
    else:
        assert c.anchor_evidence_id(claim, occurrence, anchor) == 'L3-E045'
        assert c.anchor_evidence_id(claim, occurrence, {'evidence_id': 'L3-E045'}) == 'L3-E045'
    assert (claim, occurrence, anchor) == original


@pytest.fixture
def saved():
    if os.environ.get('WKP357_REAL_SAVED_READONLY') != '1':
        pytest.skip('explicit saved production read-only opt-in')
    root = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-e60b6130e0f7c81e810f4195c47f9eda0830bb1110e48c7999cc6fcfbe4e1e2a/claim-issues')
    action = next(root.glob('*/action/*/proposal.raw.json')).parent
    frozen = runner.read(action / 'scoped-materials.json')
    raw = runner.read(action / 'proposal.raw.json')
    return action, frozen, raw, runner.effective_proposal(frozen, raw['response'])


@pytest.mark.parametrize('fault', [None, 'pin', 'retired', 'hold', 'rejected', 'permission',
                                  'proposed', 'missing', 'claim_pending', 'unknown'])
def test_initial_readonly_source_dependencies_never_approve_write(saved, fault):
    action, frozen, raw, proposal = saved
    original = deepcopy(frozen)
    files = {p: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in action.iterdir() if p.is_file()}
    target = 'evidence_steps/L3-E046'
    row = next(r for r in frozen['before_records']
               if c.key(r['collection'], r['object_id']) == target)
    pin = next(r['pin'] for r in frozen['sql_scope']['read_pins']
               if c.key(r['collection'], r['object_id']) == target)
    if fault == 'pin': pin[0] += 1
    if fault == 'retired': pin[2] = True
    if fault == 'hold': row['payload']['review_status'] = 'human_hold'
    if fault == 'rejected': row['payload']['review_status'] = 'rejected'
    if fault == 'permission':
        frozen['manifest']['permissions'].append({'collection': 'evidence_steps',
            'object_id': 'L3-E046', 'fields': ['statement'], 'create': False})
    if fault == 'proposed': proposal.records.append(c.Row.model_validate(row))
    if fault == 'missing': frozen['before_records'].remove(row)
    if fault == 'claim_pending':
        next(d for d in proposal.dispositions if d.record_key == 'claims/CL-0027').action = 'unresolved'
    if fault == 'unknown': frozen['inventory']['unknown_keys'].append('foreign/X')
    if fault:
        with pytest.raises((ValueError, KeyError)):
            runner.initial_review_base(frozen, proposal)
    else:
        packet = runner.initial_review_base(frozen, proposal)
        receipt = packet['unresolved_proposal_review']
        assert receipt['pending_record_keys'] == [target, 'knowledge_relations/AR-0043', 'knowledge_relations/AR-0044']
        assert receipt['content_write_authority'] is False
        assert receipt['mandatory_original_unique_correction'] is True
        assert frozen == original
        response = {'proposal_sha256': packet['proposal_sha256'], 'verdicts': [
            {'record_key': d.record_key, 'approved': True, 'problems': [], 'reason': 'initial draft only'}
            for d in proposal.dispositions]}
        assert c.validate_review(packet, response) is False
        with pytest.raises(ValueError): c.review_packet(frozen, proposal)
        with pytest.raises(ValueError): c.compile_package(frozen, proposal, {'response': response})
    assert files == {p: hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in action.iterdir() if p.is_file()}


def test_saved_initial_packet_retains_old_validation_profile(saved):
    action, frozen, raw, proposal = saved
    from backend.pipeline.source_review_input_repair import check_packet
    saved_packet = runner.body(runner.read(action / 'initial_review.packet.json'))
    base = runner.initial_review_base(frozen, proposal)
    base = runner.disclosed_review_base(frozen, base, saved=saved_packet)
    check_packet(frozen, raw['response'], saved_packet, base)
    assert 'anchor_binding_validation_profile' not in frozen['claim_issue_context']
    assert 'anchor is not owned by a Claim Evidence step' in base['unresolved_proposal_review']['program_findings']
    # New semantics are only a nonpersistent diagnostic here: never upgrade
    # the already reserved model input, Claim payload or its approval.
    new = deepcopy(frozen)
    new['claim_issue_context']['anchor_binding_validation_profile'] = 'declared_local_own_containing_v1'
    correction = runner.read(action / 'correction.raw.json')
    corrected = runner.effective_proposal(new, correction['response'])
    post = c.validate_proposal(new, corrected, initial_claim_draft=True)
    c.validate_graph(post, c.index(new['before_records']), new)
    with pytest.raises(ValueError): c.review_packet(new, corrected)
