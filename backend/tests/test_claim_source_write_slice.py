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


def owner_fixture(monkeypatch):
    from backend.pipeline import claim_owner_revision as owner, cvp_sql_flow
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.api.canonical_repository.postgres_store import record_content_sha
    from backend.pipeline.claim_issue_management import Proposal, Assessment
    proof={'path':'owner','file_sha256':'sealed'}
    monkeypatch.setattr(cvp_sql_flow,'check_binding',lambda b:None)
    monkeypatch.setattr('backend.pipeline.viewpoint_resolution_runtime.read_artifact',lambda p:{'claim_id':'C','campaign_sha256':'campaign'})
    monkeypatch.setattr(owner,'validate_direction',lambda *a:None)
    monkeypatch.setattr(owner,'validate_joint',lambda *a:True)
    monkeypatch.setattr(c,'validate_proposal',lambda *a,**kw:None)
    old={'claim_id':'C','revision':1,'review_status':'ai_consensus_reviewed','statement':'old',
         'context_evidence_step_ids':['E'], 'occurrences':[{'canonical_evidence_step_ids':['E'],
         'project_id':'P','anchors':[{'text':'exact'}]}]}
    link={'viewpoint_claim_link_id':'L','revision':1,'review_status':'system_approved','claim_id':'C','viewpoint_id':'V',
          'effective_state':'active','link_type':'equivalent_component','validated_against_viewpoint_revision_id':'VR',
          'pinned_claim_revision':1,'component_locator':None,'evidence_bindings':[]}
    states={'claims/C':old,'viewpoint_claim_links/L':link,'knowledge_relations/R':{'relation_id':'R','revision':1}}
    f={'manifest':{'claim_issue':proof,'permissions':[]},'sql_scope':{'run_id':'run', 'read_pins':[
        {'collection':col,'object_id':oid,'pin':[v['revision'],record_content_sha(v),False]}
        for k,v in states.items() for col,oid in [c.split_key(k)]]},
       'before_records':c.rows(states),'owner_claim_completion':proof}
    new=deepcopy(old);new.update(statement='model-authored new statement',revision=2,review_status='candidate')
    new['canonical_evidence_step_ids']=new.pop('context_evidence_step_ids')
    occ=new['occurrences'][0];occ['citation_ids']=occ.pop('canonical_evidence_step_ids');occ['parent_topic_id']=occ.pop('project_id')
    p=Proposal(records=c.rows({'claims/C':new,'viewpoint_claim_links/L':{**link,'revision':2,
        'review_status':'candidate','pinned_claim_revision':2}}),
        dispositions=[c.Disposition(record_key=k,action='update' if k!='knowledge_relations/R' else 'unresolved',
            reason='original reason') for k in states],sentence_dispositions=[],unadmitted_candidates=[],cvp_impacts=[],
        claim_assessments=[Assessment(issue_id='I',cause='claim_statement',reason='owner requested',
            citations=[{'source_id':'S','paragraph_key':'S0001','exact_quote':'exact'}])])
    d={'schema_version':m.VERSION,'mode':'owner_statement_completion','human_content_approval':False,
       'owner_claim_direction':proof,'source_write_keys':['claims/C','viewpoint_claim_links/L'],'withdraw_relation_ids':[]}
    return f,p,d


def test_owner_statement_slice_preserves_storage_and_pending(monkeypatch):
    f,p,d=owner_fixture(monkeypatch);saved=deepcopy(p.model_dump());old=c.index(f['before_records'])
    scoped,q=m.projection(f,p,d);new=c.index(q.records)['claims/C']
    assert new['statement']==p.records[0].payload['statement']
    assert new['occurrences']==old['claims/C']['occurrences']
    assert new['context_evidence_step_ids']==['E'] and 'canonical_evidence_step_ids' not in new
    assert len(scoped['owner_statement_storage_preservation'])==3
    assert 'knowledge_relations/R' in scoped['inventory']['context_keys']
    assert p.model_dump()==saved and p.dispositions[-1].action=='unresolved'


@pytest.mark.parametrize('fault',['missing_link','extra_write','foreign_direction','retirement','stale_pin',
    'held','changed_type','changed_target','changed_anchor','changed_bucket','changed_occurrence',
    'wrong_project','approved_draft','empty_statement','changed_claim_type','duplicate_record'])
def test_owner_statement_slice_refuses_scope_or_content_drift(monkeypatch,fault):
    f,p,d=owner_fixture(monkeypatch);claim=p.records[0].payload;link=p.records[1].payload
    if fault=='missing_link':p.records.pop()
    if fault=='extra_write':p.records.append(c.Row(collection='knowledge_relations',object_id='R',payload={'relation_id':'R'}))
    if fault=='foreign_direction':d['owner_claim_direction']={'path':'foreign'}
    if fault=='retirement':d['withdraw_relation_ids']=['R']
    if fault=='stale_pin':f['sql_scope']['read_pins'][0]['pin'][0]=0
    if fault=='held':
        f['before_records'][0]['payload']['review_status']='human_hold'
    if fault=='changed_type':link['link_type']='supports'
    if fault=='changed_target':link['viewpoint_id']='foreign'
    if fault=='changed_anchor':claim['occurrences'][0]['anchors'][0]['text']='new'
    if fault=='changed_bucket':claim['canonical_evidence_step_ids']=['new']
    if fault=='changed_occurrence':claim['occurrences']=[]
    if fault=='wrong_project':claim['occurrences'][0]['parent_topic_id']='other'
    if fault=='approved_draft':claim['review_status']='approved'
    if fault=='empty_statement':claim['statement']=''
    if fault=='changed_claim_type':claim['claim_type']='new'
    if fault=='duplicate_record':p.records.append(p.records[0])
    with pytest.raises((ValueError,KeyError)):m.projection(f,p,d)


@pytest.mark.skipif(os.environ.get('WKP357_G112_READONLY')!='1',reason='actual saved g112 readonly opt-in')
def test_real_owner_statement_slice_is_strict_and_parent_byte_identical():
    from dotenv import load_dotenv
    load_dotenv('.env')
    from backend.pipeline.source_reconciliation_runner import read,body,validate_raw
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    base=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
    direction=read(base/'owner-g112-statement-completion-write-slice.json')
    root=Path(direction['parent_root']);hashes={p:c.file_sha256(p) for p in root.iterdir() if p.is_file()}
    _,f,scoped,p,packet=m.prepare(direction)
    store=PostgresKnowledgeStore()
    with store.connect() as conn,conn.cursor() as cur:
        store._assert_transaction_read_pins(cur,{(r['collection'],r['object_id']):tuple(r['pin']) for r in f['sql_scope']['read_pins']})
    assert len(f['sql_scope']['read_pins'])==460
    assert len(p.records)==4 and len(packet['proposal']['dispositions'])==4
    assert len(packet['source_write_slice']['pending_dispositions'])==54
    assert len(packet['source_write_slice']['program_projection_delta'])==3
    old=c.index(f['before_records']);new=c.index(p.records)
    cid=read(Path(direction['owner_claim_direction']['path']))['claim_id']
    assert new['claims/'+cid]['occurrences']==old['claims/'+cid]['occurrences']
    assert new['claims/'+cid]['context_evidence_step_ids']==old['claims/'+cid]['context_evidence_step_ids']
    for stage in ('proposal','initial_review','correction'):validate_raw(root,stage,body(read(root/(stage+'.packet.json'))))
    assert hashes=={p:c.file_sha256(p) for p in root.iterdir() if p.is_file()}


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
