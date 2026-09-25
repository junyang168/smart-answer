"""Copy a study's built files to the fellowship folder, where the website serves them.

    python -m backend.bible_study.publish <study folder> [--overwrite]

Copies everything in `<folder>/out/` except bookkeeping to
`$DATA_BASE_DIR/fellowship/docs/<date>/` (OneDrive `团契/<date>/`, public).
Stops, copying nothing, when:

- PowerPoint has one of the target decks open. On 2026-09-25 the owner had an
  older copy open and read its page numbers against a newer cross-reference.
- A target file changed since this command last published it. On 2026-09-25
  the owner edited both published decks in PowerPoint (a chapter label, a
  removed line); a rebuild copied over them would silently undo the edits.
  Fold the edits into slides.json or the generator first, then `--overwrite`.

The SHA-256 of every file published is kept in `<folder>/out/published.json`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from backend.bible_study.paths import data_base_dir

BOOKKEEPING = {"verses.json", "published.json"}
POWERPOINT = "Microsoft PowerPoint"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def open_in_powerpoint() -> list[str] | None:
    """Names of the presentations PowerPoint has open; None if it could not be asked."""

    running = subprocess.run(["pgrep", "-x", POWERPOINT], capture_output=True)
    if running.returncode != 0:
        return []
    try:
        result = subprocess.run(
            ["osascript", "-e", f'tell application "{POWERPOINT}" to get name of every presentation'],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return [name.strip() for name in result.stdout.strip().split(", ") if name.strip()]


def plan(folder: Path, target: Path) -> tuple[list[Path], list[str]]:
    """Files to copy, and the targets that were changed since the last publish."""

    out = folder / "out"
    files = sorted(p for p in out.iterdir() if p.is_file() and p.name not in BOOKKEEPING)
    record_path = out / "published.json"
    record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
    changed = []
    for source in files:
        dest = target / source.name
        if not dest.exists():
            continue
        current = sha256(dest)
        if current == sha256(source):
            continue
        if record.get(source.name) != current:
            changed.append(source.name)
    return files, changed


def publish(
    folder: Path,
    *,
    overwrite: bool = False,
    target: Path | None = None,
    presentations=open_in_powerpoint,
) -> list[str]:
    date = folder.name[:10]
    target = target or data_base_dir() / "fellowship" / "docs" / date
    files, changed = plan(folder, target)
    if not files:
        raise RuntimeError(f"nothing in {folder / 'out'}; run tools/bible-study-ppt/build.js first")
    decks = {f.name for f in files if f.suffix == ".pptx"}
    names = presentations()
    if names is None:
        raise RuntimeError("PowerPoint is running but did not answer; close the deck (don't save) and try again")
    if decks & set(names):
        raise RuntimeError(f"PowerPoint has {', '.join(sorted(decks & set(names)))} open: close it without saving first")
    if changed and not overwrite:
        raise RuntimeError(
            "changed since the last publish (the owner may have edited them): "
            + ", ".join(changed)
            + ". Fold those edits into slides.json or the generator, rebuild, then --overwrite."
        )
    target.mkdir(parents=True, exist_ok=True)
    record = {}
    for source in files:
        shutil.copy2(source, target / source.name)
        record[source.name] = sha256(source)
    (folder / "out" / "published.json").write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return [str(target / f.name) for f in files]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder")
    parser.add_argument("--overwrite", action="store_true", help="replace targets changed since the last publish")
    args = parser.parse_args(argv)
    try:
        for path in publish(Path(args.folder).resolve(), overwrite=args.overwrite):
            print(path)
    except RuntimeError as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
