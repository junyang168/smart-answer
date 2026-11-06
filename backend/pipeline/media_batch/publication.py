"""Public media metadata before the independently reviewed transcript is published."""

import json
from pathlib import Path


def media_only_payload(root: Path, item: str) -> dict | None:
    # Opt in only newly ingested items. Legacy empty/deleted scripts retain their
    # normal reader projection, and editable candidate text is never exposed.
    if (root / "script_published" / f"{item}.json").is_file():
        return None
    catalog = root / "config/sermon.json"
    if not catalog.is_file():
        return None
    row = next((r for r in json.loads(catalog.read_text()) if r["item"] == item), None)
    if not row or not row.get("media_ingest_source_sha256"):
        return None
    fields = (
        "item",
        "title",
        "summary",
        "type",
        "deliver_date",
        "theme",
        "assigned_to_name",
        "author",
        "status",
        "core_bible_verse",
        "keypoints",
        "source",
        "series_id",
    )
    metadata = {field: row.get(field) for field in fields}
    metadata["title"] = row.get("title") or item
    metadata["author"] = row.get("author") or "王守仁"
    return dict(metadata=metadata, script=[], transcript_status="pending_review")
