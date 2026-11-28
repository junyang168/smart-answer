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
    rough = sub.add_parser(
        "rough-plan", help="Freeze ASR-first preflight groups; never call models"
    )
    for name in ("report", "assembly", "source-root", "config"):
        rough.add_argument("--" + name, type=Path, required=True)
    plan = sub.add_parser("plan")
    inputs = plan.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--folder", type=Path)
    inputs.add_argument("--manifest", type=Path)
    plan.add_argument("--actor", required=True)
    plan.add_argument("--pipeline", choices=["gemini-transcript-v1"], default="gemini-transcript-v1")
    plan.add_argument("--speaker-name", default="王守仁（王教授）")
    plan.add_argument("--title")
    plan.add_argument("--editor-origin", help="Optional editor host for an isolated review destination")
    plan.add_argument('--playback-timing-policy',choices=['complete-paragraph-playback.v1','strict'],default='complete-paragraph-playback.v1',help='Complete paragraph playback with brief context warnings; strict preserves exact-boundary review')
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
        "reconcile-costs",
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
    if args.command == "rough-plan":
        from .retirement import require_cli_retired
        require_cli_retired()
        from .rough_plan import create

        print(
            json.dumps(
                create(
                    store,
                    args.report,
                    args.assembly,
                    args.source_root,
                    json.loads(args.config.read_text()),
                )
            )
        )
        return
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
        from .gemini_plan import create
        cfg = {k: str(v) if isinstance(v, Path) else v for k,v in vars(args).items()
               if k not in ('command','manifest','folder','actor')}
        cfg['aligner_python'] = str(args.aligner_python.absolute())
        if args.manifest:
            manifest = json.loads(args.manifest.read_text())
        else:
            from .planner import EXTENSIONS
            from .runtime import file_sha
            # Folder mode uses exact registered filenames only; no inferred sermon identity.
            manifest = {'schema_version':'gemini-media-jobs.v1','jobs':[
                {'item':f.stem,'source':str(f.resolve()),'source_sha256':file_sha(f)}
                for f in sorted(args.folder.rglob('*')) if f.is_file() and not f.is_symlink()
                and f.suffix.lower() in EXTENSIONS]}
        batch = create(store,manifest,cfg,args.actor)
        print(json.dumps({'batch':batch,'progress_href':'/admin/media-batches/'+batch},ensure_ascii=False))
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
    if args.command == "reconcile-costs":
        from .costs import reconcile

        print(json.dumps(reconcile(store, args.batch)))
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
