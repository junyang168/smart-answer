"""Fail-closed proofs for carrying a legacy Claim review over locator repairs.

These helpers are read-only.  They do not infer approval from a file's presence,
and they deliberately reject Claim/EvidenceStep relation changes.  A caller
must separately validate the sealed review chain, current source bytes, and
the exact current graph before creating a status-only ChangeSet.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from backend.api.canonical_repository.postgres_store import (
    _substantive_payload,
    sha256_json,
)
from backend.pipeline.source_contract_cleanup import (
    BodyLocatorIndex,
    assert_claim_semantics_unchanged,
    changed_paths,
)


_COORDINATE_PATH = re.compile(
    r"occurrences\[\d+\]\.anchors\[\d+\]\.paragraph_key"
)
_FRAGMENT_COORDINATE_FIELDS = frozenset({
    "paragraph_key", "source_segment_index", "source_sha256",
    "paragraph_text_sha256",
})


def _without_nulls(value: Any) -> Any:
    """Treat an omitted optional package field like a stored null, never text."""

    if isinstance(value, dict):
        return {key: _without_nulls(child) for key, child in value.items()
                if child is not None}
    if isinstance(value, list):
        return [_without_nulls(child) for child in value]
    return value


def coordinate_chain_proof(
    reviewed_claim: Mapping[str, Any],
    versions: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Prove that a reviewed Claim reached current only by #368 locator edits.

    Each version must have revision, content_sha256, source_kind, and payload.
    An exact historical payload match is required.  No relation or wording
    change is normalized away, even if a later version reverses it.
    """

    if not versions:
        return None
    ordered = sorted(versions, key=lambda row: int(row["revision"]))
    if len({int(row["revision"]) for row in ordered}) != len(ordered):
        return None
    reviewed = _substantive_payload(reviewed_claim)
    matches = [index for index, row in enumerate(ordered)
               if reviewed == _substantive_payload(row["payload"])]
    if not matches:
        return None
    start = matches[-1]
    if start == len(ordered) - 1:
        return None  # Ordinary exact-payload reconciliation owns that case.
    chain = ordered[start:]
    changed = False
    for before, after in zip(chain, chain[1:]):
        if (
            int(after["revision"]) != int(before["revision"]) + 1
            or after["source_kind"] != "wkp368_source_contract_cleanup"
        ):
            return None
        try:
            assert_claim_semantics_unchanged(
                _substantive_payload(before["payload"]),
                _substantive_payload(after["payload"]),
            )
        except ValueError:
            return None
        paths = changed_paths(before["payload"], after["payload"])
        paths = [path for path in paths if path != "revision"]
        if not paths or any(_COORDINATE_PATH.fullmatch(path) is None for path in paths):
            return None
        changed = True
    if not changed:
        return None
    return {
        "reviewed_claim_revision": int(chain[0]["revision"]),
        "reviewed_claim_content_sha256": str(chain[0]["content_sha256"]),
        "current_claim_revision": int(chain[-1]["revision"]),
        "current_claim_content_sha256": str(chain[-1]["content_sha256"]),
        "coordinate_chain_sha256": sha256_json([
            {"revision": int(row["revision"]),
             "content_sha256": str(row["content_sha256"]),
             "source_kind": str(row["source_kind"])}
            for row in chain
        ]),
    }


def exact_current_anchor_count(
    claim: Mapping[str, Any], *, source_id: str, transcript_id: str,
    index: BodyLocatorIndex,
) -> int | None:
    """Count current Claim anchors only if every excerpt binds exactly."""

    count = 0
    matched_occurrence = False
    for occurrence in claim.get("occurrences") or []:
        if not isinstance(occurrence, Mapping):
            return None
        if (
            str(occurrence.get("source_id") or "") != source_id
            and str(occurrence.get("transcript_id") or "") != transcript_id
        ):
            continue
        matched_occurrence = True
        for anchor in occurrence.get("anchors") or []:
            if not isinstance(anchor, Mapping):
                return None
            locator = str(anchor.get("paragraph_key") or "")
            excerpt = str((anchor.get("proposed_highlight") or {}).get("text") or "")
            row = index.by_locator.get(locator)
            if row is None or not excerpt or excerpt not in row.text:
                return None
            count += 1
    return count if matched_occurrence and count else None


def related_package_content_unchanged(
    claim_id: str,
    reviewed_claim: Mapping[str, Any],
    package_evidence: Mapping[str, Mapping[str, Any]],
    package_fragments: Mapping[str, Mapping[str, Any]],
    current_evidence: Mapping[str, Mapping[str, Any]],
    current_fragments: Mapping[str, Mapping[str, Any]],
) -> bool:
    """Reject graph changes; permit only verified #368 fragment coordinates.

    The current fragment locator must additionally be checked against current
    source bytes by the caller.  Comparing excerpts here is not enough.
    """

    evidence_ids = reviewed_claim.get("evidence_step_ids") or []
    if not evidence_ids or len(evidence_ids) != len(set(evidence_ids)):
        return False
    for evidence_id in evidence_ids:
        before = package_evidence.get(evidence_id)
        after = current_evidence.get(evidence_id)
        if before is None or after is None:
            return False
        if claim_id not in (after.get("produced_claim_ids") or []):
            return False
        if _substantive_payload(before) != _substantive_payload(after):
            return False
        fragment_ids = list(after.get("source_fragment_ids") or [])
        if after.get("source_fragment_id"):
            fragment_ids.append(after["source_fragment_id"])
        if not fragment_ids:
            return False
        for fragment_id in fragment_ids:
            old_fragment = package_fragments.get(fragment_id)
            new_fragment = current_fragments.get(fragment_id)
            if old_fragment is None or new_fragment is None:
                return False
            old = _substantive_payload(old_fragment)
            new = _substantive_payload(new_fragment)
            for field in _FRAGMENT_COORDINATE_FIELDS:
                old.pop(field, None)
                new.pop(field, None)
            if old.get("visual_facts") in (None, []):
                old.pop("visual_facts", None)
            if new.get("visual_facts") in (None, []):
                new.pop("visual_facts", None)
            if (
                _without_nulls(old) != _without_nulls(new)
                or not str(new_fragment.get("verbatim_excerpt") or "")
            ):
                return False
    return True
