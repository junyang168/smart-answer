from __future__ import annotations

import argparse
import os
from pathlib import Path

from backend.pipeline.stage1 import run_stage1_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Stage 1 notes-to-sermon pipeline.")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--input", help="Path to corrected_notes.md")
    source_group.add_argument("--project-id", help="Existing notes-to-sermon project id")
    parser.add_argument("--output", help="Output directory for Stage 1 artifacts")
    parser.add_argument("--model", default="claude-sonnet-4-6", help="Anthropic Claude model name")
    parser.add_argument("--timeout", type=float, default=90.0, help="Per-request timeout in seconds")
    parser.add_argument("--max-retries", type=int, default=3, help="Retry count per API call")
    parser.add_argument("--force", action="store_true", help="Clear previous Stage 1 artifacts before rerun")
    parser.add_argument("--split-only", action="store_true", help="Run only Stage 1A unit splitting")
    return parser


def _get_notes_to_sermon_root() -> Path:
    data_base_dir = os.environ.get("DATA_BASE_DIR")
    if not data_base_dir:
        raise RuntimeError("DATA_BASE_DIR environment variable is required when using --project-id")
    root = Path(data_base_dir).resolve() / "notes_to_surmon"
    if not root.exists():
        raise FileNotFoundError(f"notes_to_surmon directory not found: {root}")
    return root


def _resolve_project_paths(project_id: str, output_override: str | None) -> tuple[Path, Path]:
    project_dir = _get_notes_to_sermon_root() / project_id
    if not project_dir.exists():
        raise FileNotFoundError(f"Project directory not found: {project_dir}")

    input_path = project_dir / "unified_source.md"
    if not input_path.exists():
        raise FileNotFoundError(
            f"Unified source not found for project '{project_id}': {input_path}"
        )

    output_dir = Path(output_override).resolve() if output_override else project_dir
    return input_path, output_dir


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.project_id:
        input_path, output_dir = _resolve_project_paths(args.project_id, args.output)
    else:
        input_path = Path(args.input).resolve()
        output_dir = Path(args.output).resolve() if args.output else input_path.parent

    log_path = output_dir / "stage1_logs.jsonl"

    summary = run_stage1_pipeline(
        input_path=input_path,
        output_dir=output_dir,
        model=args.model,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
        force=args.force,
        split_only=args.split_only,
        log_path=log_path,
    )

    if args.project_id:
        print(f"Project: {args.project_id}")
    print(f"Input: {summary.input_path}")
    print(f"Output: {summary.output_dir}")
    print(f"Units: {len(summary.units)}")
    if args.split_only:
        print("Mode: split-only")
    else:
        print(f"Generated: {len(summary.generated_units)}")
        print(f"Failed: {len(summary.failed_units)}")
    return 0 if args.split_only or not summary.failed_units else 1


if __name__ == "__main__":
    raise SystemExit(main())
