"""Reading a knowledge package the way a merge left it.

A claim retired by an accepted duplicate finding stays in the package: it is
the record that the merge happened, and its anchors have already moved to the
claim that survived.  Every reader that asks "what does this package claim"
therefore has to skip it, or the duplicate the pipeline just resolved comes
back at the next stage under a different name.
"""

from __future__ import annotations

from typing import Any


def live_claims(package: dict[str, Any]) -> list[dict[str, Any]]:
    """The claims a package still asserts -- retired duplicates excluded."""

    return [row for row in package.get("claims") or [] if not row.get("superseded_by")]


def live_claim_ids(package: dict[str, Any]) -> set[str]:
    return {str(row.get("claim_id")) for row in live_claims(package)}
