import pytest

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


def test_prompt_teaches_cross_reference_and_whole_topic_boundaries():
    assert 'context' in RULES and '整組保留為引言' in RULES
    assert '三層結論' in RULES and '經文相交' not in RULES
    assert '經文清單包含被解釋的經文和用作證據的經文' in RULES


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
