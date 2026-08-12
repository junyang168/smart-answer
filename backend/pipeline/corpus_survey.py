from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ALLOWED_CLUSTER_FUNCTIONS = {
    "exegesis",
    "theology",
    "application",
    "method",
    "background",
    "interaction",
    "non_substantive",
}
ALLOWED_CLAIM_KINDS = {
    "explicit_claim",
    "reasoning_conclusion",
    "interpretive_method",
    "opposed_view",
    "question",
    "application",
}
ALLOWED_ATTRIBUTIONS = {"explicit", "close_paraphrase", "editorial_inference"}
ALLOWED_RELATIONS = {"supports", "answers", "opposes", "qualifies", "applies"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}


class SurveyValidationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SurveyValidationError(message)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(payload, dict), f"{path}: top-level JSON must be an object")
    return payload


def validate_survey(
    survey: dict[str, Any],
    transcript: dict[str, Any],
    raw_source: bytes,
    expected_extraction_fingerprint: str | None = None,
) -> None:
    _require(survey.get("survey_version") == "wang_corpus_first_pass_v1", "unsupported survey_version")

    source = survey.get("source") or {}
    script = transcript.get("script") or []
    # Survey-local locators address physical segments unambiguously even when
    # a legacy transcript repeats its own ``index`` value.  Unique original
    # IDs remain accepted for backward compatibility with earlier v1 cards.
    segments = {f"S{position + 1:04d}": segment for position, segment in enumerate(script)}
    original_counts: dict[str, int] = {}
    for segment in script:
        key = str(segment.get("index"))
        original_counts[key] = original_counts.get(key, 0) + 1
    for segment in script:
        key = str(segment.get("index"))
        if original_counts[key] == 1:
            segments[key] = segment
    _require(
        source.get("publication_status") in {"published", "reviewed"},
        "first-pass samples must be published or reviewed",
    )
    _require(source.get("segment_count") == len(script), "source.segment_count does not match transcript")
    _require(source.get("sha256") == hashlib.sha256(raw_source).hexdigest(), "source SHA256 mismatch")

    extraction = survey.get("extraction")
    if expected_extraction_fingerprint is not None:
        _require(isinstance(extraction, dict), "missing extraction metadata")
    if isinstance(extraction, dict):
        identity_keys = (
            "source_sha256",
            "prompt_sha256",
            "model_id",
            "reasoning_effort",
            "max_output_tokens",
            "schema_version",
            "response_schema_sha256",
            "generation_fingerprint_sha256",
        )
        identity = {key: extraction.get(key) for key in identity_keys}
        computed = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        _require(extraction.get("source_sha256") == source.get("sha256"), "extraction source SHA256 mismatch")
        generation_identity = {
            key: extraction.get(key)
            for key in (
                "prompt_sha256",
                "model_id",
                "reasoning_effort",
                "max_output_tokens",
                "schema_version",
                "response_schema_sha256",
            )
        }
        generation_computed = hashlib.sha256(
            json.dumps(
                generation_identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        _require(
            extraction.get("generation_fingerprint_sha256") == generation_computed,
            "extraction generation fingerprint mismatch",
        )
        _require(extraction.get("fingerprint_sha256") == computed, "extraction fingerprint mismatch")
        if expected_extraction_fingerprint is not None:
            _require(computed == expected_extraction_fingerprint, "unexpected extraction generation")

    clusters = survey.get("content_clusters") or []
    cluster_ids = [cluster.get("cluster_id") for cluster in clusters]
    _require(len(cluster_ids) == len(set(cluster_ids)), "duplicate cluster_id")
    for cluster in clusters:
        cluster_id = cluster.get("cluster_id")
        _require(cluster.get("function") in ALLOWED_CLUSTER_FUNCTIONS, f"{cluster_id}: invalid function")
        for segment_index in cluster.get("segment_indexes") or []:
            _require(str(segment_index) in segments, f"{cluster_id}: unknown segment {segment_index}")

    claims = survey.get("candidate_claims") or []
    claim_ids = [claim.get("claim_id") for claim in claims]
    _require(len(claim_ids) == len(set(claim_ids)), "duplicate claim_id")
    claim_id_set = set(claim_ids)
    cluster_id_set = set(cluster_ids)

    for claim in claims:
        claim_id = claim.get("claim_id")
        _require(claim.get("claim_kind") in ALLOWED_CLAIM_KINDS, f"{claim_id}: invalid claim_kind")
        _require(claim.get("attribution") in ALLOWED_ATTRIBUTIONS, f"{claim_id}: invalid attribution")
        _require(claim.get("review_status") == "candidate", f"{claim_id}: review_status must be candidate")
        _require(claim.get("confidence") in ALLOWED_CONFIDENCE, f"{claim_id}: invalid confidence")
        _require(bool(claim.get("anchors")), f"{claim_id}: at least one anchor is required")
        if isinstance(extraction, dict):
            _require(
                claim.get("extraction_fingerprint") == extraction.get("fingerprint_sha256"),
                f"{claim_id}: extraction fingerprint mismatch",
            )

        for cluster_id in claim.get("cluster_ids") or []:
            _require(cluster_id in cluster_id_set, f"{claim_id}: unknown cluster {cluster_id}")

        for relation in claim.get("relations") or []:
            relation_type = relation.get("type")
            target = relation.get("target_claim_id")
            _require(relation_type in ALLOWED_RELATIONS, f"{claim_id}: invalid relation {relation_type}")
            _require(target in claim_id_set, f"{claim_id}: unknown relation target {target}")
            _require(target != claim_id, f"{claim_id}: self relation is not allowed")

        for anchor in claim.get("anchors") or []:
            segment_index = str(anchor.get("segment_index"))
            segment = segments.get(segment_index)
            _require(segment is not None, f"{claim_id}: unknown anchor segment {segment_index}")
            excerpt = anchor.get("verbatim_excerpt")
            _require(isinstance(excerpt, str) and excerpt, f"{claim_id}: empty verbatim_excerpt")
            _require(excerpt in segment.get("text", ""), f"{claim_id}: excerpt is not exact in segment {segment_index}")
            _require(anchor.get("start_time") == segment.get("start_time"), f"{claim_id}: start_time mismatch")
            _require(anchor.get("end_time") == segment.get("end_time"), f"{claim_id}: end_time mismatch")

    summary = survey.get("survey_summary") or {}
    _require(summary.get("cluster_count") == len(clusters), "survey_summary.cluster_count mismatch")
    _require(summary.get("candidate_claim_count") == len(claims), "survey_summary.candidate_claim_count mismatch")
    high_count = sum(claim.get("confidence") == "high" for claim in claims)
    medium_count = sum(claim.get("confidence") == "medium" for claim in claims)
    editorial_count = sum(claim.get("attribution") == "editorial_inference" for claim in claims)
    _require(summary.get("high_confidence_claim_count") == high_count, "high confidence count mismatch")
    _require(summary.get("medium_confidence_claim_count") == medium_count, "medium confidence count mismatch")
    _require(summary.get("editorial_inference_count") == editorial_count, "editorial inference count mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Wang corpus first-pass survey artifact.")
    parser.add_argument("--survey", required=True, type=Path)
    parser.add_argument("--transcript", required=True, type=Path)
    args = parser.parse_args()

    raw_source = args.transcript.read_bytes()
    validate_survey(_load_json(args.survey), json.loads(raw_source), raw_source)
    print(f"Valid first-pass survey: {args.survey}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
