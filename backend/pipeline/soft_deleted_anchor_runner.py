"""Report -- and on request withdraw -- store records that quote deleted text.

Reporting is the default and applying is opt-in, because the two questions are
different: "how much of the store stands on text a proofreader removed" is
worth being able to ask at any time, including from a test, while withdrawing
records from the authoring authority is a decision somebody makes once.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.pipeline.record_withdrawal import ANCHORED_COLLECTIONS, Withdrawal
from backend.pipeline.soft_deleted_anchor_audit import audit, segment_texts
from backend.pipeline.source_coverage_view import transcript_dirs

RELATION_COLLECTIONS = ("claim_relations", "knowledge_relations")


def _live(cursor: Any, collection: str) -> dict[str, dict[str, Any]]:
    cursor.execute(
        """SELECT object_id, payload FROM wang_knowledge.objects
           WHERE collection=%s AND retired_at IS NULL""",
        (collection,),
    )
    return {str(object_id): payload for object_id, payload in cursor.fetchall()}


def _sources(cursor: Any, search_dirs: list[Path]) -> dict[str, list[str]]:
    """Segment text for every source document whose transcript can be found.

    A source that cannot be located is left out rather than guessed at, and
    the audit counts its fragments as unresolved -- "we could not check these"
    is a different statement from "these are clean", and only one of them is
    true here.
    """

    segments: dict[str, list[str]] = {}
    for source_id, payload in _live(cursor, "source_documents").items():
        recorded = str(payload.get("source_path") or "").strip()
        candidates = [Path(recorded)] if recorded else []
        transcript_id = str(payload.get("transcript_id") or "").strip()
        if transcript_id:
            candidates += [directory / f"{transcript_id}.json" for directory in search_dirs]
        for path in candidates:
            if path.suffix == ".json" and path.is_file():
                segments[str(payload.get("source_id") or source_id)] = segment_texts(path)
                break
    return segments


def run(store: PostgresKnowledgeStore, *, data_base_path: Path) -> Withdrawal:
    with store.connect() as conn, conn.cursor() as cursor:
        return audit(
            fragments=_live(cursor, "source_fragments"),
            owners={name: _live(cursor, name) for name in ANCHORED_COLLECTIONS},
            claims=_live(cursor, "claims"),
            segments_by_source=_sources(cursor, transcript_dirs(data_base_path)),
            relations={name: _live(cursor, name) for name in RELATION_COLLECTIONS},
        )


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    from backend.api.config import DATA_BASE_PATH

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url")
    parser.add_argument("--data-base-path", type=Path, default=DATA_BASE_PATH)
    parser.add_argument(
        "--apply", action="store_true",
        help="retire the closure; without it nothing is written",
    )
    parser.add_argument("--list", action="store_true", help="print every key in the closure")
    args = parser.parse_args(argv)

    store = PostgresKnowledgeStore(args.database_url)
    report = run(store, data_base_path=args.data_base_path)
    output: dict[str, Any] = {"audit": report.as_dict()}
    if args.list:
        output["closure"] = [list(key) for key in report.closure()]

    closure = report.closure()
    if closure:
        result = store.retire_objects(
            closure,
            reason="anchor quotes text a proofreader soft-deleted (#102)",
            package_id="RETIRE-SOFT-DELETED-ANCHORS",
            source_kind="soft_deleted_anchor_retirement",
            apply=args.apply,
            metadata={"audit": report.as_dict()},
        )
        output["retirement"] = {
            key: value for key, value in result.items() if key != "operations"
        }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
