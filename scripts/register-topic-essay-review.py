#!/usr/bin/env python3
"""Register one generated topic essay as an internal, non-public review preview."""

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


def _result(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    return dict(value.get("result") or value)


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
    parser.add_argument("--authoring-dir", type=Path, required=True)
    parser.add_argument("--composition-dir", type=Path, required=True)
    args = parser.parse_args()

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", args.review_id):
        raise SystemExit("review id must contain only lowercase letters, numbers, and hyphens")
    load_dotenv(PROJECT_ROOT / ".env")
    data_base = os.getenv("DATA_BASE_DIR")
    if not data_base:
        raise SystemExit("DATA_BASE_DIR is required")
    staging = Path(data_base).expanduser().resolve() / "wang-knowledge-platform" / "staging"
    manuscript = args.authoring_dir.resolve() / "draft.md"
    workflow = args.authoring_dir.resolve() / "workflow-status.json"
    packet = args.authoring_dir.resolve() / "topic-authoring-packet.json"
    brief_path = args.composition_dir.resolve() / "theological-editorial-brief.json"
    for path in (manuscript, workflow, packet, brief_path):
        if not path.is_file():
            raise SystemExit(f"required review artifact is missing: {path}")

    markdown = manuscript.read_text(encoding="utf-8")
    title_match = re.search(r"^#\s+(.+)$", markdown, flags=re.MULTILINE)
    if not title_match:
        raise SystemExit("draft has no H1 title")
    workflow_result = _result(workflow)
    packet_result = _result(packet)
    brief_result = _result(brief_path)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "review_id": args.review_id,
        "title": title_match.group(1).strip(),
        "passage": args.passage,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "manuscript_relative_path": _relative_to_staging(manuscript, staging),
        "manuscript_sha256": _sha256(manuscript),
        "workflow_status_relative_path": _relative_to_staging(workflow, staging),
        "workflow_status_sha256": _sha256(workflow),
        "workflow_status": workflow_result.get("status"),
        "authoring_packet_sha256": packet_result.get("packet_sha256"),
        "brief_sha256": brief_result.get("brief_sha256"),
        "publication_decision": None,
    }
    output = staging / "topic-essay-reviews" / f"{args.review_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "registered_internal_review", "manifest": str(output), **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
