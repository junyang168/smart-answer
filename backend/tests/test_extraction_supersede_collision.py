"""Re-extracting a source that was already sectioned, twice.

The module's founding measurement — "0 of 185 and 0 of 317 incoming fragment
ids already existed" — was taken on a *first* sectioned re-extraction, where
the predecessor was a whole-document package. Its ids were `CL007` and the new
ones `P01-CL007`, so the two generations could not collide.

They collide from the second sectioned re-extraction onward: same sections,
same numbering. The first source to reach that point shared 82 of its 93
evidence-step ids with its predecessor, and the ingest aborted with
`ChangeSetConflict` on `DK-3d012c24a542-P01-E012` — a record the arrival wrote
and the retirement then expected to find unchanged.
"""

from __future__ import annotations

from backend.pipeline.extraction_supersede import arriving_keys, superseded


def _package(fragment_ids, evidence_ids, claim_ids=("CL001",)):
    return {
        "source_documents": [{"source_id": "SRC-A"}],
        "source_fragments": [{"fragment_id": f, "source_id": "SRC-A"} for f in fragment_ids],
        "evidence_steps": [
            {"evidence_step_id": e, "source_fragment_ids": [fragment_ids[0]]}
            for e in evidence_ids
        ],
        "claims": [{"claim_id": c, "evidence_step_ids": list(evidence_ids)} for c in claim_ids],
    }


def _live(package):
    """The store, holding exactly what this package would put there."""

    fragments = {
        row["fragment_id"]: {"source_id": "SRC-A"} for row in package["source_fragments"]
    }
    owners = {
        "evidence_steps": {
            row["evidence_step_id"]: {"source_fragment_ids": row["source_fragment_ids"]}
            for row in package["evidence_steps"]
        }
    }
    claims = {
        row["claim_id"]: {"evidence_step_ids": row["evidence_step_ids"]}
        for row in package["claims"]
    }
    return fragments, owners, claims


def test_a_record_the_new_package_carries_is_never_also_retired() -> None:
    previous = _package(["FR-1", "FR-2"], ["P01-E012", "P01-E013"])
    fragments, owners, claims = _live(previous)

    # The second sectioned extraction: new fragment ids, but the same evidence
    # step ids, which is what the section numbering guarantees.
    incoming = _package(["FR-9", "FR-8"], ["P01-E012", "P01-E013"])

    withdrawal = superseded(
        incoming, live_fragments=fragments, owners=owners, claims=claims, relations={}
    )

    retired = set(withdrawal.closure())
    written = arriving_keys(incoming)
    assert not (retired & written), f"arrives and is withdrawn in one change set: {retired & written}"

    # The predecessor's fragments still go: the new package does not carry them.
    assert ("source_fragments", "FR-1") in retired
    assert ("source_fragments", "FR-2") in retired


def test_a_record_the_new_package_drops_is_still_retired() -> None:
    """The exclusion must not become an excuse to leave dead records live."""

    previous = _package(["FR-1"], ["P01-E012", "P01-E099"])
    fragments, owners, claims = _live(previous)

    # `P01-E099` is gone from this generation, and nothing anchors it any more.
    incoming = _package(["FR-9"], ["P01-E012"])

    withdrawal = superseded(
        incoming, live_fragments=fragments, owners=owners, claims=claims, relations={}
    )
    retired = set(withdrawal.closure())

    assert ("evidence_steps", "P01-E099") in retired
    assert ("evidence_steps", "P01-E012") not in retired


def test_the_first_sectioned_re_extraction_still_behaves_as_it_did() -> None:
    """Whole-document predecessor, sectioned arrival: no shared ids, nothing changes."""

    previous = _package(["FR-1"], ["E012"])
    fragments, owners, claims = _live(previous)
    incoming = _package(["FR-9"], ["P01-E012"])

    retired = set(
        superseded(
            incoming, live_fragments=fragments, owners=owners, claims=claims, relations={}
        ).closure()
    )
    assert ("evidence_steps", "E012") in retired
