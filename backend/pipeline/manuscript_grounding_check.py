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
    evidence_step_fragment_ids,
    sha256_text,
    validate_strict_schema,
)

PROMPT_PATH = Path(__file__).with_name("prompts") / "manuscript_grounding_check.md"

PROVENANCE_COMMENT_RE = re.compile(r"<!--\s*provenance:\s*(\{.*?\})\s*-->", re.S)
FOOTNOTE_DEFINITION_RE = re.compile(r"^\[\^[^\]]+\]:")

#: A guard against a runaway packet, not a model limit -- one check runs
#: against a 1M-token context. At 20_000 it stopped being a guard and became
#: the gate's own failure mode: a paragraph citing seven claims across several
#: sermons carries ~18KB of the professor's transcript, which rule 8e puts
#: there on purpose, and the whole run failed with fifteen paragraphs never
#: checked at all. An oversized packet also raises `GroundingCheckError`, which
#: is not an `unsupported_assertion`, so the repair path cannot run either.
GROUNDING_PACKET_MAX_BYTES = 60_000

#: The reviewer lists the sentences it cannot ground and nothing else. It also
#: used to answer a yes/no `exceeds_material`, which the two fields made it
#: possible to contradict: validation rejected a "yes" with an empty list but
#: not a "no" alongside a full one, and `check_manuscript_grounding` reads only
#: the verdict, so such a paragraph would pass with its quoted sentences
#: unread. That has not been seen happen in a real run -- the field is removed
#: because it is redundant and can disagree with its own evidence, not to fix
#: an observed miss. The verdict is derived in `check_paragraph_grounding`.
GROUNDING_RESULT_SCHEMA: dict[str, Any] = {
    "name": "matthew_exposition_grounding_result_v2",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "unsupported_assertions", "notes"],
        "properties": {
            "schema_version": {
                "type": "string",
                "enum": ["matthew-exposition-grounding-result.v2"],
            },
            "unsupported_assertions": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string"},
        },
    },
}


class GroundingCheckError(RuntimeError):
    """Raised when a paragraph or its packet cannot be checked as given."""


def _paragraph_body(segment: str) -> str:
    """Return only the prose a provenance comment governs."""

    lines: list[str] = []
    for line in segment.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or FOOTNOTE_DEFINITION_RE.match(stripped):
            break
        lines.append(line)
    return "\n".join(lines).strip()


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
        # A provenance comment governs the prose that follows it, but the
        # segment can run on into a footnote definition or the next heading
        # (nothing else marks where the paragraph ends). Those are not the
        # paragraph's assertions: a footnote carries the word form the
        # original-language policy puts there deliberately, and a heading
        # belongs to the section, so checking them against the paragraph's
        # material is meaningless -- and their punctuation (apostrophes in a
        # transliteration such as fron-eh'-o) is what broke the model's JSON.
        text = _paragraph_body(segment)
        try:
            provenance = json.loads(match.group(1))
        except json.JSONDecodeError:
            provenance = None
        paragraphs.append(
            {"provenance": provenance, "paragraph_text": text, "comment_offset": match.start()}
        )
    return paragraphs


def build_paragraph_material(
    claim_ids: list[str],
    knowledge: dict[str, Any],
    instructions_by_claim: dict[str, str] | None = None,
    declared_claim_ids: list[str] | None = None,
    texture_anchors: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return the claim/evidence/source material a paragraph is allowed to use.

    `claim_ids` is the paragraph's declaration widened by its section's scope;
    `declared_claim_ids` is the declaration itself. The two answer different
    questions and need different depth. What the paragraph cites is the audit
    record, and its evidence chain is what separates a supported inference from
    an invented one. The rest of the section's scope only has to answer whether
    an assertion falls inside material this section may draw on at all, which
    the claim statement settles on its own.

    Sending every chain for both put 19KB of evidence behind a 192-byte
    paragraph and pushed the packet past its budget, so the gate could not
    check the paragraph at all. Omit the argument and it stays checkable.
    """

    declared = set(declared_claim_ids) if declared_claim_ids is not None else set(claim_ids)
    claims_by_id = {c.get("claim_id"): c for c in knowledge.get("claims", [])}
    evidence_by_id = {e.get("evidence_step_id"): e for e in knowledge.get("evidence_steps", [])}
    fragments_by_id = {f.get("fragment_id"): f for f in knowledge.get("source_fragments", [])}

    material: list[dict[str, Any]] = []
    for claim_id in claim_ids:
        claim = claims_by_id.get(claim_id)
        if claim is None:
            raise GroundingCheckError(f"paragraph cites unknown claim_id: {claim_id}")
        if claim_id not in declared:
            material.append(
                {
                    "claim_id": claim_id,
                    "claim_statement": claim.get("statement"),
                    "attribution": claim.get("attribution") or "professor",
                    "scope": "section_material_not_cited_by_this_paragraph",
                }
            )
            continue
        evidence: list[dict[str, Any]] = []
        for evidence_step_id in claim.get("evidence_step_ids", []):
            step = evidence_by_id.get(evidence_step_id)
            if step is None:
                continue
            # Every fragment behind the step, not only the first: a step whose
            # reasoning rests on two sentences of the source was being checked
            # against one of them.
            excerpts = [
                excerpt
                for fragment_id in evidence_step_fragment_ids(step)
                if (fragment := fragments_by_id.get(fragment_id))
                and (excerpt := fragment.get("verbatim_excerpt"))
            ]
            evidence.append(
                {
                    "statement": step.get("statement"),
                    "source_excerpt": "\n".join(excerpts) or None,
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
    # A texture anchor licenses what its verbatim excerpt literally says --
    # the professor's own framing, word study, timing, or setting -- without
    # requiring a Claim to exist for it. Claims carry the article's
    # conclusions; these carry how the professor taught them. The anchor was
    # already verified verbatim against the scoped source original by the
    # author-contract validator, and `_verify_texture_anchors` re-checks it
    # here so a revision cannot smuggle in an invented excerpt.
    for anchor in texture_anchors or []:
        material.append(
            {
                "kind": "texture_anchor",
                "attribution": "professor_source_verbatim",
                "source_id": anchor.get("source_id"),
                "source_excerpt": anchor.get("excerpt"),
            }
        )
    return material


def _verify_texture_anchors(
    texture_anchors: list[dict[str, Any]] | None, knowledge: dict[str, Any]
) -> None:
    """Reject any texture anchor whose excerpt is not verbatim in its source.

    The author-contract validator already enforces this, but grounding also
    runs on revision output between contract validations, so the gate cannot
    trust a declaration it has not checked itself.
    """

    if not texture_anchors:
        return
    originals = (knowledge.get("source_originals") or {}).get("originals") or []
    content_by_source = {
        str(item.get("source_id") or ""): str(item.get("content") or "")
        for item in originals
    }
    for anchor in texture_anchors:
        source_id = str(anchor.get("source_id") or "")
        excerpt = str(anchor.get("excerpt") or "")
        content = content_by_source.get(source_id)
        if content is None:
            raise GroundingCheckError(
                f"texture anchor cites a source original outside this packet: {source_id or '<empty>'}"
            )
        if not excerpt or excerpt not in content:
            raise GroundingCheckError(
                f"texture anchor excerpt is not verbatim in {source_id}"
            )


def cited_transcript_segments(
    claim_ids: list[str],
    knowledge: dict[str, Any],
    transcript_texts: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Return the sermon segments behind the fragments these claims cite.

    The author is told (rule 8e) to prefer the professor's own wording, which
    lives in the whole transcript segment rather than in the one sentence an
    editor lifted out of it as `verbatim_excerpt`. Without the segment here,
    the gate judged a faithful verbatim quote against material that does not
    contain it and the repair path then replaced his words with the notes'
    abstract paraphrase -- undoing the instruction it was meant to enforce.

    Scope stays the paragraph's own: only segments a cited fragment already
    points at, which `_sermon_transcript_slices` has already narrowed to the
    segments scoped fragments cite. Nothing outside the paragraph's material
    becomes quotable.
    """

    claims_by_id = {c.get("claim_id"): c for c in knowledge.get("claims", [])}
    evidence_by_id = {e.get("evidence_step_id"): e for e in knowledge.get("evidence_steps", [])}
    fragments_by_id = {f.get("fragment_id"): f for f in knowledge.get("source_fragments", [])}

    segments: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for claim_id in claim_ids:
        claim = claims_by_id.get(claim_id) or {}
        for evidence_step_id in claim.get("evidence_step_ids", []):
            step = evidence_by_id.get(evidence_step_id) or {}
            for fragment_id in evidence_step_fragment_ids(step):
                fragment = fragments_by_id.get(fragment_id)
                if fragment is None:
                    continue
                source_id = str(fragment.get("source_id") or "")
                segment_index = fragment.get("source_segment_index")
                if segment_index is None:
                    continue
                key = (source_id, str(segment_index))
                if key in seen:
                    continue
                text = (transcript_texts.get(source_id) or {}).get(str(segment_index))
                if not text:
                    continue
                seen.add(key)
                segments.append(
                    {
                        "source_id": source_id,
                        "segment_index": str(segment_index),
                        "text": text,
                    }
                )
    return segments


def build_grounding_packet(
    paragraph_text: str,
    claim_ids: list[str],
    knowledge: dict[str, Any],
    instructions_by_claim: dict[str, str] | None = None,
    transcript_texts: dict[str, dict[str, str]] | None = None,
    declared_claim_ids: list[str] | None = None,
    texture_anchors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    declared = declared_claim_ids if declared_claim_ids is not None else claim_ids
    _verify_texture_anchors(texture_anchors, knowledge)
    packet = {
        "schema_version": "matthew-exposition-grounding-packet.v1",
        "paragraph_text": paragraph_text,
        "material": build_paragraph_material(
            claim_ids, knowledge, instructions_by_claim, declared_claim_ids=declared,
            texture_anchors=texture_anchors,
        ),
    }
    # Carried once per segment rather than repeated under every claim that
    # cites it: the same segment backs several claims, and the packet has a
    # hard byte budget.
    if transcript_texts:
        # The paragraph's own declaration, not its section's scope. This
        # function's contract is that nothing outside the paragraph's material
        # becomes quotable; widening the caller's `claim_ids` to the section
        # broke that silently, and pulled 57KB of transcript in behind a
        # 192-byte paragraph.
        segments = cited_transcript_segments(declared, knowledge, transcript_texts)
        if segments:
            packet["professor_transcript_segments"] = segments
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
    for assertion in result["unsupported_assertions"]:
        if assertion not in paragraph_text:
            raise GroundingCheckError(
                f"unsupported_assertions must quote the paragraph verbatim; not found: {assertion!r}"
            )


def _cached_verdict(cache_dir: Path | None, fingerprint: str) -> dict[str, Any] | None:
    if cache_dir is None:
        return None
    path = cache_dir / f"{fingerprint}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _store_verdict(cache_dir: Path | None, fingerprint: str, result: dict[str, Any]) -> None:
    if cache_dir is None:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{fingerprint}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def check_paragraph_grounding(
    paragraph_text: str,
    claim_ids: list[str],
    knowledge: dict[str, Any],
    *,
    client: Any,
    instructions_by_claim: dict[str, str] | None = None,
    transcript_texts: dict[str, dict[str, str]] | None = None,
    declared_claim_ids: list[str] | None = None,
    texture_anchors: list[dict[str, Any]] | None = None,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    packet = build_grounding_packet(
        paragraph_text, claim_ids, knowledge, instructions_by_claim, transcript_texts,
        declared_claim_ids=declared_claim_ids, texture_anchors=texture_anchors,
    )
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    payload = canonical_json(packet)
    # This is the only stage in the pipeline without a generation cache, and it
    # is the one that has to be asked the same question repeatedly: the repair
    # loop re-checks the whole manuscript after rewriting a few paragraphs.
    # These calls are not deterministic -- Sonnet 5 rejects `temperature` and
    # thinks adaptively -- so an untouched paragraph could pass one round and
    # fail the next on a byte-identical packet. Four did. A repair that fixes
    # three paragraphs while re-rolling the verdict on nineteen cannot
    # converge, so the gate never settles no matter how good the prose is.
    #
    # Keying on the packet means an unchanged paragraph keeps the verdict it
    # was given, and a round costs only the paragraphs that actually changed.
    fingerprint = sha256_text(prompt + payload)
    result = _cached_verdict(cache_dir, fingerprint)
    if result is None:
        result = client.generate_json(prompt, payload, GROUNDING_RESULT_SCHEMA)
        validate_grounding_result(result, paragraph_text=paragraph_text)
        _store_verdict(cache_dir, fingerprint, result)
    validate_grounding_result(result, paragraph_text=paragraph_text)
    # Derived, never self-reported: quoting a sentence it cannot ground is the
    # finding, so there is no separate verdict to disagree with it.
    result["exceeds_material"] = bool(result["unsupported_assertions"])
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
    transcript_texts: dict[str, dict[str, str]] | None = None,
    checked_attributions: frozenset[str] = frozenset({"professor", "editorial_synthesis"}),
    cache_dir: Path | None = None,
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
        # A paragraph anchored only to source texture has no claims, but it is
        # still asserting prose: skipping it would leave its sentences the one
        # kind of reader text no gate ever reads. It is checked against its
        # anchored excerpts (plus its section's claim scope) like any other.
        texture_anchors = [
            anchor
            for anchor in provenance.get("texture_anchors") or []
            if isinstance(anchor, dict)
        ]
        if attribution not in checked_attributions or not (declared or texture_anchors):
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
                transcript_texts=transcript_texts,
                declared_claim_ids=list(dict.fromkeys(declared)),
                texture_anchors=texture_anchors,
                cache_dir=cache_dir,
            )
        except (GroundingCheckError, ValueError, RuntimeError) as exc:
            # A single paragraph's call failing must not end the run: the
            # report is the deliverable, and a malformed-JSON (ValueError) or
            # transport (RuntimeError) error on one paragraph should surface
            # as a finding to look at, not as a traceback that discards the
            # other paragraphs' results. It still fails the gate, because an
            # unchecked paragraph is not an approved one. AssertionError and
            # the like are deliberately left to propagate: those are bugs,
            # not conditions to report.
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
