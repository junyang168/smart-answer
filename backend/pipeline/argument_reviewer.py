"""Independent review of a generated argument layer, one narrow question per item.

GPT writes the argument layer; a different model (Gemini 4 Argon, owner 2026-11-16) reads
the transcript and GPT's result and answers, for each item, one question the
project's rules can settle: does he state this Claim himself, is this 題外話
unsupported, is this Question answered, is this Position someone else's view that
he refutes. It never scores a lecture. The program keeps only the answers that
differ from GPT and checks that every quote the reviewer gives is in the
transcript, so a disagreement always points at his own words.

Hand-made golden cases are not used here; they calibrate this reviewer
(``calibrate``), because they cannot be written for every lecture
(owner 2026-11-16).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

CLAIM_VERDICTS = {
    'his_claim': '他自己說出、並在這裡證明或講明的判斷',
    'evidence_for': '用來證明另一條 Claim 的論據（經文讀法、律例的出處或目的、另一位使徒的規定、定義、例子）',
    'restates': '把另一條 Claim 再說一遍（說的是同一件事）；幾部分合起來得出的結論（蓋子）不是重述',
    'others_words': '故事或引述裡別人說的話，不是他的判斷',
    'unstated_lesson': '從故事或例子推出、他自己沒有說出的教訓',
    'digression': '題外話：批評別人卻沒有給經文或推理',
}
OTHER_VERDICTS = {
    'digression': {'unsupported': '沒有給經文或推理，只是評論（或襯托這種評論的故事）',
                   'supported': '他給了經文或推理，是論證'},
    'question': {'answered': '他在本組回答了', 'open': '他留到以後（或別講）才回答',
                 'not_a_question': '不是問題（是他直接講的判斷，或只是「對不對？」之類的口頭語）'},
    'position': {'refuted': '別人的看法，他駁斥了', 'not_refuted': '別人的看法，他沒有駁斥',
                 'not_others_view': '不是別人的看法（是他自己的話）'},
}
# What GPT's result already says about each item, in the reviewer's vocabulary.
GPT_SAYS = {'claim': 'his_claim', 'digression': 'unsupported', 'position': 'refuted'}

SYSTEM = (
    '你是獨立評審。王守仁教授是口語講課。另一個模型已經把這一段整理成論證層；你逐條回答下面的問題，'
    '只依原文和給定的定義判斷，不評他講得對不對，不打總分。'
    '每條都要從原文逐字引一句支持你的答案（quote 必須是原文中連續的一段，可以只取半句；跳過中間的話用「……」連接）。'
    '拿不準時照另一個模型的歸類回答。'
)


def norm(text):
    return re.sub(r'[\s　，。、；：？！「」『』（）()…,.;:?!"\'~]', '', text or '')


def _saved(folder, kind):
    found = sorted(Path(folder).glob(f'{kind}/gpt/*/response.json')) or sorted(Path(folder).glob(f'{kind}/*/gpt/*/response.json'))
    if not found:
        return None, None
    response = json.loads(found[0].read_text())
    request = json.loads((found[0].parent / 'request.json').read_text())
    return request, response.get('response', response)


def group_views(creation):
    """One view per group from the run's saved answers: sentences and GPT's items."""
    creation = Path(creation)
    result = json.loads((creation / 'creation-result.json').read_text())
    candidates = result['claim_candidates']
    views = []
    for gid in sorted(result['group_claim_ids']):
        request, steps = _saved(creation / gid, 'steps')
        _, records = _saved(creation / gid, 'materials')
        if steps is None or records is None:
            raise ValueError(f'{gid}: saved step or record answers are missing')
        sentences = request['input']['sentences']
        text = {s['sentence_id']: s['text'] for s in sentences}
        kind = {r['step_id']: r for r in records['records']}
        own = {}
        for row in steps['claim_text']:
            own.setdefault(row['claim_id'], []).extend(text[s] for s in row['sentence_ids'])
        support = {}
        for step in steps['steps']:
            for c in step['supports']:
                support.setdefault(c, []).append(step['line'])
        claims = [{'id': cid, 'statement': candidates[cid]['statement'], 'his_words': own.get(cid, []),
                   'support_lines': support.get(cid, [])}
                  for cid in result['group_claim_ids'][gid] if cid in candidates]
        others = []
        for step in steps['steps']:
            r = kind.get(step['step_id'])
            if r and r['kind'] in ('question', 'position'):
                others.append({'id': step['step_id'], 'kind': r['kind'], 'statement': r['statement'],
                               'text': ''.join(text[s] for s in step['sentence_ids']), 'answered_by': step['supports']})
        for n, d in enumerate(steps.get('digressions', []), 1):
            others.append({'id': f'D{n}', 'kind': 'digression', 'statement': d['why'],
                           'text': ''.join(text[s] for s in d['sentence_ids'])})
        views.append({'group_id': gid, 'sentences': sentences, 'claims': claims, 'others': others})
    return views


def schema():
    item = lambda verdicts: {'type': 'object', 'additionalProperties': False,
                             'required': ['id', 'verdict', 'target', 'quote', 'why'],
                             'properties': {'id': {'type': 'string'}, 'verdict': {'type': 'string', 'enum': sorted(verdicts)},
                                            'target': {'type': 'string'}, 'quote': {'type': 'string'}, 'why': {'type': 'string'}}}
    every = set(CLAIM_VERDICTS) | {v for d in OTHER_VERDICTS.values() for v in d}
    return {'name': 'argument_review', 'schema': {
        'type': 'object', 'additionalProperties': False, 'required': ['claims', 'others', 'missing'],
        'properties': {'claims': {'type': 'array', 'items': item(CLAIM_VERDICTS)},
                       'others': {'type': 'array', 'items': item(every)},
                       'missing': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                                   'required': ['statement', 'quote', 'why'],
                                   'properties': {'statement': {'type': 'string'}, 'quote': {'type': 'string'},
                                                  'why': {'type': 'string'}}}}}}}


def prompt(view):
    lines = ['## 原文（本組）'] + [f"{s['sentence_id']} {s['text']}" for s in view['sentences']]
    lines += ['', '## 每條 Claim：選一個 verdict', *[f'- {k}：{v}' for k, v in CLAIM_VERDICTS.items()],
              'evidence_for 與 restates 在 target 寫那條 Claim 的 ID；其餘 target 留空。他為這一條本身給了理由（原文字義、另一處經文）的，或把它當作一條教導說出（「聖經的觀念是……」）的，即使也用來支持別的 Claim，仍是 his_claim。', '']
    for c in view['claims']:
        lines.append(f"{c['id']}：{c['statement']}")
        lines.append(f"  被歸為他本人說的話：{' / '.join(c['his_words']) or '（無）'}")
        lines.append(f"  支持它的步驟：{' / '.join(c['support_lines']) or '（無）'}")
    lines += ['', '## 其他條目：按 kind 選 verdict']
    for kind, verdicts in OTHER_VERDICTS.items():
        lines.append(f'- {kind}：' + '；'.join(f'{k}＝{v}' for k, v in verdicts.items()))
    for o in view['others']:
        lines.append(f"{o['id']}（{o['kind']}）：{o['statement']}｜原文：{o['text']}")
    lines += ['', '## missing',
              '他明白講出、並在本組給了理由的判斷，若上面沒有任何一條 Claim 說它，列在 missing（statement 寫判斷，quote 引原句）。沒有就給空列表。',
              '輸出 claims（每條 Claim 一筆）、others（每個條目一筆）、missing。']
    return '\n'.join(lines)


def check(view, answer):
    """Reject an answer that skips an item or quotes words he did not say."""
    spoken = norm(''.join(s['text'] for s in view['sentences']))
    want = {c['id'] for c in view['claims']}
    if {a['id'] for a in answer['claims']} != want or len(answer['claims']) != len(want):
        raise ValueError(f"{view['group_id']}: review must answer each Claim once")
    kinds = {o['id']: o['kind'] for o in view['others']}
    if {a['id'] for a in answer['others']} != set(kinds) or len(answer['others']) != len(kinds):
        raise ValueError(f"{view['group_id']}: review must answer each other item once")
    for a in answer['others']:
        if a['verdict'] not in OTHER_VERDICTS[kinds[a['id']]]:
            raise ValueError(f"{view['group_id']}/{a['id']}: verdict {a['verdict']} does not fit a {kinds[a['id']]}")
    for a in answer['claims'] + answer['others'] + answer['missing']:
        pieces = [norm(x) for x in re.split(r'…+|\.{3,}', a['quote'])]
        if not any(pieces) or any(x not in spoken for x in pieces if x):
            raise ValueError(f"{view['group_id']}: quote is not in the transcript: {a['quote'][:40]}")
    ids = want
    for a in answer['claims']:
        if a['verdict'] in ('evidence_for', 'restates') and (a['target'] not in ids or a['target'] == a['id']):
            raise ValueError(f"{view['group_id']}/{a['id']}: {a['verdict']} must name another Claim of this group")


def disagreements(view, answer):
    out = []
    for a in answer['claims']:
        if a['verdict'] != 'his_claim':
            c = next(c for c in view['claims'] if c['id'] == a['id'])
            out.append({'group_id': view['group_id'], 'item': a['id'], 'kind': 'claim', 'gpt': c['statement'], **a})
    for a in answer['others']:
        o = next(o for o in view['others'] if o['id'] == a['id'])
        gpt = GPT_SAYS.get(o['kind']) or ('answered' if o.get('answered_by') else 'open')
        if a['verdict'] != gpt:
            out.append({'group_id': view['group_id'], 'item': a['id'], 'kind': o['kind'], 'gpt': o['statement'], 'gpt_verdict': gpt, **a})
    out += [{'group_id': view['group_id'], 'item': 'missing', 'kind': 'missing', **m} for m in answer['missing']]
    return out


MODEL = 'gemini-4-argon'
GEMINI_ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'


class Blocked(RuntimeError):
    """The provider's safety filter refused the request: the group was not reviewed."""


class GeminiClient:
    """One request per group, no retry: a refusal or a failure is reported, never re-asked."""

    def __init__(self, model=MODEL, timeout=600):
        import os, ssl
        from dotenv import dotenv_values
        self.model = model; self.timeout = timeout
        self.key = os.environ.get('GEMINI_API_KEY1') or dotenv_values('.env').get('GEMINI_API_KEY1')
        if not self.key:
            raise ValueError('GEMINI_API_KEY1 is not set')
        try:
            import certifi
            self.ssl = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            self.ssl = ssl.create_default_context()
        self.last_raw_response = None

    def generate_json(self, system, user, schema):
        import urllib.request
        body = json.dumps({'systemInstruction': {'parts': [{'text': system}]},
                           'contents': [{'role': 'user', 'parts': [{'text': user}]}],
                           'generationConfig': {'temperature': 0, 'responseMimeType': 'application/json',
                                                'responseJsonSchema': schema['schema']}}, ensure_ascii=False).encode()
        request = urllib.request.Request(GEMINI_ENDPOINT.format(model=self.model) + '?key=' + self.key, data=body,
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=self.timeout, context=self.ssl) as reply:
            raw = json.loads(reply.read().decode())
        self.last_raw_response = raw
        candidates = raw.get('candidates') or []
        if (raw.get('promptFeedback') or {}).get('blockReason') or any(c.get('finishReason') == 'SAFETY' for c in candidates):
            raise Blocked(json.dumps(raw.get('promptFeedback') or [c.get('finishReason') for c in candidates]))
        return json.loads(''.join(part.get('text', '') for part in candidates[0]['content']['parts']))


def review(creation, out_dir, client, *, call=True):
    """Review every group; a saved answer is reused, so a re-run makes no new call."""
    out_dir = Path(out_dir); found = []; calls = 0; blocked = []
    for view in group_views(creation):
        text = prompt(view)
        digest = hashlib.sha256(json.dumps([SYSTEM, text, schema()], ensure_ascii=False).encode()).hexdigest()[:16]
        folder = out_dir / view['group_id'] / digest
        saved = folder / 'response.json'
        if saved.exists():
            answer = json.loads(saved.read_text())
        elif not call:
            raise ValueError(f"{view['group_id']}: no saved review and calls are off")
        else:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'request.json').write_text(json.dumps({'system': SYSTEM, 'user': text, 'schema': schema()}, ensure_ascii=False, indent=1))
            calls += 1
            try:
                answer = client.generate_json(SYSTEM, text, schema())
            except Blocked as refusal:
                # Not reviewed is not a pass: the group is listed for a person to read.
                (folder / 'blocked.json').write_text(json.dumps({'model': getattr(client, 'model', None), 'reason': str(refusal)}, ensure_ascii=False))
                blocked.append(view['group_id']); continue
            saved.write_text(json.dumps(answer, ensure_ascii=False, indent=1))
        check(view, answer)
        found += disagreements(view, answer)
    report = {'creation': str(creation), 'review_dir': str(out_dir), 'model': getattr(client, 'model', None), 'calls': calls,
              'not_reviewed': blocked, 'disagreements': found}
    (out_dir / 'disagreements.json').write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return report


def main():
    p = argparse.ArgumentParser(description='Independent per-item review of a generated argument layer')
    p.add_argument('--creation', required=True, help='a run\'s creation folder')
    p.add_argument('--out', required=True)
    p.add_argument('--model', default=MODEL)
    p.add_argument('--no-calls', action='store_true', help='only replay saved answers')
    a = p.parse_args()
    client = None
    if not a.no_calls:
        client = GeminiClient(a.model)
    print(json.dumps(review(a.creation, a.out, client, call=not a.no_calls), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()


def calibrate(key_path, run_root, review_root):
    """Compare the reviewer's Claim flags with an owner-graded outline key (argument_outline)."""
    from backend.pipeline.argument_outline import ERRORS
    key = json.loads(Path(key_path).read_text())
    hits, missed, false_alarms = [], [], []
    for gid, entry in key['groups'].items():
        report = json.loads((Path(review_root) / entry['folder'] / 'disagreements.json').read_text())
        flagged = {d['item'] for d in report['disagreements'] if d['kind'] == 'claim'}
        for cid, judged in entry['claims'].items():
            wrong = judged['kind'] in ERRORS
            row = (gid, cid, judged['kind'])
            (hits if wrong and cid in flagged else missed if wrong else false_alarms if cid in flagged else [None]).append(row)
    return {'hits': hits, 'missed': missed, 'false_alarms': false_alarms}


# ---------------------------------------------------------------- review-page questions

# Plain words for the 同工: no IDs, no project terms beyond 觀點/根據/題外話.
LABEL = {
    'his_claim': '教授要證明的一個觀點，應單獨列出',
    'evidence_for': '根據：用來支持「{target}」',
    'restates': '重複：和「{target}」是同一個觀點',
    'others_words': '故事或引述裡別人說的話，不是教授的觀點',
    'unstated_lesson': '從故事推出的教訓，教授自己沒有這樣說',
    'digression': '題外話：評論別人，沒有給經文或推理',
    'unsupported': '題外話：只是評論，沒有給經文或推理',
    'supported': '是論證：他給了經文或推理',
    'answered': '他在這一段就回答了',
    'open': '他留到後面才回答',
    'not_a_question': '不是一個問題，是他直接講的話或口頭語',
    'refuted': '別人的看法，教授駁斥了',
    'not_refuted': '別人的看法，教授沒有駁斥',
    'not_others_view': '不是別人的看法，是教授自己的話',
    'add': '要：這是教授要證明的一個觀點，應單獨列出',
    'skip': '不要：已經包含在別的觀點裡，或只是根據',
    'not_own': '不是單獨的觀點：是根據、重複，或別人的話',
}
ASK = {
    'claim': '整理結果把下面這條列為教授的一個觀點。黃色是相關的原話。這條是什麼？',
    'digression': '整理結果把黃色這一段標為題外話。它是什麼？',
    'question': '黃色是教授提的一個問題。他在這一段回答了嗎？',
    'position': '整理結果把黃色這一段當作別人的看法。它是什麼？',
    'missing': '教授在黃色這一段講了下面這個觀點，整理結果裡沒有單獨列出。要不要單獨列出？',
}


def _spans(creation, gid):
    """Sentence IDs for each item of a group, from the saved step answer."""
    request, steps = _saved(Path(creation) / gid, 'steps')
    _, records = _saved(Path(creation) / gid, 'materials')
    kind = {r['step_id']: r['kind'] for r in records['records']}
    span = {}
    for row in steps['claim_text']:
        span.setdefault(row['claim_id'], []).extend(row['sentence_ids'])
    for step in steps['steps']:
        span[step['step_id']] = step['sentence_ids']
        if kind.get(step['step_id']) not in ('question', 'position'):
            for c in step['supports']:
                span.setdefault('support:' + c, []).extend(step['sentence_ids'])
    for n, d in enumerate(steps.get('digressions', []), 1):
        span[f'D{n}'] = d['sentence_ids']
    return request['input']['sentences'], span


def _locate(sentences, paragraphs):
    """Each sentence's transcript paragraph, found in reading order."""
    from backend.pipeline.argument_review import norm as rnorm
    keys = list(paragraphs); k = 0; out = []
    for s in sentences:
        want = rnorm(s['text']); j = k
        while j < len(keys) and want not in rnorm(paragraphs[keys[j]]['text']):
            j += 1
        if j < len(keys):
            k = j
        out.append({**s, 'script_index': keys[k] if j < len(keys) else None})
    return out


def _focus(sentences, ids, quote, limit=14):
    """Sentences to highlight: the item's own, else the sentence holding the reviewer's quote."""
    order = {s['sentence_id']: i for i, s in enumerate(sentences)}
    hit = sorted(order[i] for i in ids if i in order)
    if not hit and quote:
        q = norm(re.split(r'…+|\.{3,}', quote)[0])
        hit = [i for i, s in enumerate(sentences) if q and (q in norm(s['text']) or norm(s['text']) in q)][:1]
    # A Claim's support can be spread over a group: show its first stretch, not the whole group.
    if hit and hit[-1] - hit[0] >= limit:
        run = [hit[0]]
        for i in hit[1:]:
            if i - run[-1] > 2 or len(run) >= limit:
                break
            run.append(i)
        hit = run
    return set(hit)


def page_questions(creation, report, *, lecture, media_url, paragraphs, sample=4, seed=0, context=2):
    """Each disagreement, and a few items the reviewer agreed with, as one review-page question.

    The two answers on offer are GPT's and the reviewer's, in a fixed order that does
    not say which is whose. The ruling grades both the lecture and the reviewer.
    """
    import random
    from backend.pipeline.argument_review import question_id, sentence_times
    result = json.loads((Path(creation) / 'creation-result.json').read_text())
    statement = {k: v['statement'] for k, v in result['claim_candidates'].items()}
    flagged = {(d['group_id'], d['item']) for d in report['disagreements']}
    agreed = []
    for gid in sorted(result['group_claim_ids']):
        folder = sorted(p for p in (Path(report['review_dir']) / gid).iterdir() if p.is_dir())[0]
        answer = json.loads((folder / 'response.json').read_text())
        agreed += [{'group_id': gid, 'item': a['id'], 'kind': 'claim', 'gpt': statement[a['id']], 'verdict': 'his_claim', 'target': '',
                    'quote': a['quote'], 'why': a['why'], 'agreed': True}
                   for a in answer['claims'] if (gid, a['id']) not in flagged]
    picked = random.Random(seed).sample(agreed, min(sample, len(agreed)))
    cache = {}
    out = []
    for d in report['disagreements'] + picked:
        gid = d['group_id']
        if gid not in cache:
            sentences, span = _spans(creation, gid)
            cache[gid] = (_locate(sentences, paragraphs), span)
        sentences, span = cache[gid]
        item = d['item']
        ids = span.get(item) or span.get('support:' + item) or []
        focus = _focus(sentences, ids if d['kind'] != 'missing' else [], d['quote'])
        if not focus:
            raise ValueError(f'{gid}/{item}: nothing to highlight')
        lo, hi = max(0, min(focus) - context), min(len(sentences), max(focus) + context + 1)
        shown = sentences[lo:hi]
        times = [sentence_times(s, paragraphs) for s in shown if s['script_index']]
        target = statement.get(d.get('target') or '', '')
        if d['kind'] == 'claim':
            gpt_key, reviewer_key = 'his_claim', d['verdict']
        elif d['kind'] == 'missing':
            gpt_key, reviewer_key = 'skip', 'add'
        else:
            gpt_key, reviewer_key = d['gpt_verdict'], d['verdict']
        if d.get('agreed'):
            reviewer_key = 'not_own'
        options = [(k, LABEL[k].format(target=target or '另一個觀點')) for k in (gpt_key, reviewer_key)]
        view = d.get('statement') if d['kind'] == 'missing' else d['gpt']
        out.append({'question_id': question_id(lecture, gid, f'review:{item}'), 'lecture': lecture, 'group_id': gid,
                    'item_id': f'review:{item}', 'kind': 'rule',
                    'payload': {'question': ASK[d['kind']], 'options': [{'key': k, 'label': v} for k, v in options],
                                'sentences': [{'text': s['text'], 'focus': lo + i in focus} for i, s in enumerate(shown)],
                                'viewpoints': [view],
                                'audio': {'url': media_url, 'start': min(t[0] for t in times if t[0] is not None),
                                          'end': max(t[1] for t in times if t[1] is not None)},
                                'grading': {'gpt': gpt_key, 'reviewer': d['verdict'] if not d.get('agreed') else 'his_claim',
                                            'sampled_agreement': bool(d.get('agreed')), 'why': d['why']}}})
    return out
