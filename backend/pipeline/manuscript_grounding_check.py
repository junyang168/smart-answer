"""Check whether a manuscript's paragraphs assert content beyond their sources.

The pipeline has two families of check on a paragraph's provenance:
deterministic ones (a required-step anchor is or is not a verbatim substring
of the manuscript; an application chain's fields are or are not present) and
this one, which cannot be deterministic. A paraphrase can be faithful or can
quietly add a reason, a motive, or a conclusion the source never states --
"這不是否定祂是王，而是糾正人用政治勝利界定彌賽亞的錯誤" is not a verbatim
substring of anything in matthew-16-21-23's material, and no substring check
would catch it, because it is not a substring of anything; it is invented
prose dressed as professor attribution. Judging that requires reading the
paragraph against its declared material, which is a closed question a model
can answer far more cheaply and reliably than it can write an article.

Both `professor` and `editorial_synthesis` paragraphs declare `claim_ids` in
this pipeline's actual output (confirmed against a real run), so one packet
shape and one question cover both attributions. `scripture` paragraphs are
quotations already verified verbatim elsewhere and are skipped; bare `editor`
labels carry no claim_ids and are skipped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from backend.pipeline.matthew_exposition_authoring import (
    canonical_json,
    sha256_text,
    validate_strict_schema,
)

PROMPT_PATH = Path(__file__).with_name("prompts") / "manuscript_grounding_check.md"

PROVENANCE_COMMENT_RE = re.compile(r"<!--\s*provenance:\s*(\{.*?\})\s*-->", re.S)

GROUNDING_PACKET_MAX_BYTES = 20_000

GROUNDING_RESULT_SCHEMA: dict[str, Any] = {
    "name": "matthew_exposition_grounding_result_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "exceeds_material", "unsupported_assertions", "notes"],
        "properties": {
            "schema_version": {
                "type": "string",
                "enum": ["matthew-exposition-grounding-result.v1"],
            },
            "exceeds_material": {"type": "boolean"},
            "unsupported_assertions": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string"},
        },
    },
}


class GroundingCheckError(RuntimeError):
    """Raised when a paragraph or its packet cannot be checked as given."""


def extract_provenance_paragraphs(markdown: str) -> list[dict[str, Any]]:
    """Pair every provenance comment with the paragraph text it governs.

    A paragraph is the run of non-blank lines immediately following the
    comment, up to the next blank line or the next provenance comment,
    whichever comes first -- matching how the author writes one comment
    directly above the paragraph it describes.
    """

    paragraphs: list[dict[str, Any]] = []
    matches = list(PROVENANCE_COMMENT_RE.finditer(markdown))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        segment = markdown[start:end]
        next_comment = PROVENANCE_COMMENT_RE.search(segment)
        if next_comment:
            segment = segment[: next_comment.start()]
        text = segment.strip()
        try:
            provenance = json.loads(match.group(1))
        except json.JSONDecodeError:
            provenance = None
        paragraphs.append(
            {"provenance": provenance, "paragraph_text": text, "comment_offset": match.start()}
        )
    return paragraphs


def instructions_from_contract(contract: dict[str, Any]) -> dict[str, str]:
    """Map claim_id -> the required step's editorial instruction.

    The contract is the authority for what the editorial board decided; a
    claim record may or may not carry a copy, depending on whether it was
    created by the step backfill or already existed and was reused. Deriving
    the map here means a reused claim is not silently missing the instruction
    its step imposes.
    """

    instructions: dict[str, str] = {}
    for section in contract.get("sections") or []:
        for step in section.get("required_argument_steps") or []:
            claim_id = step.get("claim_id")
            statement = step.get("statement")
            if claim_id and statement:
                instructions[str(claim_id)] = str(statement)
    return instructions


def build_paragraph_material(
    claim_ids: list[str],
    knowledge: dict[str, Any],
    instructions_by_claim: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return the claim/evidence/source material a paragraph is allowed to use."""

    claims_by_id = {c.get("claim_id"): c for c in knowledge.get("claims", [])}
    evidence_by_id = {e.get("evidence_step_id"): e for e in knowledge.get("evidence_steps", [])}
    fragments_by_id = {f.get("fragment_id"): f for f in knowledge.get("source_fragments", [])}

    material: list[dict[str, Any]] = []
    for claim_id in claim_ids:
        claim = claims_by_id.get(claim_id)
        if claim is None:
            raise GroundingCheckError(f"paragraph cites unknown claim_id: {claim_id}")
        evidence: list[dict[str, Any]] = []
        for evidence_step_id in claim.get("evidence_step_ids", []):
            step = evidence_by_id.get(evidence_step_id)
            if step is None:
                continue
            fragment = fragments_by_id.get(step.get("source_fragment_id"))
            evidence.append(
                {
                    "statement": step.get("statement"),
                    "source_excerpt": fragment.get("verbatim_excerpt") if fragment else None,
                }
            )
        entry = {
            "claim_id": claim_id,
            "claim_statement": claim.get("statement"),
            "attribution": claim.get("attribution") or "professor",
            "evidence": evidence,
        }
        # A required argument step carries two different things: what the
        # professor said, and the editorial board's decision about how this
        # article must handle it ("do not reduce the messiah to a title",
        # "derive the principle only from this passage's two-stage
        # structure"). Both are legitimate grounds for the author -- the
        # platform's own position is that the editorial board authors the new
        # work -- but only the first is the professor's assertion. Supplying
        # the instruction as separately-attributed material stops the gate
        # rejecting a paragraph for following the contract, without letting an
        # editorial decision pass as something the professor said.
        instruction = (instructions_by_claim or {}).get(claim_id) or claim.get(
            "editorial_instruction"
        )
        if instruction:
            entry["editorial_instruction"] = {
                "attribution": "editor",
                "statement": instruction,
            }
        material.append(entry)
    return material


def build_grounding_packet(
    paragraph_text: str,
    claim_ids: list[str],
    knowledge: dict[str, Any],
    instructions_by_claim: dict[str, str] | None = None,
) -> dict[str, Any]:
    packet = {
        "schema_version": "matthew-exposition-grounding-packet.v1",
        "paragraph_text": paragraph_text,
        "material": build_paragraph_material(claim_ids, knowledge, instructions_by_claim),
    }
    size = len(canonical_json(packet).encode("utf-8"))
    if size > GROUNDING_PACKET_MAX_BYTES:
        raise GroundingCheckError(
            f"grounding packet exceeds {GROUNDING_PACKET_MAX_BYTES} bytes ({size}); "
            "cite fewer claims or split the paragraph"
        )
    return packet


def validate_grounding_result(
    result: dict[str, Any], *, paragraph_text: str
) -> None:
    validate_strict_schema(result, GROUNDING_RESULT_SCHEMA)
    if result["exceeds_material"] and not result["unsupported_assertions"]:
        raise GroundingCheckError(
            "exceeds_material is true but no unsupported_assertions were given"
        )
    for assertion in result["unsupported_assertions"]:
        if assertion not in paragraph_text:
            raise GroundingCheckError(
                f"unsupported_assertions must quote the paragraph verbatim; not found: {assertion!r}"
            )


def check_paragraph_grounding(
    paragraph_text: str,
    claim_ids: list[str],
    knowledge: dict[str, Any],
    *,
    client: Any,
    instructions_by_claim: dict[str, str] | None = None,
) -> dict[str, Any]:
    packet = build_grounding_packet(
        paragraph_text, claim_ids, knowledge, instructions_by_claim
    )
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    result = client.generate_json(prompt, canonical_json(packet), GROUNDING_RESULT_SCHEMA)
    validate_grounding_result(result, paragraph_text=paragraph_text)
    return result


def section_claim_scope(
    markdown: str, author_sections: list[dict[str, Any]]
) -> list[tuple[int, list[str]]]:
    """Return (start offset, claim ids) per authored section, in document order.

    The CompositionPlan assigns claims to a reader section, not to individual
    paragraphs; the author's ledger declares them at that same level. Grounding
    a paragraph only against the ids it happens to repeat in its own provenance
    comment is therefore stricter than the plan itself, and rejects faithful
    sentences whose material was allotted to the section they sit in.
    """

    boundaries: list[tuple[int, list[str]]] = []
    for section in author_sections:
        anchor = str(section.get("output_anchor") or "")
        offset = markdown.find(anchor) if anchor else -1
        if offset < 0:
            continue
        boundaries.append((offset, list(section.get("claim_ids_used") or [])))
    return sorted(boundaries)


def _scope_for_offset(
    boundaries: list[tuple[int, list[str]]], offset: int
) -> list[str]:
    scope: list[str] = []
    for start, claim_ids in boundaries:
        if start <= offset:
            scope = claim_ids
        else:
            break
    return scope


def check_manuscript_grounding(
    markdown: str,
    knowledge: dict[str, Any],
    *,
    client: Any,
    author_sections: list[dict[str, Any]] | None = None,
    instructions_by_claim: dict[str, str] | None = None,
    checked_attributions: frozenset[str] = frozenset({"professor", "editorial_synthesis"}),
) -> dict[str, Any]:
    """Run the grounding check over every checkable paragraph in a manuscript.

    Returns a report, not a pass/fail verdict -- the caller (an audit stage,
    a CLI, a test) decides what a finding means for its own gate.
    """

    findings: list[dict[str, Any]] = []
    checked = 0
    skipped = 0
    boundaries = section_claim_scope(markdown, author_sections or [])
    for paragraph in extract_provenance_paragraphs(markdown):
        provenance = paragraph["provenance"]
        text = paragraph["paragraph_text"]
        if not text or not isinstance(provenance, dict):
            skipped += 1
            continue
        attribution = provenance.get("attribution")
        declared = provenance.get("claim_ids") or []
        if attribution not in checked_attributions or not declared:
            skipped += 1
            continue
        # The paragraph's own declaration stays the audit record; grounding
        # uses it together with the rest of its section's assigned material.
        section_scope = _scope_for_offset(boundaries, paragraph["comment_offset"])
        claim_ids = list(dict.fromkeys([*declared, *section_scope]))
        checked += 1
        try:
            result = check_paragraph_grounding(
                text, claim_ids, knowledge, client=client,
                instructions_by_claim=instructions_by_claim,
            )
        except GroundingCheckError as exc:
            findings.append(
                {
                    "code": "grounding_check_failed",
                    "attribution": attribution,
                    "claim_ids": claim_ids,
                    "paragraph_excerpt": text[:120],
                    "error": str(exc),
                }
            )
            continue
        if result["exceeds_material"]:
            findings.append(
                {
                    "code": "unsupported_assertion",
                    "attribution": attribution,
                    "claim_ids": claim_ids,
                    "declared_claim_ids": declared,
                    "paragraph_excerpt": text[:120],
                    "unsupported_assertions": result["unsupported_assertions"],
                    "notes": result.get("notes", ""),
                }
            )
    return {
        "schema_version": "matthew-exposition-grounding-report.v1",
        "manuscript_sha256": sha256_text(markdown),
        "paragraphs_checked": checked,
        "paragraphs_skipped": skipped,
        "findings": findings,
        "passed": not findings,
    }
