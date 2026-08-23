"""The sermon pipeline overview: one row per source, read from the ledger.

Deliberately not a directory scan.  The 28 extraction packages on disk sit
under seven staging layouts and cover 19 sources, one of them twice; a scan
cannot tell a current package from a superseded one, cannot say what anything
cost, and starts lying the moment somebody writes a batch into an eighth
layout.  Every stage cell here resolves to a row in `wang_knowledge.pipeline_runs`.

The consequence, stated plainly because the page has to admit it: runs that
happened before the ledger existed are not in the ledger, so most cells read
"never run" on the day this ships even where a package exists on disk.  That is
the true statement.  `warnings` carries the count of packages with no matching
run so the gap is visible rather than implied.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from fastapi import APIRouter, HTTPException

from backend.config.wang_platform_paths import wang_platform_paths
from backend.pipeline.model_prices import price_table_for
from backend.pipeline.source_keys import document_row_key


router = APIRouter(prefix="/admin/wang/operations", tags=["wang-admin"])

SCHEMA_VERSION = "wang-operations-overview.v1"

#: The order the columns appear in, and the order work happens in.
# `cross_section` sits between extraction and review because that is where it
# runs and what it reads. It had no column until now, which is how it came to be
# run for one 母本 and skipped for the sermon extracted two hours later without
# anybody noticing: a stage with no cell cannot be seen to be missing.
SERMON_STAGES = (
    "extraction", "cross_section", "review", "adjudication", "merge", "ingest",
)

#: A run still marked `running` whose heartbeat stopped this long ago is treated
#: as interrupted.  A deploy is `launchctl unload` on the API job, so a run
#: really can die without writing its own terminal status.  This is a guess and
#: the page says so -- a single model call can legitimately block for a while.
STALE_HEARTBEAT = timedelta(minutes=10)


def _data_base() -> Path:
    value = os.getenv("DATA_BASE_DIR")
    if not value:
        raise HTTPException(status_code=503, detail="DATA_BASE_DIR is required")
    return Path(value).expanduser().resolve()


def _database_url() -> Optional[str]:
    return os.getenv("KNOWLEDGE_DATABASE_URL") or os.getenv("DATABASE_URL")


def _sha256_file(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _load_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# -- the row universe ------------------------------------------------------


def _sermon_rows(paths: Any, data_base: Path) -> list[dict[str, Any]]:
    """Every sermon in the catalog, extracted or not.

    Sermons with no published transcript stay on the list rather than being
    filtered out: "this one still needs a transcript" is work the queue has to
    show, and hiding it turns the overview into a progress bar for the part
    already started.
    """

    catalog = _load_json(paths.sermon_catalog) or {}
    transcripts = data_base / "script_published"
    rows: list[dict[str, Any]] = []
    for record in catalog.get("records") or []:
        source_id = str(record.get("transcript_id") or "")
        if not source_id:
            continue
        source_path = transcripts / f"{source_id}.json"
        # Where this sermon sits in scripture, from the same field the sermon
        # centre and the coverage page read, so the three cannot disagree.
        passage = record.get("catalog_primary_passage") or {}
        rows.append({
            "source_id": source_id,
            "kind": "sermon",
            "title": record.get("title") or source_id,
            "series": record.get("series_title"),
            "year": record.get("year"),
            "book": passage.get("book"),
            "chapter": passage.get("chapter"),
            "verse_start": passage.get("verse_start"),
            "topics": list(record.get("topics") or []),
            "source_path": source_path if source_path.is_file() else None,
        })
    return rows


def _notes_rows(data_base: Path) -> list[dict[str, Any]]:
    """Every project published through a notes-to-manuscript series.

    A directory is storage, not publication.  The public manuscript-series
    pages get their membership from ``series_db.json`` lecture
    ``project_ids``, so the operations overview must read the same authority.
    Otherwise abandoned local projects with a ``unified_source.md`` become
    phantom sources even though no reader-facing series includes them.

    Membership and availability are intentionally separate: a linked project
    stays visible while it awaits ``final.md``, just as a catalogued sermon
    stays visible while its transcript awaits publication.  ``final.md`` is
    the only manuscript the extraction pipeline reads and therefore the only
    file that can supply the row's source SHA.
    """

    root = data_base / "notes_to_surmon"
    rows: list[dict[str, Any]] = []
    if not root.is_dir():
        return rows

    series_db = _load_json(root / "series_db.json")
    if not isinstance(series_db, list):
        return rows

    project_ids: list[str] = []
    seen: set[str] = set()
    for series in series_db:
        if not isinstance(series, dict):
            continue
        # Missing project_type is the legacy spelling of sermon_note, matching
        # lecture_manager. Fellowship Transcript is normalized to transcript
        # there and must not create a second lineage here.
        project_type = series.get("project_type") or "sermon_note"
        if project_type != "sermon_note":
            continue
        for lecture in series.get("lectures") or []:
            if not isinstance(lecture, dict):
                continue
            for value in lecture.get("project_ids") or []:
                project_id = str(value or "").strip()
                if project_id and project_id not in seen:
                    seen.add(project_id)
                    project_ids.append(project_id)

    for project_id in project_ids:
        project = root / project_id
        manuscript_path = project / "final.md"
        manuscript = manuscript_path if manuscript_path.is_file() else None
        meta = _load_json(project / "meta.json") or {}
        placement = _notes_placement(str(meta.get("bible_verse") or ""))
        rows.append({
            "source_id": project_id,
            "kind": "notes_manuscript",
            "title": meta.get("title") or project_id,
            "series": meta.get("bible_verse"),
            "year": None,
            "book": placement["book"],
            "chapter": placement["chapter"],
            "verse_start": None,
            "topics": [],
            "manuscript_file": manuscript.name if manuscript else None,
            "source_path": manuscript,
        })
    return rows


def corpus_rows(paths: Any, data_base: Path) -> list[dict[str, Any]]:
    """Every source this platform knows about, extracted or not.

    Sermons and notes manuscripts are discovered differently -- one from the
    catalog, one from manuscript-series membership -- and any page that needs
    "how many documents are there" needs both. Naming the pair once keeps a
    second page from answering that question with a different number.
    """

    return _sermon_rows(paths, data_base) + _notes_rows(data_base)


def _notes_placement(verse: str) -> dict[str, Any]:
    """Where a manuscript sits in scripture, from its own metadata.

    A manuscript is not a sermon and is not in the catalog, so `bible_verse` is
    the only statement anyone has made about its passage. `太 16` resolves to a
    chapter; a bare `太` resolves to a book and stays there -- guessing the
    chapter would make a judgement nobody made.
    """

    from backend.api.sermon_search.bible_refs import ALIAS_TO_BOOK, normalize_ref

    text = (verse or "").strip()
    if not text:
        return {"book": None, "chapter": None}
    reference = normalize_ref(text)
    if reference is not None:
        return {"book": reference.book_zh, "chapter": reference.chapter_start}
    alias = ALIAS_TO_BOOK.get(text.lower())
    return {"book": alias[1] if alias else None, "chapter": None}


# -- the ledger ------------------------------------------------------------


def _load_runs() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every recorded run, plus any warnings about reading them."""

    url = _database_url()
    if not url:
        return [], [{
            "code": "ledger_unavailable",
            "message": "KNOWLEDGE_DATABASE_URL is not set, so no run history can be read.",
        }]
    try:
        import psycopg
    except ImportError:
        return [], [{"code": "ledger_unavailable", "message": "psycopg is not installed."}]
    try:
        with psycopg.connect(url) as conn, conn.cursor() as cursor:
            cursor.execute(
                """SELECT run_id, subject_kind, subject_id, source_ids, stage, trigger,
                          triggered_by, status, started_at, finished_at, heartbeat_at,
                          model_id, cost_usd, price_version, quality, input_sha256,
                          output_paths, error_message,
                          -- `command` is what a run that died before recording
                          -- an output still says about where it was writing.
                          command
                     FROM wang_knowledge.pipeline_runs
                    ORDER BY started_at"""
            )
            columns = [column.name for column in cursor.description]
            rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
    except Exception as exc:  # pragma: no cover - depends on deployment
        return [], [{
            "code": "ledger_unavailable",
            "message": f"Could not read the run ledger: {exc}",
        }]
    return rows, []


def is_scratch_run(run: dict[str, Any], staging_root: Path) -> bool:
    """A run whose output never reached the canonical staging tree.

    Someone comparing extraction models writes to a scratch directory on
    purpose: the point is to look at the result, not to feed it downstream. But
    the run is still filed against the real source, so it lands as that
    source's newest extraction -- and every later stage reads `stale`, because
    the thing they consumed has apparently moved. It has not. Nothing consumed
    this.

    A finished, ingested 母本 was pushed to 舊 across four stages this way, by
    a model comparison in another session's scratchpad that produced a file the
    pipeline has never read. The run stays in the runs list, because it happened
    and it cost money; it just stops speaking for the source's state.

    A run with no recorded outputs is not scratch. Absence of evidence is the
    normal state for every row written before outputs were recorded, and
    treating those as experiments would erase most of the history.
    """

    root = str(staging_root)
    outputs = [str(path) for path in (run.get("output_paths") or []) if path]
    if outputs:
        return all(not output.startswith(root) for output in outputs)
    # Died before it could record an output. The command still says where it
    # was going to write, which is the same evidence one step earlier.
    command = str(run.get("command") or "")
    match = re.search(r"--output-dir[= ]+(\S+)", command)
    if match:
        return not match.group(1).startswith(root)
    # No output, and no command either. `run_ledger.current_command` records
    # `shlex.join(sys.argv)`, so a pipeline run always names the module it was
    # started with -- `-m backend.pipeline.…`. A bare `-` is what argv holds
    # when the code was piped in on stdin: somebody driving the internals from
    # a heredoc, which is how the model comparisons in the neighbouring
    # worktree are run. Such a run produced nothing in the canonical tree and
    # cannot say where it was going, so it is not evidence about this source's
    # pipeline. One of them, a failed benchmark on a fourth model, was showing
    # 抽取 as 失敗 on a 母本 whose extraction has succeeded three times.
    return command.strip() in {"", "-"}


def _as_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse a stored ISO timestamp, or None if it is missing or unreadable."""

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _ingested_sources() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Which sources the authoring store actually holds, keyed as the rows are.

    入庫 is the one stage that already had a trustworthy history before this
    ledger existed: the store either holds a live `source_documents` record for
    a sermon or it does not, and that is what "ingested" means. Reading only the
    ledger made 25 already-ingested sources report "never run", which is not a
    cautious answer -- it is a wrong one, contradicted by the same database the
    page is connected to.
    """

    url = _database_url()
    if not url:
        return {}, []
    try:
        import psycopg
    except ImportError:
        return {}, []
    try:
        with psycopg.connect(url) as conn, conn.cursor() as cursor:
            cursor.execute(
                """SELECT object_id, payload, revision, updated_at
                     FROM wang_knowledge.objects
                    WHERE collection='source_documents' AND retired_at IS NULL"""
            )
            rows = cursor.fetchall()
            # The document record is metadata -- title, path, sha -- and it
            # stays at revision 1 through every re-ingest that does not change
            # them. Reporting its revision answered "how often was this row
            # rewritten", which is not what 入庫 is asking. The material is
            # what moves: 生命 sat at rev 1 from 13 Aug while its fragments
            # were rewritten twice afterwards.
            cursor.execute(
                """SELECT payload->>'source_id' AS source_id,
                          COUNT(*) AS fragments,
                          MAX(updated_at) AS last_written
                     FROM wang_knowledge.objects
                    WHERE collection='source_fragments' AND retired_at IS NULL
                      AND payload->>'source_id' IS NOT NULL
                 GROUP BY 1"""
            )
            material = {
                str(source_id): {"fragments": fragments, "last_written": last_written}
                for source_id, fragments, last_written in cursor.fetchall()
            }
    except Exception as exc:  # pragma: no cover - depends on deployment
        return {}, [{
            "code": "store_unreadable",
            "message": f"Could not read the authoring store, so 入庫 is unknown: {exc}",
        }]
    held: dict[str, dict[str, Any]] = {}
    for object_id, payload, revision, updated_at in rows:
        document = dict(payload or {})
        key = document_row_key(document) or str(object_id)
        found = material.get(str(object_id)) or {}
        # Fall back to the document's own timestamp only when the store holds
        # no material for it: then the document really is all there is.
        written = found.get("last_written") or updated_at
        held[key] = {
            "source_id": str(object_id),
            "revision": revision,
            "fragments": found.get("fragments") or 0,
            "updated_at": written.isoformat() if written else None,
            "document_updated_at": updated_at.isoformat() if updated_at else None,
        }
    return held, []


def _ingest_inputs() -> dict[str, str]:
    """Which file each applied ingest actually read, keyed by extraction hash.

    `change_sets` records the path, and the path is the whole point: applying a
    knowledge package is only as good as the package handed to it. An ingest
    pointed at `.detailed-knowledge.json` loads the pre-adjudication claims even
    when a `.consensus-applied.json` sits beside it, and the store ends up
    holding text two models already agreed to correct.
    """

    url = _database_url()
    if not url:
        return {}
    try:
        import psycopg
    except ImportError:
        return {}
    try:
        with psycopg.connect(url) as conn, conn.cursor() as cursor:
            cursor.execute(
                """SELECT package_id, metadata->>'input_path'
                     FROM wang_knowledge.change_sets
                    WHERE status='applied' AND metadata->>'input_path' IS NOT NULL"""
            )
            rows = cursor.fetchall()
    except Exception:  # pragma: no cover - depends on deployment
        return {}
    inputs: dict[str, str] = {}
    for package_id, input_path in rows:
        match = re.search(r"([0-9a-f]{12})$", str(package_id or ""))
        if match:
            inputs[match.group(1)] = str(input_path)
    return inputs


#: Suffixes that mean "the consensus has been applied to this package".
MERGED_SUFFIXES = (".reviewed-candidate.json", ".consensus-applied.json")


def _effective_status(run: dict[str, Any], now: datetime) -> str:
    """`interrupted` is inferred here, never written by the run itself.

    A process that is killed does not get to record its own death, so a row left
    at `running` with a stopped heartbeat is the only evidence there is.
    """

    status = str(run.get("status") or "")
    if status not in {"running", "queued"}:
        return status
    heartbeat = run.get("heartbeat_at")
    if heartbeat and now - heartbeat > STALE_HEARTBEAT:
        return "interrupted"
    return status


def _cell(
    runs: list[dict[str, Any]],
    *,
    stage: str,
    current_source_sha: Optional[str],
    upstream_finished: Optional[datetime],
    upstream_in_flight: bool = False,
) -> dict[str, Any]:
    """One stage's cell for one source: a state, a quality, and the last run.

    The four states are `current`, `stale`, `never`, and `failed`.  `stale` is
    the load-bearing one, and it means something different at each end of the
    chain.  Extraction reads the source, so it goes stale when the source text
    changes.  Every later stage reads the stage before it, so it goes stale when
    an upstream stage has succeeded more recently than it did -- a review of an
    extraction that has since been re-run is reviewing something nobody has any
    more.  Comparing every stage against the source sha would have been wrong in
    both directions: no later stage records one, so all of them would read stale
    forever, and none would notice its actual input moving.
    """

    if not runs:
        return {"state": "never", "quality": None, "run": None}
    last_success = next(
        (run for run in reversed(runs) if run["effective_status"] == "succeeded"), None
    )
    # An interrupted run never reported anything: the process was killed and
    # the status was inferred from a stale heartbeat. That is an absence of a
    # verdict, not a verdict, so it must not overwrite one. A `failed` run is
    # different -- the runner lived long enough to say so, and that is worth
    # showing over an older success.
    latest = next(
        (
            run for run in reversed(runs)
            if run["effective_status"] != "interrupted" or last_success is None
        ),
        runs[-1],
    )
    summary = _run_summary(latest)
    if latest["effective_status"] in {"failed", "interrupted", "cancelled"}:
        return {
            "state": "failed",
            "quality": None,
            "run": summary,
            "had_earlier_success": last_success is not None,
        }
    if latest["effective_status"] in {"running", "queued"}:
        return {"state": latest["effective_status"], "quality": None, "run": summary}
    if last_success is None:
        return {"state": "never", "quality": None, "run": summary}

    success = _run_summary(last_success)
    quality = last_success.get("quality") or None

    def stale(reason: str) -> dict[str, Any]:
        if upstream_in_flight:
            return _pending({"state": "stale", "quality": quality, "run": success})
        return {"state": "stale", "reason": reason, "quality": quality, "run": success}

    if stage == "extraction":
        recorded = (last_success.get("input_sha256") or {}).get("source_sha256")
        if not recorded:
            # Nothing to compare against. Not evidence of freshness -- evidence
            # that this run cannot prove it, which is the same to-do as a moved
            # input. Every package produced before the ledger existed lands here.
            return stale("no_recorded_input")
        if current_source_sha and recorded != current_source_sha:
            return stale("source_changed")
        return {"state": "current", "quality": quality, "run": success}

    finished = last_success.get("finished_at")
    if upstream_finished and finished and upstream_finished > finished:
        return stale("upstream_rerun")
    if upstream_in_flight:
        return _pending({"state": "current", "quality": quality, "run": success})
    return {"state": "current", "quality": quality, "run": success}


def _pending(cell: dict[str, Any]) -> dict[str, Any]:
    """A result that an in-flight upstream run is in the middle of replacing.

    Showing the last run's verdict while the stage it read from is re-running
    invites the reading that cost the most here: a row with `執行中` on
    extraction and four green cells behind it looks done, and every one of
    those cells is about to be superseded. The number is not wrong yet -- it is
    about to stop being about anything -- so it moves to the tooltip and the
    cell goes grey.
    """

    return {
        "state": "pending",
        "reason": "upstream_running",
        "quality": None,
        "run": cell.get("run"),
        "superseded": {"state": cell["state"], "quality": cell.get("quality")},
        **({"store": cell["store"]} if cell.get("store") else {}),
    }


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    started = run.get("started_at")
    finished = run.get("finished_at")
    return {
        "run_id": run.get("run_id"),
        "status": run["effective_status"],
        "trigger": run.get("trigger"),
        "triggered_by": run.get("triggered_by"),
        "started_at": started.isoformat() if started else None,
        "seconds": int((finished - started).total_seconds()) if started and finished else None,
        "model_id": run.get("model_id"),
        "cost_usd": float(run["cost_usd"]) if run.get("cost_usd") is not None else None,
        "output_paths": list(run.get("output_paths") or []),
        "error_message": (str(run["error_message"]).splitlines() or [None])[0]
        if run.get("error_message") else None,
    }


# -- articles --------------------------------------------------------------


#: `DK-<12 hex>-CL007`: the middle segment is the extraction the claim came
#: from, which is how a claim in an article traces back to a source.
_CLAIM_SOURCE = re.compile(r"\bDK-([0-9a-f]{12})-")


def _snapshot_source_keys(snapshot: dict[str, Any]) -> dict[str, str]:
    """Map each extraction hash in a snapshot to the row key of its source.

    The snapshot is the knowledge the author *could* draw on -- all 25 sources
    in the store -- so it is a lookup table here and never the citation set.
    Reading it as the citation set claimed every source fed every article.
    """

    keys: dict[str, str] = {}
    for document in snapshot.get("source_documents") or []:
        source_id = str(document.get("source_id") or "")
        if not source_id:
            continue
        row_key = document_row_key(document) or source_id
        # A sermon's `SRC-…-3d012c24a542` carries the digest in its id, and
        # parsing it was the whole of this lookup. A notes manuscript's id is
        # `notes_manuscript:16_章_-_生命`, which carries none, so all three 母本
        # fell out -- and they are the sources the published articles are
        # actually written from, so every one of them showed no articles at all.
        #
        # The digest is `sha256(source_id)[:12]`, the same expression the
        # extractor uses to mint `DK-…`, so it can be derived rather than
        # scraped. Both routes are registered: derived for the ids that have no
        # digest in them, parsed for the ids whose digest was minted from
        # something else.
        keys[hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:12]] = row_key
        match = re.search(r"-([0-9a-f]{12})$", source_id)
        if match:
            keys[match.group(1)] = row_key
    return keys


def _article_citations(paths: Any) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    """Which sources each published article actually stands on.

    Many-to-many: the Matt 16:13-20 article cites eight sources, and one sermon
    can feed several articles. So this is a column on the sermon table, not a
    stage of it.

    The authority is the Program Audit's paragraph provenance -- the claims the
    reader-visible paragraphs are grounded in. Not the knowledge snapshot (that
    is everything available, which would mark every source as cited by every
    article) and not the manifest's material dispositions (those are the few
    items explicitly ruled on, which undercounts to one or two).
    """

    citations: dict[str, list[str]] = {}
    warnings: list[dict[str, Any]] = []
    root = paths.repository / "editorial_drafts"
    if not root.is_dir():
        return citations, warnings
    unmapped: set[str] = set()
    for manifest_path in sorted(root.glob("*/editorial-draft-manifest.json")):
        manifest = _load_json(manifest_path)
        if not manifest:
            continue
        for draft in manifest.get("drafts") or []:
            draft_id = str(draft.get("draft_id") or manifest_path.parent.name)
            config = draft.get("audit_config") or {}
            audit = _load_json(
                manifest_path.parent / str(config.get("audit_output_path") or "program-audit.json")
            )
            snapshot = _load_json(
                manifest_path.parent
                / str(draft.get("presentation_package_path") or "knowledge-snapshot.json")
            )
            if not audit or not snapshot:
                warnings.append({
                    "code": "article_sources_unreadable",
                    "message": (
                        f"{draft_id}: the program audit or knowledge snapshot could not be "
                        "read, so its cited sources are not shown on any row."
                    ),
                })
                continue
            lookup = _snapshot_source_keys(snapshot)
            provenance = json.dumps(audit.get("paragraph_provenance") or [], ensure_ascii=False)
            for digest in sorted(set(_CLAIM_SOURCE.findall(provenance))):
                key = lookup.get(digest)
                if not key:
                    unmapped.add(f"{draft_id}:{digest}")
                    continue
                citations.setdefault(key, [])
                if draft_id not in citations[key]:
                    citations[key].append(draft_id)
    if unmapped:
        warnings.append({
            "code": "article_sources_unmapped",
            "message": (
                f"{len(unmapped)} cited extraction(s) could not be matched to a source row, "
                "so those articles are undercounted in the 文章 column."
            ),
            "detail": sorted(unmapped)[:20],
        })
    return citations, warnings


# -- the endpoint ----------------------------------------------------------


@router.get("/overview")
def overview() -> dict[str, Any]:
    """One row per source, recomputed on every read.

    Nothing is cached and no second copy of the state is stored: a progress
    table that can disagree with the ledger is worse than no table.
    """

    data_base = _data_base()
    paths = wang_platform_paths(data_base)
    now = datetime.now(timezone.utc)

    rows = corpus_rows(paths, data_base)
    runs, warnings = _load_runs()
    for run in runs:
        run["effective_status"] = _effective_status(run, now)

    staging_root = paths.claim_layer_staging
    by_source: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for run in runs:
        run["scratch"] = is_scratch_run(run, staging_root)
        if run["scratch"]:
            # Visible in the runs list, silent in the stage cells.
            continue
        for source_id in run.get("source_ids") or []:
            by_source.setdefault(str(source_id), {}).setdefault(str(run["stage"]), []).append(run)

    ingested, store_warnings = _ingested_sources()
    warnings.extend(store_warnings)
    ingest_inputs = _ingest_inputs()

    citations, citation_warnings = _article_citations(paths)
    warnings.extend(citation_warnings)

    known = {row["source_id"] for row in rows}
    orphaned = sorted(source_id for source_id in by_source if source_id not in known)
    if orphaned:
        warnings.append({
            "code": "runs_outside_the_catalog",
            "message": (
                f"{len(orphaned)} recorded source(s) are not in the catalog or notes "
                "directory, so their runs appear in the run list but on no row here."
            ),
            "detail": orphaned[:20],
        })

    payload_rows: list[dict[str, Any]] = []
    for row in rows:
        source_path: Optional[Path] = row.pop("source_path")
        source_sha = _sha256_file(source_path) if source_path else None
        stage_runs = by_source.get(row["source_id"], {})
        stages: dict[str, Any] = {}
        # The most recent upstream success, carried forward down the chain so
        # each stage can tell whether the thing it consumed has moved since.
        upstream_finished: Optional[datetime] = None
        # Set the moment any stage is found running or queued, and never
        # cleared: everything downstream of a live run is waiting on it.
        upstream_in_flight = False
        for stage in SERMON_STAGES:
            if stage == "extraction" and source_path is None:
                stages[stage] = {"state": "no_source", "quality": None, "run": None}
                continue
            cell = _cell(
                stage_runs.get(stage) or [],
                stage=stage,
                current_source_sha=source_sha,
                upstream_finished=upstream_finished,
                upstream_in_flight=upstream_in_flight,
            )
            if stage == "ingest" and cell["state"] == "never":
                # The store outranks an empty ledger here: it is the authority
                # for this stage, and it is answering about the same sources.
                held = ingested.get(row["source_id"])
                if held:
                    # But holding the source is not the same as holding what
                    # the pipeline has since produced. A source re-extracted
                    # and re-reviewed today, whose store record is from a run
                    # two weeks ago, is the exact case this column exists to
                    # surface -- and reading it green said the work was live
                    # when the claim layer was still the old one.
                    written = _as_datetime(held.get("updated_at"))
                    behind = (
                        upstream_finished is not None
                        and written is not None
                        and upstream_finished > written
                    )
                    cell = {
                        "state": "stale" if behind else "current",
                        "reason": "upstream_rerun" if behind else "from_store_not_ledger",
                        "quality": {
                            "revision": held["revision"],
                            "fragments": held.get("fragments") or 0,
                        },
                        "run": None,
                        "store": held,
                    }
            # `_cell` greys its own verdicts; only the store fallback happens
            # out here, so only that one needs greying at this level.
            if upstream_in_flight and cell["state"] in {"current", "stale"}:
                cell = _pending(cell)
            if cell["state"] in {"running", "queued"}:
                upstream_in_flight = True
            if stage == "ingest":
                # What the store holds now, whether or not a run wrote it. The
                # cell's job is to answer "what is in the store for this
                # source"; the change set's deltas answer "what moved", which
                # is a tooltip question.
                held = ingested.get(row["source_id"])
                if held and cell.get("quality") is not None:
                    cell["quality"] = {
                        **cell["quality"], "fragments": held.get("fragments") or 0,
                    }
            stages[stage] = cell
            for run in stage_runs.get(stage) or []:
                if run["effective_status"] == "succeeded" and run.get("finished_at"):
                    if upstream_finished is None or run["finished_at"] > upstream_finished:
                        upstream_finished = run["finished_at"]
        held = ingested.get(row["source_id"])
        payload_rows.append({
            **row,
            "source_available": source_path is not None,
            # The id `/admin/wang/source-coverage` knows this source by, which
            # is the knowledge package's own (`SRC-<slug>-<hash>`, or
            # `notes_manuscript:<project>`) and not the catalog id these rows
            # are keyed on. Null until a source has a claim layer, because that
            # page has nothing to show for one that does not.
            "coverage_source_id": held["source_id"] if held else None,
            "stages": stages,
            "articles": citations.get(row["source_id"], []),
        })

    # A later stage recorded without the one before it. Not a contradiction the
    # page invented: only 3 adjudication artifacts survive against 20 merges,
    # because that output was not always kept. Saying so is the difference
    # between a table with a visible gap and a table that looks wrong.
    # An ingest that read the un-merged package. The chain reads ✓ 合併 then
    # ✓ 入庫, which says the merged version is in the library -- so this has to
    # be called out or the row is worse than a blank.
    ingested_before_merge: list[str] = []
    for row in payload_rows:
        merge_cell = row["stages"]["merge"]
        if merge_cell["state"] not in {"current", "stale"}:
            continue
        merged_paths = [
            path for path in ((merge_cell.get("run") or {}).get("output_paths") or [])
        ]
        digests = {
            match.group(1)
            for path in merged_paths
            for match in [re.search(r"-([0-9a-f]{12})\.", path)]
            if match
        }
        for digest in digests:
            read = ingest_inputs.get(digest)
            if read and not read.endswith(MERGED_SUFFIXES):
                ingested_before_merge.append(f"{row['source_id']}: {Path(read).name}")
    if ingested_before_merge:
        warnings.append({
            "code": "ingested_before_the_consensus_was_applied",
            "message": (
                f"{len(ingested_before_merge)} 篇的入庫讀的是合併前的包，"
                "所以雙模型已同意的修正沒有進到主庫。合併那一格是 ✓，但主庫拿到的不是它。"
            ),
            "detail": ingested_before_merge[:20],
        })

    out_of_order: list[str] = []
    for row in payload_rows:
        for earlier, later in zip(SERMON_STAGES, SERMON_STAGES[1:]):
            if row["stages"][later]["state"] in {"current", "stale"} and row["stages"][earlier][
                "state"
            ] in {"never", "no_source"}:
                out_of_order.append(f"{row['source_id']}: {later} without {earlier}")
    if out_of_order:
        warnings.append({
            "code": "stage_recorded_without_the_one_before_it",
            "message": (
                f"{len(out_of_order)} 個階段有紀錄，但它的上一階段沒有——通常是那一步的產出當時沒有留檔，"
                "不表示它沒有跑過。"
            ),
            "detail": out_of_order[:20],
        })

    table = price_table_for(now)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "price_version": table.version,
        "price_effective": table.effective.isoformat(),
        "price_source": table.source,
        "stages": list(SERMON_STAGES),
        "summary": _summary(payload_rows, runs),
        "rows": payload_rows,
        "warnings": warnings,
    }


def _summary(rows: list[dict[str, Any]], runs: Iterable[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = {
        stage: {"current": 0, "stale": 0, "never": 0, "failed": 0,
                "running": 0, "queued": 0, "pending": 0, "no_source": 0}
        for stage in SERMON_STAGES
    }
    for row in rows:
        for stage, cell in row["stages"].items():
            state = cell["state"]
            counts[stage][state] = counts[stage].get(state, 0) + 1
    recorded = list(runs)
    spend = sum(float(run["cost_usd"]) for run in recorded if run.get("cost_usd") is not None)
    unpriced = sum(
        1 for run in recorded
        if run.get("cost_usd") is None and run["effective_status"] == "succeeded"
    )
    return {
        "rows": len(rows),
        "sermons": sum(1 for row in rows if row["kind"] == "sermon"),
        "notes_manuscripts": sum(1 for row in rows if row["kind"] == "notes_manuscript"),
        # Not "no source text": every one of these has a transcript sitting in
        # script_review. What they lack is a proofread, published one, which is
        # what extraction reads.
        "unproofread": sum(1 for row in rows if not row["source_available"]),
        "ingested": sum(
            1 for row in rows if row["stages"]["ingest"]["state"] in {"current", "stale"}
        ),
        "by_stage": counts,
        "runs_recorded": len(recorded),
        "spend_usd": round(spend, 4),
        "succeeded_runs_without_a_price": unpriced,
    }
