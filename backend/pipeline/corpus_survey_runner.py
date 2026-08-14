"""Run the lightweight first-pass survey over published or reviewed Wang sermon transcripts.

This runner intentionally creates *candidate* observations only.  It never
approves a claim, creates a cross-sermon conclusion, or publishes a unit.
It is resumable: an already valid survey is skipped only when source, prompt,
model, generation settings, and response schema share the same extraction
fingerprint, unless ``--force`` is passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.pipeline.corpus_survey import SurveyValidationError, validate_survey
from backend.pipeline.stage1 import Stage1OpenAIClient


DEFAULT_TRANSCRIPT_DIR = Path("/opt/homebrew/var/www/church/web/data/script_published")
DEFAULT_OUTPUT_DIR = Path("output/corpus-survey")
PROMPT_PATH = Path("backend/pipeline/prompts/corpus_first_pass_survey.md")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SURVEY_VERSION = "wang_corpus_first_pass_v1"
EXTRACTION_SCHEMA_VERSION = "wang_corpus_first_pass_content_v1"


SURVEY_RESPONSE_SCHEMA: dict[str, Any] = {
    "name": "wang_corpus_first_pass_content_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "content_clusters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "cluster_id": {"type": "string"},
                        "title": {"type": "string"},
                        "function": {
                            "type": "string",
                            "enum": ["exegesis", "theology", "application", "method", "background", "interaction", "non_substantive"],
                        },
                        "summary": {"type": "string"},
                        # Published transcripts use both numeric indexes and
                        # composite indexes such as "1_27".  Preserve the
                        # source identifier verbatim rather than forcing the
                        # model to invent a numeric substitute.
                        "segment_indexes": {"type": "array", "items": {"type": "string"}},
                        "scripture_refs": {"type": "array", "items": {"type": "string"}},
                        "topic_terms": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "cluster_id", "title", "function", "summary", "segment_indexes", "scripture_refs", "topic_terms"
                    ],
                },
            },
            "candidate_claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "claim_id": {"type": "string"},
                        "statement": {"type": "string"},
                        "claim_kind": {
                            "type": "string",
                            "enum": ["explicit_claim", "reasoning_conclusion", "interpretive_method", "opposed_view", "question", "application"],
                        },
                        "attribution": {
                            "type": "string",
                            "enum": ["explicit", "close_paraphrase", "editorial_inference"],
                        },
                        "cluster_ids": {"type": "array", "items": {"type": "string"}},
                        "scripture_refs": {"type": "array", "items": {"type": "string"}},
                        "relations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "type": {"type": "string", "enum": ["supports", "answers", "opposes", "qualifies", "applies"]},
                                    "target_claim_id": {"type": "string"},
                                },
                                "required": ["type", "target_claim_id"],
                            },
                        },
                        "anchors": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "segment_index": {"type": "string"},
                        "start_time": {"type": ["number", "null"]},
                        "end_time": {"type": ["number", "null"]},
                                    "verbatim_excerpt": {"type": "string"},
                                },
                                "required": ["segment_index", "start_time", "end_time", "verbatim_excerpt"],
                            },
                        },
                        "review_status": {"type": "string", "enum": ["candidate"]},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": [
                        "claim_id", "statement", "claim_kind", "attribution", "cluster_ids", "scripture_refs",
                        "relations", "anchors", "review_status", "confidence"
                    ],
                },
            },
        },
        "required": ["content_clusters", "candidate_claims"],
    },
}


def _slug(transcript_id: str) -> str:
    """Return a human-readable, collision-proof output basename.

    Chinese-only titles previously collapsed to the same ASCII slug (for
    example, several annual Matthew lecture titles all became
    ``2016_NYSC_1``).  Preserve the readable portion but always suffix a
    stable hash of the original ID.
    """
    value = re.sub(r"[^0-9A-Za-z]+", "_", transcript_id).strip("_")
    prefix = value[:72] or "transcript"
    return f"{prefix}-{hashlib.sha256(transcript_id.encode('utf-8')).hexdigest()[:12]}"


def _load(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    parsed = json.loads(raw)
    if isinstance(parsed, list):
        return {
            "metadata": {
                "title": path.stem,
                "status": "reviewed",
            },
            "script": parsed,
        }, raw
    if not isinstance(parsed, dict):
        raise ValueError(f"{path}: transcript JSON must be an object or an array")
    return parsed, raw


def _segment_locator(position: int) -> str:
    """Stable survey-local ID for one physical transcript segment."""
    return f"S{position + 1:04d}"


def _has_duplicate_segment_ids(payload: dict[str, Any]) -> bool:
    indexes = [str(item.get("index")) for item in payload.get("script", [])]
    return len(indexes) != len(set(indexes))


def _uses_unique_segment_locators(survey_path: Path) -> bool:
    """Return whether a legacy survey already uses unambiguous locators."""
    try:
        survey = json.loads(survey_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    references: list[Any] = []
    for cluster in survey.get("content_clusters", []):
        references.extend(cluster.get("segment_indexes", []))
    for claim in survey.get("candidate_claims", []):
        references.extend(anchor.get("segment_index") for anchor in claim.get("anchors", []))
    return bool(references) and all(re.fullmatch(r"S\d{4,}", str(value)) for value in references)


def _existing_output(
    output_dir: Path,
    transcript_id: str,
    extraction_fingerprint: str,
) -> Path | None:
    canonical_path = output_dir / f"{_slug(transcript_id)}.first-pass.json"
    if canonical_path.exists():
        try:
            source = json.loads(canonical_path.read_text(encoding="utf-8")).get("source", {})
        except json.JSONDecodeError:
            source = {}
        extraction = json.loads(canonical_path.read_text(encoding="utf-8")).get("extraction", {})
        if (
            source.get("transcript_id") == transcript_id
            and extraction.get("fingerprint_sha256") == extraction_fingerprint
        ):
            return canonical_path
    for survey_path in output_dir.glob("*.first-pass.json"):
        try:
            source = json.loads(survey_path.read_text(encoding="utf-8")).get("source", {})
        except json.JSONDecodeError:
            continue
        extraction = json.loads(survey_path.read_text(encoding="utf-8")).get("extraction", {})
        if (
            source.get("transcript_id") == transcript_id
            and extraction.get("fingerprint_sha256") == extraction_fingerprint
        ):
            # Upgrade legacy collision-prone names without changing content.
            if survey_path != canonical_path:
                survey_path.replace(canonical_path)
            return canonical_path
    return None


def _transcript_for_prompt(payload: dict[str, Any]) -> str:
    rows: list[str] = []
    for position, segment in enumerate(payload.get("script", [])):
        rows.append(
            "[segment {locator}; source_index={index}; {start}-{end}]\n{text}".format(
                locator=_segment_locator(position),
                index=segment.get("index"),
                start=segment.get("start_time"),
                end=segment.get("end_time"),
                text=segment.get("text", ""),
            )
        )
    return "\n\n".join(rows)


def _extraction_metadata(
    *,
    source_sha256: str,
    system_prompt: str,
    model_id: str,
    reasoning_effort: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    response_schema_sha256 = hashlib.sha256(
        json.dumps(SURVEY_RESPONSE_SCHEMA, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    generation_identity = {
        "prompt_sha256": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
        "model_id": model_id,
        "reasoning_effort": reasoning_effort,
        "max_output_tokens": max_output_tokens,
        "schema_version": EXTRACTION_SCHEMA_VERSION,
        "response_schema_sha256": response_schema_sha256,
    }
    generation_fingerprint = hashlib.sha256(
        json.dumps(
            generation_identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    identity = {
        "source_sha256": source_sha256,
        **generation_identity,
        "generation_fingerprint_sha256": generation_fingerprint,
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        **identity,
        "fingerprint_sha256": fingerprint,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _archive_superseded_output(output_path: Path) -> Path | None:
    """Preserve the previous extraction before replacing its canonical slot."""
    if not output_path.exists():
        return None
    try:
        previous = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = {}
    extraction = previous.get("extraction") or {}
    source = previous.get("source") or {}
    fingerprint = extraction.get("fingerprint_sha256")
    if not fingerprint:
        source_hash = str(source.get("sha256") or "unknown")
        fingerprint = f"legacy-{source_hash[:16]}"
    archive_dir = output_path.parent / "generations"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{output_path.stem}.{fingerprint}.json"
    if not archive_path.exists():
        shutil.copy2(output_path, archive_path)
    return archive_path


def _make_survey(
    transcript_id: str,
    path: Path,
    payload: dict[str, Any],
    raw: bytes,
    response: dict[str, Any],
    extraction: dict[str, Any],
) -> dict[str, Any]:
    claims = response["candidate_claims"]
    for claim in claims:
        claim["extraction_fingerprint"] = extraction["fingerprint_sha256"]
    return {
        "survey_version": SURVEY_VERSION,
        "source": {
            "transcript_id": transcript_id,
            "path": str(path),
            "publication_status": payload.get("metadata", {}).get("status"),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "segment_count": len(payload.get("script", [])),
        },
        "extraction": extraction,
        "content_clusters": response["content_clusters"],
        "candidate_claims": claims,
        "survey_summary": {
            "cluster_count": len(response["content_clusters"]),
            "candidate_claim_count": len(claims),
            "high_confidence_claim_count": sum(item["confidence"] == "high" for item in claims),
            "medium_confidence_claim_count": sum(item["confidence"] == "medium" for item in claims),
            "editorial_inference_count": sum(item["attribution"] == "editorial_inference" for item in claims),
        },
    }


def _preserve_exact_anchor_fallbacks(survey: dict[str, Any], transcript: dict[str, Any]) -> int:
    """Resolve mechanical anchor-copying errors without changing the claim.

    This is not a semantic repair: the model-selected segment remains fixed.
    The model may choose the correct segment but copy its time range
    imprecisely.  Time ranges therefore always come from the selected source
    segment, never from model output.  If it instead supplies a non-existent
    segment number, it is remapped only when its exact excerpt identifies one
    and only one real segment.  A non-exact quotation still falls back to the
    complete chosen segment and carries a review warning.
    """
    script = transcript.get("script", [])
    segments = {_segment_locator(position): item for position, item in enumerate(script)}
    # Backward compatibility for already accepted v1 cards: an original
    # segment ID is usable only when it occurs exactly once.
    original_counts: dict[str, int] = {}
    for item in script:
        key = str(item.get("index"))
        original_counts[key] = original_counts.get(key, 0) + 1
    for item in script:
        key = str(item.get("index"))
        if original_counts[key] == 1:
            segments[key] = item
    fallback_count = 0
    for claim in survey.get("candidate_claims", []):
        for anchor in claim.get("anchors", []):
            exact = anchor.get("verbatim_excerpt")
            segment = segments.get(str(anchor.get("segment_index")))
            if segment is None and isinstance(exact, str) and exact:
                matches = [
                    (position, candidate)
                    for position, candidate in enumerate(script)
                    if exact in candidate.get("text", "")
                ]
                if len(matches) == 1:
                    matched_position, segment = matches[0]
                    anchor["segment_index"] = _segment_locator(matched_position)
                    anchor["anchor_resolution"] = "remapped_by_unique_exact_excerpt"
            if segment is None:
                continue
            source_position = next(
                (position for position, item in enumerate(script) if item is segment),
                None,
            )
            if source_position is not None:
                anchor["segment_index"] = _segment_locator(source_position)
                anchor["source_segment_index"] = segment.get("index")
                anchor["source_segment_ordinal"] = source_position
            # A source timestamp is metadata, not an AI judgment.  Normalize
            # it even when the quotation itself is already exact.
            if (
                anchor.get("start_time") != segment.get("start_time")
                or anchor.get("end_time") != segment.get("end_time")
            ):
                anchor["start_time"] = segment.get("start_time")
                anchor["end_time"] = segment.get("end_time")
                anchor.setdefault("anchor_resolution", "time_derived_from_source_segment")
            if isinstance(exact, str) and exact and exact in segment.get("text", ""):
                continue
            anchor["verbatim_excerpt"] = segment.get("text", "")
            anchor["start_time"] = segment.get("start_time")
            anchor["end_time"] = segment.get("end_time")
            anchor["anchor_resolution"] = "segment_fallback_due_to_nonexact_model_quote"
            claim.setdefault("review_warnings", []).append(
                "一个来源锚点暂以完整 segment 保存；公开使用前应收紧为逐字片段。"
            )
            fallback_count += 1
    return fallback_count


def _eligible_paths(
    transcript_dir: Path,
    ids: set[str] | None,
    source_stages: set[str],
    exclude_transcript_dir: Path | None = None,
) -> list[Path]:
    excluded_ids = (
        {path.stem for path in exclude_transcript_dir.glob("*.json")}
        if exclude_transcript_dir is not None
        else set()
    )
    paths: list[Path] = []
    for path in sorted(transcript_dir.glob("*.json")):
        if ids is not None and path.stem not in ids:
            continue
        if path.stem in excluded_ids:
            continue
        try:
            payload, _ = _load(path)
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("metadata", {}).get("status") in source_stages:
            paths.append(path)
    return paths


def run_one(
    path: Path,
    *,
    output_dir: Path,
    client: Stage1OpenAIClient,
    system_prompt: str,
    model_id: str,
    reasoning_effort: str,
    max_output_tokens: int,
    force: bool,
) -> tuple[str, Path | None]:
    payload, raw = _load(path)
    transcript_id = path.stem
    source_hash = hashlib.sha256(raw).hexdigest()
    extraction = _extraction_metadata(
        source_sha256=source_hash,
        system_prompt=system_prompt,
        model_id=model_id,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
    )
    existing = _existing_output(
        output_dir, transcript_id, extraction["fingerprint_sha256"]
    )
    if existing and _has_duplicate_segment_ids(payload) and not _uses_unique_segment_locators(existing):
        # Cards created before survey-local locators cannot distinguish two
        # physical segments that share the same source index.  Regenerate
        # only these affected transcripts.
        existing = None
    if existing and not force:
        return "skipped", existing

    user_prompt = (
        f"逐字稿 ID：{transcript_id}\n"
        f"标题：{payload.get('metadata', {}).get('title', transcript_id)}\n\n"
        "每段开头的 S0001、S0002 等是本次普查唯一定位码。content_clusters.segment_indexes "
        "及 anchors.segment_index 必须逐字使用这些 S 编号，不要使用 source_index。\n\n"
        "以下是完整逐字稿。请依据系统提示输出 JSON。\n\n"
        + _transcript_for_prompt(payload)
    )
    # Exact source anchors are a non-negotiable constraint.  A model may
    # occasionally normalize punctuation while copying Chinese transcript
    # text, so give it one targeted correction pass.  We never fuzzy-repair
    # anchors in code: an unresolvable mismatch remains a failed survey.
    validation_error: SurveyValidationError | None = None
    survey: dict[str, Any] | None = None
    for attempt in range(2):
        attempt_feedback = ""
        if validation_error is not None:
            attempt_feedback = (
                "\n\n上一版输出未通过机械验证，原因是："
                f"{validation_error}。请重新输出完整 JSON。特别注意：每个 verbatim_excerpt 必须从指定 segment 的原文逐字复制，"
                "不可改写、不可补标点、不可省略字。"
            )
        response = client.generate_json(
            system_prompt, attempt_feedback, SURVEY_RESPONSE_SCHEMA, cache_prefix=user_prompt
        )
        candidate = _make_survey(
            transcript_id, path, payload, raw, response, extraction
        )
        _preserve_exact_anchor_fallbacks(candidate, payload)
        try:
            validate_survey(
                candidate,
                payload,
                raw,
                expected_extraction_fingerprint=extraction["fingerprint_sha256"],
            )
        except SurveyValidationError as exc:
            validation_error = exc
            continue
        survey = candidate
        break
    if survey is None:
        raise validation_error or SurveyValidationError("survey validation failed")
    output_path = output_dir / f"{_slug(transcript_id)}.first-pass.json"
    _archive_superseded_output(output_path)
    output_path.write_text(json.dumps(survey, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "created", output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript-dir", type=Path, default=DEFAULT_TRANSCRIPT_DIR)
    parser.add_argument(
        "--source-stages",
        nargs="+",
        choices=["published", "reviewed"],
        default=["published"],
        help="Eligible source workflow stages. Array-form script_review files are treated as reviewed.",
    )
    parser.add_argument(
        "--exclude-transcript-dir",
        type=Path,
        help="Skip transcript IDs already present in another workflow-stage directory.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--pause-seconds", type=float, default=0.5)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=6000,
        help="Completion-token budget per transcript; use a larger value for long transcripts.",
    )
    args = parser.parse_args()

    # Stage1OpenAIClient expects the key in the process environment.  This
    # command is also used outside the API server, so load the project config
    # explicitly rather than relying on server startup side effects.
    load_dotenv(PROJECT_ROOT / ".env")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    ids = set(args.ids) if args.ids else None
    paths = _eligible_paths(
        args.transcript_dir,
        ids,
        set(args.source_stages),
        args.exclude_transcript_dir,
    )
    if args.limit is not None:
        paths = paths[: args.limit]
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    client = Stage1OpenAIClient(
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        timeout_seconds=180,
        max_retries=3,
        # This is a lightweight observation card, not a full evidence
        # inventory.  A bounded response prevents a routine survey from
        # spending its budget recreating the transcript in prose.
        max_output_tokens=args.max_output_tokens,
    )

    counts = {"created": 0, "skipped": 0, "failed": 0}
    for position, path in enumerate(paths, start=1):
        try:
            result, output = run_one(
                path,
                output_dir=output_dir,
                client=client,
                system_prompt=system_prompt,
                model_id=args.model,
                reasoning_effort=args.reasoning_effort,
                max_output_tokens=args.max_output_tokens,
                force=args.force,
            )
            counts[result] += 1
            print(f"[{position}/{len(paths)}] {result}: {path.name} -> {output or ''}")
        except (SurveyValidationError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            counts["failed"] += 1
            print(f"[{position}/{len(paths)}] FAILED: {path.name}: {exc}")
        if position < len(paths) and args.pause_seconds:
            time.sleep(args.pause_seconds)
    print(json.dumps(counts, ensure_ascii=False))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
