"""Story check: is each Claim his own teaching, or something a character in a story said?

Card #787. GPT wrote the pastor's remark inside the engineer story as a Claim in three runs of lecture 2
(v2 CL0022, v4 CL0019, v6 CL0018) although the prompt said a story character's words are not his teaching.
The prompt rule is written; this is the net under it, in the shape of the fidelity check (owner 2026-11-17:
「jev is used for prefilter and you are the final judge」, 2026-11-20: 「出問題的轉人工」):

  prefilter  Jev, one Noul per Claim over the Claim, its Evidence and his words; risk = 1 - P(his own teaching)
  judge      Claude reads each Claim at or above the risk against his words; his own -> ruled, otherwise a finding
  owner      a finding has no ruling, so ``argument_instance_load`` refuses the lecture until the owner rules

Calibration 2026-11-21 (``argument_golden/shape/story_calibration.json``, 3 ruled story Claims, 22 ruled-correct
Claims of lecture 2): Jev risk 0.59-0.80 on the errors; 20 of 22 correct Claims at or below 0.21, the other two
0.39 and 0.42. Only the story question is checked: the same test showed Jev unusable for "only a premise" (errors
scored 0.17-0.36, below many correct Claims) and "two points in one Claim" (10 of 22 correct Claims at 0.5).
"""
import argparse
import hashlib
import json
from pathlib import Path

from backend.pipeline import argument_fidelity as af
from backend.pipeline.argument_review_binding import bind_report, require_binding, require_unchanged

SHAPE_RISK = 0.3
JUDGE_BATCH = 6
QUESTION = {'type': 'noul',
            'instructions': 'statement 是一條 Claim。照 his_words 一步步讀：這個判斷是他自己親口作成的教導嗎？'
                            '（故事裡別人說的話，或只是從故事推出、他沒有親口作成教導的，不算。）他是口語講課，措辭不同不算。',
            'criteria': {'true': '這個判斷是他自己親口說出並作成的教導',
                         'false': '這個判斷是故事裡別人說的話，或只是從故事推出、他沒有親口作成教導'}}
INSTRUCTION = (
    '你審查另一個模型從講道逐字稿寫出的 Claim。每個工作給出一條 Claim 的 statement、支持它的 Evidence 語句（evidence）'
    '和它所根據的他的原話（his_words）。他是口語講課，不是法律條文；措辭、詳略不算。\n'
    '問：這個判斷是誰的？\n'
    '- his_own：他親口說出，作成自己的教導。他對故事中人的評語，若是他自己的判斷，也算他的。\n'
    '- others_or_story：是故事裡別人（例如牧師、弟兄）說的話，或只是從故事推出的教訓、他沒有親口說出。\n'
    '只依 his_words 判斷，不憑你對聖經或這位教授的了解。detail 引他原話中最關鍵的一句；找不到對應就寫「無」。')
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['items'], 'properties': {'items': {
    'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['job_id', 'source', 'detail'], 'properties': {
            'job_id': {'type': 'string'}, 'source': {'type': 'string', 'enum': ['his_own', 'others_or_story']},
            'detail': {'type': 'string'}}}}}}


def rows(candidate):
    """Every Claim with its Evidence statements and his words."""
    evidence = {e['evidence_step_id']: e['statement'] for e in candidate['evidence_steps']}
    claims = {c['claim_id']: c for c in candidate['claims']}
    words = {r['job_id']: r['his_words'] for r in af.records(candidate) if r['kind'] == 'Claim'}
    return [{'job_id': cid, 'statement': c['title'],
             'evidence': [evidence[e] for e in c.get('evidence_step_ids', []) if e in evidence],
             'his_words': words[cid]} for cid, c in claims.items()]


def prefilter(items, root, ports):
    """Jev's risk per Claim: 1 - P(this is his own teaching)."""
    from backend.pipeline import claim_flash_common as f
    out = {}
    for r in items:
        state = {'statement': r['statement'], 'evidence': r['evidence'], 'his_words': r['his_words']}
        out[r['job_id']] = round(1 - f.jev(Path(root) / 'jev', ports, state, {'Q0': QUESTION})['Q0'], 3)
    return out


def prefilter_report(items, risk):
    looks = [{'id': r['job_id'], 'statement': r['statement'], 'jev_risk': risk[r['job_id']]}
             for r in items if risk[r['job_id']] >= SHAPE_RISK]
    return {'claims': len(items), 'findings': [], 'needs_look': sorted(looks, key=lambda x: -x['jev_risk']),
            'threshold': SHAPE_RISK, 'judge': 'Claude reads each look against his words; the owner rules on what Claude finds'}


def judge(items, root, client):
    """Claude's answer for each Claim Jev flagged; request and raw reply kept under ``root/claude/<sha>``."""
    decided = {}
    for start in range(0, len(items), JUDGE_BATCH):
        jobs = [{k: r[k] for k in ('job_id', 'statement', 'evidence', 'his_words')} for r in items[start:start + JUDGE_BATCH]]
        user = json.dumps({'jobs': jobs}, ensure_ascii=False, sort_keys=True)
        directory = Path(root) / 'claude' / hashlib.sha256((INSTRUCTION + user + client.model).encode()).hexdigest()
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


def judged_report(pre, items, decided, *, model=af.JUDGE_MODEL, day=None):
    """His own -> ruled by the program; others_or_story -> a finding with no ruling, for the owner."""
    from datetime import date
    day = day or date.today().isoformat()
    by_id = {r['job_id']: r for r in items}
    looks, findings = [], []
    for look in pre['needs_look']:
        g = decided[look['id']]
        if g['source'] == 'his_own':
            looks.append(look | {'ruling': f"Claude {day} ({model}, program; final judge per owner): his own teaching. {g['detail']}".rstrip()})
        else:
            findings.append(look | {'verdict': g['source'], 'detail': g['detail'], 'his_words': by_id[look['id']]['his_words']})
    return pre | {'findings': findings, 'needs_look': looks,
                  'judged_by': f'{model} through the claude CLI (subscription), program `argument_shape --judge`'}


def calibrate(root, ports, runs_dir):
    """Jev's risk on the ruled Claims of ``argument_golden/shape/story_calibration.json``; run it again after any change
    to QUESTION or SHAPE_RISK. Reports the errors caught and the ruled-correct Claims sent to Claude at SHAPE_RISK."""
    cases = json.loads((Path(__file__).parent / 'argument_golden' / 'shape' / 'story_calibration.json').read_text())['cases']
    loaded, risk = {}, {}
    for c in cases:
        if c['run'] not in loaded:
            loaded[c['run']] = {r['job_id'].rsplit('-', 1)[1]: r for r in rows(json.loads((Path(runs_dir) / c['run']).read_text()))}
        r = loaded[c['run']][c['claim']]
        risk[(c['run'], c['claim'])] = prefilter([r | {'job_id': f"{c['run']}:{c['claim']}"}], root, ports)[f"{c['run']}:{c['claim']}"]
    story = [risk[(c['run'], c['claim'])] for c in cases if c['label'] == 'story']
    mine = [risk[(c['run'], c['claim'])] for c in cases if c['label'] == 'his_own']
    return {'threshold': SHAPE_RISK, 'story_caught': f"{sum(x >= SHAPE_RISK for x in story)}/{len(story)}",
            'his_own_sent_to_claude': f"{sum(x >= SHAPE_RISK for x in mine)}/{len(mine)}", 'story_risks': sorted(story),
            'his_own_top_risks': sorted(mine)[-4:]}


def main():
    from dotenv import load_dotenv
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    from backend.pipeline.evidence_refresh import Models
    p = argparse.ArgumentParser(description='Story check: is each Claim his own teaching (Jev prefilter, Claude judge)')
    p.add_argument('--candidate')
    p.add_argument('--out', required=True, help='the lecture run\'s shape/ directory')
    p.add_argument('--calibrate', metavar='RUNS_DIR', help='Jev on the ruled calibration Claims; no Claude call')
    p.add_argument('--prefilter', action='store_true')
    p.add_argument('--judge', action='store_true')
    a = p.parse_args()
    load_dotenv('.env')
    out = Path(a.out)
    if a.calibrate:
        client = CodexSubscriptionClient(model='gpt-6.1-sol', reasoning_effort='medium', max_output_tokens=16000)
        print(json.dumps(calibrate(out, Models(client, out), a.calibrate), ensure_ascii=False, indent=1))
        return
    if not a.candidate:
        raise SystemExit('--candidate is required')
    candidate_path = Path(a.candidate)
    candidate_raw = candidate_path.read_bytes()
    items = rows(json.loads(candidate_raw))
    if a.prefilter == a.judge:
        raise SystemExit('give exactly one of --prefilter and --judge')
    if a.prefilter:
        client = CodexSubscriptionClient(model='gpt-6.1-sol', reasoning_effort='medium', max_output_tokens=16000)
        report = prefilter_report(items, prefilter(items, out, Models(client, out)))
        require_unchanged(candidate_path, candidate_raw)
        report = bind_report(report, candidate_raw, items)
        out.mkdir(parents=True, exist_ok=True)
        (out / 'prefilter.json').write_text(json.dumps(report, ensure_ascii=False, indent=1))
        print(json.dumps({'claims': report['claims'], 'to_judge': len(report['needs_look'])}, ensure_ascii=False))
        return
    final = out / 'shape-final.json'
    if final.exists():
        raise SystemExit(f'{final} exists; it may hold the owner\'s rulings, not overwritten')
    from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
    pre = json.loads((out / 'prefilter.json').read_text())
    require_binding(pre, candidate_raw, items, label='story')
    asked = {l['id'] for l in pre['needs_look']}
    client = ClaudeSubscriptionClient(model=af.JUDGE_MODEL, reasoning_effort='high')
    todo = [r for r in items if r['job_id'] in asked]
    report = judged_report(pre, items, judge(todo, out, client) if todo else {})
    require_unchanged(candidate_path, candidate_raw)
    final.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({'looks': len(asked), 'his_own': len(report['needs_look']), 'to_owner': len(report['findings'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
