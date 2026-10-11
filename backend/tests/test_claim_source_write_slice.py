from copy import deepcopy
import os
from pathlib import Path
import pytest
from backend.pipeline import claim_source_write_slice as m
from backend.pipeline import source_reconciliation as c


def fixture(monkeypatch):
    from backend.pipeline import claim_evidence_completion
    monkeypatch.setattr(claim_evidence_completion,'direction_for',lambda *a,**kw:{
        'evidence_ids':['E'],'retained_evidence_repair_ids':[],
        'withdraw_relation_ids':['R'],'unchanged_held_context_claim_ids':['HELD']})
    monkeypatch.setattr(c,'validate_proposal',lambda *a,**kw:None)
    ev={'evidence_step_id':'E','revision':1,'statement':'professor text','support_eligibility':'withheld_missing_anchor'}
    claim={'claim_id':'C','revision':1,'metrics':{'frequency_count':1},'statement':'unchanged'}
    f={'manifest':{'claim_issue':{'path':'old','file_sha256':'x'},'source_id':'S','permissions':[
        {'collection':'evidence_steps','object_id':'E','fields':sorted(m.c.PROVENANCE|{'source_fragment_id'})},
        {'collection':'claims','object_id':'C','fields':['occurrences']}]},
       'sql_scope':{'read_pins':[]},'claim_issue_context':{},'sentence_inventory':['old'],
       'before_records':c.rows({'evidence_steps/E':ev,'claims/C':claim,'knowledge_relations/R':{'relation_id':'R'}})}
    p=c.Proposal(records=c.rows({'evidence_steps/E':{**ev,'revision':2,'source_fragment_id':'F',
        'support_eligibility':'withheld_unreviewed','valid_for_identity_review':False},
        'claims/C':{**claim,'revision':2,'metrics':{'frequency_count':2}}}),
        dispositions=[c.Disposition(record_key=k,action='update',reason='original reason') for k in ['claims/C','evidence_steps/E']]
            +[c.Disposition(record_key='knowledge_relations/R',action='unresolved',reason='remains pending')],
        sentence_dispositions=[],unadmitted_candidates=[])
    d={'schema_version':m.VERSION,'human_content_approval':False,
       'source_write_keys':['claims/C','evidence_steps/E'],'withdraw_relation_ids':['R']}
    return f,p,d


def test_write_slice_preserves_original_and_pending(monkeypatch):
    f,p,d=fixture(monkeypatch);original=deepcopy(p.model_dump())
    scoped,q=m.projection(f,p,d)
    assert p.model_dump()==original
    assert c.index(q.records)['claims/C']['metrics']=={'frequency_count':1}
    assert 'knowledge_relations/R' in scoped['inventory']['context_keys']
    assert 'claim_issue' not in scoped['manifest']
    assert p.dispositions[-1].action=='unresolved'
    assert c.index(q.records)['evidence_steps/E']['valid_for_identity_review'] is False


@pytest.mark.parametrize('fault',['omit','duplicate','foreign_retire','cvp_write','changed_statement','held_owner','promote_pending'])
def test_no_extra_scope_or_fake_qualification(monkeypatch,fault):
    f,p,d=fixture(monkeypatch)
    if fault=='omit':d['source_write_keys'].pop()
    if fault=='duplicate':d['source_write_keys'].append('evidence_steps/E')
    if fault=='foreign_retire':d['withdraw_relation_ids']=['OTHER']
    if fault=='cvp_write':p.records.append(c.Row(collection='canonical_viewpoints',object_id='V',payload={}))
    if fault=='changed_statement':p.records[1].payload['statement']='invented'
    if fault=='held_owner':
        p.records[0].object_id='HELD';p.records[0].payload['claim_id']='HELD';d['source_write_keys']=['claims/HELD','evidence_steps/E']
    if fault=='promote_pending':p.records[1].payload['valid_for_identity_review']=True
    with pytest.raises((ValueError,KeyError)):m.projection(f,p,d)


@pytest.mark.skipif(os.environ.get('WKP357_REAL_SAVED_READONLY')!='1',reason='actual saved readonly opt-in')
def test_real_saved_strict_source_projection_no_calls_writes():
    from dotenv import load_dotenv
    load_dotenv('.env')
    from backend.pipeline.source_reconciliation_runner import read,body,effective_proposal,validate_raw
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    base=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-e60b6130e0f7c81e810f4195c47f9eda0830bb1110e48c7999cc6fcfbe4e1e2a/claim-issues')
    roots=list(base.glob('*/evidence-completion-once/action/*'));assert len(roots)==1;root=roots[0]
    hashes={p:c.file_sha256(p) for p in root.iterdir() if p.is_file()}
    f=body(read(root/'scoped-materials.json'));c.guard_files(f);store=PostgresKnowledgeStore()
    for pin in f['sql_scope']['read_pins']:
        st=store.get_record_state(pin['collection'],pin['object_id'])
        assert [st['revision'],st['content_sha256'],st['retired']]==pin['pin']
    for stage in ('proposal','initial_review','correction'):validate_raw(root,stage,body(read(root/(stage+'.packet.json'))))
    p=effective_proposal(f,read(root/'correction.raw.json')['response']);original=deepcopy(p.model_dump())
    d={'schema_version':m.VERSION,'human_content_approval':False,
       'source_write_keys':sorted(c.index(p.records)),'withdraw_relation_ids':['AR-0044']}
    scoped,q=m.projection(f,p,d);packet=c.review_packet(scoped,q)
    assert len(q.records)==9 and len(packet['proposal']['records'])==9
    assert p.model_dump()==original
    assert hashes=={p:c.file_sha256(p) for p in root.iterdir() if p.is_file()}
