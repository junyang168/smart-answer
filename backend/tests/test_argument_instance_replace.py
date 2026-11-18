"""Replacing a lecture in a throwaway copy of the instance schema, with lecture 1's real run.

Skips when the local Postgres or the run files are not there."""
import hashlib
import json
import os
from pathlib import Path

import pytest

RUN = Path('/opt/homebrew/var/www/church/web/argument-layer-runs/gpt-20261117-lecture1-v6')
DB = 'argument_replace_test_787'


@pytest.fixture
def conn():
    psycopg = pytest.importorskip('psycopg')
    from dotenv import load_dotenv
    load_dotenv('.env')
    if not RUN.is_dir():
        pytest.skip('lecture 1 run files are not on this machine')
    from backend.pipeline.argument_review import database_url
    base = database_url().rsplit('/', 1)[0]
    try:
        with psycopg.connect(base + '/postgres', autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS {DB}')
            admin.execute(f'CREATE DATABASE {DB}')
    except psycopg.Error:
        pytest.skip('no local Postgres to create a throwaway database')
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    PostgresKnowledgeStore(base + '/' + DB).migrate()
    with psycopg.connect(base + '/' + DB) as c:
        yield c
    with psycopg.connect(base + '/postgres', autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS {DB}')


def rerun(tmp_path):
    """Lecture 1's candidate under a new record namespace, as a rerun would write it."""
    candidate = RUN / 'creation' / 'candidate-owner-corrected.json'
    text = candidate.read_text().replace('DK-b73e14ba9583df2f6cce', 'DK-0000rerun000000000000')
    text = text.replace('登山寶訓先顯明彌賽亞的身份', '登山寶訓分兩大段：先顯明彌賽亞的身份')
    new = tmp_path / 'candidate.json'
    new.write_text(text)
    receipt = tmp_path / 'candidate.receipt.json'
    receipt.write_text(json.dumps({'candidate_sha256': 'x', 'corrected_sha256': hashlib.sha256(new.read_bytes()).hexdigest()}))
    return new, receipt


def test_a_rerun_replaces_the_lecture_and_retires_its_old_records(conn, tmp_path):
    from backend.pipeline import argument_instance_load as L
    from backend.pipeline.chapter_slide_claims import instance_records
    fidelity = RUN / 'fidelity' / 'fidelity-final.json'
    first = L.load(conn, RUN / 'creation' / 'candidate-owner-corrected.json',
                   receipt_path=RUN / 'creation' / 'candidate-owner-corrected.receipt.json', fidelity_path=fidelity, run_dir=RUN)
    old = first['change_set_id']
    new_candidate, receipt = rerun(tmp_path)
    with pytest.raises(ValueError, match='pass replaces='):
        L.load(conn, new_candidate, receipt_path=receipt, fidelity_path=fidelity, run_dir=tmp_path)
    second = L.load(conn, new_candidate, receipt_path=receipt, fidelity_path=fidelity, run_dir=tmp_path, replaces=old)
    assert second['read_back'] == 'matches'
    # Every old record is either retired or rewritten under the same ID (the source document,
    # and SourceFragments whose ID comes from his words) as a new revision.
    rewritten = conn.execute("""SELECT count(*) FROM wang_knowledge.object_versions WHERE change_set_id=%s
                                 AND revision > 1 AND payload->>'review_status' <> 'retired'""", (second['change_set_id'],)).fetchone()[0]
    assert rewritten >= 1 and sum(second['retired'].values()) + rewritten == sum(first['written'].values())
    records, _ = instance_records(conn, second['change_set_id'])
    assert {k: len(v) for k, v in records.items()} == second['written']
    assert any(c['statement'].startswith('登山寶訓分兩大段') for c in records['claims'])
    with pytest.raises(ValueError, match='replaced by ' + second['change_set_id']):
        instance_records(conn, old)
    assert L.holder(conn, second['source_id']) == second['change_set_id']
    from backend.pipeline import lecture_reader
    assert [x['id'] for x in lecture_reader.lectures(conn)] == [second['change_set_id']]
    revisions = conn.execute("""SELECT count(*) FROM wang_knowledge.object_versions WHERE change_set_id=%s
                                AND payload->>'review_status'='retired'""", (second['change_set_id'],)).fetchone()[0]
    assert revisions == sum(second['retired'].values())  # history keeps each retirement
    with pytest.raises(ValueError, match='held by'):
        L.load(conn, new_candidate, receipt_path=receipt, fidelity_path=fidelity, run_dir=tmp_path, replaces=old)
