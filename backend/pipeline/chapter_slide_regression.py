"""Frozen source examples for the whole slide-generation contract.

Generate with the production prompt, check its output mechanically, and evaluate
semantic expectations plus known bad counterexamples. Saved reports bind the
prompt, test set and answers; replay makes zero model calls.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from backend.pipeline import chapter_slides as slides
from backend.pipeline.chapter_exposition import CHAPTER_GPT_TIMEOUT_SECONDS
from backend.pipeline.chapter_material import CHAPTERS
from backend.pipeline.chapter_slide_claims import RecordedClient

FIXTURE = Path(__file__).resolve().parents[1] / 'tests/fixtures/chapter_slides/generation_eval_set.json'
JUDGE_RULES = '依同步投影片規則逐項判定。只對照這份原文與候選，不做神學裁決。每個案例每項 criterion_id 回 passed 及具體理由；意思正確不要求逐字命中。passed=false 表示這項確有須修改的缺陷。非 golden 精確時序依正式規則：提前超過30秒或延後超過60秒才不合格；閾值內差距只記理由，passed=true。golden 已明確規定不得提前或指定句子的條件仍須精確遵守，不適用上述小差距豁免。\n'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def review_system():
    return (slides.REVIEW_PROMPT.replace('〈要點時間規則〉', slides.POINT_TIMING_RULES)
            .replace('〈標題規則〉', slides.SLIDE_TITLE_RULES)
            .replace('〈原聲投影片口吻〉', slides.SLIDE_VOICE_RULES))


def binding(dataset):
    return dict(test_set_sha256=digest(dataset), generation_prompt_sha256=digest(slides.system_prompt(CHAPTERS[dataset['passage']])),
                review_prompt_sha256=digest(review_system()), judge_rules_sha256=digest(JUDGE_RULES))


def object_schema(properties):
    return dict(type='object',properties=properties,required=list(properties),additionalProperties=False)


def array_schema(items):
    return dict(type='array',items=items)


def generation_schema():
    return object_schema({'cases':array_schema(object_schema({'id':{'type':'string'},
        'slides':slides.response_schema(None)['properties']['slides']}))})


JUDGEMENT = object_schema({'results':array_schema(object_schema({'id':{'type':'string'},
    'checks':array_schema(object_schema({'criterion_id':{'type':'string'},'passed':{'type':'boolean'},'reason':{'type':'string'}}))}))})


def generation_input(dataset):
    cases=[]
    for case in dataset['cases']:
        decisions={'pieces':case['pieces'],'decisions':[{'id':p['id'],'keep':True} for p in case['pieces']]}
        cases.append({'id':case['id'],'input':slides.user_prompt(case['lecture'],decisions)})
    return '以下是互相獨立的測試片段，各自從 S0001 起。逐個按同一生成規則製作投影片，回傳各 id 及 slides。不得混用案例原文。\n'+json.dumps(cases,ensure_ascii=False)


def judge_input(dataset, answer):
    actual={row['id']:row['slides'] for row in answer['cases']}
    if set(actual)!={c['id'] for c in dataset['cases']} or len(actual)!=len(answer['cases']):
        raise ValueError('Generated case IDs differ from frozen test set')
    packets=[]
    for c in dataset['cases']:
        lines=slides.sentences(c['pieces'])
        packets.append({'id':c['id']+'/generated','source':lines,'criteria':c['criteria'],'slides':actual[c['id']]})
        # Do not reveal the expected rejection to the evaluator.
        packets.append({'id':c['id']+'/counterexample','source':lines,
                        'criteria':[x for x in c['criteria'] if x['id']==c['must_fail']], 'slides':c['known_bad']})
    return JUDGE_RULES + json.dumps(packets,ensure_ascii=False)


def evaluate(dataset, answer, judgement):
    problems=[]
    judge_input(dataset,answer)  # Also validates generation identities.
    actual={row['id']:row['slides'] for row in answer['cases']}
    judged={row['id']:row['checks'] for row in judgement['results']}
    ids={c['id']+suffix for c in dataset['cases'] for suffix in ('/generated','/counterexample')}
    if set(judged)!=ids or len(judged)!=len(judgement['results']):
        raise ValueError('Evaluator omitted or duplicated case IDs')
    results=[]
    for c in dataset['cases']:
        mechanical,_,_=slides.check(actual[c['id']],c['pieces'],CHAPTERS[dataset['passage']])
        checks=judged[c['id']+'/generated']
        by_id={v['criterion_id']:v for v in checks}
        if set(by_id)!={x['id'] for x in c['criteria']} or len(by_id)!=len(checks):
            raise ValueError('Evaluator omitted or duplicated criteria')
        bad=judged[c['id']+'/counterexample']
        counter_rejected=len(bad)==1 and bad[0]['criterion_id']==c['must_fail'] and bad[0]['passed'] is False
        passed=not mechanical and all(v['passed'] is True for v in checks) and counter_rejected
        if not passed:problems.append(c['id'])
        results.append(dict(id=c['id'],passed=passed,mechanical=mechanical,checks=checks,counterexample_rejected=counter_rejected,counterexample=bad))
    return dict(schema_version='chapter-slide-generation-eval.v1',binding=binding(dataset),
                answer_sha256=digest(answer),judgement_sha256=digest(judgement),
                status='passed' if not problems else 'failed',problems=problems,results=results)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--generate',action='store_true',help='Explicitly use subscription model; otherwise replay saved answers')
    parser.add_argument('--model',default='gpt-6.1-sol')
    args=parser.parse_args()
    dataset=json.loads(FIXTURE.read_text())
    args.output.mkdir(parents=True,exist_ok=True)
    frozen=args.output/'binding.json'
    current=binding(dataset)
    if frozen.exists() and json.loads(frozen.read_text())!=current:
        raise ValueError('Prompt or fixture changed; choose a new evaluation directory')
    frozen.write_text(json.dumps(current,indent=2))
    if args.generate:
        from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
        client=RecordedClient(CodexSubscriptionClient(model=args.model,reasoning_effort='medium',timeout_seconds=CHAPTER_GPT_TIMEOUT_SECONDS,max_output_tokens=16000),args.output/'calls')
        answer_file=args.output/'generation.json'
        if not answer_file.exists():
            answer=client.generate_json(slides.system_prompt(CHAPTERS[dataset['passage']]),generation_input(dataset),generation_schema())
            answer_file.write_text(json.dumps(answer,ensure_ascii=False,indent=2))
        answer=json.loads(answer_file.read_text())
        judge_file=args.output/'judgement.json'
        if not judge_file.exists():
            judgement=client.generate_json(review_system(),judge_input(dataset,answer),JUDGEMENT)
            judge_file.write_text(json.dumps(judgement,ensure_ascii=False,indent=2))
    answer=json.loads((args.output/'generation.json').read_text())
    judgement=json.loads((args.output/'judgement.json').read_text())
    report=evaluate(dataset,answer,judgement)
    (args.output/'eval.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'status':report['status'],'problems':report['problems'],'report':str(args.output/'eval.json')}),flush=True)
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
