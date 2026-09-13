"""Adjudicate Claude fidelity reviews with OpenAI and auto-apply consensus patches.

OpenAI acceptance writes a versioned claim override. OpenAI rejection is sent
back to Claude once; only persistent model disagreement enters the human queue.
Neither model can grant human approval or publish content.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.config.wang_platform_paths import wang_platform_paths
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
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
    _matching_review_artifact,
    _normalize_claim_layer,
    _sha256_bytes,
    _validate_claim_layer_package,
)
from backend.pipeline.corpus_ai_review import validate_review_response
from backend.pipeline.llm_usage import usage_row
from backend.pipeline.run_ledger import RunRecord, run_record
from backend.pipeline.corpus_survey_runner import PROJECT_ROOT, _load
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.pipeline.knowledge_source import load_knowledge_source_document
from backend.pipeline.source_projection import project_script
from backend.pipeline.stage1 import Stage1AnthropicClient, Stage1OpenAIClient


def _atomic_artifact_write(path: Path, encoded: bytes) -> None:
    """Install one complete current or historical adjudication artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    """Install one complete adjudication artifact atomically."""

    _atomic_artifact_write(
        path,
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def _adjudication_artifact_sha256(artifact: dict[str, Any]) -> str:
    candidate = json.loads(json.dumps(artifact, ensure_ascii=False))
    (candidate.get("adjudicator") or {}).pop("artifact_sha256", None)
    return _sha256_bytes(
        json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _overrides_artifact_sha256(artifact: dict[str, Any]) -> str:
    candidate = json.loads(json.dumps(artifact, ensure_ascii=False))
    candidate.pop("artifact_sha256", None)
    return _sha256_bytes(
        json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


CLAIM_LAYER_ROOT = wang_platform_paths().claim_layer_staging
DEFAULT_PACKAGE = CLAIM_LAYER_ROOT / "shared_knowledge_pilot_v1.json"
DEFAULT_REVIEW = CLAIM_LAYER_ROOT / "independent_ai_review_v1.json"
DEFAULT_OUTPUT = CLAIM_LAYER_ROOT / "ai_adjudication_v1.json"
DEFAULT_OVERRIDES = CLAIM_LAYER_ROOT / "claim_statement_overrides_v1.json"
OPENAI_PROMPT = Path("backend/pipeline/prompts/corpus_openai_adjudication.md")
CLAUDE_PROMPT = Path("backend/pipeline/prompts/corpus_claude_reconsideration.md")
ADJUDICATION_VALIDATION_ATTEMPTS = 3


def _transcript_segments(payload: dict[str, Any]) -> dict[str, str]:
    rows = project_script(payload.get("script", [])).body_rows
    return {
        str(segment.get("index")): str(segment.get("text") or "")
        for segment in rows
    }


def _load_context(
    package_path: Path,
    transcript_dirs: list[Path],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[tuple[str, dict[str, Any]]], dict[str, dict[str, str]]]:
    package = json.loads(package_path.read_text(encoding="utf-8"))
    _validate_claim_layer_package(package)
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


def _validated_review_context(
    package_path: Path,
    review_path: Path,
    transcript_dirs: list[Path],
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    list[tuple[str, dict[str, Any]]],
    dict[str, dict[str, str]],
    dict[str, Any],
    bytes,
]:
    """Load the exact package/review pair before either a cache hit or a call."""

    survey, current_claims_by_id, transcripts, transcript_segments = _load_context(
        package_path, transcript_dirs
    )
    review_bytes = review_path.read_bytes()
    review_artifact = json.loads(review_bytes.decode("utf-8"))
    package_sha256 = _sha256_bytes(package_path.read_bytes())
    reviewed_package_sha256 = str(
        (review_artifact.get("source") or {}).get("package_sha256") or ""
    )
    if not reviewed_package_sha256 or reviewed_package_sha256 != package_sha256:
        raise AIAdjudicationValidationError(
            "review package snapshot no longer matches current package; rerun Claude review"
        )
    reviewed_claims = review_artifact.get("reviewed_claims")
    if not isinstance(reviewed_claims, list) or not reviewed_claims:
        raise AIAdjudicationValidationError(
            "review artifact has no ordered claim snapshot; rerun Claude review"
        )
    if reviewed_claims != survey.get("candidate_claims"):
        raise AIAdjudicationValidationError(
            "reviewed claim snapshot no longer matches current package"
        )
    reviewer = review_artifact.get("reviewer") or {}
    reviewer_fingerprint = str(reviewer.get("fingerprint_sha256") or "")
    spot_check_percent = review_artifact.get("spot_check_percent")
    if (
        not reviewer_fingerprint
        or not isinstance(spot_check_percent, int)
        or not _matching_review_artifact(
            review_artifact,
            survey=survey,
            expected_fingerprint=reviewer_fingerprint,
            spot_check_percent=spot_check_percent,
        )
    ):
        raise AIAdjudicationValidationError(
            "review artifact is incomplete, modified, or inconsistently routed; "
            "rerun Claude review"
        )
    return (
        survey,
        current_claims_by_id,
        transcripts,
        transcript_segments,
        review_artifact,
        review_bytes,
    )


def _openai_input(
    *,
    survey: dict[str, Any],
    transcripts: list[tuple[str, dict[str, Any]]],
    reviews: list[dict[str, Any]],
) -> str:
    anchor_constraints = _actionable_anchor_constraints(
        survey=survey,
        reviews=reviews,
    )
    return (
        _claim_layer_input(survey, transcripts)
        + "\n\n===== Claude 第一轮意见（只审理这些非 pass 项）=====\n"
        + json.dumps(reviews, ensure_ascii=False, indent=2)
        + "\n\n===== 每条裁决允许使用的现有 anchor 索引（机械硬约束）=====\n"
        + json.dumps(anchor_constraints, ensure_ascii=False, indent=2)
        + "\n`source_anchor_indexes` 与 `excluded_anchor_indexes` 只能使用"
        "对应 claim 的 `valid_anchor_indexes`；不得根据其他 artifact、"
        "原始 package 或记忆推测索引。"
    )


def _actionable_anchor_constraints(
    *,
    survey: dict[str, Any],
    reviews: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expose the exact reviewed-anchor ordinal space to adjudication.

    Package normalization can remove duplicate anchors before Claude review.
    Adjudication patches are applied to that reviewed snapshot, not the raw
    package occurrence array, so the model must not infer the ordinal range
    from another representation of the same claim.
    """

    claims_by_id = {
        str(claim.get("claim_id") or ""): claim
        for claim in survey.get("candidate_claims") or []
    }
    return [
        {
            "claim_id": str(review.get("claim_id") or ""),
            "anchor_count": len(
                claims_by_id[str(review.get("claim_id") or "")].get("anchors")
                or []
            ),
            "valid_anchor_indexes": list(
                range(
                    len(
                        claims_by_id[
                            str(review.get("claim_id") or "")
                        ].get("anchors")
                        or []
                    )
                )
            ),
        }
        for review in reviews
    ]


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
    raw = path.read_bytes()
    archive_dir = path.parent / "adjudication-generations"
    archive_dir.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.loads(raw)
        fingerprint = str((payload.get("adjudicator") or {}).get("fingerprint_sha256") or "legacy")[:12]
    except (OSError, json.JSONDecodeError):
        fingerprint = "unreadable"
    target = archive_dir / f"{path.stem}.{fingerprint}.{_sha256_bytes(raw)[:8]}.json"
    if not target.exists():
        _atomic_artifact_write(target, raw)
    return target


def _archive_rejected_adjudication(
    *, output_path: Path, attempt: int, response: dict[str, Any], error: Exception,
) -> None:
    target_dir = output_path.parent / "rejected-generations" / output_path.stem
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = target_dir / f"attempt-{attempt:02d}-{timestamp}.json"
    _atomic_json_write(
        target,
        {"validation_error": str(error), "candidate": response},
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


def _valid_adjudication_artifact(
    artifact: dict[str, Any],
    *,
    expected_fingerprint: str,
    reviews: list[dict[str, Any]],
    claims_by_id: dict[str, dict[str, Any]],
    transcript_segments: dict[str, dict[str, str]],
) -> bool:
    """Prove a same-fingerprint current file is a complete validated result."""

    try:
        if artifact.get("schema_version") != ADJUDICATION_VERSION:
            return False
        if (
            str((artifact.get("adjudicator") or {}).get("fingerprint_sha256") or "")
            != expected_fingerprint
        ):
            return False
        if (artifact.get("adjudicator") or {}).get(
            "artifact_sha256"
        ) != _adjudication_artifact_sha256(artifact):
            return False
        openai_response = artifact["openai_adjudication"]
        validate_openai_adjudication(
            openai_response,
            reviews=reviews,
            claims_by_id=claims_by_id,
            transcript_segments=transcript_segments,
        )
        rejected_ids = {
            row["claim_id"]
            for row in openai_response["adjudications"]
            if row["decision"] == "reject"
        }
        reconsideration = artifact.get("claude_reconsideration")
        if rejected_ids:
            if not isinstance(reconsideration, dict):
                return False
            validate_claude_reconsideration(
                reconsideration,
                rejected_claim_ids=rejected_ids,
                claims_by_id=claims_by_id,
            )
        elif reconsideration is not None:
            return False
        expected = compile_outcome(
            openai_response, reconsideration, reviews=reviews
        )
        return all(artifact.get(key) == value for key, value in expected.items())
    except (AIAdjudicationValidationError, KeyError, TypeError, ValueError):
        return False


def _recover_matching_overrides(
    *,
    output_path: Path,
    overrides_path: Path,
    expected_fingerprint: str,
    claims_by_id: dict[str, dict[str, Any]],
) -> bool:
    """Finish the deterministic half of an interrupted adjudication commit.

    The validated adjudication is written before its mechanically compiled
    overrides. A process death between those two atomic replaces must not
    trigger two more model calls: when the current adjudication carries the
    exact expected fingerprint, the missing/stale overrides can be rebuilt
    from that artifact and the exact claim snapshot Claude reviewed.
    """

    if not output_path.is_file():
        return False
    try:
        outcome = json.loads(output_path.read_text(encoding="utf-8"))
        fingerprint = outcome.get("adjudicator") or {}
        if str(fingerprint.get("fingerprint_sha256") or "") != expected_fingerprint:
            return False
        if not isinstance(outcome.get("claim_overrides"), dict):
            return False
        _write_overrides(
            path=overrides_path,
            outcome=outcome,
            claims_by_id=claims_by_id,
            fingerprint=fingerprint,
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return False
    return _has_matching_generation(
        output_path=output_path,
        overrides_path=overrides_path,
        expected_fingerprint=expected_fingerprint,
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
    artifact = _compile_overrides(
        outcome=outcome,
        claims_by_id=claims_by_id,
        fingerprint=fingerprint,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    _archive(path)
    _atomic_json_write(path, artifact)


def _compile_overrides(
    *,
    outcome: dict[str, Any],
    claims_by_id: dict[str, dict[str, Any]],
    fingerprint: dict[str, str],
    generated_at: str,
) -> dict[str, Any]:
    """Compile the deterministic override sidecar for integrity checks/recovery."""

    fingerprint = {
        key: value
        for key, value in fingerprint.items()
        if key not in {"generated_at", "artifact_sha256"}
    }
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
        "generated_at": generated_at,
        "adjudication_fingerprint": fingerprint,
        "claims": claims,
        "note": "OpenAI accepted Claude fidelity corrections. These are candidate overrides, not human approval or publication.",
    }
    artifact["artifact_sha256"] = _overrides_artifact_sha256(artifact)
    return artifact


def _valid_overrides_artifact(
    artifact: dict[str, Any],
    *,
    outcome: dict[str, Any],
    claims_by_id: dict[str, dict[str, Any]],
    fingerprint: dict[str, str],
) -> bool:
    generated_at = artifact.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at:
        return False
    try:
        expected = _compile_overrides(
            outcome=outcome,
            claims_by_id=claims_by_id,
            fingerprint=fingerprint,
            generated_at=generated_at,
        )
    except (KeyError, TypeError, ValueError):
        return False
    return artifact == expected


def _adjudication_subject(review_path: Path, package_path: Path) -> str:
    """Which source this adjudication is about.

    The review artifact names its transcript, so use that; a package whose
    review predates the field falls back to the package's own stem rather than
    filing the run against nothing.
    """

    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
        transcript_id = str((review.get("source") or {}).get("transcript_id") or "")
    except (OSError, json.JSONDecodeError):
        transcript_id = ""
    return transcript_id or package_path.stem


def run(
    *,
    package_path: Path,
    review_path: Path,
    output_path: Path,
    overrides_path: Path,
    transcript_dirs: list[Path],
    openai_client: Stage1OpenAIClient | CodexSubscriptionClient,
    claude_client: Stage1AnthropicClient | ClaudeSubscriptionClient,
    openai_prompt: str,
    claude_prompt: str,
) -> dict[str, Any]:
    with run_record(
        subject=_adjudication_subject(review_path, package_path), stage="adjudication"
    ) as record:
        # Two models at two prices; the run's `model_id` is the one that made
        # the primary call, and each usage row carries its own.
        record.model(openai_client.model)
        return _run_adjudication(
            record=record, package_path=package_path, review_path=review_path,
            output_path=output_path, overrides_path=overrides_path,
            transcript_dirs=transcript_dirs, openai_client=openai_client,
            claude_client=claude_client, openai_prompt=openai_prompt,
            claude_prompt=claude_prompt,
        )


def _run_adjudication(
    *,
    record: "RunRecord",
    package_path: Path,
    review_path: Path,
    output_path: Path,
    overrides_path: Path,
    transcript_dirs: list[Path],
    openai_client: Stage1OpenAIClient | CodexSubscriptionClient,
    claude_client: Stage1AnthropicClient | ClaudeSubscriptionClient,
    openai_prompt: str,
    claude_prompt: str,
) -> dict[str, Any]:
    (
        survey,
        current_claims_by_id,
        transcripts,
        transcript_segments,
        review_artifact,
        review_bytes,
    ) = _validated_review_context(package_path, review_path, transcript_dirs)
    reviewed_claims = review_artifact["reviewed_claims"]
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
    # Adjudication called two models and measured neither, so it was the one
    # stage whose price could not be stated even after `llm_usage` unified the
    # shape. Collected here for the same reason extraction collects it: the
    # rejected attempts cost money too.
    usage_rows: list[dict[str, Any]] = []
    for attempt in range(1, ADJUDICATION_VALIDATION_ATTEMPTS + 1):
        current_feedback = ""
        if previous_response is not None and last_validation_error is not None:
            current_feedback = (
                "\n\n===== 上一版仲裁 JSON（必须以此为基础定点修复）=====\n"
                + json.dumps(previous_response, ensure_ascii=False)
                + "\n\n===== 机械验证反馈 =====\n"
                + str(last_validation_error)
                + "\n\n再次核对每条 claim 的 `valid_anchor_indexes` 硬约束；"
                "删除所有超出该列表的 ordinal，不得保留或猜测。"
                + "\n请保留其余裁决，只修复所有机械错误并重新输出完整 JSON。"
                "新增 anchor 的 verbatim_excerpt 必须从指定 source_index 连续逐字复制。"
            )
        record.model_call_started()
        candidate = openai_client.generate_json(
            openai_prompt,
            current_feedback,
            OPENAI_ADJUDICATION_SCHEMA,
            cache_prefix=openai_input,
        )
        call_usage = {
            **usage_row(getattr(openai_client, "last_usage", None), attempt),
            "model_id": openai_client.model, "role": "openai_adjudication",
        }
        usage_rows.append(call_usage)
        record.usage([call_usage])
        record.model_call_completed()
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
        record.model_call_started()
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
        # The two adjudicators are different families at different prices, so
        # each row carries its own `model_id` rather than inheriting the run's.
        call_usage = {
            **usage_row(getattr(claude_client, "last_usage", None), 1),
            "model_id": claude_client.model, "role": "claude_reconsideration",
        }
        usage_rows.append(call_usage)
        record.usage([call_usage])
        record.model_call_completed()
        validate_claude_reconsideration(
            reconsideration,
            rejected_claim_ids={item["claim_id"] for item in rejected},
            claims_by_id=claims_by_id,
        )

    fingerprint = adjudication_fingerprint(
        review_fingerprint=str((review_artifact.get("reviewer") or {}).get("fingerprint_sha256") or ""),
        review_artifact_sha256=_sha256_bytes(review_bytes),
        openai_prompt=openai_prompt,
        openai_model=openai_client.model,
        openai_reasoning_effort=openai_client.reasoning_effort,
        openai_max_output_tokens=openai_client.max_output_tokens,
        openai_backend=getattr(openai_client, "backend", "api").replace("_", "-"),
        claude_prompt=claude_prompt,
        claude_model=claude_client.model,
        claude_max_output_tokens=claude_client.max_output_tokens,
        claude_backend=getattr(claude_client, "backend", "api").replace("_", "-"),
        source_package_sha256=_sha256_bytes(package_path.read_bytes()),
    )
    outcome = compile_outcome(openai_response, reconsideration, reviews=reviews)
    record.inputs({"fingerprint_sha256": fingerprint.get("fingerprint_sha256")})
    # `human_disagreement_required` is the number that matters here: the two
    # models could not settle it and a person has to. It is not a failure, but a
    # source whose adjudication routes everything to a person has not been
    # adjudicated in any useful sense, and the overview has to show that.
    record.quality(dict(outcome.get("summary") or {}))
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
    artifact["adjudicator"]["artifact_sha256"] = _adjudication_artifact_sha256(
        artifact
    )
    _archive(output_path)
    _atomic_json_write(output_path, artifact)
    _write_overrides(
        path=overrides_path,
        outcome=outcome,
        claims_by_id=claims_by_id,
        fingerprint=fingerprint,
    )
    record.outputs(output_path, overrides_path)
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
    parser.add_argument(
        "--openai-backend", choices=["api", "codex-subscription"], default="api",
        help="primary adjudicator transport; codex-subscription uses the ChatGPT login",
    )
    parser.add_argument("--claude-model", default="claude-sonnet-5")
    parser.add_argument(
        "--claude-backend", choices=["api", "claude-subscription"], default="api",
        help="reconsideration transport; claude-subscription uses the Claude.ai login",
    )
    # Raised with the reviewer's for the same reason, before it bites rather
    # than after: adjudication answers the reviewer's findings, and a package
    # with five times the claims produces more of them. 16,000 is also exactly
    # the streaming threshold, so the old default was the one value that could
    # fill the budget without streaming.
    parser.add_argument("--max-output-tokens", type=int, default=32000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    transcript_dirs = args.transcript_dirs or DEFAULT_TRANSCRIPT_DIRS
    (
        survey,
        claims_by_id,
        transcripts,
        transcript_segments,
        review_artifact,
        review_bytes,
    ) = _validated_review_context(args.package, args.review, transcript_dirs)
    reviews = actionable_reviews(review_artifact)
    openai_prompt = OPENAI_PROMPT.read_text(encoding="utf-8")
    claude_prompt = CLAUDE_PROMPT.read_text(encoding="utf-8")
    fingerprint = adjudication_fingerprint(
        review_fingerprint=str((review_artifact.get("reviewer") or {}).get("fingerprint_sha256") or ""),
        review_artifact_sha256=_sha256_bytes(review_bytes),
        openai_prompt=openai_prompt,
        openai_model=args.openai_model,
        openai_reasoning_effort=args.openai_reasoning_effort,
        openai_max_output_tokens=args.max_output_tokens,
        openai_backend=args.openai_backend,
        claude_prompt=claude_prompt,
        claude_model=args.claude_model,
        claude_max_output_tokens=args.max_output_tokens,
        claude_backend=args.claude_backend,
        source_package_sha256=_sha256_bytes(args.package.read_bytes()),
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
    current_adjudication: dict[str, Any] | None = None
    if args.output.is_file():
        try:
            candidate = json.loads(args.output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            candidate = {}
        if _valid_adjudication_artifact(
            candidate,
            expected_fingerprint=fingerprint["fingerprint_sha256"],
            reviews=reviews,
            claims_by_id=claims_by_id,
            transcript_segments=transcript_segments,
        ):
            current_adjudication = candidate
    current_overrides: dict[str, Any] = {}
    if args.overrides.is_file():
        try:
            current_overrides = json.loads(args.overrides.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current_overrides = {}
    if current_adjudication is not None and _valid_overrides_artifact(
        current_overrides,
        outcome=current_adjudication,
        claims_by_id=claims_by_id,
        fingerprint=current_adjudication["adjudicator"],
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
    if current_adjudication is not None:
        recovered = _recover_matching_overrides(
            output_path=args.output,
            overrides_path=args.overrides,
            expected_fingerprint=fingerprint["fingerprint_sha256"],
            claims_by_id=claims_by_id,
        )
        if recovered:
            load_dotenv(PROJECT_ROOT / ".env")
            with run_record(
                subject=_adjudication_subject(args.review, args.package),
                stage="adjudication",
            ) as record:
                record.inputs({"fingerprint_sha256": fingerprint["fingerprint_sha256"]})
                record.quality({"recovered_interrupted_artifact_commit": True})
                record.outputs(args.output, args.overrides)
            print(
                json.dumps(
                    {
                        "status": "recovered",
                        "reason": "rebuilt overrides from matching adjudication artifact",
                        "fingerprint_sha256": fingerprint["fingerprint_sha256"],
                    },
                    ensure_ascii=False,
                )
            )
            return 0
    load_dotenv(PROJECT_ROOT / ".env")
    openai_client = (
        CodexSubscriptionClient(
            model=args.openai_model,
            reasoning_effort=args.openai_reasoning_effort,
            timeout_seconds=900,
            max_output_tokens=args.max_output_tokens,
        )
        if args.openai_backend == "codex-subscription"
        else Stage1OpenAIClient(
            model=args.openai_model,
            reasoning_effort=args.openai_reasoning_effort,
            timeout_seconds=300,
            max_retries=3,
            max_output_tokens=args.max_output_tokens,
        )
    )
    claude_client = (
        ClaudeSubscriptionClient(
            model=args.claude_model, timeout_seconds=900,
            max_output_tokens=args.max_output_tokens,
        )
        if args.claude_backend == "claude-subscription"
        else Stage1AnthropicClient(
            model=args.claude_model, timeout_seconds=300, max_retries=3,
            max_output_tokens=args.max_output_tokens,
        )
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
