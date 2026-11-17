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
import hashlib
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


def without_claims(request, drop):
    """A step case may need the Claim list the owner ruled, not the one the old run made.

    Lecture 2 G004's saved step input still carries the story summary and the
    divided 神造男造女 Claim, so a step pass reading it must hang the stories
    somewhere wrong. ``drop`` lists keywords of Claims the owner ruled out."""
    if not drop:
        return request
    claims = {k: v for k, v in request['input']['claims'].items() if not any(has(v, words) for words in drop)}
    return {**request, 'input': {**request['input'], 'claims': claims}}


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


SIDES = {'all': 'meaning', 'a': 'a_meaning', 'b': 'b_meaning', 'if': 'if_meaning', 'then': 'then_meaning', 'claim': 'meaning'}


def check(case_check, request, answer, match=None):
    """``match(statement, side)`` says whether a Claim states one side of the check.
    By default it looks for the check's keywords; ``meaning_matcher`` asks Jev instead."""
    kind = case_check['check']
    match = ruled(case_check, match or (lambda s, side: has(s, case_check[side])))
    if kind in ('claim', 'no_claim', 'at_most', 'every_with', 'apart', 'no_word'):
        new = [c['statement'] for c in answer['candidates'] if not c.get('existing_claim_id')]
        if kind == 'claim':
            return any(match(s, 'all') for s in new)
        if kind == 'no_claim':
            return not any(match(s, 'all') for s in new)
        if kind == 'at_most':
            return sum(match(s, 'all') for s in new) <= case_check['max']
        if kind == 'every_with':
            return all(match(s, 'then') for s in new if match(s, 'if'))
        if kind == 'apart':
            return any(match(s, 'a') for s in new) and any(match(s, 'b') for s in new) and not any(match(s, 'a') and match(s, 'b') for s in new)
        if kind == 'no_word':
            return not any(re.search(case_check['pattern'], s) for s in new)
    if kind in ('in_digression', 'not_digression', 'evidence_for', 'returned', 'kept', 'question_open'):
        claims = _claims(request, answer)
        if kind in ('returned', 'kept'):
            back = {r['claim_id']: r['kind'] for r in answer['not_claims']}
            ids = [cid for cid, s in claims.items() if match(s, 'all')]
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
            want = {cid for cid, s in claims.items() if match(s, 'claim')}
            return bool(steps) and all(set(s['supports']) & want for s in steps)
    if kind == 'record_kind':
        steps = request['input']['argument_steps']
        ids = {s['step_id'] for s in steps if norm(case_check['sentence']) in norm(s['text'])}
        if not ids:
            raise ValueError(f"no step holds 「{case_check['sentence']}」")
        return all(r['kind'] == case_check['kind'] for r in answer['records'] if r['step_id'] in ids)
    raise ValueError(f'unknown check {kind}')


MEANING = ('`meaning` 是負責人裁定的一個意思。`claim` 是另一個模型寫的一條 Claim。'
           '`claim` 有沒有說出 `meaning` 這個意思？措辭可以完全不同；只說了其中一小部分、說了相反的意思，或只是提到同一個題目，都不算。')
THRESHOLD = 0.5


def ruled(case_check, match):
    """The owner's answer on the review page wins for a Claim he was asked about."""
    rulings = case_check.get('ruled') or {}
    return lambda s, side: rulings[side][s] if s in rulings.get(side, {}) else match(s, side)


def disputed(case_check, request, answer, match):
    """(side, Claim) pairs where the keywords and Jev say different things, not yet ruled."""
    rulings = case_check.get('ruled') or {}
    out = []
    for side, field in SIDES.items():
        if side in case_check and field in case_check:
            for s in dict.fromkeys(statements_of(request, answer)):
                # Only the Claim Jev picked: one question per side, not one per Claim the words touched.
                if s not in rulings.get(side, {}) and match(s, side) and not has(s, case_check[side]):
                    out.append({'side': side, 'meaning': case_check[field], 'statement': s})
    return out


def statements_of(request, answer):
    new = [c['statement'] for c in answer.get('candidates', []) if not c.get('existing_claim_id')]
    return new + list(_claims(request, answer).values())


def meaning_matcher(case_check, request, answer, root, ports):
    """Jev picks which one Claim, if any, states each side's meaning.

    A choice among the Claims, not a score per Claim: scored one by one, Jev
    matched any Claim on the same topic (「心裡恨人」 for 「看重內心」), so the
    best match is asked for instead. Jev only picks; ``grade`` keeps a result
    only when it agrees with the keyword check and lists every disagreement for
    a person (owner 2026-11-17: Jev is not used for a decision).
    """
    from backend.pipeline import claim_flash_common as f
    table = {}
    statements = list(dict.fromkeys(statements_of(request, answer)))
    for side, field in SIDES.items():
        if side not in case_check or field not in case_check or not statements:
            continue
        criteria = {f'C{i}': s for i, s in enumerate(statements)}
        criteria['none'] = '沒有任何一條說出這個意思。'
        question = {'type': 'choice', 'criteria': criteria, 'instructions':
                    '`meaning` 是負責人裁定的一個意思。下面每個選項是另一個模型寫的一條 Claim。哪一條說出了 `meaning` 這個意思？'
                    '措辭可以完全不同；只說了其中一小部分、說了相反的意思，或只是談同一個題目，都不算；都不算就選 none。'}
        pick = f.jev(Path(root) / 'meaning', ports, {'meaning': case_check[field]}, {'Q0': question})['Q0']
        for i, s in enumerate(statements):
            table[s, side] = float(pick == f'C{i}')
    return (lambda s, side: table.get((s, side), 0.0) >= THRESHOLD), table


def grade(case, request, answer, *, root=None, ports=None):
    """Each check by keywords and, where the case states a meaning, by Jev; they must agree."""
    out = []
    for c in case['checks']:
        row = dict(c)
        try:
            row['keyword'] = check(c, request, answer)
            if ports is not None and any(field in c for field in SIDES.values()):
                match, table = meaning_matcher(c, request, answer, root, ports)
                row['meaning'] = check(c, request, answer, match=match)
                row['scores'] = {f'{side}: {s[:30]}': round(p, 2) for (s, side), p in table.items() if p >= 0.2}
                row['pass'] = row['keyword'] if row['keyword'] == row['meaning'] else None
                row['needs_look'] = row['keyword'] != row['meaning']
                if row['needs_look']:
                    row['disputed'] = disputed(c, request, answer, match)
            else:
                row['pass'] = row['keyword']
        except ValueError as exc:
            row.update({'pass': None, 'error': str(exc)})
        out.append(row)
    return out


# ---------------------------------------------------------------- running

def plan(cases, only=None):
    """The distinct calls these cases need: one per (run, group, stage, dropped Claims)."""
    calls = {}
    for case in cases:
        if only and case['stage'] not in only:
            continue
        calls.setdefault((case['run'], case['group'], case['stage'], json.dumps(case.get('drop_claims'), ensure_ascii=False)), []).append(case)
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
        run_name, group, stage, _ = key
        if ports is None:
            # Baseline: grade the answer the run saved under its own prompt, no call.
            saved = saved_request(calls[key][0])
            folder = next(p.parent for p in (RUNS / run_name / group / FOLDER[stage]).rglob('request.json')
                          if json.loads(p.read_text()) == saved)
            answer = json.loads((folder / 'response.json').read_text())
            return key, saved, answer.get('response', answer), None
        request = current_request(saved_request(calls[key][0]), stage)
        request = without_claims(request, calls[key][0].get('drop_claims'))
        try:
            answer = e.recorded_call(out / run_name / group / stage, 'gpt', request, ports().repair)  # folder is keyed by the request digest
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


def saved_answer(case, answers=None):
    """The request and answer a case grades: from an eval run's folder, or the run it names (old prompt)."""
    if answers is None:
        request = without_claims(saved_request(case), case.get('drop_claims'))
        folder = next(p.parent for p in (RUNS / case['run'] / case['group'] / FOLDER[case['stage']]).rglob('request.json')
                      if json.loads(p.read_text()) == saved_request(case))
    else:
        want = without_claims(current_request(saved_request(case), case['stage']), case.get('drop_claims'))['input']
        found = [p.parent for p in (Path(answers) / case['run'] / case['group'] / case['stage']).rglob('request.json')
                 if json.loads(p.read_text())['input'] == want and (p.parent / 'response.json').exists()]
        if not found:
            raise ValueError(f"{case['id']}: no answer in {answers} for this input")
        folder = found[-1]
        request = json.loads((folder / 'request.json').read_text())
    answer = json.loads((folder / 'response.json').read_text())
    return request, answer.get('response', answer)


class JevOnly:
    """Jev's endpoint without a GPT client: grading asks Jev only."""
    from backend.pipeline.evidence_refresh import Models as _M
    judge, judge_preflight = _M.judge, _M.judge_preflight


def grade_all(cases, answers=None, *, out=None, jev=False):
    rows = []
    for case in cases:
        try:
            request, answer = saved_answer(case, answers)
            checks = grade(case, request, answer, root=out, ports=JevOnly() if jev else None)
            error = None
        except ValueError as exc:
            checks, error = [], str(exc)
        rows.append({'id': case['id'], 'shape': case['shape'], 'confirmed': case.get('confirmed', True), 'error': error,
                     'checks': checks, 'pass': None if error or any(c.get('needs_look') for c in checks) else all(c['pass'] for c in checks),
                     'needs_look': any(c.get('needs_look') for c in checks)})
    return rows


LECTURE = {'lecture1': '2011 NYSC 專題：馬太福音釋經（二）1', 'lecture2': '2011 NYSC 專題：馬太福音釋經（二）2'}
SAYS = [{'key': 'says', 'label': '說了這個意思'}, {'key': 'not_says', 'label': '沒有說這個意思'}]


def lecture_of(run):
    return next(v for k, v in LECTURE.items() if k in run.split('/')[0])


def look_questions(rows, cases):
    """One review-page question per disputed (case, check, side, Claim)."""
    from backend.pipeline.argument_review import question_id
    by_id = {c['id']: c for c in cases}
    out = []
    for r in rows:
        case = by_id[r['id']]
        for n, check_row in enumerate(r['checks']):
            for d in check_row.get('disputed', []):
                item = f"eval:{case['id']}:{n}:{d['side']}:{hashlib.sha256(d['statement'].encode()).hexdigest()[:8]}"
                lecture = lecture_of(case['run'])
                out.append({'question_id': question_id(lecture, case['group'], item), 'lecture': lecture,
                            'group_id': case['group'], 'item_id': item, 'kind': 'rule',
                            'payload': {'question': f"上面這條 Claim 有沒有說出這個意思：「{d['meaning']}」？",
                                        'options': SAYS, 'sentences': [], 'viewpoints': [d['statement']],
                                        'eval': {'case': case['id'], 'check': n, 'side': d['side'], 'statement': d['statement']}}})
    return out


def settled(ar, conn, qid):
    """An eval question is the owner's to answer: a ruling, or the answers if they all agree.
    The 同工 quorum is for content review, not for whether a sentence says a ruling."""
    s = ar.state(conn, qid)
    if s['status'] in ('ruled', 'final'):
        return s['choice']
    choices = {c for a in ar.active_answers(conn, qid) if (c := ar.resolved_choice(a, ar.followups_for(conn, a['answer_id'])))}
    return choices.pop() if len(choices) == 1 else None


def apply_rulings(cases_doc, decided):
    """Write settled eval answers into the cases: ``decided`` is [(payload['eval'], choice)]."""
    by_id = {c['id']: c for c in cases_doc['cases']}
    changed = 0
    for ev, choice in decided:
        if choice not in ('says', 'not_says'):
            continue
        side = by_id[ev['case']]['checks'][ev['check']].setdefault('ruled', {}).setdefault(ev['side'], {})
        if side.get(ev['statement']) != (choice == 'says'):
            side[ev['statement']] = choice == 'says'
            changed += 1
    return changed


def main():
    p = argparse.ArgumentParser(description='Run the argument-shape eval set against the current prompts')
    p.add_argument('--out', required=True, help='folder for this eval run')
    p.add_argument('--stage', action='append', choices=sorted(TASKS), help='only cases of this stage (repeatable)')
    p.add_argument('--cases', default=str(CASES))
    p.add_argument('--saved', action='store_true', help='grade the answers the named runs saved (the old prompt); no GPT call')
    p.add_argument('--grade', help='grade the answers in this earlier eval folder; no GPT call')
    p.add_argument('--jev', action='store_true', help='also check each stated meaning with Jev; disagreements need a look')
    p.add_argument('--to-review', action='store_true', help='put each needs-a-look Claim on the review page')
    p.add_argument('--from-review', action='store_true', help='write the review page answers into the cases; no grading')
    a = p.parse_args()
    if a.from_review:
        from dotenv import load_dotenv
        from backend.pipeline import argument_review as ar
        load_dotenv('.env')
        doc = json.loads(Path(a.cases).read_text())
        with ar.connect() as conn:
            rows = conn.execute("SELECT question_id, payload->'eval' FROM argument_review.questions WHERE item_id LIKE 'eval:%%'").fetchall()
            decided = [(ev, settled(ar, conn, qid)) for qid, ev in rows]
        changed = apply_rulings(doc, decided)
        Path(a.cases).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + '\n')
        print(json.dumps({'questions': len(rows), 'settled': sum(c is not None for _, c in decided), 'written': changed}))
        return
    if a.saved or a.grade:
        from dotenv import load_dotenv
        load_dotenv('.env')
        rows = grade_all(json.loads(Path(a.cases).read_text())['cases'], a.grade, out=Path(a.out), jev=a.jev)
        for r in rows:
            mark = 'LOOK' if r['needs_look'] else 'PASS' if r['pass'] else 'FAIL'
            print(mark, 'confirmed' if r['confirmed'] else 'unconfirmed', r['id'], r['error'] or '',
                  [(c['check'], c.get('keyword'), c.get('meaning'), c.get('scores')) for c in r['checks'] if c['pass'] is not True])
        Path(a.out).mkdir(parents=True, exist_ok=True)
        (Path(a.out) / 'grade.json').write_text(json.dumps(rows, ensure_ascii=False, indent=1))
        if a.to_review:
            from backend.pipeline import argument_review as ar
            qs = look_questions(rows, json.loads(Path(a.cases).read_text())['cases'])
            with ar.connect() as conn:
                print(json.dumps({'questions': len(qs), 'added': ar.publish_questions(conn, qs, hashlib.sha256(Path(a.cases).read_bytes()).hexdigest())}))
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
