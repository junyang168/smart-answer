from copy import deepcopy
import os
from pathlib import Path
import pytest
from backend.pipeline import claim_source_write_slice as m
from backend.pipeline import source_reconciliation as c


def impact_fixture(monkeypatch):
    from backend.api.canonical_repository.postgres_store import record_content_sha
    from backend.pipeline import cvp_source_impact_resolution as impacts
    monkeypatch.setattr(m,'validate_claim_layer_authority',lambda *a:True)
    review={'plan':{'change_set_id':'NORMAL'},'evidence':{'viewpoint_ids':['OLD','NEW']}}
    monkeypatch.setattr(impacts,'applied_review',lambda s,v,sha:v['reviewed'] if sha=='current' else review)
    payloads={('claims','C'):{'claim_id':'C','revision':2},
        **{('canonical_viewpoints',v):{'viewpoint_id':v,'revision':1} for v in ['OLD','NEW','CURRENT']},
        ('viewpoint_claim_links','L'):{'claim_id':'C','viewpoint_id':'CURRENT','effective_state':'active','revision':2},
        ('evidence_steps','E'):{'evidence_step_id':'E','revision':1}}
    states={k:{'payload':p,'revision':p['revision'],'content_sha256':record_content_sha(p),'retired':False} for k,p in payloads.items()}
    operation={'collection':'claims','object_id':'C','operation':'update','after_revision':2,
        'after_sha256':states['claims','C']['content_sha256'],'after':payloads['claims','C']}
    class Store:
        calls=[]
        def get_change_history(self,cid):
            return {'status':'applied','source_kind':'cvp_batch_resolution' if cid=='NORMAL' else 'source',
                'fingerprint_sha256':'fingerprint','source_sha256':'source','operations':[operation],
                'metadata':{'run_id':'RUN','reviewed_sha256':'review' if cid=='NORMAL' else 'current','approval':{}}}
        def read_cvp_context(self,claims,*,viewpoint_ids):
            self.calls.append((claims,viewpoint_ids));return dict(states)
        def get_record_state(self,col,oid):return states.get((col,oid))
    value={'run_id':'RUN','checkpoint':'applied','reviewed_sha256':'current','claim_ids':['C'],'change_set_ids':['NORMAL','SOURCE'],
        'reviewed':{'plan':{'change_set_id':'SOURCE','fingerprint_sha256':'fingerprint','source_sha256':'source','operations':[operation]},
            'evidence':{'approval':{},'viewpoint_ids':[],
            'package':{'payload':{'source_repair_application':{'run_root':'root'}}},
            'pending_cvp_management_record_keys':['evidence_steps/E','canonical_viewpoints/OLD']}}}
    return Store(),value,states,review


def test_complete_impact_scope_uses_original_archive_and_all_current_links(monkeypatch):
    store,value,states,_=impact_fixture(monkeypatch)
    result=m.impact_audit_scope(store,value)
    assert result['viewpoint_ids']==['CURRENT','NEW','OLD']
    assert store.calls[-1]==(['C'],['CURRENT','NEW','OLD'])
    assert result['pending_record_keys']==value['reviewed']['evidence']['pending_cvp_management_record_keys']
    assert result['source_record_keys']==['evidence_steps/E']
    assert result['cvp_record_keys']==['canonical_viewpoints/OLD']
    assert value['reviewed']['evidence']['viewpoint_ids']==[]


@pytest.mark.parametrize('fault',['missing_archive','empty_population','duplicate_population','foreign_commit',
    'uncommitted','missing_pending','duplicate_pending','unknown_domain','sha_drift','hold','retired_view','missing_view'])
def test_complete_impact_scope_fails_closed(monkeypatch,fault):
    store,value,states,review=impact_fixture(monkeypatch)
    if fault=='missing_archive':value['change_set_ids']=['SOURCE']
    if fault=='empty_population':review['evidence']['viewpoint_ids']=[]
    if fault=='duplicate_population':review['evidence']['viewpoint_ids']=['OLD','OLD']
    if fault=='foreign_commit':review['plan']['change_set_id']='foreign'
    if fault=='uncommitted':store.get_change_history=lambda cid:{'status':'prepared'}
    if fault=='missing_pending':states.pop(('evidence_steps','E'))
    if fault=='duplicate_pending':value['reviewed']['evidence']['pending_cvp_management_record_keys']*=2
    if fault=='unknown_domain':value['reviewed']['evidence']['pending_cvp_management_record_keys']=['arbitrary/X']
    if fault=='sha_drift':states['claims','C']['content_sha256']='wrong'
    if fault=='hold':states['claims','C']['payload']['review_status']='human_hold'
    if fault=='retired_view':states['canonical_viewpoints','OLD']['retired']=True
    if fault=='missing_view':states.pop(('canonical_viewpoints','NEW'))
    with pytest.raises((ValueError,KeyError)):m.impact_audit_scope(store,value)


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


def test_canonical_binding_order_preserves_full_payload_and_refuses_duplicates():
    values=[{'evidence_step_id':'E2','source_fragment_id':'F','extra':'unchanged'},
            {'evidence_step_id':'E1','source_fragment_id':'F','extra':{'precise':'field'}}]
    saved=deepcopy(values)
    result=c.canonical_binding_order(values)
    assert result==[values[1],values[0]] and values==saved
    assert c.canonical_binding_order(result)==result
    with pytest.raises(ValueError):c.canonical_binding_order([values[0],values[0]])
    with pytest.raises(KeyError):c.canonical_binding_order([{'evidence_step_id':'E1'}])


@pytest.mark.parametrize('fault',[None,'not_applied','foreign_run','wrong_mode','foreign_root','missing_pending','bad_package'])
def test_claim_layer_authority_never_waives_source_or_group_gates(monkeypatch,tmp_path,fault):
    app={'run_root':str(tmp_path),'source_write_slice':{'parent_root':str(tmp_path),
        'mode':'owner_statement_completion','run_id':'run'}}
    value={'source_repair_application':app}
    saved={'run_id':'run','checkpoint':'applied','reviewed':{'evidence':{'package':{'payload':value},
        'pending_cvp_claim_ids':['C']}}}
    def checked(v):
        assert v==value
        if fault=='bad_package':raise ValueError('bad actual raw')
    monkeypatch.setattr(m,'validate_package',checked)
    if fault=='not_applied':saved['checkpoint']='prepared'
    if fault=='foreign_run':app['source_write_slice']['run_id']='other'
    if fault=='wrong_mode':app['source_write_slice']['mode']='other'
    if fault=='foreign_root':app['run_root']=str(tmp_path/'other')
    if fault=='missing_pending':saved['reviewed']['evidence']['pending_cvp_claim_ids']=[]
    if fault:
        with pytest.raises(ValueError):m.validate_claim_layer_authority(saved,tmp_path)
    else:assert m.validate_claim_layer_authority(saved,tmp_path) is True


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
    if (root/'final_review.raw.json').exists():
        final=validate_raw(root,'final_review',body(read(root/'final_review.packet.json')))
        package=m.package(direction,final);m.validate_package(package)
        assert len(package['claims'])==1 and len(package['viewpoint_claim_links'])==3
        assert len(package['viewpoint_identity_candidates'])==len(package['viewpoint_identity_decisions'])==3
        for link in package['viewpoint_claim_links']:
            pairs=[(x['evidence_step_id'],x['source_fragment_id']) for x in link['evidence_bindings']]
            assert pairs==sorted(set(pairs))
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
