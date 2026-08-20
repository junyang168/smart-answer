"""Import the runs that happened before the ledger existed, once.

The overview reads the ledger and nothing else, which is right: a page that
scans staging directories at read time cannot tell a current package from a
superseded one, and starts lying the moment a batch lands in an eighth layout.
But it left every stage reading "never run" for sources whose packages are
plainly on disk -- true about the ledger, and useless to the person asking
whether the work has been done.

So the packages are imported *once*, deliberately, instead of being consulted on
every read.  What makes this honest rather than a scan in disguise:

* every imported row is marked `backfilled` in `metadata`, with the file it came
  from, so nothing here is ever mistaken for a run this system watched happen;
* the timestamps are the artifact's own `generated_at`, not now();
* cost is whatever the package recorded, which for the 26 packages written
  before usage was collected is nothing -- they import with a NULL cost, and the
  overview shows them as costing an unknown amount rather than nothing;
* re-running is idempotent: a package already imported is skipped by its
  fingerprint, so this can be run again after new artifacts appear.

Superseded packages are imported too, in `generated_at` order.  A source that
was extracted three times really was extracted three times, and the overview
takes the latest.

    python -m backend.pipeline.run_ledger_backfill --dry-run
    python -m backend.pipeline.run_ledger_backfill --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from backend.config.wang_platform_paths import wang_platform_paths
from backend.pipeline.model_prices import price_usage
from backend.pipeline.run_ledger import new_run_id
from backend.pipeline.source_keys import document_row_key, key_from_source_path


#: Filename suffix -> the stage that produces it.
#:
#: One stage, several spellings. Adjudication has been written as
#: `.adjudication.json`, `.ai-adjudication.json` and `.adjudication-v2.json`;
#: applying the consensus has been written as `.reviewed-candidate.json` and
#: `.consensus-applied.json`. Matching only the first name of each found 3
#: adjudications where 21 exist, and missed the merge for the very notes
#: manuscript that prompted the question.
#:
#: `.consensus-overrides.json` is deliberately absent: the adjudication runner
#: writes it *alongside* its result, so counting it would file two runs for one
#: adjudication.
ARTIFACTS = {
    ".detailed-knowledge.json": "extraction",
    ".independent-review.json": "review",
    ".adjudication.json": "adjudication",
    ".ai-adjudication.json": "adjudication",
    ".adjudication-v2.json": "adjudication",
    ".reviewed-candidate.json": "merge",
    ".consensus-applied.json": "merge",
}


def _read(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _artifact_stage(path: Path) -> Optional[str]:
    for suffix, stage in ARTIFACTS.items():
        if path.name.endswith(suffix):
            return stage
    return None


#: Which block records the generation time for each stage's artifact. Read per
#: stage rather than by trying each in turn: a `reviewed-candidate` carries the
#: `extraction` block of the package it was built from, so a search would give
#: every merge the time of its own input -- making it look older than the thing
#: it consumed, and therefore permanently stale.
TIME_BLOCKS = {
    "extraction": ("extraction",),
    "review": ("reviewer",),
    "adjudication": ("adjudicator",),
    "merge": (),
}


def _generated_at(stage: str, payload: dict[str, Any], path: Path) -> tuple[datetime, str]:
    """When this artifact was produced, and how confident that is.

    A merge artifact records no time of its own -- only the adjudication
    fingerprint it applied -- so its file mtime is the best available evidence.
    The distinction is kept rather than smoothed over: a time taken from the
    filesystem can be wrong in ways a recorded `generated_at` cannot.
    """

    for section in TIME_BLOCKS.get(stage, ()):
        value = (payload.get(section) or {}).get("generated_at")
        if value:
            try:
                return datetime.fromisoformat(str(value)), "recorded"
            except ValueError:
                continue
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc), "file_mtime"


def _subject(payload: dict[str, Any], path: Path, cache: dict[Path, Optional[str]]) -> Optional[str]:
    """Which source this artifact is about.

    A knowledge package carries its source document and answers directly. A
    review or an adjudication does not: it records the `package_path` it read
    and nothing else about the sermon, so the subject is resolved by following
    that link to the package. Without this, both stages backfilled to nothing
    and their columns stayed empty while 41 artifacts sat on disk.
    """

    documents = payload.get("source_documents") or []
    if documents:
        key = document_row_key(documents[0])
        if key:
            return key

    source = payload.get("source") or {}
    for field in ("transcript_id", "source_id"):
        value = source.get(field)
        if value:
            return document_row_key({"transcript_id": value, "source_id": value})

    # `transcript_paths` names the transcript file itself, and a published
    # transcript is stored under its catalog id -- so its stem is the row key,
    # with no lookup at all. Preferred over `package_path`, which in the older
    # reviews is a relative path from a working directory that no longer exists.
    transcript_paths = source.get("transcript_paths") or {}
    if isinstance(transcript_paths, dict) and transcript_paths:
        first = sorted(str(value) for value in transcript_paths.values())[0]
        key = key_from_source_path(first)
        if key:
            return key

    package_path = source.get("package_path")
    if package_path:
        return _package_subject(Path(str(package_path)), path, cache)
    return None


def _package_subject(
    package_path: Path, artifact_path: Path, cache: dict[Path, Optional[str]]
) -> Optional[str]:
    """The subject of the package an artifact was taken against.

    The recorded path is absolute and can be stale -- packages have been moved
    between staging layouts. So a missing path falls back to the sibling package
    with the same `<slug>-<hash>` stem, which is how these artifacts are named
    and stored.
    """

    if package_path in cache:
        return cache[package_path]
    candidate = package_path
    if not candidate.is_file():
        # These artifacts are named `<slug>-<hash>.<kind>.json` and the package
        # they were taken against carries the same stem, so look for it beside
        # the artifact and in the sibling `detailed-extractions/` that most of
        # these layouts use.
        stem = artifact_path.name.split(".")[0]
        for directory in (
            artifact_path.parent,
            artifact_path.parent.parent / "detailed-extractions",
            artifact_path.parent.parent,
        ):
            sibling = directory / f"{stem}.detailed-knowledge.json"
            if sibling.is_file():
                candidate = sibling
                break
    payload = _read(candidate) if candidate.is_file() else None
    documents = (payload or {}).get("source_documents") or []
    key = document_row_key(documents[0]) if documents else None
    cache[package_path] = key
    return key


def _quality(stage: str, payload: dict[str, Any]) -> dict[str, Any]:
    if stage == "extraction":
        coverage = payload.get("coverage") or {}
        if not coverage.get("available"):
            # Packages written before the sentence ledger ran carry no coverage.
            # Absent, not zero: the page must not draw a 0% bar for a package
            # nobody measured.
            return {"available": False, "reason": "not_measured_at_the_time"}
        prose = (coverage.get("by_category") or {}).get("prose") or {}
        return {
            "available": True,
            "sentences": coverage.get("sentences"),
            "represented": coverage.get("represented"),
            "excluded": coverage.get("excluded"),
            "unprocessed": coverage.get("unprocessed"),
            "prose_represented": prose.get("represented"),
            "prose_total": prose.get("total"),
            "prose_pct": prose.get("represented_pct"),
        }
    if stage == "review":
        return dict(payload.get("routing_summary") or {})
    if stage == "adjudication":
        return dict(payload.get("summary") or {})
    return {}


def _fingerprint(stage: str, payload: dict[str, Any]) -> dict[str, Any]:
    for section in ("extraction", "reviewer", "adjudicator"):
        block = payload.get(section) or {}
        if block.get("fingerprint_sha256"):
            return {
                "fingerprint_sha256": block["fingerprint_sha256"],
                "source_sha256": block.get("source_sha256"),
                "prompt_sha256": block.get("prompt_sha256"),
            }
    return {}


def collect(staging: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Every artifact under staging that stands for a run, oldest first.

    Returns the artifacts it could place and the paths it could not. The second
    list is reported, never discarded: an artifact whose source cannot be
    resolved is a run missing from the overview, and a silent drop here looks
    exactly like work that never happened.
    """

    unresolved: list[str] = []
    found: list[dict[str, Any]] = []
    package_cache: dict[Path, Optional[str]] = {}
    for path in sorted(staging.rglob("*.json")):
        # `*-generations/` holds rejected attempts and superseded copies kept
        # for audit. They are not runs of the pipeline's stages.
        if any(part.endswith("generations") for part in path.parts):
            continue
        stage = _artifact_stage(path)
        if stage is None:
            continue
        payload = _read(path)
        if not payload:
            continue
        subject = _subject(payload, path, package_cache)
        if not subject:
            unresolved.append(str(path))
            continue
        usage = payload.get("usage") or []
        cost = price_usage(usage, (payload.get("extraction") or {}).get("model_id"))
        generated_at, time_source = _generated_at(stage, payload, path)
        found.append({
            "path": path,
            "stage": stage,
            "subject": subject,
            "generated_at": generated_at,
            "time_source": time_source,
            "usage": usage,
            "cost": cost if usage else None,
            "quality": _quality(stage, payload),
            "inputs": _fingerprint(stage, payload),
            "model_id": (payload.get("extraction") or payload.get("reviewer") or {}).get("model_id"),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    found.sort(key=lambda row: row["generated_at"])
    return found, unresolved


def apply(rows: Iterable[dict[str, Any]], database_url: str) -> dict[str, int]:
    import psycopg

    counts = {"imported": 0, "already_present": 0}
    with psycopg.connect(database_url, autocommit=True) as conn:
        for row in rows:
            with conn.cursor() as cursor:
                cursor.execute(
                    """SELECT 1 FROM wang_knowledge.pipeline_runs
                        WHERE metadata->>'backfilled_sha256' = %s""",
                    (row["sha256"],),
                )
                if cursor.fetchone():
                    counts["already_present"] += 1
                    continue
                started = row["generated_at"]
                cost = row["cost"]
                cursor.execute(
                    """INSERT INTO wang_knowledge.pipeline_runs
                        (run_id, subject_kind, subject_id, source_ids, stage, trigger,
                         triggered_by, status, started_at, finished_at, heartbeat_at,
                         model_id, usage, cost_usd, price_version, quality, input_sha256,
                         output_paths, metadata)
                       VALUES (%s,'source',%s,%s,%s,'cli',NULL,'succeeded',%s,%s,%s,
                               %s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        new_run_id(), row["subject"], [row["subject"]], row["stage"],
                        started, started, started,
                        row["model_id"],
                        json.dumps(row["usage"], ensure_ascii=False),
                        cost.cost_usd if cost else None,
                        cost.price_version if cost else None,
                        json.dumps(row["quality"], ensure_ascii=False),
                        json.dumps(row["inputs"], ensure_ascii=False),
                        [str(row["path"])],
                        json.dumps({
                            "backfilled": True,
                            "backfilled_sha256": row["sha256"],
                            "backfilled_from": str(row["path"]),
                            "time_source": row["time_source"],
                            "note": "Imported from the artifact; this run predates the ledger.",
                        }, ensure_ascii=False),
                    ),
                )
                counts["imported"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--database-url")
    args = parser.parse_args()

    paths = wang_platform_paths()
    rows, unresolved = collect(paths.staging)
    summary: dict[str, Any] = {
        "artifacts": len(rows),
        "by_stage": {
            stage: sum(1 for row in rows if row["stage"] == stage)
            for stage in sorted({row["stage"] for row in rows})
        },
        "distinct_sources": len({row["subject"] for row in rows}),
        "with_measured_cost": sum(1 for row in rows if row["cost"] and row["cost"].cost_usd),
        "time_from_file_mtime": sum(1 for row in rows if row["time_source"] == "file_mtime"),
        "unresolved": len(unresolved),
        "unresolved_sample": unresolved[:10],
    }
    if not args.apply:
        summary["applied"] = False
        summary["sample"] = [
            {"stage": row["stage"], "subject": row["subject"],
             "generated_at": row["generated_at"].isoformat() if row["generated_at"] else None}
            for row in rows[:8]
        ]
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    url = args.database_url or os.getenv("KNOWLEDGE_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        parser.error("KNOWLEDGE_DATABASE_URL is required to apply")
    summary.update(apply(rows, url))
    summary["applied"] = True
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
