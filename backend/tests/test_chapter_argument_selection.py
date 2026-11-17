import pytest
import json
from pathlib import Path
from itertools import groupby

from backend.pipeline.chapter_argument_selection import compile_selection, RULES
from backend.pipeline.chapter_material import CHAPTERS


def material():
    topics = [{'group_id': 'G001'}, {'group_id': 'G002'}]
    paragraphs = {'S0001': {'text': '先講彌賽亞身份。', 'start_time': 0, 'end_time': 10},
                  'S0002': {'text': '再講門徒使命，直到七章。', 'start_time': 10, 'end_time': 20},
                  'S0003': {'text': '七章的三層結論。', 'start_time': 20, 'end_time': 30}}
    owners = {'S0001': 'G001', 'S0002': 'G001', 'S0003': 'G002'}
    answer = {'groups': [
        {'group_id': 'G001', 'placement': 'context', 'reason': '完整結構提供背景', 'editor_review': '請確認整組作引言'},
        {'group_id': 'G002', 'placement': 'omit', 'reason': '解釋七章結論', 'editor_review': ''}]}
    return topics, paragraphs, owners, answer


def compile(topics, paragraphs, owners, answer):
    return compile_selection(CHAPTERS['mat-5-1-48'], 'mat-5-1-12', topics, paragraphs, owners,
                             answer, {'title': '測試', 'source_id': 'S1'}, 'sha')


def test_whole_structure_is_kept_even_where_it_mentions_chapter_seven():
    record = compile(*material())
    assert [d['keep'] for d in record['decisions']] == [True, True, False]
    assert '七章' in record['pieces'][1]['text']
    assert all(d['verses'] == ['mat-5-1-12'] for d in record['decisions'][:2])
    assert len(record['editor_review']) == 1


def test_omit_excludes_the_whole_topic_not_just_its_second_half():
    topics, paragraphs, owners, answer = material()
    answer['groups'][0]['placement'] = 'omit'
    assert all(not d['keep'] for d in compile(topics, paragraphs, owners, answer)['decisions'])


def test_model_cannot_override_atomic_topic_choice_with_piece_selection():
    topics, paragraphs, owners, answer = material()
    answer['groups'][0]['selected_paragraphs'] = ['S0001']
    with pytest.raises(ValueError, match='cannot contain'):
        compile(topics, paragraphs, owners, answer)


@pytest.mark.parametrize('error', ['duplicate', 'missing', 'unknown'])
def test_every_topic_must_have_exactly_one_choice(error):
    topics, paragraphs, owners, answer = material()
    if error == 'duplicate': answer['groups'].append(answer['groups'][0])
    if error == 'missing': answer['groups'].pop()
    if error == 'unknown': answer['groups'][0]['group_id'] = 'G999'
    with pytest.raises(ValueError, match='one decision'):
        compile(topics, paragraphs, owners, answer)


def test_eval_catches_wrong_placement_and_a_partially_kept_topic():
    from backend.pipeline.chapter_argument_selection import evaluate
    record = compile(*material())
    case = {'lecture': '測試', 'target_section': 'mat-5-1-12', 'expected': {
        'G001': {'accepted_placements': ['context'], 'meaning': '整組結構背景'},
        'G002': {'accepted_placements': ['omit'], 'meaning': '排除另一主題'}}}
    assert evaluate(record, case)['passed']
    record['decisions'][1]['keep'] = False
    assert not evaluate(record, case)['passed']
    record['decisions'][1]['keep'] = True
    record['raw_decisions'][0]['placement'] = 'omit'
    assert not evaluate(record, case)['passed']


def test_settled_structure_must_not_be_sent_for_owner_review_again():
    from backend.pipeline.chapter_argument_selection import evaluate
    record = compile(*material())
    case = {'lecture': '測試', 'target_section': 'mat-5-1-12', 'expected': {
        'G001': {'accepted_placements': ['context'], 'meaning': '已定整組結構背景', 'editor_review_empty': True}}}
    assert not evaluate(record, case)['passed']
    record['raw_decisions'][0]['editor_review'] = ''
    assert evaluate(record, case)['passed']


def test_owner_golden_unit1_keeps_resumed_argument_in_original_source_order():
    path = Path(__file__).parent / 'fixtures/chapter_selection/unit1_sub_units_golden.json'
    case = json.loads(path.read_text())
    record = compile_selection(
        CHAPTERS['mat-5-1-48'], case['target_section'], case['topics'],
        case['paragraphs'], case['owners'], case['answer'],
        {'title': case['lecture'], 'source_id': case['source_id']}, case['source_sha256'])
    sub_units = {s['group_id']: s['id'] for s in case['sub_units']}
    assert len(sub_units) == len(case['sub_units']) == 3
    source_keys = list(case['paragraphs'])
    kept = [(source_keys[i], sub_units[p['argument_group_id']])
            for i, (p, d) in enumerate(zip(record['pieces'], record['decisions'], strict=True))
            if d['keep']]
    assert [key for key, _ in kept] == case['expected_kept_paragraphs']
    runs = [{'sub_unit_id': sid, 'paragraphs': [key for key, _ in rows]}
            for sid, rows in groupby(kept, key=lambda row: row[1])]
    assert runs == case['expected_playback_runs']
    assert [r['sub_unit_id'] for r in runs] == ['1.1', '1.2', '1.3', '1.2']
    assert not record['editor_review']
    from backend.pipeline.chapter_argument_selection import evaluate
    assert evaluate(record, case)['passed']
    # A prompt that moves law into Beatitudes must fail, as must reordered playback.
    record['raw_decisions'][-1]['placement'] = 'direct'
    assert not evaluate(record, case)['passed']
    record['raw_decisions'][-1]['placement'] = 'omit'
    record['pieces'][3], record['pieces'][4] = record['pieces'][4], record['pieces'][3]
    assert not evaluate(record, case)['passed']
