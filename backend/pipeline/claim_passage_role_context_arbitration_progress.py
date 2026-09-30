"""Fail-closed coverage and disposition report for #409's one-round arbitration.

This never updates the completed v7 role ledger. It distinguishes proposed
resolutions from still-held Claims and requires contiguous exact-once batches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from backend.pipeline import claim_passage_role_context_arbitration_round as round1
from backend.pipeline import claim_passage_role_runner as base


def _checked(path: Path, audit: dict, queue: dict, positions: dict[str, int]) -> dict:
    artifact = base._read_json(path)
    base._check_artifact(artifact)
    ids = artifact.get("claim_ids")
    if (artifact.get("schema_version") != "wang_claim_role_context_arbitration_round_v1"
            or artifact.get("audit_sha256") != audit["artifact_sha256"]
            or artifact.get("queue_sha256") != queue["artifact_sha256"]
            or artifact.get("model") not in round1.SUPPORTED_MODELS
            or not isinstance(ids, list) or not ids or ids[0] not in positions
            or not re.fullmatch(r"[0-9a-f]{64}", str(artifact.get("prompt_sha256") or ""))):
        raise ValueError(f"arbitration binding differs: {path}")
    start = positions[ids[0]]
    rows = audit["rows"][start:start + len(ids)]
    if [row["claim_id"] for row in rows] != ids:
        raise ValueError(f"batch is not a contiguous audit slice: {path}")
    priors = {row["claim_id"]: row for row in queue["rows"]}
    payload = json.dumps({"audit_sha256": audit["artifact_sha256"],
                          "queue_sha256": queue["artifact_sha256"],
                          "claims": [round1.input_row(row, priors[row["claim_id"]])
                                     for row in rows]},
                         ensure_ascii=False, separators=(",", ":"))
    if (artifact.get("payload_sha256") != hashlib.sha256(payload.encode()).hexdigest()
            or artifact.get("schema_sha256") != base.sha256_json(round1.schema(ids)["schema"])):
        raise ValueError(f"arbitration input/schema SHA differs: {path}")
    effective = {"decisions": dict(artifact["response"]["decisions"])}
    masked_holds = []
    for row in rows:
        reason = row.get("reason_code")
        if reason not in {"REVIEWED_HUMAN_DECISION_REQUIRED",
                          "REVIEWED_SOURCE_OR_CLAIM_REPAIR_REQUIRED"}:
            continue
        cid = row["claim_id"]
        disposition = ("needs_human" if reason == "REVIEWED_HUMAN_DECISION_REQUIRED"
                       else "repair_required")
        if (effective["decisions"][cid]["role"] != "unresolved"
                or effective["decisions"][cid]["disposition"] != disposition):
            masked_holds.append(cid)
            effective["decisions"][cid] = {
                "role": "unresolved", "disposition": disposition,
                "candidate_reference": "", "source_key": "", "source_quote": "",
                "reason": "Existing reviewed hold preserved; arbitration proposal not applied.",
            }
    try:
        round1.validate(effective, rows)
        valid = True
    except ValueError:
        valid = False
    return {"start": start, "end": start + len(ids), "artifact": artifact,
            "valid": valid, "path": str(path), "effective": effective,
            "masked_holds": masked_holds}


def progress(audit: dict, queue: dict, output_root: Path,
             additional_output_roots: tuple[Path, ...] = ()) -> dict:
    base._check_artifact(audit)
    base._check_artifact(queue)
    if (audit.get("queue_sha256") != queue["artifact_sha256"]
            or audit.get("claim_count") != len(queue["rows"])):
        raise ValueError("source context and prior queue differ")
    positions = {row["claim_id"]: i for i, row in enumerate(audit["rows"])}
    if len(positions) != len(audit["rows"]):
        raise ValueError("source context repeats Claim IDs")
    roots = (output_root, *additional_output_roots)
    if len({root.resolve() for root in roots}) != len(roots):
        raise ValueError("arbitration output roots repeat")
    attempts = [_checked(path, audit, queue, positions)
                for root in roots for path in sorted(root.glob("batch-*.json"))
                if not path.name.endswith(".failure.json")]
    by_start: dict[int, list[dict]] = {}
    for attempt in attempts:
        by_start.setdefault(attempt["start"], []).append(attempt)
    completed = []
    cursor = 0
    while cursor < len(audit["rows"]):
        candidates = [row for row in by_start.get(cursor, []) if row["valid"]]
        if not candidates:
            break
        if len(candidates) == 2:
            retry = next((row for row in candidates
                          if row["artifact"].get("attempt_number") == 2), None)
            original = next((row for row in candidates
                             if row["artifact"].get("attempt_number") is None), None)
            if (retry is None or original is None
                    or retry["artifact"].get("retry_of_artifact_sha256")
                    != original["artifact"]["artifact_sha256"]
                    or retry["end"] != original["end"]):
                raise ValueError(f"conflicting valid batches start at {cursor}")
            selected = retry
        elif len(candidates) == 1:
            selected = candidates[0]
        else:
            raise ValueError(f"duplicate valid batch starts at {cursor}")
        if selected["end"] <= cursor:
            raise ValueError("empty arbitration batch")
        completed.append(selected)
        cursor = selected["end"]
    if any(row["valid"] and row["start"] >= cursor for row in attempts):
        raise ValueError("valid arbitration batch exists beyond a gap")
    decisions = []
    for batch in completed:
        for cid, answer in batch["effective"]["decisions"].items():
            source = audit["rows"][positions[cid]]
            decisions.append({"claim_id": cid, "source_id": source["source_id"],
                              "claim_content_sha256": source["claim_content_sha256"],
                              "source_content_sha256": source["source_content_sha256"],
                              "source_context_audit_sha256": audit["artifact_sha256"],
                              "arbitration_artifact_sha256": batch["artifact"]["artifact_sha256"],
                              "model": batch["artifact"]["model"],
                              "prior_hold_masked": cid in batch["masked_holds"],
                              **answer})
    if len(decisions) != cursor or len({row["claim_id"] for row in decisions}) != cursor:
        raise ValueError("completed arbitration Claim denominator differs")
    body = {
        "schema_version": "wang_claim_role_context_arbitration_progress_v1",
        "status": "proposals_only_not_final_role_ledger",
        "audit_sha256": audit["artifact_sha256"],
        "queue_sha256": queue["artifact_sha256"],
        "claim_denominator": len(audit["rows"]),
        "completed_claims": cursor,
        "remaining_claims": len(audit["rows"]) - cursor,
        "completed_batches": len(completed),
        "model_claim_counts": dict(sorted(Counter(row["model"] for row in decisions).items())),
        "raw_attempts": len(attempts),
        "invalid_attempts": [row["path"] for row in attempts if not row["valid"]],
        "masked_prior_hold_claim_ids": sorted({cid for row in completed
                                                  for cid in row["masked_holds"]}),
        "prompt_sha256s": sorted({row["artifact"]["prompt_sha256"] for row in completed}),
        "role_counts": dict(sorted(Counter(row["role"] for row in decisions).items())),
        "disposition_counts": dict(sorted(Counter(row["disposition"] for row in decisions).items())),
        "unresolved_candidate_reference_claim_ids": sorted(
            row["claim_id"] for row in decisions
            if row["role"] == "unresolved" and row["candidate_reference"]
        ),
        "decisions": decisions,
    }
    return base._artifact(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--additional-output-root", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = progress(base._read_json(args.audit), base._read_json(args.queue),
                      args.output_root, tuple(args.additional_output_root))
    if args.output:
        if args.output.exists():
            raise ValueError(f"refusing to overwrite: {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        base._write_immutable(args.output, report)
    print(json.dumps({key: value for key, value in report.items() if key != "decisions"},
                     sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
