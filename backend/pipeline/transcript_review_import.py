"""Register a saved pipeline candidate; adoption happens explicitly in the editor.

Example: python -m backend.pipeline.transcript_review_import --root /isolated/data \
  --item S201011 --source /saved/trial --expected-script-sha256 missing
No model calls, publication, production access, or replacement of human edits.
"""

import argparse
from pathlib import Path
from backend.api.sc_api.transcript_review import ReviewStore


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--item", required=True)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--expected-script-sha256", required=True)
    args = p.parse_args()
    ReviewStore(args.root).register_candidate(
        args.item,
        args.source,
        expected_script_sha256=None
        if args.expected_script_sha256 == "missing"
        else args.expected_script_sha256,
    )
    print("候選已登記。請由認領人在審核頁明確採用。")


if __name__ == "__main__":
    main()
