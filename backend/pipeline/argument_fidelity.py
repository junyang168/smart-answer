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
               '這條記錄所根據的他的原話（his_words），和同一段論證裡他前後的話（context）。'
               '判斷以 his_words 為主；statement 裡的話若在 context 裡他說過，不算添加。\n' + RULE + '\n'
               'kind 為 Position 時，statement 寫的是別人的看法，本來就不是他的判斷，他反駁它也不寫進 statement：'
               '只問 statement 是否照實寫出了別人的看法，沒有說得比別人更多或不同。\n'
               '- faithful：沒有改變也沒有添加。\n- changed：改變了他的判斷。\n- added：加上了他沒有說的判斷、理由或結論。\n'
               'changed 和 added 都要在 detail 寫出是哪幾個字，並引他原話中對應的一句（沒有對應就寫「無」）。')
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['items'], 'properties': {'items': {
    'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['job_id', 'verdict', 'detail'], 'properties': {
            'job_id': {'type': 'string'}, 'verdict': {'type': 'string', 'enum': ['faithful', 'changed', 'added']},
            'detail': {'type': 'string'}}}}}}
JEV_QUESTION = ('`statement` 是另一個模型根據 `his_words` 寫的一句話，`kind` 是記錄種類，`context` 是同一段論證裡他前後的話；'
                'statement 裡的話若在 context 裡他說過，不算添加。kind 為 Position 時 statement 寫的是別人的看法，'
                '只問是否照實寫出別人的看法。' + RULE +
                '這句話改變了他的判斷，或加上了他沒有說的判斷的可能性有多大？')
LIKELY = 0.5   # a Jev score at or above this, against GPT's 'faithful', needs a look

# Prefilter (owner 2026-11-17: 「jev is used for prefilter and you are the final judge」), in the shape of
# the Jev cookbook's "Verifying LLM answers against their source": one positive step-by-step Noul with
# explicit true/false criteria; risk = 1 - supported; the threshold is measured on planted errors.
SUPPORTED = {
    'own': {'instructions': '照 his_words（及 context）一步步讀他的話，會得出 statement 所說的判斷嗎？'
                            '他是口語講課，措辭、詳略、次序不同都不算。',
            'criteria': {'true': 'statement 裡每個判斷（結論、理由、適用範圍、確定程度）他都說了，沒有說反',
                         'false': 'statement 至少有一個判斷他沒有說，或比他說的更廣、更確定，或與他說的相反'}},
    'others': {'instructions': 'statement 寫的是他轉述、要反駁的別人看法。照 his_words（及 context）一步步讀，'
                               '他轉述的別人看法就是 statement 寫的嗎？他是口語講課，措辭不同不算。',
               'criteria': {'true': '他轉述的別人看法就是 statement 寫的，沒有多也沒有不同',
                            'false': 'statement 寫的看法他沒有轉述，或與他轉述的不同、更多'}},
    'question': {'instructions': 'statement 是一個在講道中被提出的問題（他自己問，或會眾問）。照 his_words（及 context）讀，'
                                 '這個問題是這樣被問的嗎？他是口語講課，措辭不同不算。',
                 'criteria': {'true': '問題問的就是 statement 寫的，沒有多加前提，也沒有問別的',
                              'false': 'statement 的問題沒有被這樣問，或加了前提、換了對象、問的是另一件事'}},
}
FORM = {'Position': 'others', 'Question': 'question'}


def prefilter_question(kind):
    return {'Q0': {'type': 'noul', **SUPPORTED[FORM.get(kind, 'own')]}}


PREFILTER_RISK = 0.3  # 2026-11-17 calibration: 18/18 planted errors at or above it, 36/142 faithful records


def prefilter_report(rows, risk):
    """Records at or above the measured risk go to Claude to judge against his words; Jev decides nothing."""
    looks = [{'id': r['job_id'], 'kind': r['kind'], 'statement': r['statement'], 'jev_risk': risk[r['job_id']]}
             for r in rows if risk[r['job_id']] >= PREFILTER_RISK]
    return {'records': len(rows), 'findings': [], 'needs_look': sorted(looks, key=lambda x: -x['jev_risk']),
            'threshold': PREFILTER_RISK, 'by_kind': {k: sum(r['kind'] == k for r in rows) for _, _, _, k in KINDS},
            'judge': 'Claude reads each look against his words; owner rules on what Claude finds changed'}


JUDGE_MODEL = 'claude-opus-5-5'
JUDGE_BATCH = 8  # records per call; his words run to 8,000 characters each


def judge(rows, root, client):
    """Claude's verdict on each record Jev flagged, against his words (owner 2026-11-17: Claude is the final judge).

    Same question as the GPT check. Each batch's request and raw answer are kept under
    ``root/claude/<request sha>``, and a successful call is replayed rather than repeated.
    """
    import hashlib
    decided = {}
    for start in range(0, len(rows), JUDGE_BATCH):
        jobs = [{k: r[k] for k in ('job_id', 'kind', 'statement', 'his_words', 'context')}
                for r in rows[start:start + JUDGE_BATCH]]
        user = json.dumps({'jobs': jobs}, ensure_ascii=False, sort_keys=True)
        key = hashlib.sha256((INSTRUCTION + user + client.model).encode()).hexdigest()
        directory = Path(root) / 'claude' / key
        directory.mkdir(parents=True, exist_ok=True)
        client.call_artifacts = directory / 'transport'
        reply = client.generate_json(INSTRUCTION, user, SCHEMA)
        asked = {j['job_id'] for j in jobs}
        answered = {i['job_id']: i for i in reply['items'] if i['job_id'] in asked}
        if set(answered) != asked:
            raise ValueError(f'Claude left {sorted(asked - set(answered))} unanswered')
        (directory / 'reply.json').write_text(json.dumps(reply, ensure_ascii=False, indent=1))
        decided.update(answered)
    return decided


def judged_report(pre, rows, decided, *, model=JUDGE_MODEL, day=None):
    """The prefilter report with Claude's verdicts: faithful looks are ruled; changed or added go to the owner.

    A finding has no ``ruling``, so ``argument_instance_load`` refuses the lecture until the owner rules on it
    (owner 2026-11-20: 「出問題的轉人工」).
    """
    from datetime import date
    day = day or date.today().isoformat()
    by_id = {r['job_id']: r for r in rows}
    looks, findings = [], []
    for look in pre['needs_look']:
        g = decided[look['id']]
        if g['verdict'] == 'faithful':
            looks.append(look | {'ruling': f"Claude {day} ({model}, program; final judge per owner): faithful. {g['detail']}".rstrip()})
        else:
            findings.append(look | {'verdict': g['verdict'], 'detail': g['detail'], 'his_words': by_id[look['id']]['his_words']})
    return pre | {'findings': findings, 'needs_look': looks,
                  'judged_by': f'{model} through the claude CLI (subscription), program `argument_fidelity --judge`; '
                               'owner 2026-11-17 「jev is used for prefilter and you are the final judge」',
                  'judge': 'faithful looks are ruled by the program; changed or added findings wait for the owner'}


def prefilter(rows, root, ports):
    """Jev's risk for each record: 1 - P(his words lead to the statement)."""
    from backend.pipeline import claim_flash_common as f
    out = {}
    for r in rows:
        state = {'statement': r['statement'], 'his_words': r['his_words'], 'context': r['context']}
        out[r['job_id']] = round(1 - f.jev(Path(root) / 'jev', ports, state, prefilter_question(r['kind']))['Q0'], 3)
    return out
KINDS = (('claims', 'claim_id', 'title', 'Claim'), ('evidence_steps', 'evidence_step_id', 'statement', 'Evidence'),
         ('observations', 'observation_id', 'statement', 'Observation'),
         ('position_nodes', 'position_id', 'title', 'Position'), ('questions', 'question_id', 'text', 'Question'))
GROUP = __import__('re').compile(r'-P\d+-')
MAX_WORDS = 4000  # characters of his words per record; a Claim's whole argument rarely needs more


UNIT_KIND_MAP = {
    'claim': 'Claim',
    'evidence': 'Evidence',
    'observation': 'Observation',
    'example': 'Evidence',
    'position': 'Position',
    'question': 'Question',
}


def _unit_argument_records(candidate):
    """Adapt #845 unit_argument_candidate_v1 nodes into fidelity-check rows.

    Matches single-lecture ``records()`` semantics: a Claim's ``his_words`` puts its
    own conclusion anchor first, followed by sentences from its direct incoming
    supporting/answering/contextualizing edges and child nodes (so cross-paragraph
    synthesis isn't falsely flagged as 'added'), and ``context`` covers a wider ±3
    paragraph window around its anchor.
    """
    sents = candidate['sentences']
    nodes = candidate['nodes']
    edges = candidate.get('edges') or []
    by_para, para_order = {}, []
    for s in sents:
        k = (s['source_id'], s['paragraph'])
        if k not in by_para:
            by_para[k] = ''
            para_order.append(k)
        by_para[k] += s['text']
    para_idx = {k: i for i, k in enumerate(para_order)}

    # Collect direct incoming supporting/contextualizing sentence positions per node
    incoming_pos: dict[str, list[int]] = {}
    for e in edges:
        dst = e.get('to_id')
        src = e.get('from_id')
        if not dst or dst not in nodes:
            continue
        bucket = incoming_pos.setdefault(dst, [])
        bucket.extend(e.get('at') or [])
        if src and src in nodes:
            bucket.extend(nodes[src].get('at') or [])

    out = []
    for nid, node in nodes.items():
        at_positions = sorted(node['at'])
        if not at_positions:
            raise ValueError(f'{nid}: no sentence positions to check it against')
        own_set = set(at_positions)
        behind = sorted({p for p in incoming_pos.get(nid, []) if p not in own_set}) if node['kind'] == 'claim' else []
        ordered_pos = at_positions + behind
        words = '\n'.join(sents[i - 1]['text'] for i in ordered_pos)
        touched = sorted({para_idx[(sents[i - 1]['source_id'], sents[i - 1]['paragraph'])] for i in at_positions})
        ctx_indices = {j for pi in touched for j in range(max(0, pi - 3), min(len(para_order), pi + 4))}
        near = '\n'.join(by_para[para_order[j]] for j in sorted(ctx_indices))
        out.append({'job_id': nid, 'kind': UNIT_KIND_MAP.get(node['kind'], node['kind']),
                    'statement': node['statement'],
                    'his_words': words if len(words) <= MAX_WORDS else words[:MAX_WORDS] + '……',
                    'context': near if len(near) <= MAX_WORDS else near[:MAX_WORDS] + '……',
                    'group': 'unit'})
    return out


def records(candidate):
    """Every statement GPT wrote, with his words from its SourceFragments, in source order."""
    if candidate.get('version') == 'unit_argument_candidate_v1' and 'nodes' in candidate and 'sentences' in candidate:
        return _unit_argument_records(candidate)
    text = {f['fragment_id']: f['verbatim_excerpt'] for f in candidate['source_fragments']}
    out = []
    for kind, key, field, label in KINDS:
        for r in candidate[kind]:
            # A Claim's own words first: past MAX_WORDS a long story would otherwise push out the
            # sentence where he states it (lecture 3 CL0007: 「理性的根基……做我們信仰根基非常的重要」).
            own, behind = [], []
            for occ in r.get('occurrences', []):
                for a in occ.get('anchors', []):
                    if a.get('source_fragment_id'):
                        (behind if a.get('evidence_id') or a.get('observation_id') else own).append(a['source_fragment_id'])
            ids = own + list(r.get('source_fragment_ids') or []) + behind
            ids = list(dict.fromkeys(i for i in ids if i in text))
            if not ids:
                raise ValueError(f'{r[key]}: no SourceFragment to check it against')
            words = '\n'.join(text[i] for i in ids)
            # The rest of the same argument group: a Claim or a step may state what he said a few sentences away.
            groups = {m.group(0) for i in ids for m in [GROUP.search(i)] if m}
            near = '\n'.join(t for fid, t in text.items() if fid not in ids and any(g in fid for g in groups))
            out.append({'job_id': r[key], 'kind': label, 'statement': r[field],
                        'his_words': words if len(words) <= MAX_WORDS else words[:MAX_WORDS] + '……',
                        'context': near if len(near) <= MAX_WORDS else near[:MAX_WORDS] + '……',
                        'group': r[key].rsplit('-', 2)[-2] if '-P' in r[key] else 'claims'})
    return out


def check(candidate, root, ports, *, jev=True, only=None):
    from backend.pipeline import claim_flash_common as f
    rows = [r for r in records(candidate) if only is None or r['job_id'] in only]
    decided = {}
    for batch, reply in f.gpt(Path(root) / 'gpt', ports, 'statement_fidelity_check',
                              [{k: r[k] for k in ('job_id', 'kind', 'statement', 'his_words', 'context')} for r in rows],
                              SCHEMA, INSTRUCTION, semantic_review=False):
        asked = {j['job_id'] for j in batch}
        decided.update({i['job_id']: i for i in reply['items'] if i['job_id'] in asked})
        if not asked <= set(decided):
            raise ValueError('fidelity check left a record unanswered')
    scores = {}
    if jev:
        for r in rows:
            scores[r['job_id']] = f.jev(Path(root) / 'jev', ports, {'kind': r['kind'], 'statement': r['statement'],
                                                                     'his_words': r['his_words'], 'context': r['context']},
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
    p.add_argument('--prefilter', action='store_true', help='Jev prefilter only (owner 2026-11-17); Claude judges the looks')
    p.add_argument('--recheck', help='an earlier fidelity.json: re-ask only its findings and looks; the rest passed a stricter check')
    p.add_argument('--judge', action='store_true',
                   help="after --prefilter: Claude judges its looks; writes fidelity-final.json, findings left for the owner")
    a = p.parse_args()
    load_dotenv('.env')
    candidate = json.loads(Path(a.candidate).read_text())
    if a.judge:
        from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
        out = Path(a.out)
        final = out / 'fidelity-final.json'
        if final.exists():
            raise SystemExit(f'{final} exists; it may hold owner rulings')
        pre = json.loads((out / 'prefilter.json').read_text())
        wanted = {look['id'] for look in pre['needs_look']}
        rows = [r for r in records(candidate) if r['job_id'] in wanted]
        if {r['job_id'] for r in rows} != wanted:
            raise SystemExit('prefilter.json names records this candidate does not have')
        client = ClaudeSubscriptionClient(model=JUDGE_MODEL, reasoning_effort='high')
        result = judged_report(pre, rows, judge(rows, out, client))
        final.write_text(json.dumps(result, ensure_ascii=False, indent=1))
        print(json.dumps({'looks': len(wanted), 'faithful': len(result['needs_look']),
                          'to_owner': [f['id'] for f in result['findings']]}, ensure_ascii=False))
        return
    if a.count:
        rows = records(candidate)
        print(json.dumps({'records': len(rows), 'his_words_chars': sum(len(r['his_words']) for r in rows),
                          'jev_calls': 0 if a.no_jev else len(rows)}, ensure_ascii=False))
        return
    client = CodexSubscriptionClient(model='gpt-6.1-sol', reasoning_effort='medium', max_output_tokens=16000)
    client.capture_usage_events = True  # codex --json reports token usage
    ports = Models(client, Path(a.out))
    if a.prefilter:
        rows = records(candidate)
        result = prefilter_report(rows, prefilter(rows, a.out, ports))
        result['usage'] = usage(a.out)
        Path(a.out).mkdir(parents=True, exist_ok=True)
        (Path(a.out) / 'prefilter.json').write_text(json.dumps(result, ensure_ascii=False, indent=1))
        print(json.dumps({k: result[k] for k in ('records', 'threshold')} | {'needs_look': len(result['needs_look'])}))
        return
    only = None
    if a.recheck:
        earlier = json.loads(Path(a.recheck).read_text())
        only = {f['id'] for f in earlier['findings'] + earlier['needs_look']}
    result = check(candidate, a.out, ports, jev=not a.no_jev, only=only)
    result['usage'] = usage(a.out)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / ('fidelity-recheck.json' if a.recheck else 'fidelity.json')).write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
