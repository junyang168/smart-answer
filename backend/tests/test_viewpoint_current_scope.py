"""Complete indexed selected graph; no whole collection or old snapshot."""
from copy import deepcopy
import json
import pytest

from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.api.canonical_repository.viewpoint_resolution import claim_evidence_integrity_findings
from backend.pipeline import viewpoint_validation_context as v
from backend.pipeline.viewpoint_current_scope import read_current_scope


def row(col, oid, payload, *, retired=None, xmin='1'):
    payload={'revision':1, **payload}
    return col,oid,payload['revision'],record_content_sha(payload),retired,xmin,'(0,1)',payload


class Store:
    database_url='offline-indexed-scope'
    def __init__(self):
        self.rows={}
        self.epoch=('start','0',123)
        self.queries=[]
        self.put('claims','C',{'claim_id':'C','source_id':'S','statement':'original','claim_type':'interpretive',
            'review_status':'approved','evidence_step_ids':['E']})
        self.put('evidence_steps','E',{'evidence_step_id':'E','source_id':'S','source_fragment_id':'F',
            'produced_claim_ids':['C'],'support_eligibility':'withheld_unreviewed'})
        self.put('source_fragments','F',{'fragment_id':'F','source_id':'S','verbatim_excerpt':'verbatim',
            'source_sha256':'body-sha'})
        self.put('source_documents','S',{'source_id':'S','source_sha256':'body-sha','review_status':'human_judgment_required'})
    def put(self,col,oid,payload,**kwargs):self.rows[col,oid]=row(col,oid,payload,**kwargs)
    def connect(self):return Connection(self)
    def list_records(self,*_):pytest.fail('all-collection read forbidden')


class Connection:
    def __init__(self,store):
        self.store=store;self.snapshot=deepcopy(store.rows);self.epoch=store.epoch;self.closed=False
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
    def close(self):self.closed=True
    def rollback(self):pass
    def execute(self,query,params=None):
        self.store.queries.append((query,params))
        if query.startswith('SET '):return self
        if query.startswith('SELECT pg_postmaster_start_time'):self.one=self.epoch;return self
        if 'payload @>' in query:
            assert params[0]=='evidence_steps' and 'retired_at IS NULL' in query
            assert 'LIMIT' not in query
            array=json.loads(params[1])['produced_claim_ids'];scalar=json.loads(params[2])['produced_claim_ids']
            assert array==[scalar]
            self.result=[(col,oid) for (col,oid),r in self.snapshot.items() if col=='evidence_steps'
                and r[4] is None and (r[-1].get('produced_claim_ids')==scalar or
                    isinstance(r[-1].get('produced_claim_ids'),list) and scalar in r[-1]['produced_claim_ids'])]
            return self
        assert 'JOIN unnest' in query
        keys=list(zip(*params))
        self.result=[self.snapshot[k] if ',o.payload' in query else self.snapshot[k][:-1]
            for k in keys if k in self.snapshot]
        return self
    def fetchone(self):return self.one
    def fetchall(self):return self.result


@pytest.fixture(autouse=True)
def forbid_global_cut(monkeypatch):
    monkeypatch.setattr(v,'registry_signature',lambda *_:pytest.fail('no global MVCC scan'))
    monkeypatch.setattr(v,'database_clock',lambda *_:pytest.fail('no unrelated WAL clock'))


@pytest.mark.parametrize('scale',[1,10,100])
def test_complete_current_graph_keeps_held_evidence_and_source_and_fixed_readset(scale):
    store=Store();store.unrelated_population=234787*scale
    with v.validation_runtime(limits=v.ROUTINE_VALIDATION_LIMITS):
        with v.ValidationContext('indexed graph') as ctx:
            claims,evidence,fragments,sources=read_current_scope(store,['C'])
            assert list(claims)==['C'] and set(evidence)=={'E'} and set(fragments)=={'F'} and set(sources)=={'S'}
            assert evidence['E'].support_eligibility=='withheld_unreviewed'
            assert sources['S']['review_status']=='human_judgment_required'
            assert not claim_evidence_integrity_findings(claim_ids=['C'],claims=claims,evidence_steps=evidence)
        assert ctx.metrics['sql_payload_rows']==ctx.metrics['sql_version_rows']==4
        assert ctx.metrics['sql_membership_rows']==2
    assert len([q for q,_ in store.queries if 'payload @>' in q])==2


def test_orphan_reverse_edge_is_loaded_and_reported_not_silently_filtered():
    store=Store();store.put('evidence_steps','ORPHAN',{'evidence_step_id':'ORPHAN','produced_claim_ids':['C']})
    with v.ValidationContext('reverse integrity'):
        claims,evidence,*_=read_current_scope(store,['C'])
        assert set(evidence)=={'E','ORPHAN'}
        assert 'C/ORPHAN: non-reciprocal EvidenceStep-only binding' in claim_evidence_integrity_findings(
            claim_ids=['C'],claims=claims,evidence_steps=evidence)


def test_duplicate_reverse_binding_preserved_for_original_integrity_finding():
    store=Store();store.put('evidence_steps','E',{**store.rows['evidence_steps','E'][-1],'produced_claim_ids':['C','C']})
    with v.ValidationContext('duplicate graph'):
        claims,evidence,*_=read_current_scope(store,['C'])
        assert 'C/E: duplicate produced_claim_ids binding' in claim_evidence_integrity_findings(
            claim_ids=['C'],claims=claims,evidence_steps=evidence)


def test_forward_edge_without_matching_reverse_pointer_is_not_dropped():
    store=Store();store.put('evidence_steps','E',{**store.rows['evidence_steps','E'][-1],'produced_claim_ids':['OTHER']})
    with v.ValidationContext('Claim-only binding'):
        claims,evidence,*_=read_current_scope(store,['C'])
        assert set(evidence)=={'E'}
        assert 'C/E: non-reciprocal Claim-only evidence binding' in claim_evidence_integrity_findings(
            claim_ids=['C'],claims=claims,evidence_steps=evidence)


def test_invalid_scalar_reverse_edge_is_not_hidden_by_array_only_discovery():
    store=Store();store.put('evidence_steps','BAD',{'evidence_step_id':'BAD','produced_claim_ids':'C'})
    with pytest.raises(ValueError):
        with v.ValidationContext('invalid reverse edge'):read_current_scope(store,['C'])


@pytest.mark.parametrize('change',['new_orphan','unrelated_becomes_edge','retired_becomes_active'])
def test_fresh_predicate_recheck_detects_edges_not_present_in_original_id_readset(change):
    store=Store()
    if change=='unrelated_becomes_edge':store.put('evidence_steps','NEW',{'evidence_step_id':'NEW','produced_claim_ids':['OTHER']})
    if change=='retired_becomes_active':store.put('evidence_steps','NEW',{'evidence_step_id':'NEW','produced_claim_ids':['C']},retired='retired')
    with pytest.raises(ValueError,match='reverse Evidence population changed'):
        with v.ValidationContext('predicate phantom'):
            read_current_scope(store,['C'])
            store.put('evidence_steps','NEW',{'evidence_step_id':'NEW','produced_claim_ids':['C']},xmin='2')


def test_initial_empty_reverse_population_is_rechecked_for_new_edges():
    store=Store();store.put('claims','C',{**store.rows['claims','C'][-1],'evidence_step_ids':[]})
    del store.rows['evidence_steps','E']
    with pytest.raises(ValueError,match='reverse Evidence population changed'):
        with v.ValidationContext('empty reverse population'):
            _,evidence,*_=read_current_scope(store,['C']);assert not evidence
            store.put('evidence_steps','NEW',{'evidence_step_id':'NEW','produced_claim_ids':['C']},xmin='2')


@pytest.mark.parametrize('bad',['missing_forward','retired_fragment','wrong_source_identity'])
def test_missing_retired_foreign_dependencies_stop_without_historical_fallback(bad):
    store=Store()
    if bad=='missing_forward':del store.rows['evidence_steps','E']
    elif bad=='retired_fragment':store.put('source_fragments','F',store.rows['source_fragments','F'][-1],retired='retired')
    else:store.put('source_documents','S',{'source_id':'FOREIGN','source_sha256':'body-sha'})
    with pytest.raises(ValueError):
        with v.ValidationContext('invalid dependency'):read_current_scope(store,['C'])


def test_singular_plural_fragment_union_complete_and_current_source_sha_authoritative():
    store=Store();store.put('source_fragments','F2',{'fragment_id':'F2','source_id':'S','verbatim_excerpt':'second',
        'source_sha256':'stale-sha'})
    store.put('evidence_steps','E',{**store.rows['evidence_steps','E'][-1],'source_fragment_ids':['F2','F']})
    with v.ValidationContext('fragment population'):
        _,_,fragments,sources=read_current_scope(store,['C'])
        assert set(fragments)=={'F','F2'}
        assert sources['S']['source_sha256']=='body-sha' and fragments['F2'].source_sha256=='stale-sha'
        # Consumer must still reject stale fragment; the loader cannot upgrade it.


def test_legacy_claim_without_source_field_uses_only_its_bound_fragments_no_claim_edit():
    store=Store();payload={k:value for k,value in store.rows['claims','C'][-1].items() if k!='source_id'}
    store.put('claims','C',payload)
    before=deepcopy(store.rows)
    with v.ValidationContext('legacy Claim source identity'):
        claims,_,_,sources=read_current_scope(store,['C'])
        assert not hasattr(claims['C'],'source_id') and set(sources)=={'S'}
    assert store.rows==before
