"""Socket-only ephemeral SQL tests; never connect to the configured Registry."""
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import psycopg
from psycopg import sql

from backend.api.canonical_repository import integrity_journal as j
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, record_content_sha
# Reuse only the existing isolated PostgreSQL lifecycle, not its content tests.
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres


@pytest.fixture(scope='module')
def database(_temporary_postgres):
    name='wkp357_integrity_'+uuid.uuid4().hex
    admin=_temporary_postgres.dsn('postgres');dsn=_temporary_postgres.dsn(name)
    with psycopg.connect(admin,autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    store=PostgresKnowledgeStore(dsn);store.migrate()
    try:
        with psycopg.connect(dsn) as conn:
            assert conn.info.host.startswith('/tmp/wkp364-pg-sock-')
            assert conn.execute('SELECT current_database()').fetchone()==(name,)
            conn.execute(j.INSTALL_PATH.read_text())
        yield dsn
    finally:
        with psycopg.connect(admin,autocommit=True) as conn:
            conn.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()',(name,))
            conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


def insert(cursor,oid):
    payload={'claim_id':oid,'revision':1,'statement':'isolated fixture','review_status':'human_review_required'}
    cursor.execute('''INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload)
                      VALUES ('claims',%s,1,%s,%s::jsonb)''',(oid,record_content_sha(payload),psycopg.types.json.Jsonb(payload)))


def test_actual_insert_update_retire_delete_write_set_and_indexes(database):
    oid='C-'+uuid.uuid4().hex;keys=[('claims',oid)]
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        before=j.observe_generation(cursor,lock=True);old=j.read_object_signatures(cursor,keys)
        insert(cursor,oid)
        cursor.execute("UPDATE wang_knowledge.objects SET retired_at=now() WHERE object_id=%s",(oid,))
        mid=j.read_object_signatures(cursor,keys)
        assert mid[keys[0]]['retired'] is True
        cursor.execute('DELETE FROM wang_knowledge.objects WHERE object_id=%s',(oid,))
        new=j.read_object_signatures(cursor,keys);after=j.observe_generation(cursor)
        assert old==new=={keys[0]:None}
        result=j.verify_transaction_delta(cursor,before,after,old,new)
        assert result['rows']==3 and not result['semantic_approval_granted']
        cursor.execute('EXPLAIN (FORMAT JSON) SELECT * FROM wang_knowledge.integrity_changes WHERE journal_id=%s::uuid AND sequence>%s AND sequence<=%s',
                       (before['journal_id'],before['sequence'],after['sequence']))
        assert 'integrity_changes_pkey' in str(cursor.fetchone())


def test_actual_direct_SQL_non_target_payload_change_invalidates_global_generation(database):
    oid='C-'+uuid.uuid4().hex
    with psycopg.connect(database) as conn,conn.cursor() as cursor:insert(cursor,oid)
    with psycopg.connect(database) as conn,conn.cursor() as cursor:baseline=j.observe_generation(cursor)
    with psycopg.connect(database) as conn:
        # Deliberately leave content SHA/revision unchanged: the DB trigger
        # observes physical row changes rather than trusting app metadata.
        conn.execute("UPDATE wang_knowledge.objects SET payload=payload||'{\"unexpected\":true}'::jsonb WHERE object_id=%s",(oid,))
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        with pytest.raises(ValueError,match='generation changed'):j.assert_unchanged_generation(cursor,baseline)


def test_actual_unplanned_key_and_multiple_transactions_reject(database):
    a,b='C-'+uuid.uuid4().hex,'C-'+uuid.uuid4().hex
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        before=j.observe_generation(cursor);old=j.read_object_signatures(cursor,[('claims',a)])
        insert(cursor,a);insert(cursor,b);after=j.observe_generation(cursor)
        new=j.read_object_signatures(cursor,[('claims',a)])
        with pytest.raises(ValueError,match='non-target'):j.verify_transaction_delta(cursor,before,after,old,new)
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        before=j.observe_generation(cursor);old=j.read_object_signatures(cursor,[('claims',a)])
        cursor.execute('UPDATE wang_knowledge.objects SET revision=2 WHERE object_id=%s',(a,))
    with psycopg.connect(database) as conn:
        conn.execute('UPDATE wang_knowledge.objects SET revision=3 WHERE object_id=%s',(a,))
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        after=j.observe_generation(cursor);new=j.read_object_signatures(cursor,[('claims',a)])
        with pytest.raises(ValueError,match='multiple transaction'):j.verify_transaction_delta(cursor,before,after,old,new)


def test_actual_rollback_preserves_generation_and_negative_identity(database):
    oid='C-'+uuid.uuid4().hex
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        baseline=j.observe_generation(cursor);insert(cursor,oid)
        assert j.observe_generation(cursor)['sequence']==baseline['sequence']+1
        conn.rollback()
        assert j.assert_unchanged_generation(cursor,baseline)==baseline
        assert j.read_object_signatures(cursor,[('claims',oid)])=={('claims',oid):None}


@pytest.mark.parametrize('table',['objects','integrity_head','integrity_changes'])
def test_actual_truncate_and_direct_metadata_edits_refused(database,table):
    with psycopg.connect(database) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            # Reach the custom objects trigger beyond the pre-existing edges
            # FK backstop. This CASCADE targets only this disposable test DB.
            conn.execute(sql.SQL('TRUNCATE wang_knowledge.{}{}').format(
                sql.Identifier(table),sql.SQL(' CASCADE' if table=='objects' else '')))
        conn.rollback()
        if table!='objects':
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(sql.SQL('DELETE FROM wang_knowledge.{}').format(sql.Identifier(table)))
            conn.rollback()


def test_actual_disable_reenable_catalog_generation_does_not_restore_old_proof(database):
    with psycopg.connect(database) as conn,conn.cursor() as cursor:baseline=j.observe_generation(cursor)
    with psycopg.connect(database) as conn:
        conn.execute('ALTER TABLE wang_knowledge.objects DISABLE TRIGGER integrity_capture')
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        with pytest.raises(ValueError,match='disabled'):j.observe_generation(cursor)
    with psycopg.connect(database) as conn:
        conn.execute('ALTER TABLE wang_knowledge.objects ENABLE ALWAYS TRIGGER integrity_capture')
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        with pytest.raises(ValueError,match='generation changed'):j.assert_unchanged_generation(cursor,baseline)


def test_actual_two_writers_serialize_at_transactional_head(database):
    a,b='C-'+uuid.uuid4().hex,'C-'+uuid.uuid4().hex
    with psycopg.connect(database) as first,first.cursor() as cursor:
        baseline=j.observe_generation(cursor,lock=True)
        insert(cursor,a)
        def second_writer():
            with psycopg.connect(database) as conn,conn.cursor() as cur:
                conn.execute("SET lock_timeout='2s'")
                insert(cur,b)
                return j.observe_generation(cur)['sequence']
        with ThreadPoolExecutor(max_workers=1) as pool:
            second=pool.submit(second_writer)
            first.commit()
            assert second.result(timeout=3)==baseline['sequence']+2


def test_actual_timezone_does_not_change_physical_signature_or_engine_identity(database):
    oid='C-'+uuid.uuid4().hex
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        insert(cursor,oid)
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        conn.execute("SET timezone='UTC'")
        baseline=j.observe_generation(cursor)
        expected=j.read_object_signatures(cursor,[('claims',oid)])
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        conn.execute("SET timezone='America/Chicago'")
        assert j.assert_unchanged_generation(cursor,baseline)==baseline
        assert j.read_object_signatures(cursor,[('claims',oid)])==expected


def test_actual_missing_delta_index_vetoes_before_any_history_read(database):
    # Rollback restores the original key. Even a small interval must refuse
    # a journal without its required access path, not silently SeqScan H.
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        conn.execute('ALTER TABLE wang_knowledge.integrity_changes DROP CONSTRAINT integrity_changes_pkey')
        with pytest.raises(ValueError,match='index missing'):j.observe_generation(cursor)
        conn.rollback()


def test_actual_function_replace_restore_invalidates_old_catalog_proof(database):
    with psycopg.connect(database) as conn,conn.cursor() as cursor:baseline=j.observe_generation(cursor)
    replacement="""CREATE OR REPLACE FUNCTION wang_knowledge.deny_integrity_truncate()
        RETURNS trigger LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog
        AS $wkp$BEGIN RETURN NULL; END;$wkp$;"""
    with psycopg.connect(database) as conn:conn.execute(replacement)
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        with pytest.raises(ValueError,match='function definition'):j.observe_generation(cursor)
    declaration=j._declared_bodies()['deny_integrity_truncate']
    with psycopg.connect(database) as conn:
        conn.execute("CREATE OR REPLACE FUNCTION wang_knowledge.deny_integrity_truncate() "
            "RETURNS trigger LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog "
            "AS $wkp$"+declaration+"$wkp$;")
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        with pytest.raises(ValueError,match='generation changed'):j.assert_unchanged_generation(cursor,baseline)


def test_actual_same_named_foreign_trigger_function_cannot_authenticate_installation(database):
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        conn.execute("CREATE FUNCTION public.capture_object_integrity_change() RETURNS trigger "
            "LANGUAGE plpgsql AS $$BEGIN RETURN NULL; END;$$")
        conn.execute('DROP TRIGGER integrity_capture ON wang_knowledge.objects')
        conn.execute('CREATE TRIGGER integrity_capture AFTER INSERT OR UPDATE OR DELETE ON wang_knowledge.objects '
                     'FOR EACH ROW EXECUTE FUNCTION public.capture_object_integrity_change()')
        conn.execute('ALTER TABLE wang_knowledge.objects ENABLE ALWAYS TRIGGER integrity_capture')
        with pytest.raises(ValueError,match='trigger absent'):j.observe_generation(cursor)
        conn.rollback()


def test_actual_conditionally_skipped_capture_refused(database):
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        conn.execute('DROP TRIGGER integrity_capture ON wang_knowledge.objects')
        conn.execute("CREATE TRIGGER integrity_capture AFTER INSERT OR UPDATE OR DELETE ON wang_knowledge.objects "
            "FOR EACH ROW WHEN (current_setting('wkp357.skip_integrity',true) IS DISTINCT FROM 'yes') "
            "EXECUTE FUNCTION wang_knowledge.capture_object_integrity_change()")
        conn.execute('ALTER TABLE wang_knowledge.objects ENABLE ALWAYS TRIGGER integrity_capture')
        with pytest.raises(ValueError,match='trigger absent'):j.observe_generation(cursor)
        conn.rollback()


def test_actual_fast_column_DDL_invalidates_baseline_without_row_trigger(database):
    with psycopg.connect(database) as conn,conn.cursor() as cursor:baseline=j.observe_generation(cursor)
    with psycopg.connect(database) as conn:
        conn.execute('ALTER TABLE wang_knowledge.objects ADD COLUMN integrity_fixture_extra integer DEFAULT 1')
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        observed=j.observe_generation(cursor)
        assert observed['sequence']==baseline['sequence']
        with pytest.raises(ValueError,match='generation changed'):j.assert_unchanged_generation(cursor,baseline)


@pytest.mark.parametrize('mutation',[
    'UPDATE wang_knowledge.integrity_head SET sequence=sequence-1',
    'DELETE FROM wang_knowledge.integrity_head',
    'DELETE FROM wang_knowledge.integrity_changes',
    'UPDATE wang_knowledge.integrity_changes SET transaction_id=transaction_id',
])
def test_actual_nested_trigger_cannot_rewind_head_or_rewrite_old_history(database,mutation):
    with psycopg.connect(database) as conn,conn.cursor() as cursor:
        # Ensure even an empty-log DELETE cannot pass accidentally as a no-op.
        insert(cursor,'C-'+uuid.uuid4().hex)
        conn.execute('CREATE TABLE public.integrity_nested_fixture (value integer)')
        conn.execute('CREATE FUNCTION public.integrity_nested_fixture() RETURNS trigger LANGUAGE plpgsql '
                     'AS $$BEGIN '+mutation+'; RETURN NEW; END;$$')
        conn.execute('CREATE TRIGGER nested_fixture AFTER INSERT ON public.integrity_nested_fixture '
                     'FOR EACH ROW EXECUTE FUNCTION public.integrity_nested_fixture()')
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute('INSERT INTO public.integrity_nested_fixture VALUES (1)')
        conn.rollback()
