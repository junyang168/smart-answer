from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import pytest
from backend.pipeline import claim_retained_occurrence_context as h
from backend.pipeline import claim_issue_management as m, source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as runner


@pytest.fixture
def actual():
    if os.environ.get('WKP357_REAL_SAVED_READONLY') != '1':
        pytest.skip('explicit actual SQL read-only opt-in')
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    load_dotenv('.env'); store = PostgresKnowledgeStore()
    cid = 'DK-72483dc200ad-CL002'; sid = 'SRC-2016_NYSC_4-07c73c44dc11'
    receipt = h.build(store, cid, sid)
    keys = [('claims', cid), ('source_documents', receipt['historical_source_pin']['object_id'])]
    records = store.read_affected_context(keys)
    states = {c.key(*k): r['payload'] for k,r in records.items() if not r['retired']}
    return store,cid,sid,receipt,states


@pytest.mark.parametrize('fault',[None,'claim','occurrence','source','pin','binding','foreign','duplicate'])
def test_actual_exact_receipt_and_counterexamples(actual,fault):
    store,cid,sid,receipt,states = actual
    receipt = deepcopy(receipt); states = deepcopy(states)
    if fault == 'claim':states['claims/'+cid]['statement'] += 'changed'
    if fault == 'occurrence':receipt['occurrences'] = []
    if fault == 'source':receipt['historical_source_pin']['object_id'] = sid
    if fault == 'pin':receipt['authority']['current_dependency_pins'][0]['pin'][0] += 1
    if fault == 'binding':receipt['authority']['bindings'][0]['file_sha256'] = '0'*64
    if fault == 'foreign':receipt['source_id'] = 'foreign'
    receipts = [receipt,receipt] if fault == 'duplicate' else [receipt]
    if fault is not None:
        with pytest.raises((ValueError,KeyError)):h.validate(receipts,states,sid,[cid])
    else:
        assert h.validate(receipts,states,sid,[cid]) == {cid}
        with pytest.raises(ValueError):c.claim_inventory(states,sid,[cid])
        inv=c.claim_inventory(states,sid,[cid],retained_occurrences=receipts)
        assert 'source_documents/'+receipt['historical_source_pin']['object_id'] in inv['context_keys']


def test_actual_manifest_full_freeze_zero_writes(actual,monkeypatch):
    store,cid,sid,receipt,states=actual
    run='CVPG-b0324087577d73360f4077b8d65438c7d777d27fda381f70913a8e1a367b0913'
    with store.connect() as conn:
        issues=[m.Issue.model_validate({k:v for k,v in r[0]['reports'][0].items() if k != 'run_id'}) for r in conn.execute(
            "SELECT metadata FROM wang_knowledge.pipeline_runs WHERE metadata->>'kind'='claim_issue'"
            " AND metadata->>'owner_run_id'=%s",(run,))]
    memory={};original=c.bound_bytes
    def write(path,body,**kw):
        memory[str(path)]=(json.dumps(runner.seal(body),ensure_ascii=False).encode());return runner.seal(body)
    def binding(path):return {'path':str(path),'file_sha256':hashlib.sha256(memory[str(path)]).hexdigest()}
    def read(b,snapshots=None):
        v=b.model_dump() if hasattr(b,'model_dump') else b
        return memory[v['path']] if v['path'] in memory else original(b,snapshots)
    monkeypatch.setattr(runner,'write',write);monkeypatch.setattr(runner,'binding',binding)
    monkeypatch.setattr(c,'bound_bytes',read)
    root=Path('/zero-write-preflight')
    policy={
        'path':str(Path('backend/pipeline/policies/wang_claim_issue_management_v1.json').resolve()),
        'file_sha256':hashlib.sha256(Path('backend/pipeline/policies/wang_claim_issue_management_v1.json').read_bytes()).hexdigest()}
    manifest=m.manifest_for_issues(store,run,issues,policy,root)
    for permission in manifest.permissions:
        if permission.collection=='claims' and permission.object_id==cid:
            assert 'occurrences' not in permission.fields
    context=json.loads(read(manifest.claim_issue))
    keys=[('claims',i.claim_id) for i in issues]+[('source_documents',receipt['historical_source_pin']['object_id'])]
    records=store.read_affected_context(keys)
    states={c.key(*k):r['payload'] for k,r in records.items() if not r['retired']}
    frozen=c.freeze(manifest,states)
    assert frozen['claim_issue_context']['retained_occurrence_context']==context['retained_occurrence_context']
    assert any(s['source_id']==receipt['historical_source_pin']['object_id'] for s in frozen['context_sources'])
    assert len(frozen['claim_issue_context']['claim_ids'])==2
    c.guard_files(frozen,historical=True)
