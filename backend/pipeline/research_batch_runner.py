"""Run a neutral research batch from source file to knowledge store.

The runner deliberately stops before topic induction.  Its merged output has no
topic candidates or knowledge routes; those must be derived from cross-sermon
comparison after every sermon has been processed independently.

Every stage here is a runner that calls its own models; nothing in this chain
needs a person to read a model's output and decide what to do with it.  What
this module owns is the *order*, and that used to live in whoever was typing.
It cost the corpus three things, all visible on disk today:

* Notes manuscripts could not be batched at all, because the plan was built
  from `transcript_ids` and passed `--ids`.  Three 母本 were therefore always
  driven by hand.
* Cross-section relations were not a stage, so they were run for the 母本 and
  forgotten for the sermon extracted two hours later.
* Ingest was not a stage, so one source reached PostgreSQL from its raw
  extraction package, having skipped adjudication and consensus entirely.

The result was seven staging layouts over 28 packages.  There is now one, and
`artifact_paths` is the whole of it.

Resumption is decided from artifacts on disk, never from the run ledger.  Each
stage runner already skips on a fingerprint match, and `run_ledger` is
explicitly built to degrade to a warning when the database is unreachable; a
resume that read the ledger would make that database a hard dependency of the
pipeline and overturn the decision `run_ledger` documents.
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
from backend.pipeline.research_batch import (
    batch_members,
    load_research_batch,
    merge_reviewed_packages,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRANSCRIPT_DIR = Path("/opt/homebrew/var/www/church/web/data/script_published")

#: Run against one source, in this order.  `ingest` is last because it is the
#: only one that writes outside the batch directory.
MEMBER_STAGES = ("extract", "cross_section", "review", "adjudicate", "apply", "ingest")
#: Run once, over every member's reviewed package.
BATCH_STAGES = ("merge",)
STAGES = MEMBER_STAGES + BATCH_STAGES

#: `ingest` writes to the authoring authority, so it is opt-in twice over: it
#: is excluded from `--stage all` unless `--ingest` is passed, and the command
#: it builds only carries `--apply` when `--apply` is.
DEFAULT_STAGES = tuple(stage for stage in STAGES if stage != "ingest")


def artifact_paths(output_root: Path, member_key: str) -> dict[str, Path]:
    """Every path one member owns. This is the only layout there is.

    Derivable from `batch_id` and the member key alone, so nobody has to open a
    directory to find out where a source's artifacts went.
    """

    slug = _slug(member_key)
    return {
        "source_manifest": output_root / "sources" / f"{slug}.source-manifest.json",
        "package": output_root / "detailed-extractions" / f"{slug}.detailed-knowledge.json",
        "cross_section": output_root / "cross-section" / f"{slug}.cross-section.json",
        "review": output_root / "reviews" / f"{slug}.independent-review.json",
        "adjudication": output_root / "adjudications" / f"{slug}.ai-adjudication.json",
        "overrides": output_root / "overrides" / f"{slug}.consensus-overrides.json",
        "reviewed": output_root / "reviewed" / f"{slug}.reviewed-candidate.json",
    }


def reviewed_package_paths(
    batch: dict[str, Any], *, output_root: Path
) -> list[Path]:
    """Resolve reviewed packages, including explicitly reused prior generations.

    Reuse is opt-in per member.  Relative paths are resolved from the
    repository root so a batch config remains portable across machines that
    share the same checkout layout.
    """

    reuse = batch.get("reviewed_package_reuse") or {}
    paths: list[Path] = []
    for member in batch_members(batch):
        transcript_id = member["key"]
        configured = reuse.get(transcript_id)
        if configured:
            path = Path(os.path.expandvars(str(configured)))
            paths.append(path if path.is_absolute() else PROJECT_ROOT / path)
        else:
            paths.append(artifact_paths(output_root, transcript_id)["reviewed"])
    return paths


def resolve_transcript_dir(member: dict[str, Any], transcript_dirs: list[Path]) -> Path | None:
    """Which directory holds this member's transcript, or None if none does.

    Repeatable rather than single because chapter 16 is split across two: six
    of its sermons are in `script_review` and three in `script_published`, and
    a batch that could name only one directory could not describe the chapter.
    Notes manuscripts have no transcript directory at all -- they are addressed
    by path -- so they resolve to the first, which nothing then reads.
    """

    if member["source_type"] != "sermon_transcript":
        return transcript_dirs[0]
    return next(
        (directory for directory in transcript_dirs
         if (directory / f"{member['key']}.json").is_file()),
        None,
    )


def _member_source_manifest(member: dict[str, Any], path: Path) -> None:
    """Write the one-row source manifest the extraction runner reads.

    Generated from the batch config rather than hand-written beside it: a
    manifest somebody maintains separately is a second place for a source path
    to be wrong, and `load_source_manifest` re-checks these rows against the
    file on disk anyway.
    """

    row = {key: value for key, value in member.items() if key != "key"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"sources": [row]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_command_plan(
    batch: dict[str, Any], *, transcript_dir: Path | list[Path], output_root: Path,
    force: bool, apply_ingest: bool = False,
) -> list[dict[str, Any]]:
    models = batch.get("models") or {}
    extraction_model = str(models.get("extraction") or "gpt-5.6-sol")
    extraction_effort = str(models.get("extraction_reasoning_effort") or "medium")
    review_model = str(models.get("independent_review") or "claude-sonnet-5")
    adjudicator_model = str(models.get("adjudicator") or "gpt-5.6-sol")
    reconsideration_model = str(models.get("reconsideration") or review_model)
    relation_model = str(models.get("cross_section") or extraction_model)
    # A source big enough to need a bigger budget must be able to say so from
    # the batch config. Otherwise the only way to raise it is to bypass the
    # orchestrator and run the stage by hand, which is the habit this module
    # exists to end.
    review_budget = models.get("review_max_output_tokens")
    adjudicator_budget = models.get("adjudicator_max_output_tokens")
    plan: list[dict[str, Any]] = []
    reused = set((batch.get("reviewed_package_reuse") or {}).keys())
    transcript_dirs = (
        [transcript_dir] if isinstance(transcript_dir, Path) else list(transcript_dir)
    )

    for member in batch_members(batch):
        key = member["key"]
        if key in reused:
            continue
        paths = artifact_paths(output_root, key)
        member_dir = resolve_transcript_dir(member, transcript_dirs) or transcript_dirs[0]
        extract = [
            sys.executable, "-m", "backend.pipeline.detailed_knowledge_extraction_runner",
            "--output-dir", str(paths["package"].parent),
            "--model", extraction_model, "--reasoning-effort", extraction_effort,
        ]
        # The two source kinds differ here and nowhere else downstream: every
        # later stage reads `source_documents` out of the package and resolves
        # the source through `load_knowledge_source_document`.
        if member["source_type"] == "notes_manuscript":
            extract += ["--source-manifest", str(paths["source_manifest"])]
        else:
            extract += ["--transcript-dir", str(member_dir), "--ids", key]

        cross_section = [
            sys.executable, "-m", "backend.pipeline.cross_section_relation_runner",
            "--package", str(paths["package"]), "--output", str(paths["cross_section"]),
            "--model", relation_model, "--reasoning-effort", extraction_effort,
        ]
        # Downstream reads the cross-section package, not the raw extraction.
        # A single-section source gets it written through unchanged, so this
        # holds for every member and no stage has to be conditional.
        source = str(paths["cross_section"])
        review = [
            sys.executable, "-m", "backend.pipeline.corpus_ai_review_runner",
            "--claim-layer-package", source,
            "--claim-layer-output", str(paths["review"]),
            "--transcript-dir", str(member_dir), "--model", review_model,
        ]
        if review_budget:
            review += ["--max-output-tokens", str(int(review_budget))]
        if force:
            extract.append("--force")
            cross_section.append("--force")
            review.append("--force")

        ingest = [
            sys.executable, "-m", "backend.pipeline.extraction_supersede_runner",
            str(paths["reviewed"]),
        ]
        if apply_ingest:
            ingest.append("--apply")

        plan.extend(
            [
                {"stage": "extract", "transcript_id": key, "command": extract},
                {"stage": "cross_section", "transcript_id": key, "command": cross_section},
                {"stage": "review", "transcript_id": key, "command": review},
                {
                    "stage": "adjudicate", "transcript_id": key,
                    "command": [
                        sys.executable, "-m", "backend.pipeline.corpus_ai_adjudication_runner",
                        "--package", source, "--review", str(paths["review"]),
                        "--output", str(paths["adjudication"]), "--overrides", str(paths["overrides"]),
                        "--transcript-dir", str(member_dir),
                        "--openai-model", adjudicator_model,
                        "--openai-reasoning-effort", extraction_effort,
                        "--claude-model", reconsideration_model,
                        *(["--max-output-tokens", str(int(adjudicator_budget))]
                          if adjudicator_budget else []),
                    ],
                },
                {
                    "stage": "apply", "transcript_id": key,
                    "command": [
                        sys.executable, "-m", "backend.pipeline.knowledge_consensus_applier",
                        "--package", source, "--overrides", str(paths["overrides"]),
                        "--output", str(paths["reviewed"]), "--transcript-dir", str(member_dir),
                    ],
                },
                # Ingest supersedes the extraction it replaces in the same
                # change set. Plain `ingest-package` would leave the previous
                # extraction live beside the new one (#105).
                {"stage": "ingest", "transcript_id": key, "command": ingest},
            ]
        )
    return plan


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


#: The batch runner's stage name -> the name that stage files in the ledger,
#: which is the name the overview has a column for. They differ because the
#: plan reads as a verb list and the overview reads as a noun list; where they
#: differ, the overview wins, because that is where "done" is decided.
LEDGER_STAGE = {
    "extract": "extraction",
    "cross_section": "cross_section",
    "review": "review",
    "adjudicate": "adjudication",
    "apply": "merge",
}


def unrecorded_stages(
    members: list[dict[str, Any]], *, since: datetime, database_url: str | None = None,
) -> dict[str, list[str]]:
    """Stages that ran in this batch and left no row the overview can read.

    A stage runner writes its own ledger row, and nothing checked that it did.
    Two of them did not: the claim-layer review path filed nothing at all, and
    the adjudicator filed against the package filename instead of the source.
    Both were invisible until somebody looked at the dashboard and saw a source
    whose work was done reading as not done -- which is the only place "done"
    is actually decided, and the last place anybody wants to discover a lie.

    Reporting, never blocking: `run_ledger` degrades to a warning when the
    database is unreachable, and a check built on it must not be able to fail a
    batch that produced correct artifacts.
    """

    url = database_url or os.getenv("KNOWLEDGE_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        return {}
    try:
        import psycopg
        from backend.pipeline.source_keys import normalize_source_key
    except ImportError:  # pragma: no cover - depends on local environment
        return {}
    try:
        with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cursor:
            missing: dict[str, list[str]] = {}
            for key, stages in members.items():
                subject = normalize_source_key(key)
                for stage in stages:
                    ledger_stage = LEDGER_STAGE.get(stage)
                    if ledger_stage is None:
                        continue
                    cursor.execute(
                        """SELECT 1 FROM wang_knowledge.pipeline_runs
                           WHERE stage = %s AND started_at >= %s
                             AND (subject_id = %s OR %s = ANY(source_ids))
                           LIMIT 1""",
                        (ledger_stage, since, subject, subject),
                    )
                    if cursor.fetchone() is None:
                        missing.setdefault(key, []).append(ledger_stage)
            return missing
    except Exception as exc:  # pragma: no cover - depends on local environment
        print(f"research_batch_runner: could not verify the ledger ({exc})", file=sys.stderr)
        return {}


def _member_status(plan: list[dict[str, Any]], results: dict[str, Any]) -> list[dict[str, Any]]:
    """Where each member stands, in the order the batch names them."""

    rows: list[dict[str, Any]] = []
    for key in dict.fromkeys(row["transcript_id"] for row in plan):
        rows.append({"source": key, **results.get(key, {"status": "not_started"})})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument(
        "--transcript-dir", action="append", type=Path, dest="transcript_dirs",
        help="repeatable; chapter 16 needs two, and a transcript is looked up "
             "in the order given",
    )
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--stage", choices=("all", *STAGES), default="all")
    parser.add_argument(
        "--only", nargs="+", metavar="SOURCE",
        help="run only these members (transcript id or source_id)",
    )
    parser.add_argument(
        "--ingest", action="store_true",
        help="include the ingest stage in --stage all; plans the change set only "
             "unless --apply is also given",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="let the ingest stage write to the knowledge store",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.apply and not (args.ingest or args.stage == "ingest"):
        parser.error("--apply only means something with --ingest or --stage ingest")

    batch = load_research_batch(args.batch)
    output_root = args.output_root or (
        wang_platform_paths().claim_layer_staging
        / "research-batches"
        / batch["batch_id"]
    )
    members = batch_members(batch)
    if args.only:
        known = {member["key"] for member in members}
        unknown = sorted(set(args.only).difference(known))
        if unknown:
            parser.error("--only names members outside the batch: " + ", ".join(unknown))
        members = [member for member in members if member["key"] in set(args.only)]
    transcript_dirs = args.transcript_dirs or [DEFAULT_TRANSCRIPT_DIR]
    missing = [
        member["key"] for member in members
        if resolve_transcript_dir(member, transcript_dirs) is None
    ]
    if missing:
        parser.error(
            "transcripts not found under "
            + ", ".join(str(directory) for directory in transcript_dirs)
            + ": " + ", ".join(missing)
        )

    selected_batch = {**batch, "transcript_ids": [], "sources": []}
    for member in members:
        if member["source_type"] == "sermon_transcript":
            selected_batch["transcript_ids"].append(member["key"])
        else:
            selected_batch["sources"].append(
                {key: value for key, value in member.items() if key != "key"}
            )

    plan = build_command_plan(
        selected_batch, transcript_dir=transcript_dirs, output_root=output_root,
        force=args.force, apply_ingest=args.apply,
    )
    if args.stage == "all":
        wanted = set(DEFAULT_STAGES) | ({"ingest"} if args.ingest else set())
    else:
        wanted = {args.stage}
    selected = [row for row in plan if row["stage"] in wanted]
    merged_output = output_root / "merged" / "research-batch-knowledge.json"
    preview = {
        "batch_id": batch["batch_id"],
        "semantic_assumption": batch["semantic_assumption"],
        "members": [
            {"source": member["key"], "source_type": member["source_type"]}
            for member in members
        ],
        "selected_stage": args.stage,
        "commands": selected,
        "reused_reviewed_packages": {
            member["key"]: str(path)
            for member, path in zip(
                members, reviewed_package_paths(selected_batch, output_root=output_root)
            )
            if member["key"] in (batch.get("reviewed_package_reuse") or {})
        },
        "merged_output": str(merged_output),
        "ingest_applies": bool(args.apply),
        "would_call_models": not args.dry_run
        and bool(wanted & {"extract", "cross_section", "review", "adjudicate"}),
    }
    if args.dry_run:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0

    for member in members:
        if member["source_type"] == "notes_manuscript":
            _member_source_manifest(
                member, artifact_paths(output_root, member["key"])["source_manifest"]
            )

    started_at = datetime.now(timezone.utc)
    manifest = {
        **preview,
        "started_at": started_at.isoformat(),
        "status": "running",
        "completed_commands": [],
    }
    manifest_path = output_root / "run-manifest.json"
    _write_manifest(manifest_path, manifest)

    # Members are independent of one another, so one failing is a fact about
    # that member, not a reason to abandon the nine behind it. The previous
    # `check=True` in this loop meant a batch of ten could stop after seven and
    # leave nothing saying which three never ran.
    results: dict[str, dict[str, Any]] = {}
    interrupted = False
    for row in selected:
        key = row["transcript_id"]
        if key is not None and results.get(key, {}).get("status") == "failed":
            results[key]["skipped_stages"] = results[key].get("skipped_stages", []) + [row["stage"]]
            continue
        try:
            subprocess.run(row["command"], cwd=PROJECT_ROOT, check=True)
        except KeyboardInterrupt:
            # An interrupt has to leave a terminal status behind. Left at
            # "running" the manifest claims work is in progress that nothing is
            # doing, which is the same lie `pipeline_runs` grew a heartbeat to
            # stop telling. Everything already finished stays on disk and its
            # stage runner will skip it on the next run.
            results.setdefault(key, {})["status"] = "interrupted"
            results[key]["failed_stage"] = row["stage"]
            interrupted = True
            break
        except (subprocess.CalledProcessError, OSError) as exc:
            results.setdefault(key, {})["status"] = "failed"
            results[key]["failed_stage"] = row["stage"]
            results[key]["error"] = str(exc)
            manifest["status"] = "partial"
            _write_manifest(manifest_path, {**manifest, "members": _member_status(selected, results)})
            continue
        results.setdefault(key, {})["status"] = "running"
        results[key]["last_stage"] = row["stage"]
        manifest["completed_commands"].append({"stage": row["stage"], "transcript_id": key})
        _write_manifest(manifest_path, manifest)

    for key, result in results.items():
        if result.get("status") == "running":
            result["status"] = "completed"

    merge_error: str | None = None
    # The merged package describes the whole batch, so a run that covered part
    # of it must not write one. Merging what `--only` selected would replace a
    # full merge with a one-member file and report success -- silently wrong in
    # exactly the way this orchestration exists to stop.
    partial_selection = bool(args.only) and len(members) < len(batch_members(batch))
    if partial_selection and "merge" in wanted:
        merge_error = (
            "merge skipped: --only selected "
            f"{len(members)} of {len(batch_members(batch))} members, and the "
            "merged package describes the whole batch"
        )
    elif "merge" in wanted and not interrupted:
        reviewed_paths = reviewed_package_paths(selected_batch, output_root=output_root)
        absent = [str(path) for path in reviewed_paths if not path.is_file()]
        if absent:
            # Reaching the merge with a member missing is the expected shape of
            # a partial run, not a crash: the members that did finish keep
            # their artifacts and the table below says which ones did not.
            merge_error = "missing reviewed packages: " + ", ".join(absent)
        else:
            merged = merge_reviewed_packages(selected_batch, reviewed_paths)
            merged_output.parent.mkdir(parents=True, exist_ok=True)
            merged_output.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

    # What ran, per member, so the check asks only about stages this batch
    # actually executed.
    ran: dict[str, list[str]] = {}
    for row in selected:
        if results.get(row["transcript_id"], {}).get("status") in {"completed", "running"}:
            ran.setdefault(row["transcript_id"], []).append(row["stage"])
    unrecorded = unrecorded_stages(ran, since=started_at)

    members_status = _member_status(selected, results)
    for row in members_status:
        if row["source"] in unrecorded:
            # The work is on disk and the overview cannot see it. Said here
            # because this is the last moment anyone is looking.
            row["unrecorded_stages"] = unrecorded[row["source"]]
    failed = [row for row in members_status if row.get("status") != "completed"]
    manifest["members"] = members_status
    if interrupted:
        manifest["status"] = "interrupted"
    elif partial_selection:
        # Nothing went wrong; the run was deliberately narrowed.
        manifest["status"] = "partial_selection" if not failed else "partial"
    else:
        manifest["status"] = "completed" if not failed and not merge_error else "partial"
    if merge_error:
        manifest["merge_error"] = merge_error
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    _write_manifest(manifest_path, manifest)

    if unrecorded:
        print(
            "research_batch_runner: these stages ran but filed no ledger row, so the "
            "overview will show them as never run: "
            + "; ".join(f"{key} -> {', '.join(stages)}" for key, stages in unrecorded.items()),
            file=sys.stderr,
        )
    print(json.dumps({
        "status": manifest["status"],
        "output_root": str(output_root),
        "members": members_status,
        **({"merge_error": merge_error} if merge_error else {}),
    }, ensure_ascii=False, indent=2))
    return 1 if failed or (merge_error and not partial_selection) else 0


if __name__ == "__main__":
    raise SystemExit(main())
