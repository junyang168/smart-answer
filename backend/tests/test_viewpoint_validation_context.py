from copy import deepcopy
import json

import pytest

from backend.pipeline.viewpoint_validation_context import (
    ValidationContext, validated_history, validation_phase, readonly,
    finish_validation_phase, RecordSnapshot,
    validation_runtime, read_current_objects,
)
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, call_model
from backend.api.canonical_repository.viewpoint_production_safety import file_sha256


def proof(path, calls):
    @validated_history
    def validate(expected):
        calls.append(expected)
        assert file_sha256(path) == expected
        return read_artifact(path)
    return validate


def test_one_parse_one_full_validator_shared_immutable_result(tmp_path):
    p = tmp_path/'proof.json';p.write_text('{"row":{"revision":1},"ids":["C"]}')
    calls = []; validate = proof(p, calls); pin = file_sha256(p)
    with ValidationContext('test') as ctx:
        a = validate(pin); b = validate(pin)
        assert a is b
        with pytest.raises(TypeError): a['row']['revision'] = 2
        with pytest.raises(TypeError): a['ids'].append('foreign')
        mutable = deepcopy(a); mutable['row']['revision'] = 2
        assert b['row']['revision'] == 1
    assert calls == [pin]
    assert ctx.metrics['json_parses'] == 1
    assert ctx.metrics['proof_runs'] == 1 and ctx.metrics['proof_reuses'] == 1


@pytest.mark.parametrize('change', ['content', 'deleted', 'replacement', 'symlink', 'new_receipt'])
def test_phase_boundary_rechecks_actual_bytes_and_negative_receipts(tmp_path, change):
    p = tmp_path/'proof.json';p.write_text('{"revision":1}')
    validate = proof(p, []);pin = file_sha256(p)
    with pytest.raises((ValueError, FileNotFoundError)):
        with ValidationContext('test'):
            validate(pin)
            if change == 'content':p.write_text('{"revision":2}')
            elif change == 'deleted':p.unlink()
            elif change == 'replacement':
                q=tmp_path/'replace';q.write_text('{"revision":2}');q.replace(p)
            elif change == 'symlink':
                q=tmp_path/'target';q.write_text('{"revision":1}');p.unlink();p.symlink_to(q)
            else:(tmp_path/'apply-receipt.json').write_text('{}')
            validate(pin)  # reuse never exempts the closing boundary


def test_changed_expected_pin_cannot_reuse_and_failures_are_not_cached(tmp_path):
    p=tmp_path/'proof.json';p.write_text('{}');calls=[];validate=proof(p,calls)
    with ValidationContext('test') as ctx:
        validate(file_sha256(p))
        for _ in range(2):
            with pytest.raises(AssertionError):validate('wrong')
        assert len(ctx.results) == 1
    assert len(calls) == 3


def test_record_simulation_copies_index_not_the_complete_registry():
    records=readonly({('claims','C'):{'revision':1,'anchors':[{'quote':'exact'}]}})
    assert isinstance(records, RecordSnapshot)
    simulation=deepcopy(records)
    assert simulation['claims','C'] is records['claims','C']
    with pytest.raises(TypeError): simulation['claims','C']['anchors'][0]['quote']='drift'
    simulation['claims','C']=deepcopy(simulation['claims','C'])
    simulation['claims','C']['revision']=2
    assert records['claims','C']['revision']==1


def test_no_state_crosses_model_or_next_validation_phase(tmp_path):
    p=tmp_path/'proof.json';p.write_text('{}');calls=[];validate=proof(p,calls);pin=file_sha256(p)
    with ValidationContext('test') as ctx:
        validate(pin);finish_validation_phase()
        assert ctx.closed and not ctx.results and not ctx.documents
        validate(pin)  # no memoization after boundary, like a fresh store gate
    with ValidationContext('next'):validate(pin)
    assert len(calls)==3


def test_drift_blocks_before_adapter_execution(tmp_path):
    p=tmp_path/'proof.json';p.write_text('{}');validate=proof(p,[])
    class ForbiddenAdapter:
        def request_encoding_proof(self, payload):
            pytest.fail('model adapter reached before historical drift detection')
    with pytest.raises(ValueError):
        with ValidationContext('test'):
            validate(file_sha256(p));p.write_text('{"changed":true}')
            call_model(ForbiddenAdapter(), {}, tmp_path/'raw.json')
    assert not (tmp_path/'raw.json').exists()


def test_phase_returns_only_after_validation_and_discards_on_error(tmp_path):
    p=tmp_path/'proof.json';p.write_text('{}');calls=[];validate=proof(p,calls);pin=file_sha256(p)
    @validation_phase
    def phase():
        validate(pin);validate(pin);return 'complete'
    assert phase()=='complete' and phase()=='complete'
    assert len(calls)==2


def test_live_checks_are_not_memoized(tmp_path):
    p=tmp_path/'proof.json';p.write_text('{"revision":1}')
    with ValidationContext('test'):
        assert read_artifact(p)['revision']==1
        p.write_text('{"revision":2}')
        assert read_artifact(p)['revision']==2
        assert file_sha256(p)==__import__('hashlib').sha256(p.read_bytes()).hexdigest()


def test_shared_runtime_reauthenticates_bytes_without_reparsing_or_revalidating(tmp_path):
    p=tmp_path/'proof.json';p.write_text('{"revision":1}');calls=[];validate=proof(p,calls);pin=file_sha256(p)
    with validation_runtime():
        with ValidationContext('prepare') as a: result=validate(pin)
        with ValidationContext('dryrun') as b: assert validate(pin) is result
        assert a.metrics['json_parses']==1 and b.metrics['json_parses']==0
        assert calls==[pin]
        p.write_text('{"revision":2}')
        with pytest.raises(ValueError):
            with ValidationContext('next'):validate(pin)


def test_new_receipt_between_phases_cannot_borrow_prior_proof(tmp_path):
    p=tmp_path/'proof.json';p.write_text('{}');validate=proof(p,[]);pin=file_sha256(p)
    with validation_runtime():
        with ValidationContext('first'):validate(pin)
        (tmp_path/'raw-opus.json').write_text('{}')
        with pytest.raises(ValueError):
            with ValidationContext('next'):validate(pin)


def test_full_dependency_closure_is_kept_for_nested_proofs(tmp_path):
    p=tmp_path/'proof.json';p.write_text('{}');inner=proof(p,[]);pin=file_sha256(p)
    @validated_history
    def outer():return inner(pin)
    with validation_runtime():
        with ValidationContext('first'):outer()
        p.write_text('{"changed":true}')
        with pytest.raises(ValueError):
            with ValidationContext('second'):outer()


def test_exact_current_readset_is_fresh_and_preserves_actual_sql_metadata():
    from contextlib import nullcontext
    class Store:
        revision=1
        reads=0
        def connect(self):return nullcontext(self)
        def execute(self, query, params=None):
            if query.startswith('SELECT'):
                self.reads+=1
                assert params==(['C'],)
            return self
        def fetchall(self):return [('claims','C',self.revision,'stored-sha',{'revision':1},'retired')]
    store=Store()
    with validation_runtime():
        with ValidationContext('first'):a=read_current_objects(store,{('claims','C')})
        store.revision=2
        with ValidationContext('second'):b=read_current_objects(store,{('claims','C')})
    assert store.reads==2 and a['claims','C']['revision']==1 and b['claims','C']['revision']==2
    assert b['claims','C']['content_sha256']=='stored-sha' and b['claims','C']['retired']


def test_exact_current_readset_rejects_missing_objects():
    from contextlib import nullcontext
    class Store:
        def connect(self):return nullcontext(self)
        def execute(self,*args):return self
        def fetchall(self):return []
    with pytest.raises(ValueError,match='readset incomplete'):
        read_current_objects(Store(),{('claims','C')})


@pytest.mark.parametrize('module', ['viewpoint_reviewed_cvp_prepare','viewpoint_batch_resolution_runner'])
def test_campaign_same_runtime_dispatch_keeps_cli_args_and_does_not_spawn(monkeypatch,tmp_path,module):
    from types import SimpleNamespace
    from backend.pipeline import viewpoint_cvp_campaign_runner as campaign
    p=tmp_path/'proof.json';p.write_text('{}');calls=[];validate=proof(p,calls);pin=file_sha256(p)
    received=[]
    @validation_phase
    def main(argv):
        received.append(argv);validate(pin);return 0
    monkeypatch.setattr('importlib.import_module', lambda name:SimpleNamespace(main=main))
    monkeypatch.setattr(campaign.subprocess,'run',lambda *a,**k:pytest.fail('CVP subprocess loses proof lifecycle'))
    flag='--output-root' if module.endswith('_prepare') else '--output-dir'
    (tmp_path/'out').mkdir()  # runtime output is not a new historical receipt
    argv=[campaign.PYTHON,'-m','backend.pipeline.'+module,flag,tmp_path/'out','--dry-run']
    with validation_runtime():
        campaign.command(argv);campaign.command(argv)
    assert received==[[flag,str(tmp_path/'out'),'--dry-run']]*2 and calls==[pin]


def test_unrecognized_command_still_uses_existing_subprocess(monkeypatch):
    from backend.pipeline import viewpoint_cvp_campaign_runner as campaign
    calls=[];monkeypatch.setattr(campaign.subprocess,'run',lambda argv,**kw:calls.append(argv))
    argv=[campaign.PYTHON,'-m','other_module']
    with validation_runtime():campaign.command(argv)
    assert calls==[[str(a) for a in argv]]


def test_phase_failure_does_not_leak_proofs_into_next_phase(tmp_path):
    p=tmp_path/'proof.json';p.write_text('{}');calls=[];validate=proof(p,calls);pin=file_sha256(p)
    with validation_runtime() as pool:
        with pytest.raises(RuntimeError):
            with ValidationContext('first'):
                validate(pin);raise RuntimeError('fail closed')
        assert not pool.results and not pool.documents
        with ValidationContext('second'):validate(pin)
    assert len(calls)==2


def test_default_and_explicit_arguments_share_the_same_history_identity(tmp_path):
    p=tmp_path/'proof.json';p.write_text('{}');calls=[]
    @validated_history
    def validate(path=p):
        calls.append(path)
        return read_artifact(path)
    with ValidationContext('defaults'):
        assert validate() is validate(p) is validate(path=p)
    assert calls==[p]


class GenerationStore:
    database_url='test-only-database-identity'
    def __init__(self):
        self.generation=1;self.reads=0;self.connections=[]
        self.payload={'revision':1,'text':'original'}
    def connect(self):
        from copy import deepcopy
        store=self
        class Connection:
            autocommit=False
            closed=False
            def __init__(self):
                self.generation=store.generation;self.payload=deepcopy(store.payload);self.queries=[]
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
            def execute(self,query,params=None):
                self.queries.append(query)
                if query.startswith('SELECT object_id'):
                    store.reads+=1
                    self.rows=[('C',1,'unchanged-stored-sha',self.payload,None)]
                return self
            def fetchall(self):return self.rows
            def rollback(self):pass
            def close(self):self.closed=True
        conn=Connection();self.connections.append(conn);return conn


def generation_signature(conn):
    return {'claims':('engine-epoch',conn.generation),'__change_sets__':('engine-epoch',1)}


@pytest.fixture(autouse=True)
def mock_clock_only_for_test_generation_connections(monkeypatch):
    from backend.pipeline import viewpoint_validation_context as module
    original=module.database_clock
    def clock(conn):
        if hasattr(conn,'generation'):
            assert conn.autocommit is True  # sampled before MVCC snapshot
            return conn.generation,'restart'
        return original(conn)
    monkeypatch.setattr(module,'database_clock',clock)


def test_sql_data_reuse_requires_fresh_engine_generation_but_verdicts_never_cross_phases(monkeypatch):
    from backend.pipeline import viewpoint_validation_context as module
    monkeypatch.setattr(module,'registry_signature',generation_signature)
    store=GenerationStore();checks=[]
    @module.current_check
    def check(store):
        checks.append(store.generation)
        return module.current_registry_view(store).list_records('claims')
    with validation_runtime():
        with ValidationContext('first'):
            assert check(store)==check(store)
        with ValidationContext('second'):
            assert check(store)[0]['text']=='original'
        assert store.reads==1 and checks==[1,1]
        # Application metadata lies: unchanged revision and stored SHA, actual
        # PostgreSQL UPDATE generation changes and forces a payload reload.
        store.generation=2;store.payload['text']='changed'
        with ValidationContext('third'):
            assert check(store)[0]['text']=='changed'
        assert store.reads==2 and checks==[1,1,2]
    assert all(c.closed for c in store.connections)


def test_current_findings_preserve_mutable_list_api_without_mutating_cached_verdict():
    from backend.pipeline.viewpoint_validation_context import current_check
    calls=[]
    @current_check
    def check():calls.append(True);return []
    with ValidationContext('findings'):
        findings=check();findings.extend(['another independent finding'])
        assert check()==[]
    assert len(calls)==1


@pytest.mark.parametrize('kind',['payload','retired','insert','delete','KCS','rewrite','restart','XID-wrap'])
def test_boundary_generation_drift_blocks_even_writers_ignoring_advisory_lock(monkeypatch,kind):
    from backend.pipeline import viewpoint_validation_context as module
    monkeypatch.setattr(module,'registry_signature',generation_signature)
    store=GenerationStore()
    with validation_runtime() as pool:
        with pytest.raises(ValueError,match='Registry changed'):
            with ValidationContext(kind):
                module.current_registry_view(store).list_records('claims')
                store.generation+=1
        assert not pool.registry_data and not pool.registry_cuts
    assert all(c.closed for c in store.connections)


def test_model_boundary_closes_sql_view_and_discards_current_verdicts(monkeypatch):
    from backend.pipeline import viewpoint_validation_context as module
    monkeypatch.setattr(module,'registry_signature',generation_signature)
    store=GenerationStore()
    with validation_runtime():
        with ValidationContext('before-model') as ctx:
            module.current_registry_view(store).list_records('claims')
            ctx.current_results['verdict']=True
            finish_validation_phase()
            assert ctx.closed and not ctx.current_results
            assert all(c.closed for c in store.connections)


def test_single_writer_entry_uses_readonly_snapshot_without_global_read_lock(monkeypatch):
    from backend.pipeline import viewpoint_validation_context as module
    monkeypatch.setattr(module,'registry_signature',generation_signature)
    store=GenerationStore()
    with ValidationContext('order'):module.current_registry_view(store)
    q=store.connections[0].queries
    assert 'REPEATABLE READ READ ONLY' in q[0]
    assert all('advisory' not in query for query in q)


def test_global_canonical_cut_value_is_unchanged_and_cached_data_is_immutable(monkeypatch):
    from backend.pipeline import viewpoint_validation_context as module
    from backend.api.canonical_repository.viewpoint_foundation import sha256_json
    monkeypatch.setattr(module,'registry_signature',generation_signature)
    store=GenerationStore()
    with validation_runtime():
        with ValidationContext('cut'):
            view=module.current_registry_view(store)
            assert view.canonical_cut('claims')==[sha256_json(store.payload)]
            with pytest.raises(TypeError):view.list_records('claims')[0]['text']='tampered'
        store.generation=2;store.payload['text']='new'
        with ValidationContext('fresh-cut'):
            assert module.current_registry_view(store).canonical_cut('claims')==[sha256_json(store.payload)]


def test_current_file_pin_rechecked_without_freezing_runtime_output_directory(tmp_path):
    p=tmp_path/'input.json';p.write_text('{}')
    with ValidationContext('runtime'):
        file_sha256(p)
        (tmp_path/'result.json').write_text('{}')  # ordinary runtime output is not a historical negative receipt
    with pytest.raises(ValueError,match='input drift'):
        with ValidationContext('input'):
            file_sha256(p);p.write_text('{"changed":true}')


def test_engine_signature_reads_mvcc_generation_and_storage_epoch_not_self_reported_payload_sha():
    from backend.pipeline.viewpoint_validation_context import registry_signature
    class Connection:
        def __init__(self):self.queries=[]
        def execute(self,query):self.queries.append(query);return self
        def __iter__(self):return iter([('claims','generation')])
        def fetchone(self):
            return ('restart','wrap','objects-node','KCS-node') if len(self.queries)==3 else ('KCS-generation',)
    conn=Connection();result=registry_signature(conn)
    assert result['claims']==(('restart','wrap','objects-node','KCS-node'),'generation')
    assert all('xmin::text' in q and 'ctid::text' in q for q in conn.queries[:2])
    assert 'payload' not in conn.queries[0] and 'pg_relation_filenode' in conn.queries[2]


def test_typed_models_are_parsed_once_per_generation_but_selected_copies_are_isolated(monkeypatch):
    from pydantic import BaseModel
    from backend.pipeline import viewpoint_validation_context as module
    monkeypatch.setattr(module,'registry_signature',generation_signature)
    class Row(BaseModel):
        revision:int
        text:str
    store=GenerationStore()
    with validation_runtime():
        with ValidationContext('first'):
            a=module.typed_records(store,'claims',Row,'text')
            a['original'].text='consumer-only change'
            assert a['original'].text=='original'
        with ValidationContext('second'):
            assert module.typed_records(store,'claims',Row,'text') is a
        store.generation=2;store.payload['text']='new'
        with ValidationContext('third'):
            b=module.typed_records(store,'claims',Row,'text')
            assert b is not a and b['new'].text=='new'


def test_typed_subset_still_validates_unselected_malformed_records():
    from pydantic import BaseModel,ValidationError
    from backend.pipeline.viewpoint_validation_context import TypedRecords
    class Row(BaseModel):
        oid:str
        required:int
    with pytest.raises(ValidationError):
        TypedRecords([{'oid':'selected','required':1},{'oid':'unselected','required':'bad'}],Row,'oid')


def test_scoped_graph_retains_orphan_and_duplicate_reverse_evidence_edges():
    from backend.pipeline.viewpoint_validation_context import TypedRecords
    from backend.api.canonical_repository.knowledge_models import EvidenceStepRecord,ClaimRecord
    from backend.api.canonical_repository.viewpoint_resolution import claim_evidence_integrity_findings
    rows=[{'evidence_step_id':'E1','produced_claim_ids':['C']},
          {'evidence_step_id':'orphan','produced_claim_ids':['C','C']},
          {'evidence_step_id':'unrelated','produced_claim_ids':['elsewhere']}]
    index=TypedRecords(rows,EvidenceStepRecord,'evidence_step_id')
    selected=index.reciprocal_subset({'C'},{'E1'})
    assert set(selected)=={'E1','orphan'}
    claims={'C':ClaimRecord(claim_id='C',statement='unchanged',claim_type='teaching',evidence_step_ids=['E1'])}
    partial=claim_evidence_integrity_findings(claim_ids=['C'],claims=claims,evidence_steps=selected)
    full=claim_evidence_integrity_findings(claim_ids=['C'],claims=claims,evidence_steps=dict(index))
    assert partial==full and any('EvidenceStep-only' in finding for finding in partial)
    assert any('duplicate' in finding for finding in partial)


def test_unchanged_wal_skips_full_library_cut_but_rechecks_clock_and_current_verdict(monkeypatch):
    from backend.pipeline import viewpoint_validation_context as module
    cuts=[];checks=[];store=GenerationStore()
    def signature(conn):cuts.append(conn.generation);return generation_signature(conn)
    monkeypatch.setattr(module,'registry_signature',signature)
    @module.current_check
    def check(store):checks.append(True);return module.current_registry_view(store).list_records('claims')
    with validation_runtime():
        with ValidationContext('first'):check(store)
        with ValidationContext('second'):check(store)
    assert len(cuts)==1 and len(checks)==2
    assert len(store.connections)==4  # entry and exit clocks on fresh connections


def test_changed_wal_with_unchanged_corpus_reauthenticates_without_discarding_data(monkeypatch):
    from backend.pipeline import viewpoint_validation_context as module
    store=GenerationStore();clock=[1];cuts=[]
    monkeypatch.setattr(module,'database_clock',lambda conn:(clock[0],'restart'))
    def signature(conn):cuts.append(True);return generation_signature(conn)
    monkeypatch.setattr(module,'registry_signature',signature)
    with validation_runtime():
        with ValidationContext('first'):module.current_registry_view(store).list_records('claims')
        clock[0]=2  # unrelated WAL activity, not an authority to reuse a pass
        with ValidationContext('second'):module.current_registry_view(store).list_records('claims')
    assert len(cuts)==2 and store.reads==1


def test_unlogged_registry_cannot_use_fast_clock():
    from backend.pipeline.viewpoint_validation_context import database_clock
    class Connection:
        def execute(self,query):return self
        def fetchone(self):return ('lsn','restart',False)
    with pytest.raises(ValueError,match='WAL logged'):database_clock(Connection())
