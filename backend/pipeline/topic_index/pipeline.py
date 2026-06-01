from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from backend.pipeline.stage1 import Stage1AnthropicClient

from .chunker import split_into_groups
from .corpus import load_corpus
from .extractor import extract_topics_from_manuscript
from .merger import merge_entries
from .models import TopicEntry, TopicIndex


# ---------------------------------------------------------------------------
# Taxonomy integration
# ---------------------------------------------------------------------------

def _taxonomy_path() -> Path:
    configured = os.getenv("SERMON_SEARCH_TOPIC_TAXONOMY")
    if configured:
        return Path(configured).expanduser().resolve()
    from backend.api.config import DATA_BASE_PATH
    return DATA_BASE_PATH / "sermon_search" / "topic_taxonomy.json"


def _default_index_path() -> Path:
    """Authoritative topic_index.json location the API reads from."""
    configured = os.getenv("SERMON_SEARCH_TOPIC_INDEX_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    from backend.api.config import DATA_BASE_PATH
    return DATA_BASE_PATH / "sermon_search" / "topic_index.json"


def _load_taxonomy(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"topics": []}


_MANAGED_MARKER = "topic_index"


def _update_taxonomy(topics: List[TopicEntry], taxonomy_path: Path) -> int:
    """
    Sync extracted topics into topic_taxonomy.json.

    Entries this pipeline owns are tagged with "source": "topic_index" and are
    fully rebuilt on every run, so fixes to names/aliases propagate instead of
    leaving stale data behind. Hand-curated entries (no marker, including the
    default taxonomy) are preserved untouched. A managed entry is skipped if a
    hand-curated entry already claims the same label.
    Returns the number of managed entries written.
    """
    taxonomy = _load_taxonomy(taxonomy_path)
    existing = taxonomy.get("topics", [])

    curated = [item for item in existing if item.get("source") != _MANAGED_MARKER]
    curated_labels = {str(item.get("label") or "").strip().lower() for item in curated}

    managed_entries: List[Dict[str, Any]] = []
    seen_labels: set[str] = set()
    for entry in topics:
        label = entry.name.strip()
        key = label.lower()
        if key in curated_labels or key in seen_labels:
            continue
        seen_labels.add(key)
        aliases = [a for a in entry.taxonomy_aliases if a != label]
        managed_entries.append({"label": label, "aliases": aliases, "source": _MANAGED_MARKER})

    taxonomy["topics"] = curated + managed_entries
    taxonomy_path.parent.mkdir(parents=True, exist_ok=True)
    taxonomy_path.write_text(json.dumps(taxonomy, ensure_ascii=False, indent=2), encoding="utf-8")

    return len(managed_entries)


# ---------------------------------------------------------------------------
# Per-file cache
# ---------------------------------------------------------------------------

def _cache_path(cache_dir: Path, project_id: str) -> Path:
    return cache_dir / f"{project_id}.json"


def _load_cache(cache_dir: Path, project_id: str, content_hash: str, model: str) -> Optional[List[Dict[str, Any]]]:
    path = _cache_path(cache_dir, project_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("content_hash") == content_hash and payload.get("model") == model:
            return payload.get("raw_topics", [])
    except Exception:
        pass
    return None


def _save_cache(cache_dir: Path, project_id: str, content_hash: str, model: str, raw_topics: List[Dict[str, Any]]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, project_id)
    payload = {
        "project_id": project_id,
        "content_hash": content_hash,
        "model": model,
        "raw_topics": raw_topics,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _entries_to_raw(entries: List[TopicEntry]) -> List[Dict[str, Any]]:
    return [e.to_dict() for e in entries]


def _load_all_cached_entries(cache_dir: Path) -> List[TopicEntry]:
    """Load raw topic entries from every per-file cache, so the index
    accumulates across separate single-project runs."""
    if not cache_dir.exists():
        return []
    entries: List[TopicEntry] = []
    for path in sorted(cache_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Warning: could not read cache {path.name}: {exc}")
            continue
        entries.extend(_raw_to_entries(payload.get("raw_topics", [])))
    return entries


def _raw_to_entries(raw_topics: List[Dict[str, Any]]) -> List[TopicEntry]:
    from .models import TopicSource
    entries: List[TopicEntry] = []
    for raw in raw_topics:
        sources = [
            TopicSource(
                project_id=s["project_id"],
                project_title=s["project_title"],
                series_id=s["series_id"],
                lecture_title=s["lecture_title"],
                source_sections=s["source_sections"],
                lun_dian=s["lun_dian"],
            )
            for s in raw.get("sources", [])
        ]
        entries.append(
            TopicEntry(
                id=raw.get("id", ""),
                name=raw["name"],
                type=raw["type"],
                size=raw["size"],
                sources=sources,
                notes=raw.get("notes"),
                taxonomy_aliases=raw.get("taxonomy_aliases", []),
            )
        )
    return entries


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _write_report(index: TopicIndex, output_dir: Path) -> None:
    lines: List[str] = [
        f"Topic Index Report",
        f"Generated: {index.generated_at}",
        f"Corpus size: {index.corpus_size} manuscripts",
        f"Total topics: {len(index.topics)}",
        "",
    ]
    concept_topics = [t for t in index.topics if t.type == "concept"]
    passage_topics = [t for t in index.topics if t.type == "passage"]
    lines.append(f"Concept topics ({len(concept_topics)}):")
    for t in concept_topics:
        src_count = len(t.sources)
        lines.append(f"  [{t.id}] {t.name}  ({src_count} source{'s' if src_count > 1 else ''})")
        for ld in t.sources[0].lun_dian[:2]:
            lines.append(f"        • {ld}")
    lines.append("")
    lines.append(f"Passage topics ({len(passage_topics)}):")
    for t in passage_topics:
        src_count = len(t.sources)
        lines.append(f"  [{t.id}] {t.name}  ({src_count} source{'s' if src_count > 1 else ''})")
    report_path = output_dir / "topic_index_report.txt"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report: {report_path}")


# ---------------------------------------------------------------------------
# Main pipeline function
# ---------------------------------------------------------------------------

def run_topic_index_pipeline(
    output_dir: Optional[Path] = None,
    series_ids: Optional[Iterable[str]] = None,
    project_types: Optional[Iterable[str]] = None,
    project_ids: Optional[Iterable[str]] = None,
    model: str = "claude-sonnet-4-6",
    timeout_seconds: float = 120.0,
    max_retries: int = 3,
    force: bool = False,
) -> TopicIndex:
    # Default to the authoritative index location's parent directory, so the
    # pipeline and the API agree on where topic_index.json lives.
    output_dir = (output_dir or _default_index_path().parent).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir / "cache"

    llm = Stage1AnthropicClient(model=model, timeout_seconds=timeout_seconds, max_retries=max_retries)

    print("Discovering manuscripts…")
    corpus = load_corpus(series_ids=series_ids, project_types=project_types, project_ids=project_ids)
    print(f"Found {len(corpus)} manuscripts.")

    skipped: List[str] = []

    # --- Process the projects in this run's corpus (writes/refreshes caches) ---
    for idx, (manuscript, text) in enumerate(corpus, start=1):
        pid = manuscript.project_id
        print(f"[{idx}/{len(corpus)}] {manuscript.project_title} ({pid})")

        if not force:
            cached = _load_cache(cache_dir, pid, manuscript.content_hash, model)
            if cached is not None:
                print(f"  → cache hit, skipping LLM call")
                continue

        try:
            chunk_groups = split_into_groups(text)
            print(f"  → {len(chunk_groups)} chunk group(s)")
            entries = extract_topics_from_manuscript(llm, manuscript, chunk_groups)
            print(f"  → {len(entries)} topic(s) extracted")
            _save_cache(cache_dir, pid, manuscript.content_hash, model, _entries_to_raw(entries))
        except Exception as exc:
            print(f"  ERROR: {exc}")
            skipped.append(f"{pid}: {exc}")

    # --- Build the index from ALL cached extractions, so the index accumulates
    #     across separate single-project runs ---
    all_entries = _load_all_cached_entries(cache_dir)
    cached_project_count = len(list(cache_dir.glob("*.json"))) if cache_dir.exists() else 0
    print(f"\nMerging {len(all_entries)} raw topic entries from {cached_project_count} cached project(s)…")
    merged = merge_entries(all_entries)
    print(f"After merge: {len(merged)} distinct topics.")

    index = TopicIndex(
        generated_at=datetime.now(timezone.utc).isoformat(),
        corpus_size=cached_project_count,
        topics=merged,
    )

    index_path = output_dir / "topic_index.json"
    index_path.write_text(json.dumps(index.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Index written: {index_path}")

    taxonomy_path = _taxonomy_path()
    added = _update_taxonomy(merged, taxonomy_path)
    print(f"Taxonomy updated: {added} new entries added to {taxonomy_path}")
    if added > 0:
        print("  Run `POST /sermon_search/reindex` to make them searchable.")

    _write_report(index, output_dir)

    if skipped:
        print(f"\nSkipped {len(skipped)} manuscript(s) due to errors:")
        for msg in skipped:
            print(f"  {msg}")

    return index


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a topic index from sermon manuscripts.")
    parser.add_argument("--output", help="Output directory for topic_index.json and cache (default: <data>/sermon_search)")
    parser.add_argument("--series", nargs="*", metavar="SERIES_ID", help="Limit to specific series IDs")
    parser.add_argument("--project", nargs="*", metavar="PROJECT_ID", help="Limit to specific project IDs")
    parser.add_argument("--project-types", nargs="*", default=["sermon_note"], metavar="TYPE")
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-request timeout in seconds")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--force", action="store_true", help="Ignore cache and reprocess all manuscripts")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    run_topic_index_pipeline(
        output_dir=Path(args.output) if args.output else None,
        series_ids=args.series or None,
        project_types=args.project_types,
        project_ids=args.project or None,
        model=args.model,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
        force=args.force,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
