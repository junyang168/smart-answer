import json
from copy import deepcopy
from pathlib import Path
import pytest
from backend.pipeline.viewpoint_semantic_eval import review_cases, score

DATA=json.loads((Path(__file__).parent/'fixtures/cvp_contextual_semantics_v2_cases.json').read_text())


def expected_answers():
    # Synthetic scorer fixtures, deliberately NOT presented as model answers.
    return [{'id':r['id'],'verdict':r['expected'],'quote':r['source'],'reason':'测试评分器'} for r in DATA['cases']]


def test_blind_payload_never_leaks_expected_labels():
    request=review_cases(DATA)
    assert all('expected' not in r for r in request['cases'])
    assert all('expected' in r for r in DATA['cases'])
    assert request['not_professor_quotes'] is True


def test_correct_scoring_fixture_is_not_a_production_approval():
    result=score(DATA,expected_answers())
    assert result['passed'] and not result['production_approval']
    assert result['case_count']==13


@pytest.mark.parametrize('identity', [r['id'] for r in DATA['cases']])
def test_every_positive_and_negative_case_catches_an_incorrect_label(identity):
    answers=expected_answers()
    a=next(a for a in answers if a['id']==identity)
    a['verdict']='invalid' if a['verdict']=='valid' else 'valid'
    result=score(DATA,answers)
    assert not result['passed']
    assert identity in result['false_accepts']+result['false_rejects']+result['unresolved_errors']


@pytest.mark.parametrize('mutation',['missing','duplicate','foreign','quote','reason'])
def test_incomplete_or_unbound_eval_is_rejected(mutation):
    answers=expected_answers()
    if mutation=='missing':answers.pop()
    elif mutation=='duplicate':answers[1]=deepcopy(answers[0])
    elif mutation=='foreign':answers[0]['id']='foreign'
    elif mutation=='quote':answers[0]['quote']='未说过的话'
    else:answers[0]['reason']=''
    with pytest.raises(ValueError):score(DATA,answers)
