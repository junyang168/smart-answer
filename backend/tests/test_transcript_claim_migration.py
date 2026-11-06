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


def published_replacement(case):
    store,beta,root,rows,context=case
    live=Path(store.rows['source_documents','S']['payload']['source_path'])
    snapshot=live.with_name('historical-exact.json')
    snapshot.write_bytes(live.read_bytes())
    new=migration.beta_source(rows,context,item='lecture')
    new['metadata']['item']='lecture'
    live.write_text(json.dumps(new,ensure_ascii=False))
    return live,snapshot


def test_exact_historical_snapshot_hands_off_an_already_published_source(case):
    store,_,root,_,_=case
    live,snapshot=published_replacement(case)
    with pytest.raises(ValueError,match='registered old source file drift'):
        migration.prepare(store,beta_root=case[1],item='lecture',source_id='S',
                          root=root/'default-preflight',instruction='Check unchanged strict default')
    m=prepare(case,old_source_snapshot=snapshot)
    assert m['before']['file_sha256']==migration.sha(snapshot.read_bytes())
    assert m['historical_inputs']['live']['file_sha256']==migration.sha(live.read_bytes())
    assert migration.register(store,root)['status']=='applied'
    assert store.writes==1
    assert migration.register(store,root)['status']=='applied' and store.writes==1


@pytest.mark.parametrize('drift',['snapshot','foreign_item','incoming_text','clock','live_after_prepare','snapshot_after_prepare'])
def test_historical_snapshot_never_bypasses_source_or_publication_guards(case,drift):
    store,_,_,_,_=case
    live,snapshot=published_replacement(case)
    if drift.endswith('_after_prepare'):
        prepare(case,old_source_snapshot=snapshot)
        (live if drift.startswith('live') else snapshot).write_text('changed')
        with pytest.raises(ValueError):migration.register(store,case[2])
    else:
        if drift=='snapshot':snapshot.write_text('wrong historical bytes')
        else:
            d=migration.read(live)
            if drift=='foreign_item':d['metadata']['item']='foreign'
            elif drift=='incoming_text':d['script'][1]['text']='another text'
            else:d['script'][1]['start_time']=50
            live.write_text(json.dumps(d,ensure_ascii=False))
        with pytest.raises(ValueError):prepare(case,old_source_snapshot=snapshot)
    assert store.writes==0


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


def test_explicit_rejected_recovery_cannot_touch_an_active_controller(case):
    import fcntl
    _,_,root,_,_=case;prepare(case)
    unit=root/'units/000'
    request=migration.immutable(unit/'request.json',{'action_id':'original','target_keys':['claims/D'],'source_bindings':{}})
    manifest=migration.immutable(unit/'manifest.json',{'jobs':[{'request':request,'root':str(unit/'execution')}],'max_workers':1})
    migration.immutable(unit/'prepared.json',{'manifest':manifest})
    (unit/'batch').mkdir()
    with (unit/'batch/controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        store=SimpleNamespace(get_cvp_group=lambda key:None)
        with pytest.raises(BlockingIOError):migration.resume_rejected_unit(store,root,0)


def test_rejected_recovery_creates_derived_head_controller_directory(case,monkeypatch):
    from backend.pipeline import source_execution_locks
    _,_,root,_,_=case;prepare(case)
    unit=root/'units/000'
    migration.immutable(unit/'prepared.json',{'manifest':{'path':'unused'}})
    directory=unit/'admitted-action-heads'/'exact-head'
    monkeypatch.setattr(migration,'current_unit_prepared',lambda *a:{'manifest':{'path':str(directory/'manifest.json')}})
    class StopAtMaintenance(Exception):pass
    def stopped():raise StopAtMaintenance()
    monkeypatch.setattr(source_execution_locks,'shared_maintenance_gates',stopped)
    with pytest.raises(StopAtMaintenance):migration.resume_rejected_unit(None,root,0)
    # A SQL-admitted head has not necessarily run its derived batch yet.
    assert (directory/'batch/controller.lock').is_file()


@pytest.mark.parametrize('defect',[None,'source','membership','outside','missing','unadmitted'])
def test_resume_follows_only_the_exact_sql_admitted_action_head(case,defect):
    from backend.pipeline import claim_management as cm
    _,_,root,_,_=case;m=prepare(case)
    unit=root/'units/000'
    original={'action_id':'original','target_keys':['claims/D'],'source_bindings':{'S':m['after']}}
    child={**original,'action_id':'successor','predecessor_action_id':'original'}
    execution=unit/'ordinary-recovery-v1'
    first='CMA-'+cm.sha256_json('original');second='CMA-'+cm.sha256_json('successor')
    rows={first:{'request':original,'continued_by':second},second:{'request':child,
        'execution_root':str(execution),'continuation':{'predecessor_action_id':'original'}}}
    if defect=='source':child['source_bindings']={'other':m['after']}
    if defect=='membership':child['target_keys']=['claims/C']
    if defect=='outside':rows[second]['execution_root']=str(root.parent/'another-unit')
    if defect=='missing':rows.pop(second)
    if defect=='unadmitted':rows[second]['continuation']={}
    request=migration.immutable(unit/'request.json',original)
    manifest=migration.immutable(unit/'manifest.json',{'jobs':[{'request':request,'root':str(unit/'execution')}],'max_workers':1})
    prepared={'manifest':manifest}
    store=SimpleNamespace(get_cvp_group=lambda key:deepcopy(rows.get(key)))
    if defect:
        with pytest.raises(ValueError):migration.current_unit_prepared(store,root,0,prepared)
    else:
        selected=migration.current_unit_prepared(store,root,0,prepared)
        assert migration.current_unit_prepared(store,root,0,prepared)==selected
        job=migration.read(selected['manifest']['path'])['jobs'][0]
        assert migration.read(job['request']['path'])==child and job['root']==str(execution)
        assert migration.read(manifest['path'])['jobs'][0]['root']==str(unit/'execution')


def test_verify_uses_own_proof_and_never_treats_unrun_units_as_complete(case):
    store,_,root,_,_=case;m=prepare(case);migration.register(store,root)
    snapshot=migration.cm.source.source_snapshot(migration.cm.source.FileBinding.model_validate(m['after']))
    for cid in ('C','D'):
        store.rows['source_fragments','F'+cid]['payload'].update(
            paragraph_key='S0001',verbatim_excerpt='保留。',source_sha256=snapshot['body_sha256'])
    store.rows['source_fragments','unrelated-private-quote']=state({
        'fragment_id':'unrelated-private-quote','source_id':'S','paragraph_key':'S0001',
        'verbatim_excerpt':'不可作證據。','source_sha256':'old'})
    store.claim_management_qualification=lambda cid:{'ready':True}
    store.read_claim_context=lambda keys:deepcopy(store.rows)
    report=migration.verify(store,root)
    assert all(v['status']=='current' for v in report['results'].values())
    assert report['complete'] is False and report['created_claim_ids']==[]
    # A target's own deleted quote must fail even if some legacy qualification
    # flag says ready; the neighbour's private quote above did not fail it.
    store.rows['source_fragments','FC']['payload']['verbatim_excerpt']='不可作證據。'
    report=migration.verify(store,root)
    assert report['results']['C']['status']=='pending'
    assert report['results']['C']['invalid_live_quote_ids']==['FC']
    store.rows['claims','C']['retired']=True
    assert migration.verify(store,root)['results']['C']['status']=='retired_or_missing'


@pytest.mark.parametrize('held',[False,True])
def test_new_migration_unit_includes_question_and_quote_without_extra_claim_targets(case,postgres_store,held,monkeypatch):
    fake,beta,root,_,_=case
    fake.rows['questions','Q']=state({'question_id':'Q','text':'是什麼？','source_fragment_ids':['FQ'],
        'answer_claim_ids':['D'],'review_status':'candidate'})
    fake.rows['source_fragments','FQ']=state({'fragment_id':'FQ','source_id':'S',
        'paragraph_key':'S0001','verbatim_excerpt':'舊話。'})
    package={'package_id':'question-source-migration-fixture'}
    for (col,_),row in fake.rows.items():
        payload=deepcopy(row['payload'])
        if col=='claims':payload['review_status']='candidate'
        package.setdefault(col,[]).append(payload)
    store=postgres_store;store.apply_plan(store.plan_package(package))
    migration.prepare(store,beta_root=beta,item='lecture',source_id='S',root=root,
        instruction='移植 Claim 和相關問題與來源；軟刪除不能作證據',group_size=1)
    migration.register(store,root)
    if held:
        original=store.read_claim_context
        def with_hold(keys):
            records=original(keys)
            records['questions','Q']['payload']['review_status']='human_review_required'
            return records
        monkeypatch.setattr(store,'read_claim_context',with_hold)
        with pytest.raises(ValueError,match='protected Question|required Question evidence.*held'):
            migration.prepare_unit(store,root,0)
        return
    prepared=migration.prepare_unit(store,root,0)
    job=migration.read(prepared['manifest']['path'])['jobs'][0]
    request=migration.read(job['request']['path']);auth=migration.read(request['authorization']['path'])
    assert request['target_keys']==['claims/D']
    assert auth['required_maintenance_keys']==['questions/Q','source_fragments/FQ']
    grants={p['collection']+'/'+p['object_id']:set(p['fields']) for p in request['permissions']}
    assert 'verbatim_excerpt' in grants['source_fragments/FQ'] and 'text' in grants['questions/Q']
    assert 'claims/C' not in grants


@pytest.mark.parametrize('defect',[None,'missing_own_evidence','hidden_claim','unrelated_grant'])
def test_question_quote_admission_uses_same_reachability_on_frozen_projection(case,defect):
    from backend.pipeline import claim_management as cm,claim_joint_update as joint
    store,_,root,_,_=case;m=prepare(case);migration.register(store,root)
    store.rows['questions','Q']=state({'question_id':'Q','text':'問句','answer_claim_ids':['C'],'source_fragment_ids':['FQ']})
    store.rows['source_fragments','FQ']=state({'fragment_id':'FQ','source_id':'S','paragraph_key':'S0001','verbatim_excerpt':'舊話。'})
    store.rows['claim_relations','R']=state({'claim_relation_id':'R','from_id':'C','to_id':'D','relation_type':'supports'})
    req=cm.Request(action_id='question-context',operation='Update',allowed_operations=['Update'],
        target_keys=['claims/C'],source_bindings={'S':m['after']},policy=m['policy'],
        authorization={'path':str(root/'context-auth.json'),'file_sha256':'0'*64},
        permissions=[cm.source.Permission(collection='source_fragments',object_id='FQ',fields=['verbatim_excerpt'])])
    auth={'schema_version':cm.VERSION,'authorized_by':'owner','authorization_quote':'修正問題引用',
        'human_content_approval':False,'request':req.model_dump(mode='json',exclude={'authorization','policy'}),
        'policy':m['policy'],'required_maintenance_keys':['source_fragments/FQ'],'review_questions':['核對問題引用']}
    req.authorization=cm.source.FileBinding.model_validate(migration.immutable(root/'context-auth.json',auth))
    before={cm.source.key(*key):cm.model_state(value) for key,value in store.rows.items()}
    policy=cm.Policy.model_validate(migration.read(m['policy']['path']))
    goal=cm.original_goal(req,before)
    projected=deepcopy(goal['original_objects'])
    assert 'claims/D' in projected and 'evidence_steps/ED' not in projected
    if defect=='missing_own_evidence':projected.pop('evidence_steps/EC')
    if defect=='hidden_claim':req.permissions.append(cm.source.Permission(collection='claims',object_id='D',fields=['statement']))
    if defect=='unrelated_grant':req.permissions.append(cm.source.Permission(collection='source_fragments',object_id='FH',fields=['verbatim_excerpt']))
    error=joint.admission_error(req,policy,projected,goal)
    if defect:assert error is not None
    else:assert error is None,error


def test_comparison_is_readonly_escapes_text_and_does_not_invent_qualification(case):
    from backend.pipeline.transcript_claim_migration_report import compare
    store,_,root,_,_=case;m=prepare(case);migration.register(store,root)
    payload=deepcopy(store.rows['claims','C']['payload']);payload['statement']='<img src=x onerror=bad()>'
    store.rows['claims','C']=state(payload,2)
    snapshot=migration.cm.source.source_snapshot(migration.cm.source.FileBinding.model_validate(m['after']))
    payload=deepcopy(store.rows['source_fragments','FC']['payload'])
    payload.update(paragraph_key='S0001',verbatim_excerpt='保留。',source_sha256=snapshot['body_sha256'])
    store.rows['source_fragments','FC']=state(payload,2)
    store.read_current_records=lambda keys:{key:deepcopy(store.rows[key]) for key in keys if key in store.rows}
    result=compare(store,root);data=migration.read(result['data']);html=Path(result['comparison']).read_text()
    assert result['claim_count']==2 and result['statement_changes']==1
    assert not data['qualification_checked_by_this_report'] and not data['created_inventory_available']
    assert not data['all_data_migrated'] and data['claims'][0]['saved_verification'] is None
    assert 'H' not in {c['claim_id'] for c in data['claims']}
    assert '<img src=x' not in html and '&lt;img src=x' in html
    assert data['fragment_current_source_match']['source_fragments/FC'] is True
    assert data['fragment_current_source_match']['source_fragments/FD'] is False
    assert store.writes==1


def test_comparison_counts_only_native_applied_creations_and_includes_new_support(case):
    from backend.pipeline.transcript_claim_migration_report import compare
    from backend.pipeline import claim_management as cm
    store,_,root,_,_=case;m=prepare(case);migration.register(store,root)
    req=cm.Request(action_id='applied-source-job',operation='Update',allowed_operations=['Update'],
        target_keys=['claims/D'],source_bindings={'S':m['after']},policy=m['policy'],authorization=m['policy'])
    migration.immutable(root/'units/000/request.json',req.model_dump(mode='json'))
    parent={'kind':'claim_management_action','request':req.model_dump(mode='json'),
        'execution_root':str(root/'units/000/execution'),
        'created_keys':['claims/NEW','source_fragments/NEW-F','source_fragments/MISSING']}
    store.get_cvp_group=lambda key:deepcopy(parent)
    store.rows['claims','NEW']=state({'claim_id':'NEW','statement':'新主張','occurrences':[{'source_id':'S'}]})
    store.rows['source_fragments','NEW-F']=state({'fragment_id':'NEW-F','source_id':'S',
        'paragraph_key':'S0001','verbatim_excerpt':'保留。'})
    store.read_current_records=lambda keys:{key:deepcopy(store.rows[key]) for key in keys if key in store.rows}
    result=compare(store,root);data=migration.read(result['data'])
    assert result['created_claim_count']==1 and result['statement_changes']==0 and result['claim_count']==3
    changes={row['record_key']:row for row in data['dependent_changes']}
    assert changes['source_fragments/NEW-F']['before_pin'] is None
    assert changes['source_fragments/NEW-F']['after_pin'] is not None
    assert changes['source_fragments/MISSING']['after_pin'] is None
    assert data['created_inventory_available'] and not data['qualification_checked_by_this_report']
    assert '新增 Claim' in Path(result['comparison']).read_text()
    # A saved proposal is not an applied creation fact.
    parent['created_keys']=[]
    assert compare(store,root)['created_claim_count']==0


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
