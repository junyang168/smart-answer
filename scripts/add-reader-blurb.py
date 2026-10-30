#!/usr/bin/env python3
"""Attach a reader blurb to an already-registered topic essay (#509).

Essays registered before #509 have no card intro or opening summary. Write one
with `backend.pipeline.draft_first_reader_blurb` next to the review's
final.md, then run this: it records the blurb on the review manifest (path and
SHA, as registration now does) and, if the essay is published, copies the two
texts onto its publication manifest -- the same fields `publish_article_review`
now writes. The blurb must have been written from the registered manuscript.
Reader-visible manuscript text is not touched; its SHA is checked before and
after.

Dry run by default; `--apply` writes.

    backend/.venv/bin/python scripts/add-reader-blurb.py ransom-for-many-v1 son-of-man-draft-first-v1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.api import wang_article_reviews as reviews  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("review_ids", nargs="+")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    for review_id in args.review_ids:
        review_path = reviews.REVIEW_MANIFEST_ROOT / f"{review_id}.json"
        registered = json.loads(review_path.read_text(encoding="utf-8"))
        manuscript_relative = str(registered["manuscript_relative_path"])
        blurb_relative = str(Path(manuscript_relative).with_name("reader-blurb.json"))
        blurb_path = reviews._safe_staging_child(blurb_relative)
        blurb = json.loads(blurb_path.read_text(encoding="utf-8"))
        if blurb.get("manuscript_sha256") != registered["manuscript_sha256"]:
            raise SystemExit(f"{review_id}: {blurb_relative} was not written from the registered manuscript")
        registered["reader_blurb_relative_path"] = blurb_relative
        registered["reader_blurb_sha256"] = _sha256(blurb_path)
        print(f"{review_id}: review manifest reader blurb -> {blurb_relative}")
        print(f"  引言: {blurb['reader_intro']}")
        print(f"  提要: {blurb['reader_summary']}")

        publication = reviews.PUBLICATION_ROOT / review_id
        draft_manifest_path = publication / "editorial-draft-manifest.json"
        draft_manifest = None
        if draft_manifest_path.is_file():
            draft_manifest = json.loads(draft_manifest_path.read_text(encoding="utf-8"))
            for item in draft_manifest["drafts"]:
                item["reader_intro"] = blurb["reader_intro"]
                item["reader_summary"] = blurb["reader_summary"]
            print(f"{review_id}: publication manifest gets the intro and summary")
        else:
            print(f"{review_id}: not published")

        if not args.apply:
            continue
        manuscript = publication / "manuscript.md"
        before = _sha256(manuscript) if manuscript.is_file() else None
        reviews._atomic_write_json(review_path, registered)
        if draft_manifest is not None:
            reviews._atomic_write_json(draft_manifest_path, draft_manifest)
        after = _sha256(manuscript) if manuscript.is_file() else None
        assert before == after
        integrity = reviews._review_data(review_path, include_markdown=False)["integrity_status"]
        assert integrity == "verified", integrity
        print(f"{review_id}: written; manuscript sha256 {before} unchanged; review integrity {integrity}")
    if not args.apply:
        print("dry run; pass --apply to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
