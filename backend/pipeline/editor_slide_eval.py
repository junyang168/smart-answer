"""Score an attachment run against a separately supplied, frozen lecture reference."""

import argparse
import json
from pathlib import Path

from backend.pipeline.slide_change_pilot import read_json


def grade(reference, evidence, report):
    if reference["source_sha256"] != evidence["source_video_sha256"]:
        raise ValueError("Reference belongs to another video")
    expected = dict(reference["placements"])
    actual = {
        r["event_id"]: r["paragraph"]
        for r in report["events"]
        if r["reason"] == "attached"
    }
    merged = {
        r["event_id"]: r["reason"].removeprefix("duplicate of ")
        for r in report["events"]
        if r["reason"].startswith("duplicate of ")
    }
    return {
        "matched": sorted(
            k for k in actual.keys() & expected.keys() if actual[k] == expected[k]
        ),
        "missing": sorted(expected.keys() - actual.keys()),
        "extra": sorted(actual.keys() - expected.keys()),
        "misplaced": sorted(
            k for k in actual.keys() & expected.keys() if actual[k] != expected[k]
        ),
        "incorrect_merge": sorted(
            k for k, v in merged.items() if reference["duplicates"].get(k) != v
        ),
        "previously_held_attached": sorted(set(reference["held"]) & actual.keys()),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            grade(
                read_json(args.reference),
                read_json(args.out / "slide_evidence.json"),
                read_json(args.out / "report.json"),
            ),
            indent=2,
        )
    )
