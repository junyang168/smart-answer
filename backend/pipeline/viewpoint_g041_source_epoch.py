"""Exact corrected-source epoch consumer; never a generic lineage waiver.

Seven Claims have complete fresh source reviews. The other six paragraphs are
unchanged; their Claims still require their own historical qualification.
Accepted predecessor pins are restored only in a read-only membership copy.
"""
from copy import deepcopy
from pathlib import Path

from backend.pipeline import viewpoint_g041_qualified_transcript_repair as repair
from backend.api.canonical_repository.postgres_store import _normalize_records, merge_over_existing

PACKAGE = repair.APPLICATION_ROOT/'effective-package.json'


def bound_package(binding):
    if binding != {'path':str(PACKAGE),'file_sha256':repair.old.file_sha256(PACKAGE)}:
        raise ValueError('corrected source epoch exact package physical drift')
    package = repair.old.read_artifact(PACKAGE)
    repair.validate_package(package)
    return package


def binding():
    return {'path':str(PACKAGE),'file_sha256':repair.old.file_sha256(PACKAGE)}


def postimages(package):
    """Use the store's omission-preserves-existing contract, not model defaults."""
    normalized,stated=_normalize_records(package)
    before,_=repair.saved_before()
    return {c:{o:merge_over_existing(p,before.get((c,o)),stated[c,o]) for o,p in objects.items()}
        for c,objects in normalized.items()}


def validate_postimage(package, records):
    """records use the same typed state shape as preparation's Registry read."""
    normalized = postimages(package)
    for collection, objects in normalized.items():
        for oid, expected in objects.items():
            live = records.get((collection,oid))
            if (not live or live.get('retired') or live['payload'] != expected
                    or live['revision'] != expected['revision']
                    or live['content_sha256'] != repair.old.semantic_record_sha(expected)):
                raise ValueError(f'{collection}/{oid}: reviewed source epoch postimage drift')
    return normalized


def membership_snapshot(manifest, records, package, completed_ids, selected_ids):
    repair.validate_package(package)
    normalized = validate_postimage(package,records)
    before,_ = repair.saved_before()
    checked = deepcopy(records)
    accepted = {c['claim_id']:c for c in manifest['claim_packets']}
    used=[]
    for collection, objects in normalized.items():
        for oid in objects:
            if collection not in {'claims','evidence_steps','source_fragments','source_documents'}:
                continue
            if collection=='claims' and oid in accepted:
                if oid not in completed_ids or oid in selected_ids:
                    raise ValueError('changed Claim source epoch must be completed, never newly selected')
                used.append(oid)
            old = before[collection,oid]
            checked[collection,oid] = {'payload':old,'revision':old['revision'],
                'content_sha256':repair.old.semantic_record_sha(old),'retired':False}
    # Every newly selected same-source Claim outside the reviewed seven retains
    # its frozen Claim wording/anchors; normal independent lineage still runs.
    for cid in selected_ids:
        if cid in accepted and accepted[cid]['source_id']==repair.old.SOURCE:
            live=records.get(('claims',cid)); old=before.get(('claims',cid))
            if not live or live['payload']!=old or old.get('review_status')!='ai_consensus_reviewed':
                raise ValueError('pending source epoch Claim drift or human hold')
    return checked,used


def verified_applied(binding,store):
    package=bound_package(binding)
    before,proof=repair.saved_before()
    normalized=postimages(package)
    plan=repair.old.read_artifact(repair.APPLICATION_ROOT/'dry-run-plan.json')
    receipt=repair.old.read_artifact(repair.APPLICATION_ROOT/'apply-receipt.json')
    verified=repair.old.read_artifact(repair.APPLICATION_ROOT/'verified-result.json')
    if (verified.get('status')!='applied_readback_verified' or verified.get('master_data_mutations')!=612
            or verified.get('change_set_id')!=plan['change_set_id']
            or receipt.get('change_set_id')!=plan['change_set_id']):
        raise ValueError('source epoch lacks real single application/readback')
    live=repair.old.snapshot(store)
    states={(c,o):{'payload':p,'revision':p['revision'],
        'content_sha256':repair.old.semantic_record_sha(p),'retired':False} for (c,o),p in live.items()}
    validate_postimage(package,states)
    with store.connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        row=conn.execute('SELECT source_kind,source_sha256,status FROM wang_knowledge.change_sets WHERE change_set_id=%s',
            (plan['change_set_id'],)).fetchone()
        if not row or row[0]!=repair.VERSION or row[1]!=plan['source_sha256'] or row[2]!='applied':
            raise ValueError('source epoch applied KCS identity/status drift')
    return package, before, live


def reviewed_claim_ids(package):
    return {c['claim_id'] for c in package['source_repair_application']['review_packet']['changed_paragraph_claims']}


def selection_row(cid, package):
    if cid not in reviewed_claim_ids(package):
        raise ValueError('source epoch cannot approve an unreviewed Claim')
    return {'claim_id':cid,'review_binding':{'path':str(repair.ROOT/'review-report.json'),
        'file_sha256':repair.old.file_sha256(repair.ROOT/'review-report.json')},
        'review_input_binding':{'path':str(repair.ROOT/'packet.json'),
        'file_sha256':repair.old.file_sha256(repair.ROOT/'packet.json')},'source_repair_binding':binding()}


def bridge(*,claim_id,current_claim,current_evidence,current_fragments,source_repair_binding,store=None):
    store=store or repair.old.PostgresKnowledgeStore()
    package,before,live=verified_applied(source_repair_binding,store)
    if claim_id not in reviewed_claim_ids(package):
        raise ValueError('source epoch only seven fully fresh reviewed Claims')
    normalized=postimages(package)
    expected=normalized.get('claims',{}).get(claim_id,before['claims',claim_id])
    if (current_claim!=expected or current_claim!=live.get(('claims',claim_id))
            or current_claim.get('review_status')!='ai_consensus_reviewed'
            or before['claims',claim_id].get('review_status')!='ai_consensus_reviewed'):
        raise ValueError('fresh reviewed source Claim identity/version or human hold drift')
    if set(current_evidence)!=set(current_claim['evidence_step_ids']):
        raise ValueError('source epoch exact Claim evidence population drift')
    expected_fids=set()
    from backend.api.canonical_repository.knowledge_models import EvidenceStepRecord,evidence_fragment_ids
    for eid,payload in current_evidence.items():
        expected=normalized.get('evidence_steps',{}).get(eid,before['evidence_steps',eid])
        if payload!=expected or live.get(('evidence_steps',eid))!=expected:
            raise ValueError('source epoch Claim evidence postimage drift')
        expected_fids.update(evidence_fragment_ids(EvidenceStepRecord.model_validate(payload)))
    if set(current_fragments)!=expected_fids:
        raise ValueError('source epoch exact Claim fragment population drift')
    for fid,payload in current_fragments.items():
        if payload!=normalized['source_fragments'].get(fid) or payload['source_id']!=repair.old.SOURCE:
            raise ValueError('source epoch Claim fragment or source locality drift')
    row=selection_row(claim_id,package)
    body={'schema_version':'wang_historical_claim_review_bridge_v1','status':'verified',
        'claim_id':claim_id,'pinned_claim_revision':current_claim['revision'],
        'claim_revision_sha256':repair.old.semantic_record_sha(current_claim),
        'review_binding':row['review_binding'],'review_input_binding':row['review_input_binding'],
        'source_repair_binding':source_repair_binding,'review_model_id':'gpt-6.1-sol+claude-opus-5-5',
        'review_provider':'fresh_dual_subscription_owner_source_repair',
        'old_review_not_relabelled':True,'master_data_mutations':0,'model_calls_executed':0,
        'approval_status':'not_human_approved'}
    return {**body,'artifact_sha256':repair.old.sha256_json(body)}


def completion_from_saved_audit(sequence,audit_path,output,resolution_path=None):
    """New-source completion beside, never over, the old group proof."""
    from backend.pipeline.viewpoint_cvp_recovery_executor import load_campaign,binding as file_binding
    from backend.pipeline.viewpoint_cvp_campaign_runner import validate_audit
    from backend.pipeline.viewpoint_scoped_audit_disposition import scope_audit_disposition
    from backend.api.canonical_repository.viewpoint_production_safety import accepted_grouping_audit_scope
    if sequence not in {40,41} or output.parent != repair.APPLICATION_ROOT/'continuation':
        raise ValueError('source epoch only original affected g040/g041 completions')
    verified_applied(binding(),repair.old.PostgresKnowledgeStore())
    cfg,schedule,manifest,groups=load_campaign(repair.CAMPAIGN)
    group=repair.CAMPAIGN/f'g{sequence:03d}'
    key=next(r['group_key'] for r in schedule['schedule'] if r['sequence']==sequence)
    scope=group/'production/run/scope-run.json'
    plans=list((group/'production/run/batches').glob('*/cvp-change-plan.json'))
    if len(plans)!=1 or repair.old.read_artifact(scope)['disposition_status']!='complete':
        raise ValueError('source epoch cannot promote incomplete old applied group')
    readback=plans[0].parent/'cvp-readback-receipt.json'
    if repair.old.read_artifact(readback).get('readback_status')!='verified':
        raise ValueError('source epoch original CVP readback is missing')
    targets=sorted({o['payload']['viewpoint_id'] for o in repair.old.read_artifact(plans[0])['operations']
        if o['collection']=='viewpoint_claim_links'})
    audit=repair.old.read_artifact(audit_path)
    audit_base=repair.APPLICATION_ROOT/f'continuation/g{sequence:03d}-audit'
    allowed={audit_base/'independent-reaudit/audit.json',audit_base/'content-blocked-fallback/assessment.json'}
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit, KEY
    # Supplementary reports retain the bound original provider audit. Validate
    # every current complete model input rather than trusting an old verdict.
    if Path(audit_path) not in allowed:
        raise ValueError('source epoch audit outside fresh exact affected-group root')
    audit_bindings=verify_saved_full_audit(audit_base/'independent-reaudit/audit.json')
    validate_audit(audit,groups[key]['claim_ids'],targets,owner_resolution_path=resolution_path)
    disposition=scope_audit_disposition(audit={'meta':audit['meta'],
        'layers':{k:audit['layers'][k] for k in ('1','2')}},
        manifest=accepted_grouping_audit_scope(manifest,'group:'+key),
        audit_file_sha256=repair.old.file_sha256(audit_path),authorized_by=cfg['authorized_by'])
    if disposition['status']!='passed':
        raise ValueError('source epoch full-group mechanical disposition failed')
    proof={'group_key':key,'viewpoint_ids':targets,'audit_path':str(audit_path),
        'bindings':[file_binding(p) for p in [audit_path,plans[0],scope,readback,PACKAGE,
            repair.APPLICATION_ROOT/'apply-receipt.json',repair.APPLICATION_ROOT/'verified-result.json',
            repair.APPLICATION_ROOT/'backup.json',repair.APPLICATION_ROOT/'pg-restore-proof.json',
            repair.ROOT/'packet.json',repair.ROOT/'raw-gpt.json',repair.ROOT/'raw-opus.json',
            repair.ROOT/'review-report.json']],
        'source_epoch_recompletion':True,'source_repair_binding':binding(),
        'argument_routes_completed':False}
    proof['bindings']+=audit_bindings
    from backend.pipeline.viewpoint_g035_temporary_audit import validate as validate_exception
    exception_bindings=validate_exception(audit)
    if exception_bindings:
        from backend.pipeline.viewpoint_content_blocked_fallback import KEY
        proof.update(revisit_required=True,independent_audit_complete=False,
            approval_basis='owner_conditional_content_blocked_subscription_assessment',
            temporary_audit_exception_path=audit['meta'][KEY]['path'])
        proof['bindings']+=exception_bindings
    if resolution_path is not None:
        proof['owner_resolution_path']=str(resolution_path)
        proof['bindings']+=[file_binding(resolution_path),*repair.old.read_artifact(resolution_path)['bindings']]
    if sequence==40:
        proof['bindings'].append(file_binding(group/'completion.json'))
        proof['replaces_original_completion_binding']=file_binding(group/'completion.json')
    repair.old.write_immutable(output/'audit-disposition.json',disposition)
    repair.old.write_immutable(output/'completion.json',proof)
    return proof


def recompletion_seed(path,seeds):
    """Exactly g040, exact old seed, exact applied source epoch and new audit."""
    expected=repair.APPLICATION_ROOT/'continuation/g040-verified/completion.json'
    if Path(path)!=expected:
        raise ValueError('source epoch completion replacement outside exact independent root')
    proof=repair.old.read_artifact(expected)
    cfg,schedule,manifest,groups=__import__('backend.pipeline.viewpoint_cvp_recovery_executor',fromlist=['load_campaign']).load_campaign(repair.CAMPAIGN)
    key=next(r['group_key'] for r in schedule['schedule'] if r['sequence']==40)
    matches=[s for s in seeds if s['group_key']==key]
    old=repair.old.read_artifact(repair.CAMPAIGN/'g040/completion.json')
    if len(matches)!=1 or matches[0]!=old or proof.get('source_repair_binding')!=binding() or proof.get('source_epoch_recompletion') is not True:
        raise ValueError('source epoch must replace exactly the original g040 seed')
    rebuilt=completion_from_saved_audit(40,Path(proof['audit_path']),expected.parent,
        Path(proof['owner_resolution_path']) if proof.get('owner_resolution_path') else None)
    if proof!=rebuilt:
        raise ValueError('source epoch new completion reconstruction drift')
    return [proof if s['group_key']==key else s for s in seeds]
