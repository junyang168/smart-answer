#!/usr/bin/env python3
"""Register a draft-first essay as an internal review preview.

The draft-first pipeline (#283/#285) produces final.md + review-run.json +
source-bindings.json instead of the briefed pipeline's authoring/composition
directories; this registration binds those artifacts by SHA under the same
manifest schema with `variant: "draft_first"`, so the admin review list shows
both pipelines' products side by side.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "wang_topic_essay_review_preview.v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative_to_staging(path: Path, staging: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(staging.resolve()))
    except ValueError as exc:
        raise SystemExit(f"review artifact is outside Wang staging: {resolved}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--passage", required=True)
    parser.add_argument("--review-dir", type=Path, required=True,
                        help="draft_first_review_runner output dir (final.md + review-run.json)")
    parser.add_argument("--bindings", type=Path, required=True,
                        help="source-bindings.json from draft_first_source_binding")
    parser.add_argument("--packet", type=Path, required=True,
                        help="TheologicalEvidencePacket the essay was drafted from")
    args = parser.parse_args()

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", args.review_id):
        raise SystemExit("review id must contain only lowercase letters, numbers, and hyphens")
    load_dotenv(PROJECT_ROOT / ".env")
    data_base = os.getenv("DATA_BASE_DIR")
    if not data_base:
        raise SystemExit("DATA_BASE_DIR is required")
    staging = Path(data_base).expanduser().resolve() / "wang-knowledge-platform" / "staging"

    manuscript = args.review_dir.resolve() / "final.md"
    review_run = args.review_dir.resolve() / "review-run.json"
    bindings = args.bindings.resolve()
    packet = args.packet.resolve()
    for path in (manuscript, review_run, bindings, packet):
        if not path.is_file():
            raise SystemExit(f"required review artifact is missing: {path}")

    markdown = manuscript.read_text(encoding="utf-8")
    title_match = re.search(r"^#\s+(.+)$", markdown, flags=re.MULTILINE)
    if not title_match:
        raise SystemExit("final manuscript has no H1 title")
    run_record = json.loads(review_run.read_text(encoding="utf-8"))
    bindings_record = json.loads(bindings.read_text(encoding="utf-8"))
    manuscript_sha = _sha256(manuscript)
    if run_record.get("final_manuscript_sha256") != manuscript_sha:
        raise SystemExit("review-run.json does not bind this final manuscript")
    if bindings_record.get("manuscript_sha256") != manuscript_sha:
        raise SystemExit("source bindings do not bind this final manuscript")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "variant": "draft_first",
        "review_id": args.review_id,
        "title": title_match.group(1).strip(),
        "passage": args.passage,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "manuscript_relative_path": _relative_to_staging(manuscript, staging),
        "manuscript_sha256": manuscript_sha,
        "workflow_status_relative_path": _relative_to_staging(review_run, staging),
        "workflow_status_sha256": _sha256(review_run),
        "workflow_status": run_record.get("status"),
        "authoring_packet_relative_path": "",
        "authoring_packet_file_sha256": "",
        "authoring_packet_sha256": "",
        "brief_sha256": "",
        "evidence_packet_relative_path": _relative_to_staging(packet, staging),
        "evidence_packet_file_sha256": _sha256(packet),
        "source_bindings_relative_path": _relative_to_staging(bindings, staging),
        "source_bindings_sha256": _sha256(bindings),
        "publication_decision": None,
    }
    output = staging / "topic-essay-reviews" / f"{args.review_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "registered_internal_review", "manifest": str(output), **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
