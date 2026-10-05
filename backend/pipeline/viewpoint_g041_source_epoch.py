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
