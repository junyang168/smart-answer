from __future__ import annotations

import argparse
from pathlib import Path

from api.config import ARTICLES_DIR
from api.scripture import convert_parenthetical_references


def _gather_targets(files: list[str] | None) -> list[Path]:
    if files:
        targets: list[Path] = []
        for item in files:
            candidate = Path(item)
            if not candidate.is_absolute():
                candidate = ARTICLES_DIR / candidate
            if candidate.exists() and candidate.is_file():
                targets.append(candidate)
            else:
                print(f"Skipping missing file: {candidate}")
        return targets
    return sorted(ARTICLES_DIR.glob("*.md"))


def _convert_file(path: Path, dry_run: bool) -> bool:
    original = path.read_text(encoding="utf-8")
    updated = convert_parenthetical_references(original)
    if updated == original:
        return False
    if dry_run:
        print(f"[DRY RUN] Would update {path}")
        return True
    path.write_text(updated, encoding="utf-8")
    print(f"Updated {path}")
    return True


def main() -> None:

    targets = _gather_targets(['article-a15910ad.md'])
    if not targets:
        print("No markdown files found.")
        return

    updated_count = 0
    for path in targets:
        if _convert_file(path, False):
            updated_count += 1



if __name__ == "__main__":
    main()
