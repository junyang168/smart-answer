"""A shared read Question can receive a new answer without widening review roots."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from backend.pipeline import claim_management as cm,claim_joint_update as joint,claim_management_intake as intake
from backend.pipeline.claim_review_recovery import recover_refused_admission,VERSION
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_commit import binding
from backend.tests.test_claim_management_intake import claims
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_management_human_source_follow_up import authorize


def prepare(claims,monkeypatch,*,old_guard=False):
    store,root=claims
    store.apply_plan(store.plan_package({'package_id':'shared-question',
        'questions':[{'question_id':'Q','text':'甲是甚麼？','questioner':'professor','answer_state':'answered',
                      'answer_claim_ids':['D'],'source_fragment_ids':['FC']}]}))
    policy=cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_bounded_unit_v1.json').read_text())
    path=root.parent/'policy.json';path.write_text(policy.model_dump_json())
    prepared=intake.prepare(['C'],root,instruction='Refresh C; shared Questions only link to discoveries',policy=path,store=store,groups=[['C']])
    manifest=json.loads(open(prepared['manifest']['path']).read());job=manifest['jobs'][0]
    request=cm.Request.model_validate_json(open(job['request']['path']).read())
    assert next(p for p in request.permissions if p.key=='questions/Q').fields==['answer_claim_ids']
    ports=SyntheticPorts(store,root)
    if old_guard:
        with monkeypatch.context() as m:
            m.setattr(joint,'admission_error',lambda *args:'joint Update permission/required maintenance item is unreachable from its target closure')
            result=execute_claim_management(store,request,root=job['root'],ports=ports)
        assert result['reason']=='engineering_required' and ports.calls==[]
    records=store.read_claim_context(joint.context_roots(request));before={cm.source.key(*k):cm.model_state(v)for k,v in records.items()}
    return store,root,request,job,ports,before


def test_read_shared_question_link_only_permission_is_admitted(claims,monkeypatch):
    store,root,request,job,ports,before=prepare(claims,monkeypatch)
    policy=cm.Policy.model_validate_json(open(request.policy.path).read())
    goal=cm.original_goal(request,before)
    assert joint.admission_error(request,policy,before,goal) is None
    scope=cm.audit_keys(request,before)
    assert 'questions/Q' in scope
    from backend.pipeline.claim_direct_scope import scope as direct
    assert direct(request,before)['content_claim_keys']==['claims/C']
    request.permissions=[p.model_copy(update={'fields':['text']}) if p.key=='questions/Q' else p for p in request.permissions]
    auth=json.loads(open(request.authorization.path).read())
    extras={k:v for k,v in auth.items()if k not in {'schema_version','authorized_by','authorization_quote','human_content_approval','request','policy'}}
    authorize(request,extras)
    assert 'unreachable' in joint.admission_error(request,policy,before,goal)


@pytest.mark.parametrize('fault',[None,'stale_metadata','changed_fact','already_started'])
def test_zero_call_refusal_recovery_preserves_input_and_budget(claims,monkeypatch,fault):
    store,root,request,job,ports,before=prepare(claims,monkeypatch,old_guard=True)
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    metadata={k:v for k,v in parent.items()if k not in ['run_id','status','error']}
    direction={'schema_version':VERSION,'action_id':request.action_id,'authorized_by':'owner',
        'authorization_quote':'fix engineering fault and resume original task','human_content_approval':False,
        'prior_metadata_sha256':cm.sha256_json(metadata)}
    if fault=='stale_metadata':direction['prior_metadata_sha256']='0'*64
    if fault=='changed_fact':
        p=deepcopy(store.get_record_state('claims','C')['payload']);p['statement']+=' changed'
        store.apply_plan(store.plan_package({'package_id':'drift','claims':[p]}))
    if fault=='already_started':
        metadata['call_reservations']={'gpt':{'state':'started'}}
        # Simulate a conflicting checkpoint under test; production never edits a call ledger.
        with store.connect() as conn:
            conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',
                         (json.dumps(metadata),parent['run_id']))
        direction['prior_metadata_sha256']=cm.sha256_json(metadata)
    p=root.parent/'recovery.json';p.write_text(json.dumps(direction))
    if fault:
        with pytest.raises(ValueError):recover_refused_admission(store,request.action_id,authorization=binding(p))
        assert store.get_cvp_group(parent['run_id'])['admission_refusal']
    else:
        receipt=recover_refused_admission(store,request.action_id,authorization=binding(p))
        now=store.get_cvp_group(parent['run_id'])
        assert 'admission_refusal' not in now and now['request']==parent['request']
        assert now['policy']==parent['policy'] and now['call_reservations']=={} and now['latest_rounds']=={}
        history=json.loads(open(receipt['path']).read())
        assert history['original_parent']['admission_refusal'] and history['model_calls']==0
    assert ports.calls==[]


def test_quoteless_borrowed_evidence_report_resumes_as_unaccepted_input(claims):
    from backend.tests.test_claim_management_intake import pass_review
    from backend.pipeline.claim_review_recovery import resume_rejected_output
    from backend.pipeline.source_reconciliation_runner import read,body
    store,root=claims
    store.apply_plan(store.plan_package({'package_id':'direct-neighbour',
        'claim_relations':[{'claim_relation_id':'CD','from_id':'C','to_id':'D','relation_type':'supports','reason':'source reason'}]}))
    policy=cm.DEFAULT_POLICY.parent/'wang_claim_management_bounded_unit_v1.json'
    prepared=intake.prepare(['C'],root,instruction='Refresh C',policy=policy,store=store,groups=[['C']])
    job=json.loads(open(prepared['manifest']['path']).read())['jobs'][0]
    request=cm.Request.model_validate_json(open(job['request']['path']).read())
    def borrowed(packet):
        result=pass_review(packet)
        result['binding_assessments'][0]['evidence_keys'].append('evidence_steps/ED')
        return result
    ports=SyntheticPorts(store,root,opus=borrowed)
    result=execute_claim_management(store,request,root=job['root'],ports=ports)
    error='own Evidence assessment borrowed non-owned Evidence or a whole paragraph'
    assert result['detail']==error
    rid=result['returned_rejected_call'].rsplit(':',1)[0]
    raw=(Path(job['root'])/rid/'opus.raw.json').read_bytes()
    receipt=resume_rejected_output(store,rid,role='opus',error=error)
    record=body(read(Path(receipt['path'])))
    assert record['input']['correction_location_basis']['claim_key']=='claims/C'
    assert record['input']['correction_location_basis']['borrowed_keys']==['evidence_steps/ED']
    assert record['input']['review_not_accepted'] and record['model_calls']==0 and not record['new_budget']
    assert (Path(job['root'])/rid/'opus.raw.json').read_bytes()==raw
    assert ports.calls==['opus']
    assert store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))['result']['reason']=='revision_required'
