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
from backend.api.canonical_repository.postgres_store import _normalize_records, _substantive_payload
from backend.api.canonical_repository.reviewed_candidate_contract import validate_reviewed_candidate_artifact
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import validate_content_addressed


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
                package_path = Path(review.get('source', {}).get('package_path', ''))
                if not package_path.is_absolute() or not package_path.is_file():
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
            result[cid]['variants'] = list(found.values())
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
