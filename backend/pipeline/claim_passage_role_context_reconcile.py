"""Verify #409 context-arbitration coverage without promoting any role decision.

Accepts independent provider roots, including an explicitly named one-off
retry root. Invalid raw attempts are visible but never counted as decisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from backend.pipeline import claim_passage_role_context_arbitration as arb
from backend.pipeline import claim_passage_role_runner as base


def _candidate(path: Path, audit: dict, index: dict[str, int]) -> dict:
    artifact = base._read_json(path)
    base._check_artifact(artifact)
    provider = artifact.get("provider")
    ids = artifact.get("claim_ids")
    if (provider not in arb.MODEL or artifact.get("model") != arb.MODEL[provider]
            or artifact.get("audit_sha256") != audit["artifact_sha256"]
            or not isinstance(ids, list) or not ids or ids[0] not in index):
        raise ValueError(f"arbitration binding differs: {path}")
    start = index[ids[0]]
    batch = audit["rows"][start:start + len(ids)]
    if [row["claim_id"] for row in batch] != ids:
        raise ValueError(f"arbitration Claim ownership is not contiguous: {path}")
    payload = json.dumps({"audit_sha256": audit["artifact_sha256"],
                          "claims": [arb.compact(row) for row in batch]},
                         ensure_ascii=False, separators=(",", ":"))
    if artifact.get("payload_sha256") != hashlib.sha256(payload.encode()).hexdigest():
        raise ValueError(f"arbitration payload SHA differs: {path}")
    try:
        arb.validate(artifact["response"], batch)
        valid = True
    except ValueError:
        valid = False
    return {"path": str(path), "artifact_sha256": artifact["artifact_sha256"],
            "provider": provider, "start": start, "claim_ids": ids,
            "decisions": artifact["response"].get("decisions"), "valid": valid,
            "retry_of_artifact_sha256": artifact.get("retry_of_artifact_sha256")}


def reconcile(audit: dict, roots: list[Path]) -> dict:
    base._check_artifact(audit)
    if audit.get("schema_version") != "wang_claim_role_source_context_audit_v1":
        raise ValueError("unsupported source-context audit")
    ids = [row["claim_id"] for row in audit["rows"]]
    index = {cid: i for i, cid in enumerate(ids)}
    if len(index) != len(ids):
        raise ValueError("audit Claim IDs repeat")
    attempts = []
    for root in roots:
        for provider in arb.MODEL:
            for path in sorted((root / provider).glob("batch-*.json")):
                if path.name.endswith(".failure.json"):
                    continue
                attempts.append(_candidate(path, audit, index))
    by_claim: dict[str, dict[str, dict]] = {}
    invalid = []
    for attempt in attempts:
        if not attempt["valid"]:
            invalid.append({key: attempt[key] for key in ("path", "provider", "start", "claim_ids")})
            continue
        for cid in attempt["claim_ids"]:
            provider_rows = by_claim.setdefault(cid, {})
            previous = provider_rows.get(attempt["provider"])
            item = {"decision": attempt["decisions"][cid],
                    "artifact_sha256": attempt["artifact_sha256"],
                    "path": attempt["path"]}
            if previous and previous["decision"] != item["decision"]:
                raise ValueError(f"conflicting valid arbitration answers: {cid}/{attempt['provider']}")
            provider_rows[attempt["provider"]] = item
    both = [cid for cid in ids if set(by_claim.get(cid, {})) == set(arb.MODEL)]
    single = [cid for cid in ids if len(by_claim.get(cid, {})) == 1]
    no_answer = [cid for cid in ids if cid not in by_claim]
    role_pairs = Counter((by_claim[cid]["gpt"]["decision"]["role"],
                          by_claim[cid]["opus"]["decision"]["role"]) for cid in both)
    disagreements = [cid for cid in both if by_claim[cid]["gpt"]["decision"]["role"]
                     != by_claim[cid]["opus"]["decision"]["role"]]
    body = {
        "schema_version": "wang_claim_role_source_context_reconciliation_v1",
        "status": "progress_only_not_role_ledger",
        "audit_sha256": audit["artifact_sha256"],
        "claim_denominator": len(ids), "attempts": len(attempts),
        "dual_reviewed": len(both), "single_reviewed": len(single),
        "unreviewed": len(no_answer), "invalid_attempts": invalid,
        "role_pair_counts": {f"{a}|{b}": count for (a, b), count in sorted(role_pairs.items())},
        "role_disagreement_claim_ids": disagreements,
        "single_review_claim_ids": single,
        "unreviewed_claim_ids": no_answer,
    }
    return base._artifact(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--root", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = reconcile(base._read_json(args.audit), args.root)
    if args.output:
        if args.output.exists():
            raise ValueError(f"refusing to overwrite: {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        base._write_immutable(args.output, report)
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"role_disagreement_claim_ids", "single_review_claim_ids",
                                     "unreviewed_claim_ids", "invalid_attempts"}},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
