"""Frozen source examples for the whole slide-generation contract.

Generate and revise with production prompts, check outputs mechanically, and evaluate
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
JUDGE_RULES = 'argument_context 若存在，是本案例範圍相關的真實論證層節錄；跨段 Claim 只核對本案例原文實際講到的部分，不要求搬入範圍外的理由。有論證層先核對其角色及現有要點，再以原文確認，不能只用沒有故事開場點或空白秒數判缺漏。依同步投影片規則逐項判定。只對照這份原文與候選，不做神學裁決。每個案例每項 criterion_id 回 passed 及具體理由；意思正確不要求逐字命中。passed=false 表示這項確有須修改的缺陷。非 golden 精確時序依正式規則：提前超過30秒或延後超過5秒才不合格（負責人 2026-11-18：要點出現太晚是基本錯誤）；閾值內差距只記理由，passed=true。golden 已明確規定不得提前或指定句子的條件仍須精確遵守，不適用上述小差距豁免。\n'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def review_system():
    return slides.review_system_prompt()


def binding(dataset):
    return dict(test_set_sha256=digest(dataset), generation_prompt_sha256=digest(slides.system_prompt(CHAPTERS[dataset['passage']])),
                review_prompt_sha256=digest(review_system()), judge_rules_sha256=digest(JUDGE_RULES), common_criteria_sha256=digest(COMMON_CRITERIA),
                revision_prompt_sha256=digest(slides.system_prompt(CHAPTERS[dataset['passage']])+slides._REVISE_TAIL))


def object_schema(properties):
    return dict(type='object',properties=properties,required=list(properties),additionalProperties=False)


def array_schema(items):
    return dict(type='array',items=items)


def generation_schema():
    return object_schema({'cases':array_schema(object_schema({'id':{'type':'string'},
        'slides':slides.response_schema(None)['properties']['slides']}))})


JUDGEMENT = object_schema({'results':array_schema(object_schema({'id':{'type':'string'},
    'checks':array_schema(object_schema({'criterion_id':{'type':'string'},'passed':{'type':'boolean'},'reason':{'type':'string'}}))}))})


def generation_input(dataset, *, revisions=False):
    cases=[]
    for case in dataset['cases']:
        if bool(case.get('revision')) != revisions:
            continue
        decisions={'pieces':case['pieces'],'decisions':[{'id':p['id'],'keep':True} for p in case['pieces']]}
        prompt=(slides.revision_user_prompt(case['lecture'],decisions,case['revision']['previous_slides'],case['revision']['findings'])
                if revisions else slides.user_prompt(case['lecture'],decisions))
        cases.append({'id':case['id'],'input':prompt})
    action='修訂' if revisions else '製作'
    return f'以下是互相獨立的測試片段，各自從 S0001 起。逐個按同一規則{action}投影片，回傳各 id 及 slides。不得混用案例原文。\n'+json.dumps(cases,ensure_ascii=False)


COMMON_CRITERIA = [
    {'id':'scope-faithfulness', 'requirement':'原文立場、說話者、否定、限制與因果忠實；不添加未講的答案。'},
    {'id':'scope-coverage', 'requirement':'從原文反向核對承重問題、理由、結論、經文；非承重細節可省，已適當呈現的意思不要求重複。'},
    {'id':'scope-scripture', 'requirement':'實際引用經文、原文字義與短註歸屬正確；一般譯詞不冒充本節實際字詞。'},
    {'id':'scope-presentation', 'requirement':'原聲口吻、完整標題範圍、要點可理解，不混淆他人立場；依論證層與原文核對故事作用，不因缺開場點或只有標題的秒數判錯。'},
    {'id':'scope-timing', 'requirement':'問題與答案、故事與結論同步；答案不提前；一般時間差遵照正式閾值及 golden。'},
]


def criteria(case, *, counterexample=False):
    specific = [x for x in case['criteria'] if not counterexample or x['id']==case['must_fail']]
    return COMMON_CRITERIA + specific


def judge_input(dataset, answer):
    actual={row['id']:row['slides'] for row in answer['cases']}
    if set(actual)!={c['id'] for c in dataset['cases']} or len(actual)!=len(answer['cases']):
        raise ValueError('Generated case IDs differ from frozen test set')
    packets=[]
    for c in dataset['cases']:
        lines=slides.sentences(c['pieces'])
        argument = {'argument_context': c['argument_context']} if c.get('argument_context') else {}
        packets.append({'id':c['id']+'/generated','source':lines,'criteria':criteria(c),'slides':actual[c['id']], **argument})
        # Do not reveal the expected rejection to the evaluator.
        packets.append({'id':c['id']+'/counterexample','source':lines,
                        'criteria':criteria(c,counterexample=True), 'slides':c['known_bad'], **argument})
    for c in dataset.get('calibration', []):
        packets.append({k:v for k,v in c.items() if k not in ('expected',)})
    return JUDGE_RULES + json.dumps(packets,ensure_ascii=False)


def evaluate(dataset, answer, judgement):
    problems=[]
    judge_input(dataset,answer)  # Also validates generation identities.
    actual={row['id']:row['slides'] for row in answer['cases']}
    judged={row['id']:row['checks'] for row in judgement['results']}
    ids={c['id']+suffix for c in dataset['cases'] for suffix in ('/generated','/counterexample')} | {c['id'] for c in dataset.get('calibration',[])}
    if set(judged)!=ids or len(judged)!=len(judgement['results']):
        raise ValueError('Evaluator omitted or duplicated case IDs')
    results=[]
    for c in dataset['cases']:
        mechanical,_,_=slides.check(actual[c['id']],c['pieces'],CHAPTERS[dataset['passage']])
        checks=judged[c['id']+'/generated']
        by_id={v['criterion_id']:v for v in checks}
        if set(by_id)!={x['id'] for x in criteria(c)} or len(by_id)!=len(checks):
            raise ValueError('Evaluator omitted or duplicated criteria')
        bad=judged[c['id']+'/counterexample']
        bad_by_id={v['criterion_id']:v for v in bad}
        if set(bad_by_id)!={x['id'] for x in criteria(c,counterexample=True)} or len(bad_by_id)!=len(bad):
            raise ValueError('Evaluator omitted or duplicated counterexample criteria')
        counter_rejected=bad_by_id[c['must_fail']]['passed'] is False
        passed=not mechanical and all(v['passed'] is True for v in checks) and counter_rejected
        if not passed:problems.append(c['id'])
        results.append(dict(id=c['id'],passed=passed,mechanical=mechanical,checks=checks,counterexample_rejected=counter_rejected,counterexample=bad))
    generation_problems=list(problems)
    for c in dataset.get('calibration', []):
        checks=judged[c['id']]
        by_id={v['criterion_id']:v for v in checks}
        if set(by_id)!=set(c['expected']) or len(by_id)!=len(checks):
            raise ValueError('Evaluator omitted or duplicated calibration criteria')
        passed=all(by_id[k]['passed'] is v for k,v in c['expected'].items())
        if not passed:problems.append(c['id'])
        results.append(dict(id=c['id'],passed=passed,checks=checks))
    return dict(schema_version='chapter-slide-generation-eval.v2',binding=binding(dataset),
                answer_sha256=digest(answer),judgement_sha256=digest(judgement),
                status='passed' if not problems else 'failed',
                generation_status='passed' if not generation_problems else 'failed',
                calibration_status='failed' if any(c['id'] in problems for c in dataset.get('calibration',[])) else 'passed',
                problems=problems,results=results)


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
            combined=[]
            for revisions,name in ((False,'creation.json'),(True,'revision.json')):
                if not any(bool(c.get('revision'))==revisions for c in dataset['cases']):continue
                saved=args.output/name
                if not saved.exists():
                    system=slides.system_prompt(CHAPTERS[dataset['passage']])+(slides._REVISE_TAIL if revisions else '')
                    result=client.generate_json(system,generation_input(dataset,revisions=revisions),generation_schema())
                    saved.write_text(json.dumps(result,ensure_ascii=False,indent=2))
                result=json.loads(saved.read_text())
                expected={c['id'] for c in dataset['cases'] if bool(c.get('revision'))==revisions}
                if {c['id'] for c in result['cases']}!=expected or len(result['cases'])!=len(expected):
                    raise ValueError('Generation/revision omitted or duplicated case IDs')
                combined.extend(result['cases'])
            answer_file.write_text(json.dumps({'cases':combined},ensure_ascii=False,indent=2))
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
