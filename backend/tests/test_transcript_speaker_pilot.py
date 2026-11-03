import copy
import pytest
from backend.pipeline.transcript_speaker_pilot import validate


def candidate():
    return {'turns': [
        {'text': '好。', 'role': 'professor', 'confidence': 'clear',
         'clip_start_s': 1, 'clip_end_s': 2, 'acoustic_basis': '與前段主講者聲音一致'},
        {'text': '請問？', 'role': 'audience', 'confidence': 'clear',
         'clip_start_s': 1.8, 'clip_end_s': 3, 'acoustic_basis': '不同聲音在遠處插入'}],
        'limitations': '時間為估計值'}


def test_exact_partition_and_overlapping_interruption():
    validate(candidate(), '好。請問？', 4)


@pytest.mark.parametrize('change', ['rewrite', 'omit', 'uncertain_professor', 'unknown_clear',
                                  'negative_time', 'outside_clip', 'nonfinite', 'no_basis'])
def test_fail_closed(change):
    answer = copy.deepcopy(candidate())
    turn = answer['turns'][0]
    if change == 'rewrite': turn['text'] = '好的。'
    elif change == 'omit': answer['turns'].pop()
    elif change == 'uncertain_professor': turn['confidence'] = 'uncertain'
    elif change == 'unknown_clear': turn['role'] = 'unknown'
    elif change == 'negative_time': turn['clip_start_s'] = -1
    elif change == 'outside_clip': turn['clip_end_s'] = 5
    elif change == 'nonfinite': turn['clip_end_s'] = float('nan')
    elif change == 'no_basis': turn['acoustic_basis'] = ''
    with pytest.raises(ValueError): validate(answer, '好。請問？', 4)


def test_uncertain_kept_unknown():
    answer = candidate()
    answer['turns'][1].update(role='unknown', confidence='uncertain')
    validate(answer, '好。請問？', 4)
