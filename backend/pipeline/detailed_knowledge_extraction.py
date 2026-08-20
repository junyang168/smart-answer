from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Sequence

from backend.pipeline.observation_type_vocabulary import OBSERVATION_TYPES
from backend.pipeline.sentence_ledger_vocabulary import REASON_CODES

# v2 closes `observation_type` to the six categories the prompt already names.
# v3 moved the unit of extraction from the document to an overlapping window.
# v4 (#88) replaces the window with the `##` section the source was composed in,
# and adds `sentence_audit`: the response now has to account for every sentence
# of its section, not merely produce records from it.  Measured, that closed
# question is what lifts substantive-prose coverage from 50% to 100% -- the
# small chunk was never the lever.  The schema hash feeds
# `response_schema_sha256`, so every extraction keeps its own fingerprint and
# stays valid as the record of what that run was actually asked for.
EXTRACTION_VERSION = "wang_detailed_knowledge_extraction_v4"

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
# Whether the professor reasoned from an observation or only noted it.  The
# publication profile's own test is "delete this observation; does the
# paragraph's conclusion still hold?"  `load_bearing` is the answer "no", and
# it obliges the extraction to also record the step that reasoning took --
# see `validate_response`.
ARGUMENT_ROLES = ["load_bearing", "background"]
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
                        "observation_type": {"type": "string", "enum": list(OBSERVATION_TYPES)},
                        "argument_role": {"type": "string", "enum": ARGUMENT_ROLES},
                        "scripture_refs": {"type": "array", "items": {"type": "string"}},
                        "anchors": {"type": "array", "items": ANCHOR_SCHEMA},
                    },
                    "required": [
                        "observation_id", "statement", "observation_type", "argument_role",
                        "scripture_refs", "anchors"
                    ],
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
            "sentence_audit": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "sentence_id": {"type": "string"},
                        "status": {"type": "string", "enum": ["extracted", "not_extracted"]},
                        "covered_by": {"type": "array", "items": {"type": "string"}},
                        # The vocabulary the ledger already uses for exclusions,
                        # so a `not_extracted` verdict becomes an ExclusionRecord
                        # instead of free text nobody can act on.  Terminality
                        # depends on which code it is, which is why the model
                        # picks from a closed set rather than describing itself.
                        "reason_code": {"anyOf": [{"type": "null"},
                                                  {"type": "string", "enum": sorted(REASON_CODES)}]},
                        "reason": {"type": "string"},
                    },
                    "required": ["sentence_id", "status", "covered_by", "reason_code", "reason"],
                },
            },
        },
        "required": [
            "questions", "positions", "observations", "evidence_steps", "claims",
            "evidence_relations", "claim_relations", "sentence_audit"
        ],
    },
}


class DetailedExtractionValidationError(ValueError):
    pass


@dataclass(frozen=True)
class AuditedSentence:
    """One sentence the response has to give a verdict on."""

    sentence_id: str
    segment_index: str
    text: str


def anchor_spans(response: dict[str, Any], transcript: dict[str, Any]) -> dict[str, list[tuple[int, int]]]:
    """Where the response's anchors land, keyed by segment locator.

    Excerpts are verified verbatim elsewhere in this module, so a plain find is
    exact here; a missing excerpt simply contributes no span.
    """

    segments = {f"S{index + 1:04d}": str(segment.get("text") or "")
                for index, segment in enumerate(transcript.get("script", []))}
    spans: dict[str, list[tuple[int, int]]] = {}
    for collection in ("questions", "positions", "observations", "evidence_steps"):
        for row in response.get(collection, []) or []:
            for anchor in row.get("anchors") or []:
                locator = str(anchor.get("segment_index") or "")
                excerpt = str(anchor.get("verbatim_excerpt") or "")
                text = segments.get(locator)
                if not excerpt or text is None:
                    continue
                start = text.find(excerpt)
                if start >= 0:
                    spans.setdefault(locator, []).append((start, start + len(excerpt)))
    return spans


def validate_sentence_audit(
    response: dict[str, Any],
    transcript: dict[str, Any],
    sentences: Sequence[AuditedSentence],
) -> None:
    """Check the response accounted for every sentence, and told the truth about it.

    Two pressures are designed against, and both were observed in real runs:

      * silence. Every sentence must carry exactly one verdict, so "I did not
        mention it" is not available.
      * the semantic dodge. Opus reported four sentences `extracted` whose
        reasons read "已由 O7/E4 涵蓋" and "與 E5 同義" -- it was answering
        "is this material present somewhere" while the ledger asks "does an
        anchor land on this sentence". Only the latter counts here, because
        only the latter is what every downstream gate can see.
    """

    segments = {f"S{index + 1:04d}": str(segment.get("text") or "")
                for index, segment in enumerate(transcript.get("script", []))}
    spans = anchor_spans(response, transcript)
    rows = response.get("sentence_audit") or []
    by_id: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for row in rows:
        sentence_id = str(row.get("sentence_id") or "")
        if sentence_id in by_id:
            errors.append(f"{sentence_id}: audited more than once")
            continue
        by_id[sentence_id] = row
    expected = {sentence.sentence_id for sentence in sentences}
    for extra in sorted(set(by_id) - expected):
        errors.append(f"{extra}: audit names a sentence that is not in this section")
    for sentence in sentences:
        row = by_id.get(sentence.sentence_id)
        if row is None:
            errors.append(f"{sentence.sentence_id}: no verdict for this sentence")
            continue
        text = segments.get(sentence.segment_index, "")
        start = text.find(sentence.text)
        covered = False
        if start >= 0:
            end = start + len(sentence.text)
            covered = any(
                left < end and start < right
                for left, right in spans.get(sentence.segment_index, [])
            )
        if row.get("status") == "extracted" and not covered:
            errors.append(
                f"{sentence.sentence_id}: reported extracted, but no anchor lands on it; "
                f"either anchor a record to this sentence or report it not_extracted"
            )
        if row.get("status") == "not_extracted":
            if not str(row.get("reason") or "").strip():
                errors.append(f"{sentence.sentence_id}: not_extracted without a reason")
            if row.get("reason_code") not in REASON_CODES:
                errors.append(
                    f"{sentence.sentence_id}: not_extracted needs a reason_code from "
                    f"{sorted(REASON_CODES)}, got {row.get('reason_code')!r}"
                )
    if errors:
        raise DetailedExtractionValidationError(
            "sentence audit failed: " + " | ".join(errors)
        )


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
    section_plan: dict[str, Any] | None = None,
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
    # How the source was cut is part of what the run was asked for.  Left out,
    # a source resegmented by a later generator run matches the stored
    # fingerprint and is skipped, leaving a package in staging that answers a
    # question nobody is asking any more.
    if section_plan is not None:
        generation["section_plan"] = json.loads(json.dumps(section_plan, sort_keys=True))
    generation_fingerprint = hashlib.sha256(
        json.dumps(generation, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    full = {"source_sha256": source_sha256, **generation, "generation_fingerprint_sha256": generation_fingerprint}
    full["fingerprint_sha256"] = hashlib.sha256(
        json.dumps(full, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return full


def validate_response(
    response: dict[str, Any],
    transcript: dict[str, Any],
    *,
    visible_locators: set[str] | None = None,
    require_load_bearing_relations: bool = True,
) -> None:
    """Check one response against the source it claims to come from.

    Two knobs exist for windowed extraction (#88), and both default to the
    whole-document behaviour so the merged package is still held to the full
    contract.

    `visible_locators` restricts anchors to the slice a window was shown, which
    is the only way to catch a locator invented outside the frame.

    `require_load_bearing_relations` is switched off *per window* because the
    rule is unanswerable there: the step a load_bearing observation reasons to
    may sit in the next window's fetch zone.  Enforced per window it does not
    make the model try harder -- it makes the cheapest passing move relabelling
    the observation `background`, which is exactly the loss #86 closed.  So the
    rule moves to where it can be answered: the merged package.
    """

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
            if visible_locators is not None and locator not in visible_locators:
                anchor_errors.append(f"{owner}: {locator} is outside this window")
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

    for row in response.get("observations", []):
        collect(
            row.get("observation_type") in OBSERVATION_TYPES,
            f"{row['observation_id']}: observation_type is outside the vocabulary: "
            f"{row.get('observation_type')!r}",
        )
        collect(
            row.get("argument_role") in ARGUMENT_ROLES,
            f"{row['observation_id']}: argument_role must be one of {ARGUMENT_ROLES}, "
            f"got {row.get('argument_role')!r}",
        )
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
    # An observation may be the source of a relation into the argument: that
    # edge is how "the professor reasoned from this" is recorded at all.  The
    # target stays an evidence step -- observations do not support each other.
    supported_by_observation: set[str] = set()
    for row in response.get("evidence_relations", []):
        from_id = row["from_id"]
        collect(
            from_id in ids["evidence"] or from_id in ids["observation"],
            f"{row['relation_id']}: unknown relation source",
        )
        collect(
            row["to_id"] in ids["evidence"],
            f"{row['relation_id']}: unknown evidence endpoint",
        )
        if from_id in ids["observation"] and row["to_id"] in ids["evidence"]:
            supported_by_observation.add(from_id)

    # The rule this whole schema change exists for.  An observation the
    # professor reasoned from must also record the step that reasoning took.
    # Without it an extraction can produce the lexical fact and silently drop
    # the inference drawn from it in the very next sentence, which is what
    # happened to Matt 16:23's phroneo: nine drafts could not use an
    # observation that had never become part of any argument.
    for row in response.get("observations", []):
        if not require_load_bearing_relations or row.get("argument_role") != "load_bearing":
            continue
        collect(
            row["observation_id"] in supported_by_observation,
            f"{row['observation_id']}: load_bearing observation has no relation "
            f"to an evidence step; either record the step the professor reasoned "
            f"to, or mark it background",
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


def exclusions_from_audit(
    response: dict[str, Any],
    sentences: Sequence[AuditedSentence],
    *,
    source_id: str,
    ledger_sentence_id: Any,
) -> list[dict[str, Any]]:
    """Turn `not_extracted` verdicts into candidate exclusions.

    Without this the audit's judgements die with the response: `combine_sections`
    carries only the record collections, so 81 reasoned decisions on the 太16
    母本 reached no reader, and the ledger showed those sentences as
    `unprocessed` -- "nobody answered" -- when in fact somebody had.

    Nothing here is approved. `decided_by` stays empty precisely because the
    model that produced the verdict is not a person, and `is_terminal` will
    keep every code but `duplicate_of` out of the terminal column until one
    looks. The point is to separate "answered, awaiting review" from "not
    answered at all", which are the same colour today.
    """

    by_id = {row.sentence_id: row for row in sentences}
    # Which occurrence of its own text this sentence is, counted over *every*
    # sentence in the section, because that is how the ledger's inventory
    # counts. Counting only the excluded ones renumbers them: a transcript
    # repeats "為什麼緣故？" twice in one segment, and excluding the second
    # alone addressed the first -- which a fragment had already represented,
    # so one sentence came back both excluded and represented and its twin
    # came back unanswered.
    ordinals: dict[str, int] = {}
    seen: dict[tuple[str, str], int] = {}
    for row in sentences:
        key = (row.segment_index, row.text)
        ordinals[row.sentence_id] = seen.get(key, 0)
        seen[key] = ordinals[row.sentence_id] + 1
    rows: list[dict[str, Any]] = []
    for entry in response.get("sentence_audit") or []:
        if entry.get("status") != "not_extracted":
            continue
        sentence = by_id.get(str(entry.get("sentence_id") or ""))
        if sentence is None:
            continue
        ordinal = ordinals[sentence.sentence_id]
        identifier = ledger_sentence_id(
            source_id, int(sentence.segment_index[1:]), sentence.text, ordinal
        )
        rows.append({
            "exclusion_id": f"EXC-{identifier}",
            "sentence_id": identifier,
            "source_id": source_id,
            "segment_index": sentence.segment_index,
            "text": sentence.text,
            "reason_code": entry.get("reason_code"),
            "rationale": str(entry.get("reason") or ""),
            "duplicate_of_record_id": None,
            "decided_by": None,
            "review_status": "candidate",
        })
    return rows
