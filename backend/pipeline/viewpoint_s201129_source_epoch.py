"""Consume only the single verified S201129 repair, not arbitrary source drift."""
from copy import deepcopy
from pathlib import Path

from backend.pipeline import viewpoint_s201129_source_repair as repair
from backend.api.canonical_repository.postgres_store import _normalize_records, merge_over_existing
from backend.api.canonical_repository.knowledge_models import EvidenceStepRecord, evidence_fragment_ids
from backend.pipeline.viewpoint_validation_context import read_current_objects, current_check


def binding():
    return repair.binding(repair.PACKAGE)


def bound_package(bound):
    if bound != binding():
        raise ValueError('S201129 exact applied package physical drift')
    # Legacy format/path identity only. The completed application's generic
    # SQL/package/readback guard replaces re-entering this source's old writer.
    # New writes still call repair.validate_package in the store contract.
    from backend.pipeline.viewpoint_applied_source_epochs import consume_applied_source_package
    return consume_applied_source_package(bound,repair.PostgresKnowledgeStore())


def scoped_predecessors(package):
    """Original operation versions plus unchanged original review context only."""
    from backend.pipeline.viewpoint_applied_source_epochs import applied_source_preimages
    actual, before = applied_source_preimages(binding(), repair.PostgresKnowledgeStore())
    if package != actual:
        raise ValueError('completed source postimage package drift')
    normalized, _ = _normalize_records(package)
    # Thin legacy review-format adapter only: the original small review packet
    # contains these unchanged evidence rows. Never infer them from a newer
    # current row or reopen the whole archived Registry on a missing context.
    _, _, logical, _ = repair.reviewed_inputs()
    unchanged = {('evidence_steps', row['evidence_step_id']):row
                 for row in logical['before_evidence']}
    if len(unchanged) != len(logical['before_evidence']):
        raise ValueError('duplicate original unchanged review context')
    for collection, rows in normalized.items():
        for oid in rows:
            key = collection, oid
            if key not in before:
                if key not in unchanged:
                    raise ValueError('completed source lacks original unchanged context: '+oid)
                before[key] = unchanged[key]
    return before


def postimages(package):
    normalized, stated = _normalize_records(package)
    before = scoped_predecessors(package)
    plan = repair.read_artifact(repair.APPLICATION_ROOT/'dry-run-plan.json')
    return planned_postimages(normalized,stated,before,plan)


def planned_postimages(normalized, stated, before, plan):
    """An unchanged row keeps its stored revision; a draft is not an apply plan."""
    operations = {(o['collection'],o['object_id']):o for o in plan['operations']}
    if (len(operations) != len(plan['operations'])
            or set(operations) - {(c,o) for c,rows in normalized.items() for o in rows}):
        raise ValueError('source plan has duplicate or unreviewed objects')
    result = {}
    for collection, rows in normalized.items():
        result[collection] = {}
        for oid, payload in rows.items():
            prior = before.get((collection,oid))
            proposed = merge_over_existing(payload,prior,stated[collection,oid])
            operation = operations.get((collection,oid))
            if operation is None:
                if (prior is None or {k:v for k,v in proposed.items() if k != 'revision'}
                        != {k:v for k,v in prior.items() if k != 'revision'}):
                    raise ValueError('source plan omitted an actual changed record: '+oid)
                expected = deepcopy(prior)
            else:
                expected = {**operation['payload'],'revision':operation['after_revision']}
                if ({k:v for k,v in expected.items() if k != 'revision'}
                        != {k:v for k,v in proposed.items() if k != 'revision'}):
                    raise ValueError('source plan changed reviewed content: '+oid)
            result[collection][oid] = expected
    return result


def validate_postimage(package, records):
    from backend.pipeline.viewpoint_s201129_link_support_apply import restore_parent_records
    records = restore_parent_records(records)
    normalized = postimages(package)
    for collection, rows in normalized.items():
        for oid, expected in rows.items():
            current = records.get((collection,oid))
            if (not current or current.get('retired') or current['payload'] != expected
                    or current['revision'] != expected['revision']
                    or current['content_sha256'] != repair.semantic_record_sha(expected)):
                raise ValueError('S201129 exact reviewed postimage drift: '+oid)
    return normalized


def verify_completed_application_bindings(rows,bound):
    """Reuse the same authenticated predecessor at the membership readback.

    bound_package has already checked the actual entire applied SQL package.
    No declared projection means the original physical checks, not a waiver.
    """
    from backend.pipeline.viewpoint_completed_predecessors import has_predecessor_input,verify_archived_refusal
    compact=has_predecessor_input(bound)
    for row in rows:
        if compact:
            verify_archived_refusal(row,bound)
        elif repair.binding(Path(row['path'])) != row:
            raise ValueError('S201129 applied proof physical drift')


@current_check
def verified_applied(bound, store):
    package = bound_package(bound)
    before = scoped_predecessors(package)
    scope, _, _, _ = repair.reviewed_inputs()
    plan = repair.read_artifact(repair.APPLICATION_ROOT/'dry-run-plan.json')
    receipt = repair.read_artifact(repair.APPLICATION_ROOT/'apply-receipt.json')
    verified = repair.read_artifact(repair.APPLICATION_ROOT/'verified-result.json')
    if (verified['status'] != 'applied_readback_verified'
            or receipt['change_set_id'] != plan['change_set_id']
            or verified['change_set_id'] != plan['change_set_id']
            or verified['master_data_mutations'] != len(plan['operations'])
            or verified['pre_fingerprint_sha256'] != scope['registry_fingerprint_sha256']):
        raise ValueError('S201129 lacks actual single application and readback')
    verify_completed_application_bindings(verified['bindings'],bound)
    expected = postimages(package)
    required = {(c,o) for c,rows in expected.items() for o in rows}
    from backend.pipeline.viewpoint_s201129_link_support_apply import applied_child_object_ids
    child_ids = applied_child_object_ids()
    # Plan carries exact collection identities; do not rely on ID prefixes.
    if child_ids:
        from backend.pipeline.viewpoint_s201129_link_support_apply import ROOT as child_root
        child_plan = repair.read_artifact(child_root/'dry-run-plan.json')
        required.update((o['collection'],o['object_id']) for o in child_plan['operations'])
    records = read_current_objects(store, required)
    live = {key: row['payload'] for key,row in records.items() if not row['retired']}
    validate_postimage(package,records)
    with store.connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        row = conn.execute('SELECT source_kind,source_sha256,status FROM wang_knowledge.change_sets WHERE change_set_id=%s',
                           (plan['change_set_id'],)).fetchone()
        if not row or tuple(row) != (repair.VERSION,plan['source_sha256'],'applied'):
            raise ValueError('S201129 applied KCS identity/status drift')
    return package, before, live


@current_check
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
    if package != bound_package(binding()):
        raise ValueError('S201129 membership applied package drift')
    normalized = validate_postimage(package,records)
    before = scoped_predecessors(package)
    scope, _, _, _ = repair.reviewed_inputs()
    if manifest != repair.read_artifact(Path(scope['manifest_path'])):
        raise ValueError('S201129 accepted membership manifest drift')
    checked = dict(records)  # replace projected rows, do not clone untouched rows
    accepted = {r['claim_id'] for r in manifest['claim_packets']}
    accepted_rows = {r['claim_id']:r for r in manifest['claim_packets']}
    if len(accepted_rows) != len(manifest['claim_packets']):
        raise ValueError('duplicate original accepted Claim membership')
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
    unreviewed = {cid for cid in selected_ids - reviewed_claim_ids(package)
                  if records.get(('claims',cid)) and any(o.get('transcript_id') == 'S 201129'
                     for o in records['claims',cid]['payload'].get('occurrences',[]))}
    from backend.pipeline.viewpoint_applied_source_epochs import read_pinned_versions
    pins = []
    for cid in sorted(unreviewed):
        if cid not in accepted_rows:
            raise ValueError('unreviewed same-source Claim lacks accepted membership pin')
        row = accepted_rows[cid]
        pins.append(('claims',cid,row['claim_revision'],row['claim_content_sha256']))
    originals = read_pinned_versions(pins,repair.PostgresKnowledgeStore())
    for cid in unreviewed:
        current = records.get(('claims',cid))
        old = originals['claims',cid]
        if current['payload'] != old:
            raise ValueError('unreviewed same-source Claim changed')
    return checked, used


def selection_row(cid, package):
    if cid not in reviewed_claim_ids(package):
        raise ValueError('S201129 review cannot approve another Claim')
    return {'claim_id':cid,'review_binding':repair.binding(repair.REVIEW_ROOT/'exact-review-report.json'),
            'review_input_binding':repair.binding(repair.REVIEW_ROOT/'exact-review-packet.json'),
            'source_repair_binding':binding()}


def grouping_current_claim_packets(manifest, bound, claim_ids):
    """Rebind content, never membership, to the exact applied source review."""
    package, before, live = verified_applied(bound, repair.PostgresKnowledgeStore())
    scope, _, _, _ = repair.reviewed_inputs()
    if manifest != repair.read_artifact(Path(scope['manifest_path'])):
        raise ValueError('S201129 grouping accepted manifest drift')
    from backend.api.canonical_repository.knowledge_models import ClaimRecord, SourceFragmentRecord
    from backend.api.canonical_repository.viewpoint_resolution import compile_review_claim
    originals = {r['claim_id']: r for r in manifest['claim_packets']}
    result = {}
    for cid in set(claim_ids) & reviewed_claim_ids(package):
        if cid not in originals:
            raise ValueError('source repair cannot add a grouped Claim')
        prior = originals[cid]
        old = before['claims', cid]
        if (prior['claim_revision'] != old['revision']
                or prior['claim_content_sha256'] != repair.semantic_record_sha(old)):
            raise ValueError('source repair grouping predecessor Claim pin drift')
        claim = ClaimRecord.model_validate(live['claims', cid])
        evidence = {eid: EvidenceStepRecord.model_validate(live['evidence_steps', eid])
                    for eid in claim.evidence_step_ids}
        fids = {fid for step in evidence.values() for fid in evidence_fragment_ids(step)}
        fragments = {fid: SourceFragmentRecord.model_validate(live['source_fragments', fid]) for fid in fids}
        source_shas = {f.source_id: live['source_documents', f.source_id]['source_file_sha256']
                       for f in fragments.values()}
        current, findings = compile_review_claim(claim=claim, evidence_index=evidence,
            fragment_index=fragments, citation_index={}, coverage_source_sha256=source_shas,
            attestation_sha=None)
        if current is None or findings or current.source_id != prior['source_id']:
            raise ValueError('source repair grouping source-local graph drift')
        steps = {}
        for row in current.evidence:
            step = steps.setdefault(row.evidence_step_id, {'evidence_step_id':row.evidence_step_id,
                'statement':row.evidence_statement, 'scripture_refs':row.scripture_refs, 'fragments':[]})
            step['fragments'].append({'fragment_id':row.source_fragment_id,
                                     'verbatim_excerpt':row.verbatim_excerpt})
        result[cid] = {**prior, 'statement':current.statement,
            'claim_revision':current.pinned_claim_revision,
            'claim_content_sha256':current.claim_revision_sha256,
            'scripture_refs':current.scripture_refs, 'evidence_steps':list(steps.values())}
    return result


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
