"""Initial review may see exact readonly questions, never approve their writes."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path

import pytest

from backend.pipeline import source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as runner


@pytest.mark.skipif(os.getenv('WKP357_SAVED_QUESTION_TEST') != '1',
                    reason='explicit actual saved SQL readonly opt-in')
@pytest.mark.parametrize('fault', [None, 'pin', 'retired', 'hold', 'rejected',
                                  'permission', 'proposed', 'missing', 'unknown'])
def test_saved_readonly_question_is_initial_only(fault):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.cvp_sql_flow import decoded_pins
    load_dotenv('.env')
    root = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-b9bdf23b7ec4f63b20fa5d69818109697a8fb808138b18557dffe2e225369031/claim-issues/7aa531195fd6871077f6dc53cbb730ca7528fb3ac4576e2cb5e58c828fe9237f/action/CVPA-65dad50d41936bd1f43acac20b43dd06ab6be10a9d047f19453267d1b0d9b480')
    files = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
    frozen = runner.read(root / 'scoped-materials.json')
    c.guard_files(frozen)
    store = PostgresKnowledgeStore()
    with store.connect() as conn, conn.cursor() as cursor:
        conn.execute('SET TRANSACTION READ ONLY')
        class Cursor:
            def execute(self, q, args): return cursor.execute(q.removesuffix(' FOR UPDATE'), args)
            def __getattr__(self, name): return getattr(cursor, name)
        store._assert_transaction_read_pins(Cursor(), decoded_pins(frozen['sql_scope']['read_pins']))
    raw = runner.validate_raw(root, 'proposal', runner.body(runner.read(root / 'proposal.packet.json')))
    proposal = runner.effective_proposal(frozen, raw['response'])
    original = deepcopy(frozen)
    ident = 'DK-1004660290a2-P06-Q011'
    target = 'questions/' + ident
    row = next(r for r in frozen['before_records'] if c.key(r['collection'], r['object_id']) == target)
    pin = next(r['pin'] for r in frozen['sql_scope']['read_pins'] if c.key(r['collection'], r['object_id']) == target)
    if fault == 'pin': pin[0] += 1
    if fault == 'retired': pin[2] = True
    if fault == 'hold': row['payload']['review_status'] = 'human_hold'
    if fault == 'rejected': row['payload']['review_status'] = 'rejected'
    if fault == 'permission': frozen['manifest']['permissions'].append({'collection': 'questions', 'object_id': ident, 'fields': ['text'], 'create': False})
    if fault == 'proposed': proposal.records.append(c.Row.model_validate(row))
    if fault == 'missing': frozen['before_records'].remove(row)
    if fault == 'unknown': frozen['inventory']['unknown_keys'].append('foreign/X')
    if fault:
        with pytest.raises((ValueError, KeyError)): runner.initial_review_base(frozen, proposal)
    else:
        packet = runner.initial_review_base(frozen, proposal)
        receipt = packet['unresolved_proposal_review']
        assert target in receipt['pending_record_keys']
        assert receipt['content_write_authority'] is False
        assert receipt['mandatory_original_unique_correction'] is True
        response = {'proposal_sha256': packet['proposal_sha256'], 'verdicts': [
            {'record_key': d.record_key, 'approved': True, 'problems': [], 'reason': 'initial only'} for d in proposal.dispositions]}
        assert c.validate_review(packet, response) is False
        with pytest.raises(ValueError): c.review_packet(frozen, proposal)
        with pytest.raises(ValueError): c.compile_package(frozen, proposal, {'response': response})
        assert frozen == original
    assert files == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
