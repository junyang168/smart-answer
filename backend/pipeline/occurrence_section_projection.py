"""Compile exact Claim occurrence-to-passage-section admissions.

The projection is deliberately mechanical.  A section acquires a Matthew 16
passage-unit label only when a pinned Claim in that same source section cites
the unit directly.  Other Claims may inherit that label through their exact
EvidenceStep -> SourceFragment -> paragraph -> section path.  Titles and text
similarity are never used as evidence.
"""

from __future__ import annotations

from collections import defaultdict
import re
from typing import Any, Iterable, Mapping, Sequence

from backend.api.canonical_repository.knowledge_models import (
    ClaimRecord,
    EvidenceStepRecord,
    KnowledgeSourceDocument,
    SourceFragmentRecord,
    evidence_fragment_ids,
)
from backend.api.canonical_repository.viewpoint_foundation import (
    semantic_record_sha,
    sha256_json,
)
from backend.pipeline.viewpoint_scope_selection import direct_seed_units


SCHEMA_VERSION = "wang_occurrence_section_projection_v1"
_PARAGRAPH_KEY = re.compile(r"S(\d+)")


def claim_universe_sha256(claims: Iterable[Mapping[str, Any]]) -> str:
    """Bind only the immutable Claim/source pins that define the denominator."""

    return sha256_json(
        sorted(
            (
                {
                    "claim_id": str(row["claim_id"]),
                    "pinned_claim_revision": int(row["pinned_claim_revision"]),
                    "claim_revision_sha256": str(row["claim_revision_sha256"]),
                    "source_id": str(row["source_id"]),
                }
                for row in claims
            ),
            key=lambda row: row["claim_id"],
        )
    )


def claim_identity_universe_sha256(claims: Iterable[Mapping[str, Any]]) -> str:
    """Bind the stable Claim/source denominator independently of revision pins."""

    return sha256_json(
        sorted(
            (
                {
                    "claim_id": str(row["claim_id"]),
                    "source_id": str(row["source_id"]),
                }
                for row in claims
            ),
            key=lambda row: row["claim_id"],
        )
    )


def verify_projection_artifact(payload: Mapping[str, Any]) -> None:
    """Fail closed if a projection is unsigned or internally inconsistent."""

    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported occurrence projection schema")
    stated = str(payload.get("artifact_sha256") or "")
    unsigned = {key: value for key, value in payload.items() if key != "artifact_sha256"}
    if not stated or stated != sha256_json(unsigned):
        raise ValueError("occurrence projection artifact SHA mismatch")
    manifest = dict(payload.get("current_claim_manifest") or {})
    manifest_sha = str(manifest.pop("manifest_sha256", ""))
    if not manifest_sha or manifest_sha != sha256_json(manifest):
        raise ValueError("occurrence projection current Claim manifest SHA mismatch")


def projection_admissions_by_claim(
    payload: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Return the selector input after verifying the projection envelope."""

    verify_projection_artifact(payload)
    result: dict[str, list[dict[str, Any]]] = {}
    for row in payload.get("claims") or []:
        admissions = [dict(item) for item in row.get("admissions") or []]
        if admissions:
            result[str(row["claim_id"])] = admissions
    return result


def projection_status_by_claim(payload: Mapping[str, Any]) -> dict[str, str]:
    verify_projection_artifact(payload)
    return {
        str(row["claim_id"]): str(row["projection_status"])
        for row in payload.get("claims") or []
    }


def _normalized_plan(raw: Mapping[str, Any]) -> dict[str, Any]:
    source_sha = str(raw.get("source_sha256") or "")
    if not source_sha:
        raise ValueError("section plan lacks source_sha256")
    sections: list[dict[str, Any]] = []
    previous_end = 0
    for raw_section in raw.get("sections") or []:
        section = {
            "index": int(raw_section["index"]),
            "start": int(raw_section["start"]),
            "end": int(raw_section["end"]),
            "title": str(raw_section.get("title") or ""),
        }
        if section["start"] != previous_end or section["end"] <= section["start"]:
            raise ValueError(f"{source_sha}: section plan is not contiguous")
        previous_end = section["end"]
        sections.append(section)
    if not sections:
        raise ValueError(f"{source_sha}: section plan has no sections")
    return {
        "source_sha256": source_sha,
        "origin": str(raw.get("origin") or "unknown"),
        "sections": sections,
    }


def normalize_section_plans(
    plans: Sequence[tuple[str, Mapping[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Collapse byte-location duplicates, rejecting semantic ambiguity."""

    grouped: dict[str, dict[str, tuple[dict[str, Any], list[str]]]] = defaultdict(dict)
    for locator, raw in plans:
        normalized = _normalized_plan(raw)
        plan_sha = sha256_json(normalized)
        source_sha = normalized["source_sha256"]
        if plan_sha not in grouped[source_sha]:
            grouped[source_sha][plan_sha] = (normalized, [])
        grouped[source_sha][plan_sha][1].append(str(locator))

    resolved: dict[str, dict[str, Any]] = {}
    inventory: list[dict[str, Any]] = []
    for source_sha, variants in sorted(grouped.items()):
        if len(variants) != 1:
            raise ValueError(f"{source_sha}: ambiguous section plans")
        plan_sha, (plan, locators) = next(iter(variants.items()))
        resolved[source_sha] = plan | {"section_plan_sha256": plan_sha}
        inventory.append(
            {
                "source_sha256": source_sha,
                "section_plan_sha256": plan_sha,
                "origin": plan["origin"],
                "section_count": len(plan["sections"]),
                "duplicate_locator_count": len(locators),
                "locators": sorted(locators),
            }
        )
    return resolved, inventory


def _section_for_paragraph(plan: Mapping[str, Any], paragraph_key: Any) -> dict[str, Any] | None:
    match = _PARAGRAPH_KEY.fullmatch(str(paragraph_key or ""))
    if not match:
        return None
    position = int(match.group(1)) - 1
    return next(
        (
            dict(section)
            for section in plan["sections"]
            if int(section["start"]) <= position < int(section["end"])
        ),
        None,
    )


def build_occurrence_section_projection(
    *,
    scope_claims: Sequence[Mapping[str, Any]],
    scope_artifact_sha256: str,
    parent_claim_manifest_sha256: str,
    passage_units: Mapping[str, Any],
    claims: Sequence[Mapping[str, Any]],
    evidence_steps: Sequence[Mapping[str, Any]],
    source_fragments: Sequence[Mapping[str, Any]],
    source_documents: Sequence[Mapping[str, Any]],
    section_plans: Sequence[tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Build one SHA-bound, zero-model occurrence projection."""

    if not scope_claims:
        raise ValueError("scope artifact has no Claim universe")
    if len({str(row["claim_id"]) for row in scope_claims}) != len(scope_claims):
        raise ValueError("scope artifact contains duplicate Claim ids")

    current_claims = {
        row.claim_id: row for row in (ClaimRecord.model_validate(item) for item in claims)
    }
    step_index = {
        row.evidence_step_id: row
        for row in (EvidenceStepRecord.model_validate(item) for item in evidence_steps)
    }
    fragment_index = {
        row.fragment_id: row
        for row in (SourceFragmentRecord.model_validate(item) for item in source_fragments)
    }
    document_index = {
        row.source_id: row
        for row in (KnowledgeSourceDocument.model_validate(item) for item in source_documents)
    }
    scoped_source_ids = {str(row["source_id"]) for row in scope_claims}
    scoped_source_shas = {
        str(document_index[source_id].source_sha256)
        for source_id in scoped_source_ids
        if source_id in document_index and document_index[source_id].source_sha256
    }
    plans_by_source_sha, plan_inventory = normalize_section_plans(
        [
            (locator, raw)
            for locator, raw in section_plans
            if str(raw.get("source_sha256") or "") in scoped_source_shas
        ]
    )

    paths_by_claim: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reasons_by_claim: dict[str, set[str]] = defaultdict(set)
    current_pins: list[dict[str, Any]] = []
    parent_pin_stale: set[str] = set()
    for scoped in sorted(scope_claims, key=lambda row: str(row["claim_id"])):
        claim_id = str(scoped["claim_id"])
        source_id = str(scoped["source_id"])
        claim = current_claims.get(claim_id)
        if claim is None:
            reasons_by_claim[claim_id].add("current_claim_missing")
            continue
        current_sha = semantic_record_sha(claim)
        current_pins.append(
            {
                "claim_id": claim_id,
                "pinned_claim_revision": claim.revision,
                "claim_revision_sha256": current_sha,
                "source_id": source_id,
            }
        )
        if (
            claim.revision != int(scoped["pinned_claim_revision"])
            or current_sha != str(scoped["claim_revision_sha256"])
        ):
            parent_pin_stale.add(claim_id)
        document = document_index.get(source_id)
        if document is None:
            reasons_by_claim[claim_id].add("source_document_missing")
            continue
        if not document.source_sha256:
            reasons_by_claim[claim_id].add("source_sha_missing")
            continue
        plan = plans_by_source_sha.get(document.source_sha256)
        if plan is None:
            reasons_by_claim[claim_id].add("section_plan_missing")
            continue
        if not claim.evidence_step_ids:
            reasons_by_claim[claim_id].add("claim_has_no_evidence_steps")
            continue
        for evidence_step_id in claim.evidence_step_ids:
            step = step_index.get(evidence_step_id)
            if step is None:
                reasons_by_claim[claim_id].add("evidence_step_missing")
                continue
            fragment_ids = evidence_fragment_ids(step)
            if not fragment_ids:
                reasons_by_claim[claim_id].add("evidence_step_has_no_source_fragment")
                continue
            for fragment_id in fragment_ids:
                fragment = fragment_index.get(fragment_id)
                if fragment is None:
                    reasons_by_claim[claim_id].add("source_fragment_missing")
                    continue
                if fragment.source_id != source_id:
                    reasons_by_claim[claim_id].add("fragment_source_mismatch")
                    continue
                if fragment.source_sha256 != document.source_sha256:
                    reasons_by_claim[claim_id].add("fragment_source_sha_mismatch")
                    continue
                if fragment.anchor_state != "source_version_bound":
                    reasons_by_claim[claim_id].add("fragment_anchor_not_version_bound")
                    continue
                section = _section_for_paragraph(plan, fragment.paragraph_key)
                if section is None:
                    reason = (
                        "paragraph_key_invalid"
                        if not _PARAGRAPH_KEY.fullmatch(str(fragment.paragraph_key or ""))
                        else "paragraph_outside_section_plan"
                    )
                    reasons_by_claim[claim_id].add(reason)
                    continue
                paths_by_claim[claim_id].append(
                    {
                        "source_id": source_id,
                        "source_sha256": document.source_sha256,
                        "claim_revision": claim.revision,
                        "claim_revision_sha256": current_sha,
                        "evidence_step_id": evidence_step_id,
                        "source_fragment_id": fragment_id,
                        "paragraph_key": str(fragment.paragraph_key),
                        "section_index": section["index"],
                        "section_title": section["title"],
                        "section_plan_sha256": plan["section_plan_sha256"],
                        "section_plan_origin": plan["origin"],
                    }
                )

    # The parent scope freezes identity/source membership, but its Claim pins
    # may be old.  Section labels must come from the same current revisions
    # recorded in this projection's Claim manifest.
    direct_units = direct_seed_units(
        [
            current_claims[str(scoped["claim_id"])].model_dump(mode="json")
            | {"source_id": str(scoped["source_id"])}
            for scoped in scope_claims
            if str(scoped["claim_id"]) in current_claims
        ],
        passage_units,
    )
    section_units: dict[tuple[str, int], set[str]] = defaultdict(set)
    section_seed_claims: dict[tuple[str, int], set[str]] = defaultdict(set)
    for claim_id, units in direct_units.items():
        for path in paths_by_claim.get(claim_id, []):
            key = (path["source_id"], path["section_index"])
            section_units[key].update(units)
            section_seed_claims[key].add(claim_id)

    claim_rows: list[dict[str, Any]] = []
    for scoped in sorted(scope_claims, key=lambda row: str(row["claim_id"])):
        claim_id = str(scoped["claim_id"])
        admissions: list[dict[str, Any]] = []
        for path in paths_by_claim.get(claim_id, []):
            key = (path["source_id"], path["section_index"])
            for unit_id in sorted(section_units.get(key, set())):
                admissions.append(
                    path
                    | {
                        "passage_unit_ids": [unit_id],
                        "section_seed_claim_ids": sorted(section_seed_claims[key]),
                    }
                )
        admissions.sort(
            key=lambda row: (
                row["passage_unit_ids"],
                row["source_id"],
                row["section_index"],
                row["evidence_step_id"],
                row["source_fragment_id"],
            )
        )
        reasons = sorted(reasons_by_claim.get(claim_id, set()))
        if admissions:
            status = "proved_by_occurrence_section"
        elif reasons:
            status = "pending_missing_projection_input"
        else:
            status = "not_proved_by_occurrence_section"
        claim_rows.append(
            {
                "claim_id": claim_id,
                "current_claim_revision": (
                    current_claims[claim_id].revision if claim_id in current_claims else None
                ),
                "current_claim_revision_sha256": (
                    semantic_record_sha(current_claims[claim_id])
                    if claim_id in current_claims
                    else None
                ),
                "parent_scope_pin_status": (
                    "stale" if claim_id in parent_pin_stale else "current"
                ),
                "projection_status": status,
                "validated_occurrence_count": len(paths_by_claim.get(claim_id, [])),
                "reason_codes": reasons,
                "admissions": admissions,
            }
        )

    section_labels = [
        {
            "source_id": source_id,
            "section_index": section_index,
            "passage_unit_ids": sorted(units),
            "direct_seed_claim_ids": sorted(section_seed_claims[(source_id, section_index)]),
        }
        for (source_id, section_index), units in sorted(section_units.items())
    ]
    current_claim_manifest: dict[str, Any] = {
        "schema_version": "wang_occurrence_section_claim_manifest_v1",
        "parent_claim_manifest_sha256": parent_claim_manifest_sha256,
        "claims": current_pins,
    }
    current_claim_manifest["manifest_sha256"] = sha256_json(
        current_claim_manifest
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "scope_artifact_sha256": scope_artifact_sha256,
        "parent_scope_claim_universe_sha256": claim_universe_sha256(scope_claims),
        "claim_identity_universe_sha256": claim_identity_universe_sha256(scope_claims),
        "current_claim_manifest": current_claim_manifest,
        "claim_universe_sha256": claim_universe_sha256(current_pins),
        "policy": {
            "section_label_authority": "direct_scripture_ref_claims_only",
            "inheritance_path": "claim_evidence_step_source_fragment_paragraph_section",
            "section_interval": "zero_based_half_open",
            "title_semantics_used": False,
            "text_similarity_used": False,
        },
        "section_plan_inventory": plan_inventory,
        "section_labels": section_labels,
        "claims": claim_rows,
        "statistics": {
            "claim_total": len(claim_rows),
            "claim_with_validated_occurrence_total": sum(
                bool(row["validated_occurrence_count"]) for row in claim_rows
            ),
            "labeled_section_total": len(section_labels),
            "proved_by_occurrence_section_total": sum(
                row["projection_status"] == "proved_by_occurrence_section"
                for row in claim_rows
            ),
            "pending_missing_projection_input_total": sum(
                row["projection_status"] == "pending_missing_projection_input"
                for row in claim_rows
            ),
            "parent_scope_stale_claim_pin_total": len(parent_pin_stale),
        },
        "model_calls_executed": 0,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    payload["artifact_sha256"] = sha256_json(payload)
    return payload
