"""CLI for the unified Matthew 1–28 source coverage read model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.pipeline.matthew_source_coverage import (
    DEFAULT_CATALOG_PATH,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_NOTES_ROOT,
    DEFAULT_NOTES_SERIES_ID,
    DEFAULT_REPORT_PATH,
    DEFAULT_SURVEY_DIR,
    build_matthew_source_coverage,
    write_matthew_source_coverage,
    write_matthew_source_coverage_report,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--survey-dir", type=Path, default=DEFAULT_SURVEY_DIR)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--notes-root", type=Path, default=DEFAULT_NOTES_ROOT)
    parser.add_argument("--notes-series-id", default=DEFAULT_NOTES_SERIES_ID)
    parser.add_argument("--chapter-start", type=int, default=1)
    parser.add_argument("--chapter-end", type=int, default=28)
    args = parser.parse_args()
    payload = build_matthew_source_coverage(
        args.survey_dir,
        catalog_path=args.catalog,
        notes_root=args.notes_root,
        notes_series_id=args.notes_series_id,
        chapter_start=args.chapter_start,
        chapter_end=args.chapter_end,
    )
    output_path = write_matthew_source_coverage(payload, args.output)
    report_path = write_matthew_source_coverage_report(payload, args.report_output)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "report": str(report_path),
                "summary": payload["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
