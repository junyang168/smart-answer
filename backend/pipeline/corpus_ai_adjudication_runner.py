"""Adjudicate Claude fidelity reviews with OpenAI and auto-apply consensus patches.

OpenAI acceptance writes a versioned claim override. OpenAI rejection is sent
back to Claude once; only persistent model disagreement enters the human queue.
Neither model can grant human approval or publish content.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.config.wang_platform_paths import wang_platform_paths
from backend.pipeline.corpus_ai_adjudication import (
    ADJUDICATION_VERSION,
    CLAUDE_RECONSIDERATION_SCHEMA,
    OPENAI_ADJUDICATION_SCHEMA,
    AIAdjudicationValidationError,
    actionable_reviews,
    adjudication_fingerprint,
    compile_outcome,
    validate_claude_reconsideration,
    validate_openai_adjudication,
)
from backend.pipeline.corpus_ai_review_runner import (
    DEFAULT_TRANSCRIPT_DIRS,
    _claim_layer_input,
    _normalize_claim_layer,
    _sha256_bytes,
)
from backend.pipeline.corpus_survey_runner import PROJECT_ROOT, _load
from backend.pipeline.knowledge_source import load_knowledge_source_document
from backend.pipeline.stage1 import Stage1AnthropicClient, Stage1OpenAIClient


CLAIM_LAYER_ROOT = wang_platform_paths().claim_layer_staging
DEFAULT_PACKAGE = CLAIM_LAYER_ROOT / "shared_knowledge_pilot_v1.json"
DEFAULT_REVIEW = CLAIM_LAYER_ROOT / "independent_ai_review_v1.json"
DEFAULT_OUTPUT = CLAIM_LAYER_ROOT / "ai_adjudication_v1.json"
DEFAULT_OVERRIDES = CLAIM_LAYER_ROOT / "claim_statement_overrides_v1.json"
OPENAI_PROMPT = Path("backend/pipeline/prompts/corpus_openai_adjudication.md")
CLAUDE_PROMPT = Path("backend/pipeline/prompts/corpus_claude_reconsideration.md")
ADJUDICATION_VALIDATION_ATTEMPTS = 3


def _transcript_segments(payload: dict[str, Any]) -> dict[str, str]:
    return {
        str(segment.get("index")): str(segment.get("text") or "")
        for segment in payload.get("script", [])
    }


def _load_context(
    package_path: Path,
    transcript_dirs: list[Path],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[tuple[str, dict[str, Any]]], dict[str, dict[str, str]]]:
    package = json.loads(package_path.read_text(encoding="utf-8"))
    survey = _normalize_claim_layer(package)
    claims_by_id = {item["claim_id"]: item for item in survey["candidate_claims"]}
    transcripts: list[tuple[str, dict[str, Any]]] = []
    segments: dict[str, dict[str, str]] = {}
    for source in package.get("source_documents", []):
        # Anchor patches are applied against ``transcript_id``.  ``source_id``
        # names the knowledge node and may intentionally use a different,
        # content-addressed identifier (for example ``SRC-...``).  Giving the
        # model that source-node ID here produced valid-looking additions that
        # the consensus applier could never resolve back to a transcript.
        transcript_id = str(source.get("transcript_id") or source.get("source_id") or "")
        payload, _, _ = load_knowledge_source_document(source, transcript_dirs)
        transcripts.append((transcript_id, payload))
        segments[transcript_id] = _transcript_segments(payload)
    return survey, claims_by_id, transcripts, segments


def _openai_input(
    *,
    survey: dict[str, Any],
    transcripts: list[tuple[str, dict[str, Any]]],
    reviews: list[dict[str, Any]],
) -> str:
    return (
        _claim_layer_input(survey, transcripts)
        + "\n\n===== Claude 第一轮意见（只审理这些非 pass 项）=====\n"
        + json.dumps(reviews, ensure_ascii=False, indent=2)
    )


def _claude_reconsideration_input(
    *,
    survey: dict[str, Any],
    transcripts: list[tuple[str, dict[str, Any]]],
    reviews: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
) -> str:
    review_by_id = {item["claim_id"]: item for item in reviews}
    disputes = [
        {
            "claim_id": row["claim_id"],
            "your_original_review": review_by_id[row["claim_id"]],
            "openai_rejection": row,
        }
        for row in rejected
    ]
    return (
        _claim_layer_input(survey, transcripts)
        + "\n\n===== 需要再审的分歧 =====\n"
        + json.dumps(disputes, ensure_ascii=False, indent=2)
    )


def _archive(path: Path) -> Path | None:
    if not path.is_file():
        return None
    archive_dir = path.parent / "adjudication-generations"
    archive_dir.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fingerprint = str((payload.get("adjudicator") or {}).get("fingerprint_sha256") or "legacy")[:12]
    except (OSError, json.JSONDecodeError):
        fingerprint = "unreadable"
    target = archive_dir / f"{path.stem}.{fingerprint}.json"
    if not target.exists():
        shutil.copy2(path, target)
    return target


def _archive_rejected_adjudication(
    *, output_path: Path, attempt: int, response: dict[str, Any], error: Exception,
) -> None:
    target_dir = output_path.parent / "rejected-generations" / output_path.stem
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = target_dir / f"attempt-{attempt:02d}-{timestamp}.json"
    target.write_text(
        json.dumps(
            {"validation_error": str(error), "candidate": response},
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def _has_matching_generation(
    *,
    output_path: Path,
    overrides_path: Path,
    expected_fingerprint: str,
) -> bool:
    """Return true only when both adjudication artifacts are from this exact run generation."""
    if not output_path.is_file() or not overrides_path.is_file():
        return False
    try:
        output = json.loads(output_path.read_text(encoding="utf-8"))
        overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        str((output.get("adjudicator") or {}).get("fingerprint_sha256") or "")
        == expected_fingerprint
        and str((overrides.get("adjudication_fingerprint") or {}).get("fingerprint_sha256") or "")
        == expected_fingerprint
    )


def _anchor_signature(anchor: dict[str, Any]) -> dict[str, Any]:
    return {
        "transcript_id": anchor.get("transcript_id"),
        "paragraph_key": anchor.get("paragraph_key"),
        "evidence_id": anchor.get("evidence_id"),
        "verbatim_excerpt": anchor.get("verbatim_excerpt"),
    }


def _write_overrides(
    *,
    path: Path,
    outcome: dict[str, Any],
    claims_by_id: dict[str, dict[str, Any]],
    fingerprint: dict[str, str],
) -> None:
    claims: dict[str, Any] = {}
    for claim_id, patch in outcome["claim_overrides"].items():
        source_claim = claims_by_id[claim_id]
        excluded = [
            _anchor_signature(source_claim["anchors"][index])
            for index in patch.get("excluded_anchor_indexes", [])
        ]
        claims[claim_id] = {
            "title": patch.get("statement") or None,
            "claim_type": patch.get("claim_kind") or None,
            "route_type": (
                patch.get("route_type")
                if patch.get("route_type") not in {None, "", "unchanged"}
                else None
            ),
            "scripture_refs": patch.get("scripture_refs") or None,
            "excluded_anchors": excluded,
            "excluded_claim_relation_ids": patch.get("excluded_claim_relation_ids", []),
            "superseded_by": patch.get("superseded_by_claim_id") or None,
            "anchor_additions": patch.get("anchor_additions", []),
            "structural_notes": patch.get("structural_notes", []),
            "adjudication_fingerprint": fingerprint["fingerprint_sha256"],
            "status": "ai_consensus_applied",
            "approval_status": "not_human_approved",
        }
    artifact = {
        "schema_version": ADJUDICATION_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "adjudication_fingerprint": fingerprint,
        "claims": claims,
        "note": "OpenAI accepted Claude fidelity corrections. These are candidate overrides, not human approval or publication.",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    _archive(path)
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(
    *,
    package_path: Path,
    review_path: Path,
    output_path: Path,
    overrides_path: Path,
    transcript_dirs: list[Path],
    openai_client: Stage1OpenAIClient,
    claude_client: Stage1AnthropicClient,
    openai_prompt: str,
    claude_prompt: str,
) -> dict[str, Any]:
    survey, current_claims_by_id, transcripts, transcript_segments = _load_context(
        package_path, transcript_dirs
    )
    review_artifact = json.loads(review_path.read_text(encoding="utf-8"))
    package_sha256 = _sha256_bytes(package_path.read_bytes())
    reviewed_package_sha256 = str((review_artifact.get("source") or {}).get("package_sha256") or "")
    if reviewed_package_sha256 and reviewed_package_sha256 != package_sha256:
        raise AIAdjudicationValidationError(
            "review package snapshot no longer matches current package; rerun Claude review"
        )
    reviewed_claims = review_artifact.get("reviewed_claims")
    if not reviewed_claims:
        raise AIAdjudicationValidationError(
            "review artifact has no ordered claim snapshot; rerun Claude review"
        )
    claims_by_id = {item["claim_id"]: item for item in reviewed_claims}
    reviews = actionable_reviews(review_artifact)
    review_ids = {item["claim_id"] for item in reviews}
    if not review_ids <= set(claims_by_id) or set(claims_by_id) != set(current_claims_by_id):
        raise AIAdjudicationValidationError("review contains claims outside current package")

    # Use the exact candidate/anchor ordering Claude reviewed. This prevents an
    # accepted ordinal patch from being applied to a different anchor after a
    # package rebuild.
    survey = {"candidate_claims": reviewed_claims}

    openai_input = _openai_input(survey=survey, transcripts=transcripts, reviews=reviews)
    openai_response: dict[str, Any] | None = None
    previous_response: dict[str, Any] | None = None
    last_validation_error: AIAdjudicationValidationError | None = None
    for attempt in range(1, ADJUDICATION_VALIDATION_ATTEMPTS + 1):
        current_feedback = ""
        if previous_response is not None and last_validation_error is not None:
            current_feedback = (
                "\n\n===== 上一版仲裁 JSON（必须以此为基础定点修复）=====\n"
                + json.dumps(previous_response, ensure_ascii=False)
                + "\n\n===== 机械验证反馈 =====\n"
                + str(last_validation_error)
                + "\n请保留其余裁决，只修复所有机械错误并重新输出完整 JSON。"
                "新增 anchor 的 verbatim_excerpt 必须从指定 source_index 连续逐字复制。"
            )
        candidate = openai_client.generate_json(
            openai_prompt,
            current_feedback,
            OPENAI_ADJUDICATION_SCHEMA,
            cache_prefix=openai_input,
        )
        try:
            validate_openai_adjudication(
                candidate,
                reviews=reviews,
                claims_by_id=claims_by_id,
                transcript_segments=transcript_segments,
            )
            openai_response = candidate
            break
        except AIAdjudicationValidationError as exc:
            previous_response = candidate
            last_validation_error = exc
            _archive_rejected_adjudication(
                output_path=output_path,
                attempt=attempt,
                response=candidate,
                error=exc,
            )
    if openai_response is None:
        raise last_validation_error or AIAdjudicationValidationError(
            "OpenAI adjudication validation failed"
        )
    rejected = [item for item in openai_response["adjudications"] if item["decision"] == "reject"]
    reconsideration: dict[str, Any] | None = None
    if rejected:
        reconsideration = claude_client.generate_json(
            claude_prompt,
            _claude_reconsideration_input(
                survey=survey,
                transcripts=transcripts,
                reviews=reviews,
                rejected=rejected,
            ),
            CLAUDE_RECONSIDERATION_SCHEMA,
        )
        validate_claude_reconsideration(
            reconsideration,
            rejected_claim_ids={item["claim_id"] for item in rejected},
            claims_by_id=claims_by_id,
        )

    fingerprint = adjudication_fingerprint(
        review_fingerprint=str((review_artifact.get("reviewer") or {}).get("fingerprint_sha256") or ""),
        openai_prompt=openai_prompt,
        openai_model=openai_client.model,
        openai_reasoning_effort=openai_client.reasoning_effort,
        claude_prompt=claude_prompt,
        claude_model=claude_client.model,
    )
    outcome = compile_outcome(openai_response, reconsideration, reviews=reviews)
    artifact = {
        "schema_version": ADJUDICATION_VERSION,
        "source": {
            "package_path": str(package_path),
            "review_path": str(review_path),
        },
        "adjudicator": {
            **fingerprint,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "openai_adjudication": openai_response,
        "claude_reconsideration": reconsideration,
        **outcome,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _archive(output_path)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_overrides(
        path=overrides_path,
        outcome=outcome,
        claims_by_id=claims_by_id,
        fingerprint=fingerprint,
    )
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--transcript-dir", action="append", type=Path, dest="transcript_dirs")
    parser.add_argument("--openai-model", default="gpt-5.6-sol")
    parser.add_argument("--openai-reasoning-effort", default="medium")
    parser.add_argument("--claude-model", default="claude-sonnet-5")
    parser.add_argument("--max-output-tokens", type=int, default=16000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    transcript_dirs = args.transcript_dirs or DEFAULT_TRANSCRIPT_DIRS
    survey, claims_by_id, transcripts, _ = _load_context(args.package, transcript_dirs)
    review_artifact = json.loads(args.review.read_text(encoding="utf-8"))
    reviews = actionable_reviews(review_artifact)
    openai_prompt = OPENAI_PROMPT.read_text(encoding="utf-8")
    claude_prompt = CLAUDE_PROMPT.read_text(encoding="utf-8")
    fingerprint = adjudication_fingerprint(
        review_fingerprint=str((review_artifact.get("reviewer") or {}).get("fingerprint_sha256") or ""),
        openai_prompt=openai_prompt,
        openai_model=args.openai_model,
        openai_reasoning_effort=args.openai_reasoning_effort,
        claude_prompt=claude_prompt,
        claude_model=args.claude_model,
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "claims": len(claims_by_id),
                    "actionable_claude_reviews": len(reviews),
                    "transcripts": [item[0] for item in transcripts],
                    "openai_model": args.openai_model,
                    "claude_reconsideration_model": args.claude_model,
                    "would_call_models": False,
                },
                ensure_ascii=False,
            )
        )
        return 0
    if _has_matching_generation(
        output_path=args.output,
        overrides_path=args.overrides,
        expected_fingerprint=fingerprint["fingerprint_sha256"],
    ):
        print(
            json.dumps(
                {
                    "status": "skipped",
                    "reason": "matching adjudication fingerprint",
                    "fingerprint_sha256": fingerprint["fingerprint_sha256"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    load_dotenv(PROJECT_ROOT / ".env")
    openai_client = Stage1OpenAIClient(
        model=args.openai_model,
        reasoning_effort=args.openai_reasoning_effort,
        timeout_seconds=300,
        max_retries=3,
        max_output_tokens=args.max_output_tokens,
    )
    claude_client = Stage1AnthropicClient(
        model=args.claude_model,
        timeout_seconds=300,
        max_retries=3,
        max_output_tokens=args.max_output_tokens,
    )
    artifact = run(
        package_path=args.package,
        review_path=args.review,
        output_path=args.output,
        overrides_path=args.overrides,
        transcript_dirs=transcript_dirs,
        openai_client=openai_client,
        claude_client=claude_client,
        openai_prompt=openai_prompt,
        claude_prompt=claude_prompt,
    )
    print(json.dumps(artifact["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
