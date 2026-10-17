"""Read-only, pinned planning for adding editorial source headings.

A query changes priority only. It is not semantic topic classification, source
independence verification, Claim qualification, or permission to write sources.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline.extraction_sections import leading_untitled_body_end
from backend.pipeline.knowledge_source import markdown_source_document
from backend.pipeline.source_projection import project_script
from backend.pipeline.transcript_source import resolve_transcript_path


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()



def _heading_scope(projection):
    """Ignore only blank leading rows when headings already cover real text.

    The rows stay in the source and retain all coordinates. This is scheduling
    metadata, not permission to strip whitespace or rewrite a transcript.
    """
    prefix = leading_untitled_body_end(projection.headings, len(projection.body_rows))
    blank_prefix = (prefix is not None and bool(projection.headings)
                    and all(not str(row.get("text") or "").strip()
                            for row in projection.body_rows[:prefix]))
    effective_prefix = None if blank_prefix else prefix
    result = {"scope_end": effective_prefix, "can_generate": effective_prefix is not None,
              "status": "headingless" if not projection.headings else
              "untitled_prefix" if effective_prefix is not None else "has_headings"}
    if blank_prefix:
        result["ignored_blank_prefix"] = prefix
    return result

def _build_source_heading_inventory(store: Any, *, query: str = "人子",
                                   claim_ids: Sequence[str] | None = None,
                                   include_other_sources: bool = True) -> dict[str, Any]:
    """Inspect active SQL records and registered files without changing either.

    Only registered published/review transcripts can enter ``generation_plan``.
    Notes are inspected but never scheduled for the transcript insertion writer.
    Every referenced occurrence and direct evidence fragment contributes its
    source, including sources other than an occurrence's first source.
    """
    pins: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, str]] = []

    def read(collection: str, ident: str) -> dict[str, Any] | None:
        key = f"{collection}/{ident}"
        if key in pins:
            return pins[key]["payload"]
        state = store.get_record_state(collection, ident)
        if not state or state.get("retired"):
            issues.append({"key": key, "reason": "missing_or_retired"})
            return None
        payload = state["payload"]
        if record_content_sha(payload) != state["content_sha256"]:
            issues.append({"key": key, "reason": "stored_content_sha_mismatch"})
            return None
        pins[key] = {"collection": collection, "object_id": ident,
                     "revision": state["revision"], "content_sha256": state["content_sha256"],
                     "retired": False, "payload": payload}
        return payload

    ids = sorted(set(claim_ids if claim_ids is not None else
                     (row["claim_id"] for row in store.list_records("claims"))))
    membership: dict[str, set[str]] = {}
    matched: set[str] = set()
    for cid in ids:
        claim = read("claims", cid)
        if claim is None:
            continue
        if query and query.casefold() in str(claim.get("statement", claim.get("title", ""))).casefold():
            matched.add(cid)
        if not include_other_sources and cid not in matched:
            continue
        source_ids = {str(row["source_id"]) for row in claim.get("occurrences", [])
                      if row.get("source_id")}
        for eid in sorted(set(claim.get("evidence_step_ids", []))):
            evidence = read("evidence_steps", eid)
            if evidence is None:
                continue
            fragments = set(evidence.get("source_fragment_ids") or [])
            if evidence.get("source_fragment_id"):
                fragments.add(evidence["source_fragment_id"])
            for fid in sorted(fragments):
                fragment = read("source_fragments", fid)
                if fragment and fragment.get("source_id"):
                    source_ids.add(str(fragment["source_id"]))
        if not source_ids:
            issues.append({"key": f"claims/{cid}", "reason": "no_bound_source"})
        for sid in source_ids:
            membership.setdefault(sid, set()).add(cid)

    registered = store.list_records("source_documents")
    exact_sources = {row["source_id"] for row in registered}
    by_transcript: dict[str, set[str]] = {}
    for row in registered:
        if row.get("transcript_id"):
            by_transcript.setdefault(str(row["transcript_id"]), set()).add(row["source_id"])
    resolved_membership: dict[str, set[str]] = {}
    alias_resolutions = []
    ambiguous_sources = {}
    for sid, cids in sorted(membership.items()):
        candidates = by_transcript.get(sid, set())
        resolved = sid
        if sid not in exact_sources and len(candidates) == 1:
            resolved = next(iter(candidates))
            alias_resolutions.append({"alias": sid, "source_id": resolved,
                "resolution": "unique_registered_transcript_id", "claim_ids": sorted(cids)})
        elif sid not in exact_sources and len(candidates) > 1:
            ambiguous_sources[sid] = sorted(candidates)
        resolved_membership.setdefault(resolved, set()).update(cids)
    membership = resolved_membership
    sources: list[dict[str, Any]] = []
    for sid in sorted(membership):
        cids = sorted(membership[sid])
        priority = bool(set(cids) & matched)
        if not include_other_sources and not priority:
            continue
        entry: dict[str, Any] = {"source_id": sid, "claim_ids": cids,
                                 "priority": priority, "matched_claim_ids": sorted(set(cids) & matched)}
        if sid in ambiguous_sources:
            entry.update(status="unavailable", reason="ambiguous_registered_transcript_id",
                         candidate_source_ids=ambiguous_sources[sid])
            issues.append({"key": "source_documents/" + sid,
                           "reason": "ambiguous_registered_transcript_id"})
            sources.append(entry)
            continue
        source = read("source_documents", sid)
        if source is None:
            entry.update(status="unavailable", reason="missing_or_inconsistent_source_record")
            sources.append(entry)
            continue
        entry.update(source=source,
                     source_pin={k: pins[f"source_documents/{sid}"][k]
                                 for k in ("revision", "content_sha256")},
                     source_type=source.get("source_type"), title=source.get("title"))
        try:
            path = Path(str(source.get("source_path") or ""))
            if not path.is_absolute():
                raise ValueError("registered source path is not absolute")
            kind = source.get("source_type")
            if kind == "sermon_transcript":
                if path.parent.name not in {"script_review", "script_published"}:
                    entry.update(status="unsupported", reason="not_a_governed_transcript_stage")
                    sources.append(entry)
                    continue
                source_root = path.parent.parent
                authoritative = resolve_transcript_path(str(source.get("transcript_id") or path.stem),
                    [source_root / "script_review", source_root / "script_published"])
                if authoritative != path:
                    entry.update(status="stale", reason="registered_path_is_not_published_first",
                                 source_path=str(path), authoritative_source_path=str(authoritative))
                    sources.append(entry)
                    continue
                raw = path.read_bytes()
                payload = json.loads(raw)
                rows = payload if isinstance(payload, list) else payload["script"]
            elif kind == "notes_manuscript":
                payload, raw, loaded_path = markdown_source_document(source)
                if loaded_path != path:
                    raise ValueError("notes resolver changed registered source path")
                rows = payload["script"]
            else:
                entry.update(status="unsupported", reason="unsupported_source_type")
                sources.append(entry)
                continue
            projection = project_script(rows)
            actual_sha = hashlib.sha256(raw).hexdigest()
            entry.update(path=str(path), current_file_sha256=actual_sha,
                         source_path=str(path), source_file_sha256=actual_sha,
                         source_body_sha256=projection.body_sha256,
                         editorial_structure_sha256=projection.editorial_structure_sha256,
                         body_paragraphs=len(projection.body_rows), heading_count=len(projection.headings))
            if not source.get("source_file_sha256") or not source.get("source_body_sha256"):
                entry.update(status="stale", reason="missing_registered_source_identity")
            elif (projection.body_sha256 != source["source_body_sha256"]
                  or (source.get("source_visual_sha256") is not None and
                      str(source["source_visual_sha256"]) != str(projection.visual_content_sha256 or ""))):
                entry.update(status="stale", reason="registered_source_sha_mismatch")
            elif actual_sha != source["source_file_sha256"]:
                from backend.pipeline.source_editorial_transition import verify_current_source_binding
                try:
                    verified = verify_current_source_binding({"path": str(path),
                        "file_sha256": source["source_file_sha256"]})
                    if verified["file_sha256"] != actual_sha:
                        raise ValueError("source changed during editorial verification")
                    entry["editorial_transition_verified"] = True
                except (OSError, ValueError, KeyError, TypeError):
                    entry.update(status="stale", reason="registered_source_sha_mismatch")
            if "status" not in entry and not projection.body_rows:
                entry.update(status="empty", reason="no_source_body")
            elif "status" not in entry:
                prefix_end = leading_untitled_body_end(projection.headings, len(projection.body_rows))
                entry["scope_end"] = prefix_end
                entry["status"] = ("headingless" if not projection.headings else
                                   "untitled_prefix" if prefix_end is not None else "has_headings")
                entry["can_generate"] = kind == "sermon_transcript" and prefix_end is not None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            entry.update(status="unavailable", reason=f"{type(exc).__name__}: {exc}")
        sources.append(entry)
    sources.sort(key=lambda row: (not row["priority"], row["source_id"]))
    result = {"schema_version": "wang_source_heading_inventory_v1", "query": query,
              "routing_disclosure": "Query prioritizes literal Claim matches only; not semantic coverage or independent-source deduplication.",
              "claim_ids": ids, "matched_claim_ids": sorted(matched), "sources": sources,
              "source_alias_resolutions": alias_resolutions,
              "generation_plan": [row for row in sources if row.get("can_generate")],
              "issues": issues,
              "pins": [{k: v for k, v in pins[key].items() if k != "payload"} for key in sorted(pins)]}
    result["inventory_sha256"] = _sha(result)
    return result


class _SnapshotStore:
    """A closed SQL snapshot exposes the same narrow interface as test stores."""

    def __init__(self, rows):
        self.rows = rows

    def list_records(self, collection):
        return [row["payload"] for (col, _), row in self.rows.items() if col == collection]

    def get_record_state(self, collection, ident):
        return self.rows.get((collection, ident))


def _sql_snapshot(store, *, query, claim_ids, include_other_sources):
    rows = {}
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")

        def fetch(collection, ids=None):
            if ids is not None and not ids:
                return []
            sql = ("SELECT object_id,payload,revision,content_sha256 FROM wang_knowledge.objects "
                   "WHERE collection=%s AND retired_at IS NULL")
            params = [collection]
            if ids is not None:
                if collection == "source_documents":
                    sql += " AND (object_id=ANY(%s) OR payload->>'transcript_id'=ANY(%s))"
                    params.extend([sorted(ids), sorted(ids)])
                else:
                    sql += " AND object_id=ANY(%s)"
                    params.append(sorted(ids))
            cursor.execute(sql + " ORDER BY object_id", params)
            result = cursor.fetchall()
            for ident, payload, revision, digest in result:
                rows[collection, ident] = {"payload": dict(payload), "revision": int(revision),
                    "content_sha256": str(digest), "retired": False}
            return [dict(row[1]) for row in result]

        all_claims = fetch("claims")
        selected_ids = set(claim_ids) if claim_ids is not None else None
        selected = [row for row in all_claims
                    if (selected_ids is None or row["claim_id"] in selected_ids)
                    and (include_other_sources or (query and query.casefold() in
                        str(row.get("statement", row.get("title", ""))).casefold()))]
        evidence = fetch("evidence_steps", {eid for row in selected
                                           for eid in row.get("evidence_step_ids", [])})
        fragment_ids = {fid for row in evidence for fid in (row.get("source_fragment_ids") or [])}
        fragment_ids.update(row["source_fragment_id"] for row in evidence if row.get("source_fragment_id"))
        fragments = fetch("source_fragments", fragment_ids)
        source_ids = {str(occurrence["source_id"]) for row in selected
                      for occurrence in row.get("occurrences", []) if occurrence.get("source_id")}
        source_ids.update(str(row["source_id"]) for row in fragments if row.get("source_id"))
        fetch("source_documents", source_ids)
    return _SnapshotStore(rows), len(all_claims)


def build_source_heading_inventory(store: Any, *, query: str = "人子",
                                   claim_ids: Sequence[str] | None = None,
                                   include_other_sources: bool = True) -> dict[str, Any]:
    """Read SQL in one repeatable-read read-only transaction, then inspect files.

    At most four batched object queries replace per-record connections. Priority
    mode loads all current Claims for denominator disclosure but only traverses
    matching Claims' Evidence, fragments and sources. In-memory stores retain
    the same pure-record interface for deterministic tests.
    """
    if hasattr(store, "connect"):
        store, total = _sql_snapshot(store, query=query, claim_ids=claim_ids,
                                    include_other_sources=include_other_sources)
    else:
        total = len(store.list_records("claims"))
    result = _build_source_heading_inventory(store, query=query, claim_ids=claim_ids,
                                            include_other_sources=include_other_sources)
    result["total_active_claim_count"] = total
    result["matched_claim_count"] = len(result["matched_claim_ids"])
    result["blocking_issues"] = list(result["issues"])
    for row in result["sources"]:
        if (row["status"] in {"stale", "unavailable", "empty", "unsupported"}
                and not any(issue["key"] == "source_documents/" + row["source_id"]
                            for issue in result["blocking_issues"])):
            result["blocking_issues"].append({"key": "source_documents/" + row["source_id"],
                "reason": row.get("reason", row["status"])})
    result["ready_for_generation"] = not result["blocking_issues"]
    result["inventory_sha256"] = _sha({key: value for key, value in result.items()
                                        if key != "inventory_sha256"})
    return result


def _all_registered_source_states(store):
    """Read source registration once, without per-source database connections."""
    if not hasattr(store, "connect"):
        return {row["source_id"]: store.get_record_state("source_documents", row["source_id"])
                for row in store.list_records("source_documents")}
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor.execute("SELECT object_id,payload,revision,content_sha256 FROM wang_knowledge.objects "
                       "WHERE collection='source_documents' AND retired_at IS NULL ORDER BY object_id")
        return {ident: {"payload": dict(payload), "revision": int(revision),
                        "content_sha256": str(digest), "retired": False}
                for ident, payload, revision, digest in cursor.fetchall()}


def build_transcript_heading_inventory(store: Any, data_root: Path, *, query: str = "人子") -> dict[str, Any]:
    """Plan ALL authoritative transcript files, including unregistered sermons.

    Published wins over reviewed by exact transcript filename. SQL membership
    supplies priority, never the corpus denominator. An unregistered descriptor
    is file metadata for subtitle proof only; this function creates no SQL
    source, Claim qualification, or semantic source authority.
    """
    data_root = Path(data_root).resolve()
    priority_inventory = build_source_heading_inventory(store, query=query, include_other_sources=False)
    priority_paths = {str(Path(row["path"]).resolve()) for row in priority_inventory["sources"]
                      if row.get("path") and row["priority"]}
    priority_transcripts = {str(row["source"].get("transcript_id"))
                           for row in priority_inventory["sources"]
                           if row.get("source", {}).get("transcript_id") and row["priority"]}
    registered = _all_registered_source_states(store)
    by_transcript: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    by_path: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for sid, state in registered.items():
        doc = state["payload"]
        if doc.get("source_type") != "sermon_transcript":
            continue
        if doc.get("transcript_id"):
            by_transcript.setdefault(str(doc["transcript_id"]), []).append((sid, state))
        if doc.get("source_path"):
            by_path.setdefault(str(Path(doc["source_path"]).resolve()), []).append((sid, state))

    authoritative: dict[str, Path] = {}
    for stage in ["script_published", "script_review"]:
        for path in sorted((data_root / stage).glob("*.json")):
            authoritative.setdefault(path.stem, path)
    sources = []
    issues = []
    seen_physical: dict[str, str] = {}
    for transcript_id, path in sorted(authoritative.items()):
        physical = str(path.resolve())
        entry: dict[str, Any] = {"transcript_id": transcript_id, "path": str(path),
            "source_path": str(path), "priority": physical in priority_paths or transcript_id in priority_transcripts,
            "source_id": "transcript-file:" + transcript_id, "source_pin": None,
            "claim_ids": [], "metadata_origin": "authoritative_transcript_file"}
        try:
            if physical in seen_physical:
                raise ValueError("duplicate_physical_path:" + seen_physical[physical])
            seen_physical[physical] = transcript_id
            candidates = {sid: state for sid, state in by_transcript.get(transcript_id, [])}
            candidates.update({sid: state for sid, state in by_path.get(physical, [])})
            if len(candidates) > 1:
                entry["candidate_source_ids"] = sorted(candidates)
                raise ValueError("ambiguous_registered_transcript_source")
            raw = path.read_bytes()
            parsed = json.loads(raw)
            rows = parsed if isinstance(parsed, list) else parsed["script"]
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("invalid_transcript_script_rows")
            indexes = [str(row.get("index")) for row in rows]
            duplicate_indexes = sorted(index for index, count in Counter(indexes).items() if count > 1)
            entry["duplicate_existing_row_indexes"] = duplicate_indexes
            entry["duplicate_physical_indexes"] = duplicate_indexes
            entry["insertion_locator_mode"] = "physical_position" if duplicate_indexes else "original_index"
            projection = project_script(rows)
            body_indexes = [str(row.get("index")) for row in projection.body_rows]
            entry["duplicate_body_indexes"] = sorted(index for index, count in Counter(body_indexes).items() if count > 1)
            digest = hashlib.sha256(raw).hexdigest()
            entry.update(current_file_sha256=digest, source_file_sha256=digest,
                source_body_sha256=projection.body_sha256,
                source_visual_sha256=projection.visual_content_sha256,
                visual_block_count=len(projection.visual_blocks),
                editorial_structure_sha256=projection.editorial_structure_sha256,
                body_paragraphs=len(projection.body_rows), heading_count=len(projection.headings),
                source_type="sermon_transcript", existing_row_indexes=indexes)
            if projection.body_rows:
                entry.update(_heading_scope(projection))
            if candidates:
                sid, state = next(iter(candidates.items()))
                source = state["payload"]
                entry.update(source_id=sid, source=source,
                    source_pin={key: state[key] for key in ("revision", "content_sha256")},
                    metadata_origin="registered_source_document")
                if state.get("retired") or record_content_sha(source) != state["content_sha256"]:
                    raise ValueError("registered_source_content_sha_mismatch")
                if str(Path(str(source.get("source_path") or "")).resolve()) != physical:
                    raise ValueError("registered_path_is_not_authoritative")
                if not source.get("source_body_sha256") or source["source_body_sha256"] != projection.body_sha256:
                    raise ValueError("registered_source_body_sha_mismatch")
                if (source.get("source_visual_sha256") is not None and
                        str(source["source_visual_sha256"]) != str(projection.visual_content_sha256 or "")):
                    raise ValueError("registered_source_visual_sha_mismatch")
                if not source.get("source_file_sha256"):
                    raise ValueError("missing_registered_source_file_sha")
                if source["source_file_sha256"] != digest:
                    from backend.pipeline.source_editorial_transition import verify_current_source_binding
                    current = verify_current_source_binding({"path": str(path),
                        "file_sha256": source["source_file_sha256"]})
                    if current["file_sha256"] != digest:
                        raise ValueError("source_changed_during_editorial_verification")
                    entry["editorial_transition_verified"] = True
            else:
                entry["source"] = {"source_id": entry["source_id"], "source_type": "sermon_transcript",
                    "transcript_id": transcript_id, "source_path": str(path),
                    "source_file_sha256": digest, "source_body_sha256": projection.body_sha256,
                    "source_visual_sha256": projection.visual_content_sha256,
                    "locator_space": "spoken_body_v1", "metadata_origin": "authoritative_transcript_file",
                    "authority_disclosure": "File metadata for editorial subtitle proof only; not a registered knowledge source or Claim authority."}
            if not projection.body_rows:
                entry.update(status="empty", reason="no_source_body", can_generate=False)
            else:
                entry.update(_heading_scope(projection))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if entry.get("status") == "has_headings":
                entry.update(can_generate=False, registration_discrepancy=f"{type(exc).__name__}: {exc}")
                issues.append({"key": "transcripts/" + transcript_id,
                               "reason": entry["registration_discrepancy"], "generation_blocking": False})
            else:
                entry.update(status="unavailable", can_generate=False,
                             reason=f"{type(exc).__name__}: {exc}")
                issues.append({"key": "transcripts/" + transcript_id,
                               "reason": entry["reason"], "generation_blocking": True})
        if entry.get("status") == "empty":
            issues.append({"key": "transcripts/" + transcript_id, "reason": entry["reason"],
                           "generation_blocking": True})
        sources.append(entry)
    sources.sort(key=lambda row: (not row["priority"], row["transcript_id"]))
    result = {"schema_version": "wang_all_transcript_heading_inventory_v1", "query": query,
        "inventory_mode": "all_transcripts",
        "data_root": str(data_root), "sources": sources,
        "generation_plan": [row for row in sources if row.get("can_generate")],
        "total_authoritative_transcripts": len(sources),
        "registered_transcripts": sum(row["source_pin"] is not None for row in sources),
        "unregistered_transcripts": sum(row["source_pin"] is None for row in sources),
        "priority_inventory_sha256": priority_inventory["inventory_sha256"],
        "priority_discovery_issues": priority_inventory["blocking_issues"],
        "issues": issues,
        "generation_blocking_issues": [issue for issue in issues if issue["generation_blocking"]],
        "blocking_issues": [issue for issue in issues if issue["generation_blocking"]],
        "ready_for_generation": not any(issue["generation_blocking"] for issue in issues),
        "routing_disclosure": "All published-first authoritative transcript files are inventoried. Literal Claim matches only set priority; unregistered file descriptors confer no knowledge-source or Claim authority."}
    result["inventory_sha256"] = _sha(result)
    return result
