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
            assert 'occurrences' in permission.fields
    context=json.loads(read(manifest.claim_issue))
    keys=[('claims',i.claim_id) for i in issues]+[('source_documents',receipt['historical_source_pin']['object_id'])]
    records=store.read_affected_context(keys)
    states={c.key(*k):r['payload'] for k,r in records.items() if not r['retired']}
    frozen=c.freeze(manifest,states)
    assert frozen['claim_issue_context']['retained_occurrence_context']==context['retained_occurrence_context']
    assert any(s['source_id']==receipt['historical_source_pin']['object_id'] for s in frozen['context_sources'])
    assert len(frozen['claim_issue_context']['claim_ids'])==2
    c.guard_files(frozen,historical=True)


@pytest.mark.parametrize('fault', [None, 'historical', 'membership', 'identity', 'receipt'])
def test_current_anchor_repair_preserves_historical_occurrence(actual, fault):
    _,cid,sid,receipt,states=actual
    old=states['claims/'+cid];new=deepcopy(old)
    own=next(o for o in new['occurrences'] if o['source_id']==sid)
    own['anchors'].append(deepcopy(own['anchors'][0]))
    frozen={'manifest':{'source_id':sid},'before_records':c.rows(states),
            'claim_issue_context':{'retained_occurrence_context':[deepcopy(receipt)]}}
    if fault=='historical':new['occurrences'][0]['anchors'][0]['media_time']=99
    if fault=='membership':new['occurrences'].reverse()
    if fault=='identity':own['source_id']='foreign'
    if fault=='receipt':frozen['claim_issue_context']['retained_occurrence_context'][0]['occurrences']=[]
    if fault:
        with pytest.raises(ValueError):h.validate_occurrence_delta(frozen,cid,old,new)
    else:
        h.validate_occurrence_delta(frozen,cid,old,new)
        assert new['occurrences'][0]==old['occurrences'][0]


def test_new_review_discloses_route_receipt_without_upgrading_old_cache(actual,tmp_path):
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-b0324087577d73360f4077b8d65438c7d777d27fda381f70913a8e1a367b0913/claim-issues')
    action=next(root.glob('*/action/*/proposal.raw.json')).parent
    frozen=runner.body(runner.read(action/'scoped-materials.json'))
    raw=runner.read(action/'proposal.raw.json')
    proposal=runner.effective_proposal(frozen,raw['response'])
    obj=runner.Runner(tmp_path,None);obj.root=tmp_path
    fresh=obj.review_input(frozen,raw['response'],proposal,'initial_review')
    assert fresh['inventory']['unknown_keys']==[]
    assert fresh['read_only_adapter_receipt']['content_write_authority'] is False
    runner.write(tmp_path/'initial_review.packet.json',fresh)
    assert obj.review_input(frozen,raw['response'],proposal,'initial_review')==fresh
    obj.root=action
    saved=runner.body(runner.read(action/'initial_review.packet.json'))
    assert 'read_only_adapter_receipt' not in saved
    assert obj.review_input(frozen,raw['response'],proposal,'initial_review')==saved


def test_saved_unresolved_correction_cannot_gain_a_new_call_or_write(actual, monkeypatch):
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-b0324087577d73360f4077b8d65438c7d777d27fda381f70913a8e1a367b0913/claim-issues')
    action=next(root.glob('*/action/*/correction.raw.json')).parent
    frozen=runner.body(runner.read(action/'scoped-materials.json'))
    files={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in action.iterdir() if p.is_file()}
    reads=[]
    class SavedOnly(runner.Runner):
        def __init__(self):self.root=action
        def guard(self,frozen,**kw):c.guard_files(frozen)
        def call(self,stage,packet):
            assert stage in {'proposal','initial_review','correction'}, 'no new model stage authorized'
            saved=runner.body(runner.read(action/(stage+'.packet.json')))
            assert packet==saved
            reads.append(stage)
            return runner.validate_raw(action,stage,saved)
    def no_write(*args,**kw):raise AssertionError('saved-only diagnosis cannot write')
    monkeypatch.setattr(runner,'write',no_write)
    with pytest.raises(ValueError,match='exactly one disposition'):
        SavedOnly().reviewed(frozen)
    assert reads==['proposal','initial_review','correction']
    assert not (action/'final_review.reservation.json').exists()
    assert not (action/'final_review.raw.json').exists()
    assert files=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in action.iterdir() if p.is_file()}


@pytest.mark.parametrize('fault',[None,'pin','schema','hold','foreign','visibility','missing'])
def test_saved_internal_route_is_impact_only_not_generation(actual,fault):
    from backend.pipeline.source_reconciliation_runner import read,build_proposal_packet
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-b0324087577d73360f4077b8d65438c7d777d27fda381f70913a8e1a367b0913/claim-issues')
    frozen=deepcopy(read(next(root.glob('*/action/*/scoped-materials.json'))))
    key='knowledge_routes/ROUTE-78a6d9404481'
    assert frozen['inventory']['unknown_keys']==[key]
    old=deepcopy(frozen)
    route=next(r['payload'] for r in frozen['before_records'] if r['collection']=='knowledge_routes' and r['object_id']=='ROUTE-78a6d9404481')
    if fault=='pin':next(r['pin'] for r in frozen['sql_scope']['read_pins'] if r['collection']=='knowledge_routes')[0]+=1
    if fault=='schema':route['route_type']='not-a-route'
    if fault=='hold':route['review_status']='human_review_required'
    if fault=='visibility':route['visibility']='public'
    if fault=='missing':frozen['inventory']['affected_keys'].remove(key)
    if fault=='foreign':frozen['inventory']['unknown_keys'].append('foreign/X')
    if fault=='foreign':assert c.unresolved_consumers(frozen)==['foreign/X']
    elif fault:
        with pytest.raises(ValueError):c.unresolved_consumers(frozen)
    else:
        packet=build_proposal_packet(frozen)
        assert packet['inventory']['unknown_keys']==[]
        assert packet['read_only_adapter_receipt']['content_write_authority'] is False
        assert frozen==old
        assert not any(p['collection']=='knowledge_routes' for p in frozen['manifest']['permissions'])


@pytest.mark.parametrize('fault', [None, 'permission', 'pin', 'hold', 'proposed_pending',
                                  'source_pending', 'source_uncertain', 'unknown', 'epoch', 'no_sql'])
def test_saved_initial_draft_review_never_write_authority(actual, fault, tmp_path):
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-b0324087577d73360f4077b8d65438c7d777d27fda381f70913a8e1a367b0913/claim-issues')
    action=next(root.glob('*/action/*/proposal.raw.json')).parent
    frozen=deepcopy(runner.read(action/'scoped-materials.json'))
    raw=runner.read(action/'proposal.raw.json')
    proposal=runner.effective_proposal(frozen,raw['response'])
    original=deepcopy(frozen); saved=deepcopy(raw)
    pending=next(d for d in proposal.dispositions if d.action=='unresolved')
    col,ident=c.split_key(pending.record_key)
    row=next(r for r in frozen['before_records'] if r['collection']==col and r['object_id']==ident)
    if fault=='permission':frozen['manifest']['permissions']=[]
    if fault=='pin':next(r['pin'] for r in frozen['sql_scope']['read_pins'] if r['collection']==col and r['object_id']==ident)[0]+=1
    if fault=='hold':row['payload']['review_status']='human_review_required'
    if fault=='proposed_pending':proposal.records.append(c.Row(collection=col,object_id=ident,payload=deepcopy(row['payload'])))
    if fault=='source_pending':next(d for d in proposal.dispositions if d.record_key.startswith('claims/')).action='unresolved'
    if fault=='source_uncertain':proposal.claim_assessments[0].cause='source_unresolved'
    if fault=='unknown':frozen['inventory']['unknown_keys'].append('foreign/X')
    if fault=='epoch':frozen['after'][0]['body_sha256']='0'*64
    if fault=='no_sql':frozen.pop('sql_scope')
    if fault:
        with pytest.raises((ValueError,KeyError)):
            runner.initial_review_base(frozen,proposal)
        return
    packet=runner.initial_review_base(frozen,proposal)
    receipt=packet['unresolved_proposal_review']
    assert receipt['schema_version']=='claim_initial_draft_review.v1'
    assert receipt['content_write_authority'] is False
    assert receipt['mandatory_original_unique_correction'] is True
    assert len(receipt['program_findings'])==3
    assert frozen==original and raw==saved
    # Even an all-approved draft review must use the original unique correction.
    response={'proposal_sha256':packet['proposal_sha256'],
              'verdicts':[{'record_key':d.record_key,'approved':True,'problems':[],
                           'reason':'draft only'} for d in proposal.dispositions]}
    assert c.validate_review(packet,response) is False
    with pytest.raises(ValueError):c.review_packet(frozen,proposal)
    with pytest.raises(ValueError):c.compile_package(frozen,proposal,{'response':response})
    calls=[]
    class Stages(runner.Runner):
        def __init__(self):self.root=tmp_path
        def guard(self,*args,**kw):pass  # Test-only; actual SQL guard is independently exercised.
        def call(self,stage,packet):
            calls.append(stage)
            if stage=='initial_review':
                return {'response':{'proposal_sha256':packet['proposal_sha256'],
                    'verdicts':[{'record_key':d['record_key'],'approved':True,
                                 'problems':[],'reason':'draft only'}
                                for d in packet['proposal']['dispositions']]}}
            assert stage in {'proposal','correction'}
            return deepcopy(raw)  # Unchanged failing correction cannot open a final review.
    with pytest.raises(ValueError):Stages().reviewed(frozen)
    assert calls==['proposal','initial_review','correction']
