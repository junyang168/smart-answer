from __future__ import annotations

import argparse
import os
import traceback
from datetime import datetime, timezone

from backend.api.sermon_converter_service import (
    NOTES_TO_SERMON_DIR,
    _save_stage1_job_state,
    sync_draft_chunks_from_generated_units,
    update_sermon_processing_status,
)
from backend.pipeline.stage1 import run_stage1_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a detached Stage 1 pipeline job.")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--mode", required=True, choices=["split", "generate_all", "generate_unit"])
    parser.add_argument("--unit-id")
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_dir = NOTES_TO_SERMON_DIR / args.project_id
    input_path = project_dir / "unified_source.md"
    log_path = project_dir / "stage1_logs.jsonl"

    _save_stage1_job_state(
        args.project_id,
        {
            "status": "running",
            "mode": args.mode,
            "unit_id": args.unit_id,
            "force": args.force,
            "pid": os.getpid(),
            "model": args.model,
            "timeout_seconds": args.timeout,
            "max_retries": args.max_retries,
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    try:
        summary = run_stage1_pipeline(
            input_path=input_path,
            output_dir=project_dir,
            model=args.model,
            timeout_seconds=args.timeout,
            max_retries=args.max_retries,
            force=args.force,
            split_only=args.mode == "split",
            selected_unit_ids=[args.unit_id] if args.mode == "generate_unit" and args.unit_id else None,
            log_path=log_path,
        )

        if args.mode != "split":
            sync_draft_chunks_from_generated_units(args.project_id)
            stage_label = "Stage 1 Ready" if len(summary.generated_units) == len(summary.units) else "Stage 1 Partial Draft Updated"
            update_sermon_processing_status(
                args.project_id,
                False,
                {
                    "stage": stage_label,
                    "progress": 100,
                },
            )
        else:
            update_sermon_processing_status(
                args.project_id,
                False,
                {
                    "stage": "Stage 1 Split Complete",
                    "progress": 100,
                },
            )

        _save_stage1_job_state(
            args.project_id,
            {
                "status": "completed",
                "final_status": "completed",
                "mode": args.mode,
                "unit_id": args.unit_id,
                "force": args.force,
                "pid": os.getpid(),
                "model": args.model,
                "timeout_seconds": args.timeout,
                "max_retries": args.max_retries,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "units": len(summary.units),
                "generated_units": len(summary.generated_units),
                "failed_units": len(summary.failed_units),
            },
        )
        return 0
    except Exception as exc:
        update_sermon_processing_status(args.project_id, False, error=str(exc))
        _save_stage1_job_state(
            args.project_id,
            {
                "status": "failed",
                "final_status": "failed",
                "mode": args.mode,
                "unit_id": args.unit_id,
                "force": args.force,
                "pid": os.getpid(),
                "model": args.model,
                "timeout_seconds": args.timeout,
                "max_retries": args.max_retries,
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
                "traceback": traceback.format_exc(limit=20),
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
