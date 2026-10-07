"""Bounded read-only transition observations in disposable SQL only."""
from dataclasses import asdict
import json
import time
import uuid

import psycopg
import pytest

from backend.api.canonical_repository import integrity_baseline as b, integrity_transition as t, integrity_journal as j
from backend.api.canonical_repository.postgres_store import ChangeOperation,ChangeSetPlan,record_content_sha,stored_operation_payload,ChangeSetConflict
from backend.tests.test_registry_integrity_baseline_postgres import database,expected
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres


@pytest.fixture(scope='module')
def indexed(database):
    with psycopg.connect(database) as conn:conn.execute(t.INDEX_INSTALL_PATH.read_text())
    return database


def plan():
    oid='C-'+uuid.uuid4().hex
    payload={'claim_id':oid,'statement':'disposable plan observation, not approval','review_status':'human_review_required'}
    op=ChangeOperation('create','claims',oid,None,record_content_sha(payload),None,1,payload)
    fingerprint=j.sha256_json(asdict(op))
    return ChangeSetPlan('KCS-'+fingerprint[:20],fingerprint,'fixture-package','offline-fixture','a'*64,(op,),0,())


def prepare(database,tmp_path):
    sha,collections=expected(database)
    with psycopg.connect(database) as conn:
        bound=b.compile_baseline(conn,sha,collections,tmp_path/'baseline.json')
    p=plan();keys=[(o.collection,o.object_id) for o in p.operations]
    return dict(baseline_binding=bound,baseline_sha256=sha,collections=collections,
                plan=p,plan_sha256=j.sha256_json(asdict(p)),metadata={},expected_before={k:None for k in keys},
                output=tmp_path/'observation.json')


def write_fixture(conn,p,*,objects=True,ledger=True):
    # This intentionally does NOT claim to exercise original semantic/model
    # apply gates. It creates a precise SQL ledger fixture in its own database.
    if ledger:
        conn.execute('INSERT INTO wang_knowledge.change_sets(change_set_id,fingerprint_sha256,package_id,source_kind,source_sha256,status,summary,metadata,applied_at) '
                     "VALUES (%s,%s,%s,%s,%s,'applied',%s,'{}',now())",
                     (p.change_set_id,p.fingerprint_sha256,p.package_id,p.source_kind,p.source_sha256,
                      psycopg.types.json.Jsonb(p.as_dict()['summary'])))
    for index,op in enumerate(p.operations):
        payload=stored_operation_payload(op)
        if objects:
            conn.execute('INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload) VALUES (%s,%s,%s,%s,%s)',
                         (op.collection,op.object_id,op.after_revision,op.after_sha256,psycopg.types.json.Jsonb(payload)))
        if ledger:
            conn.execute('INSERT INTO wang_knowledge.object_versions(collection,object_id,revision,content_sha256,payload,change_set_id) VALUES (%s,%s,%s,%s,%s,%s)',
                         (op.collection,op.object_id,op.after_revision,op.after_sha256,psycopg.types.json.Jsonb(payload),p.change_set_id))
            conn.execute('INSERT INTO wang_knowledge.change_operations(change_set_id,operation_index,operation,collection,object_id,before_sha256,after_sha256,before_revision,after_revision,details) '
                         'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                         (p.change_set_id,index,op.operation,op.collection,op.object_id,op.before_sha256,op.after_sha256,
                          op.before_revision,op.after_revision,psycopg.types.json.Jsonb({})))


def current(conn,args):
    with conn.cursor() as cursor:
        return j.read_object_signatures(cursor,list(args['expected_before']))


def observe(database,args):
    with psycopg.connect(database) as conn:return t.observe_committed_plan(conn,**args)


def test_actual_committed_exact_ledger_and_index_plan_not_semantic_approval(indexed,tmp_path):
    args=prepare(indexed,tmp_path)
    with psycopg.connect(indexed) as conn:
        write_fixture(conn,args['plan']);args['expected_after']=current(conn,args)
    bound=observe(indexed,args);value=json.loads((tmp_path/'observation.json').read_text())
    assert value['status']=='committed_plan_objects_continuity_only_not_admission'
    assert value['legacy_post_registry_fingerprint_sha256'] is None
    assert value['actual_transaction']['rows']==1 and not value['semantic_approval_granted']
    assert bound['file_sha256']==j.hashlib.sha256((tmp_path/'observation.json').read_bytes()).hexdigest()
    with psycopg.connect(indexed) as conn:
        explain=conn.execute('EXPLAIN (FORMAT JSON) SELECT * FROM wang_knowledge.object_versions WHERE change_set_id=%s',
                             (args['plan'].change_set_id,)).fetchone()
        assert 'integrity_object_versions_change_set_idx' in str(explain)


def test_uncommitted_same_connection_is_refused_before_any_receipt(indexed,tmp_path):
    args=prepare(indexed,tmp_path)
    with psycopg.connect(indexed) as conn:
        write_fixture(conn,args['plan']);args['expected_after']=current(conn,args)
        with pytest.raises(ValueError,match='idle'):t.observe_committed_plan(conn,**args)
        conn.rollback()
    assert not (tmp_path/'observation.json').exists()


def test_later_transaction_cannot_retrofit_applied_ledger(indexed,tmp_path):
    args=prepare(indexed,tmp_path)
    with psycopg.connect(indexed) as conn:
        write_fixture(conn,args['plan'],ledger=False);args['expected_after']=current(conn,args)
    with psycopg.connect(indexed) as conn:write_fixture(conn,args['plan'],objects=False)
    with pytest.raises(ValueError,match='exact objects transaction'):observe(indexed,args)
    assert not (tmp_path/'observation.json').exists()


def test_non_target_write_cannot_be_hidden_by_matching_KCS_ledger(indexed,tmp_path):
    args=prepare(indexed,tmp_path)
    with psycopg.connect(indexed) as conn:
        write_fixture(conn,args['plan']);write_fixture(conn,plan(),ledger=False)
        args['expected_after']=current(conn,args)
    with pytest.raises(ValueError,match='non-target'):observe(indexed,args)
    assert not (tmp_path/'observation.json').exists()


def test_missing_version_access_index_stops_before_fetching_any_H_rows(indexed,tmp_path,monkeypatch):
    args=prepare(indexed,tmp_path)
    with psycopg.connect(indexed) as conn:
        write_fixture(conn,args['plan']);args['expected_after']=current(conn,args)
    original=t._ledger_indexes
    # The observer's actual transaction is read-only, so use the raw helper
    # in an isolated write-capable fixture transaction for this catalog negative.
    with psycopg.connect(indexed) as conn,conn.cursor() as cursor:
        conn.execute('DROP INDEX wang_knowledge.integrity_object_versions_change_set_idx')
        with pytest.raises(ValueError,match='index absent'):original(cursor)
        conn.rollback()


@pytest.mark.parametrize('kind',['metadata','operation','version','kcs','current_payload'])
def test_exact_stored_authority_or_postimage_drift_refused(indexed,tmp_path,kind):
    args=prepare(indexed,tmp_path)
    with psycopg.connect(indexed) as conn:
        p=args['plan'];write_fixture(conn,p);args['expected_after']=current(conn,args)
        if kind=='metadata':conn.execute("UPDATE wang_knowledge.change_sets SET metadata='{\"drift\":true}' WHERE change_set_id=%s",(p.change_set_id,))
        elif kind=='kcs':conn.execute("UPDATE wang_knowledge.change_sets SET status='planned' WHERE change_set_id=%s",(p.change_set_id,))
        elif kind=='operation':conn.execute("UPDATE wang_knowledge.change_operations SET details='{\"drift\":true}' WHERE change_set_id=%s",(p.change_set_id,))
        elif kind=='version':conn.execute("UPDATE wang_knowledge.object_versions SET payload=payload||'{\"drift\":true}' WHERE change_set_id=%s",(p.change_set_id,))
        else:
            conn.execute("UPDATE wang_knowledge.objects SET payload=payload||'{\"drift\":true}' WHERE object_id=%s",(p.operations[0].object_id,))
    try:
        with pytest.raises((ValueError,ChangeSetConflict)):observe(indexed,args)
        assert not (tmp_path/'observation.json').exists()
    finally:
        if kind=='current_payload':
            with psycopg.connect(indexed) as conn:
                conn.execute('DELETE FROM wang_knowledge.objects WHERE object_id=%s',(p.operations[0].object_id,))


def test_current_ledger_update_hidden_by_first_RR_is_caught_in_second_snapshot(indexed,tmp_path,monkeypatch):
    args=prepare(indexed,tmp_path)
    with psycopg.connect(indexed) as conn:
        write_fixture(conn,args['plan']);args['expected_after']=current(conn,args)
    original=t._ledger_rows;calls=[]
    def rows(cursor,kcs):
        result=original(cursor,kcs)
        if not calls:
            with psycopg.connect(indexed) as other:
                other.execute('UPDATE wang_knowledge.change_sets SET metadata=metadata WHERE change_set_id=%s',(kcs,))
        calls.append(kcs);return result
    monkeypatch.setattr(t,'_ledger_rows',rows)
    with pytest.raises(ValueError,match='ledger changed'):observe(indexed,args)
    assert len(calls)==2 and not (tmp_path/'observation.json').exists()


def test_pinned_plan_drift_and_exact_signature_denominator_fail_before_SQL(indexed,tmp_path):
    args=prepare(indexed,tmp_path);args['expected_after']={}
    args['plan_sha256']='b'*64
    with pytest.raises(ValueError,match='plan content SHA'):observe(indexed,args)
    args['plan_sha256']=j.sha256_json(asdict(args['plan']))
    with pytest.raises(ValueError,match='denominator'):observe(indexed,args)
    assert not (tmp_path/'observation.json').exists()


def test_actual_transaction_observation_rows_do_not_grow_with_unrelated_N_or_version_H(indexed,tmp_path):
    from backend.tests.test_registry_integrity_baseline_postgres import insert
    class Cursor:
        def __init__(self,inner,metrics):self.inner=inner;self.metrics=metrics
        def __enter__(self):self.inner.__enter__();return self
        def __exit__(self,*a):return self.inner.__exit__(*a)
        def execute(self,query,params=None):
            if 'FROM wang_knowledge.objects' in query:
                assert 'WHERE collection=%s AND object_id=%s' in query
            if 'FROM wang_knowledge.object_versions' in query:
                assert 'WHERE change_set_id=%s' in query
            if 'FROM wang_knowledge.integrity_changes' in query:
                assert 'sequence>%s AND sequence<=%s' in query
            self.metrics['queries']+=1
            return self.inner.execute(query,params)
        def fetchall(self):
            rows=self.inner.fetchall();self.metrics['explicit_rows']+=len(rows);return rows
        def fetchone(self):
            row=self.inner.fetchone();self.metrics['explicit_rows']+=int(row is not None);return row
    class Connection:
        def __init__(self,inner,metrics):self.inner=inner;self.metrics=metrics
        @property
        def info(self):return self.inner.info
        def transaction(self):return self.inner.transaction()
        def cursor(self):return Cursor(self.inner.cursor(),self.metrics)
    results=[];ncount=0;hcount=0;kcs='offline-history-'+uuid.uuid4().hex;history_oid='history-'+uuid.uuid4().hex
    with psycopg.connect(indexed) as conn:
        conn.execute('INSERT INTO wang_knowledge.change_sets(change_set_id,fingerprint_sha256,package_id,source_kind,source_sha256,status) '
                     "VALUES (%s,%s,'old-offline-fixture','old-offline-fixture',%s,'applied')",
                     (kcs,uuid.uuid4().hex*2,'a'*64))
    for nscale,hscale in ((1,1),(10,10),(100,100)):
        with psycopg.connect(indexed) as conn:
            for _ in range(10*nscale-ncount):insert(conn)
            for revision in range(hcount+1,10*hscale+1):
                payload={'claim_id':history_oid,'revision':revision,'statement':'unrelated synthetic archived version'}
                conn.execute('INSERT INTO wang_knowledge.object_versions(collection,object_id,revision,content_sha256,payload,change_set_id) '
                             "VALUES ('claims',%s,%s,%s,%s,%s)",
                             (history_oid,revision,record_content_sha(payload),psycopg.types.json.Jsonb(payload),kcs))
            ncount=10*nscale;hcount=10*hscale
            conn.execute('ANALYZE wang_knowledge.object_versions')
        directory=tmp_path/str(nscale);directory.mkdir();args=prepare(indexed,directory)
        with psycopg.connect(indexed) as conn:
            write_fixture(conn,args['plan']);args['expected_after']=current(conn,args)
        metrics={'queries':0,'explicit_rows':0};start=time.perf_counter()
        with psycopg.connect(indexed) as conn:t.observe_committed_plan(Connection(conn,metrics),**args)
        metrics['seconds']=time.perf_counter()-start
        assert metrics['seconds']<5
        results.append((nscale,hscale,metrics))
        with psycopg.connect(indexed) as conn,conn.cursor() as cursor:
            t._indexed_snapshot(cursor,5)
            cursor.execute('EXPLAIN (FORMAT JSON) SELECT * FROM wang_knowledge.object_versions WHERE change_set_id=%s',
                           (args['plan'].change_set_id,))
            explain=cursor.fetchone()
            assert 'integrity_object_versions_change_set_idx' in str(explain)
    assert len({(r['queries'],r['explicit_rows']) for _,_,r in results})==1
    print('new-connection component observation N/H growth, NOT cold whole-flow acceptance:',results)


def test_actual_explain_veto_when_no_legal_enabled_index_path_exists(indexed):
    with psycopg.connect(indexed) as conn,conn.cursor() as raw:
        t._indexed_snapshot(raw,5)
        raw.execute('SET LOCAL enable_indexscan=off')
        raw.execute('SET LOCAL enable_indexonlyscan=off')
        raw.execute('SET LOCAL enable_bitmapscan=off')
        cursor=t._IndexedCursor(raw,lambda:5)
        # A non-sargable expression cannot use the declared exact key. Even
        # a full INDEX scan without Index Cond is an H scan, not a safe path.
        with pytest.raises(ValueError,match='full-scan'):
            cursor.execute("SELECT * FROM wang_knowledge.object_versions WHERE (change_set_id||'')=%s",('absent-fixture',))
    # Local settings must not leak to the next connection/session.
    with psycopg.connect(indexed) as conn:
        assert conn.execute('SHOW enable_seqscan').fetchone()==('on',)


def test_repeated_same_target_SQL_write_is_not_single_apply_observation(indexed,tmp_path):
    args=prepare(indexed,tmp_path)
    with psycopg.connect(indexed) as conn:
        write_fixture(conn,args['plan'])
        conn.execute('UPDATE wang_knowledge.objects SET payload=payload WHERE object_id=%s',
                     (args['plan'].operations[0].object_id,))
        args['expected_after']=current(conn,args)
    with pytest.raises(ValueError,match='write count'):observe(indexed,args)
    assert not (tmp_path/'observation.json').exists()
