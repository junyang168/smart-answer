"""Diagnose QA answer errors with Claude, OpenAI adjudication, and Claude reconsideration."""

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
DEFAULT_QA = Path("output/claim-layer/qa_validation_cases_v1.json")
DEFAULT_KNOWLEDGE = Path("output/claim-layer/shared_knowledge_pilot_v1.json")
DEFAULT_OUTPUT = Path("output/claim-layer/qa_answer_diagnostics_v1.json")
SCHEMA_VERSION = "wang_qa_answer_diagnostics_v1"
LAYERS = ["code_projection", "knowledge_data", "generation_prompt", "source_gap", "uncertain"]
ISSUE_TYPES = [
    "unsupported_sentence",
    "overstated_answer",
    "missing_qualification",
    "attribution_error",
    "relation_error",
    "retrieval_or_projection_error",
    "unanswered_gap",
    "duplicate_or_irrelevant_material",
    "other",
]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _archive(path: Path) -> Path | None:
    """Keep superseded diagnostics; they are the record of what AI judged when."""
    if not path.is_file():
        return None
    archive_dir = path.parent / "qa-diagnostic-generations"
    archive_dir.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fingerprint = str((payload.get("fingerprint") or {}).get("run_sha256") or "legacy")[:12]
    except (OSError, json.JSONDecodeError):
        fingerprint = "unreadable"
    target = archive_dir / f"{path.stem}.{fingerprint}.json"
    if not target.exists():
        target.write_bytes(path.read_bytes())
    return target


def run_fingerprint(*, qa_sha256: str, knowledge_sha256: str, claude_model: str, openai_model: str) -> dict[str, str]:
    identity = {
        "qa_sha256": qa_sha256,
        "knowledge_sha256": knowledge_sha256,
        "claude_prompt_sha256": _sha256(CLAUDE_PROMPT.encode("utf-8")),
        "openai_prompt_sha256": _sha256(OPENAI_PROMPT.encode("utf-8")),
        "reconsideration_prompt_sha256": _sha256(RECONSIDERATION_PROMPT.encode("utf-8")),
        "independent_reviewer": claude_model,
        "adjudicator": openai_model,
        "schema_version": SCHEMA_VERSION,
    }
    identity["run_sha256"] = _sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return identity


def _scope_schema(rows_key: str, row_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {
                "type": "string",
                "const": "answer_fidelity_and_system_diagnosis_no_theological_critique",
            },
            rows_key: {"type": "array", "items": row_schema},
        },
        "required": ["scope_confirmation", rows_key],
    }


ISSUE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "issue_id": {"type": "string"},
        "issue_type": {"type": "string", "enum": ISSUE_TYPES},
        "suspected_layer": {"type": "string", "enum": LAYERS},
        "answer_excerpt": {"type": "string"},
        "claim_ids": {"type": "array", "items": {"type": "string"}},
        "evidence_step_ids": {"type": "array", "items": {"type": "string"}},
        "explanation": {"type": "string"},
        "recommended_action": {"type": "string"},
    },
    "required": [
        "issue_id",
        "issue_type",
        "suspected_layer",
        "answer_excerpt",
        "claim_ids",
        "evidence_step_ids",
        "explanation",
        "recommended_action",
    ],
}

REVIEW_ROW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "case_id": {"type": "string"},
        "decision": {"type": "string", "enum": ["pass", "changes_required"]},
        "answer_state_assessment": {
            "type": "string",
            "enum": ["supported", "too_strong", "too_weak"],
        },
        "issues": {"type": "array", "items": ISSUE_SCHEMA},
        "rationale": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": [
        "case_id",
        "decision",
        "answer_state_assessment",
        "issues",
        "rationale",
        "confidence",
    ],
}

ADJUDICATION_ROW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "issue_id": {"type": "string"},
        "decision": {"type": "string", "enum": ["accept", "reject"]},
        "earliest_error_layer": {"type": "string", "enum": LAYERS},
        "reason": {"type": "string"},
        "repair_action": {"type": "string"},
    },
    "required": ["issue_id", "decision", "earliest_error_layer", "reason", "repair_action"],
}

RECONSIDERATION_ROW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "issue_id": {"type": "string"},
        "decision": {"type": "string", "enum": ["withdraw", "reaffirm"]},
        "earliest_error_layer": {"type": "string", "enum": LAYERS},
        "reason": {"type": "string"},
    },
    "required": ["issue_id", "decision", "earliest_error_layer", "reason"],
}

REVIEW_SCHEMA = {
    "name": "wang_qa_answer_independent_review_v1",
    "strict": True,
    "schema": _scope_schema("case_reviews", REVIEW_ROW_SCHEMA),
}
ADJUDICATION_SCHEMA = {
    "name": "wang_qa_answer_openai_adjudication_v1",
    "strict": True,
    "schema": _scope_schema("issue_adjudications", ADJUDICATION_ROW_SCHEMA),
}
RECONSIDERATION_SCHEMA = {
    "name": "wang_qa_answer_claude_reconsideration_v1",
    "strict": True,
    "schema": _scope_schema("issue_reconsiderations", RECONSIDERATION_ROW_SCHEMA),
}

CLAUDE_PROMPT = """你是独立的答案忠实度与系统诊断审核员。不得评价王教授的神学是否正确，不得使用外部神学知识纠正或补充答案。只根据输入中的共享主张、关系、证据和原始引文，检查候选答案是否准确回答问题、是否夸大、遗漏限定或误归属。发现问题时必须判断错误最早可能来自 code_projection、knowledge_data、generation_prompt、source_gap 或 uncertain。不要只建议重写答案；要说明应修哪一层。pass 时 issues 必须为空；changes_required 时至少一项。必须覆盖每个 case_id。"""

OPENAI_PROMPT = """你是第二位答案诊断仲裁员。不得进行神学批评，也不得因 Claude 提出意见就盲目接受。逐项核对答案、主张、关系、证据和投影资料，决定 accept 或 reject Claude 的问题诊断，并指出最早出错层与具体修复动作。必须覆盖每个 Claude issue_id。"""

RECONSIDERATION_PROMPT = """你是第一位答案审核员。OpenAI 拒绝了你的一些诊断；请只复审这些 issue。不得进行神学批评。决定 withdraw（接受 OpenAI 反驳并撤回）或 reaffirm（仍坚持）；只有 reaffirm 才转人工。必须覆盖指定的每个 issue_id。"""


def build_projection(qa: dict[str, Any], knowledge: dict[str, Any]) -> dict[str, Any]:
    claims = {item["claim_id"]: item for item in knowledge.get("claims", [])}
    evidence = {item["evidence_step_id"]: item for item in knowledge.get("evidence_steps", [])}
    fragments = {item["fragment_id"]: item for item in knowledge.get("source_fragments", [])}
    questions = {item["question_id"]: item for item in knowledge.get("questions", [])}
    positions = {item["position_id"]: item for item in knowledge.get("position_nodes", [])}
    relations = knowledge.get("claim_relations", [])

    def claim_row(claim_id: str) -> dict[str, Any]:
        claim = claims.get(claim_id) or {"claim_id": claim_id, "missing": True}
        all_step_ids = list(claim.get("evidence_step_ids", []))
        if "eligible_evidence_step_ids" in claim:
            eligible_ids = set(claim.get("eligible_evidence_step_ids") or [])
        else:
            eligible_ids = {
                step_id
                for step_id in all_step_ids
                if str((evidence.get(step_id) or {}).get("support_eligibility") or "eligible")
                in {"eligible", "eligible_with_label"}
            }
        context_ids = set(claim.get("context_evidence_step_ids") or [])
        withheld_ids = set(claim.get("withheld_evidence_step_ids") or [])

        def step_row(step_id: str) -> dict[str, Any]:
            step = evidence.get(step_id) or {}
            fragment_ids = list(step.get("source_fragment_ids") or [])
            if not fragment_ids and step.get("source_fragment_id"):
                fragment_ids = [step["source_fragment_id"]]
            return {
                "evidence_step_id": step_id,
                "function": step.get("function") or step.get("step_type"),
                "statement": step.get("statement"),
                "speaker": step.get("speaker"),
                "stance": step.get("stance"),
                "support_eligibility": step.get("support_eligibility"),
                "source_fragments": [
                    {
                        "fragment_id": fragment_id,
                        "verbatim_excerpt": (fragments.get(fragment_id) or {}).get("verbatim_excerpt"),
                        "transcript_id": (fragments.get(fragment_id) or {}).get("transcript_id"),
                        "media_time": (fragments.get(fragment_id) or {}).get("media_time"),
                    }
                    for fragment_id in fragment_ids
                ],
            }

        return {
            "claim_id": claim_id,
            "title": claim.get("title"),
            "claim_type": claim.get("claim_type"),
            "scripture_refs": claim.get("scripture_refs", []),
            "eligible_evidence": [step_row(item) for item in all_step_ids if item in eligible_ids],
            "context_evidence": [step_row(item) for item in all_step_ids if item in context_ids],
            "withheld_evidence": [step_row(item) for item in all_step_ids if item in withheld_ids],
        }

    known_ids = set(claims) | set(positions)

    def node_title(node_id: str) -> str | None:
        return (claims.get(node_id) or positions.get(node_id) or {}).get("title")

    def relation_row(relation: dict[str, Any], relevant_ids: set[str]) -> dict[str, Any]:
        row = dict(relation)
        for end in ("source", "target"):
            node_id = relation.get(f"{end}_id")
            row[f"{end}_title"] = node_title(node_id)
            if node_id not in relevant_ids:
                row[f"{end}_outside_case"] = True
        return row

    cases = []
    for case in qa.get("cases", []):
        answer_ids = set(case.get("answer_claim_ids", []))
        context_ids = set(case.get("context_claim_ids", []))
        # Positions belong here too: a `refutes` edge to the opposed view is the
        # evidence that the professor rejected it, which is exactly what an
        # attribution question turns on.
        relevant_ids = answer_ids | context_ids | set(case.get("opposed_position_ids", []))
        cases.append(
            {
                "case": case,
                "answer_claims": [claim_row(item) for item in case.get("answer_claim_ids", [])],
                "context_claims": [claim_row(item) for item in case.get("context_claim_ids", [])],
                # Keep every relation that touches this case, including ones
                # reaching outside it -- a `qualifies` edge to an unlisted claim
                # is precisely the missing qualification the review must catch.
                # Only relations whose endpoint is absent from the whole package
                # are dropped, because nothing can be said about those.
                "claim_relations": [
                    relation_row(item, relevant_ids)
                    for item in relations
                    if {item.get("source_id"), item.get("target_id")} & relevant_ids
                    and item.get("source_id") in known_ids
                    and item.get("target_id") in known_ids
                ],
                "source_questions": [
                    questions.get(item) or {"question_id": item, "missing": True}
                    for item in case.get("source_question_ids", [])
                ],
                "opposed_positions": [
                    positions.get(item) or {"position_id": item, "missing": True}
                    for item in case.get("opposed_position_ids", [])
                ],
            }
        )
    return {
        "review_boundary": {
            "scope": "answer fidelity and earliest error-layer diagnosis",
            "no_theological_critique": True,
            "product_independence": "QA does not use exposition or topic prose as answer evidence",
        },
        "cases": cases,
    }


def _validate_review(response: dict[str, Any], case_ids: set[str]) -> None:
    if response.get("scope_confirmation") != "answer_fidelity_and_system_diagnosis_no_theological_critique":
        raise CompositionReviewValidationError("QA review scope not confirmed")
    rows = response.get("case_reviews") or []
    ids = [item.get("case_id") for item in rows]
    if len(ids) != len(set(ids)) or set(ids) != case_ids:
        raise CompositionReviewValidationError("QA review must cover every case exactly once")
    issue_ids: list[str] = []
    for row in rows:
        issues = row.get("issues") or []
        if row.get("decision") == "pass" and issues:
            raise CompositionReviewValidationError("pass case cannot contain issues")
        if row.get("decision") == "changes_required" and not issues:
            raise CompositionReviewValidationError("changes_required case needs issues")
        issue_ids.extend(str(item.get("issue_id") or "") for item in issues)
    if "" in issue_ids or len(issue_ids) != len(set(issue_ids)):
        raise CompositionReviewValidationError("QA issue ids must be non-empty and unique")


def _validate_issue_rows(response: dict[str, Any], issue_ids: set[str], rows_key: str) -> None:
    if response.get("scope_confirmation") != "answer_fidelity_and_system_diagnosis_no_theological_critique":
        raise CompositionReviewValidationError("QA adjudication scope not confirmed")
    ids = [item.get("issue_id") for item in response.get(rows_key) or []]
    if len(ids) != len(set(ids)) or set(ids) != issue_ids:
        raise CompositionReviewValidationError(f"{rows_key} must cover every issue exactly once")


def _repair_target(layer: str) -> dict[str, str]:
    return {
        "code_projection": {"target": "backend/frontend projection", "verification": "API/UI regression test"},
        "knowledge_data": {"target": "shared knowledge claim/relation/evidence", "verification": "dependency invalidation and affected-product rerun"},
        "generation_prompt": {"target": "QA generation prompt/template", "verification": "rerun fixed QA regression suite"},
        "source_gap": {"target": "answer state and corpus-search queue", "verification": "downgrade to partial/unanswered until new source is verified"},
        "uncertain": {"target": "human diagnostic queue", "verification": "manual earliest-layer decision"},
    }[layer]


def _namespace_review_issues(case_id: str, row: dict[str, Any]) -> dict[str, Any]:
    for issue_index, issue in enumerate(row.get("issues") or [], start=1):
        raw_issue_id = str(issue.get("issue_id") or f"ISS-{issue_index}")
        issue["issue_id"] = f"{case_id}::{raw_issue_id}"
    return row


def _final_issue_status(
    adjudicated: dict[str, Any], reconsidered: dict[str, Any] | None
) -> str:
    if adjudicated["decision"] == "accept":
        return "ai_consensus_issue"
    if reconsidered and reconsidered["decision"] == "reaffirm":
        return "human_diagnostic_required"
    return "withdrawn"


def run(
    *,
    qa_path: Path,
    knowledge_path: Path,
    output_path: Path,
    claude_model: str,
    openai_model: str,
    force: bool = False,
) -> dict[str, Any]:
    qa_bytes = qa_path.read_bytes()
    knowledge_bytes = knowledge_path.read_bytes()
    fingerprint = run_fingerprint(
        qa_sha256=_sha256(qa_bytes),
        knowledge_sha256=_sha256(knowledge_bytes),
        claude_model=claude_model,
        openai_model=openai_model,
    )
    if not force and output_path.is_file():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        if (existing.get("fingerprint") or {}).get("run_sha256") == fingerprint["run_sha256"]:
            print("unchanged inputs and configuration; reusing existing diagnostics", flush=True)
            return existing
    qa = json.loads(qa_bytes)
    knowledge = json.loads(knowledge_bytes)
    projection = build_projection(qa, knowledge)
    user_input = json.dumps(projection, ensure_ascii=False, indent=2)
    case_ids = {item["case_id"] for item in qa.get("cases", [])}

    # Review one case at a time.  A full-corpus request lets adaptive thinking
    # consume the entire output budget before Claude emits JSON; bounded calls
    # are also independently retryable and easier to audit.
    claude = Stage1AnthropicClient(model=claude_model, timeout_seconds=300, max_retries=3, max_output_tokens=8000)
    openai = Stage1OpenAIClient(model=openai_model, reasoning_effort="medium", timeout_seconds=300, max_retries=3, max_output_tokens=8000)
    review_rows: list[dict[str, Any]] = []
    case_projection_by_id: dict[str, dict[str, Any]] = {}
    total_cases = len(projection["cases"])
    for case_index, case_projection in enumerate(projection["cases"], start=1):
        case_id = case_projection["case"]["case_id"]
        print(f"[Claude review {case_index}/{total_cases}] {case_id}", flush=True)
        case_projection_by_id[case_id] = case_projection
        scoped_input = json.dumps(
            {"review_boundary": projection["review_boundary"], "cases": [case_projection]},
            ensure_ascii=False,
            indent=2,
        )
        scoped_review = _generate_valid(
            claude,
            CLAUDE_PROMPT,
            scoped_input,
            REVIEW_SCHEMA,
            lambda response, expected={case_id}: _validate_review(response, expected),
        )
        scoped_row = scoped_review["case_reviews"][0]
        # Issue IDs are only unique inside one model response.  Since reviews
        # run per case, namespace them before combining the responses; otherwise
        # common IDs such as "ISS-1" can bind an adjudication to the wrong case.
        review_rows.append(_namespace_review_issues(case_id, scoped_row))
    review = {
        "scope_confirmation": "answer_fidelity_and_system_diagnosis_no_theological_critique",
        "case_reviews": review_rows,
    }
    _validate_review(review, case_ids)
    issues = [issue for row in review["case_reviews"] for issue in row.get("issues", [])]
    issue_ids = {item["issue_id"] for item in issues}

    if issue_ids:
        adjudication_rows = []
        review_by_case = {item["case_id"]: item for item in review_rows}
        for issue_index, issue in enumerate(issues, start=1):
            case_id = next(
                item["case_id"] for item in review_rows if issue in item.get("issues", [])
            )
            print(
                f"[OpenAI adjudication {issue_index}/{len(issues)}] {issue['issue_id']} ({case_id})",
                flush=True,
            )
            scoped_input = json.dumps(
                {
                    "review_boundary": projection["review_boundary"],
                    "cases": [case_projection_by_id[case_id]],
                    "claude_diagnosis": {**review_by_case[case_id], "issues": [issue]},
                },
                ensure_ascii=False,
                indent=2,
            )
            scoped_adjudication = _generate_valid(
                openai,
                OPENAI_PROMPT,
                scoped_input,
                ADJUDICATION_SCHEMA,
                lambda response, expected={issue["issue_id"]}: _validate_issue_rows(
                    response, expected, "issue_adjudications"
                ),
            )
            adjudication_rows.extend(scoped_adjudication["issue_adjudications"])
        adjudication = {
            "scope_confirmation": "answer_fidelity_and_system_diagnosis_no_theological_critique",
            "issue_adjudications": adjudication_rows,
        }
    else:
        adjudication = {
            "scope_confirmation": "answer_fidelity_and_system_diagnosis_no_theological_critique",
            "issue_adjudications": [],
        }
    adjudication_by_id = {item["issue_id"]: item for item in adjudication["issue_adjudications"]}
    rejected_ids = {item["issue_id"] for item in adjudication["issue_adjudications"] if item["decision"] == "reject"}

    reconsideration = None
    reconsideration_by_id: dict[str, dict[str, Any]] = {}
    if rejected_ids:
        reconsideration_rows = []
        issue_by_id = {item["issue_id"]: item for item in issues}
        case_by_issue = {
            issue["issue_id"]: row["case_id"]
            for row in review_rows
            for issue in row.get("issues", [])
        }
        for issue_id in sorted(rejected_ids):
            case_id = case_by_issue[issue_id]
            print(f"[Claude reconsideration] {issue_id} ({case_id})", flush=True)
            scoped_input = json.dumps(
                {
                    "review_boundary": projection["review_boundary"],
                    "case": case_projection_by_id[case_id],
                    "claude_issue": issue_by_id[issue_id],
                    "openai_adjudication": next(
                        item for item in adjudication["issue_adjudications"] if item["issue_id"] == issue_id
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
            scoped_reconsideration = _generate_valid(
                claude,
                RECONSIDERATION_PROMPT,
                scoped_input,
                RECONSIDERATION_SCHEMA,
                lambda response, expected={issue_id}: _validate_issue_rows(
                    response, expected, "issue_reconsiderations"
                ),
            )
            reconsideration_rows.extend(scoped_reconsideration["issue_reconsiderations"])
        reconsideration = {
            "scope_confirmation": "answer_fidelity_and_system_diagnosis_no_theological_critique",
            "issue_reconsiderations": reconsideration_rows,
        }
        reconsideration_by_id = {
            item["issue_id"]: item for item in reconsideration["issue_reconsiderations"]
        }

    case_by_issue = {
        issue["issue_id"]: row["case_id"]
        for row in review["case_reviews"]
        for issue in row.get("issues", [])
    }
    outcomes = []
    repair_queue = []
    for issue in issues:
        adjudicated = adjudication_by_id[issue["issue_id"]]
        reconsidered = reconsideration_by_id.get(issue["issue_id"])
        status = _final_issue_status(adjudicated, reconsidered)
        human_required = status == "human_diagnostic_required"
        layer = (
            reconsidered["earliest_error_layer"]
            if reconsidered and human_required
            else adjudicated["earliest_error_layer"]
        )
        outcome = {
            "issue_id": issue["issue_id"],
            "case_id": case_by_issue[issue["issue_id"]],
            "issue_type": issue["issue_type"],
            "claude_suspected_layer": issue["suspected_layer"],
            "openai_decision": adjudicated["decision"],
            "claude_reconsideration": reconsidered["decision"] if reconsidered else None,
            "earliest_error_layer": layer,
            "status": status,
            "explanation": issue["explanation"],
            "repair_action": adjudicated["repair_action"],
        }
        outcomes.append(outcome)
        if status == "ai_consensus_issue":
            repair_queue.append(
                {
                    "repair_id": f"REPAIR-{issue['issue_id']}",
                    "case_id": outcome["case_id"],
                    "issue_id": issue["issue_id"],
                    "target_layer": layer,
                    **_repair_target(layer),
                    "action": adjudicated["repair_action"],
                    "status": "pending_repair",
                }
            )

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "qa_path": str(qa_path),
            "knowledge_path": str(knowledge_path),
            "qa_sha256": _sha256(qa_bytes),
            "knowledge_sha256": _sha256(knowledge_bytes),
        },
        "models": {"independent_reviewer": claude_model, "adjudicator": openai_model},
        "fingerprint": {
            **fingerprint,
            "projection_sha256": _sha256(user_input.encode("utf-8")),
        },
        "review_policy": {
            "scope": "answer_fidelity_and_system_diagnosis_no_theological_critique",
            "repair_the_earliest_faulty_layer": True,
            "never_patch_only_final_answer": True,
        },
        "claude_review": review,
        "openai_adjudication": adjudication,
        "claude_reconsideration": reconsideration,
        "outcomes": outcomes,
        "repair_queue": repair_queue,
        "summary": {
            "cases": len(case_ids),
            "passed": sum(item["decision"] == "pass" for item in review["case_reviews"]),
            "issues": len(outcomes),
            "ai_consensus_issues": sum(item["status"] == "ai_consensus_issue" for item in outcomes),
            "human_required": sum(item["status"] == "human_diagnostic_required" for item in outcomes),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _archive(output_path)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa", type=Path, default=DEFAULT_QA)
    parser.add_argument("--knowledge", type=Path, default=DEFAULT_KNOWLEDGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--claude-model", default="claude-sonnet-5")
    parser.add_argument("--openai-model", default="gpt-5.6-sol")
    parser.add_argument("--force", action="store_true", help="Re-run even when inputs are unchanged.")
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    artifact = run(
        qa_path=args.qa,
        knowledge_path=args.knowledge,
        output_path=args.output,
        claude_model=args.claude_model,
        openai_model=args.openai_model,
        force=args.force,
    )
    print(json.dumps(artifact["summary"], ensure_ascii=False))
    return 1 if artifact["summary"]["human_required"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
