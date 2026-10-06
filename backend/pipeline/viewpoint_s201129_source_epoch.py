"""Consume only the single verified S201129 repair, not arbitrary source drift."""
from copy import deepcopy
from pathlib import Path

from backend.pipeline import viewpoint_s201129_source_repair as repair
from backend.api.canonical_repository.postgres_store import _normalize_records, merge_over_existing
from backend.api.canonical_repository.knowledge_models import EvidenceStepRecord, evidence_fragment_ids


def binding():
    return repair.binding(repair.PACKAGE)


def bound_package(bound):
    if bound != binding():
        raise ValueError('S201129 exact applied package physical drift')
    package = repair.read_artifact(repair.PACKAGE)
    repair.validate_package(package)
    return package


def postimages(package):
    normalized, stated = _normalize_records(package)
    before, _ = repair.saved_before()
    return {c:{o:merge_over_existing(p,before.get((c,o)),stated[c,o]) for o,p in rows.items()}
            for c,rows in normalized.items()}


def validate_postimage(package, records):
    normalized = postimages(package)
    for collection, rows in normalized.items():
        for oid, expected in rows.items():
            current = records.get((collection,oid))
            if (not current or current.get('retired') or current['payload'] != expected
                    or current['revision'] != expected['revision']
                    or current['content_sha256'] != repair.semantic_record_sha(expected)):
                raise ValueError('S201129 exact reviewed postimage drift: '+oid)
    return normalized


def verified_applied(bound, store):
    package = bound_package(bound)
    before, proof = repair.saved_before()
    plan = repair.read_artifact(repair.APPLICATION_ROOT/'dry-run-plan.json')
    receipt = repair.read_artifact(repair.APPLICATION_ROOT/'apply-receipt.json')
    verified = repair.read_artifact(repair.APPLICATION_ROOT/'verified-result.json')
    if (verified['status'] != 'applied_readback_verified'
            or receipt['change_set_id'] != plan['change_set_id']
            or verified['change_set_id'] != plan['change_set_id']
            or verified['master_data_mutations'] != len(plan['operations'])
            or verified['pre_fingerprint_sha256'] != proof['pre_fingerprint_sha256']):
        raise ValueError('S201129 lacks actual single application and readback')
    for row in verified['bindings']:
        if repair.binding(Path(row['path'])) != row:
            raise ValueError('S201129 applied proof physical drift')
    live = repair.snapshot(store)
    records = {(c,o):{'payload':p,'revision':p['revision'],
                       'content_sha256':repair.semantic_record_sha(p),'retired':False}
               for (c,o),p in live.items()}
    validate_postimage(package,records)
    with store.connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        row = conn.execute('SELECT source_kind,source_sha256,status FROM wang_knowledge.change_sets WHERE change_set_id=%s',
                           (plan['change_set_id'],)).fetchone()
        if not row or tuple(row) != (repair.VERSION,plan['source_sha256'],'applied'):
            raise ValueError('S201129 applied KCS identity/status drift')
    return package, before, live


def grouping_physical_transition(manifest, bound):
    package, before, live = verified_applied(bound,repair.PostgresKnowledgeStore())
    scope, _, _, _ = repair.reviewed_inputs()
    if manifest != repair.read_artifact(Path(scope['manifest_path'])):
        raise ValueError('S201129 cannot authorize another accepted manifest')
    rows = [r for r in manifest['sources'] if r['source_id'] == repair.SOURCE]
    if len(rows) != 1:
        raise ValueError('S201129 physical predecessor not unique')
    row = rows[0]
    old = before['source_documents',repair.SOURCE]
    current = live['source_documents',repair.SOURCE]
    expected_path = str(repair.BASE.parents[2]/'script_review/S 201129.json')
    if (row['path'] != expected_path or row['file_sha256'] != old['source_file_sha256']
            or row['frozen_file_sha256'] != old['source_file_sha256']
            or row['source_revision'] != old['revision']
            or row['source_content_sha256'] != repair.semantic_record_sha(old)
            or row['source_body_sha256'] != old['source_body_sha256']
            or current['source_file_sha256'] != scope['source_files']['review']
            or current['source_body_sha256'] != scope['review_body_sha256']):
        raise ValueError('S201129 exact predecessor/current physical pins drift')
    return {row['path']:(row['file_sha256'],scope['source_files']['review'])}


def reviewed_claim_ids(package):
    return {c['claim_id'] for c in package['claims']}


def membership_snapshot(manifest, records, package, completed_ids, selected_ids):
    """Restore historical membership pins only; selection uses fresh exact review.

    Unlike the g041 completed-only repair, the owner explicitly authorized these
    pending Claims too. Neither changing the 973 IDs nor borrowing old approval
    is permitted. Normal selection independently consumes bridge() below.
    """
    repair.validate_package(package)
    normalized = validate_postimage(package,records)
    before, _ = repair.saved_before()
    scope, _, _, _ = repair.reviewed_inputs()
    if manifest != repair.read_artifact(Path(scope['manifest_path'])):
        raise ValueError('S201129 accepted membership manifest drift')
    checked = deepcopy(records)
    accepted = {r['claim_id'] for r in manifest['claim_packets']}
    used = []
    for collection, rows in normalized.items():
        if collection not in {'claims','evidence_steps','source_fragments','source_documents'}:
            continue
        for oid in rows:
            old = before.get((collection,oid))
            if old is None:
                raise ValueError('S201129 cannot add accepted members or source evidence')
            checked[collection,oid] = {'payload':old,'revision':old['revision'],
                'content_sha256':repair.semantic_record_sha(old),'retired':False}
            if collection == 'claims' and oid in accepted:
                used.append(oid)
    for cid in selected_ids - reviewed_claim_ids(package):
        current = records.get(('claims',cid))
        old = before.get(('claims',cid))
        if current and any(o.get('transcript_id') == 'S 201129' for o in current['payload'].get('occurrences',[])):
            if current['payload'] != old:
                raise ValueError('unreviewed same-source Claim changed')
    return checked, used


def selection_row(cid, package):
    if cid not in reviewed_claim_ids(package):
        raise ValueError('S201129 review cannot approve another Claim')
    return {'claim_id':cid,'review_binding':repair.binding(repair.REVIEW_ROOT/'exact-review-report.json'),
            'review_input_binding':repair.binding(repair.REVIEW_ROOT/'exact-review-packet.json'),
            'source_repair_binding':binding()}


def bridge(*, claim_id, current_claim, current_evidence, current_fragments, source_repair_binding, store=None):
    package, _, live = verified_applied(source_repair_binding,store or repair.PostgresKnowledgeStore())
    normalized = postimages(package)
    if (claim_id not in reviewed_claim_ids(package) or current_claim != normalized['claims'][claim_id]
            or current_claim != live.get(('claims',claim_id))
            or current_claim.get('review_status') != 'independent_terminal_reviewed'
            or set(current_evidence) != set(current_claim['evidence_step_ids'])):
        raise ValueError('S201129 exact fresh Claim/evidence identity drift')
    fids = set()
    for eid, payload in current_evidence.items():
        if payload != normalized['evidence_steps'].get(eid) or payload != live.get(('evidence_steps',eid)):
            raise ValueError('S201129 fresh Claim evidence drift')
        fids.update(evidence_fragment_ids(EvidenceStepRecord.model_validate(payload)))
    if set(current_fragments) != fids:
        raise ValueError('S201129 exact fragment population drift')
    for fid, payload in current_fragments.items():
        if payload != normalized['source_fragments'].get(fid) or payload['source_id'] != repair.SOURCE:
            raise ValueError('S201129 own-source fragment drift')
    row = selection_row(claim_id,package)
    body = {'schema_version':'wang_historical_claim_review_bridge_v1','status':'verified',
        'claim_id':claim_id,'pinned_claim_revision':current_claim['revision'],
        'claim_revision_sha256':repair.semantic_record_sha(current_claim),
        'review_binding':row['review_binding'],'review_input_binding':row['review_input_binding'],
        'source_repair_binding':source_repair_binding,'review_model_id':'claude-opus-5-5',
        'review_provider':'owner_scoped_independent_terminal_subscription_review',
        'old_review_not_relabelled':True,'master_data_mutations':0,'model_calls_executed':0,
        'approval_status':'not_human_approved'}
    return {**body,'artifact_sha256':repair.sha256_json(body)}
