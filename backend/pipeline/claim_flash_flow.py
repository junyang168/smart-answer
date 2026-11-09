"""Thin, layered Flash interface with a complete program-exported postimage.

No semantic task is implemented here. Existing stage receipts remain authority;
this interface supplies order, a source-local output, and an honest pending exit.
"""
from copy import deepcopy
import fcntl
import json
from pathlib import Path
import time

from backend.pipeline import evidence_refresh as e, claim_flash_common as f
from backend.pipeline import claim_flash_grouping as grouping, claim_flash_source as source
from backend.pipeline import claim_flash_coverage as coverage, claim_flash_source_fill as fill
from backend.pipeline import claim_flash_evidence as eo, claim_flash_statements as statements
from backend.pipeline import claim_flash_arguments as arguments, claim_flash_relations as relations
from backend.pipeline import claim_flash_missing as missing
from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS
from backend.pipeline.knowledge_package_merge import validate_merged_package, KnowledgePackageMergeError

VERSION='claim_flash_layered_flow_v1'


def read(path):return json.loads(Path(path).read_text())


def complete(result):
    return result.get('status') in {'verified','applied','completed'} and not result.get('pending')


def claim_tasks(plan):
    tasks=[]
    for group in plan['groups']:
        batches=group.get('sequential_claim_batches', [group['claim_ids']])
        if ([cid for batch in batches for cid in batch] != group['claim_ids']
                or any(len(batch)>10 for batch in batches)
                or len(set(group['claim_ids'])) != len(group['claim_ids'])):
            raise ValueError('sequential Claim batches must partition their frozen group exactly once')
        tasks.extend((group,batch,n) for n,batch in enumerate(batches,1))
    return tasks


def snapshot(store,sid):
    records,sources=f.load_source(store,sid,visual_context=True)
    f.source_unchanged(sources)
    return records,sources


def accounting(root,provider_root=None):
    """Actual saved receipts only; cached/reasoning tokens are reported subsets."""
    result={}
    for role in ('gpt','jev'):
        usage={'input_tokens':0,'output_tokens':0,'cached_input_tokens':0,'reasoning_output_tokens':0}
        calls=missing_usage=0
        for started in Path(root).rglob(role+'/*/started.json'):
            calls+=1;folder=started.parent
            if role=='gpt':
                provider=(Path(provider_root)/'gpt'/folder.name/'provider.json') if provider_root else folder/'provider.json'
                raw=read(provider).get('usage') if provider.exists() else None
            else:
                raw=read(folder/'response.json').get('usage') if (folder/'response.json').exists() else None
            if not isinstance(raw,dict):missing_usage+=1;continue
            for key in ('input_tokens','output_tokens'):usage[key]+=raw.get(key,0) or 0
            usage['cached_input_tokens']+=raw.get('cached_input_tokens',(raw.get('input_tokens_details') or {}).get('cached_tokens',0)) or 0
            usage['reasoning_output_tokens']+=raw.get('reasoning_output_tokens',(raw.get('output_tokens_details') or {}).get('reasoning_tokens',0)) or 0
        result[role]={'actual_calls':calls,'usage':usage,'missing_usage_receipts':missing_usage,
            'total_tokens':usage['input_tokens']+usage['output_tokens'] if not missing_usage else None}
    result['dollar_cost']=None
    return result


def export(store,sid,root,before,original,stages,pending,started,mode):
    """Read actual SQL postimages; never reconstruct a final graph from patches."""
    records,sources=snapshot(store,sid)
    result_package=deepcopy(original) if original else {'schema_version':'wang_shared_knowledge_v1.3'}
    for col in KNOWLEDGE_COLLECTIONS:
        if col in result_package or any(k.startswith(col+'/') for k in records):
            result_package[col]=[deepcopy(v['payload']) for k,v in sorted(records.items()) if k.startswith(col+'/')]
    changed={'created':[],'updated':[],'retired':[]};retired=[]
    for key,row in records.items():
        if key not in before:changed['created'].append(key)
        elif row['content_sha256']!=before[key]['content_sha256']:changed['updated'].append(key)
    for key,row in before.items():
        if key in records:continue
        current=store.get_record_state(*key.split('/',1))
        if not current or not current['retired']:raise ValueError('Flash output lost an input record: '+key)
        changed['retired'].append(key);retired.append({'key':key,'record':current['payload']})
    # Retired payloads stay in history, not as live dangling graph nodes.
    result_package['package_id']='FLASH-'+e.digest([sid,records])[:24]
    result_package['flash_application']={'version':VERSION,'mode':mode,'legacy_qualification':'not_granted',
        'initial_package_sha256':e.digest(original) if original else None,
        'source_file':sources[sid]['file'],'retired_records':retired,
        'completion':'needs_attention' if pending else 'completed'}
    validation_errors=[]
    try:
        validate_merged_package(result_package,native_relations=True)
    except KnowledgePackageMergeError as exc:
        # A pending report must preserve the real SQL snapshot even when an
        # untouched legacy row is invalid. It grants no package admission.
        if not pending:raise
        validation_errors.append(str(exc))
    digest=e.digest(result_package);path=root/'postimages'/(digest+'.json');e.save(path,result_package)
    result={'version':VERSION,'source_id':sid,'status':'needs_attention' if pending else 'completed',
        'package':{'path':str(path),'sha256':digest},'changes':changed,'pending':pending,
        'next_action':pending[0] if pending else None,'stages':stages,
        'initial_package_sha256':e.digest(original) if original else None,
        'source_file':sources[sid]['file'],'final_pins':{k:{x:v[x] for x in ('revision','content_sha256','retired')} for k,v in records.items()},
        'mode':mode,'canonical_committed':mode=='canonical_refresh' and any(root.rglob('commit.json')),
        'production_writes':0 if mode=='candidate_workspace' else None,
        'legacy_qualification':'not_granted','wall_seconds_this_invocation':round(time.monotonic()-started,3)}
    if validation_errors:result['package_validation_errors']=validation_errors
    # Bind exact saved stage outputs; no second model-generated report.
    result['evidence_refs']=[{'path':str(p.resolve()),'sha256':e.digest(read(p))} for p in sorted(root.rglob('result.json'))
        if p.parent!=root and 'postimages' not in p.parts]
    e.save(root/'exports'/(e.digest(result)+'.json'),result)
    return e.publish(root,result)


def run(store,source_id,root,ports,*,plan=None,candidate=None):
    """Apply each authorized layer to its store; candidate mode requires private SQL.

    Passing a canonical store authorizes no writes by itself: its caller must
    already have the refresh authority. Candidate entry creates only private SQL.
    """
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True);started=time.monotonic()
    with (root/'flow.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        mode='candidate_workspace' if candidate is not None else 'canonical_refresh'
        if candidate is not None and not getattr(store,'_flash_candidate_workspace',None):
            raise ValueError('candidate Flash requires its private isolated workspace')
        records,sources=snapshot(store,source_id)
        intent={'version':VERSION,'source_id':source_id,'source_file':sources[source_id]['file'],
                'models':ports.identity,'candidate_sha256':e.digest(candidate) if candidate else None,'supplied_plan':plan,'mode':mode}
        e.save(root/'flow-intent.json',intent)
        checkpoint=None;resume_claims=False
        if (root/'result.json').exists():
            saved=read(root/'result.json')
            if saved['status']=='completed':
                pins={k:{x:v[x] for x in ('revision','content_sha256','retired')} for k,v in records.items()}
                if pins!=saved['final_pins']:raise ValueError('completed Flash input changed; explicit affected-stage continuation required')
                if e.digest(read(saved['package']['path']))!=saved['package']['sha256']:raise ValueError('Flash output package changed')
                for ref in saved['evidence_refs']:
                    if e.digest(read(ref['path']))!=ref['sha256']:raise ValueError('Flash saved stage evidence changed')
                return saved
            if saved.get('pending') and saved['pending'][0].get('layer') in {'evidence','claims'}:
                pins={k:{x:v[x] for x in ('revision','content_sha256','retired')} for k,v in records.items()}
                if pins!=saved['final_pins'] or saved['source_file']!=sources[source_id]['file']:
                    raise ValueError('pending Evidence checkpoint input changed; explicit continuation required')
                if e.digest(read(saved['package']['path']))!=saved['package']['sha256']:
                    raise ValueError('pending Flash output package changed')
                for ref in saved['evidence_refs']:
                    if e.digest(read(ref['path']))!=ref['sha256']:raise ValueError('pending Flash stage evidence changed')
                checkpoint=saved['stages']
                resume_claims=saved['pending'][0]['layer']=='claims'
        if (root/'before.json').exists():before=read(root/'before.json')
        else:before=records;e.save(root/'before.json',before)
        def finish(stages,pending):
            result=export(store,source_id,root,before,candidate,stages,pending,started,mode)
            result['accounting']=accounting(root,getattr(ports,'root',None))
            return e.publish(root,result)
        if plan is None:
            gp=root/'grouping'/'shared-groups.json'
            plan=read(gp) if gp.exists() else grouping.save(store,source_id,root/'grouping',allow_claim_batches=True)
        coverage.validate_groups(plan,source_id,sources[source_id])
        e.save(root/'shared-groups.json',plan)
        if plan.get('pending'):return finish({},[{'layer':'grouping','findings':plan['pending']}])
        if checkpoint is not None:
            stages=deepcopy(checkpoint)
        else:
            stages={}
            sr=source.run(store,source_id,root/'source'/e.digest([source.CONTRACT,records]));stages['source']=sr
            if sr['status']=='calibration_available':
                work=root/'calibration';work.mkdir(exist_ok=True)
                prepared={'version':'claim_flash_mechanical_source_v1','source_id':source_id,'status':'verified',
                    'updates':sr['calibration_candidates']['updates'],'new_fragments':sr['calibration_candidates']['new_fragments'],
                    'retiring_keys':[],'pending':[],'applied':False,'claim_qualification':'not_granted'}
                e.save(work/'before.json',{'records':records,'sources':sources});e.save(work/'verification.json',prepared)
                e.commit_verified(store,work)
                records,sources=snapshot(store,source_id)
                sr=source.run(store,source_id,root/'source'/e.digest([source.CONTRACT,records]));stages['source']=sr
            if sr['status']!='verified':return finish(stages,[{'layer':'source','findings':sr['records']}])
            cv=coverage.run(store,source_id,root/'coverage',ports,plan=plan);stages['coverage']=cv
            findings=[v for row in cv['findings'] for v in row['findings']]
            if any(v['verdict']=='needs_context' for v in findings):
                return finish(stages,[{'layer':'source','findings':[v for v in findings if v['verdict']=='needs_context']}])
            if any(v['verdict'] in fill.MISSING for v in findings):
                fr=fill.run(store,source_id,cv['report_path'],root/'fill',apply=True);stages['fill']=fr
                if not complete(fr):return finish(stages,[{'layer':'source','findings':fr.get('pending',[])}])
        attempted={item['job_id'] for p in (root/'source-handoffs').glob('*/verification.json')
                   for item in read(p).get('outcomes',[])}
        while not resume_claims:
            er=eo.run(store,source_id,plan,root/'evidence',ports,root/'coverage',apply=True);stages['evidence']=er
            handoffs=er.get('pending',[])+[p for g in er.get('groups',[]) for p in g.get('pending',[])]
            if complete(er) or not handoffs or any(p.get('layer')!='source' for p in handoffs):break
            units=[p for p in handoffs if p.get('unit')]
            if units:
                token='units-'+e.digest(units)
                if token in attempted:break
                attempted.add(token)
                repaired=fill.fill_inventory_handoff(store,source_id,units,root/'coverage',root/'source-handoffs'/token,apply=True)
                stages.setdefault('source_handoffs',{})[token]=repaired
                if not complete(repaired) or not repaired['new_fragments']:break
            handoffs=[p for p in handoffs if not p.get('unit')]
            if any(not p.get('object_key') or p['object_key'] in attempted for p in handoffs):break
            # One source completion per object in this operation. Previously
            # accepted lower judgments are consumed by EO's existing cache.
            for item in handoffs:
                key=item['object_key'];attempted.add(key)
                repaired=source.repair_bindings(store,source_id,[item],root/'source-handoffs'/e.digest(key),ports,apply=True)
                stages.setdefault('source_handoffs',{})[key]=repaired
                if not complete(repaired) or not repaired['updates']:break
            else:continue
            break
        if resume_claims:
            er=stages['evidence']
            if not complete(er):raise ValueError('Claim checkpoint lacks completed Evidence layer')
        if not complete(er):return finish(stages,[{'layer':'evidence','findings':er.get('pending',[]) +
            [p for g in er.get('groups',[]) for p in g.get('pending',[])],
            'affected_prior_group_ids':er.get('affected_prior_group_ids',[])}])
        stages.setdefault('claims',{})
        tasks=claim_tasks(plan)
        for group,ids,batch_number in tasks:
            gid=group['group_id'];task_id=gid+(f'-batch-{batch_number}' if group.get('sequential_claim_batches') else '')
            gr=root/'claims'/task_id
            if not ids:
                old=stages['claims'].get(gid,{}).get('missing',{})
                if resume_claims and complete(old):
                    if e.digest(read(gr/'missing/result.json'))!=e.digest(old):
                        raise ValueError('completed Claim checkpoint stage differs')
                    continue
                value=missing.run(store,source_id,None,gr/'missing',ports,apply=True,
                    max_new_claims=2,empty_group=group,evidence_root=root/'evidence')
                stages['claims'][gid]={'missing':value}
                if not complete(value):return finish(stages,[{'layer':'claims','group_id':gid,'task':'missing','findings':value.get('pending',[])}])
                continue
            outputs=stages['claims'].setdefault(task_id,{})
            for name,fn in (
                ('statements',lambda:statements.run(store,source_id,ids,root/'evidence',gr/'statements',ports,apply=True)),
                ('arguments',lambda:arguments.run(store,source_id,plan,gid,root/'evidence',gr/'statements',gr/'arguments',ports,apply=True,
                    claim_ids=ids if group.get('sequential_claim_batches') else None)),
                ('relations',lambda:relations.run(store,source_id,gr/'arguments',gr/'relations',ports,apply=True)),
                ('missing',lambda:missing.run(store,source_id,gr/'relations',gr/'missing',ports,apply=True))):
                if resume_claims and complete(outputs.get(name,{})):
                    if e.digest(read(gr/name/'result.json'))!=e.digest(outputs[name]):
                        raise ValueError('completed Claim checkpoint stage differs')
                    continue
                value=fn();outputs[name]=value
                if not complete(value):return finish(stages,[{'layer':'claims','group_id':gid,'task':name,'findings':value.get('pending',[])}])
        return finish(stages,[])


def run_candidate(candidate_path,root,ports,*,plan=None):
    from backend.pipeline.claim_candidate_workspace import open_candidate
    candidate=read(candidate_path);root=Path(root).resolve()
    if not candidate.get('extraction'):raise ValueError('Flash candidate requires extraction provenance')
    sid=candidate['source_documents'][0]['source_id']
    with open_candidate(candidate,root/'workspace') as store:
        return run(store,sid,root/'flash',ports,plan=plan,candidate=candidate)


def main():
    import argparse
    from dotenv import load_dotenv
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    from backend.pipeline.claim_model_capacity import ClaimModelCapacity
    from backend.pipeline.claim_refresh import Models
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    parser=argparse.ArgumentParser(description='Layered Flash with full result package and exact stage receipts')
    for name in ('root','capacity-file'):parser.add_argument('--'+name,required=True)
    selector=parser.add_mutually_exclusive_group(required=True)
    selector.add_argument('--candidate',help='Complete creation package; only private candidate SQL')
    selector.add_argument('--source-id',help='Existing canonical source; requires explicit --apply')
    parser.add_argument('--apply',action='store_true');parser.add_argument('--database-url')
    parser.add_argument('--shared-groups');parser.add_argument('--codex-executable')
    args=parser.parse_args()
    if args.candidate and (args.apply or args.database_url):parser.error('candidate mode never accepts canonical write flags or database URLs')
    if args.source_id and not args.apply:parser.error('canonical flow requires explicit --apply')
    load_dotenv('.env');root=Path(args.root).resolve()
    client=CodexSubscriptionClient(model='gpt-6.1-sol',reasoning_effort='medium',max_output_tokens=6000,executable=args.codex_executable)
    ports=Models(client,root,capacity=ClaimModelCapacity(read(args.capacity_file)))
    plan=read(args.shared_groups) if args.shared_groups else None
    if args.candidate:result=run_candidate(args.candidate,root,ports,plan=plan)
    else:result=run(PostgresKnowledgeStore(args.database_url),args.source_id,root,ports,plan=plan)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
