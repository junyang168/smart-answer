"""CLI for rebuilding ``$DATA_BASE_DIR/sermon_catalog.json``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.pipeline.sermon_catalog import (
    DEFAULT_OUTPUT_PATH,
    DEFAULT_SURVEY_DIR,
    build_catalog,
    write_catalog,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the public Wang sermon catalog read model.")
    parser.add_argument("--survey-dir", type=Path, default=DEFAULT_SURVEY_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--series", type=Path)
    args = parser.parse_args()

    payload = build_catalog(
        args.survey_dir,
        metadata_path=args.metadata,
        series_path=args.series,
    )
    path = write_catalog(payload, args.output)
    print(json.dumps({"output": str(path), **payload["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
