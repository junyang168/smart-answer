"""Audit the composed source+supports postimage and then resume the unchanged campaign."""
from copy import deepcopy
import fcntl
from pathlib import Path

from dotenv import load_dotenv
from backend.pipeline import viewpoint_s201129_link_support_apply as child
from backend.pipeline import viewpoint_s201129_source_repair as source
from backend.pipeline import viewpoint_s201129_source_epoch as epoch
from backend.pipeline import viewpoint_s201129_source_continuation as original
from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable
from backend.pipeline.viewpoint_cvp_recovery_executor import load_campaign,resume_after_repair

ROOT=child.ROOT/'continuation'
RECOMPLETIONS=ROOT/'recompleted-groups.json'


def recompletion_seed(path,seeds):
    if Path(path)!=RECOMPLETIONS:raise ValueError('unknown supports recompletion proof root')
    epoch.verified_applied(epoch.binding(),source.PostgresKnowledgeStore())
    receipt=read_artifact(RECOMPLETIONS)
    if (receipt['source_repair_binding']!=epoch.binding() or receipt['child_connection_repair_binding']!=source.binding(child.PACKAGE)
            or receipt['sequences']!=[40,41]):
        raise ValueError('composed source/connection recompletion scope drift')
    result=deepcopy(seeds)
    for sequence in (40,41):
        saved=read_artifact(ROOT/f'g{sequence:03d}-verified/completion.json')
        matches=[s for s in result if s['group_key']==saved['group_key']]
        if len(matches)!=1 or source.sha256_json(matches[0])!=saved['replaces_original_completion_sha256']:
            raise ValueError('cannot replace another or already-replaced completed group')
        rebuilt=original.completion_from_saved_audit(sequence,Path(saved['audit_path']),
            ROOT/f'g{sequence:03d}-verified',Path(saved['owner_resolution_path']) if saved.get('owner_resolution_path') else None,
            root=ROOT)
        if saved!=rebuilt:raise ValueError('composed fresh completion reconstruction drift')
        result=[saved if s['group_key']==saved['group_key'] else s for s in result]
    return result


def run():
    from backend.pipeline.cvp_retired_runtime import reject_legacy_execution
    reject_legacy_execution()
    if ROOT.exists():raise ValueError('composed audit already attempted; inspect receipts, no replay')
    package,_,_=epoch.verified_applied(epoch.binding(),source.PostgresKnowledgeStore())
    if list((source.CAMPAIGN/'g042').glob('production/run/batches/*/cvp-change-plan.json')):
        raise ValueError('g042 has an application plan; cannot replay')
    runtime=original.canonical_runtime()
    write_immutable(ROOT/'runtime-proof.json',runtime)
    proofs=[source.binding(child.PACKAGE),source.binding(child.ROOT/'verified-result.json')]
    write_immutable(ROOT/'audit-intent.json',{'source_repair_binding':epoch.binding(),
        'child_connection_repair_binding':proofs[0],'bindings':proofs,
        'affected_completed_sequences':[40,41],'historical_completed_groups':41,'historical_completed_claims':228,
        'Registry_writes':0,'old_models_or_401_operations_replayed':False})
    audit,resolution=original.verified_audit('local-repair-audit',epoch.reviewed_claim_ids(package),
        {l['viewpoint_id'] for l in package['viewpoint_claim_links']},root=ROOT)
    write_immutable(ROOT/'local-repair-audit/validated-result.json',{'audit':source.binding(audit),
        'resolution':source.binding(resolution) if resolution else None,'bindings':proofs})
    cfg,schedule,manifest,groups=load_campaign(source.CAMPAIGN)
    old=read_artifact(Path(cfg['completed_proofs']['path']))['groups']
    for sequence in (40,41):
        key=next(r['group_key'] for r in schedule['schedule'] if r['sequence']==sequence)
        prior=next(s for s in old if s['group_key']==key)
        audit,resolution=original.verified_audit(f'g{sequence:03d}-audit',set(groups[key]['claim_ids']),
            set(prior['viewpoint_ids']),root=ROOT)
        original.completion_from_saved_audit(sequence,audit,ROOT/f'g{sequence:03d}-verified',resolution,root=ROOT)
    write_immutable(RECOMPLETIONS,{'source_repair_binding':epoch.binding(),
        'child_connection_repair_binding':source.binding(child.PACKAGE),'sequences':[40,41],
        'bindings':[source.binding(ROOT/f'g{s:03d}-verified/completion.json') for s in (40,41)],
        'extra_claims_or_groups_completed':0})
    attempt=ROOT/'resume';attempt.mkdir()
    receipt=resume_after_repair(source.CAMPAIGN,attempt,source.BASE/'immediate-recovery-20261002-v1',
        Path(runtime['identity']['cli']['path']),additional_source_repair_paths=[source.PACKAGE],
        source_epoch_recompletion_path=RECOMPLETIONS)
    write_immutable(ROOT/'result.json',{'status':receipt['status'],'continuation':receipt,
        'Registry_writes':0,'historical_groups':41,'startup_not_completion':True})
    return receipt


if __name__=='__main__':
    load_dotenv(source.PROJECT/'.env')
    with (source.BASE/'immediate-recovery-20261002-v1/recovery.lock').open('r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:run()
        except Exception as exc:
            if ROOT.exists():write_immutable(ROOT/'failure.json',{'error':str(exc),'Registry_writes':0,
                'automatic_model_retry':False,'production_resumed':False})
            raise
