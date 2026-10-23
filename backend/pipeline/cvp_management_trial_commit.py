"""Commit authenticated CVP2 trials without repeating their model calls.

The trial's immutable call intents and responses are authenticated against
their original packets before its previously unwritten group enters SQL.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository import cvp_management_store as persistence
from backend.api.canonical_repository.cvp_management_authority import validate_evidence
from backend.api.canonical_repository.cvp_recall import verify
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.cvp_management_models import adapter, validate_response
from backend.pipeline.cvp_management_runner import _all_findings, _guard, _readback, validate_authorization
from backend.pipeline.cvp_recall_preparation import prepare_recall
from backend.pipeline.cvp_sql_commit import binding, commit_package, resume_reviewed_action
from backend.pipeline.source_reconciliation_runner import exclusive
from backend.pipeline.viewpoint_resolution_runtime import (
    model_request_identity, read_artifact, read_saved_model_response, stable_decided_at,
    write_immutable,
)


def commit_approved_trial(store,root,*,supersede_unwritten_run):
    root=Path(root).resolve()
    request=domain.Request.model_validate(read_artifact(root/'request.json'))
    validate_authorization(request)
    existing=store.get_cvp_group('CVPM-'+sha256_json(request.action_id))
    if existing and existing.get('result',{}).get('status')=='completed':
        if existing.get('request')!=request.model_dump(mode='json') or existing.get('execution_root')!=str(root):
            raise ValueError('completed trial action has a different original request/root')
        from backend.pipeline.viewpoint_route_cvp_worker import handoff_completed_cvp_groups
        handoff_completed_cvp_groups(store,existing,root)
        return existing['result']
    if len(request.input['grouping']['groups'])!=1:
        raise ValueError('trial acceptance is limited to exactly one original group')
    step=root/'proposal-trial'/'step-0005'
    material=read_artifact(step/'materials.json')
    bundle=material['bundle'];previous=material['previous'];findings=material['prior_findings']
    candidate=read_artifact(step/'candidate.json')
    approval=read_artifact(step/'approval.json')
    prior_step=step.parent/'step-0004'
    if (previous!=read_artifact(prior_step/'candidate.json') or
            findings!=_all_findings(previous,read_artifact(prior_step/'approval.json'))):
        raise ValueError('approved trial lost its exact correction lineage')
    proposal_packet=domain.proposal_packet(request,bundle,previous=previous,prior_findings=findings)
    review_packet=domain.review_packet(request,bundle,candidate,prior_findings=findings)
    calls={}
    for role,name,packet,adapter_role in (
            ('gpt','proposal',proposal_packet,'correction'),
            ('opus','review',review_packet,'review')):
        path=step/(name+'.json')
        model=adapter(adapter_role,request,bundle,packet)
        raw=read_saved_model_response(model,packet,path)
        validate_response(adapter_role,raw)
        calls[role]=(path,model,packet,raw,read_artifact(path))
    if (material['packet_sha256']!=sha256_json(proposal_packet) or
            domain.compile_candidate(request,bundle,calls['gpt'][3]['candidate'],
                previous=previous,prior_findings=findings)!=candidate or
            domain.validate_review(request,bundle,candidate,calls['opus'][3]['review'],
                prior_findings=findings)!=approval or not approval['approved']):
        raise ValueError('saved trial candidate or approval differs from actual returned responses')
    preflight=prepare_recall(store,request.input,allow_unqualified=True,defer_index=True)
    if preflight['admission']['blocked_claims']:
        raise ValueError('trial group has lost upstream qualification')
    _guard(store,bundle)
    with exclusive(root):
        parent=persistence.prepare_action(store,request,root=str(root),preflight=preflight,
            supersede_unwritten_run=supersede_unwritten_run)
        parent_id=parent['run_id'];group_key=parent['groups'][0]['group_key']
        unit=parent['units'][group_key]
        if unit['status']=='completed':
            from backend.pipeline.viewpoint_route_cvp_worker import handoff_completed_cvp_groups
            handoff_completed_cvp_groups(store,parent,root)
            return parent['result']
        materials={'bundle':bundle,'previous':previous,'prior_findings':findings,
            'step_root':str(step),'recall_root':str(root/'recall'),'index_root':request.input.get('index_root')}
        step_id=persistence.prepare_step(store,parent_id,group_key=group_key,
            generation=sha256_json({'approved_trial':candidate['artifact_sha256']})[:24],
            ordinal=0,materials=materials,revision=False)
        for role,(path,model,packet,raw,artifact) in calls.items():
            intent={**model_request_identity(model,packet),'request_bytes':model.request_bytes(packet),
                'cache_path':str(path)}
            persistence.import_verified_call(store,parent_id,key=str(path.relative_to(root)),role=role,
                intent=intent,raw=raw,artifact_sha256=artifact['artifact_sha256'],trial_root=str(root))
        evidence={'round_id':step_id,'request':request.model_dump(mode='json'),'bundle':bundle,
            'candidate':candidate,'approval':approval,'proposal_raw':calls['gpt'][3],
            'review_raw':calls['opus'][3],'previous':previous,'prior_findings':findings,
            'call_keys':{role:str(item[0].relative_to(root)) for role,item in calls.items()},
            'provenance':{'proposal_model':calls['gpt'][1].model_id,
                'review_model':calls['opus'][1].model_id,
                'proposal_artifact_sha256':calls['gpt'][4]['artifact_sha256'],
                'review_artifact_sha256':calls['opus'][4]['artifact_sha256'],
                'created_at':stable_decided_at(step)}}
        package=validate_evidence(evidence)
        _guard(store,bundle)
        if store.get_cvp_group(step_id)['checkpoint']=='reviewed':
            resume_reviewed_action(store,step_id)
        elif store.get_cvp_group(step_id)['checkpoint']=='prepared':
            commit_package(store,step_id,package=package,approval=binding(step/'review.json'),
                authorization=request.authorization.model_dump(),backup={},sources=bundle['sources'],
                read_pins={(r['collection'],r['object_id']):(r['revision'],r['content_sha256'],False)
                    for r in bundle['records']},reason='CVP2 exact approved trial candidate',
                actor=domain.VERSION,source_kind=domain.VERSION,management_evidence=evidence)
        readback,pins=_readback(store,evidence)
        persistence.complete_step(store,step_id,readback=readback,read_pins=pins)
        persistence.progress(store,parent_id,group_key=group_key,status='completed',step_id=step_id)
        from backend.pipeline.viewpoint_route_cvp_worker import handoff_cvp_route_jobs
        handoff_cvp_route_jobs(store,package,root/'route-queue-v2',step)
        return store.get_cvp_group(parent_id)['result']


def commit_verified_pair(store,root,*,initial_proposal,initial_review,correction,final_review):
    """Accept four authenticated trial answers on the original unwritten action.

    Old execution attempts are marked inapplicable, with their call states
    preserved. No model is called and no answer is moved to a new cache path.
    """
    root=Path(root).resolve()
    paths={name:Path(path).resolve() for name,path in {
        'initial_proposal':initial_proposal,'initial_review':initial_review,
        'correction':correction,'final_review':final_review}.items()}
    request=domain.Request.model_validate(read_artifact(root/'request.json'))
    validate_authorization(request)
    if len(request.input['grouping']['groups'])!=1:
        raise ValueError('verified pair acceptance requires exactly one group')
    material=read_artifact(next(root.glob('unit-*/[0-9a-f]*/step-0001/materials.json')))
    if material['previous'] is not None or material['prior_findings']:
        raise ValueError('verified pair must start from exact initial group material')
    bundle=material['bundle']

    def authenticated(name,role,packet):
        model=adapter(role,request,bundle,packet)
        raw=read_saved_model_response(model,packet,paths[name])
        validate_response(role,raw)
        artifact=read_artifact(paths[name])
        return model,raw,artifact

    initial_packet=domain.proposal_packet(request,bundle,previous=None,prior_findings=[])
    first_model,first_raw,first_artifact=authenticated('initial_proposal','proposal',initial_packet)
    first=domain.compile_candidate(request,bundle,first_raw['candidate'],
        previous=None,prior_findings=[])
    first_review_packet=domain.review_packet(request,bundle,first,prior_findings=[])
    first_reviewer,first_review_raw,first_review_artifact=authenticated(
        'initial_review','review',first_review_packet)
    first_approval=domain.validate_review(request,bundle,first,first_review_raw['review'],
        prior_findings=[])
    findings=_all_findings(first,first_approval)
    correction_packet=domain.proposal_packet(request,bundle,previous=first,prior_findings=findings)
    proposer,proposal_raw,proposal_artifact=authenticated('correction','correction',correction_packet)
    candidate=domain.compile_candidate(request,bundle,proposal_raw['candidate'],
        previous=first,prior_findings=findings)
    final_packet=domain.review_packet(request,bundle,candidate,prior_findings=findings)
    reviewer,review_raw,review_artifact=authenticated('final_review','review',final_packet)
    approval=domain.validate_review(request,bundle,candidate,review_raw['review'],
        prior_findings=findings)
    if first_approval['approved'] or not approval['approved']:
        raise ValueError('verified pair does not have a rejected initial and approved correction')
    archive=root/'verified-trial'
    for name,path in paths.items():
        write_immutable(archive/(name+'.json'),read_artifact(path))
        write_immutable(archive/(name+'.call-intent.json'),
            read_artifact(path.with_name(path.stem+'.call-intent.json')))
    for name,value in (('initial-candidate',first),('initial-approval',first_approval),
                       ('candidate',candidate),('approval',approval)):
        write_immutable(archive/(name+'.json'),value)
    calls={
        'initial_proposal':('gpt',first_model,initial_packet,first_raw,first_artifact),
        'initial_review':('opus',first_reviewer,first_review_packet,first_review_raw,first_review_artifact),
        'correction':('gpt',proposer,correction_packet,proposal_raw,proposal_artifact),
        'final_review':('opus',reviewer,final_packet,review_raw,review_artifact),
    }
    keys={name:'verified-trial/'+name+'.json' for name in calls}
    def evidence_for(step_id):
        return {'round_id':step_id,'request':request.model_dump(mode='json'),'bundle':bundle,
            'candidate':candidate,'approval':approval,'proposal_raw':proposal_raw,
            'review_raw':review_raw,'previous':first,'prior_findings':findings,
            'call_keys':{'gpt':keys['correction'],'opus':keys['final_review']},
            'provenance':{'proposal_model':proposer.model_id,'review_model':reviewer.model_id,
                'proposal_artifact_sha256':proposal_artifact['artifact_sha256'],
                'review_artifact_sha256':review_artifact['artifact_sha256'],
                'created_at':stable_decided_at(paths['final_review'].parent)}}
    validate_evidence(evidence_for('CVPMR-preflight'))
    parent_id='CVPM-'+sha256_json(request.action_id)
    group_key=request.input['grouping']['groups'][0]['group_key']
    generation=sha256_json({'verified_trial':candidate['artifact_sha256']})[:24]
    step_id='CVPMR-'+sha256_json({'parent':parent_id,'group':group_key,
                                  'generation':generation,'ordinal':0})
    applied=store.get_cvp_group(step_id)
    if applied and applied.get('checkpoint') in {'applied','audited'}:
        with exclusive(root):
            applied=store.get_cvp_group(step_id)
            if applied['reviewed']['evidence']['cvp_management']!=evidence_for(step_id):
                raise ValueError('applied trial evidence differs from authenticated answers')
            readback,pins=_readback(store,evidence_for(step_id))
            persistence.complete_step(store,step_id,readback=readback,read_pins=pins)
            persistence.progress(store,parent_id,group_key=group_key,status='completed',step_id=step_id)
            from backend.pipeline.viewpoint_route_cvp_worker import handoff_cvp_route_jobs
            handoff_cvp_route_jobs(store,applied['reviewed']['evidence']['package']['payload'],
                                   root/'route-queue-v2',archive)
            return store.get_cvp_group(parent_id)['result']
    preflight=prepare_recall(store,request.input,allow_unqualified=True,defer_index=True)
    if preflight['admission']['blocked_claims']:
        raise ValueError('verified pair lost upstream Claim qualification')
    _guard(store,bundle)
    with exclusive(root):
        parent=persistence.prepare_action(store,request,root=str(root),preflight=preflight)
        parent_id=parent['run_id'];group_key=parent['groups'][0]['group_key']
        if parent['units'][group_key]['status']=='completed':
            from backend.pipeline.viewpoint_route_cvp_worker import handoff_completed_cvp_groups
            handoff_completed_cvp_groups(store,parent,root)
            return parent['result']
        if parent.get('obsolete_history'):
            old_calls=parent['obsolete_history']['call_keys']
            old_steps=parent['obsolete_history']['step_ids']
        else:
            old_calls=list(parent['call_reservations'])
            old_steps=[sid for unit in parent['units'].values() for sid in unit['steps']]
        persistence.mark_obsolete_unwritten_history(store,parent_id,
            call_keys=old_calls,step_ids=old_steps)
        materials={'bundle':bundle,'previous':first,'prior_findings':findings,
            'step_root':str(archive),'recall_root':material['recall_root'],
            'index_root':material['index_root']}
        step_id=persistence.prepare_step(store,parent_id,group_key=group_key,
            generation=generation,
            ordinal=0,materials=materials,revision=True)
        for name,(role,model,packet,raw,artifact) in calls.items():
            intent={**model_request_identity(model,packet),'request_bytes':model.request_bytes(packet),
                'cache_path':str(paths[name])}
            persistence.import_verified_call(store,parent_id,key=keys[name],role=role,
                intent=intent,raw=raw,artifact_sha256=artifact['artifact_sha256'],
                trial_root=str(paths[name].parent))
        evidence=evidence_for(step_id)
        package=validate_evidence(evidence)
        _guard(store,bundle)
        checkpoint=store.get_cvp_group(step_id)['checkpoint']
        if checkpoint=='reviewed':
            resume_reviewed_action(store,step_id)
        elif checkpoint=='prepared':
            commit_package(store,step_id,package=package,approval=binding(archive/'final_review.json'),
                authorization=request.authorization.model_dump(),backup={},sources=bundle['sources'],
                read_pins={(r['collection'],r['object_id']):(r['revision'],r['content_sha256'],False)
                    for r in bundle['records']},reason='CVP2 verified four-call trial',
                actor=domain.VERSION,source_kind=domain.VERSION,management_evidence=evidence)
        readback,pins=_readback(store,evidence)
        persistence.complete_step(store,step_id,readback=readback,read_pins=pins)
        persistence.progress(store,parent_id,group_key=group_key,status='completed',step_id=step_id)
        from backend.pipeline.viewpoint_route_cvp_worker import handoff_cvp_route_jobs
        handoff_cvp_route_jobs(store,package,root/'route-queue-v2',archive)
        return store.get_cvp_group(parent_id)['result']


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--supersede-unwritten-run',required=True)
    args=parser.parse_args(argv)
    load_dotenv(Path(__file__).resolve().parents[2]/'.env')
    store=PostgresKnowledgeStore()
    print(commit_approved_trial(store,args.root,supersede_unwritten_run=args.supersede_unwritten_run))


if __name__=='__main__':main()
