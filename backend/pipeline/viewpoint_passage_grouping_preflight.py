"""Read-only, Scripture-ordered preflight for reviewed exegesis Claims.

This is not a CVP grouping artifact. It records which passage buckets can use
one deterministic comparison group and which require a bounded grouping call.
No model or database client is constructed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.sermon_search.bible_refs import BOOKS


BOOK_ORDER = {book: index for index, (book, _name, _aliases) in enumerate(BOOKS)}
PASSAGE_RE = re.compile(
    r"^(?P<book>[1-3]?[A-Za-z]+)\.(?P<chapter>\d+)"
    r"(?:\.(?P<verse>\d+))?"
    r"(?:-(?P<end_book>[1-3]?[A-Za-z]+)\.(?P<end_chapter>\d+)"
    r"(?:\.(?P<end_verse>\d+))?)?$"
)


def passage_sort_key(value: str) -> tuple[int, int, int, int, int, int, str]:
    match = PASSAGE_RE.fullmatch(value)
    if match is None or match["book"] not in BOOK_ORDER:
        raise ValueError(f"unresolved passage locator: {value}")
    end_book = match["end_book"] or match["book"]
    if end_book not in BOOK_ORDER:
        raise ValueError(f"unresolved passage locator: {value}")
    return (
        BOOK_ORDER[match["book"]],
        int(match["chapter"]),
        int(match["verse"] or 0),
        BOOK_ORDER[end_book],
        int(match["end_chapter"] or match["chapter"]),
        int(match["end_verse"] or match["verse"] or 0),
        value,
    )


def _verify_artifact(value: dict[str, Any], name: str) -> None:
    actual = value.get("artifact_sha256")
    expected = sha256_json({key: item for key, item in value.items() if key != "artifact_sha256"})
    if actual != expected:
        raise ValueError(f"{name} artifact SHA mismatch")


def build_preview(
    *, ledger: dict[str, Any], packet: dict[str, Any], batch_size: int
) -> dict[str, Any]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    _verify_artifact(ledger, "role ledger")
    _verify_artifact(packet, "role packet")
    if ledger.get("status") != "all_eligible_reviewed":
        raise ValueError("role ledger is not fully reviewed")
    if ledger.get("packet_sha256") != packet["artifact_sha256"]:
        raise ValueError("role ledger and packet belong to different freezes")

    claims = {row["claim_id"]: row for row in packet["claims"]}
    if len(claims) != len(packet["claims"]):
        raise ValueError("role packet contains duplicate Claim IDs")
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    held: list[dict[str, str]] = []
    seen: set[str] = set()
    for decision in ledger["decisions"]:
        claim_id = decision["claim_id"]
        if claim_id in seen:
            raise ValueError(f"duplicate role decision for {claim_id}")
        seen.add(claim_id)
        claim = claims.get(claim_id)
        if claim is None:
            raise ValueError(f"role decision references missing Claim {claim_id}")
        statement_sha = hashlib.sha256(claim["statement"].encode("utf-8")).hexdigest()
        for reviewer in ("primary", "independent"):
            if decision[reviewer]["claim_statement_sha256"] != statement_sha:
                raise ValueError(f"{claim_id}: {reviewer} statement SHA mismatch")
        if decision["role"] != "passage_exegesis":
            continue
        keys = decision["interpreted_passage_keys"]
        if decision["passage_identity_status"] == "disputed" or not keys:
            held.append({"claim_id": claim_id, "reason": "passage_identity_disputed"})
            continue
        try:
            ordered_keys = sorted(set(keys), key=passage_sort_key)
        except ValueError:
            held.append({"claim_id": claim_id, "reason": "passage_locator_not_normalized"})
            continue
        owner = ordered_keys[0]
        buckets[owner].append(
            {
                "claim_id": claim_id,
                "statement_sha256": statement_sha,
                "other_interpreted_passage_keys": ordered_keys[1:],
            }
        )

    if len(seen) != len(packet["claims"]):
        raise ValueError("role ledger does not cover the role packet exactly once")
    passages = []
    for key in sorted(buckets, key=passage_sort_key):
        rows = sorted(buckets[key], key=lambda item: item["claim_id"])
        passages.append(
            {
                "passage_key": key,
                "claim_count": len(rows),
                "grouping_action": (
                    "one_deterministic_group"
                    if len(rows) <= batch_size
                    else "model_split_required"
                ),
                "claims": rows,
            }
        )
    held.sort(key=lambda item: item["claim_id"])
    owned = [row["claim_id"] for passage in passages for row in passage["claims"]]
    if len(owned) != len(set(owned)) or set(owned) & {row["claim_id"] for row in held}:
        raise ValueError("passage ownership is not exact-once")
    exegesis_count = sum(d["role"] == "passage_exegesis" for d in ledger["decisions"])
    if len(owned) + len(held) != exegesis_count:
        raise ValueError("exegesis denominator is incomplete")
    result: dict[str, Any] = {
        "schema_version": "wang_passage_grouping_preflight_v1",
        "status": "preview_only",
        "role_ledger_sha256": ledger["artifact_sha256"],
        "role_packet_sha256": packet["artifact_sha256"],
        "batch_size": batch_size,
        "exegesis_claim_count": exegesis_count,
        "owned_claim_count": len(owned),
        "held_claim_count": len(held),
        "deterministic_passage_count": sum(
            p["grouping_action"] == "one_deterministic_group" for p in passages
        ),
        "model_split_passage_count": sum(
            p["grouping_action"] == "model_split_required" for p in passages
        ),
        "model_calls_executed": 0,
        "master_data_mutations": 0,
        "passages": passages,
        "held_claims": held,
    }
    result["artifact_sha256"] = sha256_json(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role-ledger", type=Path, required=True)
    parser.add_argument("--role-packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()
    ledger = json.loads(args.role_ledger.read_text(encoding="utf-8"))
    packet = json.loads(args.role_packet.read_text(encoding="utf-8"))
    preview = build_preview(ledger=ledger, packet=packet, batch_size=args.batch_size)
    if args.output.exists():
        raise ValueError(f"refusing to overwrite immutable preview {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".partial")
    temporary.write_text(json.dumps(preview, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({key: value for key, value in preview.items() if key not in {"passages", "held_claims"}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
