"""What else has to go when a set of source fragments is withdrawn.

Two things withdraw fragments and they arrive at the same question. #102's
cleanup withdraws the ones that quote text a proofreader deleted; a
re-extraction withdraws the ones its predecessor produced. Either way, a
record citing nothing but withdrawn fragments has no footing left, a claim
whose every step went with them has no evidence, and an edge to any of those
is an edge to nothing.

Only the seed differs, so only the seed lives elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

#: Records that reach the source text directly, through `source_fragment_ids`.
ANCHORED_COLLECTIONS = ("evidence_steps", "observations", "questions", "position_nodes")


@dataclass
class Withdrawal:
    """A set of fragments to withdraw, and everything that falls with them."""

    #: fragment_id -> the source it belongs to.
    withdrawn_fragments: dict[str, str] = field(default_factory=dict)
    #: (collection, object_id) -> how many of its anchors are withdrawn, of how many.
    owners: dict[tuple[str, str], tuple[int, int]] = field(default_factory=dict)
    #: Claims that would keep no live evidence step.
    orphaned_claims: list[str] = field(default_factory=list)
    #: (collection, relation_id) whose endpoint this withdrawal removes.
    dangling_relations: list[tuple[str, str]] = field(default_factory=list)
    #: Fragments whose source could not be read, so nothing can be said of them.
    unresolved_fragments: int = 0

    @property
    def orphaned_owners(self) -> list[tuple[str, str]]:
        """Records every one of whose anchors is being withdrawn."""

        return [key for key, (gone, total) in self.owners.items() if gone == total]

    @property
    def weakened_owners(self) -> list[tuple[str, str]]:
        """Records that keep at least one live anchor."""

        return [key for key, (gone, total) in self.owners.items() if gone < total]

    def closure(self) -> list[tuple[str, str]]:
        """Everything the withdrawal has to cover, in dependency order.

        Fragments first, then the records left with no anchor at all, then the
        claims left with no evidence, then the relations whose endpoint has
        just gone. A record that keeps a live anchor is not here: it loses a
        citation, not its footing.
        """

        keys = [("source_fragments", fragment) for fragment in sorted(self.withdrawn_fragments)]
        keys += sorted(self.orphaned_owners)
        keys += [("claims", claim) for claim in sorted(self.orphaned_claims)]
        keys += sorted(self.dangling_relations)
        return keys

    def as_dict(self) -> dict[str, Any]:
        by_source: dict[str, int] = {}
        for source in self.withdrawn_fragments.values():
            by_source[source] = by_source.get(source, 0) + 1
        return {
            "withdrawn_fragments": len(self.withdrawn_fragments),
            "by_source": dict(sorted(by_source.items(), key=lambda row: -row[1])),
            "owners_citing_withdrawn_text": len(self.owners),
            "owners_with_no_live_anchor": len(self.orphaned_owners),
            "owners_weakened_but_standing": len(self.weakened_owners),
            "claims_with_no_live_evidence": len(self.orphaned_claims),
            "relations_left_dangling": len(self.dangling_relations),
            "unresolved_fragments": self.unresolved_fragments,
            "closure": len(self.closure()),
        }


def closure_from_fragments(
    withdrawn: Mapping[str, str],
    *,
    owners: Mapping[str, Mapping[str, Mapping[str, Any]]],
    claims: Mapping[str, Mapping[str, Any]],
    relations: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    unresolved_fragments: int = 0,
) -> Withdrawal:
    """Close a set of withdrawn fragments over everything that depends on them.

    Takes plain mappings rather than a store: what falls with a fragment is a
    property of the records, and nothing else needs a database to decide.
    """

    result = Withdrawal(
        withdrawn_fragments=dict(withdrawn), unresolved_fragments=unresolved_fragments
    )
    for collection in ANCHORED_COLLECTIONS:
        for object_id, payload in (owners.get(collection) or {}).items():
            cited = [str(value) for value in (payload.get("source_fragment_ids") or [])]
            gone = [value for value in cited if value in result.withdrawn_fragments]
            if gone:
                result.owners[(collection, object_id)] = (len(gone), len(cited))

    withdrawn_steps = {
        object_id for collection, object_id in result.orphaned_owners
        if collection == "evidence_steps"
    }
    for claim_id, payload in claims.items():
        steps = [str(value) for value in (payload.get("evidence_step_ids") or [])]
        if steps and all(step in withdrawn_steps for step in steps):
            result.orphaned_claims.append(claim_id)

    # An edge whose endpoint has been withdrawn is not a weaker edge, it is an
    # edge to nothing. Leaving these behind is how a store keeps reporting a
    # relation count that nothing can be traversed.
    going = {object_id for _, object_id in result.closure()}
    for collection, rows in (relations or {}).items():
        for relation_id, payload in rows.items():
            endpoints = {str(payload.get("from_id") or ""), str(payload.get("to_id") or "")}
            if endpoints & going:
                result.dangling_relations.append((collection, relation_id))
    return result
