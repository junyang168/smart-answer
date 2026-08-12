"""Independently review claim-to-claim relations with Claude and OpenAI."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.pipeline.composition_ai_review import CompositionReviewValidationError
from backend.pipeline.composition_ai_review_runner import _generate_valid
from backend.pipeline.stage1 import Stage1AnthropicClient, Stage1OpenAIClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KNOWLEDGE = Path("output/claim-layer/shared_knowledge_pilot_v1.json")
DEFAULT_OUTPUT = Path("output/claim-layer/claim_relation_review_v1.json")
TARGET_RELATION_IDS = {
    "DK-f0eac41a4244-CR001",
    "DK-f0eac41a4244-CR002",
    "DK-f0eac41a4244-CR004",
    "DK-f0eac41a4244-CR005",
    "DK-f0eac41a4244-CR006",
    "DK-f0eac41a4244-CR007",
    "DK-f0eac41a4244-CR008",
    "DK-f0eac41a4244-CR009",
    "DK-f0eac41a4244-CR010",
    "DK-f0eac41a4244-CR011",
}

REVIEW_SCHEMA_BODY = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scope_confirmation": {"type": "string", "const": "relation_structure_no_theological_critique"},
        "relation_reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim_relation_id": {"type": "string"},
                    "decision": {"type": "string", "enum": ["pass", "change", "remove"]},
                    "explanation": {"type": "string"},
                    "proposed_relation_type": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["claim_relation_id", "decision", "explanation", "proposed_relation_type", "confidence"],
            },
        },
    },
    "required": ["scope_confirmation", "relation_reviews"],
}

ADJUDICATION_SCHEMA_BODY = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scope_confirmation": {"type": "string", "const": "relation_structure_no_theological_critique"},
        "adjudications": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim_relation_id": {"type": "string"},
                    "decision": {"type": "string", "enum": ["accept", "reject"]},
                    "final_relation_type": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["claim_relation_id", "decision", "final_relation_type", "reason"],
            },
        },
    },
    "required": ["scope_confirmation", "adjudications"],
}

RECONSIDERATION_SCHEMA_BODY = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scope_confirmation": {"type": "string", "const": "relation_structure_no_theological_critique"},
        "reconsiderations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim_relation_id": {"type": "string"},
                    "decision": {"type": "string", "enum": ["accept_openai", "reaffirm"]},
                    "final_relation_type": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["claim_relation_id", "decision", "final_relation_type", "reason"],
            },
        },
    },
    "required": ["scope_confirmation", "reconsiderations"],
}

REVIEW_SCHEMA = {
    "name": "wang_claim_relation_independent_review_v1",
    "strict": True,
    "schema": REVIEW_SCHEMA_BODY,
}
ADJUDICATION_SCHEMA = {
    "name": "wang_claim_relation_openai_adjudication_v1",
    "strict": True,
    "schema": ADJUDICATION_SCHEMA_BODY,
}
RECONSIDERATION_SCHEMA = {
    "name": "wang_claim_relation_claude_reconsideration_v1",
    "strict": True,
    "schema": RECONSIDERATION_SCHEMA_BODY,
}

CLAUDE_PROMPT = """你是独立的论证结构审核员。不要评价王教授的神学是否正确，也不要用外部神学体系纠正他。只审核每条ClaimRelation是否被两端主张及给出的教授原始证据支持。逐条输出：pass（关系类型和方向成立）、change（关系存在但类型或方向需改）、remove（现有证据不能建立该边）。必须覆盖输入中的每个relation id。"""
OPENAI_PROMPT = """你是第二位论证结构仲裁员。不要进行神学批评。根据主张、证据、原关系和Claude逐条意见，独立决定accept或reject Claude的判断。必须覆盖每个relation id。final_relation_type在保留或修改时写最终类型；删除时写空字符串。不能因为Claude提出意见就盲目接受。"""
CLAUDE_RECONSIDERATION_PROMPT = """你是第一位论证结构审核员，现在只重新考虑OpenAI拒绝你初审的条目。不要评价神学真伪。逐条决定accept_openai（接受OpenAI的结构判断）或reaffirm（坚持原判断）。只有reaffirm才转人工；不得为了消除分歧而自动让步。"""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_scope(response: dict[str, Any], rows_key: str) -> dict[str, Any]:
    """Anthropic sometimes omits a const-only field despite honoring the scoped prompt."""
    if rows_key == "relation_reviews" and not response.get(rows_key):
        alias = next(
            (name for name in ("reviews", "review_results") if response.get(name)),
            None,
        )
        if alias:
            response = dict(response)
            response[rows_key] = response.pop(alias)
    if response.get(rows_key) and not response.get("scope_confirmation"):
        response = dict(response)
        response["scope_confirmation"] = "relation_structure_no_theological_critique"
    return response


def _validate(response: dict[str, Any], ids: set[str], key: str) -> None:
    rows = response.get(key) or []
    row_ids = [str(item.get("claim_relation_id") or "") for item in rows]
    if len(row_ids) != len(set(row_ids)) or set(row_ids) != ids:
        raise CompositionReviewValidationError(
            f"{key} must cover every target relation exactly once; keys={sorted(response)}"
        )
    if response.get("scope_confirmation") != "relation_structure_no_theological_critique":
        raise CompositionReviewValidationError(
            f"review scope not confirmed: {response.get('scope_confirmation')!r}; "
            f"keys={sorted(response)}"
        )


def _projection(knowledge: dict[str, Any], ids: set[str]) -> dict[str, Any]:
    claims = {item["claim_id"]: item for item in knowledge.get("claims", [])}
    steps = {item["evidence_step_id"]: item for item in knowledge.get("evidence_steps", [])}

    def claim_row(claim_id: str) -> dict[str, Any]:
        claim = claims[claim_id]
        return {
            "claim_id": claim_id,
            "title": claim.get("title"),
            "evidence": [
                {
                    "evidence_step_id": evidence_id,
                    "statement": (steps.get(evidence_id) or {}).get("statement"),
                    "step_type": (steps.get(evidence_id) or {}).get("step_type"),
                    "support_eligibility": (steps.get(evidence_id) or {}).get("support_eligibility"),
                }
                for evidence_id in claim.get("evidence_step_ids", [])
                if evidence_id in steps
            ],
        }

    relations = []
    for relation in knowledge.get("claim_relations", []):
        relation_id = str(relation.get("claim_relation_id") or "")
        if relation_id not in ids:
            continue
        source_id = str(relation["source_id"])
        target_id = str(relation["target_id"])
        relations.append({
            "relation": relation,
            "source_claim": claim_row(source_id),
            "target_claim": claim_row(target_id),
        })
    found = {item["relation"]["claim_relation_id"] for item in relations}
    if found != ids:
        raise ValueError(f"missing target relations: {sorted(ids - found)}")
    return {"relations": relations}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge", type=Path, default=DEFAULT_KNOWLEDGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--claude-model", default="claude-sonnet-5")
    parser.add_argument("--openai-model", default="gpt-5.6-sol")
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    knowledge = json.loads(args.knowledge.read_text(encoding="utf-8"))
    projection = _projection(knowledge, TARGET_RELATION_IDS)
    user_input = json.dumps(projection, ensure_ascii=False, indent=2)
    claude = Stage1AnthropicClient(model=args.claude_model, timeout_seconds=300, max_retries=3, max_output_tokens=12000)
    openai = Stage1OpenAIClient(model=args.openai_model, reasoning_effort="medium", timeout_seconds=300, max_retries=3, max_output_tokens=12000)
    review = _normalize_scope(_generate_valid(
        claude, CLAUDE_PROMPT, user_input, REVIEW_SCHEMA,
        lambda response: _validate(
            _normalize_scope(response, "relation_reviews"),
            TARGET_RELATION_IDS,
            "relation_reviews",
        ),
    ), "relation_reviews")
    adjudication_input = user_input + "\n\n===== Claude审核 =====\n" + json.dumps(review, ensure_ascii=False, indent=2)
    adjudication = _normalize_scope(_generate_valid(
        openai, OPENAI_PROMPT, adjudication_input, ADJUDICATION_SCHEMA,
        lambda response: _validate(
            _normalize_scope(response, "adjudications"),
            TARGET_RELATION_IDS,
            "adjudications",
        ),
    ), "adjudications")
    review_by_id = {item["claim_relation_id"]: item for item in review["relation_reviews"]}
    adjudication_by_id = {item["claim_relation_id"]: item for item in adjudication["adjudications"]}
    rejected_ids = {
        relation_id
        for relation_id, item in adjudication_by_id.items()
        if item["decision"] == "reject"
    }
    reconsideration: dict[str, Any] | None = None
    reconsideration_by_id: dict[str, dict[str, Any]] = {}
    if rejected_ids:
        reconsideration_input = (
            user_input
            + "\n\n===== Claude初审 =====\n"
            + json.dumps(review, ensure_ascii=False, indent=2)
            + "\n\n===== OpenAI仲裁 =====\n"
            + json.dumps(adjudication, ensure_ascii=False, indent=2)
            + "\n\n只复审这些关系："
            + json.dumps(sorted(rejected_ids), ensure_ascii=False)
        )
        reconsideration = _normalize_scope(_generate_valid(
            claude,
            CLAUDE_RECONSIDERATION_PROMPT,
            reconsideration_input,
            RECONSIDERATION_SCHEMA,
            lambda response: _validate(
                _normalize_scope(response, "reconsiderations"),
                rejected_ids,
                "reconsiderations",
            ),
        ), "reconsiderations")
        reconsideration_by_id = {
            item["claim_relation_id"]: item
            for item in reconsideration["reconsiderations"]
        }
    outcomes = []
    for row in adjudication["adjudications"]:
        first = review_by_id[row["claim_relation_id"]]
        agreed = row["decision"] == "accept"
        reconsidered = reconsideration_by_id.get(row["claim_relation_id"])
        accepted_after_reconsideration = bool(
            reconsidered and reconsidered["decision"] == "accept_openai"
        )
        consensus = agreed or accepted_after_reconsideration
        final_relation_type = (
            row["final_relation_type"]
            if consensus
            else ""
        )
        outcomes.append({
            "claim_relation_id": row["claim_relation_id"],
            "claude_decision": first["decision"],
            "openai_decision": row["decision"],
            "claude_reconsideration": reconsidered["decision"] if reconsidered else None,
            "final_relation_type": final_relation_type,
            "status": "ai_consensus_reviewed" if consensus else "human_review_required",
            "reason": reconsidered["reason"] if reconsidered else row["reason"],
        })
    artifact = {
        "schema_version": "wang_claim_relation_ai_review_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(args.knowledge),
        "models": {"independent_reviewer": args.claude_model, "adjudicator": args.openai_model},
        "review_fingerprint": {
            "source_sha256": _sha256_text(user_input),
            "claude_prompt_sha256": _sha256_text(CLAUDE_PROMPT),
            "openai_prompt_sha256": _sha256_text(OPENAI_PROMPT),
            "reconsideration_prompt_sha256": _sha256_text(CLAUDE_RECONSIDERATION_PROMPT),
            "schema_version": "wang_claim_relation_ai_review_v1",
        },
        "review_policy": {
            "scope": "relation_structure_no_theological_critique",
            "scope_enforced_by_prompt_and_runner": True
        },
        "claude_review": review,
        "openai_adjudication": adjudication,
        "claude_reconsideration": reconsideration,
        "outcomes": outcomes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"reviewed": len(outcomes), "consensus": sum(item["status"] == "ai_consensus_reviewed" for item in outcomes), "human_required": sum(item["status"] == "human_review_required" for item in outcomes)}, ensure_ascii=False))
    return 1 if any(item["status"] == "human_review_required" for item in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
