import json
import uuid

import psycopg
import pytest
from psycopg import sql

from backend.pipeline import argument_review as review
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres  # noqa: F401


@pytest.fixture
def conn(_temporary_postgres):  # noqa: F811
    database = f'review_{uuid.uuid4().hex}'
    admin = _temporary_postgres.dsn('postgres')
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    url = _temporary_postgres.dsn(database)
    review.migrate(url)
    with psycopg.connect(url) as c:
        yield c
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()', (database,))
        c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))


PARAGRAPHS = {'10': {'text': '甲一句。甲二句。所以結論。好。', 'start_time': '100', 'end_time': '116'}}
CASES = {'lecture': 'L', 'groups': [{'group': 'G01', 'items': [
    {'id': 'a', 'kind': 'step', 'status': 'draft', 'sentences': [{'script_index': 10, 'text': '甲一句。'}, {'script_index': 10, 'text': '甲二句。'}]},
    {'id': 'ct', 'kind': 'claim_text', 'status': 'owner', 'sentences': [{'script_index': 10, 'text': '所以結論。'}]},
    {'id': 'o', 'kind': 'other', 'status': 'draft', 'sentences': [{'script_index': 10, 'text': '好。'}]}]}]}


def publish(conn):
    qs = review.build_questions(CASES, media_url='/a.mp3', paragraphs=PARAGRAPHS)
    review.publish_questions(conn, qs, 'x' * 64)
    return {q['item_id']: q['question_id'] for q in qs}


def test_questions_cover_drafts_only_with_audio_and_context():
    qs = review.build_questions(CASES, media_url='/a.mp3', paragraphs=PARAGRAPHS)
    assert [q['item_id'] for q in qs] == ['a', 'o']
    a = qs[0]['payload']
    assert [s['focus'] for s in a['sentences']] == [True, True, False, False]
    assert a['audio']['start'] == pytest.approx(100.0) and a['audio']['end'] <= 116
    assert all('ID' not in o['label'] for o in a['options'])


def test_three_matching_answers_make_it_final_and_export_marks_the_case(conn, tmp_path):
    ids = publish(conn)
    for who in ('p1', 'p2', 'p3'):
        review.submit_answer(conn, ids['a'], who, choice='one')
    assert review.state(conn, ids['a'])['status'] == 'final'
    path = tmp_path / 'cases.json'
    path.write_text(json.dumps(CASES, ensure_ascii=False))
    out = review.export(conn, path)
    item = json.loads(path.read_text())['groups'][0]['items'][0]
    assert item['status'] == 'final' and out['applied'] == [('G01', 'a', 'agreed')]


def test_no_majority_goes_to_the_owner_and_a_ruling_decides(conn):
    ids = publish(conn)
    for who, choice in (('p1', 'one'), ('p2', 'several'), ('p3', 'conclusion'), ('p4', 'aside')):
        review.submit_answer(conn, ids['a'], who, choice=choice)
    assert review.state(conn, ids['a'])['status'] == 'owner'
    review.rule(conn, ids['a'], 'owner', 'several', '「因為」之後是第二個理由')
    assert review.state(conn, ids['a'])['status'] == 'ruled'


def test_an_opinion_waits_for_a_followup_and_the_reply_settles_it(conn):
    ids = publish(conn)
    review.submit_answer(conn, ids['a'], 'p1', opinion='我覺得後面那句才是重點')
    assert review.state(conn, ids['a'])['pending'] == 1

    class Client:
        model = 'fake'
        def generate_json(self, system, user, schema):
            assert '我覺得後面那句才是重點' in user
            return {'kind': 'ask', 'suggested_choice': None, 'text': '你是說「甲二句」是結論嗎？'}
    made = review.clarify_pending(conn, Client())
    assert len(made) == 1 and review.clarify_pending(conn, Client()) == []
    nxt = review.next_for(conn, 'p1')
    assert nxt['mode'] == 'followup' and nxt['followup']['text'].startswith('你是說')
    review.reply_followup(conn, nxt['followup']['followup_id'], 'p1', choice='one')
    assert review.state(conn, ids['a'])['settled'] == {'one': 1}
    with pytest.raises(KeyError):
        review.reply_followup(conn, nxt['followup']['followup_id'], 'p2', choice='one')


def test_two_unanswered_followups_leave_the_opinion_unclear_for_the_owner(conn):
    ids = publish(conn)
    review.submit_answer(conn, ids['a'], 'p1', opinion='不太確定')

    class Client:
        model = 'fake'
        def generate_json(self, *a):
            return {'kind': 'ask', 'suggested_choice': None, 'text': '可以多說一點嗎？'}
    for _ in range(2):
        review.clarify_pending(conn, Client())
        f = review.next_for(conn, 'p1')['followup']
        review.reply_followup(conn, f['followup_id'], 'p1', text='還是不確定')
    assert review.clarify_pending(conn, Client()) == []
    assert review.state(conn, ids['a'])['status'] == 'owner'


def test_a_changed_answer_supersedes_the_old_one(conn):
    ids = publish(conn)
    review.submit_answer(conn, ids['o'], 'p1', choice='filler')
    review.submit_answer(conn, ids['o'], 'p1', choice='content')
    assert [a['choice'] for a in review.active_answers(conn, ids['o'])] == ['content']
    assert review.next_for(conn, 'p1')['question']['item_id'] == 'a'


def test_review_records_never_go_to_the_current_database(monkeypatch):
    monkeypatch.setenv('KNOWLEDGE_DATABASE_URL', 'postgresql:///smart_answer_knowledge')
    monkeypatch.setenv('ARGUMENT_DATABASE_URL', 'postgresql:///smart_answer_knowledge')
    with pytest.raises(RuntimeError, match='current knowledge database'):
        review.database_url()
