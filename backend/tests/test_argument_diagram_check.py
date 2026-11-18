import json

import pytest

from backend.pipeline import argument_diagram_check as d

KEYS = {'keys': [{'key': 'A', 'items': [
    {'id': 'A1', 'statement': 's', 'terms': [['施恩'], ['要求']]},
    {'id': 'A3', 'statement': 's', 'terms': [['條文', '字句']]},
    {'id': 'A9', 'statement': 's', 'terms': [['重疊'], ['摩西']]}]}]}


def records(claim='', step=''):
    return {'claims': [{'claim_id': 'C1', 'statement': claim}],
            'evidence_steps': [{'evidence_step_id': 'E1', 'statement': step}]}


def test_each_item_gets_the_verdict_its_records_and_his_words_support():
    out = d.grade(KEYS, records('神先施恩，再提出要求。', '摩西律法與基督律法重疊'), ['他講條文和字句'])
    proposals = {i['id']: i['proposal']['verdict'] for i in out['items']}
    assert proposals == {'A1': 'claim', 'A3': 'missed', 'A9': 'evidence_only'}
    assert out['unruled'] == ['A1', 'A3', 'A9'] and out['errors'] == []


def test_not_in_lecture_is_not_an_error_and_rulings_decide():
    out = d.grade(KEYS, records(), ['今天講別的'], {'A1': {'verdict': 'not_in_lecture', 'reason': 'r'},
                                                  'A3': {'verdict': 'missed', 'reason': 'r'},
                                                  'A9': {'verdict': 'contradicted', 'reason': 'r'}})
    assert out['errors'] == ['A3', 'A9'] and out['counted'] == 2 and out['unruled'] == []
    with pytest.raises(ValueError, match='unknown verdict'):
        d.grade(KEYS, records(), [], {'A1': {'verdict': 'probably', 'reason': 'r'}})


def test_a_changed_diagram_is_refused(tmp_path):
    (tmp_path / 'x.svg').write_text('<svg/>')
    keys = {'keys': [{'key': 'A', 'diagram': {'path': 'x.svg', 'sha256': '0' * 64}, 'items': []}]}
    with pytest.raises(ValueError, match='changed since the owner confirmed'):
        d.check_diagrams(keys, tmp_path)


def test_the_saved_keys_match_the_owner_confirmed_items():
    keys = json.loads(d.KEYS.read_text(encoding='utf-8'))
    assert [i['id'] for k in keys['keys'] for i in k['items']] == ['A1', 'A2', 'A3', 'B1']


def test_diagrams_attach_to_the_linked_claim_of_that_lecture_only(tmp_path):
    import hashlib
    (tmp_path / 'x.svg').write_text('<svg/>')
    digest = hashlib.sha256(b'<svg/>').hexdigest()
    keys = {'keys': [{'key': 'B', 'title': 't', 'diagram': {'path': 'x.svg', 'sha256': digest}, 'items': [],
                      'arguments': [{'change_set_id': 'cs2', 'claim_id': 'C4',
                                     'elements': [{'diagram_text': ['重'], 'item': 'B1', 'records': ['E1']}]}]}]}
    linked = d.diagrams_for('cs2', tmp_path, keys)
    assert list(linked) == ['C4'] and linked['C4'][0]['elements'][0]['records'] == ['E1']
    assert d.diagrams_for('cs1', tmp_path, keys) == {}


def test_saved_links_name_records_of_the_approved_lecture():
    keys = json.loads(d.KEYS.read_text(encoding='utf-8'))
    claims = [a['claim_id'][-6:] for k in keys['keys'] for a in k['arguments']]
    assert claims == ['CL0002', 'CL0004']
