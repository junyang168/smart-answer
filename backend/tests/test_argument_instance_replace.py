"""Lecture replacement and review binding in a private socket-only PostgreSQL cluster."""
import copy
import json
import uuid

import psycopg
import pytest
from psycopg import sql

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.pipeline import argument_corrections as ac
from backend.pipeline import argument_instance_load as loader
from backend.tests.test_argument_review_binding import package, write_run, load_run
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres  # noqa: F401


@pytest.fixture
def conn(_temporary_postgres):  # noqa: F811
    name = f'argument787_{uuid.uuid4().hex}'
    admin = _temporary_postgres.dsn('postgres')
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    url = _temporary_postgres.dsn(name)
    PostgresKnowledgeStore(url).migrate()
    try:
        with psycopg.connect(url, autocommit=True) as c:
            yield c
    finally:
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


def test_repeated_replacement_retires_reused_ids_and_keeps_other_lectures(conn, tmp_path):
    original = package(); write_run(tmp_path / 'one', original)
    first = load_run(conn, tmp_path / 'one')
    write_run(tmp_path / 'other', package(2))
    unrelated = load_run(conn, tmp_path / 'other')
    select_other = "SELECT collection,object_id,revision,content_sha256,payload FROM wang_knowledge.objects WHERE object_id IN ('SRC-2','F-2','DK-2-CL0001','DK-2-CL0002','DK-2-P01-ST01') ORDER BY collection,object_id"
    before = conn.execute(select_other).fetchall()
    second = copy.deepcopy(original); second['claims'][0]['title'] = '第二版的判斷'
    write_run(tmp_path / 'two', second)
    with pytest.raises(ValueError, match='pass replaces='):
        load_run(conn, tmp_path / 'two')
    middle = load_run(conn, tmp_path / 'two', replaces=first['change_set_id'])
    assert conn.execute("SELECT revision FROM wang_knowledge.objects WHERE object_id='DK-1-CL0002'").fetchone()[0] == 2
    third = copy.deepcopy(second); ac.drop_claim(third, 'CL0002')
    write_run(tmp_path / 'three', third)
    last = load_run(conn, tmp_path / 'three', replaces=middle['change_set_id'])
    assert last['read_back'] == 'matches'
    retired = conn.execute("SELECT revision,retired_at,payload->>'review_status' FROM wang_knowledge.objects WHERE object_id='DK-1-CL0002'").fetchone()
    assert retired[0] == 3 and retired[1] is not None and retired[2] == 'retired'
    assert last['retired'] == {'claims': 1}
    assert conn.execute("SELECT count(*) FROM wang_knowledge.object_versions WHERE object_id='DK-1-CL0002'").fetchone()[0] == 3
    assert conn.execute(select_other).fetchall() == before
    assert loader.holder(conn, 'SRC-1') == last['change_set_id']
    assert loader.holder(conn, 'SRC-2') == unrelated['change_set_id']
    with pytest.raises(ValueError, match='held by'):
        load_run(conn, tmp_path / 'three', replaces=first['change_set_id'])


@pytest.mark.parametrize('corrections', [[], [
    {'op': 'drop_claim', 'short': 'CL0002', 'ruling': 'Owner: it is evidence, not a separate Claim.'}
], [
    {'op': 'restate_claim', 'short': 'CL0001', 'statement': '負責人裁定的表述',
     'basis_fragment_id': 'F-1', 'ruling': 'Owner: use this wording.'}
]])
def test_checked_original_and_its_recorded_corrections_load_without_another_review(conn, tmp_path, corrections):
    write_run(tmp_path / 'run', corrections=corrections)
    result = load_run(conn, tmp_path / 'run')
    assert result['read_back'] == 'matches'
    assert result['written']['claims'] == (1 if corrections and corrections[0]['op'] == 'drop_claim' else 2)
    metadata = conn.execute('SELECT metadata FROM wang_knowledge.change_sets WHERE change_set_id=%s', (result['change_set_id'],)).fetchone()[0]
    assert metadata['shape_report_sha256'] == loader.sha(tmp_path / 'run/shape/shape-final.json')


def test_readback_does_not_report_success_if_retirement_left_old_records_live(conn, tmp_path, monkeypatch):
    original = package(); write_run(tmp_path / 'one', original)
    first = load_run(conn, tmp_path / 'one')
    ac.drop_claim(original, 'CL0002'); write_run(tmp_path / 'two', original)
    monkeypatch.setattr(loader, 'retire_lecture', lambda *args, **kwargs: {})
    with pytest.raises(ValueError, match='old lecture still has live records'):
        load_run(conn, tmp_path / 'two', replaces=first['change_set_id'])


def test_a_new_record_namespace_retires_the_old_graph_and_readers_use_the_new_lecture(conn, tmp_path):
    from backend.pipeline.chapter_slide_claims import instance_records
    from backend.pipeline.lecture_reader import lectures
    original = package(); write_run(tmp_path / 'one', original)
    first = load_run(conn, tmp_path / 'one')
    rerun = json.loads(json.dumps(original).replace('DK-1-', 'DK-rerun-1-'))
    write_run(tmp_path / 'two', rerun)
    last = load_run(conn, tmp_path / 'two', replaces=first['change_set_id'])
    assert last['retired'] == {'claims': 2, 'evidence_steps': 1}
    records, _ = instance_records(conn, last['change_set_id'])
    assert {k: len(v) for k, v in records.items()} == {k: v for k, v in last['written'].items() if v}
    assert {c['claim_id'] for c in records['claims']} == {'DK-rerun-1-CL0001', 'DK-rerun-1-CL0002'}
    assert [r['id'] for r in lectures(conn)] == [last['change_set_id']]
    with pytest.raises(ValueError, match='replaced by ' + last['change_set_id']):
        instance_records(conn, first['change_set_id'])
