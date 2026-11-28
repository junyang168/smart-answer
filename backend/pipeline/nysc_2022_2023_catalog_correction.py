"""Repair the verified NYSC series membership for issue #884.

Run with --data-root against a copy to review the changes. Use --apply to
write the metadata and its catalog projection. Re-running safely completes
a partially written correction.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import fcntl
import json
from pathlib import Path

from backend.pipeline.media_batch.runtime import atomic

OLD_ID = "2021年 NYSC 專題：馬太福音釋經（九）"
SERIES_2022 = "2022年 NYSC 專題：馬太福音釋經（九）"
SERIES_2023 = "2023年 NYSC 專題：馬太福音書釋經"
FIRST = "2022年 NYSC 專題 馬太福音釋經（九）王守仁 教授  第一堂"
SECOND = "2022年 NYSC 專題 馬太福音釋經（九）王守仁 教授  第二堂"
AUDIO = ["011WSR01", "011WSR02", "011WSR03"]
FIRST_SOURCE = "https://www.youtube.com/watch?v=CPajzELScJU"
MEMBERS = {SERIES_2022: [FIRST, SECOND], SERIES_2023: AUDIO}


def correction(root: Path, *, apply: bool) -> list[str]:
    metadata_path = root / "config/sermon.json"
    series_path = root / "config/sermon_series.json"
    catalog_path = root / "wang-knowledge-platform/catalog/sermon_catalog.json"
    paths = (metadata_path, series_path, catalog_path)
    original_bytes = [path.read_bytes() for path in paths]
    originals = [json.loads(raw) for raw in original_bytes]
    metadata, series, catalog = deepcopy(originals)

    series_ids = Counter(row["id"] for row in series)
    if (series_ids[OLD_ID], series_ids[SERIES_2022], series_ids[SERIES_2023]) not in (
        (1, 0, 0), (0, 1, 1)
    ):
        raise ValueError("Unexpected NYSC series records")
    for row in series:
        if row["id"] == OLD_ID and row["sermons"] != [*AUDIO, SECOND]:
            raise ValueError("Original NYSC series membership changed")
        if row["id"] in MEMBERS and row["sermons"] != MEMBERS[row["id"]]:
            raise ValueError("Corrected NYSC series membership changed")
        if row["id"] not in {OLD_ID, *MEMBERS} and set(row.get("sermons", ())) & {FIRST, SECOND, *AUDIO}:
            raise ValueError("A target lecture belongs to another series")

    if series_ids[OLD_ID]:
        index = next(i for i, row in enumerate(series) if row["id"] == OLD_ID)
        # The old summary and key points described the mixed collection.
        series[index:index + 1] = [
            dict(id=series_id, title=series_id, sermons=items, summary="", topics="", keypoints="")
            for series_id, items in MEMBERS.items()
        ]

    counts = Counter(row["item"] for row in metadata)
    if any(counts[item] != 1 for item in [*AUDIO, SECOND]) or counts[FIRST] > 1:
        raise ValueError("Unexpected NYSC lecture records")
    by_item = {row["item"]: row for row in metadata}
    for item in AUDIO:
        if by_item[item].get("type") != "audio" or not (by_item[item].get("deliver_date") or "").startswith("2023-"):
            raise ValueError(f"Unexpected audio metadata for {item}")
    if by_item[SECOND].get("type") != "video":
        raise ValueError("Unexpected media type for the 2022 second lecture")

    if not counts[FIRST]:
        if any("CPajzELScJU" in (row.get("source") or "") for row in metadata):
            raise ValueError("First lecture source already registered under another item")
        first = dict(
            item=FIRST, title="馬太福音釋經（九）第一講", author="王守仁",
            source=FIRST_SOURCE, rough_href=FIRST_SOURCE, type="video",
            status="processing", deliver_date="2022",
        )
        metadata.append(first)
        by_item[FIRST] = first
    elif by_item[FIRST].get("source") != FIRST_SOURCE or by_item[FIRST].get("type") != "video":
        raise ValueError("Existing first lecture does not match the confirmed video")

    if not by_item[SECOND].get("deliver_date"):
        by_item[SECOND]["deliver_date"] = "2022"

    records = catalog["records"]
    catalog_counts = Counter(row["transcript_id"] for row in records)
    if any(catalog_counts[item] != 1 for item in [*AUDIO, SECOND]):
        raise ValueError("Missing or duplicate surveyed NYSC lecture")
    projection = {row["transcript_id"]: row for row in records}
    for series_id, items in MEMBERS.items():
        for order, item in enumerate(items, start=1):
            fields = dict(series_id=series_id, series_title=series_id, series_order=order)
            by_item[item].update(fields)
            if item in projection:
                projection[item].update(fields)
                projection[item]["year"] = 2022 if series_id == SERIES_2022 else 2023
                projection[item]["deliver_date"] = by_item[item].get("deliver_date")

    # The catalog contains surveyed lectures only. The new media-only item lives in metadata.
    catalog["summary"]["website_metadata_count"] = len(by_item)
    catalog["summary"]["metadata_only_count"] = len(by_item.keys() - projection.keys())

    changes = [
        (path, after) for path, before, after in zip(paths, originals, (metadata, series, catalog))
        if before != after
    ]
    if apply:
        if [path.read_bytes() for path in paths] != original_bytes:
            raise ValueError("Catalog inputs changed while preparing the correction")
        for path, value in changes:
            atomic(path, value)
            if json.loads(path.read_bytes()) != value:
                raise RuntimeError(f"Catalog readback differs: {path}")
    return [str(path.relative_to(root)) for path, _ in changes]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        with (args.data_root / "config/.sermon-review.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            changed = correction(args.data_root, apply=True)
    else:
        changed = correction(args.data_root, apply=False)
    print(json.dumps({"applied": args.apply, "files_changed": changed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
