"""Argument-shape eval set: check a prompt change on short cases, one call each.

Each case is one shape of how he argues, with the owner's ruling as checks the
program can grade (docs/wang-knowledge-platform/10-extraction/argument_shape_eval_set.md).
A case names a saved call in an existing run (its lecture group and stage); the
runner re-asks that call with the *current* prompt and schema and leaves every
upstream answer as it was saved, so a Claims-prompt change costs one call per
group it is tested on, not a lecture run. Cases sharing a call share its answer.

Checks locate things by his words, never by sentence or Claim IDs, so a case
survives regrouping. A keyword list means all of them; "a|b" means either.
"""
from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CASES = Path(__file__).parent / 'argument_golden' / 'shapes.json'
RUNS = Path('/opt/homebrew/var/www/church/web/argument-layer-runs')
TASKS = {'claims': 'creation_claims', 'steps': 'creation_argument_steps', 'records': 'creation_step_records'}
FOLDER = {'claims': 'claims', 'steps': 'steps', 'records': 'materials'}


def norm(text):
    return re.sub(r'[\s　，。、；：？！「」『』（）()…,.;:?!"\'~—\-]', '', text or '')


def has(text, words):
    text = norm(text)
    return all(any(norm(alt) in text for alt in w.split('|')) for w in words)


def saved_request(case):
    root = RUNS / case['run'] / case['group'] / FOLDER[case['stage']]
    found = sorted(root.glob('gpt/*/request.json')) or sorted(root.glob('*/gpt/*/request.json'))
    found = [p for p in found if json.loads(p.read_text())['input']['task'] == TASKS[case['stage']]]
    if len(found) != 1:
        raise ValueError(f"{case['id']}: expected one saved {case['stage']} call in {root}, found {len(found)}")
    return json.loads(found[0].read_text())


def current_request(saved, stage):
    """The saved input under today's prompt and schema."""
    from backend.pipeline import claim_creation_flash as flash
    prompt = {'claims': flash.CLAIMS, 'steps': flash.STEPS, 'records': flash.STEP_RECORDS}[stage]
    contract = flash.schemas(compact_audit=True, grouped_package=True)[{'claims': 'claims', 'steps': 'steps', 'records': 'step_records'}[stage]]
    return {**saved, 'system': prompt, 'schema': contract}


# ---------------------------------------------------------------- checks

def _sentences(request, fragment):
    ids = [s['sentence_id'] for s in request['input']['sentences'] if norm(fragment) in norm(s['text'])]
    if not ids:
        raise ValueError(f'no sentence holds 「{fragment}」')
    return set(ids)


def _claims(request, answer):
    """Claim statements this call can see: its own new ones and those it was given."""
    given = request['input'].get('claims') or {}
    if isinstance(given, list):
        given = {c.get('claim_id') or c.get('id'): c.get('statement', '') for c in given}
    return dict(given)


def check(case_check, request, answer):
    kind = case_check['check']
    if kind in ('claim', 'no_claim', 'at_most', 'every_with', 'apart', 'no_word'):
        new = [c['statement'] for c in answer['candidates'] if not c.get('existing_claim_id')]
        if kind == 'claim':
            return any(has(s, case_check['all']) for s in new)
        if kind == 'no_claim':
            return not any(has(s, case_check['all']) for s in new)
        if kind == 'at_most':
            return sum(has(s, case_check['all']) for s in new) <= case_check['max']
        if kind == 'every_with':
            return all(has(s, case_check['then']) for s in new if has(s, case_check['if']))
        if kind == 'apart':
            a, b = case_check['a'], case_check['b']
            return any(has(s, a) for s in new) and any(has(s, b) for s in new) and not any(has(s, a) and has(s, b) for s in new)
        if kind == 'no_word':
            return not any(re.search(case_check['pattern'], s) for s in new)
    if kind in ('in_digression', 'not_digression', 'evidence_for', 'returned', 'kept', 'question_open'):
        claims = _claims(request, answer)
        if kind in ('returned', 'kept'):
            back = {r['claim_id']: r['kind'] for r in answer['not_claims']}
            ids = [cid for cid, s in claims.items() if has(s, case_check['all'])]
            if not ids:
                raise ValueError(f"no Claim given to this call says {case_check['all']}")
            if kind == 'kept':
                return not any(cid in back for cid in ids)
            return any(back.get(cid) == case_check['kind'] for cid in ids)
        ids = _sentences(request, case_check['sentence'])
        digressed = {s for d in answer['digressions'] for s in d['sentence_ids']}
        if kind == 'in_digression':
            return ids <= digressed
        if kind == 'not_digression':
            return not ids & digressed
        steps = [s for s in answer['steps'] if ids & set(s['sentence_ids'])]
        if kind == 'question_open':
            return bool(steps) and all(s['line'].lstrip().startswith(('问题：', '問題：')) and
                                       not [t for t in s['supports'] if t in claims] for s in steps)
        if kind == 'evidence_for':
            want = {cid for cid, s in claims.items() if has(s, case_check['claim'])}
            return bool(steps) and all(set(s['supports']) & want for s in steps)
    if kind == 'record_kind':
        steps = request['input']['argument_steps']
        ids = {s['step_id'] for s in steps if norm(case_check['sentence']) in norm(s['text'])}
        if not ids:
            raise ValueError(f"no step holds 「{case_check['sentence']}」")
        return all(r['kind'] == case_check['kind'] for r in answer['records'] if r['step_id'] in ids)
    raise ValueError(f'unknown check {kind}')


# ---------------------------------------------------------------- running

def plan(cases, only=None):
    """The distinct calls these cases need: one per (run, group, stage)."""
    calls = {}
    for case in cases:
        if only and case['stage'] not in only:
            continue
        calls.setdefault((case['run'], case['group'], case['stage']), []).append(case)
    return calls


def run(cases, out, ports, *, only=None, workers=4):
    """``ports`` is a function giving a fresh client per call, or None to grade saved answers.

    One client per call, never shared: the subscription client keeps its working
    folder on itself, and four threads sharing one wrote into each other's
    folders (2026-11-16 eval run, three calls refused for schema drift)."""
    from backend.pipeline import evidence_refresh as e
    out = Path(out)
    calls = plan(cases, only)

    def one(key):
        run_name, group, stage = key
        if ports is None:
            # Baseline: grade the answer the run saved under its own prompt, no call.
            saved = saved_request(calls[key][0])
            folder = next(p.parent for p in (RUNS / run_name / group / FOLDER[stage]).rglob('request.json')
                          if json.loads(p.read_text()) == saved)
            answer = json.loads((folder / 'response.json').read_text())
            return key, saved, answer.get('response', answer), None
        request = current_request(saved_request(calls[key][0]), stage)
        try:
            answer = e.recorded_call(out / run_name / group / stage, 'gpt', request, ports().repair)
        except Exception as exc:  # a failed call fails its cases; it is reported, never retried
            return key, request, None, f'{type(exc).__name__}: {exc}'
        return key, request, answer, None

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, request, answer, error in pool.map(one, calls):
            for case in calls[key]:
                results = []
                for c in case['checks']:
                    try:
                        ok = None if error else check(c, request, answer)
                        results.append({**c, 'pass': ok})
                    except ValueError as exc:
                        results.append({**c, 'pass': None, 'error': str(exc)})
                rows.append({'id': case['id'], 'shape': case['shape'], 'use': case['use'], 'confirmed': case.get('confirmed', True),
                             'error': error, 'checks': results,
                             'pass': error is None and all(r['pass'] for r in results)})
    report = {'cases': len(rows), 'calls': len(calls), 'by_shape': {}, 'rows': rows}
    for r in rows:
        s = report['by_shape'].setdefault(r['shape'], {'train': [0, 0], 'held_out': [0, 0]})
        s[r['use']][0] += r['pass']; s[r['use']][1] += 1
    out.mkdir(parents=True, exist_ok=True)
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return report


def main():
    p = argparse.ArgumentParser(description='Run the argument-shape eval set against the current prompts')
    p.add_argument('--out', required=True, help='folder for this eval run')
    p.add_argument('--stage', action='append', choices=sorted(TASKS), help='only cases of this stage (repeatable)')
    p.add_argument('--cases', default=str(CASES))
    p.add_argument('--saved', action='store_true', help='grade the saved answers (the old prompt), no calls')
    a = p.parse_args()
    if a.saved:
        report = run(json.loads(Path(a.cases).read_text())['cases'], a.out, None, only=a.stage)
        for r in report['rows']:
            print('PASS' if r['pass'] else 'FAIL', 'confirmed' if r['confirmed'] else 'unconfirmed', r['id'], r['error'] or '',
                  [(c['check'], c.get('error')) for c in r['checks'] if not c['pass']])
        return
    from dotenv import load_dotenv
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    from backend.pipeline.claim_refresh import Models
    load_dotenv('.env')
    ports = lambda: Models(CodexSubscriptionClient(model='gpt-6.1-sol', reasoning_effort='medium', max_output_tokens=16000),
                           Path(a.out).resolve())
    report = run(json.loads(Path(a.cases).read_text())['cases'], a.out, ports, only=a.stage)
    for r in report['rows']:
        mark = 'PASS' if r['pass'] else 'FAIL'
        print(mark, r['use'], r['shape'], r['id'], r['error'] or '', [c['check'] for c in r['checks'] if not c['pass']])
    print(json.dumps({k: v for k, v in report.items() if k != 'rows'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
