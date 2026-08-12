"""Structured scripture-reference enrichment for Wang corpus surveys.

The first-pass survey deliberately stores scripture references as lightweight
strings.  This module adds a second, reviewable layer without mutating those
v1 artifacts: each occurrence is expanded, normalized to OSIS when possible,
and assigned a discourse role by the model.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from backend.api.sermon_search.bible_refs import extract_refs, normalize_ref


ENRICHMENT_VERSION = "wang_corpus_scripture_roles_v2"
ALLOWED_ROLES = {
    "primary_passage",
    "parallel_passage",
    "lexical_support",
    "historical_background",
    "theological_support",
    "counterexample",
    "application_basis",
    "unclassified",
}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}


class ScriptureEnrichmentValidationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ScriptureEnrichmentValidationError(message)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _display_for_ref(ref: Any) -> str:
    start = f"{ref.book_zh} {ref.chapter_start}"
    if ref.verse_start is not None:
        start += f":{ref.verse_start}"
    if ref.chapter_end is not None or ref.verse_end is not None:
        end_chapter = ref.chapter_end or ref.chapter_start
        if end_chapter != ref.chapter_start:
            start += f"–{end_chapter}"
            if ref.verse_end is not None:
                start += f":{ref.verse_end}"
        elif ref.verse_end is not None:
            start += f"–{ref.verse_end}"
    return start


def _expand_one(source_raw_text: str) -> list[dict[str, Any]]:
    """Expand one survey string into explicit canonical references.

    Multiple explicit books/ranges are split.  An unresolved value is kept as
    a candidate record instead of being silently dropped.
    """
    refs = extract_refs(source_raw_text)
    if not refs:
        single = normalize_ref(source_raw_text)
        refs = [single] if single is not None else []
    if not refs:
        return [
            {
                "raw_text": source_raw_text.strip(),
                "osis": None,
                "display": source_raw_text.strip(),
                "normalization_status": "unresolved",
            }
        ]
    return [
        {
            "raw_text": ref.raw,
            "osis": ref.osis,
            "display": _display_for_ref(ref),
            "normalization_status": "normalized",
        }
        for ref in refs
    ]


def build_reference_inventory(survey: dict[str, Any]) -> list[dict[str, Any]]:
    """Build stable occurrence records from a validated v1 survey."""
    inventory: list[dict[str, Any]] = []
    owner_collections = (
        ("cluster", survey.get("content_clusters") or [], "cluster_id"),
        ("claim", survey.get("candidate_claims") or [], "claim_id"),
    )
    for owner_kind, owners, id_field in owner_collections:
        for owner in owners:
            owner_id = owner.get(id_field)
            for source_index, source_raw in enumerate(owner.get("scripture_refs") or []):
                if not isinstance(source_raw, str) or not source_raw.strip():
                    continue
                expanded = _expand_one(source_raw)
                for ref_index, ref in enumerate(expanded):
                    ref_key = f"{owner_kind}:{owner_id}:{source_index}:{ref_index}"
                    inventory.append(
                        {
                            "ref_key": ref_key,
                            "owner_kind": owner_kind,
                            "owner_id": owner_id,
                            "source_ref_index": source_index,
                            "expanded_ref_index": ref_index,
                            "source_raw_text": source_raw,
                            **ref,
                        }
                    )
    return inventory


def classification_context(survey: dict[str, Any], inventory: list[dict[str, Any]]) -> dict[str, Any]:
    """Return compact evidence context for role classification."""
    cluster_by_id = {item["cluster_id"]: item for item in survey.get("content_clusters") or []}
    claim_by_id = {item["claim_id"]: item for item in survey.get("candidate_claims") or []}
    rows: list[dict[str, Any]] = []
    for item in inventory:
        if item["owner_kind"] == "cluster":
            owner = cluster_by_id[item["owner_id"]]
            context = {
                "title": owner.get("title"),
                "function": owner.get("function"),
                "summary": owner.get("summary"),
            }
        else:
            owner = claim_by_id[item["owner_id"]]
            context = {
                "statement": owner.get("statement"),
                "claim_kind": owner.get("claim_kind"),
                "attribution": owner.get("attribution"),
                "anchor_excerpts": [a.get("verbatim_excerpt") for a in owner.get("anchors") or []],
            }
        rows.append(
            {
                "ref_key": item["ref_key"],
                "raw_text": item["raw_text"],
                "osis": item["osis"],
                "owner_kind": item["owner_kind"],
                "owner_id": item["owner_id"],
                "context": context,
            }
        )
    return {"references": rows}


def make_enrichment(
    survey: dict[str, Any],
    survey_path: Path,
    inventory: list[dict[str, Any]],
    response: dict[str, Any],
    *,
    model: str,
    reasoning_effort: str,
) -> dict[str, Any]:
    classifications = {item["ref_key"]: item for item in response.get("classifications") or []}
    refs: list[dict[str, Any]] = []
    for item in inventory:
        classification = classifications.get(item["ref_key"], {})
        refs.append(
            {
                **item,
                "role": classification.get("role", "unclassified"),
                "role_reason": classification.get("role_reason", "模型未返回分类。"),
                "confidence": classification.get("confidence", "low"),
                "attribution": "professor_used",
                "review_status": "candidate",
            }
        )
    role_counts = {role: sum(ref["role"] == role for ref in refs) for role in sorted(ALLOWED_ROLES)}
    return {
        "scripture_enrichment_version": ENRICHMENT_VERSION,
        "source": {
            "transcript_id": survey.get("source", {}).get("transcript_id"),
            "transcript_sha256": survey.get("source", {}).get("sha256"),
            "survey_path": str(survey_path),
            "survey_sha256": file_sha256(survey_path),
        },
        "generation": {"model": model, "reasoning_effort": reasoning_effort},
        "references": refs,
        "summary": {
            "reference_occurrence_count": len(refs),
            "normalized_count": sum(ref["normalization_status"] == "normalized" for ref in refs),
            "unresolved_count": sum(ref["normalization_status"] == "unresolved" for ref in refs),
            "role_counts": role_counts,
            "review_status": "candidate",
        },
    }


def validate_enrichment(
    enrichment: dict[str, Any],
    survey: dict[str, Any],
    survey_path: Path,
) -> None:
    _require(enrichment.get("scripture_enrichment_version") == ENRICHMENT_VERSION, "unsupported enrichment version")
    source = enrichment.get("source") or {}
    _require(source.get("transcript_id") == survey.get("source", {}).get("transcript_id"), "transcript_id mismatch")
    _require(source.get("transcript_sha256") == survey.get("source", {}).get("sha256"), "transcript SHA mismatch")
    _require(source.get("survey_sha256") == file_sha256(survey_path), "survey SHA mismatch")

    expected = build_reference_inventory(survey)
    expected_by_key = {item["ref_key"]: item for item in expected}
    refs = enrichment.get("references") or []
    keys = [item.get("ref_key") for item in refs]
    _require(len(keys) == len(set(keys)), "duplicate ref_key")
    _require(set(keys) == set(expected_by_key), "reference coverage mismatch")
    for item in refs:
        key = item.get("ref_key")
        original = expected_by_key[key]
        for field in (
            "owner_kind", "owner_id", "source_ref_index", "expanded_ref_index",
            "source_raw_text", "raw_text", "osis", "normalization_status",
        ):
            _require(item.get(field) == original.get(field), f"{key}: derived field changed: {field}")
        _require(item.get("role") in ALLOWED_ROLES, f"{key}: invalid role")
        _require(item.get("confidence") in ALLOWED_CONFIDENCE, f"{key}: invalid confidence")
        _require(item.get("attribution") == "professor_used", f"{key}: invalid attribution")
        _require(item.get("review_status") == "candidate", f"{key}: review_status must be candidate")
        _require(bool(item.get("role_reason")), f"{key}: role_reason is required")

    summary = enrichment.get("summary") or {}
    _require(summary.get("reference_occurrence_count") == len(refs), "reference count mismatch")
    _require(summary.get("normalized_count") == sum(x["normalization_status"] == "normalized" for x in refs), "normalized count mismatch")
    _require(summary.get("unresolved_count") == sum(x["normalization_status"] == "unresolved" for x in refs), "unresolved count mismatch")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ScriptureEnrichmentValidationError(f"{path}: JSON must be an object")
    return value
