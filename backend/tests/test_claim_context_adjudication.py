"""An adjudicated unrelated source hold stays visible without blocking its neighbor."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_flow import binding
from backend.pipeline.claim_review_recovery import recover_returned_review,VERSION
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts,seed_claims,operation_request,passing,rejected


@pytest.mark.parametrize('case',['agreed','missing_judgment','new_issue','disagreement','gpt_impact','authorized','written','legacy_impact'])
def test_only_judged_and_jointly_unrelated_context_is_nonblocking(case):
    bound={'path':'/tmp/synthetic-context-file','file_sha256':'a'*64}
    request=cm.Request(action_id='context-test',operation='Update',allowed_operations=['Update'],target_keys=['claims/C'],
        source_bindings={'S':bound},authorization=bound,policy=bound)
    finding={'target_key':'claims/D','affected_target_keys':[],'impact_reason':'D is independent'}
    previous={'issue':deepcopy(finding)}
    resolution=cm.FindingResolution(finding_id='issue',disposition='persistent',reason='still needs source check',citations=[cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='quoted source')])
    candidate=cm.Candidate(records=[],dispositions=[cm.ObjectDisposition(record_key='claims/D',action='retain',reason='readonly')],finding_resolutions=[resolution])
    if case=='missing_judgment':candidate.finding_resolutions=[]
    elif case=='new_issue':previous={}
    elif case=='disagreement':resolution.disposition='resolved'
    elif case=='gpt_impact':resolution.affected_target_keys=['claims/C'];resolution.impact_reason='C depends on it'
    elif case=='authorized':request.permissions=[cm.source.Permission(collection='claims',object_id='D',fields=['statement'])]
    elif case=='written':candidate.dispositions[0].action='update'
    elif case=='legacy_impact':previous['issue'].pop('affected_target_keys')
    assert bool(cm.pending_context_adjudications(previous,candidate,{'issue':finding},request=request))==(case!='agreed')


@pytest.mark.parametrize('judged',[False,True])
def test_recovery_preserves_unrelated_hold_and_calls_but_cannot_skip_gpt(action,monkeypatch,judged):
    store,_,_,_,tmp_path,_=action
    seed_claims(store,['C','D'])
    store.apply_plan(store.plan_package({'package_id':'adjacent-claim','claim_relations':[
        {'claim_relation_id':'R','from_id':'C','to_id':'D','relation_type':'supports'}]}))
    request,root=operation_request(action,'Update',['C'])
    policy=cm.Policy.model_validate_json(cm.DEFAULT_POLICY.read_text())
    if not judged:policy.budget.max_revisions=0;policy.budget.gpt_calls=0;policy.budget.opus_calls=1
    policy_path=tmp_path/'bounded-policy.json';policy_path.write_text(policy.model_dump_json())
    request.policy=cm.source.FileBinding.model_validate(binding(policy_path))
    auth_path=Path(request.authorization.path);auth=json.loads(auth_path.read_text());auth['policy']=request.policy.model_dump(mode='json')
    auth_path.write_text(json.dumps(auth));request.authorization=cm.source.FileBinding.model_validate(binding(auth_path))
    def opus(packet):
        if not packet['round_number']:
            result=rejected(packet,key='claims/D',category='source_uncertain')
            result['findings'][0].update(affected_target_keys=[],impact_reason='D does not support C')
        else:
            result=passing(packet)
            for r in result['prior_findings']:r.update(disposition='persistent',affected_target_keys=[],impact_reason='D does not support C')
            for v in result['verdicts']:
                if v['record_key']=='claims/D':v.update(approved=False,problems=['source still uncertain'])
        return result
    def proposal(packet):
        candidate=cm.existing_candidate(packet['before'])
        candidate.finding_resolutions=[cm.FindingResolution(finding_id=fid,disposition='persistent',reason='unrelated source hold remains',
            affected_target_keys=[],impact_reason='D does not support C',citations=[cm.Citation.model_validate(f['citations'][0])]) for fid,f in packet['unresolved'].items()]
        return candidate.model_dump(mode='json')
    original=cm.pending_context_adjudications
    monkeypatch.setattr(cm,'pending_context_adjudications',lambda previous,candidate,context,**kw:context)
    ports=SyntheticPorts(store,tmp_path,opus=opus,proposal=proposal)
    first=execute_claim_management(store,request,root=root,ports=ports)
    assert first['status']=='paused'
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id));rid=next(iter(parent['latest_rounds'].values()))
    prior=store.get_cvp_group(rid);slots=deepcopy(parent['call_reservations']);states={c:store.get_record_state('claims',c) for c in ['C','D']}
    monkeypatch.setattr(cm,'pending_context_adjudications',original)
    direction=tmp_path/'recovery-direction.json';direction.write_text(json.dumps({'schema_version':VERSION,'round_id':rid,
        'authorized_by':'owner','authorization_quote':'continue after fixing unrelated context completion','human_content_approval':False,
        'prior_metadata_sha256':cm.sha256_json({k:v for k,v in prior.items() if k not in {'run_id','status','error'}})}))
    if not judged:
        with pytest.raises(ValueError,match='unadjudicated'):recover_returned_review(store,rid,authorization=binding(direction))
        assert store.get_cvp_group(rid)==prior and store.get_cvp_group(parent['run_id'])['call_reservations']==slots
        return
    recover_returned_review(store,rid,authorization=binding(direction))
    assert cm.model_packet(store.get_cvp_group(rid)['materials'])==cm.model_packet(prior['materials'])
    assert store.get_cvp_group(parent['run_id'])['call_reservations']==slots
    result=execute_claim_management(store,request,root=root,ports=ports)
    assert result['status']=='completed' and result['context_findings']
    assert ports.calls==['opus','gpt','opus']
    assert store.claim_management_qualification('C')['ready']
    assert not store.claim_management_qualification('D')['ready']
    assert {c:store.get_record_state('claims',c) for c in ['C','D']}==states
    assert execute_claim_management(store,request,root=root,ports=ports)==result
    assert ports.calls==['opus','gpt','opus']
