#!/usr/bin/env python3
"""Mark an already-registered draft-first essay as a topic essay (#505).

Essays registered before #505 carry no `product_kind`, so the public
repository shelves them by their first passage, as if they were expositions.
This records the kind on the review manifest and, if the essay is published,
copies the kind and every core passage onto its publication manifest -- the
same fields `publish_article_review` now writes. Reader-visible text is not
touched; the manuscript SHA is checked before and after.

Dry run by default; `--apply` writes.

    backend/.venv/bin/python scripts/mark-topic-essay.py ransom-for-many-v1 son-of-man-draft-first-v1
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
        registered["product_kind"] = "theological_topic_essay"
        print(f"{review_id}: review manifest product_kind -> theological_topic_essay")

        publication = reviews.PUBLICATION_ROOT / review_id
        draft_manifest_path = publication / "editorial-draft-manifest.json"
        draft_manifest = None
        if draft_manifest_path.is_file():
            draft_manifest = json.loads(draft_manifest_path.read_text(encoding="utf-8"))
            passages = [p.strip() for p in str(registered.get("passage") or "").replace(";", "；").split("；") if p.strip()]
            for item in draft_manifest["drafts"]:
                item["product_kind"] = "theological_topic_essay"
                item["core_passages"] = passages
            print(f"{review_id}: publication core_passages -> {'；'.join(passages)}")
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
        print(f"{review_id}: written; manuscript sha256 {before} unchanged")
    if not args.apply:
        print("dry run; pass --apply to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
