"""Re-pin viewpoint Claim links a Claim review moved, and only those.

A link pins the Claim revision and the Claim fingerprint it was validated
against, so that a link can never come to describe a Claim that has since
changed.  The fingerprint is ``semantic_record_sha``, which strips only
``revision`` -- review metadata stays inside it.  Reviewing the corpus therefore
invalidates the whole viewpoint layer: on 2026-08-26 an independent AI review
stamped ``review_status``, ``reviewed_by``, ``reviewed_at`` and ``review_note``
on 113 Claims without touching a word any of them says, and all 88 active claim
links failed both checks at once, blocking every write to viewpoints and routes.

Re-pinning is safe here in a way it is not when a viewpoint's wording changes.
There the reviewer has to confirm the link still holds, because what the link
was checked against moved.  Here the Claim's substance is provably identical:
the pinned revision's payload is still in ``object_versions``, and this module
re-pins only when the difference between it and the current payload is confined
to review metadata.  Anything else -- a changed statement, evidence, spans -- is
left alone and reported, because that is a link somebody has to look at.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .viewpoint_foundation import semantic_record_sha

#: Fields a Claim review writes. None of them is part of what the Claim says.
CLAIM_REVIEW_FIELDS = frozenset(
    {"review_status", "reviewed_by", "reviewed_at", "review_note", "revision"}
)


def substantive_difference(
    pinned: Mapping[str, Any], current: Mapping[str, Any]
) -> list[str]:
    """Fields that differ outside review metadata, sorted."""

    keys = (set(pinned) | set(current)) - CLAIM_REVIEW_FIELDS
    return sorted(key for key in keys if pinned.get(key) != current.get(key))


def plan_claim_link_repin(
    *,
    links: Sequence[Mapping[str, Any]],
    claims: Mapping[str, Mapping[str, Any]],
    pinned_payloads: Mapping[tuple[str, int], Mapping[str, Any]],
) -> dict[str, Any]:
    """Decide, per link, whether its pin may move.

    ``pinned_payloads`` maps (claim_id, pinned_revision) to the Claim payload as
    it stood when the link was written -- read from the version history, not
    reconstructed, so the comparison is against what was actually pinned.
    """

    repinned: list[dict[str, Any]] = []
    unchanged: list[str] = []
    needs_review: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    for link in sorted(links, key=lambda item: str(item["viewpoint_claim_link_id"])):
        link_id = str(link["viewpoint_claim_link_id"])
        claim_id = str(link["claim_id"])
        pinned_revision = int(link["pinned_claim_revision"])
        claim = claims.get(claim_id)
        if claim is None:
            missing.append({"viewpoint_claim_link_id": link_id, "claim_id": claim_id})
            continue
        current_revision = int(claim.get("revision", 1))
        if current_revision == pinned_revision:
            unchanged.append(link_id)
            continue
        pinned_payload = pinned_payloads.get((claim_id, pinned_revision))
        if pinned_payload is None:
            missing.append(
                {
                    "viewpoint_claim_link_id": link_id,
                    "claim_id": claim_id,
                    "pinned_claim_revision": pinned_revision,
                    "reason": "pinned revision is not in the version history",
                }
            )
            continue
        differences = substantive_difference(pinned_payload, claim)
        if differences:
            needs_review.append(
                {
                    "viewpoint_claim_link_id": link_id,
                    "claim_id": claim_id,
                    "pinned_claim_revision": pinned_revision,
                    "current_claim_revision": current_revision,
                    "changed_fields": differences,
                }
            )
            continue
        updated = dict(link)
        updated["pinned_claim_revision"] = current_revision
        locator = updated.get("component_locator")
        if locator:
            locator = dict(locator)
            locator["claim_sha256"] = semantic_record_sha(claim)
            updated["component_locator"] = locator
        updated["revision"] = int(link.get("revision", 1)) + 1
        repinned.append(updated)

    return {
        "schema_version": "wang_viewpoint_claim_link_repin_v1",
        "repinned": repinned,
        "unchanged_link_ids": sorted(unchanged),
        "needs_review": needs_review,
        "missing": missing,
    }
