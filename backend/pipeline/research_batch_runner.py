"""Run a neutral research batch through extraction and fidelity review.

The runner deliberately stops before topic induction.  Its merged output has no
topic candidates or knowledge routes; those must be derived from cross-sermon
comparison after every sermon has been processed independently.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config.wang_platform_paths import wang_platform_paths
from backend.pipeline.detailed_knowledge_extraction_runner import _slug
from backend.pipeline.research_batch import load_research_batch, merge_reviewed_packages


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRANSCRIPT_DIR = Path("/opt/homebrew/var/www/church/web/data/script_published")
STAGES = ("extract", "review", "adjudicate", "apply", "merge")


def artifact_paths(output_root: Path, transcript_id: str) -> dict[str, Path]:
    slug = _slug(transcript_id)
    return {
        "package": output_root / "detailed-extractions" / f"{slug}.detailed-knowledge.json",
        "review": output_root / "reviews" / f"{slug}.independent-review.json",
        "adjudication": output_root / "adjudications" / f"{slug}.ai-adjudication.json",
        "overrides": output_root / "overrides" / f"{slug}.consensus-overrides.json",
        "reviewed": output_root / "reviewed" / f"{slug}.reviewed-candidate.json",
    }


def reviewed_package_paths(
    batch: dict[str, Any], *, output_root: Path
) -> list[Path]:
    """Resolve reviewed packages, including explicitly reused prior generations.

    Reuse is opt-in per transcript.  Relative paths are resolved from the
    repository root so a batch config remains portable across machines that
    share the same checkout layout.
    """

    reuse = batch.get("reviewed_package_reuse") or {}
    paths: list[Path] = []
    for transcript_id in batch["transcript_ids"]:
        configured = reuse.get(transcript_id)
        if configured:
            path = Path(os.path.expandvars(str(configured)))
            paths.append(path if path.is_absolute() else PROJECT_ROOT / path)
        else:
            paths.append(artifact_paths(output_root, transcript_id)["reviewed"])
    return paths


def build_command_plan(
    batch: dict[str, Any], *, transcript_dir: Path, output_root: Path, force: bool
) -> list[dict[str, Any]]:
    models = batch.get("models") or {}
    extraction_model = str(models.get("extraction") or "gpt-5.6-sol")
    extraction_effort = str(models.get("extraction_reasoning_effort") or "medium")
    review_model = str(models.get("independent_review") or "claude-sonnet-5")
    adjudicator_model = str(models.get("adjudicator") or "gpt-5.6-sol")
    reconsideration_model = str(models.get("reconsideration") or review_model)
    plan: list[dict[str, Any]] = []
    reused = set((batch.get("reviewed_package_reuse") or {}).keys())

    for transcript_id in batch["transcript_ids"]:
        if transcript_id in reused:
            continue
        paths = artifact_paths(output_root, transcript_id)
        extract = [
            sys.executable, "-m", "backend.pipeline.detailed_knowledge_extraction_runner",
            "--transcript-dir", str(transcript_dir), "--output-dir", str(paths["package"].parent),
            "--ids", transcript_id, "--model", extraction_model,
            "--reasoning-effort", extraction_effort,
        ]
        review = [
            sys.executable, "-m", "backend.pipeline.corpus_ai_review_runner",
            "--claim-layer-package", str(paths["package"]),
            "--claim-layer-output", str(paths["review"]),
            "--transcript-dir", str(transcript_dir), "--model", review_model,
        ]
        if force:
            extract.append("--force")
            review.append("--force")
        plan.extend(
            [
                {"stage": "extract", "transcript_id": transcript_id, "command": extract},
                {"stage": "review", "transcript_id": transcript_id, "command": review},
                {
                    "stage": "adjudicate", "transcript_id": transcript_id,
                    "command": [
                        sys.executable, "-m", "backend.pipeline.corpus_ai_adjudication_runner",
                        "--package", str(paths["package"]), "--review", str(paths["review"]),
                        "--output", str(paths["adjudication"]), "--overrides", str(paths["overrides"]),
                        "--transcript-dir", str(transcript_dir),
                        "--openai-model", adjudicator_model,
                        "--openai-reasoning-effort", extraction_effort,
                        "--claude-model", reconsideration_model,
                    ],
                },
                {
                    "stage": "apply", "transcript_id": transcript_id,
                    "command": [
                        sys.executable, "-m", "backend.pipeline.knowledge_consensus_applier",
                        "--package", str(paths["package"]), "--overrides", str(paths["overrides"]),
                        "--output", str(paths["reviewed"]), "--transcript-dir", str(transcript_dir),
                    ],
                },
            ]
        )
    return plan


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--transcript-dir", type=Path, default=DEFAULT_TRANSCRIPT_DIR)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--stage", choices=("all", *STAGES), default="all")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    batch = load_research_batch(args.batch)
    output_root = args.output_root or (
        wang_platform_paths().claim_layer_staging
        / "research-batches"
        / batch["batch_id"]
    )
    missing = [
        transcript_id for transcript_id in batch["transcript_ids"]
        if not (args.transcript_dir / f"{transcript_id}.json").is_file()
    ]
    if missing:
        parser.error("missing published transcripts: " + ", ".join(missing))

    plan = build_command_plan(
        batch, transcript_dir=args.transcript_dir, output_root=output_root, force=args.force
    )
    selected = plan if args.stage == "all" else [row for row in plan if row["stage"] == args.stage]
    merged_output = output_root / "merged" / "research-batch-knowledge.json"
    preview = {
        "batch_id": batch["batch_id"],
        "semantic_assumption": batch["semantic_assumption"],
        "transcripts": batch["transcript_ids"],
        "selected_stage": args.stage,
        "commands": selected,
        "reused_reviewed_packages": {
            transcript_id: str(path)
            for transcript_id, path in zip(
                batch["transcript_ids"], reviewed_package_paths(batch, output_root=output_root)
            )
            if transcript_id in (batch.get("reviewed_package_reuse") or {})
        },
        "merged_output": str(merged_output),
        "would_call_models": not args.dry_run and args.stage in {"all", "extract", "review", "adjudicate"},
    }
    if args.dry_run:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0

    manifest = {
        **preview,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "completed_commands": [],
    }
    manifest_path = output_root / "run-manifest.json"
    _write_manifest(manifest_path, manifest)
    try:
        for row in selected:
            subprocess.run(row["command"], cwd=PROJECT_ROOT, check=True)
            manifest["completed_commands"].append(
                {"stage": row["stage"], "transcript_id": row["transcript_id"]}
            )
            _write_manifest(manifest_path, manifest)
        if args.stage in {"all", "merge"}:
            reviewed_paths = reviewed_package_paths(batch, output_root=output_root)
            absent = [str(path) for path in reviewed_paths if not path.is_file()]
            if absent:
                raise FileNotFoundError("missing reviewed packages: " + ", ".join(absent))
            merged = merge_reviewed_packages(batch, reviewed_paths)
            merged_output.parent.mkdir(parents=True, exist_ok=True)
            merged_output.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        manifest["status"] = "completed"
        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
        _write_manifest(manifest_path, manifest)
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        _write_manifest(manifest_path, manifest)
        raise
    print(json.dumps({"status": "completed", "output_root": str(output_root)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
