"""A new source epoch never masquerades as Claim review/publication."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.pipeline import transcript_claim_migration as migration
from backend.pipeline.source_projection import project_script
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


def state(payload, revision=1):
    return {'payload':deepcopy(payload),'revision':revision,
            'content_sha256':record_content_sha(payload),'retired':False}


class Store:
    def __init__(self, tmp):
        old=tmp/'old.json';old.write_text(json.dumps({'script':[{'index':'old','text':'舊話。'}]},ensure_ascii=False))
        projection=project_script(json.loads(old.read_text())['script'])
        doc={'source_id':'S','source_type':'sermon_transcript','transcript_id':'lecture',
             'source_path':str(old),'source_file_sha256':migration.sha(old.read_bytes()),
             'source_body_sha256':projection.body_sha256,'source_sha256':projection.body_sha256,
             'locator_space':'spoken_body_v1'}
        self.rows={('source_documents','S'):state(doc)}
        for cid, status, paragraph in [('C','candidate','S0002'),('D','candidate','S0001'),('H','superseded','S0001')]:
            self.rows['source_fragments','F'+cid]=state({'fragment_id':'F'+cid,'source_id':'S',
                'paragraph_key':paragraph,'verbatim_excerpt':'舊話。'})
            self.rows['evidence_steps','E'+cid]=state({'evidence_step_id':'E'+cid,
                'source_fragment_ids':['F'+cid],'produced_claim_ids':[cid],'statement':'舊話'})
            self.rows['claims',cid]=state({'claim_id':cid,'review_status':status,'statement':'舊判斷',
                'claim_type':'exegesis','occurrences':[{'source_id':'S'}],'evidence_step_ids':['E'+cid]})
        self.writes=0

    def get_record_state(self,col,oid):return deepcopy(self.rows.get((col,oid)))
    def read_affected_context(self,keys):return deepcopy(self.rows)
    def plan_package(self,package,**kwargs):
        return SimpleNamespace(operations=[SimpleNamespace(collection='source_documents')],
            fingerprint_sha256='f'*64,change_set_id='KCS-test',package=package)
    def apply_plan(self,plan,**kwargs):
        self.writes+=1
        before=self.rows['source_documents','S']
        self.rows['source_documents','S']=state(plan.package['source_documents'][0],2)
        self.history={'status':'applied','operations':[{'collection':'source_documents','object_id':'S',
            'before_sha256':before['content_sha256'],'after_sha256':self.rows['source_documents','S']['content_sha256'],
            'after_revision':2}]}
        return {'change_set_id':'KCS-test','status':'applied'}
    def get_change_history(self,csid):return self.history


@pytest.fixture
def case(tmp_path):
    store=Store(tmp_path);beta=tmp_path/'beta';root=tmp_path/'migration'
    rows=[{'index':'heading','type':'subtitle','text':'## AI小標題'},
          {'index':'one','type':'content','text':'保留。~~不可作證據。~~'},
          {'index':'two','type':'content','text':'~~整段軟刪除。~~'}]
    context={'revision':4,'paragraph_bindings':{
        'one':{'start_ms':1000,'end_ms':2000},'two':{'start_ms':2000,'end_ms':3000}}}
    (beta/'script_review').mkdir(parents=True);(beta/'transcript_review/lecture').mkdir(parents=True)
    (beta/'script_review/lecture.json').write_text(json.dumps(rows,ensure_ascii=False))
    (beta/'transcript_review/lecture/context.json').write_text(json.dumps(context))
    return store,beta,root,rows,context


def prepare(case,**kwargs):
    store,beta,root,_,_=case
    return migration.prepare(store,beta_root=beta,item='lecture',source_id='S',root=root,
                             instruction='審查後移植；軟刪除不能進Claim',group_size=1,**kwargs)


def test_freeze_preserves_paragraph_ids_clock_and_excludes_deleted_evidence(case):
    store,beta,root,rows,context=case
    m=prepare(case)
    assert m['claim_ids']==['D','C'] and m['groups']==[['D'],['C']]
    assert m['excluded_superseded_ids']==['H']
    assert not m['claim_approval_granted'] and not m['public_transcript_changed']
    after=migration.read(m['after']['path'])
    assert after['script'][0]==rows[0]
    assert [r['index'] for r in after['script']]==[r['index'] for r in rows]
    assert after['script'][1]['start_time']==1 and after['script'][1]['end_time']==2
    projection=project_script(after['script'])
    assert len(projection.spoken_rows)==2
    assert '不可作證據' not in projection.spoken_rows[0]['text']
    assert not projection.spoken_rows[1]['text'].strip()
    assert store.writes==0
    assert prepare(case)==m


def test_registration_only_writes_source_and_native_receipt_is_idempotent(case):
    store,_,root,_,_=case;m=prepare(case);before=deepcopy(store.rows)
    result=migration.register(store,root)
    assert result['change_set_id']=='KCS-test' and store.writes==1
    assert store.rows['source_documents','S']['payload']['source_file_sha256']==m['after']['file_sha256']
    assert {k:v for k,v in before.items() if k[0]!='source_documents'}=={
        k:v for k,v in store.rows.items() if k[0]!='source_documents'}
    assert migration.register(store,root)==result and store.writes==1


@pytest.mark.parametrize('drift',['old_file','live_old_file','beta_file','source_record'])
def test_registration_fails_before_writes_on_drift(case,drift):
    store,beta,root,_,_=case;prepare(case)
    if drift=='old_file':(root/'source-before.json').write_text('tamper')
    elif drift=='live_old_file':Path(store.rows['source_documents','S']['payload']['source_path']).write_text('changed')
    elif drift=='beta_file':(beta/'script_review/lecture.json').write_text('[]')
    else:store.rows['source_documents','S']['revision']+=1
    with pytest.raises(ValueError):migration.register(store,root)
    assert store.writes==0


def test_loss_of_receipt_does_not_resend_registration(case):
    store,_,root,_,_=case;prepare(case);migration.register(store,root)
    (root/'source-registration.json').unlink()
    assert migration.register(store,root)['recovered_without_second_write'] is True
    assert store.writes==1


def test_snapshot_missing_clock_or_duplicate_id_rejected(case):
    _,_,_,rows,context=case
    with pytest.raises(ValueError,match='ambiguous'):
        migration.beta_source(rows+[rows[1]],context,item='lecture')
    context['paragraph_bindings'].pop('one')
    with pytest.raises(ValueError,match='missing beta media'):
        migration.beta_source(rows,context,item='lecture')


def test_frozen_policy_is_subscription_only_and_original_not_modified(case):
    original=(Path(migration.__file__).parent/'policies/wang_claim_management_bounded_unit_v1.json').read_bytes()
    m=prepare(case);policy=migration.read(m['policy']['path'])
    assert policy['models']['opus']['backend']=='claude_subscription'
    assert policy['api_spend'] is None
    assert original==(Path(migration.__file__).parent/'policies/wang_claim_management_bounded_unit_v1.json').read_bytes()


def test_progress_registration_preserves_other_campaigns_and_reuses_fixed_population(case,monkeypatch,tmp_path):
    _,_,root,_,_=case;prepare(case)
    index=tmp_path/'campaigns.json'
    index.write_text(json.dumps({'campaigns':[{'id':'other','root':'elsewhere'}]}))
    monkeypatch.setenv('DATA_BASE_DIR',str(tmp_path))
    monkeypatch.setenv('WANG_CLAIM_REFRESH_CAMPAIGNS_FILE',str(index))
    request=migration.immutable(root/'request-test.json',{'action_id':'exact-action'})
    manifest=migration.immutable(root/'batch-test.json',{'jobs':[{'request':request,'root':str(root/'execution')}],'max_workers':1})
    prepared={'manifest':manifest}
    capacity={'root':str(tmp_path/'shared-existing-capacity'),'action_window':4}
    entry=migration.register_progress(root,prepared,capacity)
    assert migration.register_progress(root,prepared,capacity)==entry
    values=migration.read(index)['campaigns']
    assert len(values)==2 and values[0]=={'id':'other','root':'elsewhere'}
    assert entry['seed_action_ids']==['exact-action']
    from backend.api.claim_refresh_progress import frozen_plan
    path=Path(entry['plan']['path']);stat=path.stat()
    plan=frozen_plan(str(path),stat.st_mtime_ns,stat.st_size,entry['plan']['file_sha256'])
    assert plan['original_count']==2
    with pytest.raises(ValueError,match='input changed'):
        migration.register_progress(root,prepared,{**capacity,'action_window':8})


def test_source_epoch_admission_code_is_bound_in_model_audit_contract(tmp_path):
    from backend.pipeline import claim_management as cm
    policy=cm.Policy.model_validate_json((Path(migration.__file__).parent/'policies/wang_claim_management_bounded_unit_v1.json').read_text())
    contract=cm.audit_contract(tmp_path,policy)
    assert 'backend/pipeline/claim_source_epoch_reassessment.py' in contract['implementation']


def test_real_sql_handoff_preserves_claims_and_uses_native_update(case,postgres_store):
    fake,beta,root,_,_=case
    package={'package_id':'synthetic-migration-native-fixture'}
    for (col,_),row in fake.rows.items():
        payload=deepcopy(row['payload'])
        if col=='claims':payload['review_status']='candidate'
        package.setdefault(col,[]).append(payload)
    store=postgres_store
    store.apply_plan(store.plan_package(package))
    before={cid:store.get_record_state('claims',cid) for cid in ('C','D','H')}
    m=migration.prepare(store,beta_root=beta,item='lecture',source_id='S',root=root,
                         instruction='移植；軟刪除不作Claim證據',group_size=1)
    result=migration.register(store,root)
    history=store.get_change_history(result['change_set_id'])
    assert [(x['collection'],x['object_id']) for x in history['operations']]==[('source_documents','S')]
    assert {cid:store.get_record_state('claims',cid) for cid in before}==before
    prepared=migration.prepare_unit(store,root,0)
    from backend.pipeline import claim_management_batch as batch
    _,jobs=batch.load(prepared['manifest']['path'],root/'units/000/batch',store=store)
    assert len(jobs)==1
    request,_=next(iter(jobs.values()))
    assert request.operation=='Update' and request.target_keys==['claims/D']
    assert request.source_bindings['S'].file_sha256==m['after']['file_sha256']
    assert migration.register(store,root)==result


@pytest.mark.parametrize('fault',[None,'human','late_human','system','source_drift'])
def test_source_epoch_reassesses_only_ai_questions_on_actual_new_source(case,postgres_store,fault):
    from backend.pipeline.claim_source_epoch_reassessment import marker,VERSION
    from backend.pipeline.claim_metadata_projection import compile_projection
    from backend.pipeline import claim_management_batch as batch
    fake,beta,root,_,_=case;store=postgres_store
    package={'package_id':'synthetic-new-source-ai-question'}
    for (col,_),row in fake.rows.items():
        payload=deepcopy(row['payload'])
        if col=='claims':payload['review_status']='candidate'
        package.setdefault(col,[]).append(payload)
    store.apply_plan(store.plan_package(package))
    store.record_review('claims','D',decision='human_review_required',reviewer_kind='human' if fault=='human' else 'ai',
                        reason='old AI source question',reviewer_id='fixture-ai')
    # Synthetic legacy AI provenance, restricted to this disposable database.
    artifact={'resolution':{'schema_version':'wang_claim_ai_review_provenance_v1',
        'claim_id':'D','approval_status':'not_human_approved','target_review_status':'human_review_required'}}
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.review_events SET artifact=%s::jsonb WHERE collection='claims' AND object_id='D'",(json.dumps(artifact),))
    if fault=='system':store.record_review('claims','D',decision='human_review_required',reviewer_kind='system',reason='real pending hold')
    migration.prepare(store,beta_root=beta,item='lecture',source_id='S',root=root,
                      instruction='審查新版本，僅真正無法解決交人工',group_size=1)
    receipt=migration.register(store,root)
    epoch=migration.immutable(root/'source-epoch-reassessment.json',{'schema_version':VERSION,
        'manifest':migration.binding(root/'manifest.json'),'change_set_id':receipt['change_set_id']})
    s=store.get_record_state('claims','D')
    if fault=='source_drift':
        current=store.get_record_state('source_documents','S')['payload'];current['title']='another epoch'
        store.apply_plan(store.plan_package({'package_id':'synthetic-source-drift','source_documents':[current]}))
    with store.connect() as conn,conn.cursor() as cursor:
        if fault=='source_drift':
            with pytest.raises(ValueError,match='current identity/version drift'):marker(cursor,'D',s,epoch)
            return
        proof=marker(cursor,'D',s,epoch)
    if fault in ('human','system'):
        assert proof is None
        return
    assert proof and set(proof)=={'before_pin','events_sha256'}
    prepared=migration.prepare_unit(store,root,0)
    _,jobs=batch.load(prepared['manifest']['path'],root/'units/000/batch',store=store)
    request,_=next(iter(jobs.values()));records=store.read_claim_context([('claims','D')])
    before={migration.cm.source.key(*k):migration.cm.model_state(v) for k,v in records.items()}
    policy=migration.cm.Policy.model_validate_json(Path(request.policy.path).read_text())
    if fault=='late_human':store.record_review('claims','D',decision='human_review_required',reviewer_kind='human',reason='keep decision')
    with store.connect() as conn,conn.cursor() as cursor:
        if fault=='late_human':
            with pytest.raises(ValueError,match='history changed'):compile_projection(cursor,request,before,policy)
        else:
            projection=compile_projection(cursor,request,before,policy)
            assert 'claims/D' in projection['legacy_ai_findings_markers']
