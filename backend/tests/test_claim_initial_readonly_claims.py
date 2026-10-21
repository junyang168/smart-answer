"""Readonly co-Claims are initial-review context, not repair/write authority."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path

import pytest

from backend.pipeline import source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as runner


@pytest.mark.skipif(os.getenv('WKP357_SAVED_READONLY_CLAIMS') != '1',
                    reason='explicit saved current SQL readonly opt-in')
@pytest.mark.parametrize('fault', [None, 'pin', 'retired', 'hold', 'rejected',
                                  'withheld', 'permission', 'proposed', 'missing', 'unknown'])
def test_current_readonly_co_claim_initial_only(fault):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.cvp_sql_flow import decoded_pins
    load_dotenv('.env')
    root = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-4c6083d8d5036f316dd620945c37974f4d6b3daf128752776490dec2b2f21cd4/claim-issues/90279628408c1c38f142b72f7e53419bc216fbf8d0594f6989d686b3a2fae951/action/CVPA-aee8d874759e1887d164d7e9ec7836c3ec2f559fcfa9cbf4499bd71853f8468c')
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
    ident = 'DK-78f4b0b8eb95-P03-CL002'
    target = 'claims/' + ident
    row = next(r for r in frozen['before_records'] if c.key(r['collection'], r['object_id']) == target)
    pin = next(r['pin'] for r in frozen['sql_scope']['read_pins'] if c.key(r['collection'], r['object_id']) == target)
    if fault == 'pin': pin[0] += 1
    if fault == 'retired': pin[2] = True
    if fault == 'hold': row['payload']['review_status'] = 'human_hold'
    if fault == 'rejected': row['payload']['review_status'] = 'rejected'
    if fault == 'withheld': row['payload']['review_status'] = 'withheld_unreviewed'
    if fault == 'permission': frozen['manifest']['permissions'].append({'collection': 'claims', 'object_id': ident, 'fields': ['statement'], 'create': False})
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
