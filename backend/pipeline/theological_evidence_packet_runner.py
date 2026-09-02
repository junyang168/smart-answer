"""Compile the shared evidence packet consumed by draft-first authoring."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.pipeline.theological_evidence_packet import (
    compile_theological_evidence_packet,
    validate_editorial_scope,
    validate_theological_evidence_packet,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_COLLECTIONS = (
    "viewpoint_structures",
    "viewpoint_structure_revisions",
    "canonical_viewpoints",
    "viewpoint_revisions",
    "viewpoint_claim_links",
    "argument_routes",
    "argument_route_revisions",
    "argument_route_attestations",
    "viewpoint_relations",
    "claims",
    "evidence_steps",
    "source_fragments",
    "source_documents",
)


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def compile_from_store(*, scope: Mapping[str, Any]) -> dict[str, Any]:
    """Read the current master data once and compile one immutable packet."""

    validate_editorial_scope(scope)
    store = PostgresKnowledgeStore()
    records = {
        collection: store.list_records(collection)
        for collection in REQUIRED_COLLECTIONS
    }
    packet = compile_theological_evidence_packet(scope=scope, records=records)
    validate_theological_evidence_packet(packet)
    return packet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Compile and validate the packet without invoking any author or reviewer. "
            "The packet is still written so the exact dry-run input can be inspected."
        ),
    )
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    scope = json.loads(args.scope.read_text(encoding="utf-8"))
    packet = compile_from_store(scope=scope)
    output = args.output_dir / "theological-evidence-packet.json"
    _write_json_atomic(output, packet)
    print(
        json.dumps(
            {
                "status": packet["compiler_readiness"],
                "mode": "dry_run" if args.dry_run else "compile",
                "scope_sha256": packet["scope"]["scope_sha256"],
                "evidence_packet_sha256": packet["evidence_packet_sha256"],
                "viewpoint_count": len(packet["focal_viewpoints"]),
                "argument_route_count": len(packet["argument_routes"]),
                "claim_count": len(packet["claims"]),
                "source_count": len(packet["source_documents"]),
                "compiler_findings": packet["compiler_findings"],
                "output": str(output),
                "would_call_models": False,
                "would_write_reader_prose": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if packet["compiler_readiness"] != "insufficient_material" else 2


if __name__ == "__main__":
    raise SystemExit(main())
