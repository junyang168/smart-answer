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
    'restates': '重述另一條 Claim，或把幾條捆在一起',
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
    '每條都要從原文逐字引一句支持你的答案（quote 必須是原文中連續的一段，可以只取半句）。'
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
              'evidence_for 與 restates 在 target 寫那條 Claim 的 ID；其餘 target 留空。他為這一條本身給了理由（原文字義、另一處經文）的，是 his_claim。', '']
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
        if not norm(a['quote']) or norm(a['quote']) not in spoken:
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
    report = {'creation': str(creation), 'model': getattr(client, 'model', None), 'calls': calls,
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
