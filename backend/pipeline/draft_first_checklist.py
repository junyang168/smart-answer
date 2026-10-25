"""The source→draft checklist the author and the reviewer both answer (#439).

Review used to run one way only: every sentence in the draft was checked
against the sources, but nothing started from the professor's teaching and
asked whether it reached the essay. A missing judgement has no sentence to
start from. The checklist is that starting point, built by the program from
the evidence packet so that "did you check everything" is provable: each
item gets a disposition, and a disposition without a verbatim anchor or a
reason is rejected.

Scope (owner, 2026-10-24): CVPs and Claims only — viewpoints, their
conditions, observations, tensions, unresolved items and argument routes.
Application, background and illustrations are the author's choice; verbal
tics, slips, repetition and self-promotion are not teaching. The author and
the reviewer receive the same items and the same rules.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

STATUSES = ("landed", "absent", "distorted")

DISPOSITION_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "item_id": {"type": "string"},
        "status": {"type": "string", "enum": list(STATUSES)},
        "anchor": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["item_id", "status", "anchor", "reason"],
}


def build_checklist(packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every judgement in the packet that the essay must carry, in packet order."""

    items: list[dict[str, Any]] = []
    for focal in packet.get("focal_viewpoints") or []:
        revision = focal["revision"]
        revision_id = str(revision.get("viewpoint_revision_id"))
        signature = revision.get("proposition_signature") or {}
        items.append(
            {
                "item_id": f"V:{revision_id}",
                "kind": "viewpoint",
                "viewpoint_revision_id": revision_id,
                "structure_role": focal.get("structure_role"),
                "modality": signature.get("modality"),
                "text": str(revision.get("core_proposition") or ""),
            }
        )
        # A condition is a fence, not a sentence to write: it lands when the
        # essay states the viewpoint without stepping over it (author rule 4).
        for number, condition in enumerate(signature.get("conditions") or [], 1):
            items.append(
                {
                    "item_id": f"C:{revision_id}:{number}",
                    "kind": "condition",
                    "viewpoint_revision_id": revision_id,
                    "text": str(condition),
                }
            )
    for support in packet.get("observation_supports") or []:
        if support.get("effective_state", "active") != "active":
            continue
        items.append(
            {
                "item_id": f"O:{support['observation_support_id']}",
                "kind": "observation",
                "viewpoint_revision_id": support.get("validated_against_viewpoint_revision_id"),
                "source_id": support.get("source_id"),
                "text": str(support.get("observation_summary") or ""),
                "scope_note": str(support.get("support_scope_note") or ""),
            }
        )
    for relation in packet.get("relations") or []:
        if relation.get("relation_type") != "tensions_with":
            continue
        if relation.get("effective_state", "active") != "active":
            continue
        items.append(
            {
                "item_id": f"T:{relation['viewpoint_relation_id']}",
                "kind": "tension",
                "viewpoint_revision_ids": [
                    relation.get("validated_source_viewpoint_revision_id"),
                    relation.get("validated_target_viewpoint_revision_id"),
                ],
                "text": str(relation.get("reason") or ""),
            }
        )
    structure = (packet.get("structure") or {}).get("revision") or {}
    for number, item in enumerate(structure.get("unresolved_items") or [], 1):
        items.append({"item_id": f"U:{number}", "kind": "unresolved", "text": str(item)})
    for bundle in packet.get("argument_routes") or []:
        revision = bundle.get("revision") or {}
        steps = [
            str(node.get("normalized_proposition") or "")
            for node in revision.get("ordered_inference_nodes") or []
        ]
        items.append(
            {
                "item_id": f"R:{revision.get('argument_route_revision_id')}",
                "kind": "route",
                "viewpoint_revision_id": revision.get("validated_against_conclusion_viewpoint_revision_id"),
                "route_label": revision.get("route_label"),
                "fully_attested": bool(bundle.get("full_attestation_count")),
                "text": " → ".join(steps),
            }
        )
    return items


def rendered_text(manuscript: str) -> str:
    """The text a reader sees: blockquote markers and emphasis stripped.

    Reviewers quote what the reader sees; an anchor inside '> **編者按**：…'
    is verbatim there while failing a raw-byte match.
    """

    rendered = re.sub(r"^>\s?", "", manuscript, flags=re.MULTILINE)
    return rendered.replace("**", "").replace("*", "")


def anchor_in(manuscript: str, anchor: str) -> bool:
    return bool(anchor) and (anchor in manuscript or anchor in rendered_text(manuscript))


def quote_corpus(packet: Mapping[str, Any], checklist: Iterable[Mapping[str, Any]]) -> str:
    """Everything a disputed or removed-teaching quote may come from, verbatim."""

    parts = [str(item.get("content")) for item in packet["source_originals"]["originals"]]
    parts += [str(item.get("text")) for item in checklist]
    return "\n".join(parts)


def quote_found(quote: str, corpus: str, scripture: Mapping[str, Any] | None = None) -> bool:
    """A source quote is the sermons or the checklist word for word, or 和合本."""

    from backend.pipeline.draft_first_scripture import matches_scripture

    quote = quote.strip()
    if not quote:
        return False
    return quote in corpus or (scripture is not None and matches_scripture(quote, scripture))


def disposition_problems(
    checklist: list[Mapping[str, Any]],
    dispositions: list[Mapping[str, Any]],
    manuscript: str,
) -> list[str]:
    """Why a set of dispositions is not an answer to this checklist, if it is not.

    Exactly one disposition per item; landed and distorted point at the
    sentence with a verbatim anchor; absent says why.
    """

    problems: list[str] = []
    expected = [str(item["item_id"]) for item in checklist]
    seen = [str(item.get("item_id")) for item in dispositions]
    missing = sorted(set(expected) - set(seen))
    unknown = sorted(set(seen) - set(expected))
    repeated = sorted({item for item in seen if seen.count(item) > 1})
    if missing:
        problems.append("no disposition for: " + ", ".join(missing))
    if unknown:
        problems.append("disposition for unknown items: " + ", ".join(unknown))
    if repeated:
        problems.append("more than one disposition for: " + ", ".join(repeated))
    for item in dispositions:
        status = item.get("status")
        if status in ("landed", "distorted") and not anchor_in(manuscript, str(item.get("anchor") or "")):
            problems.append(f"{item.get('item_id')}: anchor not verbatim in manuscript: {str(item.get('anchor'))[:60]!r}")
        if status == "absent" and not str(item.get("reason") or "").strip():
            problems.append(f"{item.get('item_id')}: absent without a reason")
    return problems
