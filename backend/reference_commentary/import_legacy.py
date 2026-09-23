"""Bring pages transcribed before the inbox existed into the page store (design §7).

Two batches exist: Matthew 18 from July (one `chapter18.md` with
`<!-- Page N -->` markers, twelve HEICs) and the Matthew 20 pilot of
2026-09-23 (one `.md` per HEIC). Photos are matched to pages by the printed
page number, never by file order: the July photos were taken out of order.
The existing text is kept; Gemini is only asked for the page number when a
photo has no text file of its own that names it.

    python -m backend.reference_commentary.import_legacy \\
        --folder Matthew/ch18 --images DIR --pages-md chapter18.md [--dry-run]
    python -m backend.reference_commentary.import_legacy \\
        --folder Matthew/ch20 --images DIR --per-image-md DIR [--dry-run]
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import Callable

from backend.config.reference_commentary_paths import reference_commentary_paths
from backend.reference_commentary.images import PHOTO_SUFFIXES, photo_to_jpeg
from backend.reference_commentary.ocr import PAGE_NUMBER_PROMPT, _call_with_retry, _vertex_call, parse_output
from backend.reference_commentary.store import VolumeStore, normalize_page, sha256_bytes
from backend.reference_commentary.volumes import resolve_folder

_MARK = re.compile(r"<!--\s*Page\s+([^\s>]+)\s*-->", re.IGNORECASE)


def split_by_page_marks(markdown: str) -> dict[str, str]:
    """`<!-- Page 396 --> ... <!-- Page 397 --> ...` -> {"396": ..., "397": ...}."""

    parts = _MARK.split(markdown)
    return {normalize_page(parts[i]): parts[i + 1].strip() for i in range(1, len(parts), 2)}


def ask_page_number(jpeg: bytes) -> str | None:
    import time

    answer = _call_with_retry(_vertex_call, jpeg, PAGE_NUMBER_PROMPT, 5, time.sleep).strip().strip(".")
    try:
        return normalize_page(answer)
    except ValueError:
        return None


def plan(
    images: list[Path],
    texts: dict[str, str],
    per_image: dict[str, str],
    page_of: Callable[[bytes], str | None],
) -> tuple[list[tuple[Path, bytes, str]], list[str]]:
    """Match each photo to a page: [(photo, jpeg, page)], problems."""

    matched: list[tuple[Path, bytes, str]] = []
    problems: list[str] = []
    for photo in images:
        jpeg = photo_to_jpeg(photo)
        page = per_image.get(photo.stem) or page_of(jpeg)
        if page is None:
            problems.append(f"{photo.name}: page number not readable; not imported")
            continue
        if page not in texts:
            problems.append(f"{photo.name}: page {page} has no transcription; not imported")
            continue
        matched.append((photo, jpeg, page))
    seen: dict[str, str] = {}
    for photo, _, page in matched:
        if page in seen:
            problems.append(f"{photo.name} and {seen[page]} both read as page {page}")
        seen[page] = photo.name
    for page in texts:
        if page not in seen:
            problems.append(f"page {page} has text but no matching photo")
    return matched, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--folder", required=True, help="inbox-style folder, e.g. Matthew/ch18")
    parser.add_argument("--images", required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pages-md", type=Path, help="one file with <!-- Page N --> markers")
    source.add_argument("--per-image-md", type=Path, help="directory of <photo stem>.md files")
    parser.add_argument(
        "--assign",
        action="append",
        default=[],
        metavar="STEM=PAGE",
        help="page of a photo whose number is not printed in frame, matched by hand (e.g. IMG_2043=396)",
    )
    parser.add_argument("--assign-note", default="matched by hand", help="how --assign pages were decided")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    book, chapter_folder = args.folder.split("/")
    volume, chapter = resolve_folder(book, chapter_folder)
    images = sorted(p for p in args.images.iterdir() if p.suffix.lower() in PHOTO_SUFFIXES)

    per_image: dict[str, str] = {}
    if args.pages_md:
        texts = split_by_page_marks(args.pages_md.read_text(encoding="utf-8"))
    else:
        texts = {}
        for md in sorted(args.per_image_md.glob("*.md")):
            page = parse_output(md.read_text(encoding="utf-8"))
            if page.printed_page and md.stem in {p.stem for p in images}:
                texts[page.printed_page] = page.text
                per_image[md.stem] = page.printed_page
        # Only the photos that have a transcription: the folder may hold
        # anything else (the pilot sat in ~/Downloads).
        images = [p for p in images if p.stem in per_image]

    assigned: dict[str, str] = {}
    for item in args.assign:
        stem, _, page = item.partition("=")
        assigned[stem] = normalize_page(page)
    per_image.update(assigned)

    # Every photo without its own text file is sent to Gemini for its page
    # number, so a folder that is mostly something else must be refused, not
    # read through.
    if len(images) > len(texts) + 2:
        print(f"refused: {len(images)} photos for {len(texts)} pages in {args.images}; point --images at the scan folder only")
        return 1

    matched, problems = plan(images, texts, per_image, ask_page_number)
    for photo, _, page in sorted(matched, key=lambda m: int(m[2]) if m[2].isdigit() else -1):
        print(f"{photo.name} -> p. {page}")
    for problem in problems:
        print(f"PROBLEM {problem}")
    if args.dry_run or problems:
        print("dry run; nothing written" if args.dry_run else "not written: resolve the problems first")
        return 0 if args.dry_run else 1

    paths = reference_commentary_paths()
    store = VolumeStore(paths.volume(volume.volume_id), volume.volume_id)
    for photo, jpeg, page in matched:
        original = photo.read_bytes()
        result = store.ingest(
            printed_page=page,
            image=jpeg,
            text=texts[page],
            source={
                "legacy_file": str(photo),
                "legacy_sha256": sha256_bytes(original),
                "pdf_page": None,
                **({"page_assigned": args.assign_note} if photo.stem in assigned else {}),
            },
            original=original,
            original_suffix=photo.suffix,
            chapter=chapter,
        )
        print(f"p. {page}: {result.action}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
