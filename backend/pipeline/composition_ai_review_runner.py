"""Review CompositionPlans with Claude, adjudicate findings with OpenAI, and apply consensus."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.pipeline.composition_ai_review import (
    ADJUDICATION_VERSION,
    COMPOSITION_ADJUDICATION_SCHEMA,
    COMPOSITION_RECONSIDERATION_SCHEMA,
    COMPOSITION_REVIEW_SCHEMA,
    REVIEW_VERSION,
    CompositionReviewValidationError,
    apply_consensus,
    review_fingerprint,
    validate_adjudication,
    validate_reconsideration,
    validate_review,
)
from backend.pipeline.stage1 import Stage1AnthropicClient, Stage1OpenAIClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KNOWLEDGE = Path("output/claim-layer/shared_knowledge_pilot_v1.json")
DEFAULT_OUTPUT_DIR = Path("output/claim-layer/composition-reviews")
CLAUDE_PROMPT = Path("backend/pipeline/prompts/composition_independent_ai_review.md")
OPENAI_PROMPT = Path("backend/pipeline/prompts/composition_openai_adjudication.md")
RECONSIDERATION_PROMPT = Path("backend/pipeline/prompts/composition_claude_reconsideration.md")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _archive(path: Path) -> None:
    if not path.is_file():
        return
    archive = path.parent / "generations"
    archive.mkdir(parents=True, exist_ok=True)
    digest = _sha256(path.read_bytes())[:12]
    target = archive / f"{path.stem}.{digest}{path.suffix}"
    if not target.exists():
        shutil.copy2(path, target)


def _claim_projection(plan: dict[str, Any], knowledge: dict[str, Any]) -> dict[str, Any]:
    referenced_ids = {
        claim_id
        for decision in plan.get("decisions", [])
        for claim_id in decision.get("claim_ids", [])
    }
    source_transcripts = {
        str(item.get("transcript_id") or "")
        for item in plan.get("source_leads", [])
        if item.get("transcript_id")
    }
    fragments = {item["fragment_id"]: item for item in knowledge.get("source_fragments", [])}
    evidence = {item["evidence_step_id"]: item for item in knowledge.get("evidence_steps", [])}

    candidates: list[dict[str, Any]] = []
    included_ids: set[str] = set()
    for claim in knowledge.get("claims", []):
        occurrence_transcripts = {
            str(item.get("transcript_id") or "")
            for item in claim.get("occurrences", [])
        }
        if claim["claim_id"] not in referenced_ids and not (occurrence_transcripts & source_transcripts):
            continue
        included_ids.add(claim["claim_id"])
        evidence_rows = []
        for evidence_id in claim.get("evidence_step_ids", []):
            step = evidence.get(evidence_id)
            if not step:
                continue
            fragment_ids = list(step.get("source_fragment_ids") or [])
            if not fragment_ids and step.get("source_fragment_id"):
                fragment_ids = [step["source_fragment_id"]]
            evidence_rows.append(
                {
                    "evidence_step_id": evidence_id,
                    "function": step.get("function"),
                    "statement": step.get("statement"),
                    "support_eligibility": step.get("support_eligibility"),
                    "source_fragments": [
                        {
                            "fragment_id": fragment_id,
                            "transcript_id": next(
                                (
                                    source.get("transcript_id")
                                    for source in knowledge.get("source_documents", [])
                                    if source.get("source_id")
                                    == (fragments.get(fragment_id) or {}).get("source_id")
                                ),
                                None,
                            ),
                            "media_time": (fragments.get(fragment_id) or {}).get("media_time"),
                            "verbatim_excerpt": str(
                                (fragments.get(fragment_id) or {}).get("verbatim_excerpt") or ""
                            )[:360],
                        }
                        for fragment_id in fragment_ids
                    ],
                }
            )
        candidates.append(
            {
                "claim_id": claim["claim_id"],
                "title": claim.get("title"),
                "claim_type": claim.get("claim_type"),
                "scripture_refs": claim.get("scripture_refs", []),
                "assigned_decision_ids": [
                    decision["decision_id"]
                    for decision in plan.get("decisions", [])
                    if claim["claim_id"] in decision.get("claim_ids", [])
                ],
                "evidence": evidence_rows,
            }
        )

    claim_relations = [
        item
        for item in knowledge.get("claim_relations", [])
        if item.get("source_id") in included_ids or item.get("target_id") in included_ids
    ]
    claim_relation_constraints = [
        item
        for item in knowledge.get("claim_relation_constraints", [])
        if item.get("source_id") in included_ids or item.get("target_id") in included_ids
    ]
    return {
        "plan": plan,
        "available_claims": candidates,
        "claim_relations": claim_relations,
        "claim_relation_constraints": claim_relation_constraints,
        "source_leads": plan.get("source_leads", []),
        "review_boundary": {
            "referenced_claim_ids": sorted(referenced_ids),
            "available_claim_ids": sorted(included_ids),
            "note": "未完成详细整理的普查线索只能审核其编排成熟度，不能当作已证实主张。",
        },
    }


def _generate_valid(
    client: Any,
    prompt: str,
    user_input: str,
    schema: dict[str, Any],
    validator: Any,
    attempts: int = 2,
) -> dict[str, Any]:
    feedback = ""
    last_error: Exception | None = None
    for attempt in range(attempts):
        response = client.generate_json(prompt, feedback, schema, cache_prefix=user_input)
        try:
            validator(response)
            return response
        except CompositionReviewValidationError as exc:
            last_error = exc
            feedback = (
                "\n\n上一次JSON未通过程序验证："
                + str(exc)
                + "。请重新输出完整结果，不得只给修正片段。"
            )
    raise CompositionReviewValidationError(f"model output remained invalid: {last_error}")


def _normalize_review_response(response: dict[str, Any]) -> dict[str, Any]:
    """Remove non-operative prose models sometimes put in proposal fields for pass rows.

    A pass row is never sent to adjudication and therefore cannot apply a patch.  Issues
    remain untouched so contradictory pass-with-issues output still fails validation.
    """
    empty_by_field: dict[str, Any] = {
        "proposed_action": "",
        "proposed_decision_text": "",
        "proposed_rationale": "",
        "proposed_add_claim_ids": [],
        "proposed_remove_claim_ids": [],
        "proposed_coverage": "",
        "human_review_reason": "",
    }
    for row in response.get("decision_reviews", []):
        if row.get("decision") != "pass":
            continue
        # Blanking a proposal is still discarding model output, so keep what was
        # dropped in the artifact rather than losing it between run and record.
        dropped = {
            field: row[field]
            for field, empty in empty_by_field.items()
            if row.get(field) not in (None, empty)
        }
        if dropped:
            row["normalized_away"] = dropped
        row.update({field: deepcopy(empty) for field, empty in empty_by_field.items()})
    return response


def run_one(
    *,
    plan_path: Path,
    knowledge_path: Path,
    output_dir: Path,
    claude_client: Stage1AnthropicClient,
    openai_client: Stage1OpenAIClient,
    claude_prompt: str,
    openai_prompt: str,
    reconsideration_prompt: str,
    reuse_review: bool = False,
) -> dict[str, Any]:
    plan_bytes = plan_path.read_bytes()
    knowledge_bytes = knowledge_path.read_bytes()
    plan = json.loads(plan_bytes)
    knowledge = json.loads(knowledge_bytes)
    projection = _claim_projection(plan, knowledge)
    claim_ids = {item["claim_id"] for item in knowledge.get("claims", [])}
    user_input = json.dumps(projection, ensure_ascii=False, indent=2)
    identity = review_fingerprint(
        plan_bytes=plan_bytes,
        knowledge_bytes=knowledge_bytes,
        prompt=claude_prompt,
        model=claude_client.model,
    )

    review_path = output_dir / f"{plan['plan_id']}.independent-review.json"
    if reuse_review and review_path.is_file():
        review = _normalize_review_response(json.loads(review_path.read_text(encoding="utf-8")))
        validate_review(review, plan, claim_ids)
    else:
        review = _normalize_review_response(
            _generate_valid(
                claude_client,
                claude_prompt,
                user_input,
                COMPOSITION_REVIEW_SCHEMA,
                lambda response: validate_review(
                    _normalize_review_response(response), plan, claim_ids
                ),
            )
        )
    actionable = [item for item in review["decision_reviews"] if item["decision"] != "pass"]
    review_artifact = {
        "schema_version": REVIEW_VERSION,
        "source": {
            "plan_path": str(plan_path),
            "knowledge_path": str(knowledge_path),
            "plan_sha256": _sha256(plan_bytes),
            "knowledge_sha256": _sha256(knowledge_bytes),
        },
        "reviewer": {
            **identity,
            "provider": "anthropic",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        **review,
    }

    adjudication_path = output_dir / f"{plan['plan_id']}.adjudication.json"
    candidate_path = output_dir / f"{plan['plan_id']}.reviewed-candidate.json"
    for path in (review_path, adjudication_path, candidate_path):
        _archive(path)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not reuse_review:
        review_path.write_text(json.dumps(review_artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if actionable:
        adjudication_input = (
            user_input
            + "\n\n===== Claude需要处理的意见 =====\n"
            + json.dumps(actionable, ensure_ascii=False, indent=2)
        )
        adjudication = _generate_valid(
            openai_client,
            openai_prompt,
            adjudication_input,
            COMPOSITION_ADJUDICATION_SCHEMA,
            lambda response: validate_adjudication(response, actionable, claim_ids),
        )
    else:
        adjudication = {
            "scope_confirmation": "composition_and_argument_structure_no_theological_critique",
            "adjudications": [],
        }

    rejected = [item for item in adjudication["adjudications"] if item["decision"] == "reject"]
    reconsideration = None
    if rejected:
        reconsideration_input = (
            user_input
            + "\n\n===== 你的原意见 =====\n"
            + json.dumps(actionable, ensure_ascii=False, indent=2)
            + "\n\n===== OpenAI拒绝理由 =====\n"
            + json.dumps(rejected, ensure_ascii=False, indent=2)
        )
        rejected_ids = {item["decision_id"] for item in rejected}
        reconsideration = _generate_valid(
            claude_client,
            reconsideration_prompt,
            reconsideration_input,
            COMPOSITION_RECONSIDERATION_SCHEMA,
            lambda response: validate_reconsideration(response, rejected_ids),
        )

    candidate, outcome = apply_consensus(plan, adjudication, reconsideration)
    adjudication_artifact = {
        "schema_version": ADJUDICATION_VERSION,
        "source": {
            "plan_path": str(plan_path),
            "review_path": str(review_path),
            "plan_sha256": _sha256(plan_bytes),
        },
        "adjudicator": {
            "openai_model": openai_client.model,
            "openai_reasoning_effort": openai_client.reasoning_effort,
            "claude_reconsideration_model": claude_client.model,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "openai_adjudication": adjudication,
        "claude_reconsideration": reconsideration,
        **outcome,
        "summary": {
            "reviewed_decisions": len(plan.get("decisions", [])),
            "claude_pass": len(plan.get("decisions", [])) - len(actionable),
            "claude_actionable": len(actionable),
            "auto_applied": sum(item["status"] == "auto_applied" for item in outcome["outcomes"]),
            "withdrawn": sum(item["status"] == "withdrawn" for item in outcome["outcomes"]),
            "human_required": sum(item["status"] == "human_disagreement_required" for item in outcome["outcomes"]),
            "argument_layer_status": review["plan_assessment"]["argument_layer_status"],
            "argument_layer_findings": len(review["plan_assessment"]["argument_layer_findings"]),
        },
    }
    adjudication_path.write_text(json.dumps(adjudication_artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "plan_id": plan["plan_id"],
        "review_path": str(review_path),
        "adjudication_path": str(adjudication_path),
        "candidate_path": str(candidate_path),
        **adjudication_artifact["summary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, action="append", required=True)
    parser.add_argument("--knowledge", type=Path, default=DEFAULT_KNOWLEDGE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--claude-model", default="claude-sonnet-5")
    parser.add_argument("--openai-model", default="gpt-5.6-sol")
    parser.add_argument("--openai-reasoning-effort", default="medium")
    parser.add_argument("--max-output-tokens", type=int, default=20000)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reuse-review", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print(
            json.dumps(
                {
                    "plans": [str(path) for path in args.plan],
                    "knowledge": str(args.knowledge),
                    "claude_model": args.claude_model,
                    "openai_model": args.openai_model,
                    "would_call_models": False,
                },
                ensure_ascii=False,
            )
        )
        return 0

    load_dotenv(PROJECT_ROOT / ".env")
    claude = Stage1AnthropicClient(
        model=args.claude_model,
        timeout_seconds=300,
        max_retries=3,
        max_output_tokens=args.max_output_tokens,
    )
    openai = Stage1OpenAIClient(
        model=args.openai_model,
        reasoning_effort=args.openai_reasoning_effort,
        timeout_seconds=300,
        max_retries=3,
        max_output_tokens=args.max_output_tokens,
    )
    results = []
    for plan_path in args.plan:
        result = run_one(
            plan_path=plan_path,
            knowledge_path=args.knowledge,
            output_dir=args.output_dir,
            claude_client=claude,
            openai_client=openai,
            claude_prompt=CLAUDE_PROMPT.read_text(encoding="utf-8"),
            openai_prompt=OPENAI_PROMPT.read_text(encoding="utf-8"),
            reconsideration_prompt=RECONSIDERATION_PROMPT.read_text(encoding="utf-8"),
            reuse_review=args.reuse_review,
        )
        results.append(result)
        print(json.dumps(result, ensure_ascii=False))
    return 1 if any(item["human_required"] for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
