"""Run an independent Claude review over generation-aware corpus surveys.

The runner never writes human approval. It produces AI-reviewed, changes-
requested, and human-review queues with deterministic spot-check sampling.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.config.wang_platform_paths import wang_platform_paths
from backend.pipeline.corpus_ai_review import (
    AI_REVIEW_RESPONSE_SCHEMA,
    AI_REVIEW_VERSION,
    AIReviewValidationError,
    apply_risk_routing,
    reviewer_fingerprint,
    validate_review_response,
)
from backend.pipeline.corpus_survey import validate_survey
from backend.pipeline.corpus_survey_runner import PROJECT_ROOT, _load, _slug, _transcript_for_prompt
from backend.pipeline.knowledge_source import load_knowledge_source_document
from backend.pipeline.llm_usage import usage_row, usage_summary
from backend.pipeline.stage1 import Stage1AnthropicClient


CORPUS_SURVEY_ROOT = wang_platform_paths().corpus_survey_staging
DEFAULT_SURVEY_DIR = CORPUS_SURVEY_ROOT
DEFAULT_OUTPUT_DIR = CORPUS_SURVEY_ROOT / "independent-review"
DEFAULT_TRANSCRIPT_DIRS = [
    Path("/opt/homebrew/var/www/church/web/data/script_published"),
    Path("/opt/homebrew/var/www/church/web/data/script_review"),
]
PROMPT_PATH = Path("backend/pipeline/prompts/corpus_independent_ai_review.md")
DEFAULT_CLAIM_LAYER_OUTPUT = (
    wang_platform_paths().claim_layer_staging / "independent_ai_review_v1.json"
)
CLAIM_LAYER_PROJECTION_VERSION = "wang_claim_layer_review_projection_v2"


def _find_transcript(transcript_id: str, transcript_dirs: list[Path]) -> Path:
    for directory in transcript_dirs:
        path = directory / f"{transcript_id}.json"
        if path.is_file():
            return path
    raise FileNotFoundError(f"transcript not found: {transcript_id}")


def _review_input(survey: dict[str, Any], transcript: dict[str, Any]) -> str:
    candidates = [
        {
            "claim_id": item["claim_id"],
            "statement": item.get("statement", ""),
            "claim_kind": item.get("claim_kind", ""),
            "attribution": item.get("attribution", ""),
            "scripture_refs": item.get("scripture_refs", []),
            "relations": item.get("relations", []),
            "anchors": item.get("anchors", []),
        }
        for item in survey.get("candidate_claims", [])
    ]
    return (
        f"逐字稿 ID：{survey['source']['transcript_id']}\n\n"
        "候选主张（来自另一个模型，仅供审查，不代表正确）：\n"
        + json.dumps(candidates, ensure_ascii=False, indent=2)
        + "\n\n完整逐字稿：\n"
        + _transcript_for_prompt(transcript)
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _archive_existing_review(output_path: Path) -> Path | None:
    """Keep every previous review, including a rerun of the same reviewer.

    The name carries the content hash as well as the reviewer fingerprint: two
    runs of one reviewer over one package share a fingerprint, and naming by
    fingerprint alone silently dropped the earlier of the two — which is
    precisely the pair you need when measuring how stable a review is.
    """
    if not output_path.is_file():
        return None
    raw = output_path.read_bytes()
    try:
        existing = json.loads(raw)
        fingerprint = str(
            (existing.get("reviewer") or {}).get("fingerprint_sha256") or "legacy"
        )[:12]
    except (json.JSONDecodeError, OSError):
        fingerprint = "unreadable"
    archive_dir = output_path.parent / "review-generations"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = (
        archive_dir / f"{output_path.stem}.{fingerprint}.{_sha256_bytes(raw)[:8]}.json"
    )
    if not archive_path.exists():
        shutil.copy2(output_path, archive_path)
    return archive_path


def _normalize_claim_layer(package: dict[str, Any]) -> dict[str, Any]:
    """Project the curated shared-knowledge package into reviewer input."""
    outgoing: dict[str, list[dict[str, str]]] = {}
    for relation in package.get("claim_relations", []):
        source_id = str(relation.get("from_id") or relation.get("source_id") or "")
        target_id = str(relation.get("to_id") or relation.get("target_id") or "")
        normalized_relation = {
            "type": str(relation.get("relation_type") or ""),
            "target_claim_id": target_id,
        }
        relation_id = str(
            relation.get("claim_relation_id") or relation.get("relation_id") or ""
        )
        if relation_id:
            normalized_relation["relation_id"] = relation_id
        outgoing.setdefault(source_id, []).append(normalized_relation)
    claims: list[dict[str, Any]] = []
    for claim in package.get("claims", []):
        anchors: list[dict[str, Any]] = []
        for occurrence in claim.get("occurrences", []):
            for anchor in occurrence.get("anchors", []):
                highlight = anchor.get("proposed_highlight") or {}
                anchors.append(
                    {
                        "transcript_id": occurrence.get("transcript_id"),
                        "lecture": occurrence.get("lecture"),
                        "paragraph_key": anchor.get("paragraph_key"),
                        "media_time": anchor.get("media_time"),
                        "evidence_id": anchor.get("evidence_id"),
                        "evidence_type": anchor.get("evidence_type"),
                        "speaker": anchor.get("speaker"),
                        "stance": anchor.get("stance"),
                        "discourse_role": anchor.get("discourse_role"),
                        "assertive": anchor.get("assertive"),
                        "verbatim_excerpt": highlight.get("text") or "",
                        "highlight_status": highlight.get("status"),
                        "review_note": highlight.get("review_note")
                        or highlight.get("rejection_reason"),
                    }
                )
        claims.append(
            {
                "claim_id": claim["claim_id"],
                "statement": claim.get("title", ""),
                "claim_kind": claim.get("claim_type", ""),
                "attribution": claim.get("attribution", "professor"),
                "scripture_refs": claim.get("scripture_refs", []),
                "relations": outgoing.get(claim["claim_id"], []),
                "anchors": anchors,
                "current_review_status": claim.get("review_status", "candidate"),
                "opposes": claim.get("opposes"),
            }
        )
    return {"candidate_claims": claims}


def _claim_layer_input(
    survey: dict[str, Any],
    source_documents: list[tuple[str, dict[str, Any]]],
) -> str:
    sections = []
    for source_id, source_document in source_documents:
        sections.append(
            f"===== 完整来源：{source_id} =====\n{_transcript_for_prompt(source_document)}"
        )
    return (
        "以下是已经精编的跨讲 claim layer。现有人工标签和状态只是待复核资料，"
        "不得因此默认正确。\n\n候选主张：\n"
        + json.dumps(survey["candidate_claims"], ensure_ascii=False, indent=2)
        + "\n\n"
        + "\n\n".join(sections)
    )


def _generate_valid_review(
    *,
    client: Stage1AnthropicClient,
    prompt: str,
    user_input: str,
    survey: dict[str, Any],
    validation_attempts: int = 2,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Retry a structurally invalid review without hiding the failed attempt.

    The usage rows come back alongside the review because a rejected attempt is
    billed too: counting only the accepted call would understate what a review
    of this size actually costs.
    """
    last_error: AIReviewValidationError | None = None
    feedback = ""
    usage_rows: list[dict[str, Any]] = []
    for attempt in range(1, validation_attempts + 1):
        response = client.generate_json(
            prompt,
            feedback,
            AI_REVIEW_RESPONSE_SCHEMA,
            cache_prefix=user_input,
        )
        usage_rows.append(usage_row(getattr(client, "last_usage", None), attempt))
        try:
            validate_review_response(response, survey)
            return response, usage_rows
        except AIReviewValidationError as exc:
            last_error = exc
            if attempt == validation_attempts:
                break
            feedback = (
                "\n\n上一次审阅结果未通过程序验证。错误："
                + str(exc)
                + "\n请重新输出一份完整 JSON；不得只输出修正片段，也不得遗漏任何 claim。"
            )
    raise AIReviewValidationError(
        f"review response remained invalid after {validation_attempts} attempts: {last_error}"
    )


def run_one(
    survey_path: Path,
    *,
    transcript_dirs: list[Path],
    output_dir: Path,
    client: Stage1AnthropicClient,
    prompt: str,
    spot_check_percent: int,
    force: bool,
) -> tuple[str, Path]:
    survey = json.loads(survey_path.read_text(encoding="utf-8"))
    transcript_id = str(survey.get("source", {}).get("transcript_id") or "")
    extraction_fingerprint = str(
        (survey.get("extraction") or {}).get("fingerprint_sha256") or ""
    )
    if not extraction_fingerprint:
        raise AIReviewValidationError(
            f"{transcript_id}: legacy survey has no extraction fingerprint"
        )
    transcript_path = _find_transcript(transcript_id, transcript_dirs)
    transcript, raw = _load(transcript_path)
    validate_survey(
        survey,
        transcript,
        raw,
        expected_extraction_fingerprint=extraction_fingerprint,
    )
    identity = reviewer_fingerprint(
        source_extraction_fingerprint=extraction_fingerprint,
        prompt=prompt,
        model_id=client.model,
        max_output_tokens=client.max_output_tokens,
    )
    output_path = output_dir / f"{_slug(transcript_id)}.independent-review.json"
    if output_path.is_file() and not force:
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        if (
            existing.get("reviewer", {}).get("fingerprint_sha256")
            == identity["fingerprint_sha256"]
            and existing.get("spot_check_percent") == spot_check_percent
        ):
            return "skipped", output_path

    response, usage_rows = _generate_valid_review(
        client=client,
        prompt=prompt,
        user_input=_review_input(survey, transcript),
        survey=survey,
    )
    routed = apply_risk_routing(
        response,
        reviewer_fingerprint_sha256=identity["fingerprint_sha256"],
        spot_check_percent=spot_check_percent,
    )
    artifact = {
        "schema_version": AI_REVIEW_VERSION,
        "source": {
            "transcript_id": transcript_id,
            "survey_path": str(survey_path),
            "transcript_path": str(transcript_path),
            "source_extraction_fingerprint": extraction_fingerprint,
        },
        "reviewer": {
            **identity,
            "provider": "anthropic",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "spot_check_percent": spot_check_percent,
        "usage": usage_rows,
        # Preserve the exact ordered anchor snapshot seen by the reviewer.
        # Ordinal anchor indexes are otherwise unsafe once a derived package is
        # rebuilt or an earlier override changes an occurrence.
        "reviewed_claims": survey["candidate_claims"],
        **routed,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _archive_existing_review(output_path)
    output_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return "created", output_path


def run_claim_layer(
    package_path: Path,
    *,
    transcript_dirs: list[Path],
    output_path: Path,
    client: Stage1AnthropicClient,
    prompt: str,
    spot_check_percent: int,
    force: bool,
) -> tuple[str, Path]:
    """Review the curated third/fourth-lecture package without re-extraction."""
    package_bytes = package_path.read_bytes()
    package = json.loads(package_bytes)
    survey = _normalize_claim_layer(package)
    if not survey["candidate_claims"]:
        raise AIReviewValidationError("claim-layer package has no claims")

    transcripts: list[tuple[str, dict[str, Any]]] = []
    transcript_hashes: dict[str, str] = {}
    transcript_paths: dict[str, str] = {}
    for source in package.get("source_documents", []):
        source_id = str(source.get("source_id") or source.get("transcript_id") or "")
        source_payload, raw, source_path = load_knowledge_source_document(
            source, transcript_dirs
        )
        transcripts.append((source_id, source_payload))
        transcript_hashes[source_id] = _sha256_bytes(raw)
        transcript_paths[source_id] = str(source_path)
    if not transcripts:
        raise AIReviewValidationError("claim-layer package has no source documents")

    source_identity = {
        "input_mode": "curated_claim_layer",
        "projection_version": CLAIM_LAYER_PROJECTION_VERSION,
        "package_sha256": _sha256_bytes(package_bytes),
        "transcript_sha256": transcript_hashes,
    }
    if package.get("review_batch"):
        source_identity["review_batch"] = copy.deepcopy(package["review_batch"])
    source_fingerprint = _sha256_bytes(
        json.dumps(
            source_identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    identity = reviewer_fingerprint(
        source_extraction_fingerprint=source_fingerprint,
        prompt=prompt,
        model_id=client.model,
        max_output_tokens=client.max_output_tokens,
    )
    if output_path.is_file() and not force:
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        if (
            existing.get("reviewer", {}).get("fingerprint_sha256")
            == identity["fingerprint_sha256"]
            and existing.get("spot_check_percent") == spot_check_percent
        ):
            return "skipped", output_path

    response, usage_rows = _generate_valid_review(
        client=client,
        prompt=prompt,
        user_input=_claim_layer_input(survey, transcripts),
        survey=survey,
    )
    routed = apply_risk_routing(
        response,
        reviewer_fingerprint_sha256=identity["fingerprint_sha256"],
        spot_check_percent=spot_check_percent,
    )
    artifact = {
        "schema_version": AI_REVIEW_VERSION,
        "source": {
            **source_identity,
            "package_path": str(package_path),
            "transcript_paths": transcript_paths,
            "source_candidate_fingerprint": source_fingerprint,
            "extraction_provenance": "curated package snapshot; original model generation metadata unavailable",
        },
        "reviewer": {
            **identity,
            "provider": "anthropic",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "spot_check_percent": spot_check_percent,
        "usage": usage_rows,
        "reviewed_claims": survey["candidate_claims"],
        **routed,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _archive_existing_review(output_path)
    output_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return "created", output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--survey-dir", type=Path, default=DEFAULT_SURVEY_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--transcript-dir", action="append", type=Path, dest="transcript_dirs")
    parser.add_argument("--model", default="claude-sonnet-5")
    # Sonnet 5 counts adaptive-thinking tokens inside max_tokens.  The two-
    # lecture claim-layer review consumed the old 10k ceiling before emitting
    # JSON, so the default must leave room for both reasoning and the schema.
    parser.add_argument("--max-output-tokens", type=int, default=32000)
    parser.add_argument("--spot-check-percent", type=int, default=10)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--claim-layer-package",
        type=Path,
        help="Review one curated shared-knowledge package instead of first-pass surveys.",
    )
    parser.add_argument(
        "--claim-layer-output",
        type=Path,
        default=DEFAULT_CLAIM_LAYER_OUTPUT,
    )
    args = parser.parse_args()
    if not 0 <= args.spot_check_percent <= 100:
        parser.error("--spot-check-percent must be between 0 and 100")

    transcript_dirs = args.transcript_dirs or DEFAULT_TRANSCRIPT_DIRS
    if args.claim_layer_package:
        package = json.loads(args.claim_layer_package.read_text(encoding="utf-8"))
        normalized = _normalize_claim_layer(package)
        source_records = list(package.get("source_documents", []))
        source_ids = [
            str(item.get("source_id") or item.get("transcript_id") or "")
            for item in source_records
        ]
        if args.dry_run:
            resolved = []
            for source in source_records:
                try:
                    load_knowledge_source_document(source, transcript_dirs)
                    resolved.append(True)
                except (FileNotFoundError, ValueError, json.JSONDecodeError):
                    resolved.append(False)
            print(
                json.dumps(
                    {
                        "input_mode": "curated_claim_layer",
                        "package": str(args.claim_layer_package),
                        "claims": len(normalized["candidate_claims"]),
                        "source_documents": source_ids,
                        "all_sources_resolved": all(resolved),
                        "model": args.model,
                        "would_call_anthropic": False,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        load_dotenv(PROJECT_ROOT / ".env")
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        client = Stage1AnthropicClient(
            model=args.model,
            timeout_seconds=240,
            max_retries=3,
            max_output_tokens=args.max_output_tokens,
        )
        status, output = run_claim_layer(
            args.claim_layer_package,
            transcript_dirs=transcript_dirs,
            output_path=args.claim_layer_output,
            client=client,
            prompt=prompt,
            spot_check_percent=args.spot_check_percent,
            force=args.force,
        )
        print(json.dumps({"status": status, "output": str(output)}, ensure_ascii=False))
        if status == "created":
            review = json.loads(output.read_text(encoding="utf-8"))
            print(json.dumps(
                usage_summary(str(args.claim_layer_package.name), review.get("usage") or []),
                ensure_ascii=False,
            ))
        return 0

    paths = sorted(args.survey_dir.glob("*.first-pass.json"))
    if args.ids:
        wanted = set(args.ids)
        paths = [
            path for path in paths
            if json.loads(path.read_text(encoding="utf-8")).get("source", {}).get("transcript_id") in wanted
        ]
    if args.limit is not None:
        paths = paths[: args.limit]
    if args.dry_run:
        generation_aware = 0
        legacy_blocked = 0
        for path in paths:
            survey = json.loads(path.read_text(encoding="utf-8"))
            if (survey.get("extraction") or {}).get("fingerprint_sha256"):
                generation_aware += 1
            else:
                legacy_blocked += 1
        print(
            json.dumps(
                {
                    "selected_surveys": len(paths),
                    "generation_aware_eligible": generation_aware,
                    "legacy_blocked": legacy_blocked,
                    "model": args.model,
                    "would_call_anthropic": False,
                },
                ensure_ascii=False,
            )
        )
        return 0

    load_dotenv(PROJECT_ROOT / ".env")
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    client = Stage1AnthropicClient(
        model=args.model,
        timeout_seconds=240,
        max_retries=3,
        max_output_tokens=args.max_output_tokens,
    )
    counts = {"created": 0, "skipped": 0, "failed": 0}
    for position, survey_path in enumerate(paths, start=1):
        try:
            status, output = run_one(
                survey_path,
                transcript_dirs=transcript_dirs,
                output_dir=args.output_dir,
                client=client,
                prompt=prompt,
                spot_check_percent=args.spot_check_percent,
                force=args.force,
            )
            counts[status] += 1
            print(f"[{position}/{len(paths)}] {status}: {output}")
        except Exception as exc:
            counts["failed"] += 1
            print(f"[{position}/{len(paths)}] FAILED: {survey_path.name}: {exc}")
    print(json.dumps(counts, ensure_ascii=False))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
