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
        results.append({'id':c['id']+'/generated','checks':[{'criterion_id':k['id'],'passed':True,'reason':'unit test'} for k in ev.criteria(c)]})
        results.append({'id':c['id']+'/counterexample','checks':[{'criterion_id':k['id'],'passed':False,'reason':'known error'} for k in ev.criteria(c,counterexample=True)]})
    results += [{'id':c['id'],'checks':[{'criterion_id':k,'passed':v,'reason':'calibration'} for k,v in c['expected'].items()]} for c in data.get('calibration',[])]
    return data,answer,{'results':results}


def test_counterexample_acceptance_fails_even_if_all_generated_candidates_pass(monkeypatch):
    data,answer,judgement=example_answers()
    monkeypatch.setattr(slides,'check',lambda *a:([],[],[]))
    assert ev.evaluate(data,answer,judgement)['status']=='passed'
    next(x for x in judgement['results'][1]['checks'] if x['criterion_id']==data['cases'][0]['must_fail'])['passed']=True
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


def test_actual_model_baseline_is_bound_reproducible_and_evaluator_is_calibrated():
    baseline=json.loads(ev.FIXTURE.with_name('generation_eval_baseline.json').read_text())
    assert baseline['binding']==ev.binding(dataset()), 'Prompt/test-set changed: run fresh model Eval, keep owner golden unchanged'
    report=ev.evaluate(dataset(),baseline['answer'],baseline['judgement'])
    assert report == baseline['report']
    assert report['calibration_status']=='passed'
    # Generation quality remains a separate, failing gate; replay must not hide it.
    assert report['generation_status']=='failed'
    assert set(report['problems'])=={'whole-topic-title','story-argument'}
    assert report['status']=='failed'


def test_review_uses_identical_policy_in_production_and_regression():
    assert ev.review_system() == slides.review_system_prompt()
    assert slides.INLINE_NOTE_RULES in ev.review_system()
    assert slides.REVIEW_COMPLETENESS_RULES in ev.review_system()


def test_judge_checks_all_dimensions_without_revealing_calibration_answers():
    data,answer,_=example_answers()
    prompt=ev.judge_input(data,answer)
    assert '"expected"' not in prompt
    packets=json.loads(prompt[len(ev.JUDGE_RULES):])
    for packet in packets:
        if packet['id'].endswith(('/generated','/counterexample')):
            assert {x['id'] for x in ev.COMMON_CRITERIA} <= {x['id'] for x in packet['criteria']}
    assert 'accepted_resolutions' in prompt


@pytest.mark.parametrize('case_id', ['calibration-comparison-in-point', 'calibration-wrong-original-restored', 'calibration-story-essential-not-every-detail'])
def test_false_positive_and_reversal_calibration_cannot_be_omitted_or_misjudged(case_id, monkeypatch):
    data,answer,judgement=example_answers()
    monkeypatch.setattr(slides,'check',lambda *a:([],[],[]))
    row=next(x for x in judgement['results'] if x['id']==case_id)
    row['checks'][0]['passed']=not row['checks'][0]['passed']
    assert case_id in ev.evaluate(data,answer,judgement)['problems']
