"""Build a SHA-bound, exact-once queue for unresolved #409 role decisions.

This is a read-only projection of the completed dual-review ledger. It does not
reinterpret Claims, call models, or convert unresolved rows to another role.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from backend.pipeline import claim_passage_role_runner as base


REPAIR_BASES = {
    "analyst_repair_required_under_user_instruction",
    "user_accepted_analyst_repair_required",
}
HUMAN_BASES = {
    "analyst_needs_human_under_user_instruction",
    "user_accepted_analyst_needs_human",
}


def reason_code(decision: dict[str, Any]) -> str:
    basis = decision["decision_basis"]
    if basis in REPAIR_BASES:
        return "REVIEWED_SOURCE_OR_CLAIM_REPAIR_REQUIRED"
    if basis in HUMAN_BASES:
        return "REVIEWED_HUMAN_DECISION_REQUIRED"
    if basis != "review_disagreement_or_uncertainty":
        raise ValueError(f"unknown unresolved basis: {basis}")
    primary, independent = decision["primary"]["role"], decision["independent"]["role"]
    if primary == independent == "unresolved":
        return "BOTH_REVIEWERS_UNRESOLVED"
    if "unresolved" in {primary, independent}:
        return "ONE_REVIEWER_UNRESOLVED"
    if {primary, independent} == {"other", "passage_exegesis"}:
        return "REVIEWERS_DISAGREE_ON_ROLE"
    raise ValueError(f"unrecognized unresolved reviewer pair: {primary}/{independent}")


def build_queue(ledger: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    base._check_artifact(ledger)
    base._check_artifact(packet)
    if (ledger.get("schema_version") != "wang_claim_passage_role_ledger_v7"
            or ledger.get("status") != "all_eligible_reviewed"
            or ledger.get("packet_sha256") != packet["artifact_sha256"]):
        raise ValueError("completed role ledger and frozen packet do not match")
    claims = packet["claims"]
    decisions = ledger["decisions"]
    if not isinstance(ledger.get("batch_size"), int) or ledger["batch_size"] <= 0:
        raise ValueError("completed ledger has invalid batch size")
    claim_by_id = {row["claim_id"]: row for row in claims}
    claim_batch = {row["claim_id"]: index // ledger["batch_size"] + 1
                   for index, row in enumerate(claims)}
    decision_by_id = {row["claim_id"]: row for row in decisions}
    if (len(claim_by_id) != len(claims) or len(decision_by_id) != len(decisions)
            or set(claim_by_id) != set(decision_by_id)):
        raise ValueError("role ledger does not cover frozen Claims exactly once")
    counts = dict(sorted(Counter(row["role"] for row in decisions).items()))
    if counts != ledger["counts"]:
        raise ValueError("role counts differ from ledger decisions")
    if not isinstance(ledger.get("review_artifact_shas"), dict):
        raise ValueError("completed ledger lacks review artifact SHAs")

    rows = []
    for claim_id in sorted(claim_by_id):
        claim = claim_by_id[claim_id]
        decision = decision_by_id[claim_id]
        if decision["role"] != "unresolved":
            continue
        if decision["interpreted_passage_keys"]:
            raise ValueError(f"unresolved Claim has interpreted passage keys: {claim_id}")
        if (decision["primary"]["claim_statement_sha256"] != decision["independent"]["claim_statement_sha256"]
                or decision["primary"]["claim_statement_sha256"] != hashlib.sha256(claim["statement"].encode("utf-8")).hexdigest()):
            raise ValueError(f"reviewer statement binding differs: {claim_id}")
        batch_id = claim_batch[claim_id]
        primary_sha = ledger["review_artifact_shas"].get(f"primary-{batch_id:05d}")
        independent_sha = ledger["review_artifact_shas"].get(f"independent-{batch_id:05d}")
        if not primary_sha or not independent_sha:
            raise ValueError(f"review artifact SHA missing: {claim_id}")
        rows.append({
            "claim_id": claim_id,
            "batch_id": batch_id,
            "source_id": claim["source_id"],
            "claim_revision": claim["claim_revision"],
            "claim_content_sha256": claim["claim_content_sha256"],
            "source_content_sha256": claim["source_content_sha256"],
            "source_file_sha256": claim["source_file_sha256"],
            "primary_artifact_sha256": primary_sha,
            "independent_artifact_sha256": independent_sha,
            "reason_code": reason_code(decision),
            "decision_basis": decision["decision_basis"],
            "primary_role": decision["primary"]["role"],
            "independent_role": decision["independent"]["role"],
            "primary_reason": decision["primary"]["reason"],
            "independent_reason": decision["independent"]["reason"],
            "claim_scripture_refs": claim["scripture_refs"],
            "next_step": (
                "repair_then_re_review" if decision["decision_basis"] in REPAIR_BASES else
                "human_review" if decision["decision_basis"] in HUMAN_BASES else
                "one_round_arbitration_or_reasoned_hold"
            ),
        })
    if len(rows) != counts.get("unresolved", 0):
        raise ValueError("unresolved queue denominator differs from ledger")
    report = base._artifact({
        "schema_version": "wang_claim_passage_role_exception_queue_v2",
        "status": "reviewed_exceptions_not_role_resolutions",
        "queue_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "role_ledger_sha256": ledger["artifact_sha256"],
        "role_packet_sha256": packet["artifact_sha256"],
        "eligible_claim_count": len(claims),
        "role_counts": counts,
        "exception_count": len(rows),
        "reason_counts": dict(sorted(Counter(row["reason_code"] for row in rows).items())),
        "rows": rows,
        "model_calls_executed": 0,
        "database_mutations": 0,
    })
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role-ledger", type=Path, required=True)
    parser.add_argument("--role-packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError(f"refusing to overwrite immutable queue: {args.output}")
    ledger = base._read_json(args.role_ledger)
    packet = base._read_json(args.role_packet)
    result = build_queue(ledger, packet)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    base._write_immutable(args.output, result)
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
