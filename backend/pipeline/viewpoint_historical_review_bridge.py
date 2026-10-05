"""Authenticate a passing historical Claim review across coordinate-only repair.

This is not a new review, a consensus seal, or Claim approval. It binds an
existing passing review to actual applied version history and preserves the
original review's model/provenance. A different generation with the same Claim
ID is never sufficient.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.api.canonical_repository.postgres_store import REVIEW_FIELDS, _normalize_records, record_content_sha
from backend.api.canonical_repository.knowledge_models import evidence_fragment_ids, EvidenceStepRecord
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked, validate_content_addressed
from backend.pipeline.corpus_ai_review_runner import _normalize_claim_layer


def _meaning(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in REVIEW_FIELDS | {"revision"}}


def _fragment_meaning(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = {field: payload.get(field) for field in (
        "source_id", "verbatim_excerpt", "media_time", "source_modality", "visual_block_sha256", "visual_facts",
    )}
    if result["source_modality"] in {None, "spoken"}:
        result["source_modality"] = "spoken"
        if not result["visual_facts"]:
            result["visual_facts"] = None
    return result


def _read_binding(binding: Mapping[str, str]) -> dict[str, Any]:
    path = Path(binding["path"])
    if not path.is_absolute() or hashlib.sha256(path.read_bytes()).hexdigest() != binding["file_sha256"]:
        raise CvpProductionBlocked(["historical review bridge physical SHA drift"])
    return json.loads(path.read_text(encoding="utf-8"))


def verify_coordinate_review_migration(claim_id, before, after, review_binding,
                                     review_input_binding, review, *, root=None):
    """Consume an exact applied #397 status-only plan, never a source-kind waiver.

    The surrounding bridge has independently validated the historical pass,
    coordinate chain, live evidence and physical source. Here the complete
    frozen plan fingerprint must equal the applied ChangeSet identity.
    """
    from backend.config.wang_platform_paths import wang_platform_paths
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStoreError
    from backend.pipeline.legacy_candidate_reconciliation import _decode_plan
    root = Path(root) if root is not None else wang_platform_paths().staging / 'candidate-reconciliation-397'
    if (_meaning(before['payload']) != _meaning(after['payload'])
            or before['payload'].get('review_status') != 'candidate'
            or after['payload'].get('review_status') != 'ai_consensus_reviewed'
            or after['revision'] != before['revision'] + 1):
        raise CvpProductionBlocked([f'{claim_id}: migration is not candidate-to-reviewed status only'])
    candidates = []
    for path in sorted(root.rglob('migration-plans.json')):
        document = json.loads(path.read_text(encoding='utf-8'))
        for raw in document.get('plans', []):
            if raw.get('change_set_id') == after['change_set_id']:
                candidates.append((path, document, raw))
    if len(candidates) != 1:
        raise CvpProductionBlocked([f'{claim_id}: applied migration proof missing or ambiguous'])
    path, document, raw = candidates[0]
    try:
        plan = _decode_plan(raw)  # validates all operations/events and KCS fingerprint
        preflight_path = path.parent / 'preflight.json'
        preflight = json.loads(preflight_path.read_text(encoding='utf-8'))
        freeze = sha256_json(preflight['rows'])
        if (plan.source_kind != after['source_kind']
                or plan.source_sha256 != after['source_sha256']
                or freeze != preflight.get('snapshot_sha256')
                or freeze != document.get('freeze_sha256')
                or freeze != after.get('metadata', {}).get('freeze_sha256')):
            raise ValueError('applied plan/frozen population binding differs')
        rows = [r for r in preflight['rows'] if r['claim_id'] == claim_id]
        ops = [o for o in plan.operations if o.collection == 'claims' and o.object_id == claim_id]
        events = [e for e in plan.review_events if e.collection == 'claims' and e.object_id == claim_id]
        if len(rows) != 1 or len(ops) != 1 or len(events) != 1:
            raise ValueError('migration selected population is not unique')
        row, op, event = rows[0], ops[0], events[0]
        artifact = event.artifact
        if (row.get('reason') != 'coordinate_only_historical_review_graph_verified'
                or row.get('historical_decision_reason') != 'pass_ready_for_legacy_migration'
                or row.get('review_decision') != 'pass'
                or row.get('adjudication_status') != 'not_required'
                or row.get('spot_check_selected') or row.get('human_review_reason')
                or row.get('target_review_status') != 'ai_consensus_reviewed'
                or row.get('revision') != before['revision']
                or row.get('content_sha256') != before['content_sha256']
                or row.get('review_sha256') != review_binding['file_sha256']
                or row.get('package_sha256') != review_input_binding['file_sha256']
                or row.get('review_fingerprint') != review['reviewer']['fingerprint_sha256']
                or artifact.get('review_artifact_sha256') != row['review_sha256']
                or artifact.get('package_sha256') != row['package_sha256']
                or artifact.get('freeze_sha256') != freeze
                or sha256_json(document['claim_related_graph_guards'][claim_id]) != row['graph_guard_sha256']
                or artifact.get('graph_guard_sha256') != row['graph_guard_sha256']):
            raise ValueError('migration does not bind the resolved independent pass/graph')
        for field in ('coordinate_chain_sha256', 'coordinate_proof_code_sha256',
                      'reviewed_claim_content_sha256', 'reviewed_claim_substantive_sha256',
                      'current_anchor_count', 'current_fragment_count'):
            if not row.get(field) or artifact.get(field) != row[field]:
                raise ValueError('migration coordinate proof differs')
        expected = dict(before['payload'])
        expected.update(review_status='ai_consensus_reviewed', reviewed_by=row['reviewer_id'],
                        reviewed_at=row['adjudicated_at'], review_note=event.reason,
                        revision=after['revision'])
        if (op.operation != 'update' or op.before_revision != before['revision']
                or op.after_revision != after['revision']
                or op.before_sha256 != before['content_sha256']
                or op.after_sha256 != after['content_sha256']
                or dict(op.payload, revision=op.after_revision) != after['payload']
                or expected != after['payload']
                or event.object_revision != after['revision']
                or event.decision != 'ai_consensus_reviewed'
                or event.reviewer_kind != 'ai' or event.reviewer_id != row['reviewer_id']
                or event.reviewer_id != review['reviewer']['review_model_id']):
            raise ValueError('applied status/reviewer/postimage differs')
    except (ValueError, KeyError, TypeError, PostgresKnowledgeStoreError) as exc:
        raise CvpProductionBlocked([f'{claim_id}: invalid applied coordinate review migration: {exc}']) from exc
    return {'change_set_id': after['change_set_id'], 'authority_kind': 'exact_applied_coordinate_review_migration',
            'plan_binding': {'path': str(path), 'file_sha256': hashlib.sha256(path.read_bytes()).hexdigest()},
            'preflight_binding': {'path': str(preflight_path), 'file_sha256': hashlib.sha256(preflight_path.read_bytes()).hexdigest()},
            'before_sha256': before['content_sha256'], 'after_sha256': after['content_sha256'],
            'human_holds_released': False}


def verify_legacy_adjudicated_graph(claim_id, final, current_claim, versions,
                                  current_evidence, current_fragments):
    """Exact replayed post-image, not a waiver of an unresolved old finding."""
    normalized, _ = _normalize_records(final)
    reviewed = normalized.get('claims', {}).get(claim_id)
    if (reviewed is None or _meaning(reviewed) != _meaning(current_claim)
            or current_claim.get('review_status') != 'ai_consensus_reviewed'):
        raise CvpProductionBlocked([f'{claim_id}: legacy adjudicated Claim drift'])
    chain = sorted(versions, key=lambda row: row['revision'])
    for row in chain:
        if (row.get('status') != 'applied' or record_content_sha(row['payload']) != row['content_sha256']
                or row['payload'].get('revision') != row['revision']
                or _meaning(row['payload']) != _meaning(reviewed)):
            raise CvpProductionBlocked([f'{claim_id}: legacy applied history drift'])
    if not chain or chain[-1]['payload'] != dict(current_claim):
        raise CvpProductionBlocked([f'{claim_id}: legacy history is not current'])
    if chain[0].get('source_sha256') != sha256_json(final):
        raise CvpProductionBlocked([f'{claim_id}: initial ChangeSet does not bind replayed final'])
    for row in chain[1:]:
        if row.get('source_kind') != 'legacy_adjudicated_auto_applied_review_reconciliation_v1':
            raise CvpProductionBlocked([f'{claim_id}: unexpected legacy post-review revision'])
    for eid in reviewed['evidence_step_ids']:
        old, live = normalized['evidence_steps'][eid], current_evidence.get(eid)
        if live is None or _meaning(old) != _meaning(live) or claim_id not in live['produced_claim_ids']:
            raise CvpProductionBlocked([f'{claim_id}: legacy adjudicated evidence drift'])
        for fid in evidence_fragment_ids(EvidenceStepRecord.model_validate(live)):
            if fid not in current_fragments or _fragment_meaning(normalized['source_fragments'][fid]) != _fragment_meaning(current_fragments[fid]):
                raise CvpProductionBlocked([f'{claim_id}: legacy adjudicated fragment drift'])
    return chain[-1]


def _legacy_adjudicated_bridge(*, claim_id, current_claim, versions, current_evidence,
                              current_fragments, review_binding, review_input_binding,
                              legacy_reviewed_candidate_binding):
    from backend.pipeline.legacy_candidate_reconciliation import inspect_legacy_bundle
    from backend.pipeline.legacy_auto_applied_review_reconciliation import verify_historical_replay
    final = _read_binding(legacy_reviewed_candidate_binding)
    path = Path(legacy_reviewed_candidate_binding['path'])
    bundle = inspect_legacy_bundle(path)
    replay = verify_historical_replay(path)
    if (bundle.get('reason') is not None or not replay
            or bundle.get('review_sha256') != review_binding['file_sha256']
            or bundle.get('package_sha256') != review_input_binding['file_sha256']
            or not bundle.get('adjudication_structurally_validated')):
        raise CvpProductionBlocked([f'{claim_id}: legacy adjudication is not source-bound replay authority'])
    row = bundle['review_rows'].get(claim_id, {})
    result = bundle['adjudication_results'].get(claim_id, {})
    if (row.get('decision') != 'changes_suggested' or row.get('spot_check_selected')
            or row.get('human_review_reason') or result.get('status') != 'auto_applied'
            or result.get('human_review_reason') or result.get('openai', {}).get('decision') != 'accept'):
        raise CvpProductionBlocked([f'{claim_id}: legacy finding lacks resolved independent adjudication'])
    removed = result['openai'].get('patch', {}).get('excluded_claim_relation_ids', [])
    if (not removed or set(removed) != set(final.get('consensus_application', {}).get('removed_claim_relation_ids', []))
            or any(p.get('claim_relation_id') in removed for p in final.get('claim_relations', []))):
        raise CvpProductionBlocked([f'{claim_id}: legacy excluded relation post-image mismatch'])
    # The replay proves the original deletion; also prove it has not silently
    # reappeared in the live graph. This is a read-only query, never a repair.
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    with PostgresKnowledgeStore().connect() as connection:
        connection.execute('SET TRANSACTION READ ONLY')
        if connection.execute("SELECT object_id FROM wang_knowledge.objects WHERE collection='claim_relations' AND object_id = ANY(%s) AND retired_at IS NULL", (removed,)).fetchall():
            raise CvpProductionBlocked([f'{claim_id}: excluded legacy relation remains active'])
    baseline = verify_legacy_adjudicated_graph(claim_id, final, current_claim, versions,
                                             current_evidence, current_fragments)
    body = {'schema_version':'wang_historical_claim_review_bridge_v1', 'status':'verified',
        'claim_id':claim_id, 'pinned_claim_revision':current_claim['revision'],
        'claim_revision_sha256':record_content_sha(current_claim),
        'review_binding':dict(review_binding), 'review_input_binding':dict(review_input_binding),
        'legacy_reviewed_candidate_binding':dict(legacy_reviewed_candidate_binding),
        'review_model_id':bundle['reviewer_id'], 'review_provider':'anthropic',
        'reviewer_fingerprint_sha256':row['reviewer_fingerprint'],
        'reviewed_applied_revision':baseline['revision'], 'reviewed_applied_change_set_id':baseline['change_set_id'],
        'coordinate_repairs':[], 'authority_kind':'exact_legacy_replay_and_applied_final_not_initial_review_pass',
        'independent_review_decision':'changes_suggested', 'adjudication_status':'auto_applied',
        'adjudication_binding':{'path':bundle['adjudication_path'],'file_sha256':bundle['adjudication_sha256']},
        'historical_replay':replay,
        'evidence_dependency_sha256':sha256_json({eid:record_content_sha(p) for eid,p in sorted(current_evidence.items())}),
        'fragment_dependency_sha256':sha256_json({fid:record_content_sha(p) for fid,p in sorted(current_fragments.items())}),
        'master_data_mutations':0,'model_calls_executed':0,'approval_status':'not_human_approved'}
    return body | {'artifact_sha256':sha256_json(body)}


def _sealed_adjudicated_bridge(*, claim_id, current_claim, item, resolution, versions,
                              current_evidence, current_fragments, review_binding,
                              review_input_binding, sealed_lineage_binding):
    """Reuse modern source eligibility, not an imaginary historical pass.

    This branch only accepts the exact applied sealed final candidate; it
    does not extend coordinate/semantic drift privileges to adjudicated data.
    """
    from backend.api.canonical_repository.knowledge_models import ClaimRecord
    from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha
    from backend.api.canonical_repository.viewpoint_source_attestation import build_source_eligibility_artifact
    final=item['payload']; normalized,_=_normalize_records(final)
    reviewed=normalized.get('claims',{}).get(claim_id)
    if not reviewed or _meaning(reviewed)!=_meaning(current_claim):
        raise CvpProductionBlocked([f'{claim_id}: sealed adjudicated final Claim differs from current generation'])
    for eid in reviewed['evidence_step_ids']:
        if eid not in current_evidence or _meaning(current_evidence[eid])!=_meaning(normalized['evidence_steps'][eid]):
            raise CvpProductionBlocked([f'{claim_id}: sealed adjudicated evidence drift'])
        for fid in evidence_fragment_ids(EvidenceStepRecord.model_validate(current_evidence[eid])):
            if fid not in current_fragments or _fragment_meaning(current_fragments[fid])!=_fragment_meaning(normalized['source_fragments'].get(fid,{})):
                raise CvpProductionBlocked([f'{claim_id}: sealed adjudicated fragment drift'])
    chain=sorted(versions,key=lambda v:v['revision'])
    for row in chain:
        if row.get('status')!='applied' or record_content_sha(row['payload'])!=row['content_sha256'] or row['payload'].get('revision')!=row['revision']:
            raise CvpProductionBlocked([f'{claim_id}: adjudicated history is not an authenticated applied chain'])
    if not chain or chain[-1]['payload']!=dict(current_claim) or chain[-1]['revision']!=current_claim['revision']:
        raise CvpProductionBlocked([f'{claim_id}: adjudicated history is not current'])
    review=item['review_payload']; adjudication=item['adjudication_payload']
    review_rows=[r for r in review.get('claim_reviews',[]) if r.get('claim_id')==claim_id]
    results=[r for r in adjudication.get('results',[]) if r.get('claim_id')==claim_id]
    if len(review_rows)!=1 or len(results)!=1:
        raise CvpProductionBlocked([f'{claim_id}: adjudicated review coverage is not exact-once'])
    if review_rows[0].get('spot_check_selected') or review_rows[0].get('human_review_reason') or review_rows[0].get('routing_status')=='human_spot_check':
        raise CvpProductionBlocked([f'{claim_id}: adjudication cannot override historical human hold'])
    manifest={'claims':[{'claim_id':claim_id,'pinned_claim_revision':current_claim['revision'],
                        'claim_revision_sha256':semantic_record_sha(ClaimRecord.model_validate(current_claim))}]}
    manifest['manifest_sha256']=sha256_json(manifest)
    sha=lambda key:hashlib.sha256(Path(item[key]).read_bytes()).hexdigest()
    artifact=build_source_eligibility_artifact(claim_manifest=manifest,claims=[dict(current_claim)],
        evidence_steps=list(current_evidence.values()),source_fragments=list(current_fragments.values()),
        reviewed_packages_by_claim_id={claim_id:{'payload':final,'artifact_sha256':item['application']['artifact_sha256'],'file_sha256':sha('path'),'path':str(item['path'])}},
        reviews_by_claim_id={claim_id:{'payload':review,'claim_review':review_rows[0],
            'review_input_artifact_sha256':item['review_input_sha256'],'artifact_sha256':sha('review_path'),'path':str(item['review_path']),
            'adjudication_payload':adjudication,'adjudication_result':results[0],
            'adjudication_artifact_sha256':sha('adjudication_path'),'overrides_artifact_sha256':sha('overrides_path')}})
    if artifact.exceptions or len(artifact.attestations)!=1:
        raise CvpProductionBlocked([f'{claim_id}: modern sealed source eligibility rejected: {artifact.exceptions}'])
    body={'schema_version':'wang_historical_claim_review_bridge_v1','status':'verified','claim_id':claim_id,
        'pinned_claim_revision':current_claim['revision'],'claim_revision_sha256':record_content_sha(current_claim),
        'review_binding':dict(review_binding),'review_input_binding':dict(review_input_binding),
        'sealed_lineage_binding':dict(sealed_lineage_binding),
        'review_model_id':resolution['reviewer_id'],'review_provider':review['reviewer']['provider'],
        'reviewer_fingerprint_sha256':review['reviewer']['fingerprint_sha256'],
        'reviewed_applied_revision':chain[-1]['revision'],'reviewed_applied_change_set_id':chain[-1]['change_set_id'],
        'coordinate_repairs':[],'authority_kind':'sealed_adjudicated_final_candidate_not_initial_review_pass',
        'independent_review_decision':resolution['independent_review_decision'],'adjudication_status':resolution['adjudication_status'],
        'modern_source_eligibility':artifact.model_dump(mode='json'),
        'evidence_dependency_sha256':sha256_json({eid:record_content_sha(p) for eid,p in sorted(current_evidence.items())}),
        'fragment_dependency_sha256':sha256_json({fid:record_content_sha(p) for fid,p in sorted(current_fragments.items())}),
        'master_data_mutations':0,'model_calls_executed':0,'approval_status':'not_human_approved'}
    return body|{'artifact_sha256':sha256_json(body)}


def authenticate_legacy_merge_target(claim_id, binding, review_binding, input_binding, versions):
    """Authenticate a passing recipient's exact historical merged generation.

    Only its applied duplicate-merge patches are semantic authority. Other
    suggestions in the same package, particularly human holds, stay unresolved.
    The entire historical application is still replayed byte-for-byte.
    """
    from backend.pipeline.legacy_candidate_reconciliation import inspect_legacy_bundle
    from backend.pipeline.legacy_auto_applied_review_reconciliation import verify_historical_replay
    final = _read_binding(binding)
    merges = final.get('consensus_application', {}).get('merged_claim_ids', {})
    donors = {cid for cid, target in merges.items() if target == claim_id}
    applied = set(final.get('consensus_application', {}).get('applied_claim_ids', []))
    if not donors or claim_id in merges or not donors <= applied or claim_id in applied:
        raise CvpProductionBlocked([f'{claim_id}: no one-hop historical merge authority'])
    path = Path(binding['path'])
    bundle = inspect_legacy_bundle(path, validation_claim_ids=donors)
    replay = verify_historical_replay(path, validation_claim_ids=donors)
    if (bundle.get('reason') or not replay
            or not bundle.get('scoped_adjudication_structurally_validated')
            or set(bundle.get('validated_adjudication_claim_ids', [])) != donors
            or bundle.get('review_sha256') != review_binding['file_sha256']
            or bundle.get('package_sha256') != input_binding['file_sha256']):
        raise CvpProductionBlocked([f'{claim_id}: historical merge replay/source validation failed'])
    for donor in donors:
        row = bundle['review_rows'].get(donor, {})
        result = bundle['adjudication_results'].get(donor, {})
        if (row.get('decision') != 'changes_suggested' or row.get('human_review_reason')
                or row.get('spot_check_selected') or result.get('status') != 'auto_applied'
                or result.get('human_review_reason')
                or result.get('openai', {}).get('patch', {}).get('superseded_by_claim_id') != claim_id):
            raise CvpProductionBlocked([f'{claim_id}: merge donor lacks independent resolved duplicate finding'])
    normalized, _ = _normalize_records(final)
    reviewed = normalized.get('claims', {}).get(claim_id)
    original = bundle['original_claims'].get(claim_id)
    if not original or not reviewed or original.get('statement') != reviewed.get('statement'):
        raise CvpProductionBlocked([f'{claim_id}: duplicate merge rewrote passing recipient statement'])
    first = min(versions, key=lambda r: r['revision']) if versions else {}
    if (reviewed is None or first.get('source_kind') != 'knowledge_package'
            or first.get('source_sha256') != sha256_json(final)
            or _meaning(first.get('payload', {})) != _meaning(reviewed)):
        raise CvpProductionBlocked([f'{claim_id}: initial applied generation is not the replayed merge'])
    return normalized, {
        'authority_kind': 'passing_recipient_of_exact_replayed_duplicate_merge',
        'legacy_reviewed_candidate_binding': dict(binding),
        'adjudication_binding': {'path': bundle['adjudication_path'], 'file_sha256': bundle['adjudication_sha256']},
        'merged_claim_ids': sorted(donors), 'historical_replay': replay,
        'unrelated_human_findings_released': False,
    }


def verify_legacy_evidence_history(eid, reviewed, current, final_sha):
    """Authenticate a separately applied reverse-index repair, not just IDs."""
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.viewpoint_link_history_repair import verify_chain
    with PostgresKnowledgeStore().connect() as connection:
        connection.execute('SET TRANSACTION READ ONLY')
        rows = connection.execute(
            "SELECT v.revision,v.content_sha256,v.payload,v.change_set_id,c.source_kind,c.source_sha256,c.status,c.metadata "
            "FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c USING(change_set_id) "
            "WHERE v.collection='evidence_steps' AND v.object_id=%s ORDER BY v.revision", (eid,)).fetchall()
    columns = ('revision','content_sha256','payload','change_set_id','source_kind','source_sha256','status','metadata')
    history = [dict(zip(columns, row)) for row in rows]
    if (not history or history[0]['source_kind'] != 'knowledge_package'
            or history[0]['source_sha256'] != final_sha
            or _meaning(history[0]['payload']) != _meaning(reviewed)):
        raise CvpProductionBlocked([f'{eid}: evidence repair does not start at replayed final'])
    try:
        return verify_chain(history, collection='evidence_steps', object_id=eid, current=current)
    except (ValueError, KeyError) as exc:
        raise CvpProductionBlocked([f'{eid}: invalid applied evidence repair: {exc}']) from exc


def verify_fragment_own_source(fragment, current_claim, review, store=None):
    """Recognize only an exact unique active registered sermon alias.

    A transcript key on legacy occurrences and its SourceDocument ID are not
    foreign sources. Neither titles, paths nor fuzzy similarity establish this.
    Keep the physical review-source SHA and fragment generation constraints.
    """
    own = {o.get('source_id') for o in current_claim.get('occurrences', [])}
    if not fragment:
        raise ValueError('adjudicated evidence fragment missing')
    sid = fragment.get('source_id')
    if sid in own:
        return
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.viewpoint_audit_source_adjudication import validate_own_source_identity
    store = store or PostgresKnowledgeStore()
    state = store.get_record_state('source_documents', sid)
    if not state or state['retired']:
        raise ValueError('adjudicated source is missing or retired')
    source = state['payload']
    validate_own_source_identity(source, own, store)
    if (source.get('source_id') != sid or source.get('source_sha256') != fragment.get('source_sha256')
            or record_content_sha(source) != state['content_sha256']):
        raise ValueError('adjudicated source generation drift')
    path = Path(review.get('source', {}).get('transcript_paths', {}).get(sid, ''))
    expected = review.get('source', {}).get('transcript_sha256', {}).get(sid)
    if not expected or not path.is_absolute() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError('adjudicated own-source physical SHA drift')


def verify_historical_review_bridge(
    *, claim_id: str, current_claim: Mapping[str, Any],
    review_binding: Mapping[str, str], review_input_binding: Mapping[str, str],
    versions: Sequence[Mapping[str, Any]],
    current_evidence: Mapping[str, Mapping[str, Any]],
    current_fragments: Mapping[str, Mapping[str, Any]],
    human_closure_binding: Mapping[str, str] | None = None,
    sealed_lineage_binding: Mapping[str, str] | None = None,
    random_spot_check_policy_binding: Mapping[str, str] | None = None,
    legacy_reviewed_candidate_binding: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Verify one read-only DB version chain; never ignore arbitrary drift."""
    review = _read_binding(review_binding)
    package = _read_binding(review_input_binding)
    if (review.get("source") or {}).get("package_sha256") != review_input_binding["file_sha256"]:
        raise CvpProductionBlocked([f"{claim_id}: review input generation SHA mismatch"])
    projection = _normalize_claim_layer(package)
    if review.get("reviewed_claims") != projection["candidate_claims"]:
        raise CvpProductionBlocked([f"{claim_id}: historical review does not cover its input projection"])
    reviewer = review.get("reviewer") or {}
    rows = [row for row in review.get('claim_reviews', []) if row.get('claim_id') == claim_id]
    if (legacy_reviewed_candidate_binding is not None and len(rows) == 1
            and rows[0].get('decision') == 'changes_suggested'):
        if human_closure_binding is not None:
            raise CvpProductionBlocked([f'{claim_id}: legacy adjudication cannot override a human hold'])
        return _legacy_adjudicated_bridge(claim_id=claim_id, current_claim=current_claim, versions=versions,
            current_evidence=current_evidence, current_fragments=current_fragments,
            review_binding=review_binding, review_input_binding=review_input_binding,
            legacy_reviewed_candidate_binding=legacy_reviewed_candidate_binding)
    sealed_model = None
    sealed_final = None
    if reviewer.get("provider") == "anthropic_batched":
        if sealed_lineage_binding is None:
            raise CvpProductionBlocked([f"{claim_id}: batched review requires authenticated sealed lineage"])
        lineage = _read_binding(sealed_lineage_binding)
        from backend.pipeline.viewpoint_source_attestation_runner import _validated_lineage_inputs
        items = _validated_lineage_inputs(manifest_claim_ids={row["claim_id"] for row in lineage["claims"]}, lineage_manifest_path=Path(sealed_lineage_binding["path"]))
        item = items.get(claim_id)
        if item is None or item["review_payload"] != review or item["review_input_payload"] != package:
            raise CvpProductionBlocked([f"{claim_id}: sealed lineage binds another review generation"])
        resolution = next((row for row in item["application"]["review_resolutions"] if row["claim_id"] == claim_id), {})
        if resolution.get('target_review_status')=='ai_consensus_reviewed' and resolution.get('adjudication_status')=='auto_applied' and resolution.get('independent_review_decision')=='changes_suggested':
            if human_closure_binding is not None:
                raise CvpProductionBlocked([f'{claim_id}: automatic adjudication cannot override a human hold'])
            return _sealed_adjudicated_bridge(claim_id=claim_id,current_claim=current_claim,item=item,resolution=resolution,versions=versions,
                current_evidence=current_evidence,current_fragments=current_fragments,review_binding=review_binding,
                review_input_binding=review_input_binding,sealed_lineage_binding=sealed_lineage_binding)
        if resolution.get("target_review_status") != "ai_consensus_reviewed" or resolution.get("adjudication_status") != "not_required" or resolution.get("independent_review_decision") != "pass":
            raise CvpProductionBlocked([f"{claim_id}: sealed review does not resolve this passing Claim"])
        sealed_model = resolution.get("reviewer_id")
        sealed_final = item
    identity = {key: value for key, value in reviewer.items() if key not in {"fingerprint_sha256", "generated_at", "artifact_sha256", "provider"}}
    if sealed_model is None and sha256_json(identity) != reviewer.get("fingerprint_sha256"):
        raise CvpProductionBlocked([f"{claim_id}: historical reviewer fingerprint mismatch"])
    rows = [row for row in review.get("claim_reviews") or [] if row.get("claim_id") == claim_id]
    human_confirmed = False
    if human_closure_binding is not None:
        closure = _read_binding(human_closure_binding)
        validate_content_addressed(closure, "artifact_sha256")
        approvals = [row for row in closure.get("claims") or [] if row.get("claim_id") == claim_id]
        if closure.get("schema_version") != "wang_claim_human_spot_check_closure_v1" or closure.get("confirmed_by") != "human_user" or closure.get("user_message") != "确认" or len(approvals) != 1:
            raise CvpProductionBlocked([f"{claim_id}: invalid human spot-check closure"])
        approved = approvals[0]
        if approved.get("decision") != "pass" or approved.get("claim_revision_sha256") != record_content_sha(current_claim) or approved.get("pinned_claim_revision") != current_claim["revision"] or approved.get("review_binding") != dict(review_binding):
            raise CvpProductionBlocked([f"{claim_id}: human closure does not bind current Claim/review"])
        if approved.get("evidence_dependency_sha256") != sha256_json({eid: record_content_sha(raw) for eid, raw in sorted(current_evidence.items())}) or approved.get("fragment_dependency_sha256") != sha256_json({fid: record_content_sha(raw) for fid, raw in sorted(current_fragments.items())}):
            raise CvpProductionBlocked([f"{claim_id}: human closure evidence drift"])
        human_confirmed = True
    random_sample_waived = False
    if random_spot_check_policy_binding is not None:
        policy = _read_binding(random_spot_check_policy_binding)
        validate_content_addressed(policy, "artifact_sha256")
        if (policy.get("schema_version") != "wang_resolved_random_spot_check_policy_v1"
                or policy.get("authorized_by") != "human_user"
                or not policy.get("user_message")
                or policy.get("rule") != "pass_no_issues_random_sample_only"
                or claim_id not in policy.get("claim_ids", [])
                or policy.get("grants_human_content_approval") is not False):
            raise CvpProductionBlocked([f"{claim_id}: invalid random spot-check policy"])
        random_sample_waived = (len(rows) == 1 and rows[0].get("decision") == "pass"
            and rows[0].get("issues") == [] and not rows[0].get("human_review_reason")
            and rows[0].get("spot_check_selected") is True
            and rows[0].get("routing_status") == "human_spot_check"
            and current_claim.get("review_status") == "ai_consensus_reviewed"
            and not human_confirmed)
    sample_released = human_confirmed or random_sample_waived
    if len(rows) != 1 or any((
        rows[0].get("decision") != "pass", bool(rows[0].get("issues")),
        rows[0].get("routing_status") != ("human_spot_check" if sample_released else "ai_reviewed"), bool(rows[0].get("human_review_reason")),
        rows[0].get("reviewer_fingerprint") != reviewer.get("fingerprint_sha256"),
        reviewer.get("provider") not in ({"anthropic_batched"} if sealed_model else {"anthropic"}), not (sealed_model or reviewer.get("review_model_id")),
        bool(rows[0].get("spot_check_selected")) != sample_released,
    )):
        raise CvpProductionBlocked([f"{claim_id}: historical review is not a resolved independent pass"])
    normalized, _ = _normalize_records(package)
    reviewed = normalized.get("claims", {}).get(claim_id)
    if reviewed is None or current_claim.get("claim_id") != claim_id:
        raise CvpProductionBlocked([f"{claim_id}: review input lacks this Claim"])
    ordered = sorted(versions, key=lambda row: row["revision"])
    for row in ordered:
        if row.get("status") != "applied" or record_content_sha(row["payload"]) != row["content_sha256"] or row["payload"].get("revision") != row["revision"]:
            raise CvpProductionBlocked([f"{claim_id}: version history is not an authenticated applied chain"])
    baselines = [row for row in ordered if _meaning(row["payload"]) == _meaning(reviewed)]
    merge_proof = None
    if not baselines and sealed_final is not None:
        # A passing recipient can gain evidence from an independently resolved
        # duplicate donor before its first ingestion. Authenticate that exact
        # historical application; do not pretend its initial pass covered it.
        candidate_path = Path(sealed_final['path'])
        candidate_binding = {'path': str(candidate_path),
                             'file_sha256': hashlib.sha256(candidate_path.read_bytes()).hexdigest()}
        normalized, merge_proof = authenticate_legacy_merge_target(
            claim_id, candidate_binding, review_binding, review_input_binding, ordered)
        reviewed = normalized['claims'][claim_id]
        baselines = [row for row in ordered if _meaning(row['payload']) == _meaning(reviewed)]
    if not baselines and legacy_reviewed_candidate_binding is not None:
        normalized, merge_proof = authenticate_legacy_merge_target(
            claim_id, legacy_reviewed_candidate_binding, review_binding, review_input_binding, ordered)
        reviewed = normalized['claims'][claim_id]
        baselines = [row for row in ordered if _meaning(row['payload']) == _meaning(reviewed)]
    if not baselines:
        raise CvpProductionBlocked([f"{claim_id}: no applied generation matches the independently reviewed Claim"])
    baseline = baselines[-1]
    chain = [row for row in ordered if row["revision"] >= baseline["revision"]]
    if [row["revision"] for row in chain] != list(range(baseline["revision"], int(current_claim["revision"]) + 1)) or chain[-1]["payload"] != dict(current_claim):
        raise CvpProductionBlocked([f"{claim_id}: historical chain is discontinuous or not current"])
    repairs = []
    evidence_index_repairs = []
    review_migrations = []
    for before, after in zip(chain, chain[1:]):
        if _meaning(before["payload"]) == _meaning(after["payload"]) and after.get("source_kind") == "review_decision":
            continue
        if after.get('source_kind') == 'legacy_coordinate_only_review_reconciliation_v1':
            review_migrations.append(verify_coordinate_review_migration(
                claim_id, before, after, review_binding, review_input_binding, review))
            continue
        if after.get("source_kind") == "wkp364_claim_evidence_pair_adjudication":
            # Existing verifier binds the exact applied ChangeSet and guard,
            # requires independent include decisions for every added ID, and
            # rejects deletion, reordering or any non-index content change.
            from backend.pipeline.viewpoint_link_history_repair import verify_transition
            try:
                proof = verify_transition(before, after, collection="claims", object_id=claim_id)
                authorization = after["metadata"]["claim_evidence_reciprocity_repair"]["store_guard"]["expected_pair_adjudication_authorization"]
                for eid in proof["authorized_added_evidence_ids"]:
                    action = next(a for a in authorization["actions"]
                                  if a.get("claim_id") == claim_id and a.get("evidence_step_id") == eid)
                    old_step = normalized.get("evidence_steps", {}).get(eid)
                    live_step = current_evidence.get(eid)
                    if (not old_step or not live_step
                            or record_content_sha(old_step) != action["evidence_content_sha256"]
                            or _meaning(old_step) != _meaning(live_step)
                            or claim_id not in live_step.get("produced_claim_ids", [])):
                        raise ValueError("adjudicated evidence is not the exact reviewed, reciprocal step")
                    for fid in evidence_fragment_ids(EvidenceStepRecord.model_validate(live_step)):
                        fragment = current_fragments.get(fid)
                        verify_fragment_own_source(fragment, current_claim, review)
                evidence_index_repairs.append(proof)
                continue
            except (ValueError, KeyError, StopIteration) as exc:
                raise CvpProductionBlocked([f"{claim_id}: invalid applied evidence-index repair: {exc}"]) from exc
        if after.get("source_kind") != "wkp368_source_contract_cleanup":
            raise CvpProductionBlocked([f"{claim_id}: post-review change is not a supported coordinate repair"])
        metadata = after.get("metadata") or {}
        plan_binding = {"path": metadata["source_plan_path"], "file_sha256": metadata["source_plan_file_sha256"]}
        plan = _read_binding(plan_binding)
        validate_content_addressed(plan, "artifact_sha256")
        if plan["plan"]["change_set_id"] != after["change_set_id"] or plan["plan"]["source_sha256"] != after.get("source_sha256"):
            raise CvpProductionBlocked([f"{claim_id}: coordinate plan is not the applied ChangeSet"])
        matches = [row for row in plan["changed_records"] if row["collection"] == "claims" and row["object_id"] == claim_id]
        if len(matches) != 1 or matches[0]["before_sha256"] != before["content_sha256"] or matches[0]["after_sha256"] != after["content_sha256"]:
            raise CvpProductionBlocked([f"{claim_id}: repair plan does not bind Claim before/after versions"])
        # Only paragraph coordinates may differ; source identity, wording,
        # evidence IDs, quotation, stance and every other field must remain.
        left, right = deepcopy(_meaning(before["payload"])), deepcopy(_meaning(after["payload"]))
        for payload in (left, right):
            for occurrence in payload.get("occurrences") or []:
                for anchor in occurrence.get("anchors") or []:
                    anchor.pop("paragraph_key", None)
        if left != right:
            raise CvpProductionBlocked([f"{claim_id}: repair changed more than paragraph coordinates"])
        source = plan["source"]
        physical_source = Path(source["source_path"])
        if not physical_source.is_absolute() or hashlib.sha256(physical_source.read_bytes()).hexdigest() != source["source_file_sha256"]:
            raise CvpProductionBlocked([f"{claim_id}: repaired source physical SHA drift"])
        if (review.get("source", {}).get("transcript_sha256") or {}).get(source["source_id"]) != source["source_file_sha256"]:
            raise CvpProductionBlocked([f"{claim_id}: review and repair bind different source bytes"])
        repairs.append({"change_set_id": after["change_set_id"], "plan_binding": plan_binding,
                        "before_revision": before["revision"], "after_revision": after["revision"],
                        "before_sha256": before["content_sha256"], "after_sha256": after["content_sha256"]})
    # Include the separately adjudicated additions in dependency proof, not
    # just the original Claim's forward index. Their fragments were already
    # in this independently reviewed source package; no new wording is admitted.
    for evidence_id in current_claim["evidence_step_ids"]:
        if evidence_id not in current_evidence or evidence_id not in normalized["evidence_steps"]:
            raise CvpProductionBlocked([f"{claim_id}: reviewed EvidenceStep changed after review"])
        if _meaning(current_evidence[evidence_id]) != _meaning(normalized["evidence_steps"][evidence_id]):
            if merge_proof is not None:
                proof = verify_legacy_evidence_history(evidence_id, normalized['evidence_steps'][evidence_id],
                    current_evidence[evidence_id], merge_proof['historical_replay']['historical_replay_sha256'])
            else:
                proof=verify_sealed_reverse_index(evidence_id,normalized['evidence_steps'][evidence_id],
                    current_evidence[evidence_id],sealed_final)
            evidence_index_repairs.append(proof)
        for fragment_id in evidence_fragment_ids(EvidenceStepRecord.model_validate(current_evidence[evidence_id])):
            old = normalized["source_fragments"].get(fragment_id)
            current = current_fragments.get(fragment_id)
            if old is None or current is None or _fragment_meaning(old) != _fragment_meaning(current):
                raise CvpProductionBlocked([f"{claim_id}: reviewed fragment meaning/modality changed after review"])
    body = {
        "schema_version": "wang_historical_claim_review_bridge_v1", "status": "verified",
        "claim_id": claim_id, "pinned_claim_revision": current_claim["revision"],
        "claim_revision_sha256": record_content_sha(current_claim),
        "review_binding": dict(review_binding), "review_input_binding": dict(review_input_binding),
        "review_model_id": sealed_model or reviewer["review_model_id"], "review_provider": reviewer["provider"],
        "reviewer_fingerprint_sha256": reviewer["fingerprint_sha256"],
        "reviewed_applied_revision": baseline["revision"],
        "reviewed_applied_change_set_id": baseline["change_set_id"], "coordinate_repairs": repairs,
        "evidence_dependency_sha256": sha256_json({eid: record_content_sha(current_evidence[eid]) for eid in sorted(current_claim["evidence_step_ids"])}),
        "fragment_dependency_sha256": sha256_json({fid: record_content_sha(current_fragments[fid]) for eid in current_claim["evidence_step_ids"] for fid in evidence_fragment_ids(EvidenceStepRecord.model_validate(current_evidence[eid]))}),
        "master_data_mutations": 0, "model_calls_executed": 0, "approval_status": "not_human_approved",
    }
    if human_closure_binding is not None:
        body["human_spot_check_closure_binding"] = dict(human_closure_binding)
        body["human_spot_check_confirmed"] = True
    if sealed_lineage_binding is not None:
        body["sealed_lineage_binding"] = dict(sealed_lineage_binding)
    if evidence_index_repairs:
        body["evidence_index_repairs"] = evidence_index_repairs
    if review_migrations:
        body['review_migrations'] = review_migrations
    if merge_proof is not None:
        body['legacy_merge_authority'] = merge_proof
        body['legacy_reviewed_candidate_binding'] = dict(merge_proof['legacy_reviewed_candidate_binding'])
    if random_sample_waived:
        body["random_spot_check_policy_binding"] = dict(random_spot_check_policy_binding)
        body["random_spot_check_waived_by_owner_policy"] = True
        body["human_content_approval"] = False
    return body | {"artifact_sha256": sha256_json(body)}


def verify_sealed_reverse_index(eid, reviewed, current, authenticated_item):
    """Reuse an authenticated final graph's additive reverse index, not new meaning.

    The caller has authenticated the full sealed review generation and its
    passing resolution. No arbitrary history or hand-authored index is accepted.
    """
    if authenticated_item is None:
        raise CvpProductionBlocked([f'{eid}: no authenticated final candidate for reverse index'])
    final,_=_normalize_records(authenticated_item['payload'])
    expected=final.get('evidence_steps',{}).get(eid)
    if expected is None or _meaning(expected)!=_meaning(current):
        raise CvpProductionBlocked([f'{eid}: current EvidenceStep differs from sealed final graph'])
    left,right=deepcopy(_meaning(reviewed)),deepcopy(_meaning(current))
    old_ids=left.pop('produced_claim_ids',[]); new_ids=right.pop('produced_claim_ids',[])
    if left!=right:
        raise CvpProductionBlocked([f'{eid}: EvidenceStep content changed, not just reverse index'])
    added=set(new_ids)-set(old_ids)
    removed=set(old_ids)-set(new_ids)
    if removed and not added:
        return verify_sealed_anchor_exclusion(eid, reviewed, current, authenticated_item, removed)
    if not added or len(new_ids)!=len(set(new_ids)) or [cid for cid in new_ids if cid in set(old_ids)]!=old_ids:
        raise CvpProductionBlocked([f'{eid}: reverse index deletion/reordering/duplication'])
    for cid in added:
        claim=final.get('claims',{}).get(cid)
        if not claim or eid not in claim.get('evidence_step_ids',[]):
            raise CvpProductionBlocked([f'{eid}: reverse index addition lacks sealed reciprocal Claim'])
    return {'evidence_step_id':eid,'authority_kind':'sealed_final_additive_reverse_index',
            'final_candidate_sha256':authenticated_item['application']['artifact_sha256'],
            'added_claim_ids':sorted(added),'evidence_content_unchanged':True}


def verify_sealed_anchor_exclusion(eid, reviewed, current, item, removed):
    """Only exact replayed, independently accepted anchor exclusions qualify.

    This is not a deletion waiver: the complete original application, applied
    EvidenceStep lineage, each removed Claim's patch and reciprocal final graph
    must all authenticate. The corpus is never modified by this reader.
    """
    from backend.pipeline.legacy_candidate_reconciliation import inspect_legacy_bundle
    from backend.pipeline.legacy_auto_applied_review_reconciliation import verify_historical_replay
    path = Path(item['path'])
    bundle = inspect_legacy_bundle(path, validation_claim_ids=removed)
    replay = verify_historical_replay(path, validation_claim_ids=removed)
    if (bundle.get('reason') or not replay or
            replay.get('replay_contract') != 'sealed_batched_exact_full_package_v1' or
            not bundle.get('scoped_adjudication_structurally_validated') or
            set(bundle.get('validated_adjudication_claim_ids', [])) != removed or
            _read_binding({'path':str(path),'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}) != item['payload']):
        raise CvpProductionBlocked([f'{eid}: excluded anchor lacks exact historical replay'])
    final,_ = _normalize_records(item['payload'])
    survey = _normalize_claim_layer(_read_binding({'path':bundle['package_path'],
                                                  'file_sha256':bundle['package_sha256']}))
    original_claims = {c['claim_id']:c for c in survey['candidate_claims']}
    for cid in removed:
        row = bundle['review_rows'].get(cid, {})
        result = bundle['adjudication_results'].get(cid, {})
        original = original_claims.get(cid, {})
        patch = result.get('openai', {}).get('patch', {})
        indexes = patch.get('excluded_anchor_indexes', [])
        anchors = original.get('anchors', [])
        if (row.get('decision') != 'changes_suggested' or row.get('human_review_reason') or
                row.get('spot_check_selected') or result.get('status') != 'auto_applied' or
                result.get('human_review_reason') or result.get('openai', {}).get('decision') != 'accept' or
                cid not in final.get('claims', {}) or eid in final['claims'][cid].get('evidence_step_ids', []) or
                not any(isinstance(i,int) and 0 <= i < len(anchors) and
                        anchors[i].get('evidence_id') == eid for i in indexes)):
            raise CvpProductionBlocked([f'{eid}: removed Claim lacks accepted own anchor exclusion'])
    left,right = deepcopy(_meaning(reviewed)),deepcopy(_meaning(current))
    old = left.pop('produced_claim_ids', []); new = right.pop('produced_claim_ids', [])
    if (left != right or len(old) != len(set(old)) or len(new) != len(set(new)) or
            new != [cid for cid in old if cid not in removed]):
        raise CvpProductionBlocked([f'{eid}: exclusion changed content/order or added references'])
    history = verify_legacy_evidence_history(eid, final['evidence_steps'][eid], current,
                                            replay['historical_replay_sha256'])
    return {'evidence_step_id':eid,'authority_kind':'exact_replayed_applied_anchor_exclusion',
            'removed_claim_ids':sorted(removed),'historical_replay':replay,'applied_history':history,
            'review_binding':{'path':bundle['review_path'],'file_sha256':bundle['review_sha256']},
            'adjudication_binding':{'path':bundle['adjudication_path'],'file_sha256':bundle['adjudication_sha256']},
            'evidence_content_unchanged':True,'unrelated_human_findings_released':False}


def main() -> None:
    import argparse
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.viewpoint_source_attestation_runner import _write_immutable
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    ids = [row["claim_id"] for row in selection["claims"]]
    if not ids or len(ids) != len(set(ids)):
        raise CvpProductionBlocked(["historical review selection must be nonempty and unique"])
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    store = PostgresKnowledgeStore()
    proofs = []
    with store.connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        for selected in selection["claims"]:
            cid = selected["claim_id"]
            current = connection.execute("SELECT payload FROM wang_knowledge.objects WHERE collection='claims' AND object_id=%s AND retired_at IS NULL", (cid,)).fetchone()
            if current is None:
                raise CvpProductionBlocked([f"{cid}: current Claim missing"])
            history = connection.execute("SELECT v.revision,v.content_sha256,v.payload,v.change_set_id,c.source_kind,c.source_sha256,c.status,c.metadata FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c USING(change_set_id) WHERE v.collection='claims' AND v.object_id=%s ORDER BY v.revision", (cid,)).fetchall()
            columns = ("revision", "content_sha256", "payload", "change_set_id", "source_kind", "source_sha256", "status", "metadata")
            evidence, fragments = {}, {}
            for eid in current[0]["evidence_step_ids"]:
                raw = connection.execute("SELECT payload FROM wang_knowledge.objects WHERE collection='evidence_steps' AND object_id=%s AND retired_at IS NULL", (eid,)).fetchone()
                if raw is None:
                    raise CvpProductionBlocked([f"{cid}: active evidence missing"])
                evidence[eid] = raw[0]
                for fid in evidence_fragment_ids(EvidenceStepRecord.model_validate(raw[0])):
                    fragment = connection.execute("SELECT payload FROM wang_knowledge.objects WHERE collection='source_fragments' AND object_id=%s AND retired_at IS NULL", (fid,)).fetchone()
                    if fragment is None:
                        raise CvpProductionBlocked([f"{cid}: active fragment missing"])
                    fragments[fid] = fragment[0]
            proofs.append(verify_historical_review_bridge(
                claim_id=cid, current_claim=current[0], versions=[dict(zip(columns, row)) for row in history],
                review_binding=selected["review_binding"], review_input_binding=selected["review_input_binding"],
                current_evidence=evidence, current_fragments=fragments,
                human_closure_binding=selected.get("human_closure_binding"),
                sealed_lineage_binding=selected.get("sealed_lineage_binding"),
                random_spot_check_policy_binding=selected.get("random_spot_check_policy_binding"),
                legacy_reviewed_candidate_binding=selected.get('legacy_reviewed_candidate_binding'),
            ))
    body = {"schema_version": "wang_historical_claim_review_bridges_v1", "status": "verified",
            "selection_file_sha256": hashlib.sha256(args.selection.read_bytes()).hexdigest(),
            "proofs": proofs, "verified_claim_count": len(proofs), "model_calls_executed": 0,
            "master_data_mutations": 0, "consumer_source_eligibility_artifact": False}
    _write_immutable(args.output, body | {"artifact_sha256": sha256_json(body)})
    print(json.dumps({"status": "verified", "claims": len(proofs), "output": str(args.output)}))


if __name__ == "__main__":
    main()
