"""Part of each lecture's validation: does every sentence GPT wrote say only what he said?

The program already guarantees that each of his sentences lands once, that the
SourceFragments are his exact words and that every verse he quotes stays in the
argument. What it cannot see is whether a Claim, Evidence, Observation, Position
or Question **statement** written by GPT changed his judgement or added one he
did not make (mission: nothing added, nothing removed).

Each record is shown beside his own words (its SourceFragments). Two checkers
answer the same narrow question, as in the eval set:

- GPT judges every record, batched by group: ``faithful`` / ``changed`` / ``added``.
- Jev scores every record (a Noul: how likely the statement changed or added).

GPT decides; Jev never passes or fails a record alone (owner 2026-11-17). A record
GPT flags is a finding; a record Jev scores as likely changed while GPT calls it
faithful needs a look. Both go to the owner. Spoken teaching, not legal text:
wording, degree and order do not count, only whether a disputable judgement changed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

RULE = ('他是口語講課，這不是法律條文：措辭、語氣、詳略、次序不同都不算。'
        '只問一件事：這句話有沒有改變他的判斷（說成相反、說得比他確定或更廣、換了對象），'
        '或加上一個他沒有說的判斷、理由或結論。引述別人的看法時，照實寫成別人的看法也不算改變。')
INSTRUCTION = ('你核對另一個模型從講道逐字稿寫出的記錄。每個工作給出記錄的種類（kind）、它寫的一句話（statement），'
               '和這條記錄所根據的他的原話（his_words）。\n' + RULE + '\n'
               '- faithful：沒有改變也沒有添加。\n- changed：改變了他的判斷。\n- added：加上了他沒有說的判斷、理由或結論。\n'
               'changed 和 added 都要在 detail 寫出是哪幾個字，並引他原話中對應的一句（沒有對應就寫「無」）。')
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['items'], 'properties': {'items': {
    'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['job_id', 'verdict', 'detail'], 'properties': {
            'job_id': {'type': 'string'}, 'verdict': {'type': 'string', 'enum': ['faithful', 'changed', 'added']},
            'detail': {'type': 'string'}}}}}}
JEV_QUESTION = ('`statement` 是另一個模型根據 `his_words` 寫的一句話。' + RULE +
                '這句話改變了他的判斷，或加上了他沒有說的判斷的可能性有多大？')
LIKELY = 0.5   # a Jev score at or above this, against GPT's 'faithful', needs a look
KINDS = (('claims', 'claim_id', 'title', 'Claim'), ('evidence_steps', 'evidence_step_id', 'statement', 'Evidence'),
         ('observations', 'observation_id', 'statement', 'Observation'),
         ('position_nodes', 'position_id', 'title', 'Position'), ('questions', 'question_id', 'text', 'Question'))
MAX_WORDS = 4000  # characters of his words per record; a Claim's whole argument rarely needs more


def records(candidate):
    """Every statement GPT wrote, with his words from its SourceFragments, in source order."""
    text = {f['fragment_id']: f['verbatim_excerpt'] for f in candidate['source_fragments']}
    out = []
    for kind, key, field, label in KINDS:
        for r in candidate[kind]:
            ids = list(r.get('source_fragment_ids') or [])
            for occ in r.get('occurrences', []):
                ids += [a['source_fragment_id'] for a in occ.get('anchors', []) if a.get('source_fragment_id')]
            ids = list(dict.fromkeys(i for i in ids if i in text))
            if not ids:
                raise ValueError(f'{r[key]}: no SourceFragment to check it against')
            words = '\n'.join(text[i] for i in ids)
            out.append({'job_id': r[key], 'kind': label, 'statement': r[field],
                        'his_words': words if len(words) <= MAX_WORDS else words[:MAX_WORDS] + '……',
                        'group': r[key].rsplit('-', 2)[-2] if '-P' in r[key] else 'claims'})
    return out


def check(candidate, root, ports, *, jev=True):
    from backend.pipeline import claim_flash_common as f
    rows = records(candidate)
    decided = {}
    for batch, reply in f.gpt(Path(root) / 'gpt', ports, 'statement_fidelity_check',
                              [{k: r[k] for k in ('job_id', 'kind', 'statement', 'his_words')} for r in rows],
                              SCHEMA, INSTRUCTION, semantic_review=False):
        asked = {j['job_id'] for j in batch}
        decided.update({i['job_id']: i for i in reply['items'] if i['job_id'] in asked})
        if not asked <= set(decided):
            raise ValueError('fidelity check left a record unanswered')
    scores = {}
    if jev:
        for r in rows:
            scores[r['job_id']] = f.jev(Path(root) / 'jev', ports, {'statement': r['statement'], 'his_words': r['his_words']},
                                        {'Q0': {'type': 'noul', 'instructions': JEV_QUESTION}})['Q0']
    return report(rows, decided, scores)


def report(rows, decided, scores):
    findings, looks = [], []
    for r in rows:
        g = decided[r['job_id']]
        row = {'id': r['job_id'], 'kind': r['kind'], 'statement': r['statement'], 'verdict': g['verdict'],
               'detail': g['detail'], 'jev': scores.get(r['job_id'])}
        if g['verdict'] != 'faithful':
            findings.append(row)
        elif row['jev'] is not None and row['jev'] >= LIKELY:
            looks.append(row)
    return {'records': len(rows), 'findings': findings, 'needs_look': looks,
            'by_kind': {k: sum(r['kind'] == k for r in rows) for _, _, _, k in KINDS}}


def usage(root):
    """Token totals from every call's saved receipt: GPT (codex --json) and Jev."""
    import re
    totals = {'gpt_calls': 0, 'gpt_missing': 0, 'gpt_input': 0, 'gpt_cached_input': 0, 'gpt_output': 0, 'gpt_reasoning': 0,
              'jev_calls': 0, 'jev_input': 0, 'jev_output': 0}
    for f in Path(root).rglob('provider.json'):
        u = json.loads(f.read_text()).get('usage')
        totals['gpt_calls'] += 1
        if not u:
            totals['gpt_missing'] += 1
            continue
        totals['gpt_input'] += u.get('input_tokens', 0)
        totals['gpt_cached_input'] += u.get('cached_input_tokens', 0)
        totals['gpt_output'] += u.get('output_tokens', 0)
        totals['gpt_reasoning'] += u.get('reasoning_output_tokens', 0)
    for f in Path(root).rglob('jev/*/response.json'):
        s = f.read_text()
        totals['jev_calls'] += 1
        totals['jev_input'] += sum(map(int, re.findall(r'"input_tokens": (\d+)', s)))
        totals['jev_output'] += sum(map(int, re.findall(r'"output_tokens": (\d+)', s)))
    return totals


def main():
    from dotenv import load_dotenv
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    from backend.pipeline.evidence_refresh import Models
    p = argparse.ArgumentParser(description='Check every statement GPT wrote against his words')
    p.add_argument('--candidate', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--count', action='store_true', help='only count the records and the calls; no model call')
    p.add_argument('--no-jev', action='store_true')
    a = p.parse_args()
    load_dotenv('.env')
    candidate = json.loads(Path(a.candidate).read_text())
    if a.count:
        rows = records(candidate)
        print(json.dumps({'records': len(rows), 'his_words_chars': sum(len(r['his_words']) for r in rows),
                          'jev_calls': 0 if a.no_jev else len(rows)}, ensure_ascii=False))
        return
    client = CodexSubscriptionClient(model='gpt-6.1-sol', reasoning_effort='medium', max_output_tokens=16000)
    client.capture_usage_events = True  # codex --json reports token usage
    ports = Models(client, Path(a.out))
    result = check(candidate, a.out, ports, jev=not a.no_jev)
    result['usage'] = usage(a.out)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / 'fidelity.json').write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
