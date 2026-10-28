"""One owner-approved round reuses all original calls and verdicts."""
from copy import deepcopy
import json

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_route_cvp_queue import make_cvp_route_job
from backend.pipeline.viewpoint_resolution_runtime import write_immutable
from backend.pipeline.viewpoint_route_cvp_resolution import run_cvp_route_scope, CvpRouteReviewIncomplete
from backend.pipeline.viewpoint_route_round_extension import round_extension, VERSION, FILENAME
from test_viewpoint_observation_support import Answer, approved, fixture_packet, observation_proposal


def stopped(tmp_path, *, pass_after=3):
    material = {'viewpoint_id':'CV-1'}
    material['artifact_sha256'] = sha256_json(material)
    job = make_cvp_route_job('CV-1', material['artifact_sha256'], 'policy-sha')
    root = tmp_path/job['job_id']
    write_immutable(root/'cvp-route-materials.json', material)
    packet = fixture_packet()
    original = observation_proposal(packet)
    def correction(p):
        revised = deepcopy(original)
        revised['observation_supports'][0]['support_scope_note'] = f'原文观察范围，第{corrector.calls}次实质修正。'
        return {'schema_version':'wang_argument_route_reconsideration_v3',
            'route_proposal_sha256':p['route_proposal_sha256'], 'route_review_sha256':p['route_review_sha256'],
            'finding_dispositions':[{'target_kind':'observation','target_key':'OBS-1',
                'disposition':'accepted','reason':'已修订范围'}], 'revised_proposal':revised}
    def review(p):
        answer = approved(p)
        if reviewer.calls < pass_after:
            row = next(r for r in answer['change_reviews'] if r['target_kind']=='observation')
            row.update(decision='correct', finding_codes=['scope_overstates'],
                reason='仍须收窄范围', correction='明确实际原文范围')
        return answer
    proposer = Answer(original, model_id='gpt')
    reviewer = Answer(review, model_id='opus')
    corrector = Answer(correction, model_id='gpt')
    args = dict(route_packet=packet,existing_routes=[],output_dir=root,proposer=proposer,
        reviewer=reviewer,reconsiderer=corrector)
    with pytest.raises(CvpRouteReviewIncomplete,match='round 1'):
        run_cvp_route_scope(**args,max_correction_rounds=1)
    latest = json.loads((root/'route-review-001.json').read_text())
    auth = {'schema_version':VERSION,'authorized_by':'owner','owner_direction':'好，再加一轮',
        **{k:job[k] for k in ('job_id','viewpoint_id','material_sha256','policy_sha256')},
        'original_max_correction_rounds':1,'additional_correction_rounds':1,
        'latest_review_round':1,'latest_review_sha256':latest['review_sha256']}
    authority = tmp_path/'owner-one-more-round.json'
    write_immutable(authority,auth)
    return root,job,{'max_correction_rounds':1},authority,args


def test_exact_one_extra_round_replays_saved_calls_and_keeps_original_artifacts(tmp_path):
    root,job,policy,authority,args = stopped(tmp_path)
    assert round_extension(root,job,policy) is None
    receipt = round_extension(root,job,policy,authority)
    assert receipt['effective_max_correction_rounds']==2 and policy['max_correction_rounds']==1
    assert receipt['human_content_approval'] is False
    result = run_cvp_route_scope(**args,max_correction_rounds=2,round_extension=receipt)
    assert len(result['rounds'])==3 and result['rounds'][-1]['outcome']=='pass'
    assert result['correction_round_extension_sha256']==receipt['artifact_sha256']
    assert args['proposer'].calls==1 and args['reviewer'].calls==3 and args['reconsiderer'].calls==2
    assert round_extension(root,job,policy)==receipt
    assert json.loads((root/'route-review-001.json').read_text())['validation']['outcome']=='findings'
    assert json.loads((root/'route-review-000.json').read_text())['validation']['outcome']=='findings'


def test_initial_technical_continuation_reuses_failures_and_does_not_extend_semantic_rounds(tmp_path):
    from test_route_update_changes import Client, proposer, initial_edits
    from test_route_correction_changes import edit
    from backend.pipeline.viewpoint_route_correction_changes import PROPOSAL_VERSION
    material={'viewpoint_id':'CV-1'};material['artifact_sha256']=sha256_json(material)
    job=make_cvp_route_job('CV-1',material['artifact_sha256'],'policy-sha')
    root=tmp_path/job['job_id'];write_immutable(root/'cvp-route-materials.json',material)
    pc=Client(lambda p,n: ({'schema_version':PROPOSAL_VERSION,'edits':[]} if n==1 else
        {'schema_version':PROPOSAL_VERSION,'edits':[edit(key='absent',fields={'supports':'x'})]} if n==2 else initial_edits(p)))
    rev=Answer(approved,model_id='opus')
    args=dict(route_packet=fixture_packet(),existing_routes=[],output_dir=root,
        proposer=proposer(pc),reviewer=rev,reconsiderer=Answer({},model_id='gpt'))
    with pytest.raises(ValueError,match='program validation remains unresolved'):
        run_cvp_route_scope(**args,max_correction_rounds=1)
    old={p.name:p.read_bytes() for p in root.glob('*.json')}
    auth=tmp_path/'technical-direction.json'
    write_immutable(auth,{'schema_version':VERSION,'authorized_by':'owner','owner_direction':'继续原任务',
        **{k:job[k] for k in ('job_id','viewpoint_id','material_sha256','policy_sha256')},
        'original_max_correction_rounds':1,'additional_correction_rounds':1,
        'latest_review_round':None,'latest_review_sha256':sha256_json(json.loads(
            (root/'raw-route-proposal-000-validation-000.json').read_text())),
        'stage':'initial_program_validation'})
    extension=round_extension(root,job,{'max_correction_rounds':1},auth)
    result=run_cvp_route_scope(**args,max_correction_rounds=2,round_extension=extension)
    assert result['program_validation_repairs']==2 and rev.calls==1 and len(pc.packets)==3
    assert all((root/name).read_bytes()==content for name,content in old.items())
    assert round_extension(root,job,{'max_correction_rounds':1})==extension


@pytest.mark.parametrize('fault',['job','policy','two_rounds','different_review','unknown_call'])
def test_extension_refuses_scope_budget_review_or_unknown_call_changes(tmp_path,fault):
    root,job,policy,authority,_ = stopped(tmp_path)
    if fault=='job':job={**job,'material_sha256':'different'}
    elif fault=='policy':policy={'max_correction_rounds':2}
    elif fault=='unknown_call':(root/'raw-extra.call-intent.json').write_text('{}')
    else:
        auth=json.loads(authority.read_text())
        if fault=='two_rounds':auth['additional_correction_rounds']=2
        else:auth['latest_review_sha256']='different'
        authority.write_text(json.dumps(auth))
    with pytest.raises(ValueError):round_extension(root,job,policy,authority)
    assert not (root/FILENAME).exists()


@pytest.mark.parametrize('fault',['authority','original_review'])
def test_extension_resume_authenticates_authority_and_original_rejected_review(tmp_path,fault):
    root,job,policy,authority,_ = stopped(tmp_path)
    round_extension(root,job,policy,authority)
    path=authority if fault=='authority' else root/'route-review-001.json'
    value=json.loads(path.read_text());value['changed']=True;path.write_text(json.dumps(value))
    with pytest.raises(ValueError):round_extension(root,job,policy)


def second_authority(root, job, policy, previous, path):
    latest=json.loads((root/'route-review-002.json').read_text())
    write_immutable(path,{'schema_version':VERSION,'authorized_by':'owner','owner_direction':'再加一轮',
        **{k:job[k] for k in ('job_id','viewpoint_id','material_sha256','policy_sha256')},
        'original_max_correction_rounds':policy['max_correction_rounds'],'additional_correction_rounds':1,
        'latest_review_round':2,'latest_review_sha256':latest['review_sha256'],
        'previous_extension_sha256':previous['artifact_sha256']})


def test_second_explicit_round_appends_without_changing_first_authorization_or_calls(tmp_path):
    root,job,policy,authority,args=stopped(tmp_path,pass_after=4)
    first=round_extension(root,job,policy,authority)
    original_receipt=(root/FILENAME).read_bytes()
    with pytest.raises(CvpRouteReviewIncomplete,match='round 2'):
        run_cvp_route_scope(**args,max_correction_rounds=2,round_extension=first)
    next_authority=tmp_path/'owner-another-round.json'
    second_authority(root,job,policy,first,next_authority)
    second=round_extension(root,job,policy,next_authority)
    assert second['effective_max_correction_rounds']==3 and policy['max_correction_rounds']==1
    assert (root/FILENAME).read_bytes()==original_receipt
    assert round_extension(root,job,policy)==second
    result=run_cvp_route_scope(**args,max_correction_rounds=3,round_extension=second)
    assert result['rounds'][-1]['outcome']=='pass'
    assert args['proposer'].calls==1 and args['reviewer'].calls==4 and args['reconsiderer'].calls==3
    assert result['correction_round_extension_sha256']==second['artifact_sha256']
    authority.write_text('{}')
    with pytest.raises(ValueError):round_extension(root,job,policy)


def test_an_old_authorization_cannot_grant_the_next_round_again(tmp_path):
    root,job,policy,authority,args=stopped(tmp_path,pass_after=4)
    first=round_extension(root,job,policy,authority)
    with pytest.raises(CvpRouteReviewIncomplete,match='round 2'):
        run_cvp_route_scope(**args,max_correction_rounds=2,round_extension=first)
    copied=tmp_path/'reused-old-authority.json';copied.write_text(authority.read_text())
    with pytest.raises(ValueError,match='authorization differs'):
        round_extension(root,job,policy,copied)
    assert not(root/'correction-round-extension-002.json').exists()


def test_initial_technical_continuations_append_without_resetting_calls(tmp_path):
    from test_route_update_changes import Client,proposer,initial_edits
    from test_route_correction_changes import edit
    from backend.pipeline.viewpoint_route_correction_changes import PROPOSAL_VERSION
    material={'viewpoint_id':'CV-1'};material['artifact_sha256']=sha256_json(material)
    job=make_cvp_route_job('CV-1',material['artifact_sha256'],'policy-sha')
    root=tmp_path/job['job_id'];write_immutable(root/'cvp-route-materials.json',material)
    pc=Client(lambda p,n:initial_edits(p) if n==4 else {'schema_version':PROPOSAL_VERSION,
        'edits':[edit(key=f'absent-{n}',fields={'supports':'x'})]})
    rev=Answer(approved,model_id='opus');policy={'max_correction_rounds':1}
    args=dict(route_packet=fixture_packet(),existing_routes=[],output_dir=root,
        proposer=proposer(pc),reviewer=rev,reconsiderer=Answer({}))
    with pytest.raises(ValueError,match='program validation remains unresolved'):
        run_cvp_route_scope(**args,max_correction_rounds=1)
    previous=None
    for ordinal in (1,2):
        latest=root/f'raw-route-proposal-000-validation-{ordinal-1:03d}.json'
        direction={'schema_version':VERSION,'authorized_by':'owner','owner_direction':'完成原任务',
            **{k:job[k] for k in ('job_id','viewpoint_id','material_sha256','policy_sha256')},
            'original_max_correction_rounds':1,'additional_correction_rounds':1,
            'latest_review_round':None,'latest_review_sha256':sha256_json(json.loads(latest.read_text())),
            'stage':'initial_program_validation'}
        if previous:direction['previous_extension_sha256']=previous['artifact_sha256']
        auth=tmp_path/f'authority-{ordinal}.json';write_immutable(auth,direction)
        previous=round_extension(root,job,policy,auth)
        frozen={p.name:p.read_bytes() for p in root.glob('*.json')}
        if ordinal==1:
            with pytest.raises(ValueError,match='program validation remains unresolved'):
                run_cvp_route_scope(**args,max_correction_rounds=2,round_extension=previous)
        else:
            result=run_cvp_route_scope(**args,max_correction_rounds=3,round_extension=previous)
            assert result['program_validation_repairs']==3
        assert all((root/name).read_bytes()==value for name,value in frozen.items())
    assert len(pc.packets)==4 and rev.calls==1
    assert round_extension(root,job,policy)==previous
