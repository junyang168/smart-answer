import copy
import json
import pytest
from backend.pipeline import chapter_slide_regression as ev
from backend.pipeline import chapter_slides as slides
from backend.pipeline.chapter_material import CHAPTERS


def dataset():return json.loads(ev.FIXTURE.read_text())


def test_gold_and_diverse_actual_source_cases_remain_in_test_set():
    data=dataset()
    assert len(data['cases'])>=10
    assert len({c['id'] for c in data['cases']})==len(data['cases'])
    gold=next(c for c in data['cases'] if c['id']=='gold-decalogue-direct-voice')
    assert any('他回答：十誡也不必遵守'==p['text'] for s in gold['known_bad'] for p in s['points'])
    assert gold['must_fail']=='voice'
    assert all(c['pieces'] and c['origin']['selection_sha256'] for c in data['cases'])
    assert {'quoted-scripture','peter-conclusion-not-early','greek-tense','story-argument','gold-teachable-question-before-answer'}<={c['id'] for c in data['cases']}


def test_generation_does_not_receive_expected_answers_or_negative_examples():
    prompt=ev.generation_input(dataset())
    assert 'known_bad' not in prompt and 'must_fail' not in prompt and 'criterion' not in prompt
    assert '我說：「你錯了！」' in prompt  # Actual source is preserved; not sanitized into an answer.


def test_voice_rule_is_shared_by_generation_revision_and_review():
    assert slides.SLIDE_VOICE_RULES in slides.system_prompt(CHAPTERS['mat-5-1-48'])
    assert slides.SLIDE_VOICE_RULES in ev.review_system()
    assert '其他人的立場' in slides.SLIDE_VOICE_RULES
    assert '不挑無關緊要' in ev.review_system()


def example_answers():
    data=dataset()
    answer={'cases':[{'id':c['id'],'slides':c['known_bad']} for c in data['cases']]}
    results=[]
    for c in data['cases']:
        results.append({'id':c['id']+'/generated','checks':[{'criterion_id':k['id'],'passed':True,'reason':'unit test'} for k in c['criteria']]})
        results.append({'id':c['id']+'/counterexample','checks':[{'criterion_id':c['must_fail'],'passed':False,'reason':'known error'}]})
    return data,answer,{'results':results}


def test_counterexample_acceptance_fails_even_if_all_generated_candidates_pass(monkeypatch):
    data,answer,judgement=example_answers()
    monkeypatch.setattr(slides,'check',lambda *a:([],[],[]))
    assert ev.evaluate(data,answer,judgement)['status']=='passed'
    judgement['results'][1]['checks'][0]['passed']=True
    assert ev.evaluate(data,answer,judgement)['status']=='failed'


@pytest.mark.parametrize('target',['generation','judgement','criteria'])
def test_missing_or_duplicate_verdicts_cannot_pass(target):
    data,answer,judgement=example_answers()
    if target=='generation':answer['cases'].append(copy.deepcopy(answer['cases'][0]))
    if target=='judgement':judgement['results'].pop()
    if target=='criteria':judgement['results'][0]['checks'].pop()
    with pytest.raises(ValueError):ev.evaluate(data,answer,judgement)


def test_prompt_changes_invalidate_saved_eval_binding(monkeypatch):
    before=ev.binding(dataset())
    monkeypatch.setattr(slides,'SLIDE_VOICE_RULES',slides.SLIDE_VOICE_RULES+' 新規則')
    assert ev.binding(dataset())!=before


def test_actual_model_baseline_is_bound_to_current_prompts_and_passes_whole_set():
    baseline=json.loads(ev.FIXTURE.with_name('generation_eval_baseline.json').read_text())
    assert baseline['binding']==ev.binding(dataset()), 'Prompt/test-set changed: run fresh model Eval, keep owner golden unchanged'
    assert ev.evaluate(dataset(),baseline['answer'],baseline['judgement'])['status']=='passed'
