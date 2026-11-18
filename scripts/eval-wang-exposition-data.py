#!/usr/bin/env python3
"""Replay the published-source evidence for the seven-book exposition review."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

FIXTURE = Path(__file__).resolve().parents[1] / "backend/tests/fixtures/public_repository/exposition_data_eval.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:3648")
    parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args()
    fixture = json.loads(FIXTURE.read_text())
    with urlopen(args.base_url.rstrip("/") + "/api/sc_api/sermon_series", timeout=20) as response:
        series = {row["id"]: row["sermons"] for row in json.load(response)}

    publication = args.data_dir / "script_published"
    errors = []
    for series_id, expected in fixture["series"].items():
        if series_id not in series:
            errors.append(f"{series_id}: missing series")
            continue
        published = [row["item"] for row in series[series_id] if row["status"] == "published"]
        missing = [item for item in published if not (publication / f"{item}.json").is_file()]
        if {"published": len(published), "missing": missing} != expected:
            errors.append(f"{series_id}: publication inventory changed ({len(published)} published, missing {missing})")

    for case in fixture["cases"]:
        item = case["source"]
        file = publication / f"{item}.json"
        try:
            if not any(row["item"] == item and row["status"] == "published" for row in series.get(case["series"], [])):
                raise ValueError("the source is no longer listed as published in this series")
            raw = file.read_bytes()
            if hashlib.sha256(raw).hexdigest() != case["snapshot_sha256"]:
                raise ValueError("the published snapshot changed")
            payload = json.loads(raw)
            paragraphs = payload if isinstance(payload, list) else payload["script"]
            text = paragraphs[case["paragraph"]]["text"]
            if hashlib.sha256(text.encode()).hexdigest() != case["paragraph_sha256"]:
                raise ValueError("the source paragraph changed")
        except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
            errors.append(f"{item} — {case['passage']}: {error}")

    for error in errors:
        print(error)
    if errors:
        return 1
    print(f"Verified {len(fixture['series'])} publication inventories and {len(fixture['cases'])} reviewed source cases.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
