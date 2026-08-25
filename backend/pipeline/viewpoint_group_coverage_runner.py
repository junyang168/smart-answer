"""Report which grouping-plan groups the Registry actually covers.

Batch runs report the Claims they were handed.  A group resolved in part
therefore reads as finished, because nothing puts the plan on the other side of
the comparison -- ``rock_referent`` sat at 13 links for 14 planned Claims for
two days without a single artifact saying so.

This runner is deterministic: it calls no model and writes nothing to the
Registry.  It reads the scope's grouping plan and the active claim links, and
says per group how many planned Claims have one.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from backend.api.canonical_repository.knowledge_models import ViewpointClaimLinkRecord
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_batch_resolution import (
    ClaimGroupingResponse,
    group_coverage_report,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_STATUS_MARK = {"covered": "done", "partial": "partial", "uncovered": "—"}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_immutable(path: Path, payload: dict) -> None:
    if path.exists():
        if _read(path) != payload:
            raise ValueError(f"immutable artifact differs at {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    partial.replace(path)


def active_linked_claim_ids(rows) -> list[str]:
    """Claim ids carrying an active link, deduplicated.

    A Claim can be linked to more than one viewpoint, and an invalidated or
    retired link is not coverage -- counting rows instead of Claims would report
    a superseded revision's leftovers as progress.
    """

    linked = {
        record.claim_id
        for record in (ViewpointClaimLinkRecord.model_validate(row) for row in rows)
        if record.effective_state == "active"
    }
    return sorted(linked)


def render_table(report: dict) -> str:
    lines = ["| group | claims | linked | status |", "|---|---|---|---|"]
    for item in report["groups"]:
        lines.append(
            f'| `{item["group_key"]}` | {item["claim_count"]} '
            f'| {item["linked_claim_count"]} | {_STATUS_MARK[item["status"]]} |'
        )
    return "\n".join(lines)


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--grouping",
        type=Path,
        required=True,
        help="grouping envelope written by the batch resolution runner",
    )
    parser.add_argument("--database-url")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    grouping = ClaimGroupingResponse.model_validate(_read(args.grouping)["grouping"])
    store = PostgresKnowledgeStore(args.database_url)
    report = group_coverage_report(
        grouping=grouping,
        linked_claim_ids=active_linked_claim_ids(
            store.list_records("viewpoint_claim_links")
        ),
    )
    if args.output:
        _write_immutable(args.output, report)
    print(render_table(report))
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "scope_label",
                    "group_count",
                    "planned_claim_count",
                    "linked_claim_count",
                    "covered_group_count",
                    "partial_group_count",
                    "linked_claims_outside_plan",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
