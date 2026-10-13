"""Disambiguate retained short quotes only by their unique full bound sequence."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path

import pytest

from backend.pipeline import source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as r
from backend.api.canonical_repository.postgres_store import record_content_sha


@pytest.fixture
def sequence():
    text = '甲。背景。甲。乙。'
    source = {'path': '/exact/unchanged.json', 'file_sha256': 'same'}
    frozen = {'manifest': {'source_id': 'S', 'before_source': source, 'current_sources': [source]},
              'after': [{'body_sha256': 'bound-body'}], 'sql_scope': {'read_pins': []}}
    states = {'claims/C': {'revision': 2, 'evidence_step_ids': ['E'], 'review_status': 'ai_consensus_reviewed'},
              'evidence_steps/E': {'revision': 1, 'produced_claim_ids': ['C'], 'source_fragment_ids': ['F1', 'F2'],
                                   'support_eligibility': 'context_only'}}
    for fid, quote in [('F1', '甲。'), ('F2', '乙。')]:
        states['source_fragments/'+fid] = {'revision': 2, 'fragment_id': fid, 'source_id': 'S', 'paragraph_key': 'P',
            'verbatim_excerpt': quote, 'source_sha256': 'bound-body', 'anchor_state': 'source_version_bound',
            'paragraph_text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'verbatim_excerpt_sha256': hashlib.sha256(quote.encode()).hexdigest()}
    for k, v in states.items():
        col, oid = c.split_key(k)
        frozen['sql_scope']['read_pins'].append({'collection': col, 'object_id': oid,
                                               'pin': [v['revision'], record_content_sha(v), False]})
    return frozen, states, deepcopy(states), text


def locate(values):
    frozen, before, post, text = values
    return c.retained_evidence_sequence_start(frozen, before, post, 'claims/C', 'evidence_steps/E',
                                             post['source_fragments/F1'], text)


def test_full_bound_sequence_selects_second_occurrence_without_qualification_upgrade(sequence):
    assert locate(sequence) == 5
    assert sequence[2]['evidence_steps/E']['support_eligibility'] == 'context_only'
    assert sequence[1] == sequence[2]


@pytest.mark.parametrize('mutation', ['missing_pin', 'retired', 'pin_sha', 'revision', 'claim', 'evidence',
    'fragment', 'foreign', 'body_sha', 'paragraph_sha', 'quote_sha', 'hold', 'order', 'single', 'duplicate',
    'changed_source', 'ownership', 'gap', 'ambiguous_sequence'])
def test_sequence_proof_refuses_missing_changed_or_ambiguous_chain(sequence, mutation):
    frozen, before, post, text = sequence
    if mutation == 'missing_pin': frozen['sql_scope']['read_pins'] = []
    elif mutation == 'retired': frozen['sql_scope']['read_pins'][0]['pin'][2] = True
    elif mutation == 'pin_sha': frozen['sql_scope']['read_pins'][0]['pin'][1] = 'different'
    elif mutation == 'revision': frozen['sql_scope']['read_pins'][0]['pin'][0] += 1
    elif mutation == 'claim': post['claims/C']['statement'] = 'changed'
    elif mutation == 'evidence': post['evidence_steps/E']['support_eligibility'] = 'eligible'
    elif mutation == 'fragment': post['source_fragments/F1']['verbatim_excerpt'] = 'changed'
    elif mutation in {'foreign', 'body_sha', 'paragraph_sha', 'quote_sha', 'hold'}:
        field, value = {'foreign': ('source_id', 'foreign'), 'body_sha': ('source_sha256', 'other'),
            'paragraph_sha': ('paragraph_text_sha256', 'other'), 'quote_sha': ('verbatim_excerpt_sha256', 'other'),
            'hold': ('review_status', 'human_hold')}[mutation]
        before['source_fragments/F1'][field] = post['source_fragments/F1'][field] = value
        frozen['sql_scope']['read_pins'][2]['pin'][1] = record_content_sha(before['source_fragments/F1'])
    elif mutation in {'order', 'single', 'duplicate'}:
        ids = {'order': ['F2', 'F1'], 'single': ['F1'], 'duplicate': ['F1', 'F1']}[mutation]
        before['evidence_steps/E']['source_fragment_ids'] = post['evidence_steps/E']['source_fragment_ids'] = ids
        frozen['sql_scope']['read_pins'][1]['pin'][1] = record_content_sha(before['evidence_steps/E'])
    elif mutation == 'changed_source': frozen['manifest']['current_sources'] = [{'path': '/changed', 'file_sha256': 'other'}]
    elif mutation == 'ownership':
        before['evidence_steps/E']['produced_claim_ids'] = post['evidence_steps/E']['produced_claim_ids'] = []
        frozen['sql_scope']['read_pins'][1]['pin'][1] = record_content_sha(before['evidence_steps/E'])
    else:
        text = '甲。背景。甲。隔開。乙。' if mutation == 'gap' else '甲。乙。甲。乙。'
        for i, fid in enumerate(['F1', 'F2'], 2):
            before['source_fragments/'+fid]['paragraph_text_sha256'] = post['source_fragments/'+fid]['paragraph_text_sha256'] = hashlib.sha256(text.encode()).hexdigest()
            frozen['sql_scope']['read_pins'][i]['pin'][1] = record_content_sha(before['source_fragments/'+fid])
    with pytest.raises(ValueError): locate((frozen, before, post, text))


@pytest.mark.skipif(os.getenv('WKP_G094_ELIGIBILITY_READONLY') != '1', reason='explicit production read-only proof check')
def test_real_g094_saved_complete_initial_review_preflight_zero_model_zero_write():
    root = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-0490be727b9eef01497d20f558f7b437fc7beacd434324c8d71c1a82ce024c83/claim-issues/670ae7487f38ca391a0b3ca9c1a7a5e42814a32d14da50ff4e951299559bfdd0/scope-amendment-once/action/CVPA-949e353397967feb493ede9b46da08843748930954cdd9338ca28088ca1480c5')
    frozen = r.body(r.read(root/'scoped-materials.json'))
    raw = r.validate_raw(root, 'proposal', r.body(r.read(root/'proposal.packet.json')))
    c.guard_files(frozen)
    proposal = r.effective_proposal(frozen, raw['response'])
    packet = r.initial_review_base(frozen, proposal)
    assert packet['proposal'] == proposal.model_dump()
    assert len(proposal.records) == 6
    assert sum(d.action == 'unresolved' for d in proposal.dispositions) == 2
    with pytest.raises(ValueError, match='unresolved'): c.validate_proposal(frozen, proposal)
    assert not any((root/(stage+'.reservation.json')).exists()
                   for stage in ['initial_review', 'correction', 'final_review'])
