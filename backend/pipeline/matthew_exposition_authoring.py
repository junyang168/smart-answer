from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Sequence

from backend.pipeline.base_contract_coverage import (
    BOOK_CODE_TO_CHINESE,
    FLAG_CROSS_REFERENCE,
    FLAG_ORIGINAL_LANGUAGE,
    ScriptureRef,
    annotate_scripture_refs,
    load_bearing_flags,
    mark_passage_relevance,
    parse_passage_range,
    parse_scripture_refs,
    split_segments,
    split_sentences,
)


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
EDITORIAL_REVIEW_PACKET_MAX_BYTES = 40 * 1024
FINAL_REVIEW_TIMEOUT_MIN_SECONDS = 180.0
FINAL_REVIEW_TIMEOUT_MAX_SECONDS = 300.0
FINAL_REVIEW_MAX_ATTEMPTS = 2

# A revision aimed at one dimension can predictably disturb a small number of
# adjacent dimensions.  Keeping this map explicit makes delta scoring
# conservative without allowing a reviewer to rescore the whole manuscript.
DELTA_DIMENSION_IMPACTS: dict[str, set[str]] = {
    "source_and_exegesis": {"exegetical_reasoning"},
    "base_manuscript_preservation": {"exegetical_reasoning", "concision_without_compression"},
    "exegetical_reasoning": {"general_reader_readability", "concision_without_compression"},
    "argument_organization": {"general_reader_readability", "concision_without_compression"},
    "general_reader_readability": {"approved_written_style"},
    "editorial_voice_restraint": {"approved_written_style", "general_reader_readability"},
    "approved_written_style": {"general_reader_readability"},
    "theological_tension_and_attribution": {"source_and_exegesis", "exegetical_reasoning"},
    "concision_without_compression": {"general_reader_readability"},
    "pastoral_theological_landing": {"approved_written_style"},
}

# A Revision Agent rewrites the complete manuscript, so a paragraph inside a
# section can change without any accepted finding pointing at that section.
# These dimensions are judged directly on the prose of whichever sections were
# actually rewritten, so they can never be inherited across such a change.
SECTION_PROSE_DIMENSIONS: set[str] = {
    "general_reader_readability",
    "approved_written_style",
    "concision_without_compression",
}

#: Dimensions judged against the sources rather than the prose. Each has a
#: hard gate, and none can be scored from the manuscript alone.
SOURCE_JUDGED_DIMENSIONS = frozenset({
    "source_and_exegesis",
    "base_manuscript_preservation",
    "theological_tension_and_attribution",
})

HARD_FAILURE_DIMENSIONS = {
    "load_bearing_base_argument_removed_or_reordered": "base_manuscript_preservation",
    "editorial_or_ai_inference_attributed_to_professor": "theological_tension_and_attribution",
    "material_source_tension_silently_harmonized": "theological_tension_and_attribution",
    "production_language_dominates_reader_prose": "editorial_voice_restraint",
    "exegetical_observation_inference_conclusion_chain_missing": "exegetical_reasoning",
}


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
                        "preserved_step_anchors": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "step_id": {"type": "string"},
                                    "anchor": {"type": "string"},
                                },
                                "required": ["step_id", "anchor"],
                            },
                        },
                        "claim_ids_used": {"type": "array", "items": {"type": "string"}},
                        "integration_operations": {"type": "array", "items": {"type": "string"}},
                        "applied_operations": {"type": "array", "items": {"type": "string"}},
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
                        "preserved_step_anchors",
                        "claim_ids_used",
                        "integration_operations",
                        "applied_operations",
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


FINAL_DELTA_REVIEW_SCHEMA: dict[str, Any] = {
    "name": "matthew_exposition_final_delta_review_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {"type": "string", "enum": ["final_delta_writing_quality"]},
            "reviewed_manuscript_sha256": {"type": "string"},
            "summary": {"type": "string"},
            "dimension_scores": EDITORIAL_REVIEW_SCHEMA["schema"]["properties"]["dimension_scores"],
            "hard_failure_assessments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "failure_id": {"type": "string", "enum": HARD_FAILURE_IDS},
                        "failed": {"type": "boolean"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["failure_id", "failed", "evidence"],
                },
            },
            "findings": EDITORIAL_REVIEW_SCHEMA["schema"]["properties"]["findings"],
        },
        "required": [
            "scope_confirmation",
            "reviewed_manuscript_sha256",
            "summary",
            "dimension_scores",
            "hard_failure_assessments",
            "findings",
        ],
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


def _governing_contract_sections(
    section: dict[str, Any], contract: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return the contract sections whose operation policy binds a ledger item.

    An author may merge several composition decisions into one reader section,
    so a ledger item is bound by every contract section it draws decisions
    from; an exact section_id match takes precedence when it exists.
    """

    section_id = section.get("section_id")
    matched = [
        item for item in contract["sections"] if item["section_id"] == section_id
    ]
    if matched:
        return matched
    decision_ids = set(section.get("decision_ids", []))
    return [
        item
        for item in contract["sections"]
        if decision_ids & set(item["decision_ids"])
    ]


def _validate_section_operations(
    section: dict[str, Any], *, contract: dict[str, Any], field: str
) -> None:
    """Enforce the contract's allowed/ineligible operations for one ledger item."""

    section_id = section.get("section_id") or field
    governing = _governing_contract_sections(section, contract)
    allowed: set[str] = set()
    ineligible: set[str] = set()
    for governing_section in governing:
        allowed.update(governing_section.get("allowed_operations", []))
        ineligible.update(governing_section.get("ineligible_operations", []))

    applied = section.get("applied_operations")
    if not isinstance(applied, list) or not applied:
        raise AuthoringContractError(
            f"section {section_id} must declare at least one applied operation"
        )
    for index, operation in enumerate(applied):
        _require_nonempty_string(operation, f"{field}.applied_operations[{index}]")
    if duplicates := _duplicates(applied):
        raise AuthoringContractError(
            f"section {section_id} declared an operation twice: {sorted(duplicates)}"
        )

    integration_operations = section.get("integration_operations", [])
    if not isinstance(integration_operations, list):
        raise AuthoringContractError("integration_operations must be an array")
    unsupported = set(integration_operations) - SUPPLEMENT_OPERATIONS
    if unsupported:
        raise AuthoringContractError(
            f"section {section_id} used unsupported supplemental operations: {sorted(unsupported)}"
        )

    executed = set(applied) | set(integration_operations)
    blocked = executed & ineligible
    if blocked:
        raise AuthoringContractError(
            f"section {section_id} used ineligible operations: {sorted(blocked)}"
        )
    if allowed:
        outside = set(applied) - allowed
        if outside:
            raise AuthoringContractError(
                f"section {section_id} used operations outside allowed_operations: {sorted(outside)}"
            )


def _validate_preserved_step_anchors(
    section: dict[str, Any], *, manuscript: str, field: str
) -> None:
    """Verify that every preserved required step points at manuscript prose.

    Claiming a step in `base_step_ids_preserved` is a self-report; the literal
    anchor makes the claim locatable, so a reviewer can judge whether the step
    was reasoned out or merely summarized.
    """

    section_id = section.get("section_id") or field
    preserved = section.get("base_step_ids_preserved", [])
    if not isinstance(preserved, list):
        raise AuthoringContractError("base_step_ids_preserved must be an array")
    entries = section.get("preserved_step_anchors")
    if not isinstance(entries, list):
        raise AuthoringContractError("preserved_step_anchors must be an array")
    anchors: dict[str, str] = {}
    for index, entry_value in enumerate(entries):
        entry = _require_mapping(entry_value, f"{field}.preserved_step_anchors[{index}]")
        step_id = _require_nonempty_string(
            entry.get("step_id"), f"{field}.preserved_step_anchors[{index}].step_id"
        )
        anchor = _require_nonempty_string(
            entry.get("anchor"), f"{field}.preserved_step_anchors[{index}].anchor"
        )
        if step_id in anchors:
            raise AuthoringContractError(
                f"section {section_id} anchored base step {step_id} more than once"
            )
        if anchor not in manuscript:
            raise AuthoringContractError(
                f"preserved step anchor not found in manuscript: {step_id}"
            )
        anchors[step_id] = anchor
    missing = set(preserved) - set(anchors)
    if missing:
        raise AuthoringContractError(
            f"preserved base steps without a manuscript anchor: {sorted(missing)}"
        )
    unclaimed = set(anchors) - set(preserved)
    if unclaimed:
        raise AuthoringContractError(
            f"anchored base steps are not listed as preserved: {sorted(unclaimed)}"
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
        _validate_section_operations(
            section, contract=contract, field=f"sections[{section_index}]"
        )
        _validate_preserved_step_anchors(
            section, manuscript=manuscript, field=f"sections[{section_index}]"
        )
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


def rebind_review_after_hidden_metadata_normalization(
    *,
    review: dict[str, Any],
    outcome: dict[str, Any],
    before_manuscript: str,
    after_manuscript: str,
    contract: dict[str, Any],
    quality_profile: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebind a verified review when only hidden HTML comments changed."""

    verified = validate_editorial_review(
        review,
        contract=contract,
        manuscript=before_manuscript,
        quality_profile=quality_profile,
    )
    comparable_outcome = {
        key: value for key, value in outcome.items() if key != "manuscript_sha256"
    }
    if comparable_outcome != verified:
        raise AuthoringContractError("normalization baseline outcome is not verified")
    before_sha = sha256_text(before_manuscript)
    if outcome.get("manuscript_sha256") != before_sha:
        raise AuthoringContractError("normalization baseline SHA does not match manuscript")
    # A serializer may add or remove the final newline without changing any
    # reader-visible prose. Internal whitespace remains byte-for-byte strict.
    before_reader_text = reader_text(before_manuscript).rstrip("\n")
    after_reader_text = reader_text(after_manuscript).rstrip("\n")
    if before_reader_text != after_reader_text:
        raise AuthoringContractError(
            "hidden metadata normalization changed reader-visible manuscript text"
        )
    rebound_outcome = {
        **outcome,
        "manuscript_sha256": sha256_text(after_manuscript),
    }
    return rebound_outcome, {
        "schema_version": "matthew-exposition-hidden-metadata-normalization.v1",
        "before_manuscript_sha256": before_sha,
        "after_manuscript_sha256": rebound_outcome["manuscript_sha256"],
        "reader_text_sha256": sha256_text(before_reader_text),
        "reader_visible_text_unchanged": True,
    }


#: A provenance comment and the prose it governs; `manuscript_grounding_check`
#: imports from this module, so its own copy of this pattern cannot be reused
#: here without an import cycle.
_PROVENANCE_COMMENT_RE = re.compile(r"<!--\s*provenance:\s*(\{.*?\})\s*-->", re.S)
#: An elision written as a single, doubled, or longer run of dots: the house
#: convention is 「⋯⋯」 but a draft that writes one 「…」 means the same thing.
_QUOTE_ELISION_RE = re.compile(r"[⋯…]+|\.{3,}")

#: A quoted span shorter than this is a term being named (「體貼」那個字), not a
#: sentence being quoted, and naming a word is not a claim about what the
#: professor said. Set from the shortest real quote worth checking rather than
#: from a corpus measurement; it is a warning threshold, not a gate.
QUOTE_FIDELITY_MIN_CHARS = 8

#: Quoted prose is checked for invented words, not for copied punctuation: a
#: spoken transcript is punctuated by whoever transcribed it, so requiring a
#: quote to reproduce 、 where the body reads ，would fail faithful quotes.
_QUOTE_MATCH_STRIP_RE = re.compile(
    r"[\s，。、；：！？「」『』（）《》〈〉—–\-…⋯,.;:!?\'\"()\[\]]+"
)


def _outermost_quoted_spans(text: str) -> list[str]:
    """Return the content of each outermost 「…」 span, nesting included.

    Quotes of the professor nest by convention -- he names a word inside a
    sentence he is quoting (「我們中文翻成「體貼」那個字」) -- and a pattern that
    stops at the first closing bracket matches only the inner 「體貼」, which is
    below the term-mention threshold. The whole outer sentence would then be
    skipped, losing exactly the quote rule 8e is about. Nested brackets are
    stripped from the comparison key, so the span is matched against the source
    as one run of prose.
    """

    spans: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "「":
            if depth == 0:
                start = index + 1
            depth += 1
        elif char == "」" and depth:
            depth -= 1
            if depth == 0:
                spans.append(text[start:index])
    return spans


def _quote_match_key(text: str) -> str:
    return _QUOTE_MATCH_STRIP_RE.sub("", unicodedata.normalize("NFKC", text))


def quote_fidelity_warnings(
    markdown: str,
    source_texts: Iterable[str],
    *,
    checked_attributions: frozenset[str] = frozenset({"professor", "editorial_synthesis"}),
    min_chars: int = QUOTE_FIDELITY_MIN_CHARS,
) -> list[dict[str, Any]]:
    """Report quoted spans that no source text contains verbatim.

    Rule 8e tells the author to prefer the professor's own wording over a
    paraphrase of it, which introduces a failure the pipeline did not have
    before: prose of the author's own composition placed inside quotation
    marks and attributed to him. That is worse than the abstract paraphrase
    8e exists to remove -- an invented quote is a fabricated source, while an
    abstract paraphrase is merely flat -- so the instruction ships with a
    check behind it.

    Only spans in paragraphs claiming `checked_attributions` are examined, and
    only those at least `min_chars` long; a shorter span names a term rather
    than quoting a sentence. `⋯⋯` marks an elision the author is allowed to
    make, so each side of it is matched separately, in order, against the same
    source text -- a quote may skip material but may not reorder it or join
    two speakers.

    Known false positive: a scripture sentence quoted inside a professor
    paragraph is verbatim from the Bible, which is not among the source texts.
    That is why this returns warnings for the adjudicator to read rather than
    a gate result, and why it does not raise.
    """

    keys = [_quote_match_key(item) for item in source_texts if item]
    warnings: list[dict[str, Any]] = []
    seen: set[str] = set()
    matches = list(_PROVENANCE_COMMENT_RE.finditer(markdown))
    for index, match in enumerate(matches):
        try:
            provenance = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(provenance, dict):
            continue
        if provenance.get("attribution") not in checked_attributions:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        for quoted in _outermost_quoted_spans(markdown[match.end() : end]):
            pieces = [
                piece for part in _QUOTE_ELISION_RE.split(quoted)
                if (piece := _quote_match_key(part))
            ]
            if sum(len(piece) for piece in pieces) < min_chars:
                continue
            if any(_pieces_in_order(pieces, key) for key in keys):
                continue
            if quoted in seen:
                continue
            seen.add(quoted)
            warnings.append({"code": "quote_not_verbatim", "quoted_text": quoted})
    return warnings


def _pieces_in_order(pieces: list[str], key: str) -> bool:
    cursor = 0
    for piece in pieces:
        found = key.find(piece, cursor)
        if found < 0:
            return False
        cursor = found + len(piece)
    return True


def deterministic_writing_warnings(
    markdown: str,
    quality_profile: dict[str, Any],
    *,
    source_texts: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Run the checks that need no model over a draft.

    `source_texts` are the professor's own words -- the sermon transcript
    segments and base manuscripts the packet carries -- against which quoted
    spans are matched. Callers without them (a test, a CLI reading only a
    manuscript) get every other check and skip quote fidelity, rather than
    having every quote reported as unmatched against an empty corpus.
    """

    text = reader_text(markdown)
    warning_profile = quality_profile.get("deterministic_warnings", {})
    findings: list[dict[str, Any]] = []
    if source_texts is not None:
        findings.extend(quote_fidelity_warnings(markdown, source_texts))
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


def hard_failures_after_adjudication(
    review: dict[str, Any], withdrawn_finding_ids: set[str]
) -> tuple[list[str], dict[str, str]]:
    """Drop hard failures whose every supporting finding was rejected.

    A reviewer declares a hard failure and files the finding that evidences
    it. When adjudication rejects that finding -- concluding, say, that the
    inference chain is present and only its order could be improved -- the
    declaration it rested on has been overturned too. Leaving it standing
    deadlocks the run: nothing is left to revise, yet a one-vote veto still
    blocks publication, so every draft ends at human review no matter how
    good it is.

    A hard failure with no finding behind it is left alone. Nothing was
    adjudicated, so there is nothing to overturn, and a safety declaration
    should not evaporate for lack of paperwork.
    """

    findings_by_dimension: dict[str, list[dict[str, Any]]] = {}
    for finding in review.get("findings", []):
        dimension = finding.get("dimension_id")
        if dimension:
            findings_by_dimension.setdefault(dimension, []).append(finding)

    kept: list[str] = []
    withdrawn: dict[str, str] = {}
    for failure_id in review.get("hard_failures", []):
        dimension = HARD_FAILURE_DIMENSIONS.get(failure_id)
        supporting = findings_by_dimension.get(dimension or "", [])
        if supporting and all(
            item["finding_id"] in withdrawn_finding_ids for item in supporting
        ):
            withdrawn[failure_id] = (
                f"every finding for {dimension} was rejected in adjudication: "
                + ", ".join(sorted(item["finding_id"] for item in supporting))
            )
        else:
            kept.append(failure_id)
    return kept, withdrawn


def out_of_scope_dimensions(contract: dict[str, Any]) -> dict[str, str]:
    """Return dimensions the contract puts out of this article's reach.

    The publication profile lists 生活應用 as optional, and the platform's rule
    is that a passage without supporting material must not have one invented.
    But the rubric scores `pastoral_theological_landing` unconditionally, so an
    article that correctly omits an unsupported application is marked down for
    obeying the profile -- pressure to invent exactly the kind of unsourced
    closing paragraph this pipeline exists to prevent.

    Scope is read from the contract, never from the author's or the reviewer's
    opinion: an author cannot earn the points by claiming its material was
    thin. A section that forbids inventing an application chain, and has no
    registered chain to draw on, cannot land pastorally within its own bounds.
    """

    out_of_scope: dict[str, str] = {}
    for section in contract.get("sections") or []:
        ineligible = set(section.get("ineligible_operations") or [])
        if "invent_life_application_chain" in ineligible:
            out_of_scope["pastoral_theological_landing"] = (
                f"contract section {section.get('section_id')} forbids "
                "invent_life_application_chain and registers no application chain"
            )
    return out_of_scope


def evaluate_editorial_review(
    review: dict[str, Any],
    quality_profile: dict[str, Any],
    not_applicable: dict[str, str] | None = None,
) -> dict[str, Any]:
    configured = {item["id"]: item for item in quality_profile["dimensions"]}
    received = {item.get("dimension_id"): item for item in review.get("dimension_scores", [])}
    if set(received) != set(configured):
        missing = sorted(set(configured) - set(received))
        extra = sorted(set(received) - set(configured))
        raise AuthoringContractError(f"review dimensions mismatch; missing={missing}, extra={extra}")

    not_applicable = not_applicable or {}
    unknown_na = set(not_applicable) - set(configured)
    if unknown_na:
        raise AuthoringContractError(
            f"unknown not-applicable dimensions: {sorted(unknown_na)}"
        )

    total = 0
    applicable_weight = 0
    hard_gate_failures: list[str] = []
    for dimension_id, config in configured.items():
        score = received[dimension_id].get("score")
        if not isinstance(score, int) or not 0 <= score <= config["weight"]:
            raise AuthoringContractError(f"invalid score for {dimension_id}: {score}")
        if dimension_id in not_applicable:
            # Excluded from both numerator and denominator. Awarding the full
            # weight instead would score an article that wrote no application
            # the same as one that wrote an excellent one; the dimension was
            # not measured, so it should not contribute either way.
            continue
        applicable_weight += config["weight"]
        total += score
        if score < config.get("minimum", 0):
            hard_gate_failures.append(dimension_id)

    declared_hard_failures = review.get("hard_failures", [])
    unknown_hard_failures = set(declared_hard_failures) - set(quality_profile["hard_failures"])
    if unknown_hard_failures:
        raise AuthoringContractError(f"unknown hard failures: {sorted(unknown_hard_failures)}")
    # Every dimension must reach its own minimum; there is no total to pass.
    # A single number let a weak dimension be carried by the others -- the
    # published matthew-16-21-23 round one totalled 81 of 100 while scoring 7,
    # 3 and 3 against minimums of 8, 4 and 4. The dimensions are ten separate
    # requirements, not components of one score to trade off, so `total_score`
    # is still reported for a reader and no longer decides anything.
    passed = not hard_gate_failures and not declared_hard_failures
    return {
        "total_score": total,
        "passed": passed,
        "hard_gate_failures": hard_gate_failures,
        "declared_hard_failures": declared_hard_failures,
        "not_applicable_dimensions": dict(not_applicable),
        "applicable_weight": applicable_weight,
    }


def validate_editorial_review(
    review: dict[str, Any],
    *,
    contract: dict[str, Any],
    manuscript: str,
    quality_profile: dict[str, Any],
    require_blocking_finding_when_failing: bool = True,
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
    outcome = evaluate_editorial_review(
        review, quality_profile, out_of_scope_dimensions(contract)
    )
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
    # A reviewer that fails a draft must say what to change -- otherwise the
    # run has nothing to act on. This does not hold for a merged review
    # inherited into a later round: "below the threshold, but nothing that
    # must be fixed" is a real and reportable state, and the runner's own
    # no-actionable-findings path already handles it by stopping for a human.
    if (
        require_blocking_finding_when_failing
        and not outcome["passed"]
        and not any(item["blocking"] for item in review.get("findings", []))
    ):
        raise AuthoringContractError(
            "a failing rubric assessment requires at least one blocking finding"
        )
    return outcome


def evidence_step_fragment_ids(step: dict[str, Any]) -> list[str]:
    """Return an evidence step's source fragments from either spelling.

    A step carries `source_fragment_ids` or the singular `source_fragment_id`
    depending on which producer wrote it: `shared_knowledge_pilot` normalizes
    both onto every record, while knowledge compiled from the authoring store
    keeps whichever one its producer used. The two spellings were read in
    exactly the places the other was populated -- the packet builder collected
    only the plural, the grounding gate resolved only the singular -- so on a
    store-compiled plan the sets did not intersect and every paragraph was
    checked with no source excerpt at all.
    """

    ids = step.get("source_fragment_ids") or []
    if not ids and step.get("source_fragment_id"):
        ids = [step["source_fragment_id"]]
    return [str(item) for item in ids]


def _with_packet_size(packet: dict[str, Any], *, max_bytes: int) -> dict[str, Any]:
    packet["size_budget"] = {"max_bytes": max_bytes, "actual_bytes": 0}
    # The number of digits in actual_bytes can change the serialized size.  A
    # short fixed-point loop records the exact canonical payload size.
    for _ in range(4):
        actual_bytes = len(canonical_json(packet).encode("utf-8"))
        if packet["size_budget"]["actual_bytes"] == actual_bytes:
            break
        packet["size_budget"]["actual_bytes"] = actual_bytes
    actual_bytes = len(canonical_json(packet).encode("utf-8"))
    if actual_bytes > max_bytes:
        raise AuthoringContractError(
            f"editorial review packet exceeds {max_bytes} byte budget: {actual_bytes}"
        )
    return packet


#: A sentence earns its place in the reviewer's slice by carrying an original
#: language observation or a cross reference -- the two moves whose fidelity to
#: the source the reviewer is scoring. `FLAG_INFERENCE_BRIDGE` is deliberately
#: not here: it fires on ordinary connectives (因此, 所以, 可見), so it selects
#: most of the prose and says nothing about whether the source supports it.
EXEGETICAL_SLICE_FLAGS = frozenset({FLAG_ORIGINAL_LANGUAGE, FLAG_CROSS_REFERENCE})


def _passage_target(passage: str) -> ScriptureRef:
    """Return the contract's passage as a reference the base manuscript uses.

    `passage` is an OSIS-style code (`Matt.16.21-Matt.16.23`) while the
    manuscripts cite in Chinese (太 16:21), so the book has to be translated
    before the two can be compared.
    """

    raw = parse_passage_range(passage)
    return ScriptureRef(
        BOOK_CODE_TO_CHINESE.get(raw.book, raw.book),
        raw.chapter,
        raw.start_verse,
        raw.end_verse,
    )


def _exegetical_source_slice(
    *,
    base_manuscript_texts: dict[str, str],
    scoped_fragments: list[dict[str, Any]],
    passage: str,
    step_excerpts: Sequence[str],
) -> dict[str, list[dict[str, Any]]]:
    """Return the source sentences the three source-judged dimensions need.

    `source_and_exegesis` asks whether the article's exegesis is faithful to
    the sources, which cannot be answered from the manuscript alone -- yet the
    reviewer has been scoring it, hard gate and all, with no source in front of
    it. Sending the whole scoped material instead would not fit beside the
    draft in the packet budget, and would bury seven writing-quality
    dimensions under material irrelevant to them.

    So the slice is narrowed twice. First to the passage: the base manuscript
    keeps only the paragraphs `base_contract_coverage` already counts as
    explaining this article's verses. Then to the exegesis: within those
    paragraphs, and among the fragments the author was scoped to, only the
    sentences carrying an original-language observation or a cross reference.
    On matt16-21-23 that is three sentences of base manuscript, and the
    professor's reading of φρονέω is among them.
    """

    target = _passage_target(passage)
    base_sentences: list[dict[str, Any]] = []
    for source_id, text in base_manuscript_texts.items():
        segments = split_segments(text)
        annotate_scripture_refs(segments)
        relevance = mark_passage_relevance(segments, target, step_excerpts=step_excerpts)
        for index in sorted(relevance):
            segment = segments[index]
            for sentence in split_sentences(segment.text):
                flags = [
                    flag
                    for flag in load_bearing_flags(
                        sentence, parse_scripture_refs(sentence), target
                    )
                    if flag in EXEGETICAL_SLICE_FLAGS
                ]
                if flags:
                    # Which flag selected the sentence, and which section it
                    # sits in, are facts about how this slice was built. The
                    # reviewer is judging the sentence, so neither is sent.
                    base_sentences.append(
                        {"source_id": source_id, "sentence": sentence}
                    )

    cited_excerpts: list[dict[str, Any]] = []
    for fragment in scoped_fragments:
        excerpt = fragment.get("verbatim_excerpt") or ""
        flags = [
            flag
            for flag in load_bearing_flags(excerpt, parse_scripture_refs(excerpt), target)
            if flag in EXEGETICAL_SLICE_FLAGS
        ]
        if flags:
            cited_excerpts.append(
                {
                    "fragment_id": fragment.get("fragment_id"),
                    "source_id": fragment.get("source_id"),
                    "verbatim_excerpt": excerpt,
                }
            )
    return {
        "base_manuscript_exegesis": base_sentences,
        "cited_source_excerpts": cited_excerpts,
    }


def build_editorial_review_packet(
    *,
    authoring_packet: dict[str, Any],
    author_result: dict[str, Any],
) -> dict[str, Any]:
    """Build the bounded writing-review projection of an authoring packet.

    Three of the ten dimensions are judgements about the sources rather than
    the prose -- `base_manuscript_preservation`, `source_and_exegesis` and
    `theological_tension_and_attribution`, each with a hard gate -- so the
    packet carries the sentences those three need: the base manuscript
    sentences the contract preserved, the passage's exegetical sentences, and
    the contract's declared source tensions. It is a minimum, not a copy of
    what the author had: knowledge records, topic nodes, the composition plan,
    whole sermon segments and the base manuscript outside this passage stay
    local, and the seven writing-quality dimensions get nothing they cannot
    read off the draft.
    """

    manuscript = _require_nonempty_string(
        author_result.get("manuscript_markdown"), "manuscript_markdown"
    )
    contract = _require_mapping(authoring_packet.get("base_contract"), "base_contract")
    quality_profile = _require_mapping(
        authoring_packet.get("quality_profile"), "quality_profile"
    )
    compact_sections = []
    step_excerpts: list[str] = []
    for section in contract.get("sections", []):
        steps = section.get("required_argument_steps", [])
        compact_sections.append(
            {
                "section_id": section["section_id"],
                "required_argument_steps": [
                    {
                        "step_id": step["step_id"],
                        "statement": step["statement"],
                        "required": step.get("required", True),
                        # The base manuscript's own sentence. `statement` is
                        # the contract's rewording of it, so without this the
                        # reviewer can check that a step was mentioned but not
                        # that what the base manuscript argued survived.
                        "source_excerpt": step.get("source_excerpt", ""),
                    }
                    for step in steps
                ],
            }
        )
        step_excerpts.extend(
            excerpt for step in steps if (excerpt := step.get("source_excerpt"))
        )

    knowledge = _require_mapping(authoring_packet.get("knowledge"), "knowledge")
    source_slice = _exegetical_source_slice(
        base_manuscript_texts=authoring_packet.get("base_manuscript_texts") or {},
        scoped_fragments=knowledge.get("source_fragments", []),
        passage=_require_nonempty_string(contract.get("passage"), "passage"),
        step_excerpts=step_excerpts,
    )
    # A tension the contract registered is the material the reviewer checks
    # `theological_tension_and_attribution` against: an article that quietly
    # harmonised one has removed something the contract said to keep.
    source_slice["source_tensions"] = [
        item
        for item in contract.get("supplemental_material", [])
        if item.get("operation") == "tension"
    ]
    packet = {
        "schema_version": "matthew-exposition-editorial-review-packet.v1",
        "manuscript_sha256": sha256_text(manuscript),
        "manuscript_markdown": manuscript,
        "base_preservation_contract": {"sections": compact_sections},
        "author_section_ledger": [
            {
                "section_id": section["section_id"],
                "base_step_ids_preserved": section.get("base_step_ids_preserved", []),
                # Verified step→prose pairs let the reviewer calibrate depth at
                # the exact place each load-bearing step is supposed to live.
                "preserved_step_anchors": section.get("preserved_step_anchors", []),
                "output_anchor": section.get("output_anchor", ""),
            }
            for section in author_result.get("sections", [])
        ],
        "quality_profile": {
            "profile_id": quality_profile.get("profile_id"),
            "revision": quality_profile.get("revision"),
            # Each dimension carries its own `minimum`; there is no total.
            "dimensions": quality_profile.get("dimensions", []),
            "hard_failures": quality_profile.get("hard_failures", []),
            "review_calibration": quality_profile.get("review_calibration", {}),
        },
        "source_slice": source_slice,
        # Material the contract put in scope that the manuscript never cited.
        # The reviewer already reads the manuscript, so it knows what was used;
        # what it could not see was what was available and left on the table --
        # and `pastoral_theological_landing` is precisely a judgment about
        # whether the article landed on material it had.
        #
        # It cost a run: the reviewer scored the landing 3 of 5, could name no
        # material for one, and invented a discipleship application instead.
        # Adjudication rejected it for citing no evidence -- correctly -- and
        # the reviewer withdrew, both concluding the passage simply had no
        # application to make. Four claims of `claim_type: "application"` were
        # sitting in the author's packet unused, and neither agent could see
        # them. Only the uncited ones are sent: the full set is 13KB against a
        # 40KB budget, and the used ones are already in the prose.
        "unused_scoped_claims": [
            {
                "claim_id": item.get("claim_id"),
                "claim_type": item.get("claim_type"),
                # Two shapes exist: the store spells this `statement`, an
                # older knowledge projection spells it `title`. Reading one
                # sends the reviewer a claim with no text, which is the whole
                # failure this field was added to end.
                "statement": item.get("statement") or item.get("title"),
            }
            for item in knowledge.get("claims", [])
            if item.get("claim_id") and str(item["claim_id"]) not in manuscript
        ],
        "scope": {
            "include": [
                "writing_quality",
                "base_manuscript_preservation",
                "source_and_exegesis",
                "theological_tension_and_attribution",
            ],
            "exclude": [
                "program_audit",
                "claim_extraction",
                "knowledge_records",
                "topic_nodes",
                "evidence_steps",
                "composition_plan",
                "sermon_transcript_segments",
                "base_manuscript_outside_passage",
            ],
        },
    }
    return _with_packet_size(packet, max_bytes=EDITORIAL_REVIEW_PACKET_MAX_BYTES)


def select_delta_dimensions(
    accepted_findings: list[dict[str, Any]],
    *,
    changed_section_dimensions: Iterable[str] = (),
) -> list[str]:
    direct = {
        finding.get("dimension_id")
        for finding in accepted_findings
        if finding.get("dimension_id") in QUALITY_DIMENSION_IDS
    }
    direct.update(
        dimension_id
        for dimension_id in changed_section_dimensions
        if dimension_id in QUALITY_DIMENSION_IDS
    )
    affected = set(direct)
    for dimension_id in direct:
        affected.update(DELTA_DIMENSION_IMPACTS.get(dimension_id, set()))
    return [dimension_id for dimension_id in QUALITY_DIMENSION_IDS if dimension_id in affected]


def _markdown_blocks(markdown: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", markdown) if block.strip()]


def changed_markdown_paragraphs(before: str, after: str) -> list[dict[str, Any]]:
    before_blocks = _markdown_blocks(before)
    after_blocks = _markdown_blocks(after)
    matcher = SequenceMatcher(a=before_blocks, b=after_blocks, autojunk=False)
    changes: list[dict[str, Any]] = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        changes.append(
            {
                "change": tag,
                "before_paragraphs": before_blocks[old_start:old_end],
                "after_paragraphs": after_blocks[new_start:new_end],
            }
        )
    return changes


def _section_anchor_offsets(
    manuscript: str, sections: list[dict[str, Any]]
) -> list[tuple[int, str]]:
    """Locate each ledger section inside a manuscript by its literal anchor."""

    offsets: list[tuple[int, str]] = []
    for section in sections:
        section_id = section.get("section_id")
        anchor = section.get("output_anchor") or ""
        index = manuscript.find(anchor) if anchor else -1
        if not section_id or index < 0:
            return []
        offsets.append((index, section_id))
    offsets.sort()
    return offsets


def changed_section_ids(
    *,
    sections: list[dict[str, Any]],
    baseline_manuscript: str,
    revised_manuscript: str,
    changes: list[dict[str, Any]],
) -> list[str]:
    """Return the ledger sections whose paragraphs the revision actually touched.

    Attribution is deliberately conservative: a paragraph that cannot be placed
    inside a located section (an unanchored ledger, a rewritten heading, a
    paragraph before the first anchor) marks every section as changed, so the
    delta review widens rather than silently inherits.
    """

    all_ids = sorted({section["section_id"] for section in sections if section.get("section_id")})
    before_offsets = _section_anchor_offsets(baseline_manuscript, sections)
    after_offsets = _section_anchor_offsets(revised_manuscript, sections)
    if not before_offsets or not after_offsets:
        return all_ids

    def section_at(index: int, offsets: list[tuple[int, str]]) -> str | None:
        found: str | None = None
        for start, section_id in offsets:
            if start <= index:
                found = section_id
            else:
                break
        return found

    changed: set[str] = set()
    cursors = {"before": 0, "after": 0}
    for change in changes:
        for key, manuscript, offsets in (
            ("before", baseline_manuscript, before_offsets),
            ("after", revised_manuscript, after_offsets),
        ):
            for paragraph in change.get(f"{key}_paragraphs", []):
                index = manuscript.find(paragraph, cursors[key])
                if index < 0:
                    return all_ids
                cursors[key] = index + len(paragraph)
                section_id = section_at(index, offsets)
                if section_id is None:
                    return all_ids
                changed.add(section_id)
    return sorted(changed)


def changed_section_dimensions(
    *, section_ids: Iterable[str], baseline_review: dict[str, Any]
) -> list[str]:
    """Dimensions that the changed sections carry and therefore cannot inherit.

    A section carries the prose-level dimensions plus every dimension the
    verified baseline review anchored in that section.
    """

    attribution: dict[str, set[str]] = {}
    for finding in baseline_review.get("findings", []):
        section_id = finding.get("section_id")
        dimension_id = finding.get("dimension_id")
        if section_id and dimension_id in QUALITY_DIMENSION_IDS:
            attribution.setdefault(section_id, set()).add(dimension_id)
    dimensions: set[str] = set()
    for section_id in section_ids:
        dimensions.update(SECTION_PROSE_DIMENSIONS)
        dimensions.update(attribution.get(section_id, set()))
    return [
        dimension_id
        for dimension_id in QUALITY_DIMENSION_IDS
        if dimension_id in dimensions
    ]


def build_final_delta_review_packet(
    *,
    baseline_review: dict[str, Any],
    baseline_outcome: dict[str, Any],
    baseline_manuscript: str,
    revised_manuscript: str,
    accepted_findings: list[dict[str, Any]],
    dispositions: list[dict[str, Any]],
    quality_profile: dict[str, Any],
    contract: dict[str, Any],
    baseline_sections: list[dict[str, Any]],
    source_slice: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a SHA-bound final review packet containing revision deltas only."""

    # A baseline is inheritable only after the full local validator has attached
    # its deterministic outcome and exact manuscript binding.
    baseline_sha = sha256_text(baseline_manuscript)
    recomputed_baseline_outcome = validate_editorial_review(
        baseline_review,
        contract=contract,
        manuscript=baseline_manuscript,
        quality_profile=quality_profile,
    )
    comparable_outcome = {
        key: value for key, value in baseline_outcome.items() if key != "manuscript_sha256"
    }
    if comparable_outcome != recomputed_baseline_outcome:
        raise AuthoringContractError("baseline review outcome is not the locally verified result")
    if baseline_outcome.get("manuscript_sha256") != baseline_sha:
        raise AuthoringContractError("baseline review is not verified against the baseline manuscript SHA")
    changes = changed_markdown_paragraphs(baseline_manuscript, revised_manuscript)
    if not changes:
        raise AuthoringContractError("revision did not change any manuscript paragraphs")
    # The Revision Agent rewrites the whole manuscript, so scope follows the
    # paragraphs that actually moved as well as the accepted findings.
    revised_section_ids = changed_section_ids(
        sections=baseline_sections,
        baseline_manuscript=baseline_manuscript,
        revised_manuscript=revised_manuscript,
        changes=changes,
    )
    affected = select_delta_dimensions(
        accepted_findings,
        changed_section_dimensions=changed_section_dimensions(
            section_ids=revised_section_ids, baseline_review=baseline_review
        ),
    )
    if not affected:
        raise AuthoringContractError("final delta review requires at least one affected dimension")
    expected_ids = {item.get("finding_id") for item in accepted_findings}
    disposition_ids = [item.get("finding_id") for item in dispositions]
    if len(disposition_ids) != len(set(disposition_ids)) or set(disposition_ids) != expected_ids:
        raise AuthoringContractError("delta dispositions do not match accepted findings")
    dimensions_by_id = {item["id"]: item for item in quality_profile["dimensions"]}
    affected_hard_failures = [
        failure_id
        for failure_id in HARD_FAILURE_IDS
        if HARD_FAILURE_DIMENSIONS[failure_id] in affected
    ]
    packet = {
        "schema_version": "matthew-exposition-final-delta-review-packet.v1",
        "baseline_manuscript_sha256": baseline_sha,
        "baseline_review_sha256": sha256_text(canonical_json(baseline_review)),
        "manuscript_sha256": sha256_text(revised_manuscript),
        "changed_paragraphs": changes,
        "changed_section_ids": revised_section_ids,
        "baseline_review": {
            "review": baseline_review,
            "verified_outcome": baseline_outcome,
        },
        "accepted_findings": accepted_findings,
        "finding_dispositions": dispositions,
        "affected_dimensions": [dimensions_by_id[item] for item in affected],
        "affected_hard_failures": affected_hard_failures,
    }
    # The delta reviewer rescores whichever dimensions the revision touched, so
    # when one of the source-judged three is among them it needs the same
    # sentences the first reviewer had. Sending them only then keeps a delta
    # packet that rescores prose alone as small as it was, and keeps a
    # dimension from being scored against different evidence in round two than
    # in round one.
    if source_slice and SOURCE_JUDGED_DIMENSIONS.intersection(affected):
        packet["source_slice"] = source_slice
    return _with_packet_size(packet, max_bytes=EDITORIAL_REVIEW_PACKET_MAX_BYTES)


def validate_final_delta_review(
    review: dict[str, Any],
    *,
    packet: dict[str, Any],
    revised_manuscript: str,
    quality_profile: dict[str, Any],
) -> None:
    validate_strict_schema(review, FINAL_DELTA_REVIEW_SCHEMA)
    current_sha = sha256_text(revised_manuscript)
    if packet.get("manuscript_sha256") != current_sha:
        raise AuthoringContractError("final delta packet does not match revised manuscript SHA")
    if review.get("reviewed_manuscript_sha256") != current_sha:
        raise AuthoringContractError("final delta review does not match revised manuscript SHA")
    affected = {item["id"] for item in packet["affected_dimensions"]}
    score_ids = [item.get("dimension_id") for item in review.get("dimension_scores", [])]
    if len(score_ids) != len(set(score_ids)) or set(score_ids) != affected:
        raise AuthoringContractError(
            "delta review must score each affected dimension exactly once"
        )
    configured = {item["id"]: item for item in quality_profile["dimensions"]}
    for item in review["dimension_scores"]:
        score = item["score"]
        if not 0 <= score <= configured[item["dimension_id"]]["weight"]:
            raise AuthoringContractError(f"invalid delta score for {item['dimension_id']}: {score}")
    allowed_failures = set(packet.get("affected_hard_failures", []))
    assessment_ids = [item.get("failure_id") for item in review["hard_failure_assessments"]]
    if len(assessment_ids) != len(set(assessment_ids)) or set(assessment_ids) != allowed_failures:
        raise AuthoringContractError(
            "delta review must assess each hard failure associated with affected dimensions"
        )
    changed_text = "\n\n".join(
        paragraph
        for change in packet["changed_paragraphs"]
        for paragraph in change["after_paragraphs"]
    )
    for finding in review.get("findings", []):
        if finding["dimension_id"] not in affected:
            raise AuthoringContractError("delta finding uses an unaffected dimension")
        anchor = _require_nonempty_string(finding.get("manuscript_anchor"), "manuscript_anchor")
        if anchor not in revised_manuscript or anchor not in changed_text:
            raise AuthoringContractError(f"delta finding anchor not found in changed manuscript text: {anchor}")


def merge_final_delta_review(
    *,
    contract: dict[str, Any] | None = None,
    baseline_review: dict[str, Any],
    baseline_outcome: dict[str, Any],
    delta_review: dict[str, Any],
    packet: dict[str, Any],
    quality_profile: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if baseline_outcome.get("manuscript_sha256") != packet.get("baseline_manuscript_sha256"):
        raise AuthoringContractError("cannot inherit scores from an unverified baseline review")
    if sha256_text(canonical_json(baseline_review)) != packet.get("baseline_review_sha256"):
        raise AuthoringContractError("baseline review does not match the verified delta packet")
    affected = {item["id"] for item in packet["affected_dimensions"]}
    baseline_scores = {
        item["dimension_id"]: dict(item) for item in baseline_review["dimension_scores"]
    }
    for item in delta_review["dimension_scores"]:
        baseline_scores[item["dimension_id"]] = dict(item)
    inherited = set(baseline_scores) - affected
    declared_failures = {
        failure_id
        for failure_id in baseline_review.get("hard_failures", [])
        if HARD_FAILURE_DIMENSIONS[failure_id] not in affected
    }
    declared_failures.update(
        item["failure_id"]
        for item in delta_review["hard_failure_assessments"]
        if item["failed"]
    )
    merged = {
        "scope_confirmation": "writing_quality_and_base_preservation",
        "summary": delta_review["summary"],
        "dimension_scores": [baseline_scores[item] for item in QUALITY_DIMENSION_IDS],
        "hard_failures": sorted(declared_failures),
        "section_reviews": baseline_review["section_reviews"],
        "findings": delta_review["findings"],
        "score_provenance": {
            "rescored_dimensions": sorted(affected),
            "inherited_dimensions": sorted(inherited),
            "baseline_manuscript_sha256": packet["baseline_manuscript_sha256"],
            "manuscript_sha256": packet["manuscript_sha256"],
        },
    }
    outcome = evaluate_editorial_review(
        merged, quality_profile, out_of_scope_dimensions(contract or {})
    )
    outcome["manuscript_sha256"] = packet["manuscript_sha256"]
    return merged, outcome


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


def _sermon_transcript_slices(
    *,
    source_documents: list[dict[str, Any]],
    scoped_fragments: list[dict[str, Any]],
    sources_manifest: dict[str, Any],
) -> dict[str, dict[str, str]]:
    """Return the full transcript-segment text behind every scoped sermon fragment.

    A `source_fragments` entry only carries the one sentence an editor picked
    as `verbatim_excerpt`; the surrounding professor speech in that same
    transcript segment is otherwise invisible to the Author Agent, even
    though `source_segment_index` already identifies exactly which segment
    it came from. This mirrors `base_manuscript_texts`, which gives the
    author the full notes manuscript rather than only its cited sentences;
    sermon transcripts previously had no equivalent and were authored from
    fragment excerpts alone.

    Scope is deliberately narrow: only the segments a scoped fragment
    actually cites, not the surrounding transcript, so a topic change in a
    neighbouring segment (a different passage's material) cannot enter the
    packet unless a fragment already grounds it.
    """

    documents_by_id = {
        item.get("source_id"): item
        for item in source_documents
        if isinstance(item, dict)
    }
    referenced_indices: dict[str, set[int]] = {}
    for fragment in scoped_fragments:
        source_id = fragment.get("source_id")
        segment_index = fragment.get("source_segment_index")
        document = documents_by_id.get(source_id)
        if document is None or document.get("source_type") != "sermon_transcript":
            continue
        if segment_index is None:
            continue
        referenced_indices.setdefault(source_id, set()).add(segment_index)

    slices: dict[str, dict[str, str]] = {}
    for source_id, indices in referenced_indices.items():
        document = documents_by_id[source_id]
        transcript_path = Path(document["source_path"])
        raw_transcript = transcript_path.read_text(encoding="utf-8")
        actual_sha256 = sha256_text(raw_transcript)
        declared_sha256 = document.get("source_sha256")
        if declared_sha256 and declared_sha256 != actual_sha256:
            raise AuthoringContractError(
                f"stale sermon transcript source: {source_id}"
            )
        transcript = json.loads(raw_transcript)
        if isinstance(transcript, list):
            # `script_review/` transcripts are a bare segment list; only
            # `script_published/` transcripts wrap it in {"script": [...]}.
            segments = transcript
        else:
            segments = transcript.get("script") or transcript.get("segments") or []
        segments_by_index = {segment.get("index"): segment for segment in segments}

        segment_texts: dict[str, str] = {}
        for segment_index in sorted(indices):
            segment = segments_by_index.get(segment_index)
            if segment is None:
                raise AuthoringContractError(
                    f"referenced sermon segment not found: {source_id}#{segment_index}"
                )
            segment_texts[str(segment_index)] = str(segment.get("text") or "")
        slices[source_id] = segment_texts
        sources_manifest[f"sermon_transcript_{source_id}"] = {
            "source_id": source_id,
            "path": str(transcript_path.resolve()),
            "sha256": actual_sha256,
            "segment_indices": sorted(str(index) for index in indices),
        }
    return slices


def contract_from_plan_payload(
    plan_payload: dict[str, Any], *, plan_document_sha256: str
) -> dict[str, Any]:
    """Reconstruct a base-contract dict from an authoring-store plan record.

    The contract used to be a standalone JSON file, referencing its plan only
    by a `composition_plan.sha256` staleness check. It is now stored as fields
    on the plan record itself (`authoring_contract_migration.py`), so this is
    the inverse of that migration's merge: it rebuilds the exact shape
    `validate_base_contract` and the rest of this module already expect,
    letting the packet builder stay unaware of whether the contract came from
    the store or a file.

    `plan_document_sha256` must be the sha256 the caller will compute over the
    exact plan document `build_authoring_packet` hashes, or the built-in
    "stale composition plan" check will always fail: the plan and its
    contract are now the same PostgreSQL record, not two files that can drift
    apart, so this reconstructs the check as an identity rather than a real
    staleness guard.
    """

    return {
        "schema_version": plan_payload.get("contract_schema_version"),
        "contract_id": plan_payload.get("contract_id"),
        "passage": plan_payload.get("passage"),
        "authoring_mode": plan_payload.get("authoring_mode"),
        "composition_plan": {
            "plan_id": plan_payload.get("plan_id"),
            "sha256": plan_document_sha256,
        },
        "base_source": plan_payload.get("base_source"),
        "additional_base_sources": plan_payload.get("additional_base_sources") or [],
        "sections": plan_payload.get("authoring_sections") or [],
        "supplemental_material": plan_payload.get("supplemental_material") or [],
        "global_rules": plan_payload.get("global_rules") or [],
        "status": "editor_confirmed" if plan_payload.get("contract_confirmed_by") else None,
    }


def build_authoring_packet_from_store(
    *,
    plan_id: str,
    store: Any,
    knowledge_path: str | Path | None = None,
    compiled_snapshot_path: str | Path | None = None,
    publication_profile_path: str | Path,
    quality_profile_path: str | Path,
) -> dict[str, Any]:
    """Build an authoring packet with the plan and contract read from PostgreSQL.

    `store` is a `PostgresKnowledgeStore` (typed as `Any` to avoid a hard
    import dependency for callers that already have one). The knowledge
    snapshot, publication profile and quality profile remain files: source
    manuscripts and shared config are not authored-plan state and do not
    belong in this migration.

    `compiled_snapshot_path` is where to keep the snapshot compiled from the
    store when `knowledge_path` is omitted. Without it the snapshot only ever
    exists inside this function's temporary directory, so every later stage
    that needs the file -- the Program Audit above all -- has nothing to read.
    Pinning it as a run artifact also means the audit sees the exact material
    the author wrote against, rather than a separately supplied file that may
    have drifted from the store.
    """

    # One definition of "the plan as a document", shared with the store's
    # `export-plan`: the file handed to the composition review and the plan
    # this packet is built from must be the same thing.
    try:
        plan = store.get_plan_document(plan_id)
    except KeyError as exc:
        raise AuthoringContractError(str(exc)) from exc
    if plan is None:
        raise AuthoringContractError(f"plan not found in authoring store: {plan_id}")
    plan_payload = {key: value for key, value in plan.items() if key != "decisions"}
    plan_document = canonical_json(plan)
    contract = contract_from_plan_payload(
        plan_payload, plan_document_sha256=sha256_text(plan_document)
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        plan_path = tmp_dir / "plan.json"
        contract_path = tmp_dir / "contract.json"
        plan_path.write_text(plan_document, encoding="utf-8")
        contract_path.write_text(canonical_json(contract), encoding="utf-8")

        # Without this, a claim promoted into the authoring store stays
        # invisible to the author: the plan comes from PostgreSQL while the
        # knowledge came from a file written before the promotion, so the
        # store is the authority for what an article may write but not for
        # what it may write *about*.
        resolved_knowledge_path = knowledge_path
        if resolved_knowledge_path is None:
            compiled = store.compile_package(package_id=f"PG-COMPILED-{plan_id}")
            # compile_package stamps a wall-clock `compiled_at`; leaving it in
            # makes packet_sha256 differ on every run with identical data,
            # which defeats the generation cache. The store's own object
            # revisions already carry when each record changed.
            compiled.pop("compiled_at", None)
            # Written where the caller can keep it when one was named. The
            # path never reaches packet_sha256 -- `sources["knowledge"]` is
            # rewritten to the compiled form below -- so a durable location
            # produces the same fingerprint the temporary one did, and an
            # existing generation cache stays valid.
            if compiled_snapshot_path is None:
                compiled_path = tmp_dir / "knowledge.json"
            else:
                compiled_path = Path(compiled_snapshot_path)
                compiled_path.parent.mkdir(parents=True, exist_ok=True)
            compiled_path.write_text(canonical_json(compiled), encoding="utf-8")
            resolved_knowledge_path = compiled_path

        packet = build_authoring_packet(
            plan_path=plan_path,
            knowledge_path=resolved_knowledge_path,
            contract_path=contract_path,
            publication_profile_path=publication_profile_path,
            quality_profile_path=quality_profile_path,
        )

    # The plan and contract were staged through a temporary directory, whose
    # name differs on every run. Left in `sources`, it makes packet_sha256
    # non-deterministic, which silently defeats the generation cache: every
    # run would look like new inputs and re-call the models. Record where they
    # actually came from instead; the content sha256 stays as computed.
    for key, object_id in (("plan", plan_id), ("base_contract", plan_payload.get("contract_id"))):
        packet["sources"][key] = {
            "authority": "postgresql_authoring_store",
            "collection": "composition_plans",
            "object_id": object_id,
            "plan_revision": plan_payload.get("revision"),
            "sha256": packet["sources"][key]["sha256"],
        }
    if knowledge_path is None:
        # Compiled from the store through the same temporary directory, so it
        # carries the same per-run path that would break the fingerprint.
        packet["sources"]["knowledge"] = {
            "authority": "postgresql_authoring_store",
            "compiled": True,
            "sha256": packet["sources"]["knowledge"]["sha256"],
        }
    packet["packet_sha256"] = sha256_text(canonical_json({
        key: value for key, value in packet.items() if key != "packet_sha256"
    }))
    return packet


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
    # A required argument step is an obligation to write specific reasoning,
    # so the claim carrying that reasoning must be in scope. Without this the
    # contract obliges the author to write something the grounding gate then
    # reports as unsupported, because the material never reached the packet.
    scoped_claim_ids.update(
        claim_id
        for section in contract.get("sections", [])
        for step in section.get("required_argument_steps", [])
        if (claim_id := step.get("claim_id"))
    )
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
        for fragment_id in evidence_step_fragment_ids(step)
    }
    scoped_fragments = [
        item
        for item in knowledge.get("source_fragments", [])
        if item.get("fragment_id") in fragment_ids
    ]
    scoped_source_ids = {item.get("source_id") for item in scoped_fragments}
    sermon_transcript_texts = _sermon_transcript_slices(
        source_documents=knowledge.get("source_documents", []),
        scoped_fragments=scoped_fragments,
        sources_manifest=sources,
    )
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
        # Three collections used to ride along here and no longer do. They
        # were 24% of a 372KB packet, sent again on every draft, and no author
        # prompt has ever mentioned any of them:
        #
        #   product_plans   -- the same plan already at `packet["plan"]`, sent
        #                      a second time inside the knowledge slice.
        #   topic_nodes     -- all 59, the one collection this otherwise
        #                      carefully scoped slice never filtered. The
        #                      editorial review packet's own `scope.exclude`
        #                      lists topic_nodes as out of scope for this work.
        #   knowledge_routes-- where each claim goes next (exposition, topic,
        #                      Q&A). Editorial workflow state, not material to
        #                      write from.
        #
        # Nothing in the pipeline reads them off this packet: the grounding
        # check and the review packet take claims, evidence steps and source
        # fragments, and the Program Audit reads the snapshot file instead.
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
        "sermon_transcript_texts": sermon_transcript_texts,
        "publication_gate": {
            "eligible": False,
            "reason": "human publication approval and external program audit are not part of authoring",
        },
    }
    packet["packet_sha256"] = sha256_text(canonical_json(packet))
    return packet
