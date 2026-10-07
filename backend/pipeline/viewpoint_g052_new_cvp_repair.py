"""Exact owner-authorized missing-CVP completion; never replay g052's 73 ops."""
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess

from dotenv import load_dotenv
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, ChangeOperation, stored_operation_payload
from backend.api.canonical_repository.viewpoint_foundation import sha256_json, semantic_record_sha
from backend.api.canonical_repository.viewpoint_production_safety import file_sha256, exclusive_cvp_run_lock, REGISTRY_COLLECTIONS
from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim, StructuredJsonReviewerAdapter
from backend.api.canonical_repository.viewpoint_batch_changeset import compile_cvp_batch_package
from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot, fingerprint
from backend.pipeline.viewpoint_resolution_runtime import read_artifact as read, write_immutable, write_derived, stable_decided_at
from backend.pipeline.viewpoint_batch_resolution_runner import run_batch, build_proposer, build_reviewer, build_reconsiderer, build_consolidator
from backend.pipeline.viewpoint_scope_packet_runner import registry_context
from backend.pipeline.viewpoint_cvp_policy import load_cvp_policy
from backend.pipeline.viewpoint_deferred_source_check import build_packet as source_packet, run as source_check

PROJECT = Path(__file__).resolve().parents[2]
BASE = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357')
PARENT = BASE/'campaign-single-writer-20261007-v1-recovery-g051'
FAILED_ROOT = BASE/'g052-owner-new-cvp-repair-20261007-v1'
ROOT = BASE/'g052-owner-new-cvp-repair-20261007-v2'
COMPLETION_ROOT = BASE/'g052-applied-new-cvp-completion-20261007-v1'
GROUP = PARENT/'g052'
BATCH = GROUP/'production/run/batches/75e337ead6567ce77497686395e25b60f699720cf1771d4469a4a749593fcfc3'
CLAIM = 'DK-0bdf88fe8565-P09-CL007'
PIN = 'a8f37a21123e7d26f64a934ef30dea75db30ce9ca6f93007a8fdfded0b8b834b'
VERSION = 'wang_g052_owner_new_cvp_completion_v1'
AUTH = '同意生成一个新的 CVP，这似乎是第一次遇到这个问题'
CONSTRAINT = '''\n本次独立负责人授权仅为 DK-0bdf88fe8565-P09-CL007 建立一个新的原子 CVP 及其完整 Claim 成员连接。
该Claim完整命题已由自身E011支持；同段上下文仅消解“這”，不另造“条件性饶恕关系”正向观点。独立否定命题本身可以成为CVP，不因没有正向目标而defer。
必须忠于完整Claim自身实际意思，保留饶恕条件关系范围，不扩大为一切行为或一般恩典论。Registry全部当前观点仅供防重复比较，不能修改或新增其关系。
只允许一个new_viewpoint候选、一个完整[0,len(statement))new_viewpoint成分；禁止已有观点修订、关系、结构、ArgumentRoute、Claim/Evidence/source修改或no_registry_assertion/deferred。授权不是内容批准，发现无法在范围内忠实提议则明确报告，不为通过制造命题。
'''


def binding(path):
    return {'path':str(path),'file_sha256':file_sha256(path)}


def immutable_review_paths(root):
    # The normal batch validator appends executions / recomputes summaries on
    # cache verification. Those are not semantic approval/source authority.
    derived={'batch-run.json','measurements.json','current-state.json'}
    return [p for p in sorted((root/'review').glob('*.json')) if p.name not in derived]


def verify_ledger(ledger, claim_ids):
    if ledger.get('artifact_sha256') != sha256_json({k:v for k,v in ledger.items() if k!='artifact_sha256'}):
        raise ValueError('ledger SHA drift')
    rows = ledger['claim_dispositions']
    if (len(rows)!=14 or len(claim_ids)!=14 or len(set(claim_ids))!=14
            or sorted(r['claim_id'] for r in rows)!=sorted(claim_ids)
            or ledger['grouping_claim_ids']!=sorted(claim_ids)
            or ledger['status']!='incomplete' or any(ledger.get(k) for k in ('findings','blocked_claims','excluded_claims','unresolved_claim_ids'))
            or any(r['apply_status']!='applied' for r in rows)):
        raise ValueError('not intact original 14 Claim ledger')
    deferred = [r for r in rows if r['resolution_status']!='resolved']
    if len(deferred)!=1 or deferred[0]['claim_id']!=CLAIM or deferred[0]['resolution_status']!='deferred':
        raise ValueError('unapproved deferred population')
    if deferred[0]['components'] != [{'spans':[{'start_char':0,'end_char':24,'exact_text':'饒恕的條件關係並不表示人的行為能夠賺取神的恩典。'}],
            'disposition':'deferred','target_viewpoint_revision_id':None,'local_new_viewpoint_key':None}]:
        raise ValueError('original deferred full component drift')


def validate_scoped_proposal(proposal):
    p = proposal.model_dump(mode='json') if hasattr(proposal,'model_dump') else proposal
    if (len(p['claim_decisions'])!=1 or p['claim_decisions'][0]['claim_id']!=CLAIM
            or len(p['claim_decisions'][0]['components'])!=1 or len(p['new_viewpoint_candidates'])!=1
            or any(p.get(k) for k in ('viewpoint_revisions','viewpoint_relations','viewpoint_structures','argument_route_candidates'))):
        raise ValueError('new-CVP authorization scope exceeded')
    c = p['claim_decisions'][0]['components'][0]
    if (c['disposition']!='new_viewpoint' or c.get('target_viewpoint_revision_id')
            or c['local_new_viewpoint_key']!=p['new_viewpoint_candidates'][0]['local_key']
            or c['spans']!=[{'start_char':0,'end_char':24,'exact_text':'饒恕的條件關係並不表示人的行為能夠賺取神的恩典。'}]
            or c['evidence_step_ids']!=['DK-0bdf88fe8565-P09-E011']
            or c['source_fragment_ids']!=['FR-notes_manuscript_6_3-0bdf88fe8565-DK-0bdf88fe8565-P09-E011-01']):
        raise ValueError('missing/changed full Claim coverage or own evidence')


def validate_plan(plan):
    expected = {'canonical_viewpoints','viewpoint_revisions','viewpoint_claim_links','viewpoint_identity_candidates','viewpoint_identity_decisions'}
    ops = plan['operations']
    if len(ops)!=5 or {o['collection'] for o in ops}!=expected or any(o['operation']!='create' or o['before_revision'] is not None or o['after_revision']!=1 for o in ops):
        raise ValueError('not exact five new authority records')
    link = next(o['payload'] for o in ops if o['collection']=='viewpoint_claim_links')
    if (link['claim_id']!=CLAIM or link['pinned_claim_revision']!=3 or link['link_type']!='equivalent_component'
            or link.get('component_locator')!={'statement_component':'饒恕的條件關係並不表示人的行為能夠賺取神的恩典。','claim_sha256':PIN,
                'canonical_spans':[{'start_char':0,'end_char':24,'exact_text':'饒恕的條件關係並不表示人的行為能夠賺取神的恩典。'}]}):
        raise ValueError('not exact whole Claim equivalent member')
    cv = next(o['payload'] for o in ops if o['collection']=='canonical_viewpoints')
    rev = next(o['payload'] for o in ops if o['collection']=='viewpoint_revisions')
    decision = next(o['payload'] for o in ops if o['collection']=='viewpoint_identity_decisions')
    candidate = next(o['payload'] for o in ops if o['collection']=='viewpoint_identity_candidates')
    if (link['viewpoint_id']!=cv['viewpoint_id'] or link['validated_against_viewpoint_revision_id']!=rev['viewpoint_revision_id']
            or cv['current_revision_id']!=rev['viewpoint_revision_id'] or rev['viewpoint_id']!=cv['viewpoint_id']
            or link['decision_id']!=decision['identity_decision_id'] or decision['identity_candidate_id']!=candidate['identity_candidate_id']):
        raise ValueError('new identity provenance graph drift')
    return cv['viewpoint_id']


def adapters(root, *, cached_only=False, scoped=True):
    policy = load_cvp_policy(PROJECT/'backend/pipeline/policies/wang_cvp_resolution_policy_v5.json')
    out = {}
    for role, builder in (('proposer',build_proposer),('reviewer',build_reviewer),('reconsiderer',build_reconsiderer),('consolidator',build_consolidator)):
        gpt = role in {'proposer','reconsiderer'}
        obj = builder('gpt-6.1-sol' if gpt else 'claude-opus-5-5','high',provider='codex' if gpt else 'claude',cvp_policy=policy)
        if scoped:
            obj = StructuredJsonReviewerAdapter(client=obj._client,prompt=obj._prompt+CONSTRAINT,
                response_model=obj._response_model,schema_name=obj._schema_name,
                compact_payload=True,shared_text_payload=True,shared_record_payload=True)
        obj.max_request_bytes = 500000
        generate = obj.generate
        def once(payload, *, obj=obj, generate=generate, role=role):
            if cached_only: raise ValueError('saved raw missing; model fallback prohibited')
            stage = 'final-review' if role=='reviewer' and payload.get('initial_review') is not None else role
            marker = root/f'{stage}-call-reservation.json'
            if marker.exists(): raise ValueError('model role already reserved; no retry')
            write_immutable(marker,{'role':role,'request_sha256':sha256_json(payload),'request_bytes':obj.request_bytes(payload),'model':obj.model_id,'backend':obj.backend,'max_calls':1})
            write_derived(ROOT/'progress.json',{'status':role,'master_data_mutations':0,'group_complete':False})
            return generate(payload)
        obj.generate = once
        out[role] = obj
    return out


def batch_report(root, packet, *, cached_only=False, scoped=True):
    return run_batch(batch_id=packet['batch_id'],scope_label=packet['scope_label'],claims=[ReviewClaim.model_validate(c) for c in packet['claims']],
        registry_context=packet['registry_context'],pending_candidates=packet['pending_candidates'],output_dir=root,
        **adapters(root,cached_only=cached_only,scoped=scoped))


def verify_parent(store):
    ledger_path=GROUP/'production/run/scope-disposition-ledger.json'
    packet=read(BATCH/'batch-packet.json'); ids=[c['claim_id'] for c in packet['claims']]
    verify_ledger(read(ledger_path),ids)
    scope=read(GROUP/'production/run/scope-run.json')
    if scope['disposition_status']!='incomplete' or scope['disposition_ledger_sha256']!=read(ledger_path)['artifact_sha256']:
        raise ValueError('parent scope binding drift')
    plan=read(BATCH/'cvp-change-plan.json'); receipt=read(BATCH/'cvp-apply-result.json')
    if len(plan['operations'])!=73 or receipt['result']['status']!='applied' or receipt['change_set_id']!=plan['change_set_id'] or store.get_change_set_status(plan['fingerprint_sha256'])!='applied':
        raise ValueError('original 73 operations not actually applied')
    for row in plan['operations']:
        op=ChangeOperation(**row)
        if op.operation!='create' or store.get_record(op.collection,op.object_id)!=stored_operation_payload(op):
            raise ValueError('original g052 exact postimage drift')
    for name in ('cvp-readback-receipt.json','cvp-plan-readback-receipt.json'):
        rb=read(BATCH/name)
        if rb.get('readback_status',rb.get('status')) not in {'verified','passed'}: raise ValueError('parent readback not verified')
    original=batch_report(BATCH,packet,cached_only=True,scoped=False)
    if original['approval_outcome']!='pass' or any(v for k,v in original['measurements'].items() if k.endswith('calls_executed')):
        raise ValueError('original saved review invalid or replayed')
    freeze=read(GROUP/'production/inputs/freeze.json')
    compiled=compile_cvp_batch_package(proposal=original['_effective_proposal'],review=original['_review'],
        deterministic_validation_sha256=original['_effective_validation']['artifact_sha256'],scope_manifest_sha256=freeze['claim_manifest_sha256'],
        claims=[ReviewClaim.model_validate(c) for c in packet['claims']],registry_context=packet['registry_context'],
        revision_dependents=original['_revision_dependents'],proposal_artifact_sha256=read(BATCH/'raw-reconsideration.json')['artifact_sha256'],
        review_artifact_sha256=read(BATCH/'raw-final-review.json')['artifact_sha256'],proposer_model_id='gpt-6.1-sol',reviewer_model_id='claude-opus-5-5',decided_at=stable_decided_at(BATCH))
    if compiled!=read(BATCH/'cvp-change-package.json'): raise ValueError('original actual review/package drift')
    for b in freeze['prerequisites'].values():
        if file_sha256(Path(b['path']))!=b['sha256']: raise ValueError('parent prerequisite physical binding drift')
    # Reconstruct the source investigation from current exact Claim/evidence/physical context.
    investigation=GROUP/'deferred-source-check'
    current=source_packet(packet,read(BATCH/'batch-disposition-applied.json'),store)
    if current!=read(investigation/'packet.json'): raise ValueError('source investigation input drift')
    if any(not (investigation/f'raw-{r}.json').exists() for r in ('gpt','opus')): raise ValueError('source raw absent')
    handoff=source_check(BATCH/'batch-packet.json',BATCH/'batch-disposition-applied.json',investigation,store)
    if (len(handoff['actions'])!=1 or handoff['actions'][0]['item_id']!=CLAIM+':0'
            or not handoff['actions'][0]['models_agree'] or handoff['actions'][0]['next_action']!='reviewed_cvp_repair_required'):
        raise ValueError('not saved sufficient-evidence handoff')
    claim=next(c for c in packet['claims'] if c['claim_id']==CLAIM)
    live=store.get_record('claims',CLAIM)
    if claim['claim_revision_sha256']!=PIN or semantic_record_sha(live)!=PIN or live['revision']!=3:
        raise ValueError('authorized Claim pin drift')
    bindings=[binding(ledger_path),binding(GROUP/'production/run/scope-run.json'),binding(GROUP/'production/inputs/freeze.json'),
        *[binding(BATCH/n) for n in ('batch-packet.json','cvp-change-package.json','cvp-change-plan.json','cvp-apply-result.json','cvp-readback-receipt.json','cvp-plan-readback-receipt.json')],
        *[binding(investigation/n) for n in ('packet.json','handoff.json','raw-gpt.json','raw-opus.json')],
        *[binding(p) for p in sorted(BATCH.glob('raw-*.json'))],
        *[binding(Path(b['path'])) for b in freeze['prerequisites'].values()]]
    return packet, ids, bindings


def reviewed_package(root, packet, freeze, *, cached_only=False):
    report=batch_report(root/'review',packet,cached_only=cached_only)
    if report['approval_outcome']!='pass' or report['escalations']: raise ValueError('new CVP review unresolved')
    validate_scoped_proposal(report['_effective_proposal'])
    proposal_raw=read(root/'review'/('raw-reconsideration.json' if report['_final_review'] is not None else 'raw-proposal.json'))
    review_raw=read(root/'review'/('raw-final-review.json' if report['_final_review'] is not None else 'raw-review.json'))
    return compile_cvp_batch_package(proposal=report['_effective_proposal'],review=report['_review'],
        deterministic_validation_sha256=report['_effective_validation']['artifact_sha256'],scope_manifest_sha256=freeze['claim_manifest_sha256'],
        claims=[ReviewClaim.model_validate(packet['claims'][0])],registry_context=packet['registry_context'],
        proposal_artifact_sha256=proposal_raw['artifact_sha256'],review_artifact_sha256=review_raw['artifact_sha256'],
        proposer_model_id=proposal_raw['model_id'],reviewer_model_id=review_raw['model_id'],decided_at=stable_decided_at(root))


def build_proof(store):
    _, ids, bindings=verify_parent(store)
    plan=read(ROOT/'dry-run-plan.json'); target=validate_plan(plan)
    receipt=read(ROOT/'apply-receipt.json'); verified=read(ROOT/'verified-result.json')
    if receipt['status']!='applied' or receipt['change_set_id']!=plan['change_set_id'] or verified['status']!='applied_readback_verified' or store.get_change_set_status(plan['fingerprint_sha256'])!='applied':
        raise ValueError('new CVP not applied/read back')
    packet=read(ROOT/'packet.json'); before=read(ROOT/'before.json'); live=snapshot(store); reverse=deepcopy(live)
    if sorted({c for c,_ in live})!=before['collections']: raise ValueError('Registry collection boundary drift')
    for raw in plan['operations']:
        op=ChangeOperation(**raw); key=(op.collection,op.object_id)
        if reverse.get(key)!=stored_operation_payload(op): raise ValueError('new CVP exact postimage drift')
        del reverse[key]
    if fingerprint(reverse,before['collections'])!=before['pre_fingerprint_sha256']: raise ValueError('whole Registry reverse drift')
    if fingerprint(reverse,REGISTRY_COLLECTIONS)!=read(BATCH/'cvp-plan-readback-receipt.json')['post_registry_fingerprint_sha256']:
        raise ValueError('parent original complete Registry boundary drift')
    freeze=read(GROUP/'production/inputs/freeze.json')
    if reviewed_package(ROOT,packet,freeze,cached_only=True)!=read(ROOT/'effective-package.json'): raise ValueError('fresh actual review/package mismatch')
    for b in before['physical_sources']:
        if file_sha256(Path(b['path']))!=b['file_sha256']: raise ValueError('physical source drift')
    for path in immutable_review_paths(ROOT): bindings.append(binding(path))
    bindings += [binding(ROOT/n) for n in ('owner-authorization.json','packet.json','before.json','effective-package.json','dry-run-plan.json','backup.json','apply-intent.json','apply-receipt.json','verified-result.json')]+before['physical_sources']
    return {'schema_version':VERSION,'campaign_root':str(PARENT),'group_key':'p030:p030:whole','claim_ids':sorted(ids),
        'resolved_claim_id':CLAIM,'status':'complete','historical_scope_run_preserved':True,'viewpoint_ids':[target],
        'current_registry_sha256':fingerprint(live,before['collections']),'bindings':bindings,'new_model_calls':0,'registry_writes':0}


def verify_proof(campaign_root,path,claim_ids,store=None):
    if Path(campaign_root).resolve()!=PARENT or Path(path).resolve() not in {ROOT/'component-completion.json',COMPLETION_ROOT/'component-completion.json'}: raise ValueError('wrong bounded new-CVP completion path')
    proof=build_proof(store or PostgresKnowledgeStore())
    if sorted(claim_ids)!=proof['claim_ids'] or read(Path(path))!=proof: raise ValueError('new-CVP completion proof drift')
    return proof


def complete_saved_audit():
    """One independent zero-call/zero-write proof handoff, never rerun repair."""
    from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
    from backend.pipeline.viewpoint_cvp_recovery_executor import resume_after_repair
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
    from backend.pipeline.viewpoint_cvp_recovery_supervisor import process_table,remaining_producers,current_job_process_ids
    load_dotenv(PROJECT/'.env')
    with (BASE/'immediate-recovery-20261002-v1/recovery.lock').open('r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if COMPLETION_ROOT.exists() or (ROOT/'completion-proof-repair-start.json').exists(): raise ValueError('completion repair already reserved')
        if [p for p in remaining_producers(process_table(),BASE) if p not in current_job_process_ids()]: raise ValueError('active producer')
        if subprocess.check_output(['git','status','--porcelain'],cwd=PROJECT,text=True).strip(): raise ValueError('dirty worktree')
        if read(ROOT/'failure.json')!={'error':'new-CVP completion proof drift','applied':True,'group_complete':False,'no_retry':True}:
            raise ValueError('not exact immutable/derived proof-boundary failure')
        marker={'fingerprint':'g052-derived-measurements-in-immutable-bindings-v1','authority':'standing proven mechanical proof repair',
            'old_failure_preserved':True,'output_root':str(COMPLETION_ROOT),'new_model_calls':0,'registry_writes':0,'budget_reset':False}
        write_immutable(ROOT/'completion-proof-repair-start.json',marker);write_immutable(COMPLETION_ROOT/'start.json',marker)
        try:
            runtime=canonical_runtime();os.environ['CODEX_EXECUTABLE']=runtime['identity']['cli']['path']
            write_immutable(COMPLETION_ROOT/'runtime-proof.json',runtime)
            store=PostgresKnowledgeStore();proof=build_proof(store);old=read(ROOT/'component-completion.json')
            if {k:v for k,v in old.items() if k!='bindings'}!={k:v for k,v in proof.items() if k!='bindings'}:
                raise ValueError('not exclusively derived binding drift')
            derived={str(ROOT/'review'/n) for n in ('batch-run.json','measurements.json','current-state.json')}
            if [b for b in old['bindings'] if b['path'] not in derived]!=proof['bindings']:
                raise ValueError('immutable content/source/model/applied proof changed')
            write_immutable(COMPLETION_ROOT/'component-completion.json',proof)
            audit=ROOT/'independent-audit/audit.json';resolution=ROOT/'final-resolution.json'
            bindings=verify_saved_full_audit(audit)
            write_immutable(COMPLETION_ROOT/'saved-full-audit-proof.json',{'bindings':bindings,'audit_replayed':False,'new_model_calls':0})
            result=resume_after_repair(PARENT,COMPLETION_ROOT/'continuation',BASE/'immediate-recovery-20261002-v1',Path(os.environ['CODEX_EXECUTABLE']),
                saved_audit_path=audit,owner_resolution_path=resolution,component_resolution_path=COMPLETION_ROOT/'component-completion.json',
                additional_proof_bindings=[binding(COMPLETION_ROOT/'saved-full-audit-proof.json'),*bindings])
            write_immutable(COMPLETION_ROOT/'result.json',result)
        except Exception as exc:
            write_immutable(COMPLETION_ROOT/'failure.json',{'error':str(exc),'new_model_calls':0,'registry_writes':0,'no_retry':True});raise


def run(*,recover_storage_shape=False):
    from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
    from backend.pipeline.viewpoint_cvp_recovery_supervisor import process_table,remaining_producers,current_job_process_ids
    from backend.pipeline.viewpoint_cvp_recovery_executor import run_logged,load_campaign,resume_after_repair,PYTHON
    from backend.pipeline.viewpoint_content_blocked_fallback import apply as fallback
    from backend.pipeline.viewpoint_audit_source_adjudication import disputes,run as adjudicate
    from backend.pipeline.viewpoint_audit_owner_resolution import create_gpt_resolution as final_resolution
    from backend.pipeline import viewpoint_backup_archive as archive
    load_dotenv(PROJECT/'.env')
    with (BASE/'immediate-recovery-20261002-v1/recovery.lock').open('r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if ROOT.exists(): raise ValueError('reserved owner repair root; no retry')
        if not recover_storage_shape: raise ValueError('v2 only consumes exact zero-write storage-shape failure; explicit mechanical recovery flag required')
        if read(FAILED_ROOT/'failure.json')!={'error':'not exact whole Claim equivalent member','applied':False,'group_complete':False,'no_retry':True} or (FAILED_ROOT/'apply-intent.json').exists() or (FAILED_ROOT/'apply-receipt.json').exists():
            raise ValueError('not exact preserved zero-write mechanical failure')
        if (FAILED_ROOT/'storage-shape-recovery-start.json').exists(): raise ValueError('same fingerprint repair already reserved')
        if PROJECT.name!='wkp-357-worktree-per-session' or subprocess.check_output(['git','status','--porcelain'],cwd=PROJECT,text=True).strip(): raise ValueError('wrong/dirty worktree')
        if [p for p in remaining_producers(process_table(),BASE) if p not in current_job_process_ids()]: raise ValueError('active production/maintenance')
        write_immutable(ROOT/'owner-authorization.json',{'quote':AUTH,'claim_id':CLAIM,'claim_sha256':PIN,'new_cvps':1,'new_links':1,'other_mutations':0,'future_permission':False,'old_budgets_preserved':True})
        try:
            runtime=canonical_runtime();os.environ['CODEX_EXECUTABLE']=runtime['identity']['cli']['path'];write_immutable(ROOT/'runtime-proof.json',runtime)
            volume=archive.verify_volume();migration=read(BASE/'registry-backup-ssd-migration-20261005-v1/result.json')
            if migration['count']!=47 or len(migration['receipts'])!=47: raise ValueError('archive denominator drift')
            for row in migration['receipts']:
                path=Path(row['original_path'])
                if archive.logical_binding_path(path)!=path or path.stat().st_size!=row['size'] or archive.digest(path)!=row['file_sha256'] or archive.catalog(path)!=row['restore_catalog_sha256']:
                    raise ValueError('archive SHA/size/catalog drift')
            write_immutable(ROOT/'archive-proof.json',{'volume':volume,'count':47,'migration':binding(BASE/'registry-backup-ssd-migration-20261005-v1/result.json'),'all_original_paths_verified':True})
            store=PostgresKnowledgeStore()
            with exclusive_cvp_run_lock({'global_lock_path':str(BASE/'registry-cvp.lock')}):
                parent, ids, bindings=verify_parent(store);states=snapshot(store);cols=sorted({c for c,_ in states})
                if fingerprint(states,REGISTRY_COLLECTIONS)!=read(BATCH/'cvp-plan-readback-receipt.json')['post_registry_fingerprint_sha256']:
                    raise ValueError('current Registry differs from original verified g052 postimage')
                if any(c=='viewpoint_claim_links' and p.get('claim_id')==CLAIM and p.get('effective_state')=='active' for (c,_),p in states.items()):
                    raise ValueError('authorized missing Claim already has active coverage')
                packet={'batch_id':'CVB-G052-OWNER-NEW-CVP-20261007-V1','scope_label':'Owner exact g052 unresolved Claim completion',
                    'claims':[c for c in parent['claims'] if c['claim_id']==CLAIM],
                    'registry_context':registry_context([v for (c,_),v in states.items() if c=='canonical_viewpoints'],[v for (c,_),v in states.items() if c=='viewpoint_revisions']),
                    'pending_candidates':[]}
                sources=[binding(Path(e['source_context']['source_path'])) for e in packet['claims'][0]['evidence']]
                write_immutable(ROOT/'before.json',{'collections':cols,'pre_fingerprint_sha256':fingerprint(states,cols),'physical_sources':sources,'parent_bindings':bindings})
                write_immutable(ROOT/'packet.json',packet)
                if packet!=read(FAILED_ROOT/'packet.json') or read(ROOT/'before.json')!=read(FAILED_ROOT/'before.json'):
                    raise ValueError('original reviewed input or full Registry/source boundary changed')
                cached=reviewed_package(FAILED_ROOT,packet,read(GROUP/'production/inputs/freeze.json'),cached_only=True)
                old_plan=store.plan_package(cached,source_kind=VERSION)
                if store.get_change_set_status(old_plan.fingerprint_sha256) is not None: raise ValueError('prior proposed ChangeSet already exists')
                oldbindings=[binding(p) for p in sorted((FAILED_ROOT/'review').glob('*.json'))]
                marker={'fingerprint':'g052-standard-compiler-whole-component-storage-shape-v1','authority':'standing proven mechanical implementation repair',
                    'old_root':str(FAILED_ROOT),'new_root':str(ROOT),'semantic_packet_sha256':sha256_json(packet),
                    'old_refusals_budgets_preserved':True,'new_model_calls':0,'prior_registry_writes':0,'bindings':oldbindings}
                write_immutable(FAILED_ROOT/'storage-shape-recovery-start.json',marker)
                for p in sorted((FAILED_ROOT/'review').glob('*.json')): write_immutable(ROOT/'review'/p.name,read(p))
                write_immutable(ROOT/'decision-time.json',read(FAILED_ROOT/'decision-time.json'))
                write_immutable(ROOT/'mechanical-cache-handoff.json',marker)
                package=reviewed_package(ROOT,packet,read(GROUP/'production/inputs/freeze.json'),cached_only=True)
                if package!=cached: raise ValueError('cached exact approved package drift')
                if fingerprint(snapshot(store),cols)!=fingerprint(states,cols): raise ValueError('Registry drift during review')
                plan=store.plan_package(package,source_kind=VERSION);doc=json.loads(json.dumps(plan.as_dict()));validate_plan(doc)
                write_immutable(ROOT/'effective-package.json',package);write_immutable(ROOT/'dry-run-plan.json',doc)
                label='before-g052-new-cvp-'+sha256_json({'root':str(ROOT)})[:16]
                out=subprocess.run(['scripts/backup-registry.sh',label],cwd=PROJECT,check=True,capture_output=True,text=True)
                match=re.search(r'backup-registry: wrote (.+\.dump) \(',out.stdout)
                if not match: raise ValueError('fresh backup not reported')
                path=Path(match.group(1));listing=subprocess.check_output(['pg_restore','--list',str(path)])
                if not listing.strip(): raise ValueError('empty restore list')
                backup=binding(path)|{'pg_restore_list_sha256':__import__('hashlib').sha256(listing).hexdigest(),'pg_restore_list_passed':True}
                write_immutable(ROOT/'backup.json',backup)
                if reviewed_package(ROOT,packet,read(GROUP/'production/inputs/freeze.json'),cached_only=True)!=package: raise ValueError('zero-call preapply package drift')
                for b in sources:
                    if file_sha256(Path(b['path']))!=b['file_sha256']: raise ValueError('preapply source drift')
                write_immutable(ROOT/'apply-intent.json',{'plan_sha256':sha256_json(doc),'pre_fingerprint_sha256':fingerprint(states,cols),'backup':backup})
                receipt=store.apply_plan(plan,metadata={'owner_missing_cvp_completion':str(ROOT)},expected_registry_fingerprint_sha256=fingerprint(states,cols),registry_collections=cols)
                write_immutable(ROOT/'apply-receipt.json',receipt)
                expected=deepcopy(states)
                for op in plan.operations: expected[op.collection,op.object_id]=stored_operation_payload(op)
                if snapshot(store)!=expected: raise ValueError('full Registry exact readback mismatch')
                for b in sources:
                    if file_sha256(Path(b['path']))!=b['file_sha256']: raise ValueError('postapply source drift')
                write_immutable(ROOT/'verified-result.json',{'status':'applied_readback_verified','change_set_id':plan.change_set_id,'operations':5,'post_fingerprint_sha256':fingerprint(expected,cols),'claim_evidence_source_existing_cvp_writes':0,'group_complete':False})
            proof=build_proof(store);write_immutable(ROOT/'component-completion.json',proof)
            cfg,schedule,manifest,groups=load_campaign(PARENT)
            original=read(BATCH/'cvp-change-plan.json')
            targets=sorted({o['payload']['viewpoint_id'] for o in original['operations'] if o['collection']=='viewpoint_claim_links'}|set(proof['viewpoint_ids']))
            write_derived(ROOT/'progress.json',{'status':'full_group_independent_audit','claim_count':14,'viewpoint_ids':targets,'master_data_mutations':5,'group_complete':False})
            auditroot=ROOT/'independent-audit'
            run_logged([PYTHON,'scripts/audit-library.py','--scope','all','--layers','1,2,3,4','--claim-ids',','.join(ids),'--viewpoint-ids',','.join(targets),'--workers','2','--model','gemini-3.8-flash','--out',auditroot,'--no-status-file','--preserve-full-context'],ROOT,'independent-audit')
            audit=fallback(auditroot/'audit.json',ROOT/'content-blocked-fallback');resolution=None
            if disputes(read(audit)):
                report=adjudicate(audit,ROOT/'audit-source-adjudication',store,final_gpt=True)
                if any(a['verdict']!='audit_objection_rejected' for a in report['actions']): raise ValueError('new semantic objection upheld/unresolved; no retry')
                resolution=ROOT/'final-resolution.json'
                final_resolution(audit,ROOT/'audit-source-adjudication',resolution,store)
            result=resume_after_repair(PARENT,ROOT/'continuation',BASE/'immediate-recovery-20261002-v1',Path(os.environ['CODEX_EXECUTABLE']),
                saved_audit_path=audit,owner_resolution_path=resolution,component_resolution_path=ROOT/'component-completion.json')
            write_immutable(ROOT/'result.json',result);write_derived(ROOT/'progress.json',result)
        except Exception as exc:
            write_immutable(ROOT/'failure.json',{'error':str(exc),'applied':(ROOT/'apply-receipt.json').exists(),'group_complete':False,'no_retry':True})
            raise


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--recover-storage-shape',action='store_true');parser.add_argument('--complete-saved-audit',action='store_true')
    args=parser.parse_args()
    if args.complete_saved_audit and args.recover_storage_shape: parser.error('select one exact execution entry')
    if args.complete_saved_audit: complete_saved_audit()
    else: run(recover_storage_shape=args.recover_storage_shape)
