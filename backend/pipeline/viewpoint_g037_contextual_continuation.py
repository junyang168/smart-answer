"""Read-only completion of g037 using saved applied repairs and new resolution.

No repair writer or model fallback: prove 3 then 19 applied operations in a
reverse snapshot, reuse full audit and explicit contextual conflict judgment.
Only the ordinary next-group dry-run/backup/serial producer may advance.
"""
import argparse
import fcntl
from pathlib import Path
import shutil

from dotenv import load_dotenv
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable, subscription_client,call_model
from backend.pipeline.viewpoint_g037_contextual_conflict import BASE,OUTPUT as JUDGMENT,LATEST

REPAIR=LATEST.parents[1]
CLAIM_REPAIR=BASE/'g037-owner-claim-condition-repair-20261005-v1'
CAMPAIGN=BASE/'campaign-recovery-2fc29744d5b0204c7b1e472f1f0e0862a914c4ab2e1ee0c4742935aeb699914f'
OUTPUT=BASE/'g037-contextual-applied-completion-20261005-v1'


def verify_applied_chain(store):
    from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot
    from backend.pipeline.viewpoint_applied_link_repair_continuation import verify_transition
    from backend.pipeline.viewpoint_g037_secondary_supports import verify_transition as prior_transition
    from backend.pipeline.viewpoint_g037_claim_condition_repair import validate_package
    from backend.pipeline.viewpoint_audit_link_repair import link_review_adapter,PROMPT,package_for
    from backend.api.canonical_repository.viewpoint_foundation import sha256_json
    from backend.pipeline.viewpoint_cvp_recovery_executor import binding
    live=snapshot(store)
    packet=read_artifact(REPAIR/'packet.json');plan=read_artifact(REPAIR/'dry-run-plan.json')
    if plan['change_set_id']!='KCS-8b2be154d709aa8af3c1':raise ValueError('wrong applied secondary repair')
    for root, count in ((REPAIR,3),(CLAIM_REPAIR,19)):
        receipt=read_artifact(root/'apply-receipt.json');verified=read_artifact(root/'verified-result.json')
        p=read_artifact(root/'dry-run-plan.json')
        if (receipt.get('status')!='applied' or receipt['change_set_id']!=p['change_set_id']
                or verified['change_set_id']!=p['change_set_id'] or verified['status']!='applied_readback_verified'
                or verified['master_data_mutations']!=count):raise ValueError('applied/readback proof missing')
        if binding(read_artifact(root/'backup.json')['path'])!=read_artifact(root/'backup.json'):
            raise ValueError('applied backup physical drift')
    reverse=verify_transition(live,read_artifact(REPAIR/'before.json'),packet,plan)
    reviews={}
    for label,provider,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
        adapter=link_review_adapter(subscription_client(provider,model,'high'),PROMPT)
        cache=REPAIR/f'raw-{label}.json'
        if not cache.is_file():raise ValueError('missing saved review; no fallback')
        def forbidden(_):raise ValueError('saved double review replay forbidden')
        adapter.generate=forbidden
        response,calls,_=call_model(adapter,packet,cache)
        rows=response['verdicts']
        if calls or len(rows)!=1 or rows[0]['claim_id']!=packet['targets'][0]['claim']['claim_id'] or any(not v['approved'] or v['problems'] for v in rows):
            raise ValueError('saved secondary double review not approved')
        reviews[label]=response
    if package_for(reverse,packet,reviews,REPAIR)!=read_artifact(REPAIR/'effective-package.json'):
        raise ValueError('applied secondary package binding drift')
    cp=read_artifact(CLAIM_REPAIR/'effective-package.json');validate_package(cp)
    prior_transition(reverse,read_artifact(CLAIM_REPAIR/'before.json'),read_artifact(CLAIM_REPAIR/'packet.json'),
                     read_artifact(CLAIM_REPAIR/'dry-run-plan.json'),cp)
    prior=read_artifact(REPAIR/'prior-applied-proof.json')['bindings']
    for b in prior:
        if binding(b['path'])!=b:raise ValueError('frozen prior Claim proof changed')
    for root in (REPAIR,CLAIM_REPAIR):
        for t in read_artifact(root/'packet.json')['targets']:
            for source in t['sources']:
                if binding(source['path'])!={'path':source['path'],'file_sha256':source['file_sha256']}:
                    raise ValueError('applied repair own source drift')
    names=('packet.json','before.json','owner-authorization.json','raw-gpt.json','raw-opus.json',
           'raw-gpt.request-encoding.json','raw-opus.request-encoding.json','review-report.json',
           'effective-package.json','dry-run-plan.json','backup.json','apply-intent.json','apply-receipt.json','verified-result.json')
    proofs=prior+[binding(root/n) for root in (REPAIR,CLAIM_REPAIR) for n in names]
    return live,proofs


def run(*,preflight_only=False):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.viewpoint_adjudication_history import validated_history
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet,validate_population
    from backend.pipeline.viewpoint_content_blocked_fallback import validate,verify_saved_full_audit
    from backend.pipeline.viewpoint_cvp_recovery_executor import binding,load_campaign,resume_after_repair
    from backend.pipeline.viewpoint_audit_owner_resolution import create_gpt_resolution
    from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot
    with (BASE/'immediate-recovery-20261002-v1/recovery.lock').open('r') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if OUTPUT.exists():raise ValueError('existing completion attempt; inspect, never replay')
        store=PostgresKnowledgeStore();live,proofs=verify_applied_chain(store)
        packet,findings=validated_history(JUDGMENT)
        if not findings or any(f['verdict']!='audit_objection_rejected' for f in findings.values()):
            raise ValueError('new contextual judgment upheld/unresolved; stop')
        authority=read_artifact(JUDGMENT/'owner-authorization.json')
        if authority!=read_artifact(LATEST/'owner-contextual-conflict-start.json') or authority['output_root']!=str(JUDGMENT):
            raise ValueError('explicit conflict authority drift')
        for b in authority['bindings']:
            if binding(b['path'])!=b:raise ValueError('conflict historical/calibration physical drift')
        audit=Path(packet['audit_binding']['path'])
        if audit!=REPAIR/'continuation/content-blocked-fallback/assessment.json' or build_packet(audit,store)!=packet:
            raise ValueError('full saved audit/current Registry packet drift')
        proofs+=validate(read_artifact(audit))
        proofs+=verify_saved_full_audit(REPAIR/'continuation/independent-reaudit/audit.json')
        cfg,schedule,manifest,groups=load_campaign(CAMPAIGN)
        state=read_artifact(CAMPAIGN/'progress.json')
        if state['sequence']!=37:raise ValueError('only g037 completion authorized')
        plans=list((CAMPAIGN/'g037/production/run/batches').glob('*/cvp-change-plan.json'))
        if len(plans)!=1:raise ValueError('original group plan ambiguous')
        targets=sorted({o['payload']['viewpoint_id'] for o in read_artifact(plans[0])['operations'] if o['collection']=='viewpoint_claim_links'})
        validate_population(read_artifact(audit),groups[state['group_key']]['claim_ids'],targets)
        proofs+=[binding(p) for p in sorted(JUDGMENT.glob('*.json'))]+authority['bindings']
        if snapshot(store)!=live:raise ValueError('Registry changed during read-only proof')
        proof={'status':'exact_saved_proofs_verified','registry_writes':0,'model_replays':0,
               'claim_count':len(groups[state['group_key']]['claim_ids']),'bindings':proofs}
        if preflight_only:return {k:v for k,v in proof.items() if k!='bindings'}|{'binding_count':len(proofs)}
        OUTPUT.mkdir()
        try:
            write_immutable(OUTPUT/'preflight.json',proof)
            resolution=OUTPUT/'gpt-source-final-resolution.json'
            # Required raw already exists and validated above; the existing
            # consumer can only validate/reuse it, never initiate a fresh call.
            create_gpt_resolution(audit,JUDGMENT,resolution,store)
            result=resume_after_repair(CAMPAIGN,OUTPUT/'continuation',BASE/'immediate-recovery-20261002-v1',
                Path(shutil.which('codex')),saved_audit_path=audit,owner_resolution_path=resolution,
                additional_source_repair_paths=[CLAIM_REPAIR/'effective-package.json'],
                additional_proof_bindings=proofs+[binding(OUTPUT/'preflight.json')],contextual_semantics=True)
            write_immutable(OUTPUT/'result.json',result);return result
        except Exception as exc:
            write_immutable(OUTPUT/'failure.json',{'error':str(exc),'already_applied':True,'no_retry':True});raise


if __name__=='__main__':
    load_dotenv(Path(__file__).resolve().parents[2]/'.env')
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--preflight-only',action='store_true')
    print(run(preflight_only=parser.parse_args().preflight_only))
