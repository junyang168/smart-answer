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


def test_saved_links_name_records_of_the_approved_lecture():
    keys = json.loads(d.KEYS.read_text(encoding='utf-8'))
    claims = [a['claim_id'][-6:] for k in keys['keys'] for a in k['arguments']]
    assert claims == ['CL0002', 'CL0004']


def test_regeneration_is_compared_through_his_words_element_by_element():
    keys = {'keys': [{'key': 'B', 'arguments': [{'change_set_id': 'cs', 'claim_id': 'G4', 'elements': [
        {'diagram_text': ['重'], 'item': 'B1', 'records': ['GS1']},
        {'diagram_text': ['摩西'], 'item': 'B1', 'records': ['GF2']}]}]}]}
    gold = {'source_fragments': [{'fragment_id': 'GF1', 'verbatim_excerpt': '摩西的律法跟基督的律法有重疊的地方'},
                                 {'fragment_id': 'GF2', 'verbatim_excerpt': '獻祭潔淨的食物基督的律法不管這一套'}],
            'evidence_steps': [{'evidence_step_id': 'GS1', 'source_fragment_ids': ['GF1']}]}
    new = {'source_fragments': [{'fragment_id': 'NF1', 'verbatim_excerpt': '所以摩西的律法跟基督的律法有重疊的地方。'}],
           'evidence_steps': [{'evidence_step_id': 'NS1', 'source_fragment_ids': ['NF1'], 'produced_claim_ids': ['N4']}],
           'claims': [{'claim_id': 'N4', 'statement': '兩律法重疊', 'occurrences': []}]}
    first, second = d.compare_regeneration(gold, new, 'cs', keys)
    assert first['not_held'] == [] and first['new_claims'] == {'N4': '兩律法重疊'}
    assert second['not_held'] == ['GF2']


def test_an_observation_holds_words_for_the_claim_it_supports():
    keys = {'keys': [{'key': 'A', 'arguments': [{'change_set_id': 'cs', 'claim_id': 'G2', 'elements': [
        {'diagram_text': ['新約'], 'item': 'A2', 'records': ['GS7']}]}]}]}
    gold = {'source_fragments': [{'fragment_id': 'GF7', 'verbatim_excerpt': '各人的重擔要互相擔當如此就完全了基督的律法'}],
            'evidence_steps': [{'evidence_step_id': 'GS7', 'source_fragment_ids': ['GF7']}]}
    new = {'source_fragments': [{'fragment_id': 'NF6', 'verbatim_excerpt': '你們各人的重擔要互相擔當，如此，就完全了基督的律法。'}],
           'observations': [{'observation_id': 'NO6', 'source_fragment_ids': ['NF6']}],
           'knowledge_relations': [{'from_id': 'NO6', 'to_id': 'N2', 'relation_type': 'supports'}],
           'claims': [{'claim_id': 'N2', 'title': '先施恩再要求', 'occurrences': []}]}
    [element] = d.compare_regeneration(gold, new, 'cs', keys)
    assert element['not_held'] == [] and element['new_claims'] == {'N2': '先施恩再要求'}
