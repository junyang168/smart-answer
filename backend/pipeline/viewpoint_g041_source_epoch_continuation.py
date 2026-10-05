"""Once-only fresh affected-group audits after the verified source repair."""
import fcntl
from pathlib import Path
from backend.pipeline import viewpoint_g041_source_epoch as epoch

repair=epoch.repair
ROOT=repair.APPLICATION_ROOT/'continuation'


def run():
    from backend.pipeline.viewpoint_cvp_recovery_executor import load_campaign,run_logged,PYTHON,resume_after_repair,binding
    from backend.pipeline.viewpoint_content_blocked_fallback import apply,verify_saved_full_audit
    from backend.pipeline.viewpoint_audit_source_adjudication import run as adjudicate,disputes,validate_population
    from backend.pipeline.viewpoint_audit_owner_resolution import create_gpt_resolution
    if ROOT.exists():
        raise ValueError('source epoch continuation already attempted; no repeat')
    store=repair.old.PostgresKnowledgeStore()
    epoch.verified_applied(epoch.binding(),store)
    runtime=repair.old.canonical_runtime()
    cfg,schedule,manifest,groups=load_campaign(repair.CAMPAIGN)
    ROOT.mkdir()
    repair.old.write_immutable(ROOT/'runtime-proof.json',runtime)
    repair.old.write_immutable(ROOT/'audit-intent.json',{'group_sequences':[40,41],
        'source_repair_binding':epoch.binding(),'old_models_replayed':False,'registry_writes':0})
    results={}
    for sequence in (40,41):
        key=next(r['group_key'] for r in schedule['schedule'] if r['sequence']==sequence)
        group=repair.CAMPAIGN/f'g{sequence:03d}'
        plans=list((group/'production/run/batches').glob('*/cvp-change-plan.json'))
        if len(plans)!=1:raise ValueError('ambiguous original applied group plan')
        targets=sorted({o['payload']['viewpoint_id'] for o in repair.old.read_artifact(plans[0])['operations']
            if o['collection']=='viewpoint_claim_links'})
        out=ROOT/f'g{sequence:03d}-audit';out.mkdir()
        run_logged([PYTHON,'scripts/audit-library.py','--scope','all','--layers','1,2,3,4',
            '--claim-ids',','.join(groups[key]['claim_ids']),'--viewpoint-ids',','.join(targets),
            '--workers','2','--model','gemini-3.8-flash','--out',out/'independent-reaudit',
            '--no-status-file','--preserve-full-context'],out,'independent-reaudit')
        original=out/'independent-reaudit/audit.json'
        proof=verify_saved_full_audit(original)
        repair.old.write_immutable(out/'current-full-input-proof.json',{'bindings':proof})
        audit=apply(original,out/'content-blocked-fallback')
        resolution=None
        if disputes(repair.old.read_artifact(audit)):
            validate_population(repair.old.read_artifact(audit),groups[key]['claim_ids'],targets)
            arbitration=out/'audit-source-adjudication'
            report=adjudicate(audit,arbitration,store,final_gpt=True)
            if not report['actions'] or any(a['verdict']!='audit_objection_rejected' for a in report['actions']):
                raise ValueError('fresh source audit objection upheld/unresolved; needs owner, no loop')
            resolution=out/'gpt-source-final-resolution.json'
            create_gpt_resolution(audit,arbitration,resolution,store)
        completion=epoch.completion_from_saved_audit(sequence,audit,ROOT/f'g{sequence:03d}-verified',resolution)
        results[sequence]=(audit,resolution,completion)
        repair.old.write_derived(repair.APPLICATION_ROOT/'progress.json',{'status':'affected_group_audit_verified',
            'sequence':sequence,'master_data_mutations':612,'new_group_completed':False})
    attempt=ROOT/'resume';attempt.mkdir()
    audit,resolution,completion=results[41]
    receipt=resume_after_repair(repair.CAMPAIGN,attempt,repair.old.BASE/'immediate-recovery-20261002-v1',
        Path(runtime['identity']['cli']['path']),saved_audit_path=audit,owner_resolution_path=resolution,
        additional_source_repair_paths=[epoch.PACKAGE],additional_proof_bindings=completion['bindings'],
        source_epoch_recompletion_path=ROOT/'g040-verified/completion.json')
    repair.old.write_derived(repair.APPLICATION_ROOT/'progress.json',{'status':receipt['status'],
        'master_data_mutations':612,'continuation':receipt})
    return receipt


def main():
    from dotenv import load_dotenv
    load_dotenv(repair.old.PROJECT/'.env')
    with (repair.old.BASE/'immediate-recovery-20261002-v1/recovery.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:run()
        except Exception as exc:
            if ROOT.exists():
                repair.old.write_immutable(ROOT/'failure.json',{'error':str(exc),'registry_writes':0,
                    'old_application_preserved':True,'automatic_retry':False})
            raise


if __name__=='__main__':main()
