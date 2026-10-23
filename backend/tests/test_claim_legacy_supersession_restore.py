"""Restore old AI supersessions in the existing reviewed transaction flow."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_flow import binding
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import seed_claims,operation_request,SyntheticPorts
from backend.tests.test_claim_management_human_source_follow_up import authorize


@pytest.mark.parametrize('fault',[None,'second_round','retain_target','no_capability','not_ai','human','raw_approval'])
def test_restore_legacy_ai_supersession_needs_explicit_capability_and_preserves_teaching(action,fault):
    store,_,_,_,root,_=action;seed_claims(store,['C','A'])
    old=store.get_record_state('claims','C')['payload'];old=deepcopy(old)
    old.update(review_status='superseded',superseded_by='A',ai_adjudication={
        'status':'ai_consensus_applied','approval_status':'not_human_approved','fingerprint':'old-ai-decision'})
    if fault=='not_ai':old['ai_adjudication']['status']='unreviewed'
    from backend.api.canonical_repository.postgres_store import record_content_sha
    with store.connect() as conn:
        for table in ['objects','object_versions']:
            conn.execute(f"UPDATE wang_knowledge.{table} SET payload=%s::jsonb,content_sha256=%s WHERE collection='claims' AND object_id='C'",(json.dumps(old),record_content_sha(old)))
        conn.execute("UPDATE wang_knowledge.objects SET review_status='superseded' WHERE collection='claims' AND object_id='C'")
    if fault=='human':store.record_review('claims','C',decision='superseded',reviewer_kind='human')
    before=store.get_record_state('claims','C')
    request,resultroot=operation_request(action,'Restore',['C'])
    request.permissions[0].fields += ['superseded_by']
    policy=cm.Policy.model_validate_json(Path(request.policy.path).read_text());policy.independent_audit=False;policy.budget.gemini_calls=0
    if fault!='no_capability':policy.legacy_ai_supersession_restore=True
    pp=root/'restore-legacy-policy.json';pp.write_text(policy.model_dump_json());request.policy=cm.source.FileBinding.model_validate(binding(pp));authorize(request)
    def propose(packet):
        if fault=='retain_target':
            candidate=cm.existing_candidate(packet['before']);candidate.operation='Restore'
            return candidate.model_dump(mode='json')
        if fault=='second_round' and packet['round_number']:
            candidate=cm.Candidate.model_validate(packet['previous_candidate'])
            assert candidate.records[0].payload['review_status']=='candidate'
            candidate.records[0].payload['statement']='甲是假设，不是断言。'
            candidate.finding_resolutions=[cm.FindingResolution(finding_id=fid,disposition='resolved',reason='clarified from the complete source',
                citations=[cm.Citation.model_validate(f['citations'][0])]) for fid,f in packet['unresolved'].items()]
            return candidate.model_dump(mode='json')
        candidate=cm.existing_candidate(packet['before']);candidate.operation='Restore'
        disposition=next(d for d in candidate.dispositions if d.record_key=='claims/C')
        disposition.action='restore';disposition.reason='The old AI merge joined different judgments; restore the original identity.'
        disposition.citations=[cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='甲是假设，不是断言。')]
        payload=deepcopy(packet['before']['claims/C']['payload'])
        if fault=='raw_approval':payload['review_status']='human_approved'
        candidate.records=[cm.source.Row(collection='claims',object_id='C',payload=payload)]
        return candidate.model_dump(mode='json')
    from backend.tests.test_claim_management_execution_postgres import rejected,passing
    def review(packet):
        return rejected(packet,key='claims/C') if fault=='second_round' and not packet['round_number'] else passing(packet)
    ports=SyntheticPorts(store,root,proposal=propose,opus=review)
    if fault in {'human','not_ai'}:
        from backend.api.canonical_repository.postgres_store import ChangeSetConflict
        with pytest.raises(ChangeSetConflict,match='human decision' if fault=='human' else 'no exact AI supersession'):
            execute_claim_management(store,request,root=resultroot,ports=ports)
        assert ports.calls==[]
    else:
        result=execute_claim_management(store,request,root=resultroot,ports=ports)
        if fault not in {None,'second_round'}:
            assert result['status']=='paused',result
        else:
            assert result['status']=='completed',result
            after=store.get_record_state('claims','C')
            assert after['payload']['review_status']=='candidate' and after['payload']['superseded_by'] is None
            assert after['revision']==before['revision']+1
            mechanical={'review_status','superseded_by','revision'} | ({'statement'} if fault=='second_round' else set())
            assert {k:v for k,v in before['payload'].items() if k not in mechanical}=={
                k:v for k,v in after['payload'].items() if k not in mechanical}
            if fault=='second_round':assert after['payload']['statement']=='甲是假设，不是断言。'
            assert store.claim_management_qualification('C')['ready']
            assert ports.calls==(['gpt','opus']*2 if fault=='second_round' else ['gpt','opus'])
            return
    assert store.get_record_state('claims','C')==before


@pytest.mark.parametrize('fault',[None,'changed_scope','unrelated_target','late_failure'])
@pytest.mark.parametrize('direct_scope',[False,True])
def test_failed_update_can_handoff_unchanged_scope_to_atomic_restore_and_maintenance(action,fault,monkeypatch,direct_scope):
    from backend.tests.test_claim_management_execution_postgres import rejected
    from backend.api.canonical_repository.postgres_store import record_content_sha,ChangeSetConflict
    store,_,_,_,root,_=action
    store.apply_plan(store.plan_package({'package_id':'restore-shared',
        'claims':[{'claim_id':cid,'statement':'甲是假设。'+cid,'claim_type':'exegesis',
            'occurrences':[{'source_id':'S','anchors':[]}],'evidence_step_ids':['E']} for cid in ['A','B','C']],
        'evidence_steps':[{'evidence_step_id':'E','statement':'甲是假设。','produced_claim_ids':['A','B','C']}]}))
    payload=deepcopy(store.get_record_state('claims','C')['payload']);payload.update(review_status='superseded',superseded_by='A',
        ai_adjudication={'status':'ai_consensus_applied','approval_status':'not_human_approved','fingerprint':'legacy'})
    with store.connect() as conn:
        for table in ['objects','object_versions']:
            conn.execute(f"UPDATE wang_knowledge.{table} SET payload=%s::jsonb,content_sha256=%s WHERE collection='claims' AND object_id='C'",(json.dumps(payload),record_content_sha(payload)))
        conn.execute("UPDATE wang_knowledge.objects SET review_status='superseded' WHERE collection='claims' AND object_id='C'")
    old,oldroot=operation_request(action,'Update',['A'])
    policy_file=cm.DEFAULT_POLICY.parent/('wang_claim_management_opus_only_v3.json' if direct_scope else 'wang_claim_management_opus_only_v2.json')
    policy=cm.Policy.model_validate_json(policy_file.read_text());policy.independent_audit=False;policy.budget.gemini_calls=0;policy.initial_candidate='proposal';policy.all_findings_to_gpt=True
    pp=root/'old-failed-policy.json';pp.write_text(policy.model_dump_json());old.policy=cm.source.FileBinding.model_validate(binding(pp));authorize(old)
    def initial(packet):
        return {} if packet['round_number'] else cm.existing_candidate(packet['before']).model_dump(mode='json')
    result=execute_claim_management(store,old,root=oldroot,ports=SyntheticPorts(store,root,proposal=initial,opus=lambda p:rejected(p,key='claims/A')))
    assert result['reason']=='engineering_required',result
    prior=store.get_cvp_group('CMA-'+cm.sha256_json(old.action_id));assert prior['findings']
    if fault=='changed_scope':
        # B is a legacy transitive consumer, not part of the new one-hop
        # review. E is target evidence in both contracts. Drift a pinned row.
        collection,oid=('evidence_steps','E') if direct_scope else ('claims','B')
        changed=deepcopy(store.get_record_state(collection,oid)['payload']);changed['statement']='changed later'
        store.apply_plan(store.plan_package({'package_id':'subsequent-change',collection:[changed]}))
    request,newroot=operation_request(action,'Restore',['A','C']+(['B'] if fault=='unrelated_target' else []));request.allowed_operations=['Restore','Update']
    next(p for p in request.permissions if p.object_id=='C').fields.append('superseded_by')
    request.permissions.append(cm.source.Permission(collection='evidence_steps',object_id='E',fields=['statement']))
    policy.legacy_ai_supersession_restore=True;pp=root/'new-restore-policy.json';pp.write_text(policy.model_dump_json());request.policy=cm.source.FileBinding.model_validate(binding(pp))
    authorize(request,{'context_review_actions':{old.action_id:list(prior['findings'])},'required_maintenance_keys':['evidence_steps/E']})
    def propose(packet):
        assert packet['unresolved']==prior['findings']
        candidate=cm.existing_candidate(packet['before']);candidate.operation='Restore'
        for key,action_name in [('claims/A','update'),('claims/C','restore')]:
            post=deepcopy(packet['before'][key]['payload'])
            if key=='claims/A':post['statement']='甲是假设，不是断言。'
            candidate.records.append(cm.source.Row(collection='claims',object_id=key.split('/')[1],payload=post))
            d=next(d for d in candidate.dispositions if d.record_key==key);d.action=action_name;d.reason='source-supported restoration and coordinated repair'
            d.citations=[cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='甲是假设，不是断言。')]
        candidate.finding_resolutions=[cm.FindingResolution(finding_id=fid,disposition='resolved',reason='corrected original target',
            citations=[cm.Citation.model_validate(f['citations'][0])]) for fid,f in packet['unresolved'].items()]
        return candidate.model_dump(mode='json')
    ports=SyntheticPorts(store,root,proposal=propose)
    if fault in {'changed_scope','unrelated_target'}:
        with pytest.raises(ChangeSetConflict):execute_claim_management(store,request,root=newroot,ports=ports)
        assert ports.calls==[] and store.get_cvp_group(prior['run_id'])==prior
    else:
        if fault=='late_failure':
            before_pair={cid:store.get_record_state('claims',cid) for cid in ['A','C']}
            original=store._assert_transaction_edges_and_reviews
            def fail(*args,**kwargs):raise RuntimeError('synthetic late transaction rollback')
            monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',fail)
            failed=execute_claim_management(store,request,root=newroot,ports=ports)
            assert failed['status']=='paused' and ports.calls==['gpt','opus']
            assert before_pair=={cid:store.get_record_state('claims',cid) for cid in ['A','C']}
            monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',original)
        result=execute_claim_management(store,request,root=newroot,ports=ports);assert result['status']=='completed',result
        parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id));assert len(parent['latest_rounds'])==1
        assert ports.calls==['gpt','opus'] and all(store.claim_management_qualification(cid)['ready'] for cid in ['A','C'])
        oldnow=store.get_cvp_group(prior['run_id']);assert oldnow['call_reservations']==prior['call_reservations'] and oldnow['result']==prior['result']


@pytest.mark.parametrize('status,pointer',[('superseded','A'),('candidate','A'),('human_review_required',None),('candidate',None)])
def test_restore_completion_checks_logical_retirement_not_only_sql_active(status,pointer):
    old={'retired':False,'payload':{'review_status':'superseded','superseded_by':'A',
        'ai_adjudication':{'status':'ai_consensus_applied','approval_status':'not_human_approved','fingerprint':'old'}}}
    goal={'operation':'Restore','target_keys':['claims/C'],'original_objects':{'claims/C':old}}
    actual={'claims/C':{'retired':False,'payload':{'review_status':status,'superseded_by':pointer}}}
    if status=='candidate' and pointer is None:assert cm.goal_satisfied(goal,actual,{})
    else:
        with pytest.raises(ValueError,match='not actually restored'):cm.goal_satisfied(goal,actual,{})
