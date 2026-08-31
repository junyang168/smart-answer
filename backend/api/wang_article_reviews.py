"""Read-only, SHA-bound previews of unpublished Wang topic essays.

These records live in staging and are deliberately separate from the public
Wang repository.  A preview manifest makes one draft visible to authenticated
admin readers; it never supplies a publication decision and can therefore
never make the public article endpoint accept the manuscript.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from opencc import OpenCC

from backend.api.config import DATA_BASE_PATH, WANG_STAGING_DIR
from backend.api.canonical_repository.service import CanonicalRepositoryService
from backend.pipeline.excerpt_audio_alignment import (
    align_transcript_excerpt,
    project_excerpt_timings,
)


router = APIRouter(prefix="/admin/wang/article-reviews", tags=["wang-admin"])

MANIFEST_SCHEMA = "wang_topic_essay_review_preview.v1"
RESPONSE_SCHEMA = "wang_topic_essay_review_read_model.v1"
REVIEW_MANIFEST_ROOT = WANG_STAGING_DIR / "topic-essay-reviews"
PROVENANCE_COMMENT_RE = re.compile(r"<!--\s*provenance:\s*(\{.*?\})\s*-->", re.S)
FOOTNOTE_DEFINITION_RE = re.compile(r"^\[\^[^\]]+\]:")
SOURCE_MARKER_PREFIX = "#review-source-evidence-"
_TO_SIMPLIFIED = OpenCC("t2s")
_LEGACY_EVIDENCE_OVERLAP_MINIMUM = 0.08


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_staging_child(relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise ValueError("review artifact path must be relative to Wang staging")
    root = WANG_STAGING_DIR.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("review artifact path leaves Wang staging") from exc
    return candidate


def _reader_markdown(markdown: str) -> str:
    """Hide provenance comments without rewriting a byte of reader prose."""

    return re.sub(r"<!--\s*provenance:\s*[\s\S]*?-->\s*", "", markdown).strip()


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("result") or payload
    return dict(value) if isinstance(value, dict) else {}


def _fragment_ids(step: dict[str, Any]) -> list[str]:
    values = step.get("source_fragment_ids") or []
    result = [str(value) for value in values if value]
    single = str(step.get("source_fragment_id") or "").strip()
    if single:
        result.append(single)
    return list(dict.fromkeys(result))


def _safe_resource_url(value: Any) -> str | None:
    url = str(value or "").strip()
    if url.startswith("/resources/") and not url.startswith("//"):
        return url
    return None


def _source_fragment_read_model(
    fragment: dict[str, Any],
    source: dict[str, Any],
    sermon_cache: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    excerpt = str(fragment.get("verbatim_excerpt") or "").strip()
    fragment_id = str(fragment.get("fragment_id") or "").strip()
    source_type = str(source.get("source_type") or "").strip()
    if not excerpt or not fragment_id or source_type not in {"sermon_transcript", "notes_manuscript"}:
        return None
    title = str(source.get("title") or source.get("transcript_id") or "来源材料").strip()
    result: dict[str, Any] = {
        "fragment_ids": [fragment_id],
        "source_type": source_type,
        "title": title,
        "excerpts": [excerpt],
        "full_source_url": None,
        "media": None,
    }
    if source_type == "notes_manuscript":
        result["full_source_url"] = _safe_resource_url(source.get("source_url"))
        return result

    transcript_id = str(source.get("transcript_id") or "").strip()
    if not transcript_id:
        return None
    sermon = sermon_cache.get(transcript_id)
    if sermon is None:
        catalog = CanonicalRepositoryService._sermon_catalog_record(transcript_id)
        media = CanonicalRepositoryService._sermon_media(transcript_id, {}, catalog)
        sermon = {
            "full_source_url": f"/resources/sermons/{quote(transcript_id, safe='')}",
            "media": media.model_dump(mode="json"),
        }
        sermon_cache[transcript_id] = sermon
    timing = fragment.get("excerpt_timing") or {}
    excerpt_start = fragment.get("excerpt_media_time")
    excerpt_end = fragment.get("excerpt_media_end_time")
    has_excerpt_timing = isinstance(excerpt_start, (int, float)) and isinstance(
        excerpt_end, (int, float)
    )
    paragraph_start = fragment.get("media_time")
    paragraph_end = fragment.get("media_end_time")
    start_seconds = (
        max(0.0, float(excerpt_start) - 2.0)
        if has_excerpt_timing
        else paragraph_start
    )
    end_seconds = float(excerpt_end) + 2.0 if has_excerpt_timing else paragraph_end
    result["full_source_url"] = sermon["full_source_url"]
    result["media"] = {
        **sermon["media"],
        "fragment_ids": [fragment_id],
        "start_seconds": float(start_seconds) if isinstance(start_seconds, (int, float)) else None,
        "end_seconds": float(end_seconds) if isinstance(end_seconds, (int, float)) else None,
        "excerpt_start_seconds": float(excerpt_start) if has_excerpt_timing else None,
        "excerpt_end_seconds": float(excerpt_end) if has_excerpt_timing else None,
        "paragraph_start_seconds": (
            float(paragraph_start) if isinstance(paragraph_start, (int, float)) else None
        ),
        "paragraph_end_seconds": (
            float(paragraph_end) if isinstance(paragraph_end, (int, float)) else None
        ),
        "timing_status": str(timing.get("status") or "paragraph_fallback"),
        "timing_method": str(timing.get("method") or "paragraph_start"),
        "timing_match_ratio": (
            float(timing["match_ratio"])
            if isinstance(timing.get("match_ratio"), (int, float))
            else None
        ),
        "reviewed_text_differs_from_raw": timing.get("reviewed_text_differs_from_raw"),
        "lineage_window_expanded": bool(timing.get("lineage_window_expanded")),
        "timing_alignment_sha256": timing.get("alignment_sha256"),
    }
    return result


def _claim_sources(
    provenance: dict[str, Any],
    knowledge: dict[str, Any],
    sermon_cache: dict[str, dict[str, Any]],
    *,
    paragraph_markdown: str = "",
    evidence_step_ids: set[str] | None = None,
    exclude_fragment_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    claims = {str(item.get("claim_id") or ""): item for item in knowledge.get("claims", [])}
    steps = {
        str(item.get("evidence_step_id") or ""): item
        for item in knowledge.get("evidence_steps", [])
    }
    fragments = {
        str(item.get("fragment_id") or ""): item
        for item in knowledge.get("source_fragments", [])
    }
    documents = {
        str(item.get("source_id") or ""): item
        for item in knowledge.get("source_documents", [])
    }
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    by_fragment_id: dict[str, dict[str, Any]] = {}
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    excluded = exclude_fragment_ids or set()
    for claim_id in provenance.get("claim_ids") or []:
        claim = claims.get(str(claim_id))
        if not claim:
            continue
        for step_id in claim.get("evidence_step_ids") or []:
            if evidence_step_ids is not None and str(step_id) not in evidence_step_ids:
                continue
            step = steps.get(str(step_id))
            if not step:
                continue
            for fragment_id in _fragment_ids(step):
                if fragment_id in excluded:
                    continue
                if fragment_id in seen:
                    existing_fragment = by_fragment_id.get(fragment_id)
                    if existing_fragment:
                        if str(claim_id) not in existing_fragment["claim_ids"]:
                            existing_fragment["claim_ids"].append(str(claim_id))
                        if str(step_id) not in existing_fragment["evidence_step_ids"]:
                            existing_fragment["evidence_step_ids"].append(str(step_id))
                    continue
                fragment = fragments.get(fragment_id)
                if not fragment:
                    continue
                source = documents.get(str(fragment.get("source_id") or ""))
                if not source:
                    continue
                item = _source_fragment_read_model(fragment, source, sermon_cache)
                if item:
                    item["mapping_kind"] = "claim_evidence"
                    item["claim_ids"] = [str(claim_id)]
                    item["evidence_step_ids"] = [str(step_id)]
                    item["route_revision_id"] = None
                    item["route_label"] = None
                    item["route_steps"] = []
                    seen.add(fragment_id)
                    group_key = (
                        fragment.get("source_id"),
                        fragment.get("media_time"),
                        fragment.get("media_end_time"),
                        fragment.get("paragraph_key"),
                    )
                    existing = grouped.get(group_key)
                    if existing:
                        if str(claim_id) not in existing["claim_ids"]:
                            existing["claim_ids"].append(str(claim_id))
                        if str(step_id) not in existing["evidence_step_ids"]:
                            existing["evidence_step_ids"].append(str(step_id))
                        existing["fragment_ids"].append(fragment_id)
                        for excerpt in item["excerpts"]:
                            if excerpt not in existing["excerpts"]:
                                existing["excerpts"].append(excerpt)
                        by_fragment_id[fragment_id] = existing
                    else:
                        grouped[group_key] = item
                        result.append(item)
                        by_fragment_id[fragment_id] = item
    quoted = _quoted_paragraph_text(paragraph_markdown)
    if quoted:
        matching: list[dict[str, Any]] = []
        for item in result:
            matching_fragment_ids = [
                fragment_id
                for fragment_id in item["fragment_ids"]
                if _contains_normalized_quote(
                    str((fragments.get(fragment_id) or {}).get("verbatim_excerpt") or ""),
                    quoted,
                )
            ]
            if not matching_fragment_ids:
                continue
            selected = dict(item)
            selected["fragment_ids"] = matching_fragment_ids
            selected["excerpts"] = list(
                dict.fromkeys(
                    str(fragments[fragment_id].get("verbatim_excerpt") or "").strip()
                    for fragment_id in matching_fragment_ids
                    if str(fragments[fragment_id].get("verbatim_excerpt") or "").strip()
                )
            )
            matching.append(selected)
        if matching:
            return matching
    return result


def _normalized_quote_text(value: str) -> str:
    return re.sub(
        r"[\W_]+", "", _TO_SIMPLIFIED.convert(value), flags=re.UNICODE
    ).casefold()


def _quoted_paragraph_text(paragraph_markdown: str) -> str | None:
    """Return the text of a substantive Markdown block quote, if present.

    A quotation paragraph is a source-local statement, not an invitation to
    display every EvidenceStep owned by its Claim.  Exact quote selection keeps
    the Claim fallback honest while route-bearing argument paragraphs continue
    to resolve through ArgumentRoute attestations.
    """

    lines = [
        re.sub(r"^\s*>\s?", "", line).strip()
        for line in paragraph_markdown.splitlines()
        if re.match(r"^\s*>", line)
    ]
    value = " ".join(line for line in lines if line).strip()
    return value if len(_normalized_quote_text(value)) >= 6 else None


def _contains_normalized_quote(excerpt: str, quoted: str) -> bool:
    excerpt_text = _normalized_quote_text(excerpt)
    quoted_text = _normalized_quote_text(quoted)
    return bool(quoted_text) and (
        quoted_text in excerpt_text or excerpt_text in quoted_text
    )


def _semantic_tokens(value: str) -> set[str]:
    simplified = _TO_SIMPLIFIED.convert(value).casefold()
    chinese = "".join(re.findall(r"[\u3400-\u9fff]", simplified))
    return {
        chinese[index : index + 2]
        for index in range(max(0, len(chinese) - 1))
    } | set(re.findall(r"[a-z0-9]{2,}", simplified))


def _semantic_overlap(left: str, right: str) -> float:
    left_tokens = _semantic_tokens(left)
    right_tokens = _semantic_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / math.sqrt(
        len(left_tokens) * len(right_tokens)
    )


def _semantic_overlap_count(left: str, right: str) -> int:
    return len(_semantic_tokens(left) & _semantic_tokens(right))


def _selected_supplemental_step_ids(
    provenance: dict[str, Any],
    knowledge: dict[str, Any],
    paragraph_markdown: str,
    represented_step_ids: set[str],
    represented_claim_ids: set[str],
) -> set[str]:
    """Select Claim evidence that the route projection did not carry.

    New manuscripts declare their exact EvidenceSteps in provenance.  Older
    manuscripts cannot be rewritten without invalidating their publication
    SHA, so their compatibility path ranks only the unrepresented steps by
    overlap with the paragraph.  At least one step is kept for every cited
    Claim that no route attestation represents; this prevents a compound
    paragraph from silently losing one of its premises.
    """

    claims = {
        str(item.get("claim_id") or ""): item
        for item in knowledge.get("claims", [])
    }
    steps = {
        str(item.get("evidence_step_id") or ""): item
        for item in knowledge.get("evidence_steps", [])
    }
    fragments = {
        str(item.get("fragment_id") or ""): item
        for item in knowledge.get("source_fragments", [])
    }
    explicit = {
        str(value) for value in provenance.get("evidence_step_ids") or []
    }
    if explicit:
        return explicit - represented_step_ids

    selected: set[str] = set()
    footnote = bool(
        paragraph_markdown.splitlines()
        and FOOTNOTE_DEFINITION_RE.match(paragraph_markdown.splitlines()[0].strip())
    )
    for claim_id_value in provenance.get("claim_ids") or []:
        claim_id = str(claim_id_value)
        claim = claims.get(claim_id) or {}
        ranked: list[tuple[float, int, str]] = []
        for step_id_value in claim.get("evidence_step_ids") or []:
            step_id = str(step_id_value)
            if step_id in represented_step_ids:
                continue
            step = steps.get(step_id) or {}
            comparison = " ".join(
                [
                    str(step.get("statement") or ""),
                    *[
                        str((fragments.get(fragment_id) or {}).get("verbatim_excerpt") or "")
                        for fragment_id in _fragment_ids(step)
                    ],
                ]
            )
            ranked.append(
                (
                    _semantic_overlap(paragraph_markdown, comparison),
                    _semantic_overlap_count(paragraph_markdown, comparison),
                    step_id,
                )
            )
        if claim_id not in represented_claim_ids or footnote:
            matching = sorted(
                (
                    item
                    for item in ranked
                    if item[0] >= _LEGACY_EVIDENCE_OVERLAP_MINIMUM
                    and item[1] >= 2
                ),
                reverse=True,
            )[:2]
            selected.update(step_id for _, _, step_id in matching)
            if claim_id not in represented_claim_ids and not matching and ranked:
                selected.add(max(ranked)[2])
    return selected


def _source_ids_for_claims(
    provenance: dict[str, Any], knowledge: dict[str, Any]
) -> set[str]:
    claims = {
        str(item.get("claim_id") or ""): item
        for item in knowledge.get("claims", [])
    }
    steps = {
        str(item.get("evidence_step_id") or ""): item
        for item in knowledge.get("evidence_steps", [])
    }
    fragments = {
        str(item.get("fragment_id") or ""): item
        for item in knowledge.get("source_fragments", [])
    }
    return {
        str(fragment.get("source_id") or "")
        for claim_id in provenance.get("claim_ids") or []
        for step_id in (claims.get(str(claim_id)) or {}).get("evidence_step_ids") or []
        for fragment_id in _fragment_ids(steps.get(str(step_id)) or {})
        if (fragment := fragments.get(fragment_id))
    }


def _timestamp_seconds(value: str) -> float | None:
    match = re.search(r"\[(\d{2}):(\d{2}):(\d{2})\]", value)
    if not match:
        return None
    hours, minutes, seconds = (int(part) for part in match.groups())
    return float(hours * 3600 + minutes * 60 + seconds)


def _original_source_card(
    *,
    original: dict[str, Any],
    document: dict[str, Any],
    excerpt: str,
    mapping_kind: str,
    claim_ids: list[str],
    sermon_cache: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    source_type = str(document.get("source_type") or original.get("source_type") or "")
    if source_type not in {"sermon_transcript", "notes_manuscript"}:
        return None
    synthetic_id = "ORIGINAL-" + hashlib.sha256(
        f"{original.get('source_id')}\0{excerpt}".encode("utf-8")
    ).hexdigest()[:16]
    card: dict[str, Any] = {
        "fragment_ids": [synthetic_id],
        "source_type": source_type,
        "title": str(document.get("title") or original.get("title") or "来源材料"),
        "excerpts": [excerpt.strip()],
        "full_source_url": None,
        "media": None,
        "mapping_kind": mapping_kind,
        "claim_ids": list(dict.fromkeys(claim_ids)),
        "evidence_step_ids": [],
        "route_revision_id": None,
        "route_label": None,
        "route_steps": [],
    }
    if source_type == "notes_manuscript":
        card["full_source_url"] = _safe_resource_url(document.get("source_url"))
        return card
    transcript_id = str(document.get("transcript_id") or original.get("transcript_id") or "")
    if not transcript_id:
        return None
    sermon = sermon_cache.get(transcript_id)
    if sermon is None:
        catalog = CanonicalRepositoryService._sermon_catalog_record(transcript_id)
        media = CanonicalRepositoryService._sermon_media(transcript_id, {}, catalog)
        sermon = {
            "full_source_url": f"/resources/sermons/{quote(transcript_id, safe='')}",
            "media": media.model_dump(mode="json"),
        }
        sermon_cache[transcript_id] = sermon
    start_seconds = _timestamp_seconds(excerpt)
    configured = Path(str(document.get("source_path") or ""))
    published_path = (
        configured
        if configured.is_file()
        else DATA_BASE_PATH / "script_published" / f"{transcript_id}.json"
    )
    timing = align_transcript_excerpt(
        excerpt=excerpt,
        source=document,
        published_path=published_path,
        raw_path=DATA_BASE_PATH / "script" / f"{transcript_id}.json",
    )
    excerpt_start = timing.get("excerpt_start_time")
    excerpt_end = timing.get("excerpt_end_time")
    has_excerpt_timing = isinstance(excerpt_start, (int, float)) and isinstance(
        excerpt_end, (int, float)
    )
    if has_excerpt_timing:
        start_seconds = max(0.0, float(excerpt_start) - 2.0)
    card["full_source_url"] = sermon["full_source_url"]
    card["media"] = {
        **sermon["media"],
        "fragment_ids": [synthetic_id],
        "start_seconds": start_seconds,
        "end_seconds": float(excerpt_end) + 2.0 if has_excerpt_timing else None,
        "excerpt_start_seconds": float(excerpt_start) if has_excerpt_timing else None,
        "excerpt_end_seconds": float(excerpt_end) if has_excerpt_timing else None,
        "paragraph_start_seconds": None,
        "paragraph_end_seconds": None,
        "timing_status": str(timing.get("status") or "paragraph_fallback"),
        "timing_method": str(timing.get("method") or "paragraph_start"),
        "timing_match_ratio": (
            float(timing["match_ratio"])
            if isinstance(timing.get("match_ratio"), (int, float))
            else None
        ),
        "reviewed_text_differs_from_raw": timing.get(
            "reviewed_text_differs_from_raw"
        ),
        "lineage_window_expanded": bool(timing.get("lineage_window_expanded")),
        "timing_alignment_sha256": timing.get("alignment_sha256"),
    }
    return card


def _original_source_support(
    provenance: dict[str, Any],
    knowledge: dict[str, Any],
    paragraph_markdown: str,
    sermon_cache: dict[str, dict[str, Any]],
    *,
    exact_quote: str | None = None,
) -> list[dict[str, Any]]:
    originals_payload = knowledge.get("source_originals") or {}
    originals = {
        str(item.get("source_id") or ""): item
        for item in originals_payload.get("originals", [])
    }
    documents = {
        str(item.get("source_id") or ""): item
        for item in knowledge.get("source_documents", [])
    }
    candidates: list[tuple[float, str, dict[str, Any], str]] = []
    for source_id in _source_ids_for_claims(provenance, knowledge):
        original = originals.get(source_id)
        if not original:
            continue
        for block in re.split(r"(?=\[\d{2}:\d{2}:\d{2}\])", str(original.get("content") or "")):
            block = block.strip()
            if not block:
                continue
            if exact_quote and not _contains_normalized_quote(block, exact_quote):
                continue
            score = 1.0 if exact_quote else _semantic_overlap(paragraph_markdown, block)
            candidates.append((score, source_id, original, block))
    if not candidates:
        return []
    score, source_id, original, block = max(candidates, key=lambda item: item[0])
    if not exact_quote and score < 0.05:
        return []
    sentences = [
        value.strip()
        for value in re.split(r"(?<=[。！？!?])", block)
        if value.strip()
    ]
    if exact_quote:
        quoted_spans = [
            value
            for value in re.findall(r"「[^」]{1,800}」", block)
            if _contains_normalized_quote(value, exact_quote)
        ]
        matching = [
            sentence
            for sentence in sentences
            if _contains_normalized_quote(sentence, exact_quote)
        ]
        excerpt = quoted_spans[0] if quoted_spans else matching[0] if matching else block[:800]
        mapping_kind = "original_exact_quote"
    else:
        ranked = [(_semantic_overlap(paragraph_markdown, sentence), index) for index, sentence in enumerate(sentences)]
        best_index = max(ranked)[1] if ranked else 0
        start = max(0, best_index - 1)
        excerpt = "".join(sentences[start : best_index + 2])[:800]
        mapping_kind = "source_original_context"
    card = _original_source_card(
        original=original,
        document=documents.get(source_id) or original,
        excerpt=excerpt,
        mapping_kind=mapping_kind,
        # Context snippets prove wording and local setup. Claim coverage is
        # credited only to route attestations or EvidenceSteps, never to a
        # broad transcript window that merely happens to be nearby.
        claim_ids=[],
        sermon_cache=sermon_cache,
    )
    return [card] if card else []


def _merge_media_range(
    target: dict[str, Any], source: dict[str, Any]
) -> None:
    target_media = target.get("media")
    source_media = source.get("media")
    if not isinstance(target_media, dict) or not isinstance(source_media, dict):
        return
    starts = [
        value
        for value in (target_media.get("start_seconds"), source_media.get("start_seconds"))
        if isinstance(value, (int, float))
    ]
    ends = [
        value
        for value in (target_media.get("end_seconds"), source_media.get("end_seconds"))
        if isinstance(value, (int, float))
    ]
    target_media["start_seconds"] = min(starts) if starts else None
    target_media["end_seconds"] = max(ends) if ends else None


def _append_media_clip(clips: list[dict[str, Any]], media: Any) -> None:
    if not isinstance(media, dict):
        return
    key = (
        media.get("url"),
        media.get("start_seconds"),
        media.get("end_seconds"),
        media.get("timing_alignment_sha256"),
    )
    if any(
        (
            item.get("url"),
            item.get("start_seconds"),
            item.get("end_seconds"),
            item.get("timing_alignment_sha256"),
        )
        == key
        for item in clips
    ):
        return
    clips.append(dict(media))


def _route_sources(
    provenance: dict[str, Any],
    packet: dict[str, Any],
    route_revision_ids: list[str],
    sermon_cache: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve reader evidence through approved route attestations.

    Claim evidence says which assertions a paragraph uses, but it does not say
    which source-local argument establishes the paragraph's inference.  The
    route attestation does: its step bindings select the exact fragments for
    each premise, qualification and conclusion.  Only routes assigned to the
    paragraph's approved section (or explicitly named by provenance) are
    eligible, so a Claim shared by another section cannot pull in a competing
    argument path.
    """

    claim_ids = {str(value) for value in provenance.get("claim_ids") or []}
    if not claim_ids or not route_revision_ids:
        return []
    knowledge = packet.get("knowledge") or {}
    fragments = {
        str(item.get("fragment_id") or ""): item
        for item in knowledge.get("source_fragments", [])
    }
    documents = {
        str(item.get("source_id") or ""): item
        for item in knowledge.get("source_documents", [])
    }
    claims = {
        str(item.get("claim_id") or ""): item
        for item in knowledge.get("claims", [])
    }
    claim_evidence_step_ids = {
        str(step_id)
        for claim_id in claim_ids
        for step_id in (claims.get(claim_id) or {}).get("evidence_step_ids") or []
    }
    explicit_evidence_step_ids = {
        str(value) for value in provenance.get("evidence_step_ids") or []
    }
    declared_evidence_step_ids = explicit_evidence_step_ids or claim_evidence_step_ids
    routes = {
        str(item.get("revision", {}).get("argument_route_revision_id") or ""): item
        for item in packet.get("argument_routes") or []
    }
    result: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for route_revision_id in route_revision_ids:
        route = routes.get(route_revision_id)
        if not route:
            continue
        revision = route.get("revision") or {}
        nodes = {
            str(item.get("route_step_key") or ""): item
            for item in revision.get("ordered_inference_nodes") or []
        }
        for attestation in route.get("attestations") or []:
            attested_claim_ids = {
                str(value) for value in attestation.get("claim_ids") or []
            }
            if not (claim_ids & attested_claim_ids):
                continue
            source = documents.get(str(attestation.get("source_id") or ""))
            if not source:
                continue
            card: dict[str, Any] | None = None
            route_steps: list[dict[str, Any]] = []
            for binding in attestation.get("step_bindings") or []:
                binding_evidence_step_ids = {
                    str(value) for value in binding.get("evidence_step_ids") or []
                }
                if (
                    binding_evidence_step_ids
                    and not (binding_evidence_step_ids & declared_evidence_step_ids)
                ):
                    continue
                step_key = str(binding.get("route_step_key") or "")
                step_excerpts: list[str] = []
                step_fragment_ids: list[str] = []
                step_media_clips: list[dict[str, Any]] = []
                for fragment_id_value in binding.get("source_fragment_ids") or []:
                    fragment_id = str(fragment_id_value)
                    fragment = fragments.get(fragment_id)
                    if not fragment:
                        continue
                    fragment_source = documents.get(str(fragment.get("source_id") or ""))
                    if not fragment_source:
                        continue
                    item = _source_fragment_read_model(
                        fragment,
                        fragment_source,
                        sermon_cache,
                    )
                    if not item:
                        continue
                    item_excerpts = list(item["excerpts"])
                    _append_media_clip(step_media_clips, item.get("media"))
                    if card is None:
                        card = item
                        card["excerpts"] = []
                        card["fragment_ids"] = []
                    else:
                        _merge_media_range(card, item)
                    if fragment_id not in card["fragment_ids"]:
                        card["fragment_ids"].append(fragment_id)
                    for excerpt in item_excerpts:
                        if excerpt not in card["excerpts"]:
                            card["excerpts"].append(excerpt)
                        if excerpt not in step_excerpts:
                            step_excerpts.append(excerpt)
                    if fragment_id not in step_fragment_ids:
                        step_fragment_ids.append(fragment_id)
                if step_excerpts:
                    node = nodes.get(step_key) or {}
                    route_steps.append(
                        {
                            "route_step_key": step_key,
                            "role": str(node.get("role") or "support"),
                            # Conclusion nodes deliberately have no normalized
                            # proposition: their conclusion_ref points to a CVP.
                            # Do not substitute that CVP's editorial wording for
                            # the source-local route step shown to a reviewer.
                            "proposition": (
                                str(node.get("normalized_proposition"))
                                if node.get("normalized_proposition")
                                else None
                            ),
                            "fragment_ids": step_fragment_ids,
                            "excerpts": step_excerpts,
                            "media_clips": step_media_clips,
                        }
                    )
            if card and route_steps:
                card["mapping_kind"] = "argument_route_attestation"
                card["claim_ids"] = sorted(claim_ids & attested_claim_ids)
                card["evidence_step_ids"] = list(
                    dict.fromkeys(
                        str(value)
                        for binding in attestation.get("step_bindings") or []
                        for value in binding.get("evidence_step_ids") or []
                        if str(value) in declared_evidence_step_ids
                    )
                )
                card["route_revision_id"] = route_revision_id
                card["route_label"] = str(revision.get("route_label") or "本段论证")
                card["route_steps"] = route_steps
                # A route may contain non-contiguous evidence.  Its steps own
                # their clips; a card-level min/max range would replay all of
                # the unrelated material between them.
                card["media"] = None
                group_key = (route_revision_id, str(attestation.get("source_id") or ""))
                existing = grouped.get(group_key)
                if existing is None:
                    grouped[group_key] = card
                    result.append(card)
                    continue
                _merge_media_range(existing, card)
                for fragment_id in card["fragment_ids"]:
                    if fragment_id not in existing["fragment_ids"]:
                        existing["fragment_ids"].append(fragment_id)
                for excerpt in card["excerpts"]:
                    if excerpt not in existing["excerpts"]:
                        existing["excerpts"].append(excerpt)
                for claim_id in card["claim_ids"]:
                    if claim_id not in existing["claim_ids"]:
                        existing["claim_ids"].append(claim_id)
                for step_id in card["evidence_step_ids"]:
                    if step_id not in existing["evidence_step_ids"]:
                        existing["evidence_step_ids"].append(step_id)
                existing_steps = {
                    str(step["route_step_key"]): step
                    for step in existing["route_steps"]
                }
                for step in card["route_steps"]:
                    step_key = str(step["route_step_key"])
                    existing_step = existing_steps.get(step_key)
                    if existing_step is None:
                        existing["route_steps"].append(step)
                        existing_steps[step_key] = step
                        continue
                    for fragment_id in step["fragment_ids"]:
                        if fragment_id not in existing_step["fragment_ids"]:
                            existing_step["fragment_ids"].append(fragment_id)
                    for excerpt in step["excerpts"]:
                        if excerpt not in existing_step["excerpts"]:
                            existing_step["excerpts"].append(excerpt)
                    for media in step.get("media_clips") or []:
                        _append_media_clip(existing_step.setdefault("media_clips", []), media)
    return result


def _section_route_ranges(
    markdown: str, packet: dict[str, Any]
) -> list[tuple[int, int, list[str]]]:
    sections = list((packet.get("editorial_decisions") or {}).get("sections") or [])
    starts: list[tuple[int, list[str]]] = []
    for section in sections:
        heading = str(section.get("heading") or "").strip()
        offset = markdown.find(f"## {heading}") if heading else -1
        if offset >= 0:
            starts.append(
                (
                    offset,
                    [
                        str(value)
                        for value in section.get("argument_route_revision_ids") or []
                    ],
                )
            )
    return [
        (start, starts[index + 1][0] if index + 1 < len(starts) else len(markdown), routes)
        for index, (start, routes) in enumerate(starts)
    ]


def _paragraph_sources(
    provenance: dict[str, Any],
    packet: dict[str, Any],
    section_route_ids: list[str],
    sermon_cache: dict[str, dict[str, Any]],
    *,
    paragraph_markdown: str = "",
) -> list[dict[str, Any]]:
    explicit_route_ids = [
        str(value)
        for value in provenance.get("argument_route_revision_ids") or []
    ]
    routed = _route_sources(
        provenance,
        packet,
        explicit_route_ids or section_route_ids,
        sermon_cache,
    )
    knowledge = packet.get("knowledge") or {}
    if not routed:
        sources = _claim_sources(
            provenance,
            knowledge,
            sermon_cache,
            paragraph_markdown=paragraph_markdown,
        )
    else:
        represented_fragments = {
            str(value)
            for source in routed
            for value in source.get("fragment_ids") or []
        }
        represented_steps = {
            str(value)
            for source in routed
            for value in source.get("evidence_step_ids") or []
        }
        represented_claims = {
            str(value)
            for source in routed
            for value in source.get("claim_ids") or []
        }
        supplemental_steps = (
            set()
            if _quoted_paragraph_text(paragraph_markdown)
            else _selected_supplemental_step_ids(
                provenance,
                knowledge,
                paragraph_markdown,
                represented_steps,
                represented_claims,
            )
        )
        sources = [
            *routed,
            *_claim_sources(
                provenance,
                knowledge,
                sermon_cache,
                paragraph_markdown=paragraph_markdown,
                evidence_step_ids=supplemental_steps,
                exclude_fragment_ids=represented_fragments,
            ),
        ]

    quoted = _quoted_paragraph_text(paragraph_markdown)
    if quoted:
        represented_fragments = {
            str(value)
            for source in sources
            for value in source.get("fragment_ids") or []
        }
        exact_claim_sources = [
            source
            for source in _claim_sources(
                provenance,
                knowledge,
                sermon_cache,
                paragraph_markdown=paragraph_markdown,
                exclude_fragment_ids=represented_fragments,
            )
            if any(_contains_normalized_quote(excerpt, quoted) for excerpt in source["excerpts"])
        ]
        sources.extend(exact_claim_sources)
        if not any(
            _contains_normalized_quote(excerpt, quoted)
            for source in sources
            for excerpt in source.get("excerpts") or []
        ):
            sources.extend(
                _original_source_support(
                    provenance,
                    knowledge,
                    paragraph_markdown,
                    sermon_cache,
                    exact_quote=quoted,
                )
            )
    elif paragraph_markdown.rstrip().endswith(("：", ":")) and "问" in _TO_SIMPLIFIED.convert(
        paragraph_markdown
    ):
        # These short paragraphs introduce a quotation in the next governed
        # block.  Their contextual question or narrative premise often lives
        # in the transcript segment around the Claim, not in the route's
        # compressed step fragments.
        sources.extend(
            _original_source_support(
                provenance,
                knowledge,
                paragraph_markdown,
                sermon_cache,
            )
        )
    return sources


def _governed_block_end(markdown: str, comment_end: int, next_comment: int) -> int | None:
    segment = markdown[comment_end:next_comment]
    content_match = re.search(r"\S", segment)
    if not content_match:
        return None
    body_start = content_match.start()
    first_line = segment[body_start:].splitlines()[0].strip()
    if re.match(r"^#{1,6}\s+", first_line):
        return None
    separator = re.search(r"\r?\n[ \t]*\r?\n", segment[body_start:])
    relative_end = body_start + (separator.start() if separator else len(segment[body_start:]))
    return comment_end + relative_end


def _source_projection_audit(
    *,
    manuscript_sha256: str,
    packet_sha256: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    quote_count = 0
    for row in rows:
        paragraph_id = str(row["paragraph_id"])
        sources = list(row.get("sources") or [])
        if not sources:
            findings.append(
                {
                    "code": "missing_source_projection",
                    "paragraph_id": paragraph_id,
                    "message": "本段没有可核验的来源投影。",
                }
            )
            continue
        projected_claims = {
            str(value)
            for source in sources
            for value in source.get("claim_ids") or []
        }
        missing_claims = set(row.get("claim_ids") or []) - projected_claims
        if missing_claims:
            findings.append(
                {
                    "code": "claim_not_projected",
                    "paragraph_id": paragraph_id,
                    "claim_ids": sorted(missing_claims),
                    "message": "本段声明的 Claim 没有全部出现在可见来源中。",
                }
            )
        declared_steps = set(row.get("evidence_step_ids") or [])
        projected_steps = {
            str(value)
            for source in sources
            for value in source.get("evidence_step_ids") or []
        }
        missing_steps = declared_steps - projected_steps
        if missing_steps:
            findings.append(
                {
                    "code": "evidence_step_not_projected",
                    "paragraph_id": paragraph_id,
                    "evidence_step_ids": sorted(missing_steps),
                    "message": "本段声明的 EvidenceStep 没有全部出现在可见来源中。",
                }
            )
        declared_routes = set(row.get("argument_route_revision_ids") or [])
        projected_routes = {
            str(source.get("route_revision_id") or "")
            for source in sources
            if source.get("route_revision_id")
        }
        missing_routes = declared_routes - projected_routes
        if missing_routes:
            findings.append(
                {
                    "code": "argument_route_not_projected",
                    "paragraph_id": paragraph_id,
                    "argument_route_revision_ids": sorted(missing_routes),
                    "message": "本段声明的 ArgumentRoute 没有全部出现在可见来源中。",
                }
            )
        quoted = row.get("quoted_text")
        if quoted:
            quote_count += 1
            if not any(
                _contains_normalized_quote(excerpt, str(quoted))
                for source in sources
                for excerpt in source.get("excerpts") or []
            ):
                findings.append(
                    {
                        "code": "direct_quote_without_exact_source",
                        "paragraph_id": paragraph_id,
                        "message": "本段逐字引文没有命中原稿中的精确文本。",
                    }
                )
    return {
        "schema_version": "wang_article_source_projection_audit.v1",
        "manuscript_sha256": manuscript_sha256,
        "authoring_packet_sha256": packet_sha256,
        "paragraphs_checked": len(rows),
        "paragraphs_with_sources": sum(bool(row.get("sources")) for row in rows),
        "direct_quotes_checked": quote_count,
        "findings": findings,
        "passed": not findings,
    }


def _source_playback_audit(
    *,
    manuscript_sha256: str,
    packet_sha256: str,
    annotations: list[dict[str, Any]],
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    clips: list[tuple[str, dict[str, Any], list[str]]] = []
    seen: set[tuple[Any, ...]] = set()
    for annotation in annotations:
        paragraph_id = str(annotation.get("annotation_id") or "")
        for source in annotation.get("sources") or []:
            if source.get("source_type") != "sermon_transcript":
                continue
            rows = [
                media
                for step in source.get("route_steps") or []
                for media in step.get("media_clips") or []
            ]
            if not rows and isinstance(source.get("media"), dict):
                rows = [source["media"]]
            for media in rows:
                key = (
                    media.get("url"),
                    media.get("start_seconds"),
                    media.get("end_seconds"),
                    media.get("timing_alignment_sha256"),
                )
                if key in seen:
                    continue
                seen.add(key)
                fragment_ids = list(
                    media.get("fragment_ids") or source.get("fragment_ids") or []
                )
                clips.append((paragraph_id, media, fragment_ids))
    for paragraph_id, media, fragment_ids in clips:
        status = str(media.get("timing_status") or "paragraph_fallback")
        if status not in {"exact", "estimated"}:
            findings.append(
                {
                    "code": "excerpt_audio_alignment_unresolved",
                    "severity": "error",
                    "paragraph_id": paragraph_id,
                    "fragment_ids": fragment_ids,
                    "message": (
                        "本段录音只能从整段开头播放，尚未定位到具体引文。"
                    ),
                }
            )
        if media.get("reviewed_text_differs_from_raw") is True:
            findings.append(
                {
                    "code": "reviewed_text_differs_from_raw_transcript",
                    "severity": "warning",
                    "paragraph_id": paragraph_id,
                    "fragment_ids": fragment_ids,
                    "message": (
                        "校订文字与原始带时间转录不完全一致；"
                        "录音位置已对齐，但文字需要复核。"
                    ),
                }
            )
    return {
        "schema_version": "wang_article_source_playback_audit.v1",
        "manuscript_sha256": manuscript_sha256,
        "authoring_packet_sha256": packet_sha256,
        "clips_checked": len(clips),
        "exact_clips": sum(media.get("timing_status") == "exact" for _, media, _ in clips),
        "estimated_clips": sum(
            media.get("timing_status") == "estimated" for _, media, _ in clips
        ),
        "paragraph_fallback_clips": sum(
            media.get("timing_status") not in {"exact", "estimated"}
            for _, media, _ in clips
        ),
        "findings": findings,
        "passed": not any(item["severity"] == "error" for item in findings),
    }


def _annotated_reader_markdown(
    markdown: str,
    packet: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Attach hidden-source controls without changing the manuscript's prose."""

    matches = list(PROVENANCE_COMMENT_RE.finditer(markdown))
    insertions: list[tuple[int, str]] = []
    annotations: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    sermon_cache: dict[str, dict[str, Any]] = {}
    route_ranges = _section_route_ranges(markdown, packet)
    for index, match in enumerate(matches):
        try:
            provenance = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(provenance, dict):
            continue
        next_comment = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        block_end = _governed_block_end(markdown, match.end(), next_comment)
        if block_end is None:
            continue
        paragraph_markdown = markdown[match.end():block_end].strip()
        section_route_ids = next(
            (
                route_ids
                for start, end, route_ids in route_ranges
                if start <= match.start() < end
            ),
            [],
        )
        sources = _paragraph_sources(
            provenance,
            packet,
            section_route_ids,
            sermon_cache,
            paragraph_markdown=paragraph_markdown,
        )
        paragraph_id = f"p{len(audit_rows) + 1}"
        explicit_routes = [
            str(value)
            for value in provenance.get("argument_route_revision_ids") or []
        ]
        audit_rows.append(
            {
                "paragraph_id": paragraph_id,
                "paragraph_sha256": hashlib.sha256(
                    paragraph_markdown.encode("utf-8")
                ).hexdigest(),
                "claim_ids": [
                    str(value) for value in provenance.get("claim_ids") or []
                ],
                "evidence_step_ids": [
                    str(value)
                    for value in provenance.get("evidence_step_ids") or []
                ],
                "argument_route_revision_ids": explicit_routes,
                "quoted_text": _quoted_paragraph_text(paragraph_markdown),
                "sources": sources,
            }
        )
        if not sources:
            continue
        annotation_id = paragraph_id
        annotations.append(
            {
                "annotation_id": annotation_id,
                "paragraph_sha256": audit_rows[-1]["paragraph_sha256"],
                "sources": sources,
            }
        )
        footnote = FOOTNOTE_DEFINITION_RE.match(
            paragraph_markdown.splitlines()[0].strip()
        )
        marker = (
            f" [查看本注来源]({SOURCE_MARKER_PREFIX}{annotation_id})"
            if footnote
            else f"\n\n[查看本段来源]({SOURCE_MARKER_PREFIX}{annotation_id})"
        )
        insertions.append((block_end, marker))
    annotated = markdown
    for position, marker in reversed(insertions):
        annotated = annotated[:position] + marker + annotated[position:]
    annotated = PROVENANCE_COMMENT_RE.sub("", annotated)
    audit = _source_projection_audit(
        manuscript_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        packet_sha256=str(packet.get("packet_sha256") or ""),
        rows=audit_rows,
    )
    playback_audit = _source_playback_audit(
        manuscript_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        packet_sha256=str(packet.get("packet_sha256") or ""),
        annotations=annotations,
    )
    return annotated.strip(), annotations, audit, playback_audit


def _stage_checks(workflow: dict[str, Any]) -> list[dict[str, str]]:
    status = str(workflow.get("status") or "unknown")
    published = status == "workflow_published"
    grounding = "passed" if status == "draft_grounded" or published else "not_run"
    if status == "grounding_gate_failed":
        grounding = "failed"
    return [
        {"id": "author", "label": "Author 初稿", "state": "complete"},
        {"id": "grounding", "label": "Grounding", "state": grounding},
        {
            "id": "editorial_review",
            "label": "Editorial Review",
            "state": "passed" if published else "not_run",
        },
        {
            "id": "program_audit",
            "label": "Program Audit",
            "state": "passed" if published else "not_run",
        },
        {
            "id": "publication",
            "label": "正式出版",
            "state": "passed" if published else "not_run",
        },
    ]



def _draft_first_annotated_markdown(
    markdown: str,
    bindings_record: dict[str, Any],
    packet: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Source disclosure for a draft-first essay, driven by derived bindings.

    Draft-first manuscripts carry no provenance comments; the bindings file
    (verbatim-verified spans per reader paragraph, #287) plays their role.
    Cards go through the same builder the provenance path uses, so sermon
    spans keep their audio timing and the frontend shape is unchanged.
    """

    from backend.pipeline.draft_first_source_binding import reader_paragraphs

    knowledge = packet
    originals_payload = knowledge.get("source_originals") or {}
    originals = {
        str(item.get("source_id") or ""): item
        for item in originals_payload.get("originals", [])
    }
    documents = {
        str(item.get("source_id") or ""): item
        for item in knowledge.get("source_documents", [])
    }
    paragraphs = reader_paragraphs(markdown)
    bindings_by_sha = {
        str(item.get("paragraph_sha256") or ""): item
        for item in bindings_record.get("bindings") or []
    }
    sermon_cache: dict[str, dict[str, Any]] = {}
    annotations: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    insertions: list[tuple[int, str]] = []
    with_sources = 0
    cursor = 0
    for paragraph in paragraphs:
        text = str(paragraph["text"])
        offset = markdown.find(text, cursor)
        if offset < 0:
            findings.append(
                {
                    "code": "paragraph_not_located",
                    "paragraph_id": f"p{paragraph['paragraph_index'] + 1}",
                    "message": "读者段落无法在稿件中定位。",
                }
            )
            continue
        cursor = offset + len(text)
        binding = bindings_by_sha.get(str(paragraph["paragraph_sha256"]))
        if binding is None:
            findings.append(
                {
                    "code": "paragraph_binding_missing",
                    "paragraph_id": f"p{paragraph['paragraph_index'] + 1}",
                    "message": "该段落没有登记来源绑定（稿件可能在绑定后被改动）。",
                }
            )
            continue
        cards: list[dict[str, Any]] = []
        for span in binding.get("spans") or []:
            source_id = str(span.get("source_id") or "")
            excerpt = str(span.get("excerpt") or "")
            original = originals.get(source_id)
            if not original or excerpt not in str(original.get("content") or ""):
                findings.append(
                    {
                        "code": "binding_excerpt_unverified",
                        "paragraph_id": f"p{paragraph['paragraph_index'] + 1}",
                        "message": "登记的来源片段无法在原文中逐字复核。",
                    }
                )
                continue
            card = _original_source_card(
                original=original,
                document=documents.get(source_id) or original,
                excerpt=excerpt,
                mapping_kind="derived_source_binding",
                claim_ids=[],
                sermon_cache=sermon_cache,
            )
            if card:
                cards.append(card)
        if not cards:
            continue
        with_sources += 1
        annotation_id = f"p{paragraph['paragraph_index'] + 1}"
        annotations.append(
            {
                "annotation_id": annotation_id,
                "paragraph_sha256": paragraph["paragraph_sha256"],
                "sources": cards,
            }
        )
        insertions.append(
            (offset + len(text), f"\n\n[查看本段来源]({SOURCE_MARKER_PREFIX}{annotation_id})")
        )
    annotated = markdown
    for position, marker in reversed(insertions):
        annotated = annotated[:position] + marker + annotated[position:]
    projection_audit = {
        "schema_version": "wang_article_source_projection_audit.v1",
        "manuscript_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "authoring_packet_sha256": str(packet.get("evidence_packet_sha256") or ""),
        "paragraphs_checked": len(paragraphs),
        "paragraphs_with_sources": with_sources,
        "direct_quotes_checked": 0,
        "findings": findings,
        "passed": not any(
            item["code"] in {"binding_excerpt_unverified", "paragraph_not_located"}
            for item in findings
        ),
    }
    clips = [
        annotation["sources"][index].get("media")
        for annotation in annotations
        for index in range(len(annotation["sources"]))
        if annotation["sources"][index].get("media")
    ]
    playback_audit = {
        "schema_version": "wang_article_source_playback_audit.v1",
        "manuscript_sha256": projection_audit["manuscript_sha256"],
        "authoring_packet_sha256": projection_audit["authoring_packet_sha256"],
        "clips_checked": len(clips),
        "exact_clips": sum(
            1 for clip in clips if clip.get("timing_status") == "exact"
        ),
        "estimated_clips": sum(
            1 for clip in clips if clip.get("timing_status") == "estimated"
        ),
        "paragraph_fallback_clips": sum(
            1 for clip in clips if clip.get("timing_status") not in {"exact", "estimated"}
        ),
        "findings": [],
        "passed": True,
    }
    return annotated, annotations, projection_audit, playback_audit


def _review_data(manifest_path: Path, *, include_markdown: bool) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise ValueError("unsupported or unreadable review preview manifest")
    review_id = str(manifest.get("review_id") or "").strip()
    if not review_id or manifest_path.stem != review_id:
        raise ValueError("review id does not match its manifest filename")

    manuscript_path = _safe_staging_child(str(manifest.get("manuscript_relative_path") or ""))
    workflow_path = _safe_staging_child(str(manifest.get("workflow_status_relative_path") or ""))
    if not manuscript_path.is_file() or not workflow_path.is_file():
        raise ValueError("review manuscript or workflow status is missing")

    expected_manuscript_sha = str(manifest.get("manuscript_sha256") or "")
    expected_workflow_sha = str(manifest.get("workflow_status_sha256") or "")
    current_manuscript_sha = _sha256(manuscript_path)
    current_workflow_sha = _sha256(workflow_path)
    integrity_matches = (
        expected_manuscript_sha == current_manuscript_sha
        and expected_workflow_sha == current_workflow_sha
    )
    variant = str(manifest.get("variant") or "briefed")
    packet: dict[str, Any] = {}
    packet_relative_path = str(manifest.get("authoring_packet_relative_path") or "").strip()
    if packet_relative_path:
        packet_path = _safe_staging_child(packet_relative_path)
        if not packet_path.is_file():
            raise ValueError("review authoring packet is missing")
        packet = _result(_read_json(packet_path))
        integrity_matches = integrity_matches and (
            str(manifest.get("authoring_packet_file_sha256") or "") == _sha256(packet_path)
            and str(manifest.get("authoring_packet_sha256") or "")
            == str(packet.get("packet_sha256") or "")
        )
    bindings_record: dict[str, Any] = {}
    evidence_packet: dict[str, Any] = {}
    if variant == "draft_first":
        bindings_path = _safe_staging_child(
            str(manifest.get("source_bindings_relative_path") or "")
        )
        evidence_path = _safe_staging_child(
            str(manifest.get("evidence_packet_relative_path") or "")
        )
        if not bindings_path.is_file() or not evidence_path.is_file():
            raise ValueError("draft-first review bindings or evidence packet is missing")
        bindings_record = _read_json(bindings_path)
        evidence_packet = _result(_read_json(evidence_path))
        integrity_matches = integrity_matches and (
            str(manifest.get("source_bindings_sha256") or "") == _sha256(bindings_path)
            and str(bindings_record.get("manuscript_sha256") or "")
            == current_manuscript_sha
        )
    integrity = "verified" if integrity_matches else "changed"
    workflow = _read_json(workflow_path)
    result: dict[str, Any] = {
        "review_id": review_id,
        "title": str(manifest.get("title") or "").strip(),
        "passage": str(manifest.get("passage") or "").strip(),
        "registered_at": str(manifest.get("registered_at") or ""),
        "status": "internal_review",
        "integrity_status": integrity,
        "manuscript_sha256": expected_manuscript_sha,
        "brief_sha256": str(manifest.get("brief_sha256") or ""),
        "authoring_packet_sha256": str(manifest.get("authoring_packet_sha256") or ""),
        "workflow_status": str(workflow.get("status") or "unknown"),
        "stage_checks": _stage_checks(workflow),
        "href": f"/admin/wang/operations/articles/reviews/{review_id}",
    }
    if include_markdown:
        if integrity != "verified":
            raise HTTPException(
                status_code=409,
                detail="审稿预览绑定的稿件或状态已经改变，请重新登记后再审。",
            )
        manuscript = manuscript_path.read_text(encoding="utf-8")
        if variant == "draft_first":
            (
                result["markdown"],
                result["source_annotations"],
                result["source_projection_audit"],
                result["source_playback_audit"],
            ) = _draft_first_annotated_markdown(
                manuscript, bindings_record, evidence_packet
            )
            return result
        knowledge = packet.get("knowledge") if isinstance(packet.get("knowledge"), dict) else {}
        if knowledge:
            packet = dict(packet)
            packet["knowledge"] = project_excerpt_timings(
                knowledge, data_base_path=DATA_BASE_PATH
            )
            (
                result["markdown"],
                result["source_annotations"],
                result["source_projection_audit"],
                result["source_playback_audit"],
            ) = _annotated_reader_markdown(manuscript, packet)
        else:
            result["markdown"] = _reader_markdown(manuscript)
            result["source_annotations"] = []
            result["source_projection_audit"] = {
                "schema_version": "wang_article_source_projection_audit.v1",
                "manuscript_sha256": current_manuscript_sha,
                "authoring_packet_sha256": "",
                "paragraphs_checked": 0,
                "paragraphs_with_sources": 0,
                "direct_quotes_checked": 0,
                "findings": [
                    {
                        "code": "missing_authoring_knowledge",
                        "paragraph_id": "",
                        "message": "稿件没有可用于来源核验的 authoring knowledge。",
                    }
                ],
                "passed": False,
            }
            result["source_playback_audit"] = {
                "schema_version": "wang_article_source_playback_audit.v1",
                "manuscript_sha256": current_manuscript_sha,
                "authoring_packet_sha256": "",
                "clips_checked": 0,
                "exact_clips": 0,
                "estimated_clips": 0,
                "paragraph_fallback_clips": 0,
                "findings": [],
                "passed": False,
            }
    return result


@router.get("")
def list_article_reviews() -> dict[str, Any]:
    reviews: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    if REVIEW_MANIFEST_ROOT.is_dir():
        for manifest_path in sorted(REVIEW_MANIFEST_ROOT.glob("*.json")):
            try:
                reviews.append(_review_data(manifest_path, include_markdown=False))
            except ValueError as exc:
                warnings.append({"manifest": manifest_path.name, "message": str(exc)})
    return {"schema_version": RESPONSE_SCHEMA, "reviews": reviews, "warnings": warnings}


@router.get("/{review_id}")
def article_review(review_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", review_id):
        raise HTTPException(status_code=404, detail="找不到这份审稿预览。")
    manifest_path = REVIEW_MANIFEST_ROOT / f"{review_id}.json"
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="找不到这份审稿预览。")
    try:
        return {"schema_version": RESPONSE_SCHEMA, **_review_data(manifest_path, include_markdown=True)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
