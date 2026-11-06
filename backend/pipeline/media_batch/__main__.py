"""python -m backend.pipeline.media_batch --help"""

import argparse
import json
import os
from pathlib import Path
import subprocess
from .store import Store


def main():
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / "backend/.env")
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    p = argparse.ArgumentParser(
        description="Repeatable media transcription; plan never calls a model."
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="Install isolated media_ingest schema")
    plan = sub.add_parser("plan")
    plan.add_argument("--folder", type=Path, required=True)
    plan.add_argument("--title")
    for name in (
        "artifact-root",
        "media-root",
        "review-root",
        "capacity-root",
        "aligner-python",
    ):
        plan.add_argument("--" + name, type=Path, required=True)
    plan.add_argument(
        "--vocabulary",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "policies/wang_transcription_vocabulary_v1.json",
    )
    plan.add_argument("--profile", default="wang-sermon-v1")
    plan.add_argument("--budget-usd", type=float, required=True)
    plan.add_argument("--max-calls", type=int, required=True)
    plan.add_argument("--ssd-reserve-gib", type=int, default=80)
    plan.add_argument("--internal-reserve-gib", type=int, default=10)
    plan.add_argument("--working-set-gib", type=int, default=15)
    inventory = sub.add_parser(
        "inventory",
        help="Watch an existing copy inventory without dispatching transcription",
    )
    inventory.add_argument("--manifest", type=Path, required=True)
    inventory.add_argument("--receipts", type=Path, required=True)
    inventory.add_argument("--title", default="全部影音檔案")
    inventory.add_argument(
        "--catalog-root",
        type=Path,
        help="Read-only existing website catalog and manuscripts",
    )
    for name in ("artifact-root", "media-root", "review-root", "capacity-root"):
        inventory.add_argument("--" + name, type=Path, required=True)
    index = sub.add_parser("index-existing")
    index.add_argument("--review-root", type=Path, required=True)
    index.add_argument("--media-root", type=Path, required=True)
    for name in (
        "run",
        "start",
        "stop",
        "status",
        "pause",
        "resume",
        "retry",
        "reconcile",
        "raise-budget",
    ):
        cmd = sub.add_parser(name)
        cmd.add_argument("--batch", required=name != "status")
        if name in ("run", "start"):
            cmd.add_argument("--workers", type=int, default=4)
            cmd.add_argument(
                "--drain",
                action="store_true",
                help="Exit when no more work is runnable; omit for a persistent UI-controlled controller",
            )
        if name in ("pause", "resume", "retry", "stop", "raise-budget"):
            cmd.add_argument("--revision", type=int, required=True)
            cmd.add_argument("--operation", required=True)
        if name == "raise-budget":
            cmd.add_argument("--budget-usd", type=float, required=True)
            cmd.add_argument("--reason", required=True)
    args = p.parse_args()
    store = Store()
    if args.command == "install":
        store.install()
        return
    if args.command == "inventory":
        from .inventory import create

        cfg = {
            k: str(v.resolve()) if isinstance(v, Path) else v
            for k, v in vars(args).items()
            if k not in ("command", "manifest", "receipts")
        }
        cfg.update(max_calls=0, budget_usd=0)
        batch = create(store, args.manifest, args.receipts, cfg)
        print(
            json.dumps(dict(batch=batch, progress_href="/admin/media-batches/" + batch))
        )
        return
    if args.command == "index-existing":
        from .planner import index_existing

        print(index_existing(store, args.review_root, args.media_root))
        return
    if args.command == "plan":
        from .planner import plan as create

        if args.budget_usd <= 0 or args.max_calls <= 0:
            raise ValueError("Positive explicit budgets required")
        if (
            args.ssd_reserve_gib < 0
            or args.internal_reserve_gib < 0
            or args.working_set_gib <= 0
        ):
            raise ValueError(
                "Disk reserves must be nonnegative and working set positive"
            )
        cfg = {
            k: str(v.resolve()) if isinstance(v, Path) else v
            for k, v in vars(args).items()
            if k not in ("command", "folder")
        }
        cfg["aligner_python"] = str(
            args.aligner_python.absolute()
        )  # Keep the venv launcher; resolving its symlink loses the environment.
        cfg.update(
            title=args.title or args.folder.name,
            profile=args.profile,
            pipeline_sha=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
        )
        from .runtime import file_sha, storage_guards

        cfg["storage_guards"] = storage_guards(cfg)
        cfg["vocabulary_sha"] = file_sha(args.vocabulary) if args.vocabulary else None
        base = Path(__file__).resolve().parents[1]
        from .store import key

        stage_files = {
            "prepare": ["full_video_transcript.py", "transcript_audio_pilot.py"],
            "visuals": [
                "full_video_transcript.py",
                "slide_change_pilot.py",
                "slide_visual_evidence.py",
                "slide_pilot_models.py",
            ],
            "speech": [
                "media_batch/window_tasks.py",
                "full_video_transcript.py",
                "transcript_audio_pilot.py",
                "transcript_text_layout.py",
                "transcript_audio_correction.py",
                "transcription_vocabulary.py",
            ],
            "final-align": [
                "full_video_transcript.py",
                "transcript_alignment_worker.py",
            ],
            "finish": [
                "full_video_transcript.py",
                "contextual_transcript_timing.py",
                "boundary_audio_review.py",
                "paired_boundary_review.py",
                "paragraph_audio_coherence.py",
                "slide_reading_layout.py",
            ],
            "speakers": [
                "transcript_speaker_batch.py",
                "transcript_speaker_pilot.py",
                "media_batch/speakers.py",
            ],
            "media": ["media_batch/stages.py"],
            "handoff": [
                "video_transcript_handoff.py",
                "media_batch/stages.py",
                "../api/sc_api/transcript_review.py",
                "transcript_editorial_soft_deletions.py",
            ],
        }
        cfg["stage_shas"] = {
            stage: key({file: file_sha(base / file) for file in files})
            for stage, files in stage_files.items()
        }
        batch = create(store, args.folder, cfg)
        print(
            json.dumps(
                dict(batch=batch, progress_href="/admin/media-batches/" + batch),
                ensure_ascii=False,
            )
        )
        return
    if args.command == "start":
        from .service import start

        print(json.dumps(start(store, args.batch, args.workers)))
        return
    if args.command == "stop":
        from .service import stop

        print(json.dumps(stop(store, args.batch, args.operation, args.revision)))
        return
    if args.command == "reconcile":
        print(json.dumps(store.reconcile(args.batch)))
        return
    if args.command == "raise-budget":
        print(
            store.raise_budget(
                args.batch, args.operation, args.revision, args.budget_usd, args.reason
            )
        )
        return
    if args.command in ("pause", "resume", "retry"):
        print(store.control(args.batch, args.operation, args.revision, args.command))
        return
    if args.command == "run":
        from .worker import run

        result = run(store, args.batch, args.workers, args.drain)
    else:
        result = store.snapshot(args.batch)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
