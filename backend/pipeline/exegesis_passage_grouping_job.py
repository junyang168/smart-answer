"""L2 only: reuse a reviewed L1 manifest, bounded parallel subscription workers."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import fcntl
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from dotenv import load_dotenv
from backend.pipeline import exegesis_intelligent_grouping_job as core
from backend.pipeline.exegesis_grouping_transport import call, write_new
from backend.pipeline.exegesis_passage_unit_job import retain
from backend.pipeline.viewpoint_passage_grouping_preflight import plan_reviewed_passage_unit
from backend.pipeline.viewpoint_passage_grouping_sample_runner import split_reviewed_unit
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_batch_resolution import ClaimGroupingResponse


def load_manifest(path):
    manifest=core.checked(path)
    if manifest['schema_version']!='wang_exegesis_passage_units_v1' or not manifest['layer_1_semantic_passed']:
        raise ValueError('reviewed L1 passage manifest required; no preview authorization')
    if manifest['layer_2_executed']:raise ValueError('input already reports L2 execution')
    claims=manifest['claim_packets'];index={c['claim_id']:c for c in claims}
    core.exact([c['claim_id'] for c in claims],index,'frozen Claim IDs')
    core.exact([c for u in manifest['units'] for c in u['claim_ids']],index,'reviewed L1 membership')
    if len({u['unit_id'] for u in manifest['units']})!=len(manifest['units']):raise ValueError('duplicate unit IDs')
    for u in manifest['units']:
        if not u['claim_ids']:raise ValueError('empty reviewed passage')
        core.passage_sort_key(u['passage_key'])
    return manifest


def validate_groups(answer,unit):
    core.exact([c for g in answer['groups'] for c in g['claim_ids']],unit['claim_ids'],'whole-passage grouping')
    if any(not g['claim_ids'] or len(g['claim_ids'])>20 for g in answer['groups']):raise ValueError('group ceiling/empty group')
    if len({g['group_key'] for g in answer['groups']})!=len(answer['groups']):raise ValueError('duplicate group keys')
    if len(unit['claim_ids'])<=20 and answer!=plan_reviewed_passage_unit(unit_id=unit['unit_id'],claim_ids=unit['claim_ids'],batch_size=20).model_dump(mode='json'):
        raise ValueError('small reviewed unit must remain a single whole group')



def load_direct_preparation(directory, manifest):
    if directory is None:return {},None
    prepared=core.checked(directory/'preparation.json')
    if prepared['input_manifest_sha256']!=manifest['artifact_sha256']:
        raise ValueError('direct preparation belongs to another L1 manifest')
    expected={u['unit_id']:u for u in manifest['units'] if len(u['claim_ids'])<=20}
    answers={}
    for uid,unit in expected.items():
        artifact=core.checked(directory/(uid+'.json'))
        if artifact['input_manifest_sha256']!=manifest['artifact_sha256'] or artifact['unit_id']!=uid:
            raise ValueError('direct prepared unit binding mismatch')
        validate_groups(artifact['grouping'],unit)
        answers[uid]=artifact
    if prepared['completed_direct_units']!=len(answers) or prepared['completed_direct_claims']!=sum(len(u['claim_ids']) for u in expected.values()):
        raise ValueError('direct preparation counts differ')
    return answers,prepared['artifact_sha256']



def recover_scope_labels(directory, manifest, model):
    """Reuse raw successes rejected solely for scope metadata; never change groups."""
    if directory is None:return {},None
    config=core.checked(directory/'config.json')
    if config['input_manifest_sha256']!=manifest['artifact_sha256'] or config['model']!=model or config['provider']!='claude':
        raise ValueError('scope recovery input/model mismatch')
    recovered={}
    for unit in manifest['units']:
        uid=unit['unit_id'];response_path=directory/'groups'/uid/'response.json'
        if not response_path.exists():continue
        failure_path=directory/('failure-'+uid+'.json')
        if not failure_path.exists():continue
        failure=core.checked(failure_path)
        if 'grouping is for scope' not in failure['error'] or not failure['error'].endswith('not '+uid):
            continue
        request=core.checked(response_path.with_name('request.json'));raw=core.checked(response_path)
        if raw['request_sha256']!=request['artifact_sha256'] or request['model']!=model or request['provider']!='claude':
            raise ValueError('raw scope recovery request binding mismatch')
        if request['payload']['reviewed_unit']!=unit or request['payload']['input_manifest_sha256']!=manifest['artifact_sha256']:
            raise ValueError('raw scope recovery unit binding mismatch')
        core.exact([c['claim_id'] for c in request['payload']['claims']],unit['claim_ids'],'recovery request membership')
        before=raw['response'];answer=dict(before,scope_label=uid)
        validated=plan_reviewed_passage_unit(unit_id=uid,claim_ids=unit['claim_ids'],batch_size=20,
            model_split=ClaimGroupingResponse.model_validate(answer)).model_dump(mode='json')
        validate_groups(validated,unit)
        if validated['groups']!=before['groups']:raise ValueError('scope recovery changed argument groups')
        recovered[uid]=dict(grouping=validated,raw_response_sha256=raw['artifact_sha256'],request_sha256=request['artifact_sha256'],
            normalization=dict(field='scope_label',before=before['scope_label'],after=uid),groups_unchanged=True)
    return recovered,config['artifact_sha256']


def execute(args):
    root=args.output_root.resolve();root.mkdir(parents=True,exist_ok=True)
    lock=(root/'job.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    global_lock=(root.parent/'.411-l2-job.lock').open('a');fcntl.flock(global_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    load_dotenv('.env');os.environ['CODEX_EXECUTABLE']=str(args.codex_executable);os.environ['CLAUDE_EXECUTABLE']=str(args.claude_executable)
    manifest=load_manifest(args.manifest);sources_art=core.checked(args.sources)
    sids={c['source_id'] for c in manifest['claim_packets']};sources=[s for s in sources_art['sources'] if s['source_id'] in sids]
    core.verify_current(manifest['claim_packets']);core.verify_files(sources)
    direct,preparation_sha=load_direct_preparation(args.direct_preparation,manifest)
    recovered,recovery_config_sha=recover_scope_labels(args.recover_scope_root,manifest,args.model)
    config=dict(layer=2,input_manifest_sha256=manifest['artifact_sha256'],sources_sha256=sources_art['artifact_sha256'],
        model=args.model,provider='claude',effort='high',direct_preparation_sha256=preparation_sha,recovery_config_sha256=recovery_config_sha,reviewer_model='gpt-6.1-sol',reviewer_provider='gpt',workers=args.workers,
        max_request_bytes=args.max_request_bytes,max_group_size=20,code_shas={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
        [Path(__file__),Path(core.__file__),core.REVIEWER,Path(__file__).with_name('viewpoint_passage_grouping_sample_runner.py'),Path(__file__).with_name('viewpoint_passage_grouping_preflight.py'),Path(__file__).with_name('exegesis_grouping_transport.py'),Path(__file__).with_name('exegesis_grouping_packet.py')]},
        prompt_shas={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [core.PROMPTS/'exegesis_argument_grouping.md',core.PROMPTS/'exegesis_matthew_16_19_regression.md']})
    retain(root,'config.json',config)
    mutex=threading.Lock();states={};results={};failures={}
    def status(stage,**extra):
        with mutex:
            data=dict(stage=stage,pid=os.getpid(),layer=2,workers=args.workers,updated_at=time.time(),completed_units=len(results),failed_units=len(failures),unit_states=dict(states),**extra)
            tmp=root/'status.tmp';tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));tmp.replace(root/'status.json')
            with (root/'events.jsonl').open('a') as f:f.write(json.dumps(data,ensure_ascii=False)+'\n')
        print(json.dumps({k:v for k,v in data.items() if k!='unit_states'},ensure_ascii=False),flush=True)
    status('preflight',claims=len(manifest['claim_packets']),units=len(manifest['units']))
    # Reuse bound successful probe; unknown/unsupported exact model stops without downgrade.
    probe=root/'model-probe'
    probe_fp=sha256_json(config|{'stage':'model-probe'})
    core.obtain(probe,probe_fp,lambda:call(provider='claude',model=args.model,effort='high',prompt='Return only the required JSON, no tools.',payload={'task':'subscription model availability check'},schema={'type':'object','additionalProperties':False,'required':['ok'],'properties':{'ok':{'type':'boolean','const':True}}},directory=probe,max_bytes=args.max_request_bytes))
    raw=core.checked(probe/'transport.raw.json');metadata=json.loads(raw['stdout'])
    used=list(metadata.get('modelUsage',{}))
    if not used or any(not key.startswith(args.model) for key in used):raise ValueError('model probe did not confirm exact requested model: '+str(used))
    retain(root,'model-confirmation.json',dict(requested=args.model,actual_model_ids=used,probe_sha256=core.checked(probe/'validated.json')['artifact_sha256']))
    index={c['claim_id']:c for c in manifest['claim_packets']}
    reviewer_args=SimpleNamespace(provider='claude',reviewer_provider='gpt',reviewer_model='gpt-6.1-sol',effort='high',max_request_bytes=args.max_request_bytes)
    def work(unit):
        uid=unit['unit_id'];directory=root/'groups'/uid
        with mutex:states[uid]='grouping'
        status('running')
        members=[index[c] for c in unit['claim_ids']]
        # Read-only existing ownership view for the existing independent reviewer;
        # blank primary stays blank, and no candidate is promoted to ownership.
        model_members=[dict(c,ownership=dict(primary=manifest['input_primary_ownership'][c['claim_id']],evidence=[],read_only=True)) for c in members]
        payload=core.payload_for(model_members,sources,scope_label=uid,reviewed_unit=unit,layer=2,input_manifest_sha256=manifest['artifact_sha256']) if len(members)>20 else dict(reviewed_unit=unit,input_manifest_sha256=manifest['artifact_sha256'])
        fingerprint=sha256_json(dict(config=config,payload=payload,stage='grouping'))
        def generate():
            if len(members)<=20:
                directory.mkdir(parents=True,exist_ok=False)
                if uid in direct:return direct[uid]['grouping']
                return plan_reviewed_passage_unit(unit_id=uid,claim_ids=unit['claim_ids'],batch_size=20).model_dump(mode='json')
            if uid in recovered:
                directory.mkdir(parents=True,exist_ok=False)
                retain(directory,'scope-normalization.json',recovered[uid])
                return recovered[uid]['grouping']
            return split_reviewed_unit(unit_id=uid,payload=payload,provider='claude',model=args.model,effort='high',directory=directory,max_request_bytes=args.max_request_bytes,regression_context=unit['passage_key']=='Matt.16.19').model_dump(mode='json')
        answer=core.obtain(directory,fingerprint,generate);validate_groups(answer,unit)
        if len(members)>20:
            with mutex:states[uid]='independent_review'
            status('running')
            binding=sha256_json(dict(config=config,payload=payload,proposal=answer))
            review=core.independent_review(payload,answer,root/'reviews'/uid,reviewer_args,binding)
            basis=dict(type='independent_argument_boundary_review',review_sha256=review['artifact_sha256'])
        else:basis=dict(type='deterministic_whole_reviewed_passage',l1_manifest_sha256=manifest['artifact_sha256'],reused_prepared_artifact_sha256=direct[uid]['artifact_sha256'] if uid in direct else None)
        result=retain(root,'unit-'+uid+'.json',dict(unit_id=uid,grouping=answer,validation_basis=basis))
        with mutex:states[uid]='completed';results[uid]=result
        status('running')
    units=sorted(manifest['units'],key=lambda u:(*core.passage_sort_key(u['passage_key']),u['unit_id']))
    # Direct groups first, without any grouping/review model call.
    for unit in units:
        if len(unit['claim_ids'])<=20:work(unit)
    pending=iter(u for u in units if len(u['claim_ids'])>20)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        active={}
        for _ in range(args.workers):
            unit=next(pending,None)
            if unit:active[executor.submit(work,unit)]=unit
        while active:
            done,_=wait(active,return_when=FIRST_COMPLETED)
            for future in done:
                unit=active.pop(future)
                try:future.result()
                except Exception as exc:
                    with mutex:states[unit['unit_id']]='failed';failures[unit['unit_id']]=str(exc)
                    retain(root,'failure-'+unit['unit_id']+'.json',dict(error=str(exc),error_type=type(exc).__name__))
            if not failures:
                for _ in done:
                    unit=next(pending,None)
                    if unit:active[executor.submit(work,unit)]=unit
    core.verify_current(manifest['claim_packets']);core.verify_files(sources)
    groups=[dict(g,unit_id=u['unit_id'],group_key=u['unit_id']+':'+g['group_key']) for u in units if u['unit_id'] in results for g in results[u['unit_id']]['grouping']['groups']]
    unprocessed=[u['unit_id'] for u in units if u['unit_id'] not in results]
    if not unprocessed:core.exact([c for g in groups for c in g['claim_ids']],index,'global L2 exact coverage')
    retain(root,'manifest.json',dict(schema_version='wang_exegesis_grouping_manifest_v1',status='complete' if not unprocessed else 'explicit_unresolved',
        scope='Matthew only',input_manifest_sha256=manifest['artifact_sha256'],groups=groups,units=units,claim_packets=manifest['claim_packets'],input_primary_ownership=manifest['input_primary_ownership'],sources=sources,
        validation_bases=[results[k]['validation_basis'] for k in sorted(results)],unresolved=failures,unprocessed_unit_ids=unprocessed,cvp_generated=0,master_data_mutations=0,full_3843_scope_completed=False))
    retain(root,'validation-report.json',dict(eligible_count=len(index),grouped_count=sum(len(g['claim_ids']) for g in groups),groups=len(groups),unprocessed_unit_ids=unprocessed,
        semantic_passed=not unprocessed,missing=len(set(index)-{c for g in groups for c in g['claim_ids']}),duplicate=0,foreign=0,group_ceiling_pass=True,primary_unchanged=True,provenance_preserved=True))
    status('completed' if not unprocessed else 'completed_with_unresolved',group_count=len(groups),unprocessed_unit_ids=unprocessed)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for field in ['manifest','sources','output-root','codex-executable','claude-executable']:parser.add_argument('--'+field,type=Path,required=True)
    parser.add_argument('--recover-scope-root',type=Path,help='Recover SHA-bound raw splits rejected solely for scope label metadata')
    parser.add_argument('--direct-preparation',type=Path,help='Reuse sealed deterministic small-unit outputs bound to this L1 manifest')
    parser.add_argument('--model',default='claude-opus-5-5');parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--max-request-bytes',type=int,default=2500000)
    args=parser.parse_args()
    if not 1<=args.workers<=3:parser.error('workers must be 1..3')
    try:execute(args)
    except Exception as exc:
        root=args.output_root
        if root.exists():
            status=dict(stage='failed',pid=os.getpid(),updated_at=time.time(),error=str(exc),layer=2)
            temp=root/'controller-status.tmp';temp.write_text(json.dumps(status,ensure_ascii=False));temp.replace(root/'status.json')
            write_new(root/f'controller-failure-{time.time_ns()}.json',dict(error=str(exc),error_type=type(exc).__name__))
        raise
if __name__=='__main__':main()
