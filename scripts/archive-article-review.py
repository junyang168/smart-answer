#!/usr/bin/env python3
"""Archive one registered review so the list shows only living essays.

The manifest stays on disk and the detail page keeps serving it; only the
front-page listing moves it under archived_reviews. Idempotent.

    scripts/archive-article-review.py --review-id church-foundation-poc-v12 \
        --superseded-by church-foundation-draft-first-v1
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from backend.api.wang_article_reviews import REVIEW_MANIFEST_ROOT  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--superseded-by")
    parser.add_argument("--unarchive", action="store_true")
    args = parser.parse_args()

    path = REVIEW_MANIFEST_ROOT / f"{args.review_id}.json"
    if not path.is_file():
        raise SystemExit(f"no such review manifest: {args.review_id}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if args.unarchive:
        manifest.pop("archived", None)
        manifest.pop("superseded_by", None)
        manifest.pop("archived_at", None)
    else:
        if args.superseded_by:
            successor = REVIEW_MANIFEST_ROOT / f"{args.superseded_by}.json"
            if not successor.is_file():
                raise SystemExit(f"successor manifest missing: {args.superseded_by}")
            manifest["superseded_by"] = args.superseded_by
        manifest["archived"] = True
        manifest.setdefault(
            "archived_at", datetime.now(timezone.utc).isoformat()
        )
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"review_id": args.review_id, "archived": manifest.get("archived", False), "superseded_by": manifest.get("superseded_by")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
