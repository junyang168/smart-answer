"""Compile one immutable source-local identity context packet; zero model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.viewpoint_identity_context import (
    build_identity_context_packet,
)
from backend.api.canonical_repository.viewpoint_resolution import (
    ViewpointIdentityReviewPacket,
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hypothesis-id", required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--knowledge-package", type=Path, action="append", required=True)
    parser.add_argument(
        "--source-file",
        action="append",
        help="Explicit pinned source revision as SOURCE_ID=/absolute/path",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--window-before-items", type=int, default=1)
    parser.add_argument("--window-after-items", type=int, default=1)
    parser.add_argument("--max-context-characters", type=int, default=120000)
    args = parser.parse_args()
    source_documents: dict[str, dict[str, Any]] = {}
    source_fragment_indexes: dict[str, int] = {}
    for path in args.knowledge_package:
        package = _read(path)
        for item in package.get("source_documents", []):
            source_id = str(item["source_id"])
            previous = source_documents.get(source_id)
            if previous is not None and previous != item:
                raise ValueError(f"conflicting source descriptor for {source_id}")
            source_documents[source_id] = item
        for item in package.get("source_fragments", []):
            fragment_id = str(item["fragment_id"])
            index = item.get("source_segment_index")
            if not isinstance(index, int):
                continue
            previous_index = source_fragment_indexes.get(fragment_id)
            if previous_index is not None and previous_index != index:
                raise ValueError(f"conflicting source segment index for {fragment_id}")
            source_fragment_indexes[fragment_id] = index
    for binding in args.source_file or []:
        source_id, separator, raw_path = binding.partition("=")
        if not separator or not source_id or not raw_path:
            raise ValueError("--source-file must be SOURCE_ID=/absolute/path")
        source_path = Path(raw_path)
        if not source_path.is_file():
            raise ValueError(f"explicit source file is unavailable: {source_path}")
        source_documents[source_id] = {
            "source_id": source_id,
            "source_path": str(source_path),
            "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        }
    result = build_identity_context_packet(
        hypothesis_id=args.hypothesis_id,
        parent_packet=ViewpointIdentityReviewPacket.model_validate(_read(args.packet)),
        source_documents=source_documents,
        source_fragment_indexes=source_fragment_indexes,
        expansion_reason="boundary_disagreement",
        window_before_items=args.window_before_items,
        window_after_items=args.window_after_items,
        max_context_characters=args.max_context_characters,
    )
    payload = result.model_dump(mode="json")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        if _read(args.output) != payload:
            raise ValueError(f"immutable context packet differs at {args.output}")
    else:
        temporary = args.output.with_suffix(args.output.suffix + ".partial")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "packet_sha256": result.packet_sha256,
                "source_count": len(result.source_context_windows),
                "context_character_count": result.context_character_count,
                "model_calls_executed": 0,
                "master_data_mutations": 0,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
