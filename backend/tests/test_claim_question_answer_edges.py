"""Question answers share one endpoint rule in candidate and SQL validation."""
from pathlib import Path
import json
import pytest
from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.claim_review_recovery import recover_returned_review,VERSION
from backend.pipeline.cvp_sql_flow import binding
from backend.api.canonical_repository import postgres_store as sql_store
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import operation_request,SyntheticPorts


def answer_action(action,relation_type='answers'):
    store=action[0]
    store.apply_plan(store.plan_package({'package_id':'answer-graph',
        'claims':[{'claim_id':'C','claim_type':'exegesis','statement':'甲是假设。','occurrences':[{'source_id':'S','anchors':[]}],'evidence_step_ids':['E','F']}],
        'evidence_steps':[{'evidence_step_id':i,'source_id':'S','statement':'甲是假设。','produced_claim_ids':['C']} for i in ['E','F']],
        'questions':[{'question_id':'Q','source_id':'S','question':'甲是假设吗？','answer_claim_ids':['C']}],
        'knowledge_relations':[{'relation_id':'R','from_id':'E','to_id':'F','relation_type':'answers','reason':'answer step'}]}))
    req,root=operation_request(action,'Update',['C'])
    req.permissions.append(cm.source.Permission(collection='knowledge_relations',object_id='R',fields=['to_id','reason','relation_type']))
    policy=cm.Policy.model_validate_json((Path(cm.__file__).parent/'policies/wang_claim_management_joint_update_v3.json').read_text());policy.initial_candidate='proposal'
    path=root.with_suffix('.policy.json');path.write_text(policy.model_dump_json());req.policy=cm.source.FileBinding.model_validate(binding(path))
    authpath=Path(req.authorization.path);auth=json.loads(authpath.read_text());auth.update(request=req.model_dump(mode='json',exclude={'authorization','policy'}),policy=req.policy.model_dump(mode='json'),required_maintenance_keys=[])
    authpath.write_text(json.dumps(auth));req.authorization=cm.source.FileBinding.model_validate(binding(authpath))
    def proposal(packet):return {'changes':[{'record_key':'knowledge_relations/R','fields_json':json.dumps({'to_id':'Q','reason':'answers this question','relation_type':relation_type}),
        'reason':'connect the response to its question','citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}]}],'findings':[],'finding_resolutions':[]}
    ports=SyntheticPorts(store,root,proposal=proposal,opus=lambda p:{'complete_review':True,'findings':[],'prior_findings':[]})
    return store,req,root,ports


@pytest.mark.parametrize('relation_type',['answers','supports','refutes','contextualizes'])
def test_candidate_and_sql_agree_on_answer_to_question(action,relation_type):
    store,req,root,ports=answer_action(action,relation_type)
    result=execute_claim_management(store,req,root=root,ports=ports)
    if relation_type=='answers':
        assert result['status']=='completed',result
        assert ports.calls==['gpt','opus']
        assert store.get_record_state('knowledge_relations','R')['payload']['to_id']=='Q'
    else:
        assert result['reason']=='engineering_required' and 'incompatible endpoint collection' in result['detail']
        assert ports.calls==['gpt'],'invalid endpoints must fail before independent review'
        payload=store.get_record_state('knowledge_relations','R')['payload'];assert payload['to_id']=='F'
        with pytest.raises(sql_store.ChangeSetConflict,match='expected'):
            store.apply_plan(store.plan_package({'package_id':'invalid-answer-edge','knowledge_relations':[{**payload,'to_id':'Q','relation_type':relation_type}]}))


@pytest.mark.parametrize('committed',[False,True])
def test_reviewed_commit_validator_recovery_reuses_both_answers(action,monkeypatch,committed):
    store,req,root,ports=answer_action(action)
    actual=sql_store.knowledge_relation_endpoints
    monkeypatch.setattr(sql_store,'knowledge_relation_endpoints',lambda _:({'evidence_steps','observations'},{'evidence_steps'}))
    first=execute_claim_management(store,req,root=root,ports=ports)
    assert first['reason']=='engineering_required' and 'expected' in first['detail']
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(req.action_id));rid=next(iter(parent['latest_rounds'].values()));old=store.get_cvp_group(rid)
    assert old['checkpoint']=='reviewed' and old['opus_approved'] and not old.get('change_set_ids')
    assert store.get_record_state('knowledge_relations','R')['payload']['to_id']=='F'
    monkeypatch.setattr(sql_store,'knowledge_relation_endpoints',actual)
    if committed:
        metadata={k:v for k,v in old.items() if k not in {'run_id','status','error'}};metadata['change_set_ids']=['already-applied']
        with store.connect() as c:c.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',(json.dumps(metadata),rid))
        old=store.get_cvp_group(rid)
    direction=root/'commit-validator-recovery.json';direction.write_text(json.dumps({'schema_version':VERSION,'authorized_by':'owner',
        'authorization_quote':'recover after tested shared endpoint validator correction','human_content_approval':False,'round_id':rid,
        'prior_metadata_sha256':cm.sha256_json({k:v for k,v in old.items() if k not in {'run_id','status','error'}})}))
    if committed:
        with pytest.raises(ValueError):recover_returned_review(store,rid,authorization=binding(direction))
        assert store.get_cvp_group(rid)==old and ports.calls==['gpt','opus'];return
    recovered=recover_returned_review(store,rid,authorization=binding(direction))
    new=store.get_cvp_group(rid);assert new['checkpoint']=='prepared'
    assert new['candidate']==old['candidate'] and cm.model_packet(new['materials'])==cm.model_packet(old['materials'])
    assert store.get_cvp_group(parent['run_id'])['call_reservations']==parent['call_reservations']
    assert recover_returned_review(store,rid,authorization=binding(direction))==recovered
    final=execute_claim_management(store,req,root=root,ports=ports)
    assert final['status']=='completed',final
    assert ports.calls==['gpt','opus'] and store.claim_management_qualification('C')['ready']
    assert store.get_record_state('knowledge_relations','R')['payload']['to_id']=='Q'
