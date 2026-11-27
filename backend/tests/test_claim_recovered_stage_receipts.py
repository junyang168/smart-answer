"""Relocated receipts may reuse a completed call only with exact saved facts."""
import pytest

from backend.pipeline import claim_review_recovery as recovery
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline.source_reconciliation_runner import write


@pytest.mark.parametrize('change', ['none','unknown','raw','reservation','packet','candidate','round','stage','intent','binding'])
def test_relocated_completed_receipt_exactness(tmp_path, monkeypatch, change):
    monkeypatch.setattr(recovery.cm, 'model_packet', lambda material: material['model_input'])
    intent = {'model_id': 'same-model', 'packet_sha256': 'same-packet'}
    roots = [tmp_path/'original', tmp_path/'recovered']
    receipts = []
    for root in roots:
        root.mkdir()
        write(root/'candidate.json', {'statement':'unchanged'})
        write(root/'materials.json', {'model_input':'unchanged'})
        write(root/'opus.raw.json', {'response':'unchanged'})
        write(root/'opus.reservation.json', intent)
        receipt = {'round_id':'R','stage':'opus', 'candidate':binding(root/'candidate.json'), 'materials':binding(root/'materials.json')}
        write(root/'opus-result.json', receipt)
        receipts.append(receipt)
    slot = {'state':'completed','result':binding(roots[0]/'opus-result.json'),
            'intent':intent,'intent_sha256':recovery.sha256_json(intent)}
    current = roots[1]
    if change == 'unknown': slot['state'] = 'started'
    if change in {'raw','reservation'}:
        write(current/('opus.'+('raw' if change=='raw' else 'reservation')+'.json'), {'different':True}, immutable=False)
    if change in {'packet','candidate'}:
        name = 'materials' if change=='packet' else 'candidate'
        write(current/(name+'.json'), {'model_input':'changed'} if change=='packet' else {'statement':'changed'}, immutable=False)
        receipts[1][name] = binding(current/(name+'.json'))
    if change in {'round','stage'}: receipts[1]['round_id' if change=='round' else 'stage'] = 'different'
    if change == 'intent': slot['intent'] = {'different':True}
    write(current/'opus-result.json', receipts[1], immutable=False)
    bound = binding(current/'opus-result.json')
    if change == 'binding':
        bound['file_sha256'] = '0'*64
        with pytest.raises(ValueError): recovery._completed_stage_matches(slot, bound, 'R', 'opus')
    else:
        assert recovery._completed_stage_matches(slot, bound, 'R', 'opus') is (change=='none')
