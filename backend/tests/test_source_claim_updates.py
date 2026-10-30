"""Use ordinary Claim Update contracts on changed shared Question evidence."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from backend.pipeline import claim_management as cm, claim_management_batch as batch
from backend.pipeline import source_claim_updates as updates
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_commit import binding
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_management_intake import pass_review


def fixture(action):
    store,request,_,_,root,_=action
    path=Path(request.source_bindings['S'].path)
    rows=[{'index':1,'text':'耶稣是摩西的中保。'},{'index':2,'text':'后来会被证明。'},{'index':3,'text':'教授说中保。'}]
    path.write_text(json.dumps(rows,ensure_ascii=False))
    doc=deepcopy(store.get_record_state('source_documents','S')['payload']);doc['source_file_sha256']=binding(path)['file_sha256']
    store.apply_plan(store.plan_package({'package_id':'old-source','source_documents':[doc],
        'claims':[{'claim_id':c,'claim_type':'exegesis','statement':'教授说中保。','evidence_step_ids':['E'+c],
                   'occurrences':[{'source_id':'S','anchors':[]}]} for c in ['A','B','C','D','N']],
        'evidence_steps':[{'evidence_step_id':'E'+c,'statement':'教授说中保。','produced_claim_ids':[c],
                          'source_fragment_ids':['FD' if c=='D' else 'FS'],
                          'speaker':'professor','stance':'asserted','support_eligibility':'eligible_candidate'} for c in ['A','B','C','D','N']],
        'source_fragments':[{'fragment_id':f,'source_id':'S','paragraph_key':f'S{i:04d}','verbatim_excerpt':rows[i-1]['text']} for f,i in [('FQ',1),('FD',2),('FS',3)]],
        'questions':[{'question_id':'Q','text':'中保有矛盾吗？','answer_claim_ids':['A','B','C'],'source_fragment_ids':['FQ']}],
        'claim_relations':[{'claim_relation_id':'NA','from_id':'N','to_id':'A','relation_type':'supports','reason':'direct neighbour only'}]}))
    old=root/'before.json';old.write_bytes(path.read_bytes())
    rows[0]['text']='摩西是中保。';rows[1]['text']='教授否定这个译法。'
    path.write_text(json.dumps(rows,ensure_ascii=False));doc['source_file_sha256']=binding(path)['file_sha256']
    plan=store.plan_package({'package_id':'registered-source','source_documents':[doc]});store.apply_plan(plan)
    return store,old,plan.change_set_id,root/'source-updates'


def test_shared_question_owners_are_selected_but_claim_neighbour_is_not(action):
    store,old,cid,root=fixture(action)
    plan=updates.prepare(store,'S',old,cid,root,'source corrections')
    assert plan['changed_paragraphs']==['S0001','S0002']
    assert plan['paragraphs']==[{'paragraph_key':'S0001','claim_ids':['A','B','C']},{'paragraph_key':'S0002','claim_ids':['D']}]
    assert plan['unowned_paragraphs']==[]
    assert 'source_fragments/FQ' in plan['dependencies']['claims/B']['S0001']
    assert store.get_record_state('source_fragments','FQ')['payload']['verbatim_excerpt']=='耶稣是摩西的中保。'
    assert not list(root.glob('paragraphs/*/prepared.json'))  # planning never dispatches or grants identity
    assert updates.prepare(store,'S',old,cid,root,'source corrections')==plan


def test_complete_shared_object_refresh_then_fresh_next_paragraph_and_zero_call_resume(action,monkeypatch):
    store,old,cid,root=fixture(action);clients={};events=[]
    original={c:store.get_record_state('claims',c)['payload']['statement'] for c in ['A','B','C','D']}
    def execute(manifest,output):
        for key,(request,execution) in batch.load(manifest,output,store=store)[1].items():
            target=request.target_keys[0];events.append(target)
            fid='FD' if target=='claims/D' else 'FQ';paragraph='S0002' if fid=='FD' else 'S0001'
            if target=='claims/D':
                assert store.get_record_state('source_fragments','FQ')['payload']['verbatim_excerpt']=='摩西是中保。'
                assert all(store.claim_management_qualification(c)['ready'] for c in ['A','B','C'])
            def citation(packet):return {'source_id':'S','paragraph_key':paragraph,'exact_quote':packet['sources']['S']['paragraphs'][paragraph]['text']}
            def resolutions(packet):return [{'finding_id':f,'disposition':'resolved','reason':'refresh shared source quotation',
                'citations':[citation(packet)],'affected_target_keys':[target],
                'affected_write_keys':['source_fragments/'+fid],'impact_reason':'current shared source binding'} for f in packet['unresolved']]
            def review(packet):
                result=pass_review(packet);rows={k:v['payload'] for k,v in packet['before'].items()}
                rows.update(cm.source.index(cm.Candidate.model_validate(packet['candidate']).records))
                if rows['source_fragments/'+fid]['verbatim_excerpt']!=citation(packet)['exact_quote']:
                    if fid=='FD':
                        for assessment in result['binding_assessments']:
                            if assessment['claim_key']==target:assessment['sufficient']=False
                    result['findings']=[{'target_key':'source_fragments/'+fid,'component':'source_binding','category':'mapping',
                        'reason':'old shared quotation is no longer in the source','acceptance_condition':'use current exact source',
                        'citations':[citation(packet)],'affected_target_keys':[target],'affected_write_keys':[],
                        'impact_reason':'related Question or Evidence needs refresh even though statement is unchanged'}]
                result['prior_findings']=resolutions(packet)
                return result
            def proposal(packet):return {'changes':[{'record_key':'source_fragments/'+fid,
                'fields_json':json.dumps({'verbatim_excerpt':citation(packet)['exact_quote']}),
                'reason':'refresh shared evidence','citations':[citation(packet)]}],
                'creations':[],'retirements':[],'findings':[],'finding_resolutions':resolutions(packet)}
            client=clients.setdefault(key,SyntheticPorts(store,execution,opus=review,proposal=proposal))
            result=execute_claim_management(store,request,root=execution,ports=client)
            assert result['status']=='completed',result
    monkeypatch.setattr(batch,'run',execute)
    result=updates.run(store,'S',old,cid,root,'full refresh')
    assert result['status']=='claims_completed',result
    assert result['downstream_handoff']['status']=='not_executed'
    assert result['downstream_handoff']['claim_ids']==['A','B','C','D']
    assert result['downstream_handoff']['retired_claim_ids']==[]
    assert events==['claims/A','claims/B','claims/C','claims/D']
    assert {c:store.get_record_state('claims',c)['payload']['statement'] for c in original}==original
    assert [c.calls for c in clients.values()]==[['opus','gpt','opus'],['opus'],['opus'],['opus','gpt','opus']]
    assert updates.run(store,'S',old,cid,root,'full refresh')==result
    assert len(events)==4


def test_current_source_drift_blocks_without_preparing_claim_batches(action):
    store,old,cid,root=fixture(action);updates.prepare(store,'S',old,cid,root,'refresh')
    doc=store.get_record_state('source_documents','S')['payload'];Path(doc['source_path']).write_text('[]')
    with pytest.raises(ValueError):updates.run(store,'S',old,cid,root,'refresh')
    assert not (root/'paragraphs').exists()


@pytest.mark.parametrize('citation_error',[False,'current','before','foreign'])
def test_unaccepted_fragment_review_recovers_same_raw_and_then_normal_revision(action,monkeypatch,citation_error):
    from backend.pipeline import claim_management_intake as intake,claim_evidence_review as evidence
    from backend.pipeline.claim_review_recovery import recover_returned_review,VERSION,resume_invalid_initial_review,INVALID_REVIEW_VERSION
    store,old,cid,root=fixture(action);root=root/'single-D'
    citation={'source_id':'S','paragraph_key':'S0002','exact_quote':'教授否定这个译法。'}
    def resolutions(packet):return [{'finding_id':f,'disposition':'resolved','reason':'bound excerpt repaired',
        'citations':[citation],'affected_target_keys':['claims/D'],'affected_write_keys':['source_fragments/FD'],
        'impact_reason':'own bound evidence refreshed'} for f in packet['unresolved']]
    def review(packet):
        value=pass_review(packet)
        if not packet['unresolved']:
            for a in value['binding_assessments']:
                if a['claim_key']=='claims/D':a['sufficient']=False
            value['findings']=[{'target_key':'source_fragments/FD','component':'binding','category':'evidence',
                'reason':'old bound excerpt is no longer current','acceptance_condition':'refresh own bound excerpt',
                'citations':[citation],'affected_target_keys':['claims/D'],'affected_write_keys':[],
                'impact_reason':'own source evidence is stale'}]
        if citation_error and not packet['unresolved']:
            value['findings'][0]['citations']=[{**citation,'exact_quote':'教授否定这个译法TYPO。',
                'source_epoch':'before' if citation_error=='before' else 'current',
                'source_id':'MISSING' if citation_error=='foreign' else 'S'}]
        value['prior_findings']=resolutions(packet);return value
    def proposal(packet):
        if citation_error:
            original=packet['original_goal']['unaccepted_review_inputs'][0]['response']
            assert original['findings'][0]['citations'][0]['exact_quote']=='教授否定这个译法TYPO。'
        return {'changes':[{'record_key':'source_fragments/FD',
        'fields_json':json.dumps({'verbatim_excerpt':citation['exact_quote']}),'reason':'refresh quotation','citations':[citation]}],
        'creations':[],'retirements':[],'findings':[],'finding_resolutions':resolutions(packet)}
    clients={}
    def execute(manifest,output):
        for key,(request,execution) in batch.load(manifest,output,store=store)[1].items():
            client=clients.setdefault(key,SyntheticPorts(store,execution,opus=review,proposal=proposal))
            execute_claim_management(store,request,root=execution,ports=client)
    monkeypatch.setattr(batch,'run',execute)
    current=evidence.validate_assessments
    def legacy(review,materials,candidate):
        if not citation_error and any(not a.sufficient for a in review.binding_assessments):
            raise ValueError('insufficient own Evidence requires a concrete Claim finding')
        return current(review,materials,candidate)
    monkeypatch.setattr(evidence,'validate_assessments',legacy)
    first=intake.run(['D'],root,store=store,instruction='refresh source')
    assert first['status']=='paused'
    aid=first['claims']['D']['action_id'];parent=store.get_cvp_group('CMA-'+cm.sha256_json(aid))
    rid=next(iter(parent['latest_rounds'].values()));saved=store.get_cvp_group(rid)
    raw=Path(parent['execution_root'])/rid/'opus.raw.json';original_raw=raw.read_bytes()
    monkeypatch.setattr(evidence,'validate_assessments',current)
    auth=root/'engineering-direction.json';auth.write_text(json.dumps({'schema_version':INVALID_REVIEW_VERSION if citation_error else VERSION,'round_id':rid,
        'authorized_by':'owner','authorization_quote':'fix the bound-excerpt validator and resume original result',
        'human_content_approval':False,'record_unaccepted_result':True,
        'prior_metadata_sha256':cm.sha256_json({k:v for k,v in saved.items() if k not in {'run_id','status','error'}})}))
    if citation_error=='foreign':
        with pytest.raises(ValueError,match='cannot invent a source or paragraph'):
            resume_invalid_initial_review(store,rid,authorization=binding(auth))
        assert store.get_cvp_group(rid)==saved and raw.read_bytes()==original_raw
        return
    recovery=(resume_invalid_initial_review(store,rid,authorization=binding(auth)) if citation_error else
        recover_returned_review(store,rid,authorization=binding(auth),unrecorded=True))
    record=json.loads(Path(recovery,'engineering-recovery.json').read_text())
    assert record['model_calls']==0
    assert record['review_not_accepted'] if citation_error else record['returned_review_passed'] is False
    assert raw.read_bytes()==original_raw
    if not citation_error:assert Path(recovery,'opus.raw.json').read_bytes()==original_raw
    final=intake.run(['D'],root,store=store,instruction='refresh source')
    assert final['all_actions_completed'],final
    assert next(iter(clients.values())).calls==['opus','gpt','opus']
    assert store.get_record_state('source_fragments','FD')['payload']['verbatim_excerpt']==citation['exact_quote']
    if citation_error:
        from backend.pipeline.claim_review_recovery import consumed_invalid_review
        parent=store.get_cvp_group('CMA-'+cm.sha256_json(aid))
        key=rid+':opus';slot=parent['call_reservations'][key]
        assert consumed_invalid_review(parent,key,slot)
        assert slot['state']=='started' and raw.read_bytes()==original_raw
        tampered=deepcopy(parent)
        tampered['original_goal']['unaccepted_review_inputs'][0]['response']['findings'][0]['reason']='changed'
        with pytest.raises(ValueError,match='response drift'):
            consumed_invalid_review(tampered,key,slot)
        assert not consumed_invalid_review(parent,rid+':gpt',{'role':'gpt','state':'started','round_id':'unrelated'})
        # A read-only successor drops every creation/write grant and still
        # charges the authentic rejected initial reply, without accepting it.
        metadata={k:v for k,v in parent.items() if k not in {'run_id','status','error'}}
        metadata['context_findings']={'context':{'target_key':'claims/N'}}
        request=cm.Request.model_validate(parent['request'])
        assert request.creation_permissions
        request.action_id+='-readback';request.predecessor_action_id=aid
        request.permissions=[];request.creation_permissions=[];request.allowed_operations=['Update']
        policy=cm.Policy.model_validate(parent['policy']);policy.initial_candidate='proposal'
        policy.budget.gpt_calls=1;policy.budget.opus_calls=1;policy.budget.max_revisions=1
        policy_path=root/'refresh-policy.json';policy_path.write_text(policy.model_dump_json())
        request.policy=cm.source.FileBinding.model_validate(binding(policy_path))
        auth_path=root/'refresh-authorization.json';request.authorization=cm.source.FileBinding(path=str(auth_path),file_sha256='0'*64)
        ext={'predecessor_action_id':aid,'prior_metadata_sha256':cm.sha256_json(metadata),'budget':policy.budget.model_dump(mode='json'),
             'authorization_quote':'finish with a bounded read-only refresh','basis':'qualification_refresh',
             'current_objects_sha256':'bound separately at SQL admission','related_action_metadata_sha256':{}}
        auth_path.write_text(json.dumps({'authorized_by':'owner','human_content_approval':False,'owner_call_extension':ext,'required_maintenance_keys':[]}))
        request.authorization=cm.source.FileBinding.model_validate(binding(auth_path))
        continuation=cm.continuation_budget(metadata,request,policy)
        assert continuation['read_only_refresh'] and continuation['spent_generations']=={'gpt':1,'opus':2,'gemini':0}
        metadata['created_keys']=['claims/NEW']
        ext['prior_metadata_sha256']=cm.sha256_json(metadata)
        auth_path.write_text(json.dumps({'authorized_by':'owner','human_content_approval':False,'owner_call_extension':ext,'required_maintenance_keys':[]}))
        request.authorization=cm.source.FileBinding.model_validate(binding(auth_path))
        continuation=cm.continuation_budget(metadata,request,policy)
        assert continuation['predecessor_created_keys']==['claims/NEW']
        assert not request.permissions and not request.creation_permissions


def test_retired_claims_are_separate_from_ready_handoff(action,monkeypatch):
    store,old,cid,root=fixture(action)
    monkeypatch.setattr(updates.intake,'run',lambda *a,**k:{'all_actions_completed':True})
    final={'all_actions_completed':True,'current_qualifications':{'claims/A':{'ready':True},'claims/D':None,'claims/X':{'ready':False}},'retired_keys':['claims/D']}
    monkeypatch.setattr(batch,'_paragraph_results',lambda *a:deepcopy(final))
    plan=updates.prepare(store,'S',old,cid,root,'refresh')
    for group in plan['paragraphs']:
        p=root/'paragraphs'/group['paragraph_key']/'prepared.json';p.parent.mkdir(parents=True);p.write_text('{"manifest":null}')
    result=updates.run(store,'S',old,cid,root,'refresh')
    assert result['downstream_handoff']=={'status':'not_executed','claim_ids':['A'],'retired_claim_ids':['D']}


@pytest.mark.parametrize('defect',['current','opus','foreign','drift','budget','repeat','tamper','missing','intent','creation','duplicate','missing_reference','transaction_consumer','transaction_consumer_opus','evidence_drift','scope_drift'])
def test_agent_resume_rejected_proposal_keeps_raw_budget_and_requires_real_review(action,monkeypatch,defect):
    from backend.pipeline import claim_management_intake as intake
    from backend.pipeline.claim_review_recovery import consumed_rejected_citations
    store,old,cid,base=fixture(action)
    consumer = defect in {'transaction_consumer','transaction_consumer_opus'}
    if consumer or defect=='scope_drift':
        original_reader=store.read_claim_context
        def read_with_consumer(*args,**kwargs):
            records=original_reader(*args,**kwargs)
            records.update(original_reader([('claims','N')]))
            return records
        monkeypatch.setattr(store,'read_claim_context',read_with_consumer)
    root=base/'proposal-quote';citation={'source_id':'S','paragraph_key':'S0002','exact_quote':'教授否定这个译法。'}
    def resolutions(packet,bad=False):
        return [{'finding_id':fid,'disposition':'resolved','reason':'repair exact source binding',
            'citations':[{**citation,**({'exact_quote':'教授否定这个译法TYPO。','source_id':'FOREIGN' if defect=='foreign' else 'S'} if bad else {})}],
            'affected_target_keys':['claims/D'],'affected_write_keys':['source_fragments/FD'],'impact_reason':'own bound evidence'} for fid in packet['unresolved']]
    def proposal(packet):
        inputs=packet['original_goal'].get('unaccepted_model_inputs')
        if inputs:
            original=inputs[0]['response']
            if defect=='creation':
                assert json.loads(original['creations'][0]['fields_json'])['visibility']=='internal'
            elif defect=='duplicate':
                assert original['changes'][0]['record_key']==original['changes'][1]['record_key']
            elif defect=='missing_reference':
                assert json.loads(original['changes'][1]['fields_json'])['source_fragment_ids']==['MISSING']
            else:
                original_item=original['findings'][0] if inputs[0]['role']=='opus' else original['finding_resolutions'][0]
                assert original_item['citations'][0]['exact_quote'].endswith('TYPO。')
        creations=[]
        if defect=='creation' and not inputs:
            oid=packet['original_goal']['claim_creation_allocations']['S']
            creations=[{'collection':'claims','object_id':oid,'reason':'new source judgment','citations':[citation],
                'fields_json':json.dumps({'statement':'教授否定这个译法。','claim_type':'explicit_claim','attribution':'professor',
                    'occurrences':[{'source_id':'S'}],'evidence_step_ids':['ED'],'visibility':'internal'})}]
        changes=[{'record_key':'source_fragments/FD','fields_json':json.dumps({'verbatim_excerpt':citation['exact_quote']}),
            'reason':'repair bound excerpt','citations':[citation]}]
        if defect=='duplicate' and not inputs:changes+=deepcopy(changes)
        if defect=='missing_reference' and not inputs:
            changes.extend([{'record_key':'evidence_steps/ED','fields_json':json.dumps({'source_fragment_ids':['MISSING']}),
                'reason':'returned malformed reference','citations':[citation]},
                {'record_key':'claims/D','fields_json':json.dumps({'statement':citation['exact_quote']}),
                 'reason':'source-bound judgment','citations':[citation]}])
        return {'changes':changes,'creations':creations,'retirements':[],'findings':[],
            'finding_resolutions':resolutions(packet,bad=(not inputs or defect=='repeat') and defect not in {'creation','duplicate','missing_reference'})}
    def review(packet):
        value=pass_review(packet);value['prior_findings']=resolutions(packet)
        if not packet['unresolved']:
            for assessment in value['binding_assessments']:
                if assessment['claim_key']=='claims/D':assessment['sufficient']=False
            value['findings']=[{'target_key':'source_fragments/FD','category':'evidence','component':'binding',
                'reason':'bound excerpt stale','acceptance_condition':'repair exact excerpt','citations':[citation],
                'affected_target_keys':['claims/D'],'affected_write_keys':[],'impact_reason':'own evidence'}]
        if defect in {'opus','transaction_consumer_opus'} and not packet['unresolved']:
            value['findings'][0]['citations']=[{**citation,'exact_quote':'教授否定这个译法TYPO。'}]
        return value
    clients={}
    def execute(manifest,output):
        for key,(request,execution) in batch.load(manifest,output,store=store)[1].items():
            port=clients.setdefault(key,SyntheticPorts(store,execution,opus=review,proposal=proposal))
            execute_claim_management(store,request,root=execution,ports=port)
    monkeypatch.setattr(batch,'run',execute)
    policy=json.loads(intake.DEFAULT_POLICY.read_text())
    if defect in {'budget','repeat'}:policy['budget']['gpt_calls']=1 if defect=='budget' else 2
    policy_path=root/'selected-policy.json';policy_path.parent.mkdir(parents=True);policy_path.write_text(json.dumps(policy))
    first=intake.run(['D'],root,store=store,instruction='full Update and repair engineering errors',policy=policy_path)
    assert first['status']=='paused'
    aid=first['claims']['D']['action_id'];parent=store.get_cvp_group('CMA-'+cm.sha256_json(aid));rid=next(iter(parent['latest_rounds'].values()))
    role='opus' if defect in {'opus','transaction_consumer_opus'} else 'gpt'
    raw=Path(parent['execution_root'])/rid/(role+'.raw.json');raw_bytes=raw.read_bytes();before=store.get_record_state('source_fragments','FD')
    initial_calls=['opus'] if defect in {'opus','transaction_consumer_opus'} else ['opus','gpt']
    assert next(iter(clients.values())).calls==initial_calls
    if defect=='drift':
        record=store.get_record_state('claims','D')['payload'];record['statement']='external change'
        store.apply_plan(store.plan_package({'package_id':'external-drift','claims':[record]}))
    if consumer or defect=='scope_drift':
        child=store.get_cvp_group(rid)
        assert 'evidence_steps/EN' in child['materials']['transaction_before']
        assert 'evidence_steps/EN' not in child['materials']['before']
    if consumer or defect=='evidence_drift':
        eid='EN' if consumer else 'ED'
        record=deepcopy(store.get_record_state('evidence_steps',eid)['payload']);record['statement']='later evidence correction'
        store.apply_plan(store.plan_package({'package_id':'later-evidence-drift','evidence_steps':[record]}))
        if consumer:consumer_after=store.get_record_state('evidence_steps','EN')
    if defect=='scope_drift':
        store.apply_plan(store.plan_package({'package_id':'new-direct-neighbour','claim_relations':[
            {'claim_relation_id':'ND','from_id':'N','to_id':'D','relation_type':'supports'}]}))
    if defect=='intent':
        from backend.api.canonical_repository.postgres_store import canonical_json
        broken=deepcopy({k:v for k,v in parent.items() if k not in {'run_id','status','error'}})
        broken['call_reservations'][rid+':gpt']['intent_sha256']='0'*64
        with store.connect() as conn:
            conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',(canonical_json(broken),parent['run_id']))
    if defect=='tamper':
        raw.write_text(raw.read_text().replace('TYPO','FORGED'));raw_bytes=raw.read_bytes()
    if defect=='missing':raw.unlink()
    final=intake.run(['D'],root,store=store,instruction='full Update and repair engineering errors',policy=policy_path)
    assert not raw.exists() if defect=='missing' else raw.read_bytes()==raw_bytes
    after=store.get_cvp_group('CMA-'+cm.sha256_json(aid))
    assert after['request']==parent['request'] and after['policy']==parent['policy']
    if defect not in {'current','opus','creation','duplicate','missing_reference','transaction_consumer','transaction_consumer_opus'}:
        expected_calls=initial_calls+(['gpt'] if defect=='repeat' else [])
        assert final['status']=='paused' and next(iter(clients.values())).calls==expected_calls
        assert store.get_record_state('source_fragments','FD')==before
        if defect=='repeat':
            from backend.api.canonical_repository.postgres_store import canonical_json
            latest = next(iter(after['latest_rounds'].values()))
            error = 'citation is not an exact quote from the complete own source'
            assert after['result']['detail'] == error
            current_slot = after['call_reservations'][latest + ':gpt']
            assert current_slot['rejection']['detail'] == error
            # Reconcile the exact diagnostic pollution produced by the old
            # executor; authenticating this never accepts the proposal.
            broken = deepcopy({k:v for k,v in after.items() if k not in {'run_id','status','error'}})
            polluted = error + '; returned-answer authentication failed: returned rejection cannot relabel an accepted, committed or superseded result'
            broken['call_reservations'][latest + ':gpt']['rejection']['detail'] = polluted
            with store.connect() as conn:
                conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s', (canonical_json(broken), after['run_id']))
            with pytest.raises(RuntimeError):
                store.record_claim_management_returned_rejection(latest, role='gpt', detail=error)
            with pytest.raises(RuntimeError):
                store.record_claim_management_returned_rejection(latest, role='gpt', detail='forged validation error', repair_recorded_detail=True)
            store.record_claim_management_returned_rejection(latest, role='gpt', detail=error, repair_recorded_detail=True)
            repaired = store.get_cvp_group(after['run_id'])['call_reservations'][latest + ':gpt']
            assert repaired['rejection']['detail'] == error
            assert repaired['rejection_history'][-1]['detail'] == polluted
            assert repaired['state'] == 'started'
            third=intake.run(['D'],root,store=store,instruction='full Update and repair engineering errors',policy=policy_path)
            assert third['status']=='paused' and next(iter(clients.values())).calls==expected_calls
            assert store.get_record_state('source_fragments','FD')==before
            # The once-only owner supplement must also support a known
            # returned rejection with no accepted candidate at all.
            from backend.api.canonical_repository.claim_identity_allocation import reserve
            from backend.pipeline.cvp_sql_commit import binding
            prior = store.get_cvp_group(after['run_id'])
            store.pause_claim_management_action(prior['run_id'], reason='budget_exhausted', detail='complete next correction pair exceeds original budget')
            prior = {k:v for k,v in store.get_cvp_group(prior['run_id']).items() if k not in {'run_id','status','error'}}
            request = cm.Request.model_validate(prior['request']).model_copy(deep=True)
            request.predecessor_action_id = request.action_id
            request.action_id += '-verified-engineering-followup'
            output = root / 'engineering-followup'
            output.mkdir()
            selected = cm.Policy.model_validate(prior['policy'])
            selected.initial_candidate = 'proposal'
            selected.budget.gpt_calls = selected.budget.opus_calls = selected.budget.max_revisions = 1
            selected_path = output/'policy.json';selected_path.write_text(selected.model_dump_json())
            request.policy = cm.source.FileBinding.model_validate(binding(selected_path))
            records = store.read_claim_context([cm.source.split_key(k) for k in request.target_keys])
            current = {cm.source.key(*k):cm.model_state(v) for k,v in records.items()}
            authority = json.loads(Path(request.authorization.path).read_text())
            authority.update(request=request.model_dump(mode='json',exclude={'authorization','policy'}),policy=request.policy.model_dump(mode='json'),
                claim_creation_allocations={g.source_id:reserve(store,action_id=request.action_id,source_id=g.source_id,target_id='D',root=output,count=1) for g in request.creation_permissions},
                owner_call_extension={'basis':'remaining_findings','predecessor_action_id':request.predecessor_action_id,
                    'prior_metadata_sha256':cm.sha256_json(prior),'budget':selected.budget.model_dump(mode='json'),
                    'authorization_quote':'one correction after verified engineering repair','current_objects_sha256':cm.sha256_json(current),'related_action_metadata_sha256':{}})
            auth_path=output/'authority.json';auth_path.write_text(json.dumps(authority));request.authorization=cm.source.FileBinding.model_validate(binding(auth_path))
            def valid_proposal(packet):
                assert packet['previous_candidate'] is None
                rejected=packet['continuation']['unaccepted_returned_proposals'][latest+':gpt']
                assert rejected['review_not_accepted'] is True
                assert rejected['response']['finding_resolutions'][0]['citations'][0]['exact_quote'].endswith('TYPO。')
                return {'changes':[{'record_key':'source_fragments/FD','fields_json':json.dumps({'verbatim_excerpt':citation['exact_quote']}),
                    'reason':'repair exact quote','citations':[citation]}],'creations':[],'retirements':[],'findings':[],
                    'finding_resolutions':resolutions(packet)}
            port=SyntheticPorts(store,output,opus=review,proposal=valid_proposal)
            result=execute_claim_management(store,request,root=output,ports=port)
            assert result['status']=='completed',result
            assert port.calls==['gpt','opus']
            assert store.claim_management_qualification('D')['ready']
        else:assert not after['original_goal'].get('unaccepted_model_inputs')
        return
    assert final['all_actions_completed'],final
    assert next(iter(clients.values())).calls==initial_calls+['gpt','opus']
    assert after['call_reservations'][rid+':'+role]==parent['call_reservations'][rid+':'+role]
    if role=='gpt':assert after['call_reservations'][rid+':opus']['state']=='cancelled'
    assert consumed_rejected_citations(after,rid+':'+role,after['call_reservations'][rid+':'+role])
    if defect=='duplicate':assert parent['result']['detail']=='changed record repeated or absent from frozen input'
    if defect=='missing_reference':assert 'missing dependency: source_fragments/MISSING' in parent['result']['detail']
    if defect=='creation':
        assert parent['result']['detail']=='created fields exceed creation permission'
        assert not after.get('created_keys')
        assert after['request']['creation_permissions']==parent['request']['creation_permissions']
    assert store.get_record_state('source_fragments','FD')['payload']['verbatim_excerpt']==citation['exact_quote']
    assert store.claim_management_qualification('D')['ready']

    if consumer:
        child=store.get_cvp_group(rid)
        recovery=json.loads(Path(child['citation_input_recovery']['path']).read_text())
        proof=recovery['transaction_context_refresh']
        assert proof['changed_transaction_keys']==['evidence_steps/EN']
        assert proof['content_scope_sha256']==cm.sha256_json(child['materials']['before'])
        assert store.get_record_state('evidence_steps','EN')==consumer_after
