"""Move viewpoint Claim link pins that a Claim review left behind.

Deterministic: no model call. It re-pins only links whose Claim differs from the
pinned revision in review metadata alone, and reports every other case rather
than deciding it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_claim_repin import (
    plan_claim_link_repin,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != payload:
            raise ValueError(f"immutable artifact differs at {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    partial.replace(path)


def pinned_payloads(store: PostgresKnowledgeStore, links) -> dict[tuple[str, int], dict]:
    """The Claim payload each link was actually written against."""

    wanted = {
        (str(link["claim_id"]), int(link["pinned_claim_revision"])) for link in links
    }
    found: dict[tuple[str, int], dict] = {}
    with store.connect() as conn, conn.cursor() as cursor:
        for claim_id, revision in sorted(wanted):
            cursor.execute(
                """SELECT payload FROM wang_knowledge.object_versions
                   WHERE collection='claims' AND object_id=%s AND revision=%s""",
                (claim_id, revision),
            )
            row = cursor.fetchone()
            if row:
                found[(claim_id, revision)] = row[0]
    return found


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    store = PostgresKnowledgeStore(args.database_url)
    links = [
        item
        for item in store.list_records("viewpoint_claim_links")
        if item.get("effective_state") == "active"
    ]
    claims = {str(item["claim_id"]): item for item in store.list_records("claims")}
    report = plan_claim_link_repin(
        links=links,
        claims=claims,
        pinned_payloads=pinned_payloads(store, links),
    )
    package = {
        "package_id": f"VCLREPIN-{sha256_json(report['repinned'])[:20]}",
        "viewpoint_claim_links": report["repinned"],
    }
    summary = {
        "schema_version": report["schema_version"],
        "active_link_count": len(links),
        "repinned_count": len(report["repinned"]),
        "unchanged_count": len(report["unchanged_link_ids"]),
        "needs_review": report["needs_review"],
        "missing": report["missing"],
        "package_id": package["package_id"],
        "applied": False,
    }

    if report["repinned"] and args.apply:
        plan = store.plan_package(package, source_kind="viewpoint_claim_link_repin")
        result = store.apply_plan(
            plan, metadata={"reason": "Claim review moved the pinned revision"}
        )
        summary["applied"] = True
        summary["change_set_id"] = plan.change_set_id
        summary["apply_status"] = result.get("status")
    summary["artifact_sha256"] = sha256_json(summary)
    _write_immutable(args.output, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
