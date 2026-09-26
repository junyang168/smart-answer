"""Freeze the complete Matthew notes-manuscript repair denominator.

This preflight is deliberately model-free and read-only with respect to
PostgreSQL.  It reconciles the Matthew source catalog, the notes manuscript
files, and current knowledge records before a repair batch is allowed to spend
tokens.  Sources already referenced by CVP/Route master data are split from
ordinary source work because they require a coordinated semantic rebind.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import (
    SEMANTIC_REFERENCE_COLLECTIONS,
    PostgresKnowledgeStore,
    sha256_json,
)
from backend.pipeline.knowledge_source import markdown_blocks
from backend.pipeline.source_projection import (
    LOCATOR_SPACE,
    project_script,
    script_from_markdown_blocks,
)


SCHEMA_VERSION = "matthew-notes-source-repair-plan.v1"
OWNERSHIP_SCHEMA_VERSION = "wang-repair-output-ownership.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class MatthewNotesPreflightError(ValueError):
    """The current source census cannot be frozen safely."""


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _seal(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(payload, ensure_ascii=False))
    result.pop("artifact_sha256", None)
    result["artifact_sha256"] = sha256_json(result)
    return result


def _write_once(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = _canonical_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise MatthewNotesPreflightError(
                f"refusing to overwrite a different frozen artifact: {path}"
            )
        return
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with open(descriptor, "wb", closefd=True) as handle:
            handle.write(encoded)
            handle.flush()
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _catalog_notes_sources(catalog: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for raw in [
        *(catalog.get("source_directory") or []),
        *(catalog.get("book_level_sources") or []),
    ]:
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("source_type") or "") not in {
            "notes_to_manuscript",
            "notes_manuscript",
        }:
            continue
        source_id = str(raw.get("source_id") or "").strip()
        project_id = str(raw.get("project_id") or "").strip()
        if not source_id.startswith("notes_manuscript:") or not project_id:
            raise MatthewNotesPreflightError(
                "catalog notes source lacks a stable source_id/project_id"
            )
        current = rows.get(source_id)
        normalized = dict(raw)
        if current is not None and (
            str(current.get("project_id")) != project_id
            or str(current.get("title")) != str(normalized.get("title"))
        ):
            raise MatthewNotesPreflightError(
                f"catalog repeats {source_id} with different identity"
            )
        rows[source_id] = normalized
    if not rows:
        raise MatthewNotesPreflightError("catalog contains no Matthew notes sources")
    return rows


def _strings(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        result = {str(key) for key in value if isinstance(key, str)}
        for child in value.values():
            result.update(_strings(child))
        return result
    if isinstance(value, (list, tuple, set)):
        result: set[str] = set()
        for child in value:
            result.update(_strings(child))
        return result
    return {value} if isinstance(value, str) else set()


def _claim_source_ids(claim: Mapping[str, Any]) -> set[str]:
    return {
        str(row.get("source_id") or "")
        for row in claim.get("occurrences") or []
        if isinstance(row, Mapping) and str(row.get("source_id") or "")
    }


def _source_descriptor(
    *, row: Mapping[str, Any], notes_root: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_id = str(row["source_id"])
    project_id = str(row["project_id"])
    path = (notes_root / project_id / "final.md").resolve()
    meta_path = (notes_root / project_id / "meta.json").resolve()
    if not path.is_file():
        raise MatthewNotesPreflightError(f"catalog source is missing final.md: {source_id}")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MatthewNotesPreflightError(f"source is not UTF-8: {path}") from exc
    projection = project_script(script_from_markdown_blocks(markdown_blocks(text)))
    physical_sha = hashlib.sha256(raw).hexdigest()
    catalog_sha = str(((row.get("artifacts") or {}).get("manuscript_sha256") or ""))
    if catalog_sha and catalog_sha != physical_sha:
        raise MatthewNotesPreflightError(
            f"catalog manuscript SHA drifted for {source_id}: {catalog_sha} != {physical_sha}"
        )
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    batch_row = {
        "source_id": source_id,
        "source_type": "notes_manuscript",
        "source_path": str(path),
        "source_sha256": physical_sha,
        "title": str(row.get("title") or meta.get("title") or project_id),
        "project_id": project_id,
        "source_url": row.get("source_url"),
        "lineage": {
            "upstream_kind": "professor_notes",
            "transformation": "notes_to_manuscript",
            "fidelity_status": "current_passed",
            "meta_path": str(meta_path) if meta_path.is_file() else None,
            "source_pages": list(row.get("source_pages") or meta.get("pages") or []),
        },
    }
    projection_state = {
        "source_file_sha256": physical_sha,
        "source_body_sha256": projection.body_sha256,
        "source_text_sha256": projection.spoken_text_sha256,
        "editorial_structure_sha256": projection.editorial_structure_sha256,
        "editorial_topology_sha256": projection.editorial_topology_sha256,
        "body_row_count": len(projection.body_rows),
        "heading_count": len(projection.headings),
    }
    return batch_row, projection_state


def _batch(
    *, batch_id: str, purpose: str, rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "schema_version": "wang_research_batch_v1",
        "batch_id": batch_id,
        "purpose": purpose,
        "selection_note": (
            "Generated from a SHA-bound Matthew notes preflight. The source set "
            "must not be edited by hand after freeze."
        ),
        "semantic_assumption": "none",
        "candidate_generation_policy": {
            "derive_after_independent_extraction": True,
            "allow_unassigned_material": True,
            "allow_multiple_topic_candidates": True,
            "preserve_scripture_context": True,
            "separate_questions_from_topic_exposition": True,
        },
        "models": {
            "extraction": "gpt-5.6-sol",
            "extraction_reasoning_effort": "medium",
            "independent_review": "claude-sonnet-5",
            "adjudicator": "gpt-5.6-sol",
            "reconsideration": "claude-sonnet-5",
        },
        "transcript_ids": [],
        "sources": [dict(row) for row in rows],
    }


def build_repair_plan(
    *,
    catalog: Mapping[str, Any],
    catalog_sha256: str,
    notes_root: Path,
    database_rows: Sequence[Mapping[str, Any]],
    output_root: Path,
    runner_commit: str,
    worktree: Path,
    generated_at: str,
    batch_id_prefix: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    catalog_sources = _catalog_notes_sources(catalog)
    source_states: dict[str, dict[str, Any]] = {}
    claims: dict[str, dict[str, Any]] = {}
    semantic_rows: list[dict[str, Any]] = []
    snapshot_rows: list[dict[str, Any]] = []
    for raw in database_rows:
        collection = str(raw.get("collection") or "")
        object_id = str(raw.get("object_id") or "")
        payload = raw.get("payload")
        if not object_id or not isinstance(payload, Mapping):
            raise MatthewNotesPreflightError("database snapshot row is incomplete")
        state = {
            "collection": collection,
            "object_id": object_id,
            "revision": int(raw.get("revision") or 0),
            "content_sha256": str(raw.get("content_sha256") or ""),
            "payload": dict(payload),
        }
        if collection == "source_documents" and str(payload.get("source_type")) == "notes_manuscript":
            source_states[object_id] = state
        elif collection == "claims":
            if _claim_source_ids(payload) & set(catalog_sources):
                claims[object_id] = state
        elif collection in SEMANTIC_REFERENCE_COLLECTIONS:
            semantic_rows.append(state)
        else:
            continue
        snapshot_rows.append(
            {
                "collection": collection,
                "object_id": object_id,
                "revision": state["revision"],
                "content_sha256": state["content_sha256"],
            }
        )

    claims_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for state in claims.values():
        for source_id in sorted(_claim_source_ids(state["payload"]) & set(catalog_sources)):
            claims_by_source[source_id].append(state)
    semantic_strings = [
        (state, _strings(state["payload"])) for state in semantic_rows
    ]

    source_rows: list[dict[str, Any]] = []
    unlinked_batch_rows: list[dict[str, Any]] = []
    linked_batch_rows: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for source_id, catalog_row in sorted(catalog_sources.items()):
        batch_row, projection = _source_descriptor(row=catalog_row, notes_root=notes_root)
        current = source_states.get(source_id)
        source_claims = claims_by_source.get(source_id, [])
        claim_ids = {state["object_id"] for state in source_claims}
        references = [
            {
                "collection": state["collection"],
                "object_id": state["object_id"],
                "revision": state["revision"],
                "content_sha256": state["content_sha256"],
            }
            for state, strings in semantic_strings
            if strings & claim_ids
        ]
        statuses = Counter(
            str(state["payload"].get("review_status") or "<missing>")
            for state in source_claims
        )
        if current is None:
            action = "full_chain_new_source"
            reason_code = "CATALOG_AUTHORITY_MISSING_FROM_POSTGRES"
        else:
            payload = current["payload"]
            locator_space = str(payload.get("locator_space") or "")
            if locator_space and locator_space != LOCATOR_SPACE:
                action = "blocked"
                reason_code = "UNSUPPORTED_LOCATOR_SPACE"
                blockers.append({"source_id": source_id, "reason_code": reason_code})
            elif not locator_space and projection["heading_count"]:
                action = "full_chain_locator_space_migration"
                reason_code = "LEGACY_EDITORIAL_ROWS_REQUIRE_REEXTRACTION"
            elif (
                locator_space == LOCATOR_SPACE
                and str(payload.get("source_body_sha256") or "") == projection["source_body_sha256"]
                and str(payload.get("source_file_sha256") or "") == projection["source_file_sha256"]
                and str(payload.get("editorial_structure_sha256") or "") == projection["editorial_structure_sha256"]
            ):
                action = "current"
                reason_code = "CURRENT_EXACT_SOURCE_GENERATION"
            else:
                action = "full_chain_source_revision_changed"
                reason_code = "SOURCE_GENERATION_DRIFT_REQUIRES_REEXTRACTION"
        requires_rebind = bool(references)
        row = {
            "source_id": source_id,
            "project_id": batch_row["project_id"],
            "title": batch_row["title"],
            "catalog_scope": (
                "book_level_or_unscoped"
                if not list(catalog_row.get("assigned_chapters") or [])
                else "chapter_assigned"
            ),
            "assigned_chapters": list(catalog_row.get("assigned_chapters") or []),
            "action": action,
            "reason_code": reason_code,
            "projection": projection,
            "current_source_state": (
                {
                    "revision": current["revision"],
                    "content_sha256": current["content_sha256"],
                    "locator_space": current["payload"].get("locator_space"),
                    "source_sha256": current["payload"].get("source_sha256"),
                }
                if current else None
            ),
            "active_claim_count": len(source_claims),
            "claim_review_status_counts": dict(sorted(statuses.items())),
            "semantic_reference_count": len(references),
            "semantic_rebind_required": requires_rebind,
            "semantic_references": sorted(
                references, key=lambda item: (item["collection"], item["object_id"])
            ),
        }
        source_rows.append(row)
        if action != "current" and action != "blocked":
            if requires_rebind:
                linked_batch_rows.append(batch_row)
            else:
                unlinked_batch_rows.append(batch_row)

    disk_exclusions: list[dict[str, Any]] = []
    known_projects = {str(row["project_id"]) for row in catalog_sources.values()}
    for final_path in sorted(notes_root.glob("*/final.md")):
        project_id = final_path.parent.name
        if project_id in known_projects:
            continue
        meta_path = final_path.parent / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        if str(meta.get("project_type") or "") == "transcript":
            reason_code = "TRANSCRIPT_PROJECT_USES_SPOKEN_SOURCE_PIPELINE"
        else:
            reason_code = "UNEXPLAINED_DISK_NOTES_SOURCE"
            blockers.append(
                {"project_id": project_id, "reason_code": reason_code}
            )
        disk_exclusions.append(
            {
                "project_id": project_id,
                "path": str(final_path.resolve()),
                "source_file_sha256": hashlib.sha256(final_path.read_bytes()).hexdigest(),
                "project_type": meta.get("project_type"),
                "audit_passed": meta.get("audit_passed"),
                "reason_code": reason_code,
            }
        )

    unlinked_batch = _batch(
        batch_id=f"{batch_id_prefix}-UNLINKED",
        purpose="Migrate or add Matthew notes manuscripts without current Registry references.",
        rows=unlinked_batch_rows,
    )
    linked_batch = _batch(
        batch_id=f"{batch_id_prefix}-LINKED",
        purpose="Stage Matthew notes manuscripts that require coordinated Registry rebind before apply.",
        rows=linked_batch_rows,
    )
    plan = _seal(
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "status": "blocked" if blockers else "repair_required",
            "runner_commit": runner_commit,
            "worktree": str(worktree.resolve()),
            "output_root": str(output_root.resolve()),
            "catalog_path": str(Path(str(catalog.get("_path") or "")).resolve()) if catalog.get("_path") else None,
            "catalog_sha256": catalog_sha256,
            "database_snapshot_sha256": sha256_json(sorted(snapshot_rows, key=lambda item: (item["collection"], item["object_id"]))),
            "counts": {
                "catalog_authoritative_sources": len(catalog_sources),
                "active_source_documents": sum(1 for source_id in catalog_sources if source_id in source_states),
                "missing_source_documents": sum(1 for source_id in catalog_sources if source_id not in source_states),
                "active_claims": sum(row["active_claim_count"] for row in source_rows),
                "candidate_claims": sum(row["claim_review_status_counts"].get("candidate", 0) for row in source_rows),
                "human_review_required_claims": sum(row["claim_review_status_counts"].get("human_review_required", 0) for row in source_rows),
                "unlinked_repair_sources": len(unlinked_batch_rows),
                "linked_repair_sources": len(linked_batch_rows),
                "disk_exclusions": len(disk_exclusions),
                "blockers": len(blockers),
            },
            "sources": source_rows,
            "disk_exclusions": disk_exclusions,
            "blockers": blockers,
            "batches": {
                "unlinked": {
                    "batch_id": unlinked_batch["batch_id"],
                    "artifact_sha256": sha256_json(unlinked_batch),
                    "output_root": str((output_root / "unlinked").resolve()),
                },
                "linked": {
                    "batch_id": linked_batch["batch_id"],
                    "artifact_sha256": sha256_json(linked_batch),
                    "output_root": str((output_root / "linked").resolve()),
                    "apply_gate": "coordinated_registry_rebind_required",
                },
            },
        }
    )
    ownership = _seal(
        {
            "schema_version": OWNERSHIP_SCHEMA_VERSION,
            "issue": 357,
            "runner_commit": runner_commit,
            "worktree": str(worktree.resolve()),
            "output_root": str(output_root.resolve()),
            "allowed_subroots": [
                str((output_root / "unlinked").resolve()),
                str((output_root / "linked").resolve()),
            ],
            "forbidden_issue": 358,
        }
    )
    return plan, unlinked_batch, linked_batch, ownership


def _database_rows(store: PostgresKnowledgeStore) -> list[dict[str, Any]]:
    collections = sorted(
        {"source_documents", "claims", *SEMANTIC_REFERENCE_COLLECTIONS}
    )
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(
            """SELECT collection, object_id, revision, content_sha256, payload
               FROM wang_knowledge.objects
               WHERE retired_at IS NULL AND collection = ANY(%s)
               ORDER BY collection, object_id""",
            (collections,),
        )
        return [
            {
                "collection": str(collection),
                "object_id": str(object_id),
                "revision": int(revision),
                "content_sha256": str(content_sha256),
                "payload": dict(payload),
            }
            for collection, object_id, revision, content_sha256, payload in cursor.fetchall()
        ]


def _git_output(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True
    ).stdout.strip()


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--notes-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--batch-id-prefix", required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if "357" not in args.batch_id_prefix or "358" in args.batch_id_prefix:
        parser.error("batch id must name issue 357 and must not name issue 358")
    output_root = args.output_root.resolve()
    if "358" in str(output_root):
        parser.error("#357 preflight refuses an output root containing 358")
    worktree = Path(_git_output("rev-parse", "--show-toplevel", cwd=PROJECT_ROOT))
    branch = _git_output("branch", "--show-current", cwd=worktree)
    if "357" not in branch:
        parser.error("Matthew notes repair preflight must run in the #357 worktree")
    catalog_raw = args.catalog.read_bytes()
    catalog = json.loads(catalog_raw)
    catalog["_path"] = str(args.catalog.resolve())
    plan, unlinked, linked, ownership = build_repair_plan(
        catalog=catalog,
        catalog_sha256=hashlib.sha256(catalog_raw).hexdigest(),
        notes_root=args.notes_root.resolve(),
        database_rows=_database_rows(PostgresKnowledgeStore(args.database_url)),
        output_root=output_root,
        runner_commit=_git_output("rev-parse", "HEAD", cwd=worktree),
        worktree=worktree,
        generated_at=datetime.now(timezone.utc).isoformat(),
        batch_id_prefix=args.batch_id_prefix,
    )
    if not args.dry_run:
        _write_once(output_root / "source-repair-plan.json", plan)
        _write_once(output_root / "unlinked" / "research-batch.json", unlinked)
        _write_once(output_root / "linked" / "research-batch.json", linked)
        _write_once(output_root / "ownership.json", ownership)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 2 if plan["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
