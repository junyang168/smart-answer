"""Exact-ID mechanical reads: no all-library query, provider, or mutation."""
from copy import deepcopy
import pytest

from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline import viewpoint_validation_context as m


def row(revision=1, *, retired=None, xmin='10', ctid='(0,1)', hold=None):
    payload={'claim_id':'C','revision':revision,'statement':'original'}
    if hold:payload['review_status']=hold
    return ('claims','C',revision,record_content_sha(payload),retired,xmin,ctid,payload)


class Store:
    database_url='pytest-only-scope'
    def __init__(self):
        self.rows={('claims','C'):row()}
        self.epoch=('cluster-start','0',123)
        self.queries=[];self.connections=[]
        self.unrelated_records=1
    def connect(self):
        connection=Connection(self);self.connections.append(connection);return connection


class Connection:
    def __init__(self,store):
        self.store=store;self.snapshot=deepcopy(store.rows);self.epoch=store.epoch
        self.rows=[];self.closed=False
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def rollback(self):pass
    def close(self):self.closed=True
    def execute(self,query,params=None):
        self.store.queries.append((query,params))
        if query.startswith('SET '):return self
        if query.startswith('SELECT pg_postmaster_start_time'):
            self.one=self.epoch;return self
        assert 'JOIN unnest(%s::text[],%s::text[])' in query
        assert 'o.collection=wanted.collection AND o.object_id=wanted.object_id' in query
        probes=list(zip(*params))
        self.rows=[self.snapshot[key] if ',o.payload' in query else self.snapshot[key][:-1]
                   for key in probes if key in self.snapshot]
        return self
    def fetchall(self):return self.rows
    def fetchone(self):return self.one


@pytest.fixture(autouse=True)
def no_global_cut(monkeypatch):
    monkeypatch.setattr(m,'registry_signature',lambda *_:pytest.fail('exact ID reads must not scan global generations'))
    monkeypatch.setattr(m,'database_clock',lambda *_:pytest.fail('exact ID reads must not depend on unrelated WAL'))


@pytest.mark.parametrize('scale',[1,10,100])
def test_cold_fixed_readset_query_and_row_count_independent_of_unrelated_growth(scale):
    store=Store();store.unrelated_records=234787*scale
    with m.validation_runtime(limits=m.ROUTINE_VALIDATION_LIMITS):
        with m.ValidationContext('exact readset') as ctx:
            first=m.read_current_objects(store,{('claims','C')})
            assert m.read_current_objects(store,{('claims','C')})==first
            assert first['claims','C']['revision']==1
            with pytest.raises(TypeError):first['claims','C']['payload']['statement']='unreviewed'
            store.unrelated_records+=100  # no relation to current identity approval
        assert ctx.metrics['sql_payload_rows']==1 and ctx.metrics['sql_version_rows']==1
    assert len([query for query,_ in store.queries if 'JOIN unnest' in query])==2
    assert all(conn.closed for conn in store.connections)
    assert any('SET LOCAL statement_timeout' in query for query,_ in store.queries)


@pytest.mark.parametrize('change',['payload_only','revision','stored_sha','retire','delete','reinsert'])
def test_phase_boundary_detects_actual_target_drift_even_same_stored_content_sha(change):
    store=Store()
    with pytest.raises(ValueError,match='changed during|incomplete'):
        with m.ValidationContext('mechanical phase'):
            m.read_current_objects(store,{('claims','C')})
            current=list(store.rows['claims','C'])
            if change=='delete':del store.rows['claims','C'];return
            if change=='payload_only':current[-1]['statement']='changed';current[5]='11'
            elif change=='revision':current[2]=2
            elif change=='stored_sha':current[3]='different'
            elif change=='retire':current[4]='retired-timestamp'
            elif change=='reinsert':current[5]='12';current[6]='(0,9)'
            store.rows['claims','C']=tuple(current)
    assert all(conn.closed for conn in store.connections)


@pytest.mark.parametrize('epoch',[('new-start','0',123),('cluster-start','1',123),('cluster-start','0',456)])
def test_cluster_restart_xid_wrap_and_relation_rewrite_invalidate_scope(epoch):
    store=Store()
    with pytest.raises(ValueError,match='storage epoch changed'):
        with m.ValidationContext('mechanical phase'):
            m.read_current_objects(store,{('claims','C')});store.epoch=epoch


@pytest.mark.parametrize('bad',['missing','revision','sha'])
def test_initial_sql_metadata_is_verified_and_never_filled_from_old_snapshot(bad):
    store=Store()
    if bad=='missing':store.rows.clear()
    else:
        value=list(store.rows['claims','C']);value[2 if bad=='revision' else 3]='invalid'
        store.rows['claims','C']=tuple(value)
    with pytest.raises(ValueError,match='incomplete|revision/content SHA drift'):
        with m.ValidationContext('mechanical phase'):
            m.read_current_objects(store,{('claims','C')})


def test_next_phase_has_fresh_current_data_not_reusable_approval_or_pool_rows():
    store=Store()
    with m.validation_runtime() as pool:
        with m.ValidationContext('before'):
            before=m.read_current_objects(store,{('claims','C')})
        store.rows['claims','C']=row(2,xmin='11')
        with m.ValidationContext('after'):
            after=m.read_current_objects(store,{('claims','C')})
        assert not pool.registry_data and not pool.registry_generations
    assert before['claims','C']['revision']==1 and after['claims','C']['revision']==2


def test_retirement_and_human_hold_metadata_are_not_hidden_or_upgraded():
    store=Store();store.rows['claims','C']=row(retired='timestamp',hold='human_judgment_required')
    with m.ValidationContext('read-only evidence'):
        observed=m.read_current_objects(store,{('claims','C')})
    assert observed['claims','C']['retired'] is True
    assert observed['claims','C']['payload']['review_status']=='human_judgment_required'


def test_foreign_collection_same_id_is_not_returned_instead_of_requested_record():
    store=Store();value=list(store.rows.pop(('claims','C')));value[0]='foreign'
    store.rows['foreign','C']=tuple(value)
    with pytest.raises(ValueError,match='incomplete'):
        with m.ValidationContext('exact collection'):
            m.read_current_objects(store,{('claims','C')})
