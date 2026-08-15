from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "matthew-exposition-authoring.v1"
AUTHOR_STATUSES = {"drafted", "plan_change_required"}
SUPPLEMENT_OPERATIONS = {"corroborate", "extend", "qualify", "tension", "route_out"}
QUALITY_DIMENSION_IDS = [
    "source_and_exegesis",
    "base_manuscript_preservation",
    "exegetical_reasoning",
    "argument_organization",
    "general_reader_readability",
    "editorial_voice_restraint",
    "approved_written_style",
    "theological_tension_and_attribution",
    "concision_without_compression",
    "pastoral_theological_landing",
]
HARD_FAILURE_IDS = [
    "load_bearing_base_argument_removed_or_reordered",
    "editorial_or_ai_inference_attributed_to_professor",
    "material_source_tension_silently_harmonized",
    "production_language_dominates_reader_prose",
    "exegetical_observation_inference_conclusion_chain_missing",
]


AUTHOR_RESULT_SCHEMA: dict[str, Any] = {
    "name": "matthew_exposition_author_result_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": sorted(AUTHOR_STATUSES)},
            "manuscript_markdown": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "section_id": {"type": "string"},
                        "decision_ids": {"type": "array", "items": {"type": "string"}},
                        "base_step_ids_preserved": {"type": "array", "items": {"type": "string"}},
                        "claim_ids_used": {"type": "array", "items": {"type": "string"}},
                        "integration_operations": {"type": "array", "items": {"type": "string"}},
                        "omissions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "step_id": {"type": "string"},
                                    "reason": {"type": "string"},
                                },
                                "required": ["step_id", "reason"],
                            },
                        },
                        "output_anchor": {"type": "string"},
                    },
                    "required": [
                        "section_id",
                        "decision_ids",
                        "base_step_ids_preserved",
                        "claim_ids_used",
                        "integration_operations",
                        "omissions",
                        "output_anchor",
                    ],
                },
            },
            "plan_change_requests": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "request_id": {"type": "string"},
                        "reason": {"type": "string"},
                        "proposed_change": {"type": "string"},
                        "affected_decision_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["request_id", "reason", "proposed_change", "affected_decision_ids"],
                },
            },
        },
        "required": ["status", "manuscript_markdown", "sections", "plan_change_requests"],
    },
}


EDITORIAL_REVIEW_SCHEMA: dict[str, Any] = {
    "name": "matthew_exposition_editorial_review_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {"type": "string", "enum": ["writing_quality_and_base_preservation"]},
            "summary": {"type": "string"},
            "dimension_scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "dimension_id": {"type": "string", "enum": QUALITY_DIMENSION_IDS},
                        "score": {"type": "integer"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["dimension_id", "score", "evidence"],
                },
            },
            "hard_failures": {"type": "array", "items": {"type": "string", "enum": HARD_FAILURE_IDS}},
            "section_reviews": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "section_id": {"type": "string"},
                        "base_step_ids_preserved": {"type": "array", "items": {"type": "string"}},
                        "assessment": {"type": "string"},
                    },
                    "required": ["section_id", "base_step_ids_preserved", "assessment"],
                },
            },
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "finding_id": {"type": "string"},
                        "dimension_id": {"type": "string", "enum": QUALITY_DIMENSION_IDS},
                        "section_id": {"type": "string"},
                        "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                        "blocking": {"type": "boolean"},
                        "manuscript_anchor": {"type": "string"},
                        "explanation": {"type": "string"},
                        "recommended_action": {"type": "string"},
                    },
                    "required": [
                        "finding_id", "dimension_id", "section_id", "severity", "blocking",
                        "manuscript_anchor", "explanation", "recommended_action"
                    ],
                },
            },
        },
        "required": ["scope_confirmation", "summary", "dimension_scores", "hard_failures", "section_reviews", "findings"],
    },
}


ADJUDICATION_SCHEMA: dict[str, Any] = {
    "name": "matthew_exposition_editorial_adjudication_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "adjudications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "finding_id": {"type": "string"},
                        "decision": {"type": "string", "enum": ["accept", "reject"]},
                        "rationale": {"type": "string"},
                    },
                    "required": ["finding_id", "decision", "rationale"],
                },
            }
        },
        "required": ["adjudications"],
    },
}


RECONSIDERATION_SCHEMA: dict[str, Any] = {
    "name": "matthew_exposition_editorial_reconsideration_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "reconsiderations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "finding_id": {"type": "string"},
                        "decision": {"type": "string", "enum": ["withdraw", "maintain"]},
                        "rationale": {"type": "string"},
                    },
                    "required": ["finding_id", "decision", "rationale"],
                },
            }
        },
        "required": ["reconsiderations"],
    },
}


REVISION_SCHEMA: dict[str, Any] = {
    "name": "matthew_exposition_author_revision_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": ["revised", "plan_change_required"]},
            "manuscript_markdown": {"type": "string"},
            "sections": AUTHOR_RESULT_SCHEMA["schema"]["properties"]["sections"],
            "finding_dispositions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "finding_id": {"type": "string"},
                        "status": {"type": "string", "enum": ["resolved", "deferred"]},
                        "note": {"type": "string"},
                    },
                    "required": ["finding_id", "status", "note"],
                },
            },
            "plan_change_requests": AUTHOR_RESULT_SCHEMA["schema"]["properties"]["plan_change_requests"],
        },
        "required": ["status", "manuscript_markdown", "sections", "finding_dispositions", "plan_change_requests"],
    },
}


class AuthoringContractError(ValueError):
    """Raised when an authoring artifact violates a versioned handoff contract."""


def validate_strict_schema(value: Any, schema_wrapper: dict[str, Any]) -> None:
    """Validate the strict JSON-schema subset used by this pipeline.

    The Anthropic adapter treats schemas as prompt guidance, so cached and
    Anthropic-produced artifacts need the same local enforcement as OpenAI
    structured outputs.
    """

    schema = schema_wrapper.get("schema", schema_wrapper)

    def visit(item: Any, node: dict[str, Any], field: str) -> None:
        if "enum" in node and item not in node["enum"]:
            raise AuthoringContractError(f"{field} is not in enum: {item!r}")
        node_type = node.get("type")
        if node_type == "object":
            if not isinstance(item, dict):
                raise AuthoringContractError(f"{field} must be an object")
            required = set(node.get("required", []))
            missing = required - set(item)
            if missing:
                raise AuthoringContractError(f"{field} missing required fields: {sorted(missing)}")
            properties = node.get("properties", {})
            if node.get("additionalProperties") is False:
                extra = set(item) - set(properties)
                if extra:
                    raise AuthoringContractError(f"{field} has unknown fields: {sorted(extra)}")
            for key, child in properties.items():
                if key in item:
                    visit(item[key], child, f"{field}.{key}")
        elif node_type == "array":
            if not isinstance(item, list):
                raise AuthoringContractError(f"{field} must be an array")
            child = node.get("items")
            if child:
                for index, entry in enumerate(item):
                    visit(entry, child, f"{field}[{index}]")
        elif node_type == "string":
            if not isinstance(item, str):
                raise AuthoringContractError(f"{field} must be a string")
        elif node_type == "integer":
            if not isinstance(item, int) or isinstance(item, bool):
                raise AuthoringContractError(f"{field} must be an integer")
        elif node_type == "boolean":
            if not isinstance(item, bool):
                raise AuthoringContractError(f"{field} must be a boolean")

    visit(value, schema, "result")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def generation_fingerprint(
    *,
    inputs: dict[str, Any],
    prompt_text: str,
    schema: dict[str, Any],
    model: str,
    reasoning: str,
) -> str:
    return sha256_text(
        canonical_json(
            {
                "inputs": inputs,
                "prompt_sha256": sha256_text(prompt_text),
                "schema_sha256": sha256_text(canonical_json(schema)),
                "model": model,
                "reasoning": reasoning,
            }
        )
    )


def _require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AuthoringContractError(f"{field} must be an object")
    return value


def _require_nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuthoringContractError(f"{field} must be a non-empty string")
    return value


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for value in values:
        if value in seen:
            repeated.add(value)
        seen.add(value)
    return repeated


def validate_base_contract(contract: dict[str, Any], *, verify_source: bool = True) -> None:
    if contract.get("schema_version") != "matthew-exposition-base-contract.v1":
        raise AuthoringContractError("unsupported base contract schema_version")
    _require_nonempty_string(contract.get("contract_id"), "contract_id")
    _require_nonempty_string(contract.get("passage"), "passage")
    if contract.get("authoring_mode") != "verified_manuscript_integration":
        raise AuthoringContractError("unsupported authoring_mode")
    if contract.get("status") != "editor_confirmed":
        raise AuthoringContractError("base contract must be editor_confirmed")
    base_source = _require_mapping(contract.get("base_source"), "base_source")
    source_records = [base_source, *contract.get("additional_base_sources", [])]
    base_texts: dict[str, str] = {}
    for source_index, source_value in enumerate(source_records):
        source = _require_mapping(source_value, f"base_sources[{source_index}]")
        source_id = _require_nonempty_string(source.get("source_id"), "base_source.source_id")
        source_path = Path(_require_nonempty_string(source.get("path"), "base_source.path"))
        expected_sha = _require_nonempty_string(source.get("sha256"), "base_source.sha256")
        if source.get("fidelity_status") != "current_passed":
            raise AuthoringContractError("base source fidelity_status must be current_passed")
        if verify_source:
            if not source_path.is_file():
                raise AuthoringContractError(f"base source does not exist: {source_path}")
            actual_sha = sha256_file(source_path)
            if actual_sha != expected_sha:
                raise AuthoringContractError(
                    f"stale base source: expected {expected_sha}, got {actual_sha}"
                )
            base_text = source_path.read_text(encoding="utf-8")
            anchor = _require_nonempty_string(source.get("section_anchor"), "base_source.section_anchor")
            if anchor not in base_text:
                raise AuthoringContractError(f"base source section_anchor not found: {anchor}")
            base_texts[source_id] = base_text
    if duplicates := _duplicates(
        _require_nonempty_string(item.get("source_id"), "base_source.source_id")
        for item in source_records
    ):
        raise AuthoringContractError(f"duplicate base source_ids: {sorted(duplicates)}")

    sections = contract.get("sections")
    if not isinstance(sections, list) or not sections:
        raise AuthoringContractError("sections must be a non-empty array")
    section_ids: list[str] = []
    step_ids: list[str] = []
    for section_index, section_value in enumerate(sections):
        section = _require_mapping(section_value, f"sections[{section_index}]")
        section_id = _require_nonempty_string(
            section.get("section_id"), f"sections[{section_index}].section_id"
        )
        section_ids.append(section_id)
        decision_ids = section.get("decision_ids")
        if not isinstance(decision_ids, list) or not decision_ids:
            raise AuthoringContractError(f"section {section_id} requires decision_ids")
        steps = section.get("required_argument_steps")
        if not isinstance(steps, list) or not steps:
            raise AuthoringContractError(f"section {section_id} requires argument steps")
        for step_index, step_value in enumerate(steps):
            step = _require_mapping(step_value, f"{section_id}.steps[{step_index}]")
            step_ids.append(
                _require_nonempty_string(step.get("step_id"), f"{section_id}.step_id")
            )
            _require_nonempty_string(step.get("statement"), f"{section_id}.statement")
            if verify_source:
                step_source_id = _require_nonempty_string(
                    step.get("source_id", base_source["source_id"]),
                    f"{section_id}.source_id",
                )
                if step_source_id not in base_texts:
                    raise AuthoringContractError(
                        f"unknown base source_id for {step['step_id']}: {step_source_id}"
                    )
                excerpt = _require_nonempty_string(
                    step.get("source_excerpt"), f"{section_id}.source_excerpt"
                )
                if excerpt not in base_texts[step_source_id]:
                    raise AuthoringContractError(
                        f"base step excerpt not found for {step['step_id']}"
                    )

    if duplicates := _duplicates(section_ids):
        raise AuthoringContractError(f"duplicate section_ids: {sorted(duplicates)}")
    if duplicates := _duplicates(step_ids):
        raise AuthoringContractError(f"duplicate step_ids: {sorted(duplicates)}")

    for item_index, item_value in enumerate(contract.get("supplemental_material", [])):
        item = _require_mapping(item_value, f"supplemental_material[{item_index}]")
        if item.get("operation") not in SUPPLEMENT_OPERATIONS:
            raise AuthoringContractError(
                f"unsupported supplemental operation: {item.get('operation')}"
            )


def validate_author_result(
    result: dict[str, Any],
    *,
    contract: dict[str, Any],
    plan: dict[str, Any],
    valid_claim_ids: set[str] | None = None,
) -> None:
    validate_base_contract(contract)
    status = result.get("status")
    if status not in AUTHOR_STATUSES:
        raise AuthoringContractError(f"unsupported author status: {status}")

    requests = result.get("plan_change_requests", [])
    if not isinstance(requests, list):
        raise AuthoringContractError("plan_change_requests must be an array")
    if status == "plan_change_required":
        if not requests:
            raise AuthoringContractError("plan_change_required needs at least one request")
        if result.get("manuscript_markdown"):
            raise AuthoringContractError("plan-change handoff must not masquerade as a final draft")
        return
    if requests:
        raise AuthoringContractError("drafted result cannot contain unresolved plan changes")

    manuscript = _require_nonempty_string(
        result.get("manuscript_markdown"), "manuscript_markdown"
    )
    all_plan_decisions = {
        decision.get("decision_id")
        for decision in plan.get("decisions", [])
        if isinstance(decision, dict) and decision.get("decision_id")
    }
    plan_decisions = {
        decision_id
        for section in contract["sections"]
        for decision_id in section["decision_ids"]
    }
    missing_from_plan = plan_decisions - all_plan_decisions
    if missing_from_plan:
        raise AuthoringContractError(
            f"contract decision_ids missing from plan: {sorted(missing_from_plan)}"
        )
    contract_steps = {
        step["step_id"]
        for section in contract["sections"]
        for step in section["required_argument_steps"]
    }
    required_steps = {
        step["step_id"]
        for section in contract["sections"]
        for step in section["required_argument_steps"]
        if step.get("required", True)
    }
    authored_sections = result.get("sections")
    if not isinstance(authored_sections, list) or not authored_sections:
        raise AuthoringContractError("drafted result requires sections")

    covered_decisions: list[str] = []
    preserved_steps: list[str] = []
    omitted_steps: list[str] = []
    used_claim_ids: list[str] = []
    for section_index, section_value in enumerate(authored_sections):
        section = _require_mapping(section_value, f"sections[{section_index}]")
        decision_ids = section.get("decision_ids", [])
        if not isinstance(decision_ids, list) or not decision_ids:
            raise AuthoringContractError("each authored section requires decision_ids")
        unknown_decisions = set(decision_ids) - plan_decisions
        if unknown_decisions:
            raise AuthoringContractError(f"unknown decision_ids: {sorted(unknown_decisions)}")
        covered_decisions.extend(decision_ids)
        preserved_steps.extend(section.get("base_step_ids_preserved", []))
        used_claim_ids.extend(section.get("claim_ids_used", []))
        omissions = section.get("omissions", [])
        if not isinstance(omissions, list):
            raise AuthoringContractError("omissions must be an array")
        for omission in omissions:
            omission = _require_mapping(omission, "omission")
            omitted_steps.append(_require_nonempty_string(omission.get("step_id"), "omission.step_id"))
            _require_nonempty_string(omission.get("reason"), "omission.reason")
        anchor = _require_nonempty_string(section.get("output_anchor"), "output_anchor")
        if anchor not in manuscript:
            raise AuthoringContractError(f"output anchor not found in manuscript: {anchor}")

    if duplicates := _duplicates(covered_decisions):
        raise AuthoringContractError(f"decision covered more than once: {sorted(duplicates)}")
    if plan_decisions - set(covered_decisions):
        raise AuthoringContractError(
            f"uncovered decisions: {sorted(plan_decisions - set(covered_decisions))}"
        )
    unknown_steps = (set(preserved_steps) | set(omitted_steps)) - contract_steps
    if unknown_steps:
        raise AuthoringContractError(f"unknown base step_ids: {sorted(unknown_steps)}")
    accounted_steps = set(preserved_steps) | set(omitted_steps)
    if contract_steps - accounted_steps:
        raise AuthoringContractError(
            f"unaccounted base steps: {sorted(contract_steps - accounted_steps)}"
        )
    omitted_required = set(omitted_steps) & required_steps
    if omitted_required:
        raise AuthoringContractError(
            f"required base steps cannot be omitted in a drafted result: {sorted(omitted_required)}"
        )
    if valid_claim_ids is not None:
        unknown_claim_ids = set(used_claim_ids) - valid_claim_ids
        if unknown_claim_ids:
            raise AuthoringContractError(f"unknown claim_ids: {sorted(unknown_claim_ids)}")


def reader_text(markdown: str) -> str:
    return re.sub(r"<!--.*?-->", "", markdown, flags=re.DOTALL)


def deterministic_writing_warnings(
    markdown: str, quality_profile: dict[str, Any]
) -> list[dict[str, Any]]:
    text = reader_text(markdown)
    warning_profile = quality_profile.get("deterministic_warnings", {})
    findings: list[dict[str, Any]] = []
    for term in warning_profile.get("production_language_terms", []):
        count = text.lower().count(str(term).lower())
        if count:
            findings.append({"code": "production_language", "term": term, "count": count})

    professor_mentions = sum(
        text.count(pattern) for pattern in warning_profile.get("professor_name_patterns", [])
    )
    cjk_chars = len(re.findall(r"[\u3400-\u9fff]", text))
    allowed_density = warning_profile.get("max_professor_name_mentions_per_1000_cjk_chars", 3)
    allowed_mentions = max(1, int((cjk_chars / 1000) * allowed_density + 0.999))
    if professor_mentions > allowed_mentions:
        findings.append(
            {
                "code": "professor_name_density",
                "count": professor_mentions,
                "allowed": allowed_mentions,
            }
        )

    editor_labels = sum(
        text.count(label) for label in warning_profile.get("visible_editor_labels", [])
    )
    allowed_labels = warning_profile.get("max_visible_editor_labels_per_article", 2)
    if editor_labels > allowed_labels:
        findings.append(
            {"code": "visible_editor_label_count", "count": editor_labels, "allowed": allowed_labels}
        )
    return findings


def evaluate_editorial_review(
    review: dict[str, Any], quality_profile: dict[str, Any]
) -> dict[str, Any]:
    configured = {item["id"]: item for item in quality_profile["dimensions"]}
    received = {item.get("dimension_id"): item for item in review.get("dimension_scores", [])}
    if set(received) != set(configured):
        missing = sorted(set(configured) - set(received))
        extra = sorted(set(received) - set(configured))
        raise AuthoringContractError(f"review dimensions mismatch; missing={missing}, extra={extra}")

    total = 0
    hard_gate_failures: list[str] = []
    for dimension_id, config in configured.items():
        score = received[dimension_id].get("score")
        if not isinstance(score, int) or not 0 <= score <= config["weight"]:
            raise AuthoringContractError(f"invalid score for {dimension_id}: {score}")
        total += score
        if score < config.get("minimum", 0):
            hard_gate_failures.append(dimension_id)

    declared_hard_failures = review.get("hard_failures", [])
    unknown_hard_failures = set(declared_hard_failures) - set(quality_profile["hard_failures"])
    if unknown_hard_failures:
        raise AuthoringContractError(f"unknown hard failures: {sorted(unknown_hard_failures)}")
    passed = (
        total >= quality_profile["passing_score"]
        and not hard_gate_failures
        and not declared_hard_failures
    )
    return {
        "total_score": total,
        "passed": passed,
        "hard_gate_failures": hard_gate_failures,
        "declared_hard_failures": declared_hard_failures,
    }


def validate_editorial_review(
    review: dict[str, Any],
    *,
    contract: dict[str, Any],
    manuscript: str,
    quality_profile: dict[str, Any],
) -> dict[str, Any]:
    validate_strict_schema(review, EDITORIAL_REVIEW_SCHEMA)
    if review.get("scope_confirmation") != "writing_quality_and_base_preservation":
        raise AuthoringContractError("editorial reviewer did not confirm its scope")
    expected_sections = {section["section_id"] for section in contract["sections"]}
    section_reviews = review.get("section_reviews", [])
    received_sections = [item.get("section_id") for item in section_reviews]
    if len(received_sections) != len(set(received_sections)) or set(received_sections) != expected_sections:
        raise AuthoringContractError(
            "editorial review must cover every contract section exactly once"
        )
    known_steps = {
        step["step_id"]
        for section in contract["sections"]
        for step in section["required_argument_steps"]
    }
    reviewed_steps = {
        step_id for item in section_reviews for step_id in item["base_step_ids_preserved"]
    }
    if reviewed_steps - known_steps:
        raise AuthoringContractError(
            f"editorial review used unknown base steps: {sorted(reviewed_steps - known_steps)}"
        )
    for finding in review.get("findings", []):
        anchor = _require_nonempty_string(finding.get("manuscript_anchor"), "manuscript_anchor")
        if anchor not in manuscript:
            raise AuthoringContractError(f"editorial finding anchor not found: {anchor}")
    outcome = evaluate_editorial_review(review, quality_profile)
    if outcome["passed"]:
        required_steps = {
            step["step_id"]
            for section in contract["sections"]
            for step in section["required_argument_steps"]
            if step.get("required", True)
        }
        missing_required = required_steps - reviewed_steps
        if missing_required:
            raise AuthoringContractError(
                f"passing editorial review omitted required base steps: {sorted(missing_required)}"
            )
    if not outcome["passed"] and not any(item["blocking"] for item in review.get("findings", [])):
        raise AuthoringContractError(
            "a failing rubric assessment requires at least one blocking finding"
        )
    return outcome


def validate_revision_result(
    revision: dict[str, Any],
    *,
    contract: dict[str, Any],
    plan: dict[str, Any],
    valid_claim_ids: set[str],
) -> None:
    validate_strict_schema(revision, REVISION_SCHEMA)
    author_status = "drafted" if revision["status"] == "revised" else "plan_change_required"
    validate_author_result(
        {
            "status": author_status,
            "manuscript_markdown": revision["manuscript_markdown"],
            "sections": revision["sections"],
            "plan_change_requests": revision["plan_change_requests"],
        },
        contract=contract,
        plan=plan,
        valid_claim_ids=valid_claim_ids,
    )


def build_authoring_packet(
    *,
    plan_path: str | Path,
    knowledge_path: str | Path,
    contract_path: str | Path,
    publication_profile_path: str | Path,
    quality_profile_path: str | Path,
) -> dict[str, Any]:
    paths = {
        "plan": Path(plan_path),
        "knowledge": Path(knowledge_path),
        "base_contract": Path(contract_path),
        "publication_profile": Path(publication_profile_path),
        "quality_profile": Path(quality_profile_path),
    }
    loaded: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    for name, path in paths.items():
        if not path.is_file():
            raise AuthoringContractError(f"missing {name}: {path}")
        raw = path.read_text(encoding="utf-8")
        try:
            loaded[name] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AuthoringContractError(f"invalid JSON in {name}: {path}") from exc
        sources[name] = {"path": str(path.resolve()), "sha256": sha256_text(raw)}

    contract = loaded["base_contract"]
    validate_base_contract(contract)
    plan = loaded["plan"]
    knowledge = loaded["knowledge"]
    knowledge_plan_ids = {
        item.get("plan_id")
        for item in knowledge.get("product_plans", [])
        if isinstance(item, dict)
    }
    if plan.get("plan_id") not in knowledge_plan_ids:
        raise AuthoringContractError("plan_id is not present in the knowledge snapshot")
    expected_plan = _require_mapping(contract.get("composition_plan"), "composition_plan")
    if expected_plan.get("plan_id") != plan.get("plan_id"):
        raise AuthoringContractError("base contract composition plan_id does not match input plan")
    actual_plan_sha = sources["plan"]["sha256"]
    if expected_plan.get("sha256") != actual_plan_sha:
        raise AuthoringContractError(
            f"stale composition plan: expected {expected_plan.get('sha256')}, got {actual_plan_sha}"
        )
    plan_decision_ids = {
        item.get("decision_id") for item in plan.get("decisions", []) if isinstance(item, dict)
    }
    contract_decision_ids = {
        decision_id
        for section in contract["sections"]
        for decision_id in section["decision_ids"]
    }
    if contract_decision_ids - plan_decision_ids:
        raise AuthoringContractError(
            f"contract decisions missing from plan: {sorted(contract_decision_ids - plan_decision_ids)}"
        )
    source_ids = {
        item.get("source_id")
        for item in knowledge.get("source_documents", [])
        if isinstance(item, dict)
    }
    source_records = [contract["base_source"], *contract.get("additional_base_sources", [])]
    missing_source_ids = {item.get("source_id") for item in source_records} - source_ids
    if missing_source_ids:
        raise AuthoringContractError(
            f"base source_ids are not present in the knowledge snapshot: {sorted(missing_source_ids)}"
        )
    base_manuscript_texts: dict[str, str] = {}
    for index, source in enumerate(source_records, start=1):
        base_path = Path(source["path"])
        base_text = base_path.read_text(encoding="utf-8")
        sources[f"base_manuscript_{index}"] = {
            "source_id": source["source_id"],
            "path": str(base_path.resolve()),
            "sha256": sha256_text(base_text),
        }
        base_manuscript_texts[source["source_id"]] = base_text
    scoped_claim_ids = {
        claim_id
        for decision in plan.get("decisions", [])
        if decision.get("decision_id") in contract_decision_ids
        for claim_id in decision.get("claim_ids", [])
    }
    scoped_claim_ids.update(
        claim_id
        for item in contract.get("supplemental_material", [])
        for claim_id in item.get("claim_ids", [])
    )
    scoped_claims = [
        item for item in knowledge.get("claims", []) if item.get("claim_id") in scoped_claim_ids
    ]
    evidence_step_ids = {
        evidence_step_id
        for claim in scoped_claims
        for evidence_step_id in claim.get("evidence_step_ids", [])
    }
    scoped_evidence_steps = [
        item
        for item in knowledge.get("evidence_steps", [])
        if item.get("evidence_step_id") in evidence_step_ids
    ]
    fragment_ids = {
        fragment_id
        for step in scoped_evidence_steps
        for fragment_id in step.get("source_fragment_ids", [])
    }
    scoped_fragments = [
        item
        for item in knowledge.get("source_fragments", [])
        if item.get("fragment_id") in fragment_ids
    ]
    scoped_source_ids = {item.get("source_id") for item in scoped_fragments}
    scoped_knowledge = {
        "schema_version": knowledge.get("schema_version"),
        "package_id": knowledge.get("package_id"),
        "source_documents": [
            item
            for item in knowledge.get("source_documents", [])
            if item.get("source_id") in scoped_source_ids
            or item.get("source_id") in {source.get("source_id") for source in source_records}
        ],
        "source_fragments": scoped_fragments,
        "evidence_steps": scoped_evidence_steps,
        "claims": scoped_claims,
        "claim_relations": [
            item
            for item in knowledge.get("claim_relations", [])
            if item.get("source_id") in scoped_claim_ids and item.get("target_id") in scoped_claim_ids
        ],
        "knowledge_routes": [
            item
            for item in knowledge.get("knowledge_routes", [])
            if item.get("claim_id") in scoped_claim_ids
        ],
        "topic_nodes": knowledge.get("topic_nodes", []),
        "product_plans": [
            {
                **item,
                "decisions": [
                    decision
                    for decision in item.get("decisions", [])
                    if decision.get("decision_id") in contract_decision_ids
                ],
            }
            for item in knowledge.get("product_plans", [])
            if item.get("plan_id") == plan.get("plan_id")
        ],
        "scope": {
            "decision_ids": sorted(contract_decision_ids),
            "claim_ids": sorted(scoped_claim_ids),
            "source_snapshot_sha256": sources["knowledge"]["sha256"],
        },
    }
    packet = {
        "schema_version": "matthew-exposition-authoring-packet.v1",
        "sources": sources,
        "plan": loaded["plan"],
        "knowledge": scoped_knowledge,
        "base_contract": contract,
        "publication_profile": loaded["publication_profile"],
        "quality_profile": loaded["quality_profile"],
        "base_manuscript_text": base_manuscript_texts[contract["base_source"]["source_id"]],
        "base_manuscript_texts": base_manuscript_texts,
        "publication_gate": {
            "eligible": False,
            "reason": "human publication approval and external program audit are not part of authoring",
        },
    }
    packet["packet_sha256"] = sha256_text(canonical_json(packet))
    return packet
