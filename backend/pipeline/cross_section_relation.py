"""Recover the argument links that no single extraction window could see.

Section extraction (#88) asks about one `##` section at a time, so a relation
whose two ends sit in different sections is one no call could see. Measured on
the 太16:21–23 母本, that is a small but real set: 0 of 264 relations extraction
produced cross a `##`, while the whole-document pass produced 7 that span 11–21
segments -- every one of them the editorial pattern the notes prompt warns
about, the fact filed under 釋經 and the inference under 神學意義.

    span 16  可8:27-33 彼得宣认后耶稣立刻预告受苦
          →  门徒缺少的是对弥赛亚性质的认识
    span 21  马可反复呈现的四项现象
          →  太16:20 的保密命令要放在事工处境中解释

Sectioning trades those for a rise in local coverage from 50% to 100%. This
stage buys them back, and it can be cheap because it does not re-read the source: by the
time it runs, every record is a statement with a known position, so the question
is 289 short statements wide instead of a whole manuscript.

Two properties keep it from becoming a second extraction:

  * It may only propose relations between records already in the package. It
    cannot invent a record, and it is never shown the source text, so it has
    nothing to quote and no way to add material.
  * It may only relate records in *different* sections. A relation inside one
    section is the extraction's to make -- it had both ends in front of it and
    the anchors to prove them -- and re-proposing it here would let a model
    holding no anchors relitigate one that does.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from backend.pipeline.knowledge_package import live_claim_ids, live_claims
from backend.pipeline.relation_id_namespace import namespaced_relation_id, package_source_key

PROMPT_PATH = Path(__file__).with_name("prompts") / "cross_section_relation_discovery.md"

# v2: retired duplicates are no longer relatable records. The version is part
# of `discovery_identity`, so a package processed under v1 is re-proposed
# instead of served from a cache built when a merged-away claim was a valid
# endpoint.
# ID globalization is a deterministic representation change, not a semantic
# discovery change. Keep the v2 identity stable so already-reviewed v2 output
# remains a valid model cache; merge/ingest globalizes its IDs mechanically.
# New model responses are globalized below before they leave this stage.
SCHEMA_VERSION = "wang_cross_section_relation_v2"

#: The same vocabulary the extraction uses. This stage adds edges to an existing
#: graph; it does not get its own dialect.
RELATION_TYPES = ["supports", "answers", "qualifies", "applies", "refutes", "contextualizes"]


class CrossSectionValidationError(ValueError):
    """Raised when a proposal cannot be accepted without weakening the graph."""


DISCOVERY_SCHEMA: dict[str, Any] = {
    "name": SCHEMA_VERSION,
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["evidence_relations", "claim_relations"],
        "properties": {
            "evidence_relations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["relation_id", "from_id", "to_id", "relation_type", "reason"],
                    "properties": {
                        "relation_id": {"type": "string"},
                        "from_id": {"type": "string"},
                        "to_id": {"type": "string"},
                        "relation_type": {"type": "string", "enum": RELATION_TYPES},
                        "reason": {"type": "string"},
                    },
                },
            },
            "claim_relations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["claim_relation_id", "from_id", "to_id", "relation_type", "reason"],
                    "properties": {
                        "claim_relation_id": {"type": "string"},
                        "from_id": {"type": "string"},
                        "to_id": {"type": "string"},
                        "relation_type": {"type": "string", "enum": RELATION_TYPES},
                        "reason": {"type": "string"},
                    },
                },
            },
        },
    },
}


def record_positions(package: dict[str, Any]) -> dict[str, int]:
    """Where each anchored record sits, as a segment position.

    Read off `paragraph_key`, which extraction validated against the source
    before writing the fragment. Nothing here re-reads the source: this stage
    reasons over statements, never over text it could quote.
    """

    fragment_position = {}
    for fragment in package.get("source_fragments") or []:
        key = str(fragment.get("paragraph_key") or "")
        if key[1:].isdigit():
            fragment_position[str(fragment.get("fragment_id"))] = int(key[1:]) - 1
    positions: dict[str, int] = {}
    for collection in ("observations", "evidence_steps", "questions", "position_nodes"):
        for record in package.get(collection) or []:
            record_id = next((value for key, value in record.items() if key.endswith("_id")), None)
            spots = [
                fragment_position[fragment_id]
                for fragment_id in record.get("source_fragment_ids") or []
                if fragment_id in fragment_position
            ]
            if record_id and spots:
                positions[str(record_id)] = min(spots)
    # A claim has no anchors of its own; it reaches the text through its steps.
    step_position = {
        str(step.get("evidence_step_id")): positions[str(step.get("evidence_step_id"))]
        for step in package.get("evidence_steps") or []
        if str(step.get("evidence_step_id")) in positions
    }
    for claim in live_claims(package):
        spots = [step_position[value] for value in claim.get("evidence_step_ids") or [] if value in step_position]
        if spots:
            positions[str(claim.get("claim_id"))] = min(spots)
    return positions


def existing_edges(package: dict[str, Any]) -> set[tuple[str, str]]:
    """Undirected pairs already related, so this stage never restates one."""

    edges: set[tuple[str, str]] = set()
    for collection in ("knowledge_relations", "claim_relations"):
        for relation in package.get(collection) or []:
            first, second = str(relation.get("from_id")), str(relation.get("to_id"))
            edges.add((first, second))
            edges.add((second, first))
    return edges


def build_catalogue(
    package: dict[str, Any], positions: dict[str, int]
) -> list[dict[str, Any]]:
    """The records this stage may relate, as statements with positions."""

    rows: list[dict[str, Any]] = []
    for collection, id_key, kind in (
        ("observations", "observation_id", "observation"),
        ("evidence_steps", "evidence_step_id", "evidence_step"),
        ("claims", "claim_id", "claim"),
    ):
        for record in package.get(collection) or []:
            record_id = str(record.get(id_key) or "")
            if record_id not in positions:
                continue
            rows.append({
                "id": record_id,
                "kind": kind,
                "segment": positions[record_id] + 1,
                "statement": str(record.get("statement") or record.get("title") or ""),
            })
    return sorted(rows, key=lambda row: (row["segment"], row["id"]))


def validate_proposals(
    response: dict[str, Any],
    package: dict[str, Any],
    *,
    positions: dict[str, int],
    boundaries: Sequence[int],
) -> None:
    """Reject anything that adds material, restates an edge, or stays in one section.

    `boundaries` is the start position of each `##` section, from the package's
    own `section_plan` -- so widening or narrowing the sections automatically
    changes what this stage may propose, with no second place to remember.

    Errors are collected rather than raised one at a time so a retry can fix
    the whole batch, matching how extraction validation reports.
    """

    evidence_ids = {str(row.get("evidence_step_id")) for row in package.get("evidence_steps") or []}
    observation_ids = {str(row.get("observation_id")) for row in package.get("observations") or []}
    claim_ids = live_claim_ids(package)
    edges = existing_edges(package)
    errors: list[str] = []
    seen: set[tuple[str, str, str]] = set()

    def check(row: dict[str, Any], label: str, allowed_from: set[str], allowed_to: set[str]) -> None:
        from_id, to_id = str(row["from_id"]), str(row["to_id"])
        if from_id not in allowed_from:
            errors.append(f"{label}: {from_id} is not a record this stage may relate from")
            return
        if to_id not in allowed_to:
            errors.append(f"{label}: {to_id} is not a record this stage may relate to")
            return
        if from_id == to_id:
            errors.append(f"{label}: relates a record to itself")
            return
        if (from_id, to_id) in edges:
            errors.append(f"{label}: {from_id}->{to_id} is already related")
            return
        signature = (from_id, to_id, str(row["relation_type"]))
        if signature in seen:
            errors.append(f"{label}: duplicate proposal")
            return
        seen.add(signature)
        if _section_of(positions.get(from_id, 0), boundaries) == _section_of(
            positions.get(to_id, 0), boundaries
        ):
            errors.append(
                f"{label}: both ends are in the same section, which extraction "
                f"could already see"
            )
        if not str(row.get("reason") or "").strip():
            errors.append(f"{label}: no reason given")

    for row in response.get("evidence_relations") or []:
        # Same rule as extraction: an observation may reason into a step, and a
        # step into a step, but nothing supports an observation.
        check(row, str(row.get("relation_id") or "?"), observation_ids | evidence_ids, evidence_ids)
    for row in response.get("claim_relations") or []:
        check(row, str(row.get("claim_relation_id") or "?"), claim_ids, claim_ids)
    if errors:
        raise CrossSectionValidationError("cross-window validation failed: " + " | ".join(errors))


def _section_of(position: int, boundaries: Sequence[int]) -> int:
    return sum(1 for start in boundaries if start <= position)


def apply_proposals(
    package: dict[str, Any], response: dict[str, Any], *, identity: dict[str, Any]
) -> dict[str, Any]:
    """Add the accepted relations, labelled with where they came from.

    A relation nobody can trace back to the stage that proposed it is a
    relation nobody can withdraw, so every added edge carries its origin.
    """

    updated = json.loads(json.dumps(package, ensure_ascii=False))
    try:
        source_key = package_source_key(updated)
    except ValueError as exc:
        raise CrossSectionValidationError(str(exc)) from exc

    for row in response.get("evidence_relations") or []:
        updated.setdefault("knowledge_relations", []).append({
            **row,
            "relation_id": namespaced_relation_id(source_key, row.get("relation_id")),
            "discovered_by": SCHEMA_VERSION,
            "review_status": "candidate",
        })
    for row in response.get("claim_relations") or []:
        updated.setdefault("claim_relations", []).append({
            **row,
            "claim_relation_id": namespaced_relation_id(
                source_key, row.get("claim_relation_id")
            ),
            "discovered_by": SCHEMA_VERSION,
            "review_status": "candidate",
        })
    summary = updated.setdefault("summary", {})
    summary["evidence_relation_count"] = len(updated.get("knowledge_relations") or [])
    summary["claim_relation_count"] = len(updated.get("claim_relations") or [])
    updated["cross_section_relations"] = {
        **identity,
        "evidence_relations_added": len(response.get("evidence_relations") or []),
        "claim_relations_added": len(response.get("claim_relations") or []),
    }
    return updated


def discovery_identity(
    *, package_sha256: str, prompt: str, model_id: str, section_count: int,
    backend: str = "api",
) -> dict[str, Any]:
    generation = {
        "package_sha256": package_sha256,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "model_id": model_id,
        "section_count": section_count,
        "schema_version": SCHEMA_VERSION,
    }
    # Keep the historical API fingerprint stable while making a subscription
    # generation distinct from a separately billed API generation.
    if backend != "api":
        generation["backend"] = backend
    generation["fingerprint_sha256"] = hashlib.sha256(
        json.dumps(generation, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return generation


def render_catalogue(
    rows: Sequence[dict[str, Any]], section_of: dict[str, int] | None = None
) -> str:
    """The catalogue, each row labelled with the section it belongs to.

    The section number is what the proposer has to reason about -- it may only
    relate across one -- so it is shown rather than left to be inferred from
    段号.
    """

    sections = section_of or {}
    return "\n".join(
        f"[{row['id']}] 第{sections.get(row['id'], 0)}节 段{row['segment']:04d} "
        f"{row['kind']}：{row['statement']}"
        for row in rows
    )
