"""Bind authoring-store source fragments to immutable transcript versions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore,
    sha256_json,
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_anchor_binding_package(
    package: dict[str, Any], transcript_root: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    sources = {str(item["source_id"]): dict(item) for item in package.get("source_documents", [])}
    fragments = [dict(item) for item in package.get("source_fragments", [])]
    updated_sources: dict[str, dict[str, Any]] = {}
    updated_fragments: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    transcript_cache: dict[str, tuple[bytes, dict[str, Any], dict[str, dict[str, Any]]]] = {}

    for fragment in fragments:
        if fragment.get("anchor_state") in {"source_version_bound", "canonical_citation_bound"}:
            continue
        source_id = str(fragment.get("source_id") or "")
        source = sources.get(source_id)
        transcript_id = str((source or {}).get("transcript_id") or "")
        if not source or not transcript_id:
            unresolved.append({"fragment_id": fragment.get("fragment_id"), "reason": "missing_transcript_id"})
            continue
        path = transcript_root / f"{transcript_id}.json"
        if not path.is_file():
            unresolved.append({"fragment_id": fragment.get("fragment_id"), "reason": "missing_transcript_file"})
            continue
        if transcript_id not in transcript_cache:
            raw = path.read_bytes()
            transcript = json.loads(raw)
            paragraphs = {
                str(item.get("index")): item
                for item in transcript.get("script", [])
                if item.get("index") is not None and item.get("text")
            }
            transcript_cache[transcript_id] = (raw, transcript, paragraphs)
        raw, transcript, paragraphs = transcript_cache[transcript_id]
        paragraph = paragraphs.get(str(fragment.get("paragraph_key")))
        excerpt = str(fragment.get("verbatim_excerpt") or "")
        if not paragraph or not excerpt or excerpt not in str(paragraph.get("text") or ""):
            unresolved.append({
                "fragment_id": fragment.get("fragment_id"),
                "reason": "paragraph_or_verbatim_mismatch",
            })
            continue
        source_sha = hashlib.sha256(raw).hexdigest()
        paragraph_text = str(paragraph["text"])
        source_row = dict(source)
        source_row.update(
            {
                "source_sha256": source_sha,
                "title": source_row.get("title") or (transcript.get("metadata") or {}).get("title"),
                "canonical_source_id": f"script_published/{transcript_id}.json",
            }
        )
        updated_sources[source_id] = source_row
        fragment.update(
            {
                "source_sha256": source_sha,
                "paragraph_text_sha256": _sha256_text(paragraph_text),
                "verbatim_excerpt_sha256": _sha256_text(excerpt),
                "anchor_state": "source_version_bound",
            }
        )
        updated_fragments.append(fragment)

    identity = {
        "source_ids": sorted(updated_sources),
        "fragment_ids": sorted(str(item["fragment_id"]) for item in updated_fragments),
        "source_hashes": {key: value.get("source_sha256") for key, value in sorted(updated_sources.items())},
    }
    result = {
        "schema_version": "wang_source_anchor_binding_v1",
        "package_id": f"ANCHOR-BIND-{sha256_json(identity)[:16]}",
        "source_documents": list(updated_sources.values()),
        "source_fragments": updated_fragments,
    }
    summary = {
        "bound_sources": len(updated_sources),
        "bound_fragments": len(updated_fragments),
        "unresolved_fragments": len(unresolved),
        "unresolved": unresolved,
    }
    return result, summary


def bind_source_versions(
    store: PostgresKnowledgeStore, transcript_root: Path, *, apply: bool
) -> dict[str, Any]:
    package, summary = build_anchor_binding_package(store.compile_package(), transcript_root)
    if not package["source_fragments"]:
        return {"status": "nothing_to_bind", "summary": summary}
    result = store.ingest_package(
        package,
        source_kind="source_anchor_binding",
        apply=apply,
        metadata={"transcript_root": str(transcript_root), "binding_summary": summary},
    )
    return {**result, "binding_summary": summary}
