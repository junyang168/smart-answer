"""Complete baseline certification only in a disposable socket-only database."""
import json
import time
import uuid

import psycopg
from psycopg import sql
import pytest

from backend.api.canonical_repository import integrity_baseline as b, integrity_journal as j
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, record_content_sha
from backend.pipeline import source_reconciliation as c
from backend.pipeline.viewpoint_validation_context import ValidationContext, ROUTINE_VALIDATION_LIMITS, ValidationBudgetExceeded
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres


@pytest.fixture(scope='module')
def database(_temporary_postgres):
    name='wkp357_baseline_'+uuid.uuid4().hex
    admin=_temporary_postgres.dsn('postgres');dsn=_temporary_postgres.dsn(name)
    with psycopg.connect(admin,autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    PostgresKnowledgeStore(dsn).migrate()
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


def insert(conn,*,collection='claims',retired=False):
    oid='C-'+uuid.uuid4().hex
    payload={'claim_id':oid,'revision':1,'statement':'ephemeral baseline fixture','review_status':'human_review_required'}
    conn.execute('INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload,retired_at) '
                 'VALUES (%s,%s,1,%s,%s,CASE WHEN %s THEN now() END)',
                 (collection,oid,record_content_sha(payload),psycopg.types.json.Jsonb(payload),retired))
    return oid


def expected(database):
    # Independent compatibility oracle is allowed only in this small fixture.
    with psycopg.connect(database) as conn:
        rows=conn.execute('SELECT collection,object_id,payload FROM wang_knowledge.objects WHERE retired_at IS NULL').fetchall()
    states={c.key(col,oid):p for col,oid,p in rows}
    collections=sorted(set(b.KNOWLEDGE_COLLECTIONS)|{col for col,_,_ in rows})
    return c.fingerprint(states),collections


def compile(database,tmp_path,**kwargs):
    sha,collections=expected(database)
    with psycopg.connect(database) as conn:
        bound=b.compile_baseline(conn,sha,collections,tmp_path/'baseline.json',**kwargs)
    return bound,sha,collections


def test_actual_full_cut_compatibility_and_bounded_new_snapshot_consumer(database,tmp_path,monkeypatch):
    with psycopg.connect(database) as conn:insert(conn);insert(conn,retired=True)
    bound,sha,collections=compile(database,tmp_path)
    value=json.loads((tmp_path/'baseline.json').read_text())
    assert value['registry_fingerprint_sha256']==sha
    assert set(value['active_counts'])==set(b.KNOWLEDGE_COLLECTIONS)
    assert any(n==0 for n in value['active_counts'].values())
    assert value['metrics']['sql_rows']>value['metrics']['active_payload_rows']
    monkeypatch.setattr(b,'compile_baseline',lambda *a,**k:pytest.fail('implicit baseline rebuild'))
    with psycopg.connect(database) as conn:
        result=b.consume_baseline(conn,bound,expected_sha256=sha,collections=collections)
    assert result['status']=='unchanged_complete_cut_observed' and not result['semantic_approval_granted']


def test_old_fingerprint_plus_new_generation_cannot_be_certified(database,tmp_path):
    sha,collections=expected(database)
    with psycopg.connect(database) as conn:insert(conn)
    with psycopg.connect(database) as conn,pytest.raises(ValueError,match='differs from expected'):
        b.compile_baseline(conn,sha,collections,tmp_path/'baseline.json')
    assert not (tmp_path/'baseline.json').exists()


def test_writer_hidden_by_RR_is_detected_by_fresh_final_transaction(database,tmp_path,monkeypatch):
    original=j.assert_unchanged_generation;calls=[]
    def check(cursor,token):
        if not calls:
            with psycopg.connect(database) as other:insert(other)
        calls.append(token)
        return original(cursor,token)
    monkeypatch.setattr(j,'assert_unchanged_generation',check)
    with pytest.raises(ValueError,match='generation changed'):compile(database,tmp_path)
    assert len(calls)==2 and not (tmp_path/'baseline.json').exists()


@pytest.mark.parametrize('kind',['insert','retire','delete','direct_payload'])
def test_actual_non_target_mutation_invalidates_consumer_without_full_rebuild(database,tmp_path,kind):
    with psycopg.connect(database) as conn:oid=insert(conn)
    bound,sha,collections=compile(database,tmp_path)
    with psycopg.connect(database) as conn:
        if kind=='insert':insert(conn)
        elif kind=='retire':conn.execute('UPDATE wang_knowledge.objects SET retired_at=now() WHERE object_id=%s',(oid,))
        elif kind=='delete':conn.execute('DELETE FROM wang_knowledge.objects WHERE object_id=%s',(oid,))
        else:
            conn.execute("UPDATE wang_knowledge.objects SET payload=payload||'{\"untracked\":true}'::jsonb WHERE object_id=%s",(oid,))
    with psycopg.connect(database) as conn,pytest.raises(ValueError,match='generation changed'):
        b.consume_baseline(conn,bound,expected_sha256=sha,collections=collections)
    # Keep deliberately invalid direct metadata out of later fixture baselines.
    with psycopg.connect(database) as conn:conn.execute('DELETE FROM wang_knowledge.objects WHERE object_id=%s',(oid,))


@pytest.mark.parametrize('kind',['revision','content','identity'])
def test_full_certification_rejects_inconsistent_SQL_metadata(database,tmp_path,kind):
    with psycopg.connect(database) as conn:
        oid=insert(conn)
        if kind=='revision':conn.execute('UPDATE wang_knowledge.objects SET revision=2 WHERE object_id=%s',(oid,))
        elif kind=='content':conn.execute("UPDATE wang_knowledge.objects SET content_sha256=%s WHERE object_id=%s",('a'*64,oid))
        else:
            payload={'claim_id':'different','revision':1}
            conn.execute('UPDATE wang_knowledge.objects SET payload=%s,content_sha256=%s WHERE object_id=%s',
                         (psycopg.types.json.Jsonb(payload),record_content_sha(payload),oid))
    try:
        with pytest.raises(ValueError,match='drift'):compile(database,tmp_path)
        assert not (tmp_path/'baseline.json').exists()
    finally:
        with psycopg.connect(database) as conn:conn.execute('DELETE FROM wang_knowledge.objects WHERE object_id=%s',(oid,))


def test_no_inherited_transaction_or_normal_runtime_compilation(database,tmp_path):
    sha,collections=expected(database)
    with psycopg.connect(database) as conn:
        conn.execute('SELECT 1')
        with pytest.raises(ValueError,match='idle'):b.compile_baseline(conn,sha,collections,tmp_path/'baseline.json')
    from backend.pipeline.viewpoint_validation_context import require_explicit_history_maintenance
    # Exercise the existing hard routine boundary without SQL/model state.
    with ValidationContext('routine',limits=ROUTINE_VALIDATION_LIMITS):
        with pytest.raises(ValidationBudgetExceeded):
            require_explicit_history_maintenance('complete Registry integrity baseline certification')


@pytest.mark.parametrize('kind',['rows','payload','value','seconds'])
def test_explicit_budget_stops_without_certificate(database,tmp_path,kind):
    with psycopg.connect(database) as conn:insert(conn)
    limits=b.BaselineLimits(**{'rows':1} if kind=='rows' else
                            {'payload_bytes':1} if kind=='payload' else
                            {'value_bytes':1} if kind=='value' else {'seconds':1e-9})
    with pytest.raises(ValueError):compile(database,tmp_path,limits=limits)
    assert not (tmp_path/'baseline.json').exists()


def test_empty_collection_cut_cannot_be_omitted_and_output_cannot_be_overwritten(database,tmp_path):
    sha,collections=expected(database)
    with psycopg.connect(database) as conn,pytest.raises(ValueError,match='empty known'):
        b.compile_baseline(conn,sha,collections[:-1],tmp_path/'baseline.json')
    compile(database,tmp_path)
    with psycopg.connect(database) as conn,pytest.raises(ValueError,match='overwrite'):
        b.compile_baseline(conn,sha,collections,tmp_path/'baseline.json')


def test_unknown_registered_collection_is_part_of_complete_cut(database,tmp_path):
    with psycopg.connect(database) as conn:oid=insert(conn,collection='offline_unknown_fixture')
    try:
        bound,sha,collections=compile(database,tmp_path)
        assert 'offline_unknown_fixture' in collections
        with psycopg.connect(database) as conn:
            assert not b.consume_baseline(conn,bound,expected_sha256=sha,collections=collections)['semantic_approval_granted']
    finally:
        with psycopg.connect(database) as conn:conn.execute('DELETE FROM wang_knowledge.objects WHERE object_id=%s',(oid,))


def test_extra_empty_collection_is_not_an_alternative_full_cut(database,tmp_path):
    sha,collections=expected(database);collections.append('invented_empty_collection')
    with psycopg.connect(database) as conn,pytest.raises(ValueError,match='denominator drift'):
        b.compile_baseline(conn,sha,collections,tmp_path/'baseline.json')
    assert not (tmp_path/'baseline.json').exists()


@pytest.mark.parametrize('kind',['physical','seal','code','expected','collection'])
def test_certificate_or_expected_authority_drift_refused(database,tmp_path,kind):
    bound,sha,collections=compile(database,tmp_path)
    if kind=='expected':sha='f'*64
    elif kind=='collection':collections.append('not_in_certificate')
    else:
        path=tmp_path/'baseline.json';value=json.loads(path.read_text())
        if kind=='code':value['implementation_bindings'][str(b.CODE[0])]='a'*64
        else:value['registry_fingerprint_sha256']='a'*64
        if kind=='code':value['artifact_sha256']=c.sha256_json({k:v for k,v in value.items() if k!='artifact_sha256'})
        path.write_text(json.dumps(value))
        if kind!='physical':bound['file_sha256']=c.file_sha256(path)
    with psycopg.connect(database) as conn,pytest.raises(ValueError):
        b.consume_baseline(conn,bound,expected_sha256=sha,collections=collections)


def test_actual_bounded_catalog_reads_independent_of_unrelated_object_and_journal_rows(database,tmp_path):
    class ObservedCursor:
        def __init__(self,inner,metrics):self.inner=inner;self.metrics=metrics
        def __enter__(self):self.inner.__enter__();return self
        def __exit__(self,*args):return self.inner.__exit__(*args)
        def execute(self,query,parameters=None):
            assert 'FROM wang_knowledge.objects' not in query
            assert 'FROM wang_knowledge.integrity_changes' not in query
            self.metrics['queries']+=1
            return self.inner.execute(query,parameters)
        def fetchall(self):
            rows=self.inner.fetchall();self.metrics['rows']+=len(rows);return rows
        def fetchone(self):
            row=self.inner.fetchone();self.metrics['rows']+=int(row is not None);return row
    class ObservedConnection:
        def __init__(self,inner,metrics):self.inner=inner;self.metrics=metrics
        @property
        def info(self):return self.inner.info
        def transaction(self):return self.inner.transaction()
        def cursor(self):return ObservedCursor(self.inner.cursor(),self.metrics)
    results=[];inserted=0
    for scale in (1,10,100):
        target=10*scale
        with psycopg.connect(database) as conn:
            for _ in range(target-inserted):insert(conn)
        inserted=target
        directory=tmp_path/str(scale);directory.mkdir()
        bound,sha,collections=compile(database,directory)
        # Each observation opens a fresh connection and fresh SQL snapshot;
        # no process-local memoized generation/current payload cache is used.
        for mode in ('cold','next_phase'):
            metrics={'queries':0,'rows':0};started=time.perf_counter()
            with psycopg.connect(database) as inner:
                result=b.consume_baseline(ObservedConnection(inner,metrics),bound,
                                          expected_sha256=sha,collections=collections)
            assert not result['semantic_approval_granted']
            metrics['seconds']=time.perf_counter()-started
            assert metrics['seconds']<5
            results.append((scale,mode,metrics))
    assert len({(r['queries'],r['rows']) for _,_,r in results})==1
    print('offline baseline component SQL observations, not whole-flow acceptance:',results)
