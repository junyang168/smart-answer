"""Ask about the sentences reconciliation could not account for, and only those.

Reconciliation names what is unaccounted for; it cannot produce the missing
material. Without this stage the gate is a red light with no path to green,
and the first person to meet a queue they cannot drain switches it off.

The question put here is deliberately narrow and closed. Whole-source
extraction is an open-ended recall problem and cannot be verified from the
inside -- that is the whole reason the ledger exists. But "does the material
reason from this particular sentence, yes or no" is a question with a checkable
answer, asked about a sentence already in hand.

Two pressures are designed against, because the model answering is the one that
missed these sentences in the first place:

  * `no_argument` is the cheapest way to make the queue go away.  Every
    sentence must be answered exactly once and no answer may be omitted, so
    silence is not available; and `background_only` is never terminal without
    a person, so the cheap answer does not clear the gate by itself.
  * inventing an inference is the cheapest way to look diligent.  Every excerpt
    is verified verbatim against the paragraph it claims to come from before
    anything is accepted, so a fabricated quotation fails mechanically.

Nothing here writes to the store and nothing is approved: every product of
this stage is `candidate`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from backend.pipeline.observation_type_vocabulary import OBSERVATION_TYPES
from backend.pipeline.sentence_ledger import REASON_CODES

PROMPT_PATH = Path(__file__).with_name("prompts") / "sentence_ledger_second_pass.md"

CARRIES_ARGUMENT = "carries_argument"
IS_ASSERTION = "is_assertion"
NO_ARGUMENT = "no_argument"
VERDICTS = (CARRIES_ARGUMENT, IS_ASSERTION, NO_ARGUMENT)

SECOND_PASS_VERSION = "wang_sentence_ledger_second_pass_v1"


class SecondPassValidationError(ValueError):
    """Raised when a response cannot be accepted without losing traceability."""


SECOND_PASS_SCHEMA: dict[str, Any] = {
    "name": SECOND_PASS_VERSION,
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdicts"],
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "sentence_id", "verdict", "observation", "evidence_step",
                        "claim", "reason_code", "duplicate_of_record_id", "rationale",
                    ],
                    "properties": {
                        "sentence_id": {"type": "string"},
                        "verdict": {"type": "string", "enum": list(VERDICTS)},
                        "observation": {
                            "anyOf": [
                                {"type": "null"},
                                {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["statement", "observation_type", "supporting_excerpt"],
                                    "properties": {
                                        "statement": {"type": "string"},
                                        "observation_type": {"type": "string", "enum": list(OBSERVATION_TYPES)},
                                        "supporting_excerpt": {"type": "string"},
                                    },
                                },
                            ]
                        },
                        "evidence_step": {
                            "anyOf": [
                                {"type": "null"},
                                {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["statement", "supporting_excerpt"],
                                    "properties": {
                                        "statement": {"type": "string"},
                                        "supporting_excerpt": {"type": "string"},
                                    },
                                },
                            ]
                        },
                        "claim": {
                            "anyOf": [
                                {"type": "null"},
                                {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["statement", "attribution", "supporting_excerpt"],
                                    "properties": {
                                        "statement": {"type": "string"},
                                        "attribution": {"type": "string", "enum": ["professor", "editorial_inference"]},
                                        "supporting_excerpt": {"type": "string"},
                                    },
                                },
                            ]
                        },
                        "reason_code": {"anyOf": [{"type": "null"}, {"type": "string", "enum": sorted(REASON_CODES)}]},
                        "duplicate_of_record_id": {"anyOf": [{"type": "null"}, {"type": "string"}]},
                        "rationale": {"type": "string"},
                    },
                },
            }
        },
    },
}


@dataclass(frozen=True)
class SecondPassQuestion:
    """One unaccounted sentence, with the paragraph it has to be judged against."""

    sentence_id: str
    text: str
    segment_index: int
    segment_text: str


def build_questions(
    reconciliation_rows: Sequence[Any],
    inventory_by_id: dict[str, Any],
    segments_by_index: dict[int, str],
    *,
    unprocessed_status: str = "unprocessed",
) -> list[SecondPassQuestion]:
    """Only the unaccounted sentences. Nothing already settled is re-litigated."""

    questions: list[SecondPassQuestion] = []
    for row in reconciliation_rows:
        if row.status != unprocessed_status:
            continue
        sentence = inventory_by_id.get(row.sentence_id)
        if sentence is None:
            continue
        questions.append(
            SecondPassQuestion(
                sentence_id=sentence.sentence_id,
                text=sentence.text,
                segment_index=sentence.segment_index,
                segment_text=segments_by_index.get(sentence.segment_index, ""),
            )
        )
    return questions


def render_request(questions: Sequence[SecondPassQuestion]) -> str:
    """The paragraph is sent with each sentence, not the whole source.

    Judging whether the material reasons from a sentence needs the sentence's
    own context and nothing more; sending the source again would restore the
    open-ended reading this stage exists to avoid.
    """

    blocks: list[str] = []
    for question in questions:
        blocks.append(
            f"[sentence_id] {question.sentence_id}\n"
            f"[待判定句] {question.text}\n"
            f"[所在段落 S{question.segment_index:04d}]\n{question.segment_text}"
        )
    return "以下每一句都必须恰好判定一次。\n\n" + "\n\n---\n\n".join(blocks)


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_response(
    response: dict[str, Any], questions: Sequence[SecondPassQuestion]
) -> None:
    """Every sentence answered exactly once, and every quotation real.

    The coverage check is the load-bearing one. A model that may quietly skip
    sentences can make any residue disappear, which would reproduce the exact
    failure the ledger was built to end.
    """

    errors: list[str] = []
    asked = {q.sentence_id: q for q in questions}
    verdicts = response.get("verdicts") or []

    seen: dict[str, int] = {}
    for row in verdicts:
        seen[row.get("sentence_id", "")] = seen.get(row.get("sentence_id", ""), 0) + 1

    for sentence_id in asked:
        count = seen.get(sentence_id, 0)
        _require(count == 1, f"{sentence_id}: answered {count} times, expected exactly 1", errors)
    for sentence_id in seen:
        _require(sentence_id in asked, f"{sentence_id}: not one of the sentences asked about", errors)

    for row in verdicts:
        sid = row.get("sentence_id", "?")
        question = asked.get(sid)
        verdict = row.get("verdict")
        if question is None:
            continue

        if verdict == CARRIES_ARGUMENT:
            _require(bool(row.get("observation")), f"{sid}: {CARRIES_ARGUMENT} needs an observation", errors)
            _require(bool(row.get("evidence_step")), f"{sid}: {CARRIES_ARGUMENT} needs the step it supports", errors)
        elif verdict == IS_ASSERTION:
            _require(bool(row.get("claim")), f"{sid}: {IS_ASSERTION} needs a claim", errors)
        elif verdict == NO_ARGUMENT:
            reason = row.get("reason_code")
            _require(reason in REASON_CODES, f"{sid}: reason_code {reason!r} is not one of {sorted(REASON_CODES)}", errors)
            _require(bool((row.get("rationale") or "").strip()), f"{sid}: rationale must not be empty", errors)
            if reason == "duplicate_of":
                _require(
                    bool((row.get("duplicate_of_record_id") or "").strip()),
                    f"{sid}: duplicate_of must name the record that covers it",
                    errors,
                )

        # Every quotation must be verbatim in the paragraph it claims to be from.
        for field in ("observation", "evidence_step", "claim"):
            payload = row.get(field)
            if not payload:
                continue
            excerpt = (payload.get("supporting_excerpt") or "").strip()
            _require(bool(excerpt), f"{sid}: {field} has an empty supporting_excerpt", errors)
            if excerpt:
                _require(
                    excerpt in question.segment_text,
                    f"{sid}: {field} supporting_excerpt is not verbatim in segment "
                    f"S{question.segment_index:04d}",
                    errors,
                )

    if errors:
        raise SecondPassValidationError("second pass validation failed: " + " | ".join(errors))


def verdict_counts(response: dict[str, Any]) -> dict[str, int]:
    """What the model did with the queue, so a run can be judged at a glance.

    A pass that answered `no_argument` to nearly everything has not drained the
    residue; it has renamed it.
    """

    counts = {verdict: 0 for verdict in VERDICTS}
    reasons: dict[str, int] = {}
    for row in response.get("verdicts") or []:
        verdict = row.get("verdict")
        if verdict in counts:
            counts[verdict] += 1
        if verdict == NO_ARGUMENT:
            reason = row.get("reason_code") or "?"
            reasons[reason] = reasons.get(reason, 0) + 1
    counts["reason_codes"] = reasons  # type: ignore[assignment]
    return counts


def response_fingerprint(prompt: str, questions: Sequence[SecondPassQuestion]) -> str:
    payload = json.dumps(
        {
            "version": SECOND_PASS_VERSION,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "sentence_ids": [q.sentence_id for q in questions],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
