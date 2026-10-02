"""Filesystem layout for reference commentaries (WKP-E11).

Carson and any later commentary live beside the Wang platform, never inside
it: nothing here is a Wang claim, viewpoint, or source. See
docs/wang-knowledge-platform/60-reference-commentary/reference_commentary_solution_v1.md.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


REFERENCE_COMMENTARY_DIRNAME = "reference-commentary"
DEFAULT_INBOX = "~/Library/Mobile Documents/com~apple~CloudDocs/Carson"


@dataclass(frozen=True)
class ReferenceCommentaryPaths:
    root: Path
    inbox: Path
    inbox_ledger: Path
    lock: Path

    def volume(self, volume_id: str) -> Path:
        return self.root / volume_id


def reference_commentary_paths(
    data_base_dir: str | Path | None = None,
    inbox: str | Path | None = None,
) -> ReferenceCommentaryPaths:
    """Resolve the layout without creating any directories."""

    value = data_base_dir if data_base_dir is not None else os.getenv("DATA_BASE_DIR")
    if not value:
        raise RuntimeError("DATA_BASE_DIR is required")
    root = (Path(value).expanduser().resolve() / REFERENCE_COMMENTARY_DIRNAME).resolve()
    inbox_value = inbox if inbox is not None else os.getenv("REFERENCE_COMMENTARY_INBOX", DEFAULT_INBOX)
    return ReferenceCommentaryPaths(
        root=root,
        inbox=Path(inbox_value).expanduser(),
        inbox_ledger=root / "inbox-ledger.json",
        lock=root / ".inbox.lock",
    )
