"""Discover and double-review cross-sermon relations in one research batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from backend.pipeline.cross_sermon_relation import (
    ADJUDICATION_SCHEMA,
    DISCOVERY_SCHEMA,
    RECONSIDERATION_SCHEMA,
    REVIEW_SCHEMA,
    SCOPE,
    CrossSermonRelationValidationError,
    actionable_reviews,
    apply_consensus,
    fingerprint,
    normalize_discovery,
    validate_adjudication,
    validate_discovery,
    validate_reconsideration,
    validate_review,
)
from backend.pipeline.stage1 import Stage1AnthropicClient, Stage1OpenAIClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = Path(
    "output/claim-layer/research-batches/RB-COVENANT-LAW-VALIDATION-01/"
    "merged/research-batch-knowledge.json"
)
DEFAULT_OUTPUT_DIR = Path(
    "output/claim-layer/research-batches/RB-COVENANT-LAW-VALIDATION-01/"
    "cross-sermon-relations"
)
PROMPT_DIR = Path("backend/pipeline/prompts")
DISCOVERY_PROMPT = PROMPT_DIR / "cross_sermon_relation_discovery.md"
REVIEW_PROMPT = PROMPT_DIR / "cross_sermon_relation_review.md"
ADJUDICATION_PROMPT = PROMPT_DIR / "cross_sermon_relation_adjudication.md"
RECONSIDERATION_PROMPT = PROMPT_DIR / "cross_sermon_relation_reconsideration.md"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _archive(path: Path) -> None:
    if not path.is_file():
        return
    archive_dir = path.parent / "generations"
    archive_dir.mkdir(parents=True, exist_ok=True)
    digest = _sha256_bytes(path.read_bytes())[:12]
    target = archive_dir / f"{path.stem}.{digest}{path.suffix}"
    if not target.exists():
        shutil.copy2(path, target)


def _cached_result(path: Path, expected_fingerprint: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if (artifact.get("generation") or {}).get("fingerprint_sha256") != expected_fingerprint:
        return None
    return artifact


def _write_artifact(
    path: Path,
    *,
    schema_version: str,
    generation: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    _archive(path)
    artifact = {
        "schema_version": schema_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generation": generation,
        "result": result,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


def build_projection(knowledge: dict[str, Any]) -> dict[str, Any]:
    evidence = {
        str(row["evidence_step_id"]): row for row in knowledge.get("evidence_steps", [])
    }
    source_titles = {
        str(row.get("transcript_id") or ""): str(row.get("title") or "")
        for row in knowledge.get("source_documents", [])
    }
    claims = []
    for claim in knowledge.get("claims", []):
        transcripts = sorted(
            {
                str(row.get("transcript_id") or "")
                for row in claim.get("occurrences", [])
                if row.get("transcript_id")
            }
        )
        claims.append(
            {
                "claim_id": claim["claim_id"],
                "source_transcript_ids": transcripts,
                "source_titles": [source_titles.get(value, value) for value in transcripts],
                "title": claim.get("title"),
                "claim_type": claim.get("claim_type"),
                "scripture_refs": claim.get("scripture_refs", []),
                "topic_terms": claim.get("topic_terms", []),
                "evidence": [
                    {
                        "evidence_step_id": evidence_id,
                        "statement": (evidence.get(evidence_id) or {}).get("statement"),
                        "step_type": (evidence.get(evidence_id) or {}).get("step_type"),
                        "support_eligibility": (
                            evidence.get(evidence_id) or {}
                        ).get("support_eligibility"),
                    }
                    for evidence_id in claim.get("evidence_step_ids", [])
                    if evidence_id in evidence
                ],
            }
        )
    return {
        "batch": knowledge.get("batch"),
        "comparison_policy": {
            "selection_is_not_classification": True,
            "original_claims_are_immutable": True,
            "allow_unassigned_claims": True,
        },
        "claims": claims,
    }


def _generate_valid(
    *,
    client: Any,
    prompt: str,
    user_input: str,
    schema: dict[str, Any],
    normalize: Callable[[dict[str, Any]], dict[str, Any]],
    validate: Callable[[dict[str, Any]], None],
    attempts: int = 3,
) -> dict[str, Any]:
    current_input = user_input
    previous: dict[str, Any] | None = None
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        response = normalize(client.generate_json(prompt, current_input, schema))
        try:
            validate(response)
            return response
        except CrossSermonRelationValidationError as exc:
            last_error = exc
            previous = response
            current_input = (
                user_input
                + "\n\n上一版未通过机械验证："
                + str(exc)
                + "。请修正后重新输出完整 JSON，不得只给修正片段。"
                + "\n上一版完整 JSON：\n"
                + json.dumps(previous, ensure_ascii=False)
            )
    raise CrossSermonRelationValidationError(
        f"model output remained invalid after {attempts} attempts: {last_error}"
    )


def _generation_identity(
    *, source_sha256: str, prompt: str, model: str, schema: dict[str, Any],
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    identity = {
        "pipeline_schema_version": "wang_cross_sermon_relation_runner_v1",
        "source_sha256": source_sha256,
        "prompt_sha256": _sha256_bytes(prompt.encode("utf-8")),
        "model_id": model,
        "reasoning_effort": reasoning_effort,
        "temperature": 0.0,
        "response_schema_sha256": fingerprint(schema),
    }
    return {**identity, "fingerprint_sha256": fingerprint(identity)}


def _normalize_scope(response: dict[str, Any], rows_key: str) -> dict[str, Any]:
    """Fill the fixed audit scope when a JSON-only model omits the const field.

    OpenAI enforces the JSON Schema server-side.  Anthropic receives the schema
    as instructions, so it can occasionally omit a constant field while still
    returning every substantive row.  This normalization changes no judgment.
    """
    normalized = dict(response)
    if normalized.get(rows_key) is not None and not normalized.get("scope_confirmation"):
        normalized["scope_confirmation"] = (
            "cross_sermon_structure_no_theological_critique"
        )
    return normalized


def _chunked(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    if size < 1:
        raise ValueError("review batch size must be positive")
    return [rows[index:index + size] for index in range(0, len(rows), size)]


def _run_chunked_review(
    *,
    client: Stage1AnthropicClient,
    prompt: str,
    projection_claims: list[dict[str, Any]],
    discovery: dict[str, Any],
    output_dir: Path,
    batch_size: int,
    force: bool,
) -> dict[str, Any]:
    """Review bounded candidate batches, then verify exact global coverage."""
    claim_index = {str(row["claim_id"]): row for row in projection_claims}
    all_reviews: list[dict[str, Any]] = []
    parts = _chunked(discovery.get("relation_candidates", []), batch_size)
    for part_number, candidates in enumerate(parts, start=1):
        endpoint_ids = {
            str(candidate[field])
            for candidate in candidates
            for field in ("source_claim_id", "target_claim_id")
        }
        part_discovery = {
            "scope_confirmation": SCOPE,
            "relation_candidates": candidates,
            "unassigned_claim_ids": [],
            "comparison_summary": (
                f"Independent review part {part_number} of {len(parts)}."
            ),
        }
        part_input = {
            "claims": [claim_index[claim_id] for claim_id in sorted(endpoint_ids)],
            "discovery": part_discovery,
            "review_batch": {"number": part_number, "total": len(parts)},
        }
        part_source_sha = fingerprint(part_input)
        part_generation = _generation_identity(
            source_sha256=part_source_sha,
            prompt=prompt,
            model=client.model,
            schema=REVIEW_SCHEMA,
        )
        part_path = output_dir / "review-parts" / f"part-{part_number:03d}.json"
        cached = None if force else _cached_result(
            part_path, part_generation["fingerprint_sha256"]
        )
        if cached:
            part_review = cached["result"]
            validate_review(part_review, part_discovery)
            print(f"cross-sermon review part {part_number}/{len(parts)}: skipped")
        else:
            part_review = _generate_valid(
                client=client,
                prompt=prompt,
                user_input=json.dumps(part_input, ensure_ascii=False, indent=2),
                schema=REVIEW_SCHEMA,
                normalize=lambda value: _normalize_scope(value, "relation_reviews"),
                validate=lambda value, expected=part_discovery: validate_review(
                    value, expected
                ),
            )
            _write_artifact(
                part_path,
                schema_version="wang_cross_sermon_relation_review_part_v1",
                generation=part_generation,
                result=part_review,
            )
            print(f"cross-sermon review part {part_number}/{len(parts)}: created")
        all_reviews.extend(part_review["relation_reviews"])
    combined = {"scope_confirmation": SCOPE, "relation_reviews": all_reviews}
    validate_review(combined, discovery)
    return combined


def run(
    *,
    knowledge_path: Path,
    output_dir: Path,
    openai_client: Stage1OpenAIClient,
    claude_client: Stage1AnthropicClient,
    discovery_prompt: str,
    review_prompt: str,
    adjudication_prompt: str,
    reconsideration_prompt: str,
    review_batch_size: int = 12,
    force: bool = False,
) -> dict[str, Any]:
    raw = knowledge_path.read_bytes()
    knowledge = json.loads(raw)
    if (knowledge.get("batch") or {}).get("semantic_assumption") != "none":
        raise CrossSermonRelationValidationError(
            "cross-sermon comparison requires a neutral merged research batch"
        )
    projection = build_projection(knowledge)
    projection_text = json.dumps(projection, ensure_ascii=False, indent=2)
    source_sha = _sha256_bytes(raw)

    discovery_path = output_dir / "discovery.json"
    discovery_generation = _generation_identity(
        source_sha256=source_sha,
        prompt=discovery_prompt,
        model=openai_client.model,
        reasoning_effort=openai_client.reasoning_effort,
        schema=DISCOVERY_SCHEMA,
    )
    cached = None if force else _cached_result(
        discovery_path, discovery_generation["fingerprint_sha256"]
    )
    if cached:
        discovery = cached["result"]
        validate_discovery(discovery, knowledge)
        print("cross-sermon discovery: skipped")
    else:
        discovery = _generate_valid(
            client=openai_client,
            prompt=discovery_prompt,
            user_input=projection_text,
            schema=DISCOVERY_SCHEMA,
            normalize=normalize_discovery,
            validate=lambda value: validate_discovery(value, knowledge),
        )
        _write_artifact(
            discovery_path,
            schema_version="wang_cross_sermon_relation_discovery_v1",
            generation=discovery_generation,
            result=discovery,
        )
        print(f"cross-sermon discovery: created {len(discovery['relation_candidates'])}")

    discovery_sha = fingerprint(
        {"discovery": discovery, "review_batch_size": review_batch_size}
    )
    review_path = output_dir / "independent-review.json"
    review_generation = _generation_identity(
        source_sha256=discovery_sha,
        prompt=review_prompt,
        model=claude_client.model,
        schema=REVIEW_SCHEMA,
    )
    cached = None if force else _cached_result(review_path, review_generation["fingerprint_sha256"])
    if cached:
        review = cached["result"]
        validate_review(review, discovery)
        print("cross-sermon review: skipped")
    else:
        review = _run_chunked_review(
            client=claude_client,
            prompt=review_prompt,
            projection_claims=projection["claims"],
            discovery=discovery,
            output_dir=output_dir,
            batch_size=review_batch_size,
            force=force,
        )
        _write_artifact(
            review_path,
            schema_version="wang_cross_sermon_relation_review_v1",
            generation=review_generation,
            result=review,
        )
        print("cross-sermon review: created")

    actionable = actionable_reviews(review)
    adjudication_path = output_dir / "adjudication.json"
    adjudication_source_sha = fingerprint({"discovery": discovery, "review": review})
    adjudication_generation = _generation_identity(
        source_sha256=adjudication_source_sha,
        prompt=adjudication_prompt,
        model=openai_client.model,
        reasoning_effort=openai_client.reasoning_effort,
        schema=ADJUDICATION_SCHEMA,
    )
    cached = None if force else _cached_result(
        adjudication_path, adjudication_generation["fingerprint_sha256"]
    )
    if cached:
        adjudication = cached["result"]
        validate_adjudication(adjudication, review)
        print("cross-sermon adjudication: skipped")
    elif not actionable:
        adjudication = {"scope_confirmation": "cross_sermon_structure_no_theological_critique", "adjudications": []}
        _write_artifact(
            adjudication_path,
            schema_version="wang_cross_sermon_relation_adjudication_v1",
            generation=adjudication_generation,
            result=adjudication,
        )
        print("cross-sermon adjudication: no actionable reviews")
    else:
        adjudication_input = json.dumps(
            {"claims": projection["claims"], "discovery": discovery, "actionable_reviews": actionable},
            ensure_ascii=False,
            indent=2,
        )
        adjudication = _generate_valid(
            client=openai_client,
            prompt=adjudication_prompt,
            user_input=adjudication_input,
            schema=ADJUDICATION_SCHEMA,
            normalize=lambda value: _normalize_scope(value, "adjudications"),
            validate=lambda value: validate_adjudication(value, review),
        )
        _write_artifact(
            adjudication_path,
            schema_version="wang_cross_sermon_relation_adjudication_v1",
            generation=adjudication_generation,
            result=adjudication,
        )
        print(f"cross-sermon adjudication: created {len(actionable)}")

    rejected_ids = {
        str(row["candidate_id"])
        for row in adjudication.get("adjudications", [])
        if row.get("decision") == "reject"
    }
    reconsideration: dict[str, Any] | None = None
    reconsideration_generation: dict[str, Any] | None = None
    reconsideration_path = output_dir / "reconsideration.json"
    if rejected_ids:
        reconsideration_source_sha = fingerprint(
            {"discovery": discovery, "review": review, "adjudication": adjudication}
        )
        reconsideration_generation = _generation_identity(
            source_sha256=reconsideration_source_sha,
            prompt=reconsideration_prompt,
            model=claude_client.model,
            schema=RECONSIDERATION_SCHEMA,
        )
        cached = None if force else _cached_result(
            reconsideration_path, reconsideration_generation["fingerprint_sha256"]
        )
        if cached:
            reconsideration = cached["result"]
            validate_reconsideration(
                reconsideration, rejected_candidate_ids=rejected_ids
            )
            print("cross-sermon reconsideration: skipped")
        else:
            reconsideration_input = json.dumps(
                {
                    "claims": projection["claims"],
                    "discovery": discovery,
                    "review": review,
                    "adjudication": adjudication,
                    "rejected_candidate_ids": sorted(rejected_ids),
                },
                ensure_ascii=False,
                indent=2,
            )
            reconsideration = _generate_valid(
                client=claude_client,
                prompt=reconsideration_prompt,
                user_input=reconsideration_input,
                schema=RECONSIDERATION_SCHEMA,
                normalize=lambda value: _normalize_scope(value, "reconsiderations"),
                validate=lambda value: validate_reconsideration(
                    value, rejected_candidate_ids=rejected_ids
                ),
            )
            _write_artifact(
                reconsideration_path,
                schema_version="wang_cross_sermon_relation_reconsideration_v1",
                generation=reconsideration_generation,
                result=reconsideration,
            )
            print(f"cross-sermon reconsideration: created {len(rejected_ids)}")

    result = apply_consensus(discovery, review, adjudication, reconsideration)
    final_generation = {
        "source_knowledge_sha256": source_sha,
        "discovery_fingerprint": discovery_generation["fingerprint_sha256"],
        "review_fingerprint": review_generation["fingerprint_sha256"],
        "adjudication_fingerprint": adjudication_generation["fingerprint_sha256"],
        "reconsideration_fingerprint": (
            reconsideration_generation or {}
        ).get("fingerprint_sha256"),
    }
    final_generation["fingerprint_sha256"] = fingerprint(final_generation)
    final_path = output_dir / "reviewed-relations.json"
    cached_final = None if force else _cached_result(
        final_path, final_generation["fingerprint_sha256"]
    )
    if cached_final:
        return {"output": str(final_path), **cached_final["result"]["summary"]}
    _write_artifact(
        final_path,
        schema_version="wang_cross_sermon_relation_consensus_v1",
        generation=final_generation,
        result=result,
    )
    return {"output": str(final_path), **result["summary"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--openai-model", default="gpt-5.6-sol")
    parser.add_argument("--openai-reasoning-effort", default="medium")
    parser.add_argument("--claude-model", default="claude-sonnet-5")
    parser.add_argument("--review-batch-size", type=int, default=12)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    prompts = {
        "discovery": DISCOVERY_PROMPT.read_text(encoding="utf-8"),
        "review": REVIEW_PROMPT.read_text(encoding="utf-8"),
        "adjudication": ADJUDICATION_PROMPT.read_text(encoding="utf-8"),
        "reconsideration": RECONSIDERATION_PROMPT.read_text(encoding="utf-8"),
    }
    result = run(
        knowledge_path=args.knowledge,
        output_dir=args.output_dir,
        openai_client=Stage1OpenAIClient(
            model=args.openai_model,
            reasoning_effort=args.openai_reasoning_effort,
            timeout_seconds=360,
            max_retries=3,
            max_output_tokens=20000,
        ),
        claude_client=Stage1AnthropicClient(
            model=args.claude_model,
            timeout_seconds=360,
            max_retries=3,
            max_output_tokens=20000,
        ),
        discovery_prompt=prompts["discovery"],
        review_prompt=prompts["review"],
        adjudication_prompt=prompts["adjudication"],
        reconsideration_prompt=prompts["reconsideration"],
        review_batch_size=args.review_batch_size,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
