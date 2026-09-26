"""Report which grouping-plan groups have exact-once terminal dispositions.

Batch runs report the Claims they were handed.  A group resolved in part
therefore reads as finished, because nothing puts the plan on the other side of
the comparison -- ``rock_referent`` sat at 13 links for 14 planned Claims for
two days without a single artifact saying so.

This runner is deterministic: it calls no model and writes nothing to the
Registry.  Active links are deliberately not a completion proxy: a terminal
``no_registry_assertion`` has no link, while a support link is not an identity
membership decision.  The SHA-bound disposition ledger is authoritative.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from backend.api.canonical_repository.knowledge_models import ViewpointClaimLinkRecord
from backend.api.canonical_repository.viewpoint_batch_resolution import (
    ClaimGroupingResponse,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_STATUS_MARK = {"complete": "done", "partial": "partial", "unresolved": "—"}


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


def disposition_coverage_report(
    *, grouping: ClaimGroupingResponse, grouping_sha256: str, ledger: dict
) -> dict:
    body = {key: value for key, value in ledger.items() if key != "artifact_sha256"}
    if ledger.get("artifact_sha256") != sha256_json(body):
        raise ValueError("disposition ledger SHA mismatch")
    if ledger.get("scope_label") != grouping.scope_label:
        raise ValueError("disposition ledger belongs to another scope")
    if ledger.get("grouping_sha256") != grouping_sha256:
        raise ValueError("disposition ledger belongs to another grouping")
    dispositions = {
        str(item["claim_id"]): item for item in ledger.get("claim_dispositions") or []
    }
    groups = []
    for group in sorted(grouping.groups, key=lambda item: item.group_key):
        terminal = sorted(
            claim_id
            for claim_id in group.claim_ids
            if claim_id in dispositions
            and dispositions[claim_id].get("resolution_status") == "resolved"
            and dispositions[claim_id].get("apply_status")
            in {"applied", "already_applied"}
        )
        unresolved = sorted(set(group.claim_ids) - set(terminal))
        status = (
            "complete" if not unresolved else "partial" if terminal else "unresolved"
        )
        groups.append(
            {
                "group_key": group.group_key,
                "claim_count": len(group.claim_ids),
                "terminal_disposition_count": len(terminal),
                "unresolved_claim_ids": unresolved,
                "status": status,
            }
        )
    return {
        "schema_version": "wang_canonical_viewpoint_disposition_coverage_v1",
        "scope_label": grouping.scope_label,
        "group_count": len(groups),
        "complete_group_count": sum(item["status"] == "complete" for item in groups),
        "groups": groups,
        "status": "complete" if all(item["status"] == "complete" for item in groups) else "incomplete",
        "disposition_ledger_sha256": ledger["artifact_sha256"],
    }


def render_table(report: dict) -> str:
    lines = ["| group | claims | terminal | status |", "|---|---|---|---|"]
    for item in report["groups"]:
        lines.append(
            f'| `{item["group_key"]}` | {item["claim_count"]} '
            f'| {item["terminal_disposition_count"]} | {_STATUS_MARK[item["status"]]} |'
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
    parser.add_argument("--disposition-ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    grouping_envelope = _read(args.grouping)
    grouping = ClaimGroupingResponse.model_validate(grouping_envelope["grouping"])
    report = disposition_coverage_report(
        grouping=grouping,
        grouping_sha256=str(grouping_envelope.get("artifact_sha256") or ""),
        ledger=_read(args.disposition_ledger),
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
                    "complete_group_count",
                    "status",
                    "disposition_ledger_sha256",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
