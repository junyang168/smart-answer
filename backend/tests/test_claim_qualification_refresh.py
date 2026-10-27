"""Real SQL/Update execution, synthetic providers, automatic currentness handoff."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from backend.pipeline import claim_management as cm,claim_management_batch as batch,claim_management_intake as intake
from backend.pipeline import claim_qualification_refresh as refresh
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_management_intake import claims,pass_review


def shared(claims):
    store,root=claims
    c=store.get_record_state('claims','C')['payload'];d=store.get_record_state('claims','D')['payload']
    c['evidence_step_ids']=['E'];d['evidence_step_ids']=['E']
    former=[store.get_record_state('evidence_steps','E'+cid)['payload'] for cid in ['C','D']]
    for step in former:step['produced_claim_ids']=[]
    store.apply_plan(store.plan_package({'package_id':'shared-currentness-evidence','claims':[c,d],
        'evidence_steps':[{'evidence_step_id':'E','statement':'甲是假设。','produced_claim_ids':['C','D'],
            'source_fragment_ids':['FC'],'speaker':'professor','stance':'asserted','support_eligibility':'eligible_candidate'},*former]}))
    return store,root


def executor(monkeypatch,store,*,mode='pass',create=False):
    clients={};events=[]
    def execute(manifest,output):
        plan,jobs=batch.load(manifest,output,store=store)
        for aid,(req,root) in jobs.items():
            target=req.target_keys[0];readonly=aid.startswith('claim-currentness-')
            if readonly:
                assert plan['max_workers']==1 and req.permissions==req.creation_permissions==[]
                assert req.allowed_operations==['Update'] and req.predecessor_action_id is None
                assert json.loads(Path(req.policy.path).read_text())['budget']['max_revisions']==1
            def cite():return {'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
            def resolutions(packet):return [{'finding_id':fid,'disposition':'persistent' if readonly and mode=='disagree' else 'resolved',
                'reason':'current exact own evidence evaluated','citations':[cite()],
                'affected_target_keys':['claims/C'] if readonly and mode=='disagree' else [target],
                'affected_write_keys':[] if readonly else ['evidence_steps/E'],
                'impact_reason':'own evidence judgment'} for fid in packet['unresolved']]
            def review(packet):
                if readonly and mode=='unknown':raise RuntimeError('synthetic lost Opus reply')
                result=pass_review(packet)
                rows={k:v['payload'] for k,v in packet['before'].items()}
                rows.update(cm.source.index(cm.Candidate.model_validate(packet['candidate']).records))
                desired='甲是假设，不是断言' if target=='claims/B' else '甲是假设，不是断言。'
                bad=(target in {'claims/D','claims/B'} and not readonly and rows['evidence_steps/E']['statement']!=desired) or readonly and mode=='disagree'
                if bad:
                    for a in result['binding_assessments']:
                        if a['claim_key']==target:a['sufficient']=False
                    if not packet['unresolved']:
                        result['findings']=[{'target_key':target,'component':'own_evidence','category':'evidence',
                            'reason':'synthetic substantive evidence objection','acceptance_condition':'evaluate full current evidence',
                            'citations':[cite()],'affected_target_keys':[target],'affected_write_keys':[],
                            'impact_reason':'target own evidence'}]
                result['prior_findings']=resolutions(packet)
                return result
            def proposal(packet):
                fields={'statement':'甲是假设，不是断言' if target=='claims/B' else '甲是假设，不是断言。'}
                creations=[]
                if create and target=='claims/D' and not readonly:
                    cid=packet['original_goal']['claim_creation_allocations']['S']
                    fields['produced_claim_ids']=packet['before']['evidence_steps/E']['payload']['produced_claim_ids']+[cid]
                    creations=[{'collection':'claims','object_id':cid,'fields_json':json.dumps({'statement':'甲不是断言。','claim_type':'exegesis',
                        'occurrences':[{'source_id':'S','anchors':[]}],'evidence_step_ids':['E']}),'reason':'a separately expressed judgment','citations':[cite()]}]
                return {'changes':[] if readonly else [{'record_key':'evidence_steps/E',
                    'fields_json':json.dumps(fields),'reason':'explicit own-source qualification','citations':[cite()]}],
                    'creations':creations,'retirements':[],'findings':[],'finding_resolutions':resolutions(packet)}
            port=clients.setdefault(aid,SyntheticPorts(store,root,opus=review,proposal=proposal))
            before=len(port.calls);result=execute_claim_management(store,req,root=root,ports=port)
            events.append((target,readonly,result['status'],len(port.calls)-before))
    monkeypatch.setattr(batch,'run',execute)
    return clients,events


def test_list_updates_automatically_requalify_only_stale_owner_then_zero_call_resume(claims,monkeypatch):
    store,root=shared(claims);clients,events=executor(monkeypatch,store)
    original={cid:store.get_record_state('claims',cid) for cid in ['C','D']}
    result=intake.run(['C','D'],root,store=store,instruction='finish complete shared-object Updates')
    assert result['all_actions_completed'],json.dumps({'results':result['results'],'refresh':result.get('completion_refresh')},ensure_ascii=False)
    assert all(v['qualification']['ready'] for v in result['claims'].values())
    assert [(k,r) for k,r,_,_ in events]==[('claims/C',False),('claims/D',False),('claims/C',True)]
    assert [p.calls for p in clients.values()]==[['opus'],['opus','gpt','opus'],['opus']]
    assert {cid:store.get_record_state('claims',cid) for cid in ['C','D']}==original
    assert store.get_record_state('evidence_steps','E')['revision']==2
    snapshot={k:deepcopy(store.get_cvp_group('CMA-'+cm.sha256_json(k))['call_reservations']) for k in clients}
    assert intake.run(['C','D'],root,store=store,instruction='finish complete shared-object Updates')==result
    assert snapshot=={k:store.get_cvp_group('CMA-'+cm.sha256_json(k))['call_reservations'] for k in clients}
    assert len(events)==3
    plan=json.loads((root/'completion-currentness/plan.json').read_text())
    job=json.loads(Path(plan['manifest']['path']).read_text())['jobs'][0]
    request=cm.Request.model_validate_json(Path(job['request']['path']).read_text())
    auth=json.loads(Path(request.authorization.path).read_text())
    origin=json.loads(Path(auth['completion_refresh']['path']).read_text())
    assert origin['owner_action_id'] in snapshot and origin['target_keys']==['claims/C']
    assert not list((root/'completion-currentness').glob('**/source-change*'))


@pytest.mark.parametrize('mode',['disagree','unknown'])
def test_failed_currentness_never_allocates_a_new_pass_or_replays_calls(claims,monkeypatch,mode):
    store,root=shared(claims);clients,events=executor(monkeypatch,store,mode=mode)
    result=intake.run(['C','D'],root,store=store,instruction='finish Updates')
    assert not result['all_actions_completed'] and result['status']=='paused'
    readonly=[p for aid,p in clients.items() if aid.startswith('claim-currentness-')]
    assert len(readonly)==1
    calls=list(readonly[0].calls)
    assert calls==(['opus','gpt','opus'] if mode=='disagree' else ['opus'])
    count=len(clients)
    resumed=intake.run(['C','D'],root,store=store,instruction='finish Updates')
    assert not resumed['all_actions_completed']
    assert len(clients)==count and readonly[0].calls==calls
    assert store.get_record_state('claims','C')['revision']==2
    assert store.get_record_state('evidence_steps','E')['revision']==2


@pytest.mark.parametrize('defect',['target','evidence','hold','source'])
def test_unowned_external_changes_do_not_authorize_new_reviews(claims,monkeypatch,defect):
    store,root=shared(claims);clients,_=executor(monkeypatch,store)
    result=intake.run(['C'],root,store=store,instruction='Update C')
    assert result['all_actions_completed']
    _,jobs=batch.load(root/'manifest.json',root/'batch',store=store)
    if defect=='source':
        p=Path(store.get_record_state('source_documents','S')['payload']['source_path']);p.write_text(p.read_text()+' ')
    else:
        col,oid=('evidence_steps','E') if defect=='evidence' else ('claims','C')
        p=store.get_record_state(col,oid)['payload']
        p.update({'review_status':'human_hold'} if defect=='hold' else {'statement':'different content'})
        store.apply_plan(store.plan_package({'package_id':'outside-batch',col:[p]}))
    before={k:len(p.calls) for k,p in clients.items()}
    result=refresh.run(store,jobs,root/'outside-checkpoint',instruction='finish C')
    assert not result['all_actions_completed'] and result['completion_refresh']['status']=='paused'
    assert before=={k:len(p.calls) for k,p in clients.items()}
    assert not (root/'outside-checkpoint/manifest.json').exists()


@pytest.mark.parametrize('interrupt',[False,True])
def test_source_second_paragraph_requalifies_first_before_reporting_complete(action,monkeypatch,interrupt):
    from backend.tests.test_source_claim_updates import fixture
    from backend.pipeline import source_claim_updates as source_updates
    store,old,cid,root=fixture(action)
    d=store.get_record_state('claims','D')['payload'];d['evidence_step_ids'].append('EA')
    ea=store.get_record_state('evidence_steps','EA')['payload'];ea['produced_claim_ids'].append('D');ea['statement']='中保。'
    store.apply_plan(store.plan_package({'package_id':'cross-paragraph-shared-evidence','claims':[d],'evidence_steps':[ea]}))
    clients={};events=[]
    def execute(manifest,output):
        plan,jobs=batch.load(manifest,output,store=store)
        for aid,(req,execution) in jobs.items():
            target=req.target_keys[0];readonly=aid.startswith('claim-currentness-');events.append((target,readonly))
            if target=='claims/D':
                assert json.loads((root/'paragraphs/S0001/result.json').read_text())['all_actions_completed']
                assert all(store.claim_management_qualification(x)['ready'] for x in ['A','B','C'])
            fid='FD' if target=='claims/D' else 'FQ';paragraph='S0002' if fid=='FD' else 'S0001'
            def cite(packet,p=paragraph):return {'source_id':'S','paragraph_key':p,'exact_quote':packet['sources']['S']['paragraphs'][p]['text']}
            def resolutions(packet):return [{'finding_id':f,'disposition':'resolved','reason':'current shared source and evidence',
                'citations':[cite(packet)],'affected_target_keys':[target],
                'affected_write_keys':['source_fragments/'+fid]+(['evidence_steps/EA'] if target=='claims/D' else []),
                'impact_reason':'source quotation and shared evidence refreshed'} for f in packet['unresolved']]
            def review(packet):
                result=pass_review(packet);rows={k:v['payload'] for k,v in packet['before'].items()}
                rows.update(cm.source.index(cm.Candidate.model_validate(packet['candidate']).records))
                if rows['source_fragments/'+fid]['verbatim_excerpt']!=cite(packet)['exact_quote']:
                    result['findings']=[{'target_key':'source_fragments/'+fid,'component':'source','category':'evidence',
                        'reason':'old quotation','acceptance_condition':'current quotation','citations':[cite(packet)],
                        'affected_target_keys':[target],'affected_write_keys':[],'impact_reason':'current source required'}]
                result['prior_findings']=resolutions(packet);return result
            def proposal(packet):
                changes=[{'record_key':'source_fragments/'+fid,'fields_json':json.dumps({'verbatim_excerpt':cite(packet)['exact_quote']}),
                    'reason':'current quotation','citations':[cite(packet)]}]
                if target=='claims/D':changes.append({'record_key':'evidence_steps/EA','fields_json':json.dumps({'statement':'教授说中保。'}),
                    'reason':'shared evidence full wording','citations':[cite(packet,'S0003')]})
                return {'changes':changes,'creations':[],'retirements':[],'findings':[],'finding_resolutions':resolutions(packet)}
            port=clients.setdefault(aid,SyntheticPorts(store,execution,opus=review,proposal=proposal))
            assert execute_claim_management(store,req,root=execution,ports=port)['status']=='completed'
    monkeypatch.setattr(batch,'run',execute)
    if interrupt:
        original_run=refresh.run;interrupted=[]
        def crash_after_second_commit(store,jobs,output,**kwargs):
            if Path(output)==root/'completion-currentness'/'S0002' and not interrupted:
                interrupted.append(True);raise RuntimeError('simulated coordinator interruption after p2 commit')
            return original_run(store,jobs,output,**kwargs)
        monkeypatch.setattr(refresh,'run',crash_after_second_commit)
        with pytest.raises(RuntimeError,match='simulated coordinator'):
            source_updates.run(store,'S',old,cid,root,'complete ordered source Updates')
        assert (root/'completed-paragraphs/S0001.json').exists()
        assert not store.claim_management_qualification('A')['ready']
    result=source_updates.run(store,'S',old,cid,root,'complete ordered source Updates')
    assert result['status']=='claims_completed',result
    assert events==[('claims/A',False),('claims/B',False),('claims/C',False),('claims/D',False),('claims/A',True)]
    assert all(q['ready'] for q in result['current_qualifications'].values())
    assert result['completed_paragraphs']==['S0001','S0002']
    assert [p.calls for p in clients.values()]==[['opus','gpt','opus'],['opus'],['opus'],['opus','gpt','opus'],['opus']]
    result_bytes=(root/'result.json').read_bytes()
    assert source_updates.run(store,'S',old,cid,root,'complete ordered source Updates')==result
    assert (root/'result.json').read_bytes()==result_bytes
    assert len(events)==5


def test_new_claim_gets_its_own_current_proof_after_a_later_shared_write(claims,monkeypatch):
    store,root=shared(claims)
    e=store.get_record_state('evidence_steps','E')['payload'];e['produced_claim_ids'].append('B')
    store.apply_plan(store.plan_package({'package_id':'later-consumer',
        'claims':[{'claim_id':'B','statement':'甲是假设。','claim_type':'exegesis',
            'occurrences':[{'source_id':'S','anchors':[]}],'evidence_step_ids':['E']}],'evidence_steps':[e]}))
    clients,events=executor(monkeypatch,store,create=True)
    result=intake.run(['C','D','B'],root,store=store,instruction='complete Updates and new Claims on final shared evidence')
    assert result['all_actions_completed'],result.get('completion_refresh') or result['results']
    assert len(result['created_keys'])==1
    created=result['created_keys'][0]
    assert all(q['ready'] for q in result['current_qualifications'].values())
    readonly=[(target,calls) for target,read,_,calls in events if read]
    assert {target for target,_ in readonly}=={'claims/C','claims/D',created}
    assert all(calls==1 for _,calls in readonly)
    assert store.get_record_state('claims',created.split('/',1)[1])['revision']==1
    assert store.get_record_state('evidence_steps','E')['revision']==4
    assert intake.run(['C','D','B'],root,store=store,instruction='complete Updates and new Claims on final shared evidence')==result
    assert len(events)==6


def test_worker_rejects_frozen_currentness_input_drift_before_any_call(claims,monkeypatch):
    store,root=shared(claims);clients,_=executor(monkeypatch,store)
    dispatch=batch.run;changed=[]
    def modify_before_readonly_worker(manifest,output):
        if Path(output)==root/'completion-currentness/batch' and not changed:
            changed.append(True)
            e=store.get_record_state('evidence_steps','E')['payload'];e['statement']='outside edit after freeze'
            store.apply_plan(store.plan_package({'package_id':'after-refresh-freeze','evidence_steps':[e]}))
        return dispatch(manifest,output)
    monkeypatch.setattr(batch,'run',modify_before_readonly_worker)
    result=intake.run(['C','D'],root,store=store,instruction='finish safely')
    assert not result['all_actions_completed']
    assert 'completion currentness input/scope' in result['completion_refresh']['detail']
    readonly=[p for aid,p in clients.items() if aid.startswith('claim-currentness-')]
    assert len(readonly)==1 and readonly[0].calls==[]
    assert store.get_record_state('claims','C')['revision']==2
