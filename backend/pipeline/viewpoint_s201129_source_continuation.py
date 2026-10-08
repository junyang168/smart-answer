"""After the real S201129 write, audit affected groups and resume, without replay."""
from copy import deepcopy
import fcntl
import os
from pathlib import Path

from dotenv import load_dotenv

from backend.pipeline import viewpoint_s201129_source_repair as repair
from backend.pipeline import viewpoint_s201129_source_epoch as epoch
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable
from backend.pipeline.viewpoint_cvp_campaign_runner import binding, validate_audit
from backend.pipeline.viewpoint_cvp_recovery_executor import load_campaign, run_logged, PYTHON
from backend.pipeline.viewpoint_content_blocked_fallback import apply as fallback, verify_saved_full_audit
from backend.pipeline.viewpoint_audit_source_adjudication import run as adjudicate, disputes, validate_population
from backend.pipeline.viewpoint_audit_owner_resolution import create_gpt_resolution
from backend.pipeline.viewpoint_audit_link_repair import write_derived

ROOT = repair.APPLICATION_ROOT/'continuation'
RECOMPLETIONS = ROOT/'recompleted-groups.json'


def verified_audit(ident, claim_ids, viewpoint_ids, *, root=None):
    """One fresh audit; real filter receipts only; upheld/new dispute stops."""
    out = (root or ROOT)/ident
    if out.exists():
        raise ValueError('audit already attempted; inspect exact saved receipts, never rerun')
    out.mkdir()
    run_logged([PYTHON,'scripts/audit-library.py','--scope','all','--layers','1,2,3,4',
        '--claim-ids',','.join(sorted(claim_ids)),'--viewpoint-ids',','.join(sorted(viewpoint_ids)),
        '--workers','2','--model','gemini-3.8-flash','--out',out/'independent-reaudit',
        '--no-status-file','--preserve-full-context'],out,'independent-reaudit')
    original = out/'independent-reaudit/audit.json'
    proofs = verify_saved_full_audit(original)
    audit = fallback(original,out/'content-blocked-fallback')
    resolution = None
    if disputes(read_artifact(audit)):
        validate_population(read_artifact(audit),sorted(claim_ids),sorted(viewpoint_ids))
        report = adjudicate(audit,out/'audit-source-adjudication',repair.PostgresKnowledgeStore(),final_gpt=True)
        if not report['actions'] or any(a['verdict'] != 'audit_objection_rejected' for a in report['actions']):
            raise ValueError('new-source independent objection upheld/unresolved; no correction loop')
        resolution = out/'gpt-source-final-resolution.json'
        create_gpt_resolution(audit,out/'audit-source-adjudication',resolution,repair.PostgresKnowledgeStore())
    validate_audit(read_artifact(audit),sorted(claim_ids),sorted(viewpoint_ids),owner_resolution_path=resolution)
    write_immutable(out/'current-full-input-proof.json',{'bindings':proofs,'audit_path':str(audit),
        'resolution_path':str(resolution) if resolution else None})
    return audit, resolution


def completion_from_saved_audit(sequence, audit_path, output, resolution_path=None, *, root=None):
    from backend.pipeline.viewpoint_scoped_audit_disposition import scope_audit_disposition
    from backend.api.canonical_repository.viewpoint_production_safety import accepted_grouping_audit_scope
    active_root = root or ROOT
    child_bindings = []
    if active_root != ROOT:
        from backend.pipeline.viewpoint_s201129_link_support_continuation import ROOT as CHILD_ROOT
        from backend.pipeline import viewpoint_s201129_link_support_apply as child
        if active_root != CHILD_ROOT:
            raise ValueError('unknown source continuation root')
        child_bindings = [binding(child.PACKAGE),binding(child.ROOT/'verified-result.json'),
            *read_artifact(child.ROOT/'verified-result.json')['bindings']]
    if sequence not in {40,41} or output != active_root/f'g{sequence:03d}-verified':
        raise ValueError('S201129 only the two already-completed affected groups')
    epoch.verified_applied(epoch.binding(),repair.PostgresKnowledgeStore())
    cfg, schedule, manifest, groups = load_campaign(repair.CAMPAIGN)
    key = next(r['group_key'] for r in schedule['schedule'] if r['sequence'] == sequence)
    prior = read_artifact(Path(cfg['completed_proofs']['path']))['groups']
    matches = [s for s in prior if s['group_key'] == key]
    if len(matches) != 1:
        raise ValueError('exact previous completed group proof not unique')
    old = matches[0]
    scope, _, _, _ = repair.reviewed_inputs()
    _, historic_source_bindings = repair.historical_seed_projection(old,scope)
    audit = read_artifact(audit_path)
    original = active_root/f'g{sequence:03d}-audit/independent-reaudit/audit.json'
    if Path(audit_path) not in {original,active_root/f'g{sequence:03d}-audit/content-blocked-fallback/assessment.json'}:
        raise ValueError('affected audit outside exact new-source root')
    proofs = verify_saved_full_audit(original)
    validate_audit(audit,groups[key]['claim_ids'],old['viewpoint_ids'],owner_resolution_path=resolution_path)
    disposition = scope_audit_disposition(audit={'meta':audit['meta'],
        'layers':{k:audit['layers'][k] for k in ('1','2')}},
        manifest=accepted_grouping_audit_scope(manifest,'group:'+key),
        audit_file_sha256=repair.file_sha256(audit_path),authorized_by=cfg['authorized_by'])
    if disposition['status'] != 'passed':
        raise ValueError('affected group mechanical audit failed')
    fresh = deepcopy(old)
    fresh.update(audit_path=str(audit_path), claim_audit_path=str(audit_path),
                 source_epoch_recompletion=True,source_repair_binding=epoch.binding(),
                 replaces_original_completion_sha256=repair.sha256_json(old),
                 historical_changed_source_bindings=historic_source_bindings,
                 bindings=[b for b in old['bindings'] if b not in historic_source_bindings]+[binding(audit_path),*proofs,
                    *read_artifact(repair.APPLICATION_ROOT/'verified-result.json')['bindings'],
                    binding(repair.APPLICATION_ROOT/'verified-result.json'),*child_bindings])
    if child_bindings:
        fresh['child_connection_repair_binding'] = binding(child.PACKAGE)
    # Never inherit a prior source-epoch arbitration as approval of new input.
    fresh.pop('owner_resolution_path',None)
    fresh.pop('claim_audit_path',None)
    from backend.pipeline.viewpoint_g035_temporary_audit import validate as validate_exception
    exception = validate_exception(audit)
    if exception:
        from backend.pipeline.viewpoint_content_blocked_fallback import KEY
        fresh.update(revisit_required=True,independent_audit_complete=False,
            temporary_audit_exception_path=audit['meta'][KEY]['path'])
        fresh['bindings'] += exception
    if resolution_path:
        fresh['owner_resolution_path'] = str(resolution_path)
        fresh['bindings'] += [binding(resolution_path),*read_artifact(resolution_path)['bindings']]
    write_immutable(output/'audit-disposition.json',disposition)
    write_immutable(output/'completion.json',fresh)
    return fresh


def recompletion_seed(path, seeds):
    if Path(path) != RECOMPLETIONS:
        raise ValueError('S201129 recompletion outside exact root')
    receipt = read_artifact(RECOMPLETIONS)
    if receipt['source_repair_binding'] != epoch.binding() or receipt['sequences'] != [40,41]:
        raise ValueError('S201129 affected group scope drift')
    result = deepcopy(seeds)
    for sequence in (40,41):
        saved = read_artifact(ROOT/f'g{sequence:03d}-verified/completion.json')
        matches = [s for s in result if s['group_key'] == saved['group_key']]
        if len(matches) != 1 or repair.sha256_json(matches[0]) != saved['replaces_original_completion_sha256']:
            raise ValueError('S201129 cannot replace another or already-replaced group proof')
        rebuilt = completion_from_saved_audit(sequence,Path(saved['audit_path']),ROOT/f'g{sequence:03d}-verified',
            Path(saved['owner_resolution_path']) if saved.get('owner_resolution_path') else None)
        if saved != rebuilt:
            raise ValueError('fresh S201129 completion reconstruction drift')
        result = [saved if s['group_key'] == saved['group_key'] else s for s in result]
    return result


def canonical_runtime():
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    from backend.pipeline.codex_subscription_preflight import verify_runtime
    saved = read_artifact(repair.BASE/'s201129-owner-local-reextraction-20261005-v2/runtime-proof.json')
    executable = saved['identity']['cli']['path']
    client = CodexSubscriptionClient(model='gpt-6.1-sol',reasoning_effort='high',executable=executable)
    client._verify_chatgpt_login()
    auth = Path(client.environment.get('CODEX_HOME',str(Path.home()/'.codex')))/'auth.json'
    actual = {'cli':client.cli_identity,'model':client.model,'backend':client.backend,
        'reasoning_effort':client.reasoning_effort,'login_state_sha256':repair.file_sha256(auth)}
    if actual != saved['identity']:
        raise ValueError('canonical binary/model/effort/login identity changed; no diagnostic fallback')
    reused = verify_runtime(client,Path(saved['proof_path']).parent)
    if reused['proof_path'] != saved['proof_path'] or not reused['reused'] or reused['new_model_calls'] != 0:
        raise ValueError('canonical diagnostic must reuse the prior success proof')
    os.environ['CODEX_EXECUTABLE'] = executable
    return reused


def run():
    from backend.pipeline.cvp_retired_runtime import reject_legacy_execution
    reject_legacy_execution()
    if ROOT.exists():
        raise ValueError('source continuation already attempted; inspect raw receipts, no replay')
    package, _, _ = epoch.verified_applied(epoch.binding(),repair.PostgresKnowledgeStore())
    if list((repair.CAMPAIGN/'g042').glob('production/run/batches/*/cvp-change-plan.json')):
        raise ValueError('g042 has a saved write plan; exact application proof needed, no replay')
    cfg, schedule, manifest, groups = load_campaign(repair.CAMPAIGN)
    ROOT.mkdir()
    runtime = canonical_runtime()
    write_immutable(ROOT/'runtime-proof.json',runtime)
    write_immutable(ROOT/'audit-intent.json',{'source_repair_binding':epoch.binding(),
        'completed_group_sequences':[40,41],'repair_claim_ids':sorted(epoch.reviewed_claim_ids(package)),
        'old_models_or_changesets_replayed':False,'Registry_writes':0})
    write_derived(repair.APPLICATION_ROOT/'progress.json',{'status':'new_source_affected_full_audit',
        'master_data_mutations':401,'completed_groups':41,'completed_claims':228,'production_resumed':False})
    repair_targets = {l['viewpoint_id'] for l in package['viewpoint_claim_links']}
    audit, resolution = verified_audit('local-repair-audit',epoch.reviewed_claim_ids(package),repair_targets)
    write_immutable(ROOT/'local-repair-audit/validated-result.json',{'audit':binding(audit),
        'resolution':binding(resolution) if resolution else None,'source_repair_binding':epoch.binding(),
        'new_group_completion_asserted':False})
    old = read_artifact(Path(cfg['completed_proofs']['path']))['groups']
    for sequence in (40,41):
        key = next(r['group_key'] for r in schedule['schedule'] if r['sequence'] == sequence)
        prior = next(s for s in old if s['group_key'] == key)
        audit, resolution = verified_audit(f'g{sequence:03d}-audit',set(groups[key]['claim_ids']),set(prior['viewpoint_ids']))
        completion_from_saved_audit(sequence,audit,ROOT/f'g{sequence:03d}-verified',resolution)
    write_immutable(RECOMPLETIONS,{'source_repair_binding':epoch.binding(),'sequences':[40,41],
        'bindings':[binding(ROOT/f'g{s:03d}-verified/completion.json') for s in (40,41)],
        'extra_claims_or_groups_completed':0})
    attempt = ROOT/'resume'
    attempt.mkdir()
    from backend.pipeline.viewpoint_cvp_recovery_executor import resume_after_repair
    receipt = resume_after_repair(repair.CAMPAIGN,attempt,repair.BASE/'immediate-recovery-20261002-v1',
        Path(runtime['identity']['cli']['path']),additional_source_repair_paths=[repair.PACKAGE],
        source_epoch_recompletion_path=RECOMPLETIONS)
    write_derived(repair.APPLICATION_ROOT/'progress.json',{'status':receipt['status'],
        'master_data_mutations':401,'continuation':receipt,'production_complete':False})
    return receipt


def main():
    load_dotenv(repair.PROJECT/'.env')
    with (repair.BASE/'immediate-recovery-20261002-v1/recovery.lock').open('r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            run()
        except Exception as exc:
            if ROOT.exists():
                write_immutable(ROOT/'failure.json',{'error':str(exc),'Registry_writes':0,
                    'original_application_retained':True,'automatic_model_retry':False,'production_resumed':False})
            raise


if __name__ == '__main__':
    main()
