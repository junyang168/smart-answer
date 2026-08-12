from __future__ import annotations

import hashlib
import json
from typing import Any


EXTRACTION_VERSION = "wang_detailed_knowledge_extraction_v1"

CLAIM_KINDS = [
    "explicit_claim",
    "reasoning_conclusion",
    "interpretive_judgment",
    "interpretive_method",
    "application",
    "editorial_inference",
]
STEP_TYPES = [
    "question",
    "scripture_evidence",
    "original_language",
    "literary_context",
    "historical_background",
    "reasoning",
    "answer",
    "qualification",
    "application",
    "dialogue_context",
]
RELATION_TYPES = ["supports", "answers", "qualifies", "applies", "refutes", "contextualizes"]
SPEAKERS = ["professor", "audience", "quoted_source", "editorial"]
STANCES = ["asserted", "questioned", "opposed", "quoted", "neutral"]
ELIGIBILITY = ["eligible_candidate", "context_only", "withheld_unreviewed"]


ANCHOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "segment_index": {"type": "string"},
        "start_time": {"type": ["number", "null"]},
        "end_time": {"type": ["number", "null"]},
        "verbatim_excerpt": {"type": "string"},
    },
    "required": ["segment_index", "start_time", "end_time", "verbatim_excerpt"],
}


DETAILED_RESPONSE_SCHEMA: dict[str, Any] = {
    "name": EXTRACTION_VERSION,
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "question_id": {"type": "string"},
                        "text": {"type": "string"},
                        "questioner": {"type": "string", "enum": ["professor", "audience", "editorial"]},
                        "question_type": {"type": "string"},
                        "answer_state": {"type": "string", "enum": ["answered", "partially_answered", "unanswered"]},
                        "answer_claim_ids": {"type": "array", "items": {"type": "string"}},
                        "anchors": {"type": "array", "items": ANCHOR_SCHEMA},
                    },
                    "required": ["question_id", "text", "questioner", "question_type", "answer_state", "answer_claim_ids", "anchors"],
                },
            },
            "positions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "position_id": {"type": "string"},
                        "title": {"type": "string"},
                        "attribution": {"type": "string"},
                        "anchors": {"type": "array", "items": ANCHOR_SCHEMA},
                    },
                    "required": ["position_id", "title", "attribution", "anchors"],
                },
            },
            "observations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "observation_id": {"type": "string"},
                        "statement": {"type": "string"},
                        "observation_type": {"type": "string"},
                        "scripture_refs": {"type": "array", "items": {"type": "string"}},
                        "anchors": {"type": "array", "items": ANCHOR_SCHEMA},
                    },
                    "required": ["observation_id", "statement", "observation_type", "scripture_refs", "anchors"],
                },
            },
            "evidence_steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "evidence_step_id": {"type": "string"},
                        "statement": {"type": "string"},
                        "step_type": {"type": "string", "enum": STEP_TYPES},
                        "speaker": {"type": "string", "enum": SPEAKERS},
                        "stance": {"type": "string", "enum": STANCES},
                        "discourse_role": {"type": "string"},
                        "support_eligibility": {"type": "string", "enum": ELIGIBILITY},
                        "scripture_refs": {"type": "array", "items": {"type": "string"}},
                        "produced_claim_ids": {"type": "array", "items": {"type": "string"}},
                        "anchors": {"type": "array", "items": ANCHOR_SCHEMA},
                    },
                    "required": [
                        "evidence_step_id", "statement", "step_type", "speaker", "stance",
                        "discourse_role", "support_eligibility", "scripture_refs",
                        "produced_claim_ids", "anchors"
                    ],
                },
            },
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "claim_id": {"type": "string"},
                        "statement": {"type": "string"},
                        "claim_kind": {"type": "string", "enum": CLAIM_KINDS},
                        "attribution": {"type": "string", "enum": ["professor", "editorial_inference"]},
                        "scripture_refs": {"type": "array", "items": {"type": "string"}},
                        "topic_terms": {"type": "array", "items": {"type": "string"}},
                        "evidence_step_ids": {"type": "array", "items": {"type": "string"}},
                        "opposed_position_ids": {"type": "array", "items": {"type": "string"}},
                        "review_status": {"type": "string", "enum": ["candidate"]},
                    },
                    "required": [
                        "claim_id", "statement", "claim_kind", "attribution", "scripture_refs",
                        "topic_terms", "evidence_step_ids", "opposed_position_ids", "review_status"
                    ],
                },
            },
            "evidence_relations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "relation_id": {"type": "string"},
                        "from_id": {"type": "string"},
                        "to_id": {"type": "string"},
                        "relation_type": {"type": "string", "enum": RELATION_TYPES},
                        "reason": {"type": "string"},
                    },
                    "required": ["relation_id", "from_id", "to_id", "relation_type", "reason"],
                },
            },
            "claim_relations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "claim_relation_id": {"type": "string"},
                        "from_id": {"type": "string"},
                        "to_id": {"type": "string"},
                        "relation_type": {"type": "string", "enum": RELATION_TYPES},
                        "reason": {"type": "string"},
                    },
                    "required": ["claim_relation_id", "from_id", "to_id", "relation_type", "reason"],
                },
            },
        },
        "required": [
            "questions", "positions", "observations", "evidence_steps", "claims",
            "evidence_relations", "claim_relations"
        ],
    },
}


class DetailedExtractionValidationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DetailedExtractionValidationError(message)


def extraction_identity(
    *,
    source_sha256: str,
    prompt: str,
    model_id: str,
    reasoning_effort: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    generation = {
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "model_id": model_id,
        "reasoning_effort": reasoning_effort,
        "max_output_tokens": max_output_tokens,
        "schema_version": EXTRACTION_VERSION,
        "response_schema_sha256": hashlib.sha256(
            json.dumps(DETAILED_RESPONSE_SCHEMA, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }
    generation_fingerprint = hashlib.sha256(
        json.dumps(generation, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    full = {"source_sha256": source_sha256, **generation, "generation_fingerprint_sha256": generation_fingerprint}
    full["fingerprint_sha256"] = hashlib.sha256(
        json.dumps(full, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return full


def validate_response(response: dict[str, Any], transcript: dict[str, Any]) -> None:
    segments = {f"S{index + 1:04d}": segment for index, segment in enumerate(transcript.get("script", []))}
    collections = {
        "question": (response.get("questions", []), "question_id"),
        "position": (response.get("positions", []), "position_id"),
        "observation": (response.get("observations", []), "observation_id"),
        "evidence": (response.get("evidence_steps", []), "evidence_step_id"),
        "claim": (response.get("claims", []), "claim_id"),
        "evidence relation": (response.get("evidence_relations", []), "relation_id"),
        "claim relation": (response.get("claim_relations", []), "claim_relation_id"),
    }
    ids: dict[str, set[str]] = {}
    for label, (rows, key) in collections.items():
        values = [str(row.get(key) or "") for row in rows]
        _require(all(values), f"{label}: missing ID")
        _require(len(values) == len(set(values)), f"{label}: duplicate ID")
        ids[label] = set(values)

    anchor_errors: list[str] = []

    def check_anchors(owner: str, anchors: list[dict[str, Any]]) -> None:
        if not anchors:
            anchor_errors.append(f"{owner}: at least one source anchor is required")
            return
        for anchor in anchors:
            locator = str(anchor.get("segment_index") or "")
            if locator not in segments:
                anchor_errors.append(f"{owner}: missing segment {locator}")
                continue
            segment = segments[locator]
            excerpt = str(anchor.get("verbatim_excerpt") or "")
            if not excerpt:
                anchor_errors.append(f"{owner}: empty verbatim excerpt in {locator}")
            elif excerpt not in str(segment.get("text") or ""):
                anchor_errors.append(f"{owner}: excerpt is not verbatim in {locator}")

    for collection_name, id_key in (
        ("questions", "question_id"),
        ("positions", "position_id"),
        ("observations", "observation_id"),
        ("evidence_steps", "evidence_step_id"),
    ):
        for row in response.get(collection_name, []):
            check_anchors(str(row.get(id_key) or collection_name), row.get("anchors") or [])
    validation_errors = list(anchor_errors)

    def collect(condition: bool, message: str) -> None:
        if not condition:
            validation_errors.append(message)

    for row in response.get("questions", []):
        collect(
            set(row["answer_claim_ids"]) <= ids["claim"],
            f"{row['question_id']}: unknown answer claim",
        )
    for row in response.get("evidence_steps", []):
        collect(
            set(row["produced_claim_ids"]) <= ids["claim"],
            f"{row['evidence_step_id']}: unknown claim",
        )
        if row["speaker"] != "professor" or row["stance"] != "asserted":
            collect(
                row["support_eligibility"] != "eligible_candidate",
                f"{row['evidence_step_id']}: non-professor/asserted evidence cannot be eligible",
            )
    for row in response.get("claims", []):
        collect(row["review_status"] == "candidate", f"{row['claim_id']}: extraction cannot approve")
        collect(
            set(row["evidence_step_ids"]) <= ids["evidence"],
            f"{row['claim_id']}: unknown evidence",
        )
        collect(
            set(row["opposed_position_ids"]) <= ids["position"],
            f"{row['claim_id']}: unknown opposed position",
        )
        collect(bool(row["evidence_step_ids"]), f"{row['claim_id']}: claim has no evidence")
    for row in response.get("evidence_relations", []):
        collect(
            row["from_id"] in ids["evidence"] and row["to_id"] in ids["evidence"],
            f"{row['relation_id']}: unknown evidence endpoint",
        )
    for row in response.get("claim_relations", []):
        collect(
            row["from_id"] in ids["claim"] and row["to_id"] in ids["claim"],
            f"{row['claim_relation_id']}: unknown claim endpoint",
        )
    if validation_errors:
        raise DetailedExtractionValidationError(
            "mechanical validation failed: " + " | ".join(validation_errors)
        )
