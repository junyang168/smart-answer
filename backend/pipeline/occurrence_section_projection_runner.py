"""Compile the zero-call occurrence-to-section projection for one scope."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from backend.api.canonical_repository.matthew16_viewpoint_pilot import PASSAGE_UNITS
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.occurrence_section_projection import (
    build_occurrence_section_projection,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _verified_artifact(path: Path) -> tuple[dict[str, Any], str]:
    payload = _read(path)
    stated = str(payload.get("artifact_sha256") or "")
    unsigned = {key: value for key, value in payload.items() if key != "artifact_sha256"}
    if not stated or stated != sha256_json(unsigned):
        raise ValueError("scope artifact SHA mismatch")
    return payload, stated


def _section_plans(roots: list[Path]) -> list[tuple[str, Mapping[str, Any]]]:
    paths = sorted(
        {
            path.resolve()
            for root in roots
            for path in root.rglob("*.json")
            if path.parent.name == "section-plans"
        }
    )
    if not paths:
        raise ValueError("no section plans found")
    return [(str(path), _read(path)) for path in paths]


def _write_immutable(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read(path) != payload:
            raise ValueError(f"immutable projection differs at {path}")
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope-artifact", type=Path, required=True)
    parser.add_argument("--section-plan-root", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url")
    args = parser.parse_args()

    scope, scope_sha = _verified_artifact(args.scope_artifact)
    store = PostgresKnowledgeStore(args.database_url)
    projection = build_occurrence_section_projection(
        scope_claims=[dict(row) for row in scope.get("claims") or []],
        scope_artifact_sha256=scope_sha,
        parent_claim_manifest_sha256=str(
            scope.get("parent_claim_manifest_sha256") or ""
        ),
        passage_units=PASSAGE_UNITS,
        claims=store.list_records("claims"),
        evidence_steps=store.list_records("evidence_steps"),
        source_fragments=store.list_records("source_fragments"),
        source_documents=store.list_records("source_documents"),
        section_plans=_section_plans(args.section_plan_root),
    )
    _write_immutable(args.output, projection)
    print(
        json.dumps(
            projection["statistics"]
            | {"artifact_sha256": projection["artifact_sha256"]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
