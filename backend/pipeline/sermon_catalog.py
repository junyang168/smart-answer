"""Build the public sermon catalog from the full-corpus first-pass survey.

The catalog is a reproducible website read model.  It complements, but never
rewrites, the manually maintained ``config/sermon.json`` metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from backend.api.config import DATA_BASE_PATH
from backend.config.wang_platform_paths import wang_platform_paths
from backend.api.sermon_search.bible_refs import normalize_ref
from backend.api.sermon_search.topics import extract_topics


CATALOG_SCHEMA_VERSION = "wang_sermon_catalog_v4"
CLASSIFIER_VERSION = "content_structure_classifier_v2"
DEFAULT_SURVEY_DIR = wang_platform_paths().corpus_survey_staging
DEFAULT_OUTPUT_PATH = DATA_BASE_PATH / "sermon_catalog.json"
DEFAULT_OVERRIDES_PATH = DATA_BASE_PATH / "config" / "sermon_catalog_overrides.json"

MODE_LABELS = {
    "scripture_led": "經卷釋經",
    "topic_led": "專題講論",
    "mixed": "釋經與專題並重",
}

SOURCE_CATEGORY_LABELS = {
    "dallas_hlc": "達拉斯聖道教會",
    "nysc": "紐約靈命進深會",
    "external_church": "其他教會",
    "other": "其他來源",
    "unknown": "來源待確認",
}


def normalize_sermon_source(
    metadata: dict[str, Any] | None,
    series: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Separate source organization, provider and media URL.

    ``config/sermon.json`` predates this distinction, so its ``source`` field
    may contain a URL, a provider name, or nothing at all.  Preserve that raw
    value while exposing stable fields for filtering and provenance display.
    An explicit event marker in the series metadata (for example ``NYSC``)
    identifies the organization even when ``source`` is only a YouTube media
    URL.  Source identity never determines Scripture coverage.
    """

    if metadata is None:
        return {
            "source_category": "unknown",
            "source_category_label": SOURCE_CATEGORY_LABELS["unknown"],
            "source_organization": None,
            "source_provider": None,
            "source_url": None,
            "source_raw": None,
        }

    series = series or {}
    series_blob = " ".join(
        str(value or "")
        for value in (series.get("series_id"), series.get("series_title"))
    )
    explicit_nysc = bool(re.search(r"NYSC", series_blob, re.IGNORECASE))

    raw_value = metadata.get("source")
    raw = str(raw_value).strip() if raw_value is not None else ""
    lowered = raw.lower()
    is_url = bool(re.match(r"^https?://", raw, re.IGNORECASE))
    if explicit_nysc:
        category = "nysc"
        organization = "紐約靈命進深會"
        provider = None if is_url or not raw else raw
        url = raw if is_url else None
    elif not raw:
        category = "dallas_hlc"
        organization = "達拉斯聖道教會"
        provider = None
        url = None
    elif "bctcnj.org" in lowered:
        category = "nysc"
        organization = "紐約靈命進深會"
        provider = None
        url = raw
    elif raw.casefold() == "ruxin zhang".casefold():
        category = "external_church"
        organization = "其他教會（名稱未記錄）"
        provider = "Ruxin Zhang"
        url = None
    else:
        category = "other"
        organization = None
        provider = None if is_url else raw
        url = raw if is_url else None
    return {
        "source_category": category,
        "source_category_label": SOURCE_CATEGORY_LABELS[category],
        "source_organization": organization,
        "source_provider": provider,
        "source_url": url,
        "source_raw": raw_value,
    }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_json(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _series_lookup(series_payload: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for series in series_payload:
        sermons = series.get("sermons") or []
        for index, transcript_id in enumerate(sermons):
            lookup[str(transcript_id)] = {
                "series_id": series.get("id"),
                "series_title": series.get("title") or series.get("id"),
                "series_order": index + 1,
            }
    return lookup


def _canonical_refs(clusters: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], Counter]:
    refs: dict[str, dict[str, Any]] = {}
    weights: Counter = Counter()
    for cluster in clusters:
        raw_refs = cluster.get("scripture_refs") or []
        parsed = [normalize_ref(str(raw)) for raw in raw_refs]
        parsed = [ref for ref in parsed if ref is not None]
        if not parsed:
            continue
        cluster_weight = max(1, len(set(cluster.get("segment_indexes") or []))) / len(parsed)
        for ref in parsed:
            if ref.osis not in refs:
                refs[ref.osis] = ref.model_dump(mode="json")
            weights[ref.osis] += cluster_weight
    ordered = sorted(refs.values(), key=lambda item: (-weights[item["osis"]], item["osis"]))
    return ordered, weights


def _content_metrics(clusters: list[dict[str, Any]], refs: list[dict[str, Any]], ref_weights: Counter) -> dict[str, Any]:
    function_segments: dict[str, set[str]] = defaultdict(set)
    substantive_segments: set[str] = set()
    for cluster in clusters:
        function = str(cluster.get("function") or "unknown")
        segments = {str(item) for item in cluster.get("segment_indexes") or []}
        function_segments[function].update(segments)
        if function != "non_substantive":
            substantive_segments.update(segments)

    denominator = max(1, len(substantive_segments))
    function_shares = {
        key: round(len(value & substantive_segments) / denominator, 4)
        for key, value in sorted(function_segments.items())
        if key != "non_substantive"
    }

    locus_weights: Counter = Counter()
    book_weights: Counter = Counter()
    for ref in refs:
        weight = float(ref_weights[ref["osis"]])
        locus_weights[(ref["book"], ref["chapter_start"])] += weight
        book_weights[ref["book"]] += weight
    total_ref_weight = sum(locus_weights.values()) or 1.0
    dominant_locus, dominant_weight = (locus_weights.most_common(1)[0] if locus_weights else ((None, None), 0.0))
    dominant_book, dominant_book_weight = (book_weights.most_common(1)[0] if book_weights else (None, 0.0))
    return {
        "substantive_segment_count": len(substantive_segments),
        "function_shares": function_shares,
        "scripture_reference_count": len(refs),
        "scripture_locus_count": len(locus_weights),
        "scripture_book_count": len(book_weights),
        "dominant_locus": (
            {"book": dominant_locus[0], "chapter": dominant_locus[1]}
            if dominant_locus[0]
            else None
        ),
        "dominant_locus_share": round(dominant_weight / total_ref_weight, 4),
        "dominant_book": dominant_book,
        "dominant_book_share": round(dominant_book_weight / total_ref_weight, 4),
    }


def classify_sermon(
    survey: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    series: dict[str, Any] | None = None,
) -> tuple[str, str, str, dict[str, Any]]:
    """Classify by content structure; titles and series are corroborating only."""

    metadata = metadata or {}
    series = series or {}
    clusters = list(survey.get("content_clusters") or [])
    refs, weights = _canonical_refs(clusters)
    metrics = _content_metrics(clusters, refs, weights)
    shares = metrics["function_shares"]
    exegesis = float(shares.get("exegesis", 0.0))
    topical = sum(float(shares.get(key, 0.0)) for key in ("theology", "application", "background"))
    dominant = float(metrics["dominant_locus_share"])
    locus_count = int(metrics["scripture_locus_count"])

    title_blob = " ".join(str(value or "") for value in (metadata.get("title"), metadata.get("theme")))
    series_blob = " ".join(str(value or "") for value in (series.get("series_id"), series.get("series_title")))
    scripture_title_hint = bool(re.search(r"釋經|释经|查經|查经", title_blob))
    scripture_series_hint = bool(re.search(r"釋經|释经|查經|查经", series_blob))
    series_book_match = any(
        str(ref.get("book_zh") or "") in series_blob
        for ref in _metadata_refs(metadata)
        if ref.get("book_zh")
    )

    reasons: list[str] = []
    if exegesis >= 0.35 and dominant >= 0.42:
        mode = "scripture_led"
        reasons.append("多數材料沿一個主要經文段落推進")
    elif exegesis >= 0.55 and dominant >= 0.28 and metrics["dominant_book_share"] >= 0.55:
        mode = "scripture_led"
        reasons.append("釋經材料占主導，且主要集中於同一卷書")
    elif locus_count >= 4 and dominant < 0.25:
        if (scripture_title_hint or series_book_match) and exegesis >= 0.40:
            mode = "mixed"
            reasons.append("本講名為釋經，但內容同時以多處經文與專題論證推進")
        else:
            mode = "topic_led"
            reasons.append("論述跨越多處經文，主要由議題而非連續經文推進")
    elif scripture_title_hint and exegesis >= 0.40 and dominant >= 0.28:
        mode = "scripture_led"
        reasons.append("內容結構與講道標題所示的釋經方向互相印證")
    elif (scripture_title_hint or scripture_series_hint) and exegesis >= 0.30:
        mode = "mixed"
        reasons.append("屬於釋經系列，但本講含有大量跨經文或問答式專題材料")
    elif exegesis < 0.25:
        mode = "topic_led"
        reasons.append("論述跨越多處經文，主要由議題而非連續經文推進")
    elif topical >= 0.50 and dominant < 0.38:
        mode = "topic_led"
        reasons.append("神學、背景或應用材料占主導，經文分布較分散")
    else:
        mode = "mixed"
        reasons.append("釋經推進與跨經文專題論述都占有實質篇幅")

    boundary_distance = min(
        abs(exegesis - 0.35),
        abs(dominant - 0.42),
        abs(dominant - 0.25),
    )
    if mode == "mixed":
        confidence = "medium"
    elif boundary_distance < 0.06:
        confidence = "medium"
    else:
        confidence = "high"

    reasons.append(f"釋經段落占比 {exegesis:.0%}；主要經文集中度 {dominant:.0%}")
    return mode, confidence, "；".join(reasons), metrics


def _scripture_catalog_eligibility(
    mode: str,
    primary_passage: dict[str, Any] | None,
    metadata: dict[str, Any],
    series: dict[str, Any],
) -> tuple[bool, str | None]:
    """Decide Bible-directory membership independently from discourse mode.

    A lecture can be topical in its internal structure while still belonging to
    an explicitly named expository series.  The Bible directory is an index,
    not a second copy of the mutually exclusive discourse classification.
    """

    if not primary_passage:
        return False, None
    title_blob = " ".join(str(value or "") for value in (metadata.get("title"), metadata.get("theme")))
    series_blob = " ".join(str(value or "") for value in (series.get("series_id"), series.get("series_title")))
    explicit_expository_context = bool(re.search(r"釋經|释经|查經|查经", f"{title_blob} {series_blob}"))
    if explicit_expository_context:
        return True, "屬於明確標示的釋經或查經系列"
    if mode in {"scripture_led", "mixed"}:
        return True, "本講以經文推進，或釋經與專題並重"
    return False, None


def _topic_labels(survey: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    texts: list[str] = [str(metadata.get("theme") or ""), str(metadata.get("summary") or "")]
    for cluster in survey.get("content_clusters") or []:
        texts.extend(
            [
                str(cluster.get("title") or ""),
                str(cluster.get("summary") or ""),
                " ".join(str(item) for item in cluster.get("topic_terms") or []),
            ]
        )
    for claim in survey.get("candidate_claims") or []:
        texts.append(str(claim.get("statement") or ""))
    taxonomy_path = DATA_BASE_PATH / "sermon_search" / "topic_taxonomy.json"
    if taxonomy_path.is_file():
        try:
            taxonomy = _load_json(taxonomy_path)
            curated = {
                str(item.get("label")): tuple(str(alias) for alias in item.get("aliases") or [])
                for item in taxonomy.get("topics") or []
                if item.get("label") and item.get("source") != "topic_index"
            }
            blob = "\n".join(texts).lower()
            return [
                label
                for label, aliases in curated.items()
                if any(candidate.lower() in blob for candidate in (label, *aliases))
            ][:8]
        except (OSError, json.JSONDecodeError):
            pass
    return extract_topics(texts)[:8]


def _metadata_refs(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in metadata.get("core_bible_verse") or []:
        if not isinstance(item, dict):
            continue
        book = str(item.get("book") or "").strip()
        chapter_verse = str(item.get("chapter_verse") or "").strip()
        ref = normalize_ref(f"{book} {chapter_verse}".strip())
        if ref and ref.osis not in seen:
            refs.append(ref.model_dump(mode="json"))
            seen.add(ref.osis)
    return refs


def _display_ref(ref: dict[str, Any]) -> str:
    chapter = ref["chapter_start"]
    start = ref.get("verse_start")
    end_chapter = ref.get("chapter_end")
    end = ref.get("verse_end")
    label = f'{ref.get("book_zh") or ref["book"]} {chapter}'
    if start is not None:
        label += f":{start}"
    if end is not None:
        if end_chapter and end_chapter != chapter:
            label += f"–{end_chapter}:{end}"
        elif end != start:
            label += f"–{end}"
    return label


def _passage_payload(ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "osis": ref["osis"],
        "book_osis": ref["book"],
        "book": ref.get("book_zh") or ref["book"],
        "chapter": ref["chapter_start"],
        "verse_start": ref.get("verse_start"),
        "chapter_end": ref.get("chapter_end"),
        "verse_end": ref.get("verse_end"),
        "display": _display_ref(ref),
    }


def _normalize_passage(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        value = value.get("osis") or value.get("display")
    ref = normalize_ref(str(value or "").strip())
    return _passage_payload(ref.model_dump(mode="json")) if ref else None


def _catalog_overrides(payload: Any) -> dict[str, dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("sermons"), dict):
        return {
            str(transcript_id): value
            for transcript_id, value in payload["sermons"].items()
            if isinstance(value, dict)
        }
    return {}


def _catalog_primary_passage(
    refs: list[dict[str, Any]],
    ref_weights: Counter,
    metrics: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the one passage that owns the sermon in the Scripture catalog.

    A sermon may cite many books.  The Scripture catalog must not duplicate it
    under every citation, so ownership follows the dominant exegetical locus
    found in the content survey rather than the first manually listed verse.
    """

    locus = metrics.get("dominant_locus") or {}
    book = locus.get("book")
    chapter = locus.get("chapter")
    if not book or chapter is None:
        return None

    candidates = [
        ref
        for ref in refs
        if ref.get("book") == book and ref.get("chapter_start") == chapter
    ]
    if not candidates:
        return None

    candidates.sort(
        key=lambda ref: (
            -float(ref_weights.get(ref.get("osis"), 0.0)),
            int(ref.get("verse_start") or 0),
            str(ref.get("osis") or ""),
        )
    )
    primary = candidates[0]
    return _passage_payload(primary)


def build_catalog(
    survey_dir: Path = DEFAULT_SURVEY_DIR,
    *,
    metadata_path: Path | None = None,
    series_path: Path | None = None,
    overrides_path: Path | None = None,
) -> dict[str, Any]:
    metadata_path = metadata_path or DATA_BASE_PATH / "config" / "sermon.json"
    series_path = series_path or DATA_BASE_PATH / "config" / "sermon_series.json"
    overrides_path = overrides_path or DEFAULT_OVERRIDES_PATH
    metadata_payload = _load_json(metadata_path) if metadata_path.is_file() else []
    series_payload = _load_json(series_path) if series_path.is_file() else []
    overrides_payload = _load_json(overrides_path) if overrides_path.is_file() else {}
    metadata_by_id = {str(item.get("item")): item for item in metadata_payload if item.get("item")}
    series_by_id = _series_lookup(series_payload)
    overrides_by_id = _catalog_overrides(overrides_payload)

    records: list[dict[str, Any]] = []
    survey_fingerprints: dict[str, str] = {}
    for path in sorted(survey_dir.glob("*.first-pass.json")):
        survey = _load_json(path)
        source = survey.get("source") or {}
        transcript_id = str(source.get("transcript_id") or "").strip()
        if not transcript_id:
            continue
        metadata = metadata_by_id.get(transcript_id, {})
        series = series_by_id.get(transcript_id, {})
        refs, ref_weights = _canonical_refs(survey.get("content_clusters") or [])
        mode, confidence, reason, metrics = classify_sermon(survey, metadata, series)
        catalog_primary_passage = _catalog_primary_passage(refs, ref_weights, metrics)
        catalog_assignment = "automatic"
        catalog_assignment_note = None
        substantial_passages: list[dict[str, Any]] = []
        override = overrides_by_id.get(transcript_id, {})
        if override:
            overridden_primary = _normalize_passage(override.get("catalog_primary_passage"))
            if overridden_primary:
                catalog_primary_passage = overridden_primary
                catalog_assignment = "reviewed_override"
                catalog_assignment_note = override.get("reason")
            substantial_passages = [
                passage
                for value in override.get("substantial_passages") or []
                if (passage := _normalize_passage(value)) is not None
            ]
        scripture_catalog_eligible, scripture_catalog_reason = _scripture_catalog_eligibility(
            mode,
            catalog_primary_passage,
            metadata,
            series,
        )
        ordered_refs = sorted(refs, key=lambda item: (-ref_weights[item["osis"]], item["osis"]))
        manual_refs = _metadata_refs(metadata)
        ordered_refs = manual_refs + [
            ref for ref in ordered_refs if ref["osis"] not in {manual["osis"] for manual in manual_refs}
        ]
        role_osis = {
            passage["osis"]
            for passage in [catalog_primary_passage, *substantial_passages]
            if passage
        }
        supporting_passages = [
            _passage_payload(ref) for ref in ordered_refs if ref["osis"] not in role_osis
        ]
        role_order = [
            passage for passage in [catalog_primary_passage, *substantial_passages, *supporting_passages] if passage
        ]
        primary_refs = role_order[:5]
        deliver_date = metadata.get("deliver_date")
        year_match = re.match(r"(19|20)\d{2}", str(deliver_date or ""))
        normalized_source = normalize_sermon_source(
            metadata if transcript_id in metadata_by_id else None,
            series,
        )
        record = {
            "transcript_id": transcript_id,
            "title": metadata.get("title") or transcript_id,
            "organization_mode": mode,
            "organization_mode_label": MODE_LABELS[mode],
            "classification_confidence": confidence,
            "classification_reason": reason,
            "classification_metrics": metrics,
            "primary_scriptures": [ref["display"] for ref in primary_refs],
            "canonical_scriptures": [ref["osis"] for ref in primary_refs],
            "catalog_primary_passage": catalog_primary_passage,
            "substantial_passages": substantial_passages,
            "supporting_passages": supporting_passages[:5],
            "catalog_assignment": catalog_assignment,
            "catalog_assignment_note": catalog_assignment_note,
            "scripture_catalog_eligible": scripture_catalog_eligible,
            "scripture_catalog_reason": scripture_catalog_reason,
            "books": list(dict.fromkeys(ref.get("book_zh") or ref["book"] for ref in ordered_refs)),
            "topics": _topic_labels(survey, metadata),
            "series_id": series.get("series_id") or metadata.get("series_id"),
            "series_title": series.get("series_title"),
            "series_order": series.get("series_order"),
            "deliver_date": deliver_date,
            "year": int(year_match.group(0)) if year_match else None,
            **normalized_source,
            "source_stage": source.get("publication_status"),
            "source_sha256": source.get("sha256"),
            "survey_path": str(path.resolve()),
        }
        records.append(record)
        survey_fingerprints[transcript_id] = _sha256_json(survey)

    counts = Counter(record["organization_mode"] for record in records)
    metadata_ids = set(metadata_by_id)
    catalog_ids = {record["transcript_id"] for record in records}
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "classifier_version": CLASSIFIER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "survey_dir": str(survey_dir.resolve()),
            "metadata_path": str(metadata_path.resolve()),
            "series_path": str(series_path.resolve()),
            "overrides_path": str(overrides_path.resolve()),
            "survey_fingerprints_sha256": _sha256_json(survey_fingerprints),
        },
        "summary": {
            "survey_count": len(records),
            "website_metadata_count": len(metadata_by_id),
            "matched_website_count": len(catalog_ids & metadata_ids),
            "survey_only_count": len(catalog_ids - metadata_ids),
            "metadata_only_count": len(metadata_ids - catalog_ids),
            "mode_counts": dict(sorted(counts.items())),
        },
        "records": records,
    }


def write_catalog(payload: dict[str, Any], output_path: Path = DEFAULT_OUTPUT_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output_path)
    return output_path
