"""Validate reviewed scripture-use roles before passage scope selection.

``Claim.scripture_refs`` records which passages a Claim mentions.  It does not
say whether the professor is interpreting that passage.  This artifact supplies
that missing, reviewable occurrence-level judgment without changing Claim master
data.  Only an approved ``primary_passage`` occurrence may seed a passage scope.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.passage_knowledge_slice import reference_overlaps


SCHEMA_VERSION = "wang_passage_scope_attestation_v1"
PRIMARY_EXEGESIS_ROLE = "primary_passage"
ALLOWED_ROLES = frozenset(
    {
        PRIMARY_EXEGESIS_ROLE,
        "parallel_passage",
        "lexical_support",
        "historical_background",
        "theological_support",
        "counterexample",
        "application_basis",
        "unclassified",
    }
)
APPROVED_REVIEW_STATUSES = frozenset(
    {"approved", "human_approved", "system_approved", "ai_consensus_reviewed"}
)


def passage_units_sha256(passage_units: Mapping[str, Any]) -> str:
    """Return the stable identity of the exact requested passage windows."""

    return sha256_json(
        {key: [str(item) for item in value] for key, value in sorted(passage_units.items())}
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_passage_scope_attestation(
    payload: Mapping[str, Any],
    *,
    claims: Sequence[Mapping[str, Any]],
    claim_manifest_sha256: str,
    passage_units: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Return approved primary-exegesis admissions or fail closed.

    Every Claim reference that overlaps the requested passage must have exactly
    one reviewed occurrence row.  Missing or ``unclassified`` rows block the
    scope rather than being guessed from the sermon title or reference text.
    """

    _require(payload.get("schema_version") == SCHEMA_VERSION, "unsupported passage scope attestation schema")
    body = dict(payload)
    stated_sha = str(body.pop("artifact_sha256", ""))
    _require(bool(stated_sha) and stated_sha == sha256_json(body), "passage scope attestation SHA mismatch")
    _require(
        payload.get("claim_manifest_sha256") == claim_manifest_sha256,
        "passage scope attestation Claim manifest mismatch",
    )
    expected_units_sha = passage_units_sha256(passage_units)
    _require(
        payload.get("passage_units_sha256") == expected_units_sha,
        "passage scope attestation passage units mismatch",
    )

    claim_index = {str(row["claim_id"]): row for row in claims}
    seen: set[tuple[str, int]] = set()
    admissions: dict[str, list[dict[str, Any]]] = {}
    for raw in payload.get("references") or []:
        row = dict(raw)
        claim_id = str(row.get("claim_id") or "")
        _require(claim_id in claim_index, f"{claim_id or '<empty>'}: attestation Claim is outside the manifest")
        ref_index = int(row.get("source_ref_index", -1))
        key = (claim_id, ref_index)
        _require(key not in seen, f"{claim_id}: duplicate scripture-role row at index {ref_index}")
        seen.add(key)
        claim = claim_index[claim_id]
        refs = list(claim.get("scripture_refs") or [])
        _require(0 <= ref_index < len(refs), f"{claim_id}: scripture reference index is invalid")
        scripture_ref = str(refs[ref_index])
        _require(row.get("scripture_ref") == scripture_ref, f"{claim_id}: scripture reference text drift")
        _require(row.get("claim_revision") == claim.get("pinned_claim_revision"), f"{claim_id}: Claim revision drift")
        _require(row.get("claim_revision_sha256") == claim.get("claim_revision_sha256"), f"{claim_id}: Claim SHA drift")
        role = str(row.get("role") or "")
        review_status = str(row.get("review_status") or "")
        _require(role in ALLOWED_ROLES, f"{claim_id}: invalid scripture-use role")
        _require(review_status in APPROVED_REVIEW_STATUSES, f"{claim_id}: scripture-use role is not approved")
        _require(bool(str(row.get("role_reason") or "").strip()), f"{claim_id}: scripture-use role reason is required")
        overlapping_units = sorted(
            unit_id
            for unit_id, passages in passage_units.items()
            if any(reference_overlaps(scripture_ref, passage) for passage in passages)
        )
        _require(
            sorted(str(value) for value in row.get("passage_unit_ids") or []) == overlapping_units,
            f"{claim_id}: attested passage units disagree with the reference",
        )
        if overlapping_units and role == "unclassified":
            raise ValueError(f"{claim_id}: overlapping scripture reference is unclassified")
        if overlapping_units and role == PRIMARY_EXEGESIS_ROLE:
            admissions.setdefault(claim_id, []).append(
                {
                    "signal": "primary_scripture_exegesis",
                    "passage_unit_ids": overlapping_units,
                    "source_ref_index": ref_index,
                    "scripture_ref": scripture_ref,
                    "scripture_use_role": role,
                    "scripture_role_review_status": review_status,
                    "scripture_role_attestation_sha256": stated_sha,
                }
            )

    missing: list[str] = []
    for claim_id, claim in sorted(claim_index.items()):
        for ref_index, raw_ref in enumerate(claim.get("scripture_refs") or []):
            if any(
                reference_overlaps(str(raw_ref), passage)
                for passages in passage_units.values()
                for passage in passages
            ) and (claim_id, ref_index) not in seen:
                missing.append(f"{claim_id}[{ref_index}]")
    _require(not missing, "missing scripture-use roles for overlapping references: " + ", ".join(missing))
    return admissions
