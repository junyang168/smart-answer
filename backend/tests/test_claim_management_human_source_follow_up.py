"""A real human source answer resumes bounded work without rewriting old review."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runner import binding
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts,seed_claims,operation_request,rejected,passing


def authorize(request,extra=None):
    path=Path(request.authorization.path)
    path.write_text(json.dumps({'schema_version':cm.VERSION,'authorized_by':'owner','human_content_approval':False,
        'authorization_quote':'Synthetic human answer and continued original Update',
        'request':request.model_dump(mode='json',exclude={'authorization','policy'}),
        'policy':request.policy.model_dump(),**(extra or {})}))
    request.authorization=cm.source.FileBinding.model_validate(binding(path))


@pytest.fixture
def human_pending(action):
    store,_,_,_,root,_=action;seed_claims(store,['C'])
    request,output=operation_request(action,'Update',['C'])
    request.policy=cm.source.FileBinding.model_validate(binding(cm.DEFAULT_POLICY));authorize(request)
    def opus(packet):
        if not packet['round_number']:return rejected(packet,category='source_uncertain')
        result=passing(packet)
        for value in result['prior_findings']:value['disposition']='persistent'
        return result
    def propose(packet):
        candidate=cm.existing_candidate(packet['before']);candidate.operation='Update'
        post=deepcopy(packet['before']['claims/C']['payload']);post['statement']='教授转述甲是假设。'
        candidate.records=[cm.source.Row(collection='claims',object_id='C',payload=post)]
        candidate.dispositions[0].action='update'
        candidate.dispositions[0].reason='synthetic attribution repair'
        candidate.dispositions[0].citations=[cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='甲是假设，不是断言。')]
        candidate.finding_resolutions=[cm.FindingResolution(finding_id=fid,disposition='persistent',
            reason='source speaker uncertain',citations=[cm.Citation.model_validate(f['citations'][0])]) for fid,f in packet['unresolved'].items()]
        return candidate.model_dump(mode='json')
    result=execute_claim_management(store,request,root=output,ports=SyntheticPorts(store,root,opus=opus,proposal=propose))
    assert result['reason']=='human_required',result
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    fid,finding=next(iter(parent['findings'].items()))
    verification={'schema_version':'wang_claim_human_source_verification_v1','verified_by':'user','human_content_approval':False,
        'predecessor_action_id':request.action_id,'source_bindings':request.model_dump(mode='json')['source_bindings'],
        'user_confirmation':'这是教授自己的话','answers':[{'finding_id':fid,'question':finding['human_question'],
            'answer':'The speaker is the professor','citation':deepcopy(finding['citations'][0])}]}
    file=root/'human-answer.json';file.write_text(json.dumps(verification))
    successor,newroot=operation_request(action,'Update',['C']);successor.predecessor_action_id=request.action_id
    policy=cm.Policy.model_validate(parent['policy']);policy.policy_id='human-source-follow-up';policy.initial_candidate='proposal';policy.budget.gpt_calls=policy.budget.opus_calls=1
    policy_file=root/'human-policy.json';policy_file.write_text(policy.model_dump_json());successor.policy=cm.source.FileBinding.model_validate(binding(policy_file))
    authorize(successor,{'human_source_verification':binding(file)})
    return store,request,parent,successor,newroot,file,verification


def test_human_answer_reaches_both_models_and_preserves_history_with_exact_new_budget(human_pending):
    store,request,parent,successor,root,file,verification=human_pending
    old_calls=deepcopy(parent['call_reservations'])
    def propose(packet):
        follow=packet['continuation']['human_source_verification']
        assert follow['record']==verification
        assert packet['previous_candidate']['records'][0]['payload']['statement']=='教授转述甲是假设。'
        candidate=cm.Candidate.model_validate(packet['previous_candidate'])
        candidate.finding_resolutions=[cm.FindingResolution(finding_id=fid,disposition='resolved',reason='confirmed by user, retained exact source wording',
            citations=[cm.Citation.model_validate(f['citations'][0])]) for fid,f in packet['unresolved'].items()]
        return candidate.model_dump(mode='json')
    def opus(packet):
        assert packet['continuation']['human_source_verification']['record']==verification
        return passing(packet)
    ports=SyntheticPorts(store,root,proposal=propose,opus=opus)
    result=execute_claim_management(store,successor,root=root,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['gpt','opus']
    assert store.claim_management_qualification('C')['ready']
    current=store.get_cvp_group(parent['run_id'])
    assert current['call_reservations']==old_calls and current['result']['reason']=='human_required'
    assert execute_claim_management(store,successor,root=root,ports=ports)==result
    assert ports.calls==['gpt','opus']
    proof=store.get_cvp_group('CMA-'+cm.sha256_json(successor.action_id))['continuation']
    assert proof['remaining_generations']['opus']==0 and proof['follow_up_budget']['opus_calls']==1
    # The same confirmation cannot create a second budget fork.
    fork=successor.model_copy(deep=True);fork.action_id+='-fork';fork.authorization=cm.source.FileBinding(path=str(root/'fork.json'),file_sha256='a'*64)
    authorize(fork,{'human_source_verification':binding(file)})
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    with pytest.raises(ChangeSetConflict):
        execute_claim_management(store,fork,root=root/'fork',ports=SyntheticPorts(store,root))


@pytest.mark.parametrize('bad',['wrong_predecessor','changed_source','unknown_finding','changed_question','approval','double_budget','repeat_answer','empty_answer','empty_confirmation','wrong_citation'])
def test_human_follow_up_rejects_unbound_answers_and_budget_reset(human_pending,bad):
    store,_,parent,request,root,file,record=human_pending
    if bad=='wrong_predecessor':record['predecessor_action_id']='other'
    if bad=='changed_source':record['source_bindings']['S']['file_sha256']='b'*64
    if bad=='unknown_finding':record['answers'][0]['finding_id']='unknown'
    if bad=='changed_question':record['answers'][0]['question']='another question'
    if bad=='approval':record['human_content_approval']=True
    if bad=='empty_answer':record['answers'][0]['answer']=''
    if bad=='empty_confirmation':record['user_confirmation']=''
    if bad=='wrong_citation':record['answers'][0]['citation']['paragraph_key']='S9999'
    if bad=='repeat_answer':parent['continuation']={'human_verified_finding_ids':[record['answers'][0]['finding_id']]}
    file.write_text(json.dumps(record));authorize(request,{'human_source_verification':binding(file)})
    policy=cm.Policy.model_validate_json(Path(request.policy.path).read_text())
    if bad=='double_budget':policy.budget.opus_calls=2
    with pytest.raises(ValueError):cm.continuation_budget(parent,request,policy)


@pytest.mark.parametrize('fault', [None, 'source', 'quote', 'approval', 'changed_file'])
def test_new_explicit_target_receives_bound_human_source_facts(action, fault):
    store,_,_,_,root,_=action;seed_claims(store,['C'])
    request,output=operation_request(action,'Update',['C'])
    policy=cm.Policy.model_validate_json(Path(request.policy.path).read_text())
    policy.initial_candidate='proposal'
    policy.independent_audit=False
    policy.budget.gemini_calls=0
    policy_file=root/'clarified-policy.json';policy_file.write_text(policy.model_dump_json())
    request.policy=cm.source.FileBinding.model_validate(binding(policy_file))
    record={'schema_version':'wang_claim_human_source_verification_v1','verified_by':'user',
        'human_content_approval':False,'source_bindings':request.model_dump(mode='json')['source_bindings'],
        'user_confirmation':'核听确认是口误，保留原话并注明',
        'answers':[{'finding_id':'saved-source-question','question':'Is this a slip?',
            'answer':'The user confirmed the slip; do not rewrite the source.',
            'citation':{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}}]}
    if fault=='source':record['source_bindings']['S']['file_sha256']='b'*64
    if fault=='quote':record['answers'][0]['citation']['exact_quote']='不存在的原话'
    if fault=='approval':record['human_content_approval']=True
    file=root/'explicit-target-human-facts.json';file.write_text(json.dumps(record))
    authorize(request,{'human_source_verification':binding(file)})
    if fault=='changed_file':file.write_text('{}')
    original=store.get_record_state('claims','C')
    def check(packet):
        evidence=packet['sources']['S']['human_source_verification']
        assert evidence['record']==record
        assert evidence['source_text_unchanged'] and evidence['content_review_still_required']
        assert packet['sources']['S']['paragraphs']['S0001']['text']=='甲是假设，不是断言。'
    def propose(packet):
        check(packet)
        candidate=cm.existing_candidate(packet['before']);candidate.operation='Update'
        return candidate.model_dump(mode='json')
    def opus(packet):
        check(packet)
        return passing(packet)
    ports=SyntheticPorts(store,root,proposal=propose,opus=opus)
    if fault:
        with pytest.raises((ValueError,RuntimeError)):
            execute_claim_management(store,request,root=output,ports=ports)
        assert ports.calls==[]
    else:
        result=execute_claim_management(store,request,root=output,ports=ports)
        assert result['status']=='completed',result
        assert ports.calls==['gpt','opus']
        assert store.claim_management_qualification('C')['ready']
    assert store.get_record_state('claims','C')==original
