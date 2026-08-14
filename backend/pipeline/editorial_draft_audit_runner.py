"""CLI for deterministic editorial draft audits."""

from __future__ import annotations

import argparse
from pathlib import Path

from backend.pipeline.editorial_draft_audit import write_editorial_draft_audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit an editorial manuscript draft.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--draft-id", required=True)
    args = parser.parse_args()
    output_path = write_editorial_draft_audit(args.manifest, args.draft_id)
    print(output_path)


if __name__ == "__main__":
    main()
