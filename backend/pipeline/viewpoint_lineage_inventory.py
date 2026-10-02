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
