"""同工 review of golden-case answers: questions, answers, follow-ups, rulings.

Design: ``docs/wang-knowledge-platform/10-extraction/argument_layer_rebuild_design.md``
§0.3.1. Records live in the new knowledge database (``ARGUMENT_DATABASE_URL``,
schema ``argument_review``), never in the current one. Final answers reach the
grader only through ``export``, which rewrites ``cases.json`` for a PR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Optional

GOLDEN = Path(__file__).parent / 'argument_golden'
MIGRATION = Path(__file__).parent / 'argument_review.sql'
DEFAULT_URL = 'postgresql:///smart_answer_knowledge_v2'
QUORUM = 3          # answers needed before a majority can decide
MAX_FOLLOWUPS = 2   # questions asked of one reviewer about one answer

# One plain question per kind of answer. Options never show IDs or terms.
QUESTIONS = {
    'step': ('教授這一段（黃色標示的）是在講一個理由嗎？', [
        ('one', '是，這是一個理由'),
        ('several', '不是，裡面有兩個以上的理由'),
        ('conclusion', '這是在下結論，不是在講理由'),
        ('aside', '這是題外話，不是在講經文')]),
    'claim_text': ('這幾句（黃色標示的）是教授在說結論嗎？', [
        ('conclusion', '是，在說結論'),
        ('reason', '不是，是在講理由、經文或例子'),
        ('filler', '只是過場話')]),
    'other': ('這幾句（黃色標示的）只是過場話嗎？', [
        ('filler', '是，只是過場話，可以略過'),
        ('content', '不是，裡面有內容')]),
    'aside': ('這一段（黃色標示的）是在講經文，還是題外話？', [
        ('aside', '題外話，聽眾可以略過'),
        ('exegesis', '在講經文')]),
}
# The answer each draft already holds, so a majority can be read as agree/disagree.
DRAFT_CHOICE = {'step': 'one', 'claim_text': 'conclusion', 'other': 'filler', 'aside': 'aside'}
# A kind change the program can apply to the case without a human edit.
KIND_FOR_CHOICE = {'conclusion': 'claim_text', 'aside': 'aside', 'filler': 'other', 'reason': 'step', 'content': 'step',
                   'one': 'step', 'exegesis': 'step'}


def database_url():
    url = os.getenv('ARGUMENT_DATABASE_URL', DEFAULT_URL)
    current = os.getenv('KNOWLEDGE_DATABASE_URL')
    if current and url.rstrip('/').rsplit('/', 1)[-1] == current.rstrip('/').rsplit('/', 1)[-1]:
        raise RuntimeError('ARGUMENT_DATABASE_URL points at the current knowledge database; refuse to write review records there')
    return url


def connect(url=None):
    import psycopg
    return psycopg.connect(url or database_url())


def migrate(url=None):
    with connect(url) as conn:
        conn.execute(MIGRATION.read_text())


# ---------------------------------------------------------------- questions

def norm(text):
    return re.sub(r'\s', '', text or '')


def transcript(lecture, data_dir=None):
    base = Path(data_dir or os.environ['DATA_BASE_DIR'])
    script = json.loads((base / 'script_published' / f'{lecture}.json').read_text())['script']
    return {str(p['index']): p for p in script if p.get('type') != 'subtitle' and p.get('start_time') is not None}


def sentence_times(sentence, paragraphs):
    """Seconds where a sentence starts and ends, by its place in the timed paragraph."""
    p = paragraphs.get(str(sentence['script_index']))
    if not p:
        return None, None
    text = p['text']
    at = text.find(sentence['text'])
    if at < 0:
        at = norm(text).find(norm(sentence['text']))
        text = norm(text)
    if at < 0:
        return float(p['start_time']), float(p['end_time'])
    start, end, length = float(p['start_time']), float(p['end_time']), max(len(text), 1)
    return start + (end - start) * at / length, start + (end - start) * (at + len(sentence['text'])) / length


def ordered_sentences(group, paragraphs):
    out = []
    order = {k: i for i, k in enumerate(paragraphs)}
    for it in group['items']:
        for s in it['sentences']:
            p = paragraphs.get(str(s['script_index']), {'text': ''})
            out.append((order.get(str(s['script_index']), 10**6), p['text'].find(s['text']), s, it['id']))
    return [(s, item) for _, _, s, item in sorted(out, key=lambda x: (x[0], x[1]))]


def question_id(lecture, group_id, item_id):
    return hashlib.sha256(f'{lecture}\0{group_id}\0{item_id}'.encode()).hexdigest()[:16]


def build_questions(cases, *, media_url, paragraphs, context=2, statuses=('draft',), disagreements=None):
    """One question per answer still to settle; drafts first, then disagreements with a run."""
    lecture = cases['lecture']
    out = []
    for group in cases['groups']:
        seq = ordered_sentences(group, paragraphs)
        for item in group['items']:
            # An owner ruling is never put back to reviewers, even where a run disagrees with it.
            disputed = disagreements and (group['group'], item['id']) in disagreements and item['status'] not in ('owner', 'final')
            if item['status'] not in statuses and not disputed:
                continue
            if item.get('optional') and not disputed:
                continue
            ask, options = QUESTIONS[item['kind']]
            mine = [i for i, (s, owner) in enumerate(seq) if owner == item['id']]
            lo, hi = max(0, mine[0] - context), min(len(seq), mine[-1] + context + 1)
            shown = [{'text': s['text'], 'focus': owner == item['id']} for s, owner in seq[lo:hi]]
            times = [sentence_times(s, paragraphs) for s, _ in seq[lo:hi]]
            starts = [t[0] for t in times if t[0] is not None]
            ends = [t[1] for t in times if t[1] is not None]
            out.append({
                'question_id': question_id(lecture, group['group'], item['id']),
                'lecture': lecture, 'group_id': group['group'], 'item_id': item['id'], 'kind': item['kind'],
                'payload': {'question': ask, 'options': [{'key': k, 'label': v} for k, v in options],
                            'sentences': shown, 'audio': {'url': media_url, 'start': min(starts) if starts else None,
                                                          'end': max(ends) if ends else None},
                            'draft_choice': DRAFT_CHOICE[item['kind']],
                            'disagreement': (disagreements or {}).get((group['group'], item['id']))},
            })
    return out


def publish_questions(conn, questions, cases_sha256):
    """Insert new questions; an existing question keeps its answers and history."""
    added = 0
    for q in questions:
        cur = conn.execute('''INSERT INTO argument_review.questions
            (question_id, lecture, group_id, item_id, kind, payload, cases_sha256)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (question_id) DO NOTHING''',
            (q['question_id'], q['lecture'], q['group_id'], q['item_id'], q['kind'], json.dumps(q['payload'], ensure_ascii=False), cases_sha256))
        added += cur.rowcount
    return added


# ---------------------------------------------------------------- answers

def active_answers(conn, question_id):
    rows = conn.execute('''SELECT answer_id, reviewer, choice, opinion, created_at FROM argument_review.answers
        WHERE question_id=%s AND superseded_at IS NULL ORDER BY answer_id''', (question_id,)).fetchall()
    return [dict(zip(('answer_id', 'reviewer', 'choice', 'opinion', 'created_at'), r)) for r in rows]


def submit_answer(conn, question_id, reviewer, *, choice=None, opinion=None):
    opinion = (opinion or '').strip() or None
    q = conn.execute('SELECT payload FROM argument_review.questions WHERE question_id=%s', (question_id,)).fetchone()
    if not q:
        raise KeyError('unknown question')
    if choice is not None and choice not in {o['key'] for o in q[0]['options']}:
        raise ValueError('unknown choice')
    if choice is None and opinion is None:
        raise ValueError('choose an option or write your view')
    conn.execute('''UPDATE argument_review.answers SET superseded_at=now()
        WHERE question_id=%s AND reviewer=%s AND superseded_at IS NULL''', (question_id, reviewer))
    return conn.execute('''INSERT INTO argument_review.answers (question_id, reviewer, choice, opinion)
        VALUES (%s,%s,%s,%s) RETURNING answer_id''', (question_id, reviewer, choice, opinion)).fetchone()[0]


def followups_for(conn, answer_id):
    rows = conn.execute('''SELECT followup_id, kind, text, suggested_choice, reply_choice, reply_text, replied_at
        FROM argument_review.followups WHERE answer_id=%s ORDER BY followup_id''', (answer_id,)).fetchall()
    return [dict(zip(('followup_id', 'kind', 'text', 'suggested_choice', 'reply_choice', 'reply_text', 'replied_at'), r)) for r in rows]


def resolved_choice(answer, followups):
    """The reviewer's settled choice, or None while a follow-up is open, or 'unclear'."""
    if not followups:
        # An opinion waits for the clarifier, even with a choice: the words may disagree with it.
        return None if answer['opinion'] else answer['choice']
    last = followups[-1]
    if last['replied_at'] is None:
        return None
    if last['reply_choice']:
        return last['reply_choice']
    if sum(f['kind'] == 'ask' for f in followups) >= MAX_FOLLOWUPS:
        return 'unclear'
    return None


def needs_clarifying(answer, followups):
    if not answer['opinion']:
        return False
    if not followups:
        return True
    last = followups[-1]
    return (last['replied_at'] is not None and not last['reply_choice'] and bool(last['reply_text'])
            and sum(f['kind'] == 'ask' for f in followups) < MAX_FOLLOWUPS)


def reply_followup(conn, followup_id, reviewer, *, choice=None, text=None):
    row = conn.execute('''SELECT f.followup_id, a.reviewer, q.payload FROM argument_review.followups f
        JOIN argument_review.answers a USING (answer_id) JOIN argument_review.questions q ON q.question_id=a.question_id
        WHERE f.followup_id=%s AND f.replied_at IS NULL''', (followup_id,)).fetchone()
    if not row or row[1] != reviewer:
        raise KeyError('no open follow-up for this reviewer')
    if choice is not None and choice not in {o['key'] for o in row[2]['options']}:
        raise ValueError('unknown choice')
    if choice is None and not (text or '').strip():
        raise ValueError('choose an option or answer in words')
    conn.execute('''UPDATE argument_review.followups SET reply_choice=%s, reply_text=%s, replied_at=now()
        WHERE followup_id=%s''', (choice, (text or '').strip() or None, followup_id))


def state(conn, question_id):
    """Counts, decision and what is still open for one question."""
    ruling = conn.execute('SELECT ruler, choice, reason, created_at FROM argument_review.rulings WHERE question_id=%s',
                          (question_id,)).fetchone()
    answers = active_answers(conn, question_id)
    settled, pending, unclear = Counter(), 0, 0
    for a in answers:
        c = resolved_choice(a, followups_for(conn, a['answer_id']))
        if c is None:
            pending += 1
        elif c == 'unclear':
            unclear += 1
        else:
            settled[c] += 1
    total = sum(settled.values())
    top, top_n = (settled.most_common(1)[0] if settled else (None, 0))
    if ruling:
        decision = {'status': 'ruled', 'choice': ruling[1], 'by': ruling[0], 'reason': ruling[2]}
    elif total >= QUORUM and top_n * 2 > total:
        decision = {'status': 'final', 'choice': top, 'minority': {k: v for k, v in settled.items() if k != top}}
    elif unclear or (total >= QUORUM and top_n * 2 <= total):
        decision = {'status': 'owner', 'choice': None}
    else:
        decision = {'status': 'open', 'choice': None}
    return {'answers': len(answers), 'settled': dict(settled), 'pending': pending, 'unclear': unclear, **decision}


def rule(conn, question_id, ruler, choice, reason):
    if not (reason or '').strip():
        raise ValueError('a ruling needs a reason')
    conn.execute('''INSERT INTO argument_review.rulings (question_id, ruler, choice, reason) VALUES (%s,%s,%s,%s)
        ON CONFLICT (question_id) DO UPDATE SET ruler=EXCLUDED.ruler, choice=EXCLUDED.choice,
        reason=EXCLUDED.reason, created_at=now()''', (question_id, ruler, choice, reason.strip()))


def next_for(conn, reviewer):
    """Open follow-ups first, then the next question this reviewer has not answered."""
    f = conn.execute('''SELECT f.followup_id, f.kind, f.text, f.suggested_choice, a.question_id, a.choice, a.opinion
        FROM argument_review.followups f JOIN argument_review.answers a USING (answer_id)
        WHERE a.reviewer=%s AND a.superseded_at IS NULL AND f.replied_at IS NULL ORDER BY f.followup_id LIMIT 1''',
        (reviewer,)).fetchone()
    total = conn.execute('SELECT count(*) FROM argument_review.questions').fetchone()[0]
    done = conn.execute('''SELECT count(DISTINCT question_id) FROM argument_review.answers
        WHERE reviewer=%s AND superseded_at IS NULL''', (reviewer,)).fetchone()[0]
    progress = {'answered': done, 'total': total}
    if f:
        q = question(conn, f[4])
        return {'mode': 'followup', 'progress': progress, 'question': q,
                'followup': {'followup_id': f[0], 'kind': f[1], 'text': f[2], 'suggested_choice': f[3]},
                'your_answer': {'choice': f[5], 'opinion': f[6]}}
    row = conn.execute('''SELECT question_id FROM argument_review.questions q WHERE NOT EXISTS (
        SELECT 1 FROM argument_review.answers a WHERE a.question_id=q.question_id AND a.reviewer=%s AND a.superseded_at IS NULL)
        ORDER BY lecture, group_id, created_at, question_id LIMIT 1''', (reviewer,)).fetchone()
    if not row:
        return {'mode': 'done', 'progress': progress}
    return {'mode': 'question', 'progress': progress, 'question': question(conn, row[0])}


def question(conn, question_id):
    r = conn.execute('SELECT question_id, lecture, group_id, item_id, kind, payload FROM argument_review.questions WHERE question_id=%s',
                     (question_id,)).fetchone()
    if not r:
        raise KeyError('unknown question')
    return {'question_id': r[0], 'lecture': r[1], 'group_id': r[2], 'item_id': r[3], 'kind': r[4], **r[5]}


def my_answers(conn, reviewer):
    rows = conn.execute('''SELECT a.question_id, a.choice, a.opinion, a.created_at, q.payload FROM argument_review.answers a
        JOIN argument_review.questions q USING (question_id) WHERE a.reviewer=%s AND a.superseded_at IS NULL
        ORDER BY a.created_at DESC''', (reviewer,)).fetchall()
    return [{'question_id': r[0], 'choice': r[1], 'opinion': r[2], 'answered_at': r[3].isoformat(),
             'focus': ''.join(s['text'] for s in r[4]['sentences'] if s['focus'])[:60],
             'choice_label': next((o['label'] for o in r[4]['options'] if o['key'] == r[1]), None)} for r in rows]


def overview(conn):
    ids = [r[0] for r in conn.execute('SELECT question_id FROM argument_review.questions ORDER BY lecture, group_id, created_at').fetchall()]
    out = []
    for qid in ids:
        q = question(conn, qid)
        answers = [{**a, 'created_at': a['created_at'].isoformat(), 'followups': [
            {**f, 'replied_at': f['replied_at'].isoformat() if f['replied_at'] else None} for f in followups_for(conn, a['answer_id'])]}
            for a in active_answers(conn, qid)]
        out.append({'question': q, 'state': state(conn, qid), 'answers': answers})
    return out


# ---------------------------------------------------------------- follow-ups

CLARIFY_PROMPT = '''你協助整理同工對一道審閱題的回答。同工不熟電腦，也不熟 AI，常只寫自己的看法。
你只做一件事：判斷他的話是否已清楚對應題目的某一個選項。
- 清楚對應：kind 寫 confirm，suggested_choice 寫那個選項的 key，text 用一句白話請他確認（例：「你的意思是『這是一個理由』，對嗎？」）。
- 不清楚、或他的話同時符合兩個選項、或他提出選項沒有的看法：kind 寫 ask，suggested_choice 寫 null，text 寫一個具體、只問一件事的追問，指著原文的句子問（例：「你說『後面在下結論』，是指『所以耶穌是誰？耶穌就是神』這兩句嗎？」）。
不判斷誰對，不替他作答，不用 ID、句號或英文術語。'''

CLARIFY_SCHEMA = {'name': 'review_followup', 'schema': {'type': 'object', 'additionalProperties': False,
    'required': ['kind', 'suggested_choice', 'text'], 'properties': {
        'kind': {'type': 'string', 'enum': ['confirm', 'ask']},
        'suggested_choice': {'type': ['string', 'null']}, 'text': {'type': 'string'}}}}


def clarify_pending(conn, client, *, limit=20):
    """Ask the model one narrow question per opinion that is not yet clear; persist the follow-up."""
    rows = conn.execute('''SELECT a.answer_id, a.question_id, a.reviewer, a.choice, a.opinion FROM argument_review.answers a
        WHERE a.superseded_at IS NULL AND a.opinion IS NOT NULL ORDER BY a.answer_id''').fetchall()
    made = []
    for answer_id, qid, reviewer, choice, opinion in rows:
        answer = {'choice': choice, 'opinion': opinion}
        followups = followups_for(conn, answer_id)
        if not needs_clarifying(answer, followups) or len(made) >= limit:
            continue
        q = question(conn, qid)
        payload = {'question': q['question'], 'options': q['options'],
                   'passage': [s['text'] for s in q['sentences'] if s['focus']],
                   'context': [s['text'] for s in q['sentences'] if not s['focus']],
                   'reviewer_choice': next((o['label'] for o in q['options'] if o['key'] == choice), None),
                   'reviewer_words': opinion,
                   'earlier_followups': [{'asked': f['text'], 'reply': f['reply_text']} for f in followups]}
        out = client.generate_json(CLARIFY_PROMPT, json.dumps(payload, ensure_ascii=False), CLARIFY_SCHEMA)
        if out['kind'] == 'confirm' and out['suggested_choice'] not in {o['key'] for o in q['options']}:
            out = {**out, 'kind': 'ask', 'suggested_choice': None}
        conn.execute('''INSERT INTO argument_review.followups (answer_id, kind, text, suggested_choice, model)
            VALUES (%s,%s,%s,%s,%s)''', (answer_id, out['kind'], out['text'], out['suggested_choice'],
                                        getattr(client, 'model', 'unknown')))
        made.append({'answer_id': answer_id, **out})
    return made


# ---------------------------------------------------------------- export

def export(conn, cases_path):
    """Write settled reviews into cases.json; a structural change the program cannot make is listed, not guessed."""
    path = Path(cases_path)
    cases = json.loads(path.read_text())
    applied, needs_edit = [], []
    for group in cases['groups']:
        for item in group['items']:
            qid = question_id(cases['lecture'], group['group'], item['id'])
            if not conn.execute('SELECT 1 FROM argument_review.questions WHERE question_id=%s', (qid,)).fetchone():
                continue
            s = state(conn, qid)
            if s['status'] not in ('final', 'ruled'):
                continue
            item['review'] = {k: s[k] for k in ('status', 'choice', 'settled') if k in s}
            if s['status'] == 'ruled':
                item['review'].update(by=s['by'], reason=s['reason'])
            if s['choice'] == DRAFT_CHOICE[item['kind']]:
                item['status'] = 'final'
                applied.append((group['group'], item['id'], 'agreed'))
            elif s['choice'] in KIND_FOR_CHOICE and s['choice'] not in ('several',):
                item['kind'] = KIND_FOR_CHOICE[s['choice']]
                item['status'] = 'final'
                applied.append((group['group'], item['id'], f"kind → {item['kind']}"))
            else:
                item['status'] = 'reviewed_needs_edit'
                needs_edit.append((group['group'], item['id'], s['choice']))
    path.write_text(json.dumps(cases, ensure_ascii=False, indent=1))
    return {'applied': applied, 'needs_edit': needs_edit}


# ---------------------------------------------------------------- command

def disagreements_from(report_paths):
    out = {}
    for p in report_paths or []:
        r = json.loads(Path(p).read_text())
        for e in r['errors']:
            if e['kind'] != 'record' and '+' not in e['item']:
                out[(e['group'], e['item'])] = f"生成結果：{ {'split': '分成兩段以上', 'merged': '和別段合在一起', 'missed': '沒有放進論證', 'partial': '只放進一部分'}.get(e['verdict'], e['verdict']) }"
    return out


def main():
    p = argparse.ArgumentParser(description='同工 review of golden cases')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('migrate')
    pub = sub.add_parser('publish', help='Create questions for one lecture\'s draft answers')
    pub.add_argument('--cases', required=True)
    pub.add_argument('--media-url', required=True)
    pub.add_argument('--grading-report', action='append', help='argument_grading output whose disagreements also become questions')
    cl = sub.add_parser('clarify', help='Ask follow-up questions about free-text opinions (model calls)')
    cl.add_argument('--limit', type=int, default=20)
    ex = sub.add_parser('export', help='Write settled answers into cases.json')
    ex.add_argument('--cases', required=True)
    a = p.parse_args()
    from dotenv import load_dotenv
    load_dotenv('.env')
    if a.command == 'migrate':
        migrate()
        print('migrated', database_url())
        return
    with connect() as conn:
        if a.command == 'publish':
            cases = json.loads(Path(a.cases).read_text())
            qs = build_questions(cases, media_url=a.media_url, paragraphs=transcript(cases['lecture']),
                                 disagreements=disagreements_from(a.grading_report))
            print(json.dumps({'questions': len(qs), 'added': publish_questions(conn, qs, hashlib.sha256(Path(a.cases).read_bytes()).hexdigest())}))
        elif a.command == 'clarify':
            from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
            client = CodexSubscriptionClient(model='gpt-6.1-sol', reasoning_effort='medium', max_output_tokens=2000,
                                             executable=os.getenv('CODEX_EXECUTABLE'))
            print(json.dumps(clarify_pending(conn, client, limit=a.limit), ensure_ascii=False, indent=1))
        elif a.command == 'export':
            print(json.dumps(export(conn, a.cases), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
