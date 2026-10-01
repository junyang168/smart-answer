"""Read-only sizing of complete #395 semantic partitions, never auto-split them.

This is not the legacy capacity planner or a production grouping authorization.
It uses the exact grouping request constructor used by the CVP runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from backend.pipeline import claim_passage_role_runner as base
from backend.pipeline.claim_passage_role_audited_resume import _check_graph
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import canonical_json
from backend.api.canonical_repository.viewpoint_batch_resolution import ClaimGroupingResponse
from backend.api.canonical_repository.viewpoint_resolution import structured_json_request
from backend.pipeline.viewpoint_partition_manifest import (
    GROUPING_PROMPT, GROUPING_SCHEMA_NAME, grouping_payload, grouping_request_bytes,
    load_partition_policy,
)


def build_report(manifest, routing_packet, role_packet, deferred, policy, runner_commit):
    for value in (manifest, routing_packet, role_packet, deferred):
        base._check_artifact(value)
    if (manifest["packet_sha256"] != routing_packet["artifact_sha256"]
            or routing_packet["role_packet_sha256"] != role_packet["artifact_sha256"]
            or deferred["manifest_sha256"] != manifest["artifact_sha256"]
            or deferred["status"] != "deferred_by_user" or deferred["grouping_eligible"] is not False):
        raise ValueError("preflight input binding differs")
    rows = {r["claim_id"]: r for r in role_packet["claims"]}
    frozen = {r["claim_id"]: r for r in routing_packet["claims"]}
    owners = manifest["owners"]
    held = [r["claim_id"] for r in manifest["held"]]
    deferred_ids = [r["claim_id"] for r in deferred["claims"]]
    if (len(frozen) != len(routing_packet["claims"]) or len(rows) != len(role_packet["claims"])
            or len(set(held)) != len(held) or len(set(deferred_ids)) != len(deferred_ids)
            or set(deferred_ids) != set(held) or set(owners) & set(held)
            or set(owners) | set(held) != set(frozen)
            or manifest["claim_denominator"] != len(frozen)
            or manifest["counts"] != dict(sorted(Counter(o["partition"] for o in owners.values()).items()))):
        raise ValueError("preflight exact-once scope differs")
    for cid, owner in owners.items():
        if cid not in rows:
            raise ValueError("foreign Claim")
        for key in ("claim_revision", "claim_content_sha256", "source_id"):
            if owner[key] != frozen[cid][key] or owner[key] != rows[cid][key]:
                raise ValueError("preflight Claim/source pin drift")
        if rows[cid]["statement"] != frozen[cid]["statement"]:
            raise ValueError("preflight statement differs")
    if policy["max_request_bytes"] <= 0 or not 0 < policy["target_request_bytes"] < policy["max_request_bytes"]:
        raise ValueError("invalid request byte policy")
    partitions, seen = [], set()
    for name in sorted(manifest["counts"]):
        ids = sorted(cid for cid, owner in owners.items() if owner["partition"] == name)
        claims = [rows[cid] for cid in ids]
        if seen & set(ids) or set(ids) & set(held):
            raise ValueError("duplicate/deferred grouping Claim")
        seen.update(ids)
        payload = grouping_payload(name, claims)
        request = structured_json_request(payload, prompt=GROUPING_PROMPT.read_text(),
            response_model=ClaimGroupingResponse, schema_name=GROUPING_SCHEMA_NAME)
        serialized = canonical_json(request).encode("utf-8")
        size = grouping_request_bytes(name, claims)
        if size != len(serialized):
            raise ValueError("grouping estimator/runtime serialization differs")
        links = [r for r in manifest["preserved_relations"]
                 if r["from_id"] in ids or r["to_id"] in ids]
        partitions.append({"partition": name, "claim_count": len(ids), "claim_ids": ids,
            "source_count": len({r["source_id"] for r in claims}),
            "payload_sha256": base.sha256_json(payload),
            "request_sha256": hashlib.sha256(serialized).hexdigest(),
            "request_bytes": size, "within_hard_ceiling": size <= policy["max_request_bytes"],
            "above_target": size > policy["target_request_bytes"],
            "legacy_planner_claim_cap_exceeded": len(ids) > policy["max_claims_per_partition"],
            "incident_relation_count": len(links),
            "cross_partition_relation_count": sum(r["cross_partition"] for r in links),
            "split_performed": False})
    if seen != set(owners):
        raise ValueError("preflight owner coverage differs")
    oversized = [r["partition"] for r in partitions if not r["within_hard_ceiling"]]
    return base._artifact({"schema_version": "wang_other_partition_grouping_preflight_v1",
        "status": "capacity_blocked" if oversized else "capacity_checked_not_execution_authorization",
        "manifest_sha256": manifest["artifact_sha256"],
        "routing_packet_sha256": routing_packet["artifact_sha256"],
        "role_packet_sha256": role_packet["artifact_sha256"],
        "deferred_queue_sha256": deferred["artifact_sha256"],
        "policy": policy, "policy_sha256": base.sha256_json(policy),
        "grouping_prompt_sha256": hashlib.sha256(GROUPING_PROMPT.read_bytes()).hexdigest(),
        "grouping_schema_sha256": base.sha256_json(request["json_schema"]),
        "runner_commit": runner_commit,
        "runner_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "claim_denominator": len(frozen), "owned_claims": len(owners), "deferred_claims": len(held),
        "deferred_claim_ids": sorted(held), "missing": 0, "duplicate_ownership": 0, "foreign": 0,
        "partitions": partitions, "oversized_partitions": oversized,
        "preserved_relation_count": len(manifest["preserved_relations"]),
        "relations_sha256": base.sha256_json(manifest["preserved_relations"]),
        "execution_boundary": {
            "semantic_partition_manifest_is_not_legacy_execution_envelope": True,
            "legacy_claim_cap": policy["max_claims_per_partition"],
            "no_legacy_random_or_contiguous_split": True,
            "cross_partition_relations_preserved_in_manifest_not_added_to_grouping_payload": True,
            "requires_validated_scope_and_current_production_freeze_before_grouping": True},
        "would_call_models": False, "database_mutations": 0, "grouping_authorization": False})


def main():
    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "routing-packet", "role-packet", "roles", "deferred", "output-root"):
        p.add_argument("--" + name, type=Path, required=True)
    args = p.parse_args()
    if args.output_root.exists():
        raise ValueError("refusing to replace preflight output")
    manifest, routing_packet, role_packet, roles, deferred = [base._read_json(path) for path in
        (args.manifest, args.routing_packet, args.role_packet, args.roles, args.deferred)]
    script = Path(__file__).resolve().parents[2] / "scripts/audit-other-claim-partitions.py"
    spec = importlib.util.spec_from_file_location("independent_routing_audit", script)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    audit_report = audit.audit(routing_packet, manifest, roles,
                              input_roots=[r["root"] for r in manifest["input_roots"]])
    selected = set(manifest["owners"]) | {r["claim_id"] for r in manifest["held"]}
    _check_graph({"claims": [r for r in role_packet["claims"] if r["claim_id"] in selected]},
                 PostgresKnowledgeStore())
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    result = build_report(manifest, routing_packet, role_packet, deferred, load_partition_policy(), commit)
    result = base._artifact({k: v for k, v in result.items() if k != "artifact_sha256"} | {
        "independent_audit": audit_report, "current_claim_source_graph": "verified_read_only"})
    args.output_root.mkdir(parents=True)
    base._write_immutable(args.output_root / "preflight-report.json", result)
    print(json.dumps({k: result[k] for k in ("artifact_sha256", "status", "oversized_partitions",
                                           "owned_claims", "deferred_claims")}))


if __name__ == "__main__":
    main()
