from copy import deepcopy
import hashlib
import os
from pathlib import Path

import pytest

from backend.pipeline import source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as runner


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
