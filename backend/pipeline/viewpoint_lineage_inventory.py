"""Read-only inventory of extraction review inputs for a frozen grouping.

This report is diagnostic, never a source eligibility attestation. In
particular, historical coordinate repairs must be proved from their applied
ChangeSets rather than treating a matching Claim statement as approval.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.knowledge_models import ClaimRecord
from backend.api.canonical_repository.postgres_store import _normalize_records, _substantive_payload, record_content_sha
from backend.api.canonical_repository.reviewed_candidate_contract import validate_reviewed_candidate_artifact
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import validate_content_addressed


def applied_review_match(claim_id, variants, events):
    """Discover the uniquely recorded legacy review; never grant eligibility.

    Legacy events record a truncated artifact hash in their reason. Compare the
    complete deterministic reason, applied before/after version, event and
    operation ledger, not that prefix alone. The full historical bridge remains
    mandatory after discovery and verifies the full physical review/input SHAs.
    """
    from backend.pipeline.ai_review_ledger import Verdict, review_reason
    from copy import deepcopy
    matched = set()
    if any(e['reviewer_kind'] == 'human' for e in events):
        return variants
    for event in events:
        if event['source_kind'] == 'legacy_coordinate_only_review_reconciliation_v1':
            matched.update(_coordinate_migration_review_matches(claim_id, variants, event))
            continue
        before, after = event['before'], event['after']
        left, right = deepcopy(before), deepcopy(after)
        for payload in (left, right):
            for key in ('revision', 'review_status', 'review_note', 'reviewed_by', 'reviewed_at'):
                payload.pop(key, None)
        if (event['status'] != 'applied' or event['source_kind'] != 'review_decision'
                or event['reviewer_kind'] != 'ai' or event['decision'] != 'ai_consensus_reviewed'
                or before.get('claim_id') != claim_id or after.get('claim_id') != claim_id
                or after.get('revision') != before.get('revision', 0) + 1 or left != right
                or after.get('review_status') != event['decision']
                or after.get('review_note') != event['reason']
                or after.get('reviewed_by') != event['reviewer_id']
                or event['before_sha'] != record_content_sha(before)
                or event['after_sha'] != record_content_sha(after)
                or event['source_sha'] != event['before_sha']
                or event['operation'] != 'update' or event['operation_count'] != 1
                or event['metadata'] != {'review_event_id': event['event_id']}
                or event['artifact'] != {'change_set_id': event['change_set_id']}
                or event['fingerprint'] != sha256_json({'review_event_id': event['event_id'],
                        'collection': 'claims', 'object_id': claim_id})):
            continue
        for index, variant in enumerate(variants):
            rb, pb = variant['review_binding'], variant['review_input_binding']
            raw = Path(rb['path']).read_bytes(); package = Path(pb['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != rb['file_sha256'] or hashlib.sha256(package).hexdigest() != pb['file_sha256']:
                continue
            review = json.loads(raw)
            rows = [r for r in review.get('claim_reviews', []) if r.get('claim_id') == claim_id]
            if len(rows) != 1 or review.get('source', {}).get('package_sha256') != pb['file_sha256']:
                continue
            row = rows[0]
            if (row.get('decision') != 'pass' or row.get('issues') or row.get('human_review_reason')
                    or row.get('spot_check_selected') or row.get('routing_status') != 'ai_reviewed'):
                continue
            expected = review_reason(Verdict(claim_id, 'pass', 'ai_consensus_reviewed',
                                    row.get('rationale', ''), rb['path'], rb['file_sha256']))
            if expected == event['reason']:
                matched.add(index)
    return [variants[next(iter(matched))]] if len(matched) == 1 else variants


def _coordinate_migration_review_matches(claim_id, variants, event):
    """Discover identity only after the existing full applied #397 proof passes.

    Neither the source-kind nor event's SHA pointers grant eligibility. The
    frozen complete plan, graph, selected review and exact status-only postimage
    are reconstructed by the same migration verifier used by the full bridge.
    Current source/evidence/coordinate/human-hold gates still run afterwards.
    """
    from backend.pipeline.viewpoint_historical_review_bridge import verify_coordinate_review_migration
    from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked
    if (event['status'] != 'applied' or event['reviewer_kind'] != 'ai'
            or event['decision'] != 'ai_consensus_reviewed' or event['operation'] != 'update'
            or event['source_sha'] is None):
        return set()
    before, after = event['before'], event['after']
    if (before.get('claim_id') != claim_id or after.get('claim_id') != claim_id
            or event['before_sha'] != record_content_sha(before)
            or event['after_sha'] != record_content_sha(after)
            or after.get('review_note') != event['reason']
            or after.get('reviewed_by') != event['reviewer_id']):
        return set()
    left = {'payload': before, 'revision': before['revision'], 'content_sha256': event['before_sha']}
    right = {'payload': after, 'revision': after['revision'], 'content_sha256': event['after_sha'],
             'change_set_id': event['change_set_id'], 'source_kind': event['source_kind'],
             'source_sha256': event['source_sha'], 'metadata': event['metadata']}
    matched = set()
    for index, variant in enumerate(variants):
        rb, pb = variant['review_binding'], variant['review_input_binding']
        artifact = event['artifact']
        if (artifact.get('review_artifact_sha256') != rb['file_sha256']
                or artifact.get('package_sha256') != pb['file_sha256']):
            continue
        raw = Path(rb['path']).read_bytes(); package = Path(pb['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != rb['file_sha256'] or hashlib.sha256(package).hexdigest() != pb['file_sha256']:
            continue
        review = json.loads(raw)
        if (review.get('source', {}).get('package_sha256') != pb['file_sha256']
                or review.get('reviewer', {}).get('fingerprint_sha256') != artifact.get('review_fingerprint')):
            continue
        try:
            proof = verify_coordinate_review_migration(claim_id, left, right, rb, pb, review)
            # The verifier reconstructs every frozen operation/event. Also bind
            # its complete operation count/fingerprint and SQL event artifact.
            from backend.pipeline.legacy_candidate_reconciliation import _decode_plan
            document = json.loads(Path(proof['plan_binding']['path']).read_bytes())
            plans = [p for p in document['plans'] if p.get('change_set_id') == event['change_set_id']]
            if len(plans) != 1:
                continue
            plan = _decode_plan(plans[0])
            rows = [e for e in plan.review_events if e.collection == 'claims' and e.object_id == claim_id]
            if (plan.fingerprint_sha256 != event['fingerprint']
                    or len(plan.operations) != event['operation_count'] or len(rows) != 1
                    or rows[0].review_event_id != event['event_id']
                    or rows[0].artifact != artifact or rows[0].reason != event['reason']):
                continue
        except (CvpProductionBlocked, ValueError, KeyError, TypeError):
            continue
        matched.add(index)
    return matched


def discover_applied_review(claim_id, variants, store):
    if len(variants) < 2 or not hasattr(store, 'connect'):
        return variants
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
        cursor.execute("""SELECT re.review_event_id,re.reviewer_kind,re.reviewer_id,re.decision,
              re.reason,re.artifact,cs.change_set_id,cs.status,cs.source_kind,cs.source_sha256,
              cs.fingerprint_sha256,cs.metadata,b.payload,v.payload,co.before_sha256,
              co.after_sha256,co.operation,
              (SELECT count(*) FROM wang_knowledge.change_operations x
               WHERE x.change_set_id=cs.change_set_id)
            FROM wang_knowledge.review_events re
            JOIN wang_knowledge.object_versions v ON v.collection=re.collection
              AND v.object_id=re.object_id AND v.revision=re.object_revision
            JOIN wang_knowledge.change_sets cs ON cs.change_set_id=v.change_set_id
            JOIN wang_knowledge.change_operations co ON co.change_set_id=cs.change_set_id
              AND co.collection=v.collection AND co.object_id=v.object_id
              AND co.after_revision=v.revision AND co.after_sha256=v.content_sha256
            JOIN wang_knowledge.object_versions b ON b.collection=co.collection
              AND b.object_id=co.object_id AND b.revision=co.before_revision
              AND b.content_sha256=co.before_sha256
            WHERE re.collection='claims' AND re.object_id=%s""", (claim_id,))
        keys = ('event_id','reviewer_kind','reviewer_id','decision','reason','artifact',
                'change_set_id','status','source_kind','source_sha','fingerprint','metadata',
                'before','after','before_sha','after_sha','operation','operation_count')
        events = [dict(zip(keys, row)) for row in cursor.fetchall()]
        cursor.execute("SELECT count(*) FROM wang_knowledge.review_events WHERE collection='claims' AND object_id=%s AND reviewer_kind='human'", (claim_id,))
        if cursor.fetchone()[0]:
            return variants
    return applied_review_match(claim_id, variants, events)


def bound_review_input(review):
    """Locate archived bytes by the review's full SHA, never by chronology."""
    path = Path(review.get('source', {}).get('package_path', ''))
    sha = review.get('source', {}).get('package_sha256')
    if not path.is_absolute() or not sha:
        return None
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == sha:
        return path
    # Existing generations of this exact input only; no unrelated batch scan.
    found = [p for p in sorted((path.parent/'generations').glob(path.stem+'.*.json'))
             if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == sha]
    return found[0] if len(found) == 1 else None


def discover_review_inputs(*, claim_ids, inventory, store):
    """Find later reviewed input generations; never grant review approval.

    Search only batches already named by the frozen inventory. Same physical
    review copied into review-generations is one candidate, not two approvals.
    Actual history, review decision, source and evidence are verified by the
    unchanged historical bridge after discovery.
    """
    from copy import deepcopy
    result = deepcopy(inventory)
    live = {r['claim_id']: r for r in store.list_records('claims') if r['claim_id'] in claim_ids}
    for cid in sorted(claim_ids):
        if cid not in live:
            raise ValueError(f'{cid}: current Claim missing during review discovery')
        roots = {Path(v['path']).parent.parent / 'reviews' for v in inventory[cid]['variants']}
        found = {}
        for root in sorted(roots):
            for path in sorted(root.rglob('*.json')):
                review = json.loads(path.read_bytes())
                if not any(r.get('claim_id') == cid for r in review.get('claim_reviews', [])):
                    continue
                package_path = bound_review_input(review)
                if package_path is None:
                    continue
                raw_bytes = package_path.read_bytes()
                package_sha = hashlib.sha256(raw_bytes).hexdigest()
                if package_sha != review['source'].get('package_sha256'):
                    continue
                normalized, _ = _normalize_records(json.loads(raw_bytes))
                if cid not in normalized.get('claims', {}):
                    continue
                prior = _substantive_payload(ClaimRecord.model_validate(normalized['claims'][cid]).model_dump(mode='json'))
                current = _substantive_payload(ClaimRecord.model_validate(live[cid]).model_dump(mode='json'))
                differences = sorted(k for k in set(prior) | set(current) if prior.get(k) != current.get(k))
                if not set(differences) <= {'occurrences', 'evidence_step_ids'}:
                    continue
                review_sha = hashlib.sha256(path.read_bytes()).hexdigest()
                found.setdefault((review_sha, package_sha), {
                    'contract': 'review_input_discovery_requires_history_validation',
                    'changed_substantive_fields': differences,
                    'review_binding': {'path': str(path.resolve()), 'file_sha256': review_sha},
                    'review_input_binding': {'path': str(package_path.resolve()), 'file_sha256': package_sha},
                })
        if found:
            result[cid]['variants'] = discover_applied_review(cid, list(found.values()), store)
    return result


def build_lineage_inventory(*, manifest: dict[str, Any], reviewed_root: Path, store: Any) -> dict[str, Any]:
    validate_content_addressed(manifest, "artifact_sha256")
    wanted = {row["claim_id"] for row in manifest["claim_packets"]}
    current = {row["claim_id"]: row for row in store.list_records("claims") if row["claim_id"] in wanted}
    if set(current) != wanted:
        raise ValueError("frozen grouping Claim is absent from current database")
    indexed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    artifacts: list[dict[str, Any]] = []
    for path in sorted(reviewed_root.rglob("*.reviewed-candidate.json")):
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes)
        normalized, _ = _normalize_records(raw)
        selected = wanted & set(normalized.get("claims", {}))
        if not selected:
            continue
        try:
            validate_reviewed_candidate_artifact(raw)
            contract = "sealed_valid"
            detail = None
        except ValueError as exc:
            contract = "requires_historical_proof_adapter"
            detail = str(exc)
        artifact = {
            "path": str(path.resolve()), "file_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "contract": contract, "detail": detail,
        }
        artifacts.append(artifact)
        for claim_id in sorted(selected):
            prior = _substantive_payload(ClaimRecord.model_validate(normalized["claims"][claim_id]).model_dump(mode="json"))
            live = _substantive_payload(ClaimRecord.model_validate(current[claim_id]).model_dump(mode="json"))
            differences = sorted(key for key in set(prior) | set(live) if prior.get(key) != live.get(key))
            indexed[claim_id].append({**artifact, "changed_substantive_fields": differences})
    rows = []
    for claim_id in sorted(wanted):
        variants = indexed[claim_id]
        if not variants:
            disposition = "no_reviewed_candidate_in_claim_layer_root"
        elif any(not row["changed_substantive_fields"] and row["contract"] == "sealed_valid" for row in variants):
            disposition = "sealed_current_substance_match_requires_review_lineage_validation"
        elif any(not row["changed_substantive_fields"] for row in variants):
            disposition = "historical_current_substance_match_requires_proof_adapter"
        elif any(row["changed_substantive_fields"] == ["occurrences"] for row in variants):
            disposition = "occurrence_change_requires_applied_repair_proof"
        else:
            disposition = "other_change_requires_current_generation_lineage"
        rows.append({"claim_id": claim_id, "current_revision": current[claim_id]["revision"],
                     "current_review_status": current[claim_id].get("review_status"),
                     "disposition": disposition, "variants": variants})
    body = {
        "schema_version": "wang_cvp_review_lineage_inventory_v1",
        "grouping_manifest_sha256": manifest["artifact_sha256"], "claim_count": len(wanted),
        "artifact_count": len(artifacts), "artifacts": artifacts, "claims": rows,
        "disposition_counts": dict(sorted(Counter(row["disposition"] for row in rows).items())),
        "artifact_contract_counts": dict(sorted(Counter(row["contract"] for row in artifacts).items())),
        "eligibility_attestation_created": False, "master_data_mutations": 0, "model_calls": 0,
    }
    return body | {"artifact_sha256": sha256_json(body)}
