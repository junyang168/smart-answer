"""Resume audit only after an already applied, dual-reviewed narrow link repair.

Reverse the exact three operations in a read-only snapshot and prove the entire
pre-Registry fingerprint; do not reapply, regenerate or repeat saved judgments.
"""
from copy import deepcopy
import fcntl
import os
from pathlib import Path
from dotenv import load_dotenv
from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable
from backend.api.canonical_repository.viewpoint_foundation import sha256_json

def verify_transition(live, before, packet, plan):
    from backend.pipeline.viewpoint_authorized_source_repair_357 import fingerprint
    reverse=deepcopy(live); originals={i['before_link']['viewpoint_claim_link_id']:i['before_link'] for i in packet['targets']}
    if sorted({c for c,_ in live})!=before['collections']:
        raise ValueError('Registry collection boundary drift')
    if len(plan['operations'])!=3*len(originals):raise ValueError('not exact narrow supports repair')
    seen=set()
    for op in plan['operations']:
        key=op['collection'],op['object_id']
        if key in seen:raise ValueError('duplicate operation')
        seen.add(key); expected=dict(op['payload'],revision=op['after_revision'])
        if live.get(key)!=expected:raise ValueError('applied exact postimage drift')
        if op['collection']=='viewpoint_claim_links' and op['operation']=='update' and op['object_id'] in originals:
            reverse[key]=originals[op['object_id']]
        elif op['collection'] in {'viewpoint_identity_candidates','viewpoint_identity_decisions'} and op['operation']=='create':
            del reverse[key]
        else:raise ValueError('unexpected mutation in applied repair')
    if fingerprint(reverse,before['collections'])!=before['pre_fingerprint_sha256']:
        raise ValueError('whole Registry reverse fingerprint drift')
    return reverse

def run(campaign, repair, output, prior_adjudication):
    from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot,fingerprint,Review,review_call
    from backend.pipeline.viewpoint_audit_link_repair import BASE,PROJECT,PROMPT,package_for
    from backend.pipeline.viewpoint_cvp_recovery_executor import load_campaign,run_logged,PYTHON,binding,resume_after_repair
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    from backend.pipeline.viewpoint_resolution_runtime import subscription_client
    from backend.pipeline.viewpoint_content_blocked_fallback import apply
    from backend.pipeline.viewpoint_audit_source_adjudication import disputes
    from backend.pipeline.viewpoint_reused_source_resolution import create
    load_dotenv(PROJECT/'.env')
    os.environ['CODEX_EXECUTABLE']='/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'
    with (BASE/'immediate-recovery-20261002-v1/recovery.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if output.exists():raise ValueError('existing continuation attempt; inspect receipts')
        output.mkdir()
        try:
            store=PostgresKnowledgeStore();live=snapshot(store)
            packet=read_artifact(repair/'packet.json'); before=read_artifact(repair/'before.json');plan=read_artifact(repair/'dry-run-plan.json')
            receipt=read_artifact(repair/'apply-receipt.json'); verified=read_artifact(repair/'verified-result.json')
            if (receipt['change_set_id']!=plan['change_set_id'] or verified['change_set_id']!=plan['change_set_id']
                    or verified['status']!='applied_readback_verified' or verified['claim_evidence_source_writes']!=0):
                raise ValueError('missing real applied readback')
            reverse=verify_transition(live,before,packet,plan);reviews={}
            for label,provider,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
                if not (repair/f'raw-{label}.json').exists():raise ValueError('saved review missing; no fallback')
                adapter=StructuredJsonReviewerAdapter(client=subscription_client(provider,model,'high'),prompt=PROMPT,response_model=Review,schema_name='audit_link_repair_review_v1',compact_payload=True)
                adapter.max_request_bytes=500000
                response,calls,_=review_call(adapter,packet,repair,label)
                if calls or any(not v.approved or v.problems for v in Review.model_validate(response).verdicts):raise ValueError('saved dual review not approved')
                reviews[label]=response
            if package_for(reverse,packet,reviews,repair)!=read_artifact(repair/'effective-package.json'):
                raise ValueError('applied package does not match saved double review')
            for item in packet['targets']:
                for source in item['sources']:
                    if binding(source['path'])['file_sha256']!=source['file_sha256']:raise ValueError('physical source drift')
            write_immutable(output/'preflight.json',{'applied_repair_root':str(repair),'change_set_id':plan['change_set_id'],
                'full_registry_fingerprint_sha256':fingerprint(live,before['collections']),'model_replays':0,'registry_writes':0})
            cfg,schedule,manifest,groups=load_campaign(campaign);state=read_artifact(campaign/'progress.json')
            plans=list((campaign/f"g{state['sequence']:03d}/production/run/batches").glob('*/cvp-change-plan.json'))
            if len(plans)!=1:raise ValueError('original group plan ambiguous')
            targets=sorted({o['payload']['viewpoint_id'] for o in read_artifact(plans[0])['operations'] if o['collection']=='viewpoint_claim_links'})
            audit_root=output/'independent-reaudit'
            run_logged([PYTHON,'scripts/audit-library.py','--scope','all','--layers','1,2,3,4','--claim-ids',','.join(groups[state['group_key']]['claim_ids']),
                '--viewpoint-ids',','.join(targets),'--workers','2','--model','gemini-3.8-flash','--out',audit_root,'--no-status-file','--preserve-full-context'],output,'independent-reaudit')
            audit_path=apply(audit_root/'audit.json',output/'content-blocked-fallback');resolution_path=None
            if disputes(read_artifact(audit_path)):
                resolution_path=output/'reused-source-resolution.json'
                create(audit_path,prior_adjudication,resolution_path,store)
            if snapshot(store)!=live:raise ValueError('Registry changed during audit continuation')
            proof_files=['packet.json','raw-gpt.json','raw-opus.json','review-report.json','effective-package.json','dry-run-plan.json','backup.json','apply-intent.json','apply-receipt.json','verified-result.json']
            result=resume_after_repair(campaign,output/'continuation',BASE/'immediate-recovery-20261002-v1',Path(os.environ['CODEX_EXECUTABLE']),
                saved_audit_path=audit_path,owner_resolution_path=resolution_path,
                additional_proof_bindings=[binding(repair/n) for n in proof_files]+[binding(output/'preflight.json')])
            write_immutable(output/'result.json',result);return result
        except Exception as exc:
            write_immutable(output/'failure.json',{'error':str(exc),'repair_already_applied':True,'no_retry':True});raise

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser(description=__doc__)
    for key in ('campaign','repair','output','prior-adjudication'):ap.add_argument('--'+key,type=Path,required=True)
    a=ap.parse_args();print(run(a.campaign,a.repair,a.output,a.prior_adjudication))
