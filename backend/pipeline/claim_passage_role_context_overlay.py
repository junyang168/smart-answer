"""Apply the user-approved context arbitration to immutable role artifacts only.

Never writes Claim/Registry. Passage candidates remain pending identity checks,
and deferred rows retain their reasons, evidence and model provenance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path

from backend.pipeline import claim_passage_role_runner as base
from backend.pipeline.claim_passage_role_context_arbitration_progress import progress


def assemble(ledger: dict, queue: dict, report: dict) -> tuple[dict, dict]:
    for artifact in (ledger, queue, report):
        base._check_artifact(artifact)
    if (queue.get("role_ledger_sha256") != ledger["artifact_sha256"]
            or queue.get("role_packet_sha256") != ledger["packet_sha256"]
            or report.get("queue_sha256") != queue["artifact_sha256"]
            or report.get("remaining_claims") != 0
            or report.get("completed_claims") != len(queue["rows"])):
        raise ValueError("incomplete or differently bound arbitration")
    original = {row["claim_id"]: row for row in ledger["decisions"]}
    queued = {row["claim_id"]: row for row in queue["rows"]}
    answers = {row["claim_id"]: row for row in report["decisions"]}
    if (len(original) != len(ledger["decisions"])
            or len(queued) != len(queue["rows"])
            or len(answers) != len(report["decisions"])
            or set(answers) != set(queued)
            or not set(queued) <= set(original)):
        raise ValueError("arbitration denominator/ownership differs")
    decisions = deepcopy(ledger["decisions"])
    deferred = []
    applied = 0
    for row in decisions:
        cid = row["claim_id"]
        if cid not in answers:
            continue
        answer, prior = answers[cid], queued[cid]
        if row["role"] != "unresolved":
            raise ValueError("cannot overwrite a previously resolved role")
        for key in ("source_id", "claim_content_sha256", "source_content_sha256"):
            if answer[key] != prior[key]:
                raise ValueError(f"arbitration source binding differs: {cid}")
        role, disposition = answer["role"], answer["disposition"]
        if disposition == "resolved":
            if (role not in {"other", "passage_exegesis"}
                    or answer.get("prior_hold_masked")
                    or prior.get("reason_code") in {
                        "REVIEWED_HUMAN_DECISION_REQUIRED",
                        "REVIEWED_SOURCE_OR_CLAIM_REPAIR_REQUIRED"}):
                raise ValueError("cannot promote a reviewed hold")
            applied += 1
            row["role"] = role
            row["decision_basis"] = "user_approved_source_context_arbitration"
            # Approval here concerns role, not canonical passage ownership.
            row["interpreted_passage_keys"] = []
            row["passage_identity_status"] = (
                "pending_context_reference_verification" if role == "passage_exegesis"
                else "not_applicable")
        else:
            if role != "unresolved" or disposition not in {"needs_human", "repair_required"}:
                raise ValueError("invalid deferred role/disposition")
            deferred.append({**prior, "arbitration": answer,
                             "follow_up_status": "deferred_by_user_2026-09-30",
                             "candidate_reference_is_confirmed": False})
        row["context_arbitration"] = answer
        row["context_arbitration_disposition"] = disposition
    counts = dict(sorted(Counter(row["role"] for row in decisions).items()))
    body = {key: deepcopy(value) for key, value in ledger.items()
            if key not in {"artifact_sha256", "schema_version", "decisions", "counts", "status"}}
    body.update(schema_version="wang_claim_passage_role_ledger_v8",
                status="all_eligible_reviewed_with_user_approved_context_overlay",
                parent_role_ledger_sha256=ledger["artifact_sha256"],
                context_arbitration_progress_sha256=report["artifact_sha256"],
                context_audit_sha256=report["audit_sha256"],
                context_exception_queue_sha256=queue["artifact_sha256"],
                approval_scope="2026-09-30 user: defer 170; update resolved 576",
                applied_context_roles=applied, decisions=decisions, counts=counts,
                database_mutations=0, grouping_authorization=False)
    updated = base._artifact(body)
    held = base._artifact({
        "schema_version": "wang_claim_role_deferred_context_queue_v1",
        "status": "deferred_by_user_2026-09-30",
        "role_ledger_sha256": updated["artifact_sha256"],
        "parent_exception_queue_sha256": queue["artifact_sha256"],
        "context_arbitration_progress_sha256": report["artifact_sha256"],
        "exception_count": len(deferred),
        "disposition_counts": dict(sorted(Counter(
            row["arbitration"]["disposition"] for row in deferred).items())),
        "rows": deferred, "database_mutations": 0,
    })
    return updated, held


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("ledger", "queue", "audit", "output-root", "additional-output-root", "destination"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    report = progress(base._read_json(args.audit), base._read_json(args.queue),
                      args.output_root, (args.additional_output_root,))
    updated, held = assemble(base._read_json(args.ledger), base._read_json(args.queue), report)
    # The approved production scope is pinned; smaller fixtures test assemble().
    if (report["role_counts"] != {"other": 311, "passage_exegesis": 265, "unresolved": 170}
            or updated["counts"] != {"other": 8743, "passage_exegesis": 3843, "unresolved": 170}
            or len(updated["decisions"]) != 12756 or updated["applied_context_roles"] != 576
            or held["disposition_counts"] != {"needs_human": 145, "repair_required": 25}):
        raise ValueError("approved production scope differs")
    paths = {"context-arbitration-progress.final.json": report,
             "claim-passage-role-ledger.v8.json": updated,
             "claim-role-deferred-170.v1.json": held}
    if any((args.destination / name).exists() for name in paths):
        raise ValueError("refusing to overwrite existing output")
    args.destination.mkdir(parents=True, exist_ok=True)
    for name, value in paths.items():
        base._write_immutable(args.destination / name, value)
    print(updated["counts"])
    print("ledger SHA:", updated["artifact_sha256"])
    print("deferred SHA:", held["artifact_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
