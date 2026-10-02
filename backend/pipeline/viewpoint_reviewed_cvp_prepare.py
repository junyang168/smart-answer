"""Prepare the deterministic first CVP group from shared accepted grouping.

The full parent denominator is retained and rechecked. Only the mechanically
selected intact canary is qualified for this first production phase; nothing
asserts that the remaining groups have been processed or qualified for CVP.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import build_coverage_snapshot, build_input_claim_manifest, sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import (
    CvpProductionBlocked, accepted_grouping_canary, accepted_grouping_audit_scope,
    build_cvp_freeze, build_canary_selection, validate_apply_authorization, accepted_grouping_phase_group,
    CVP_APPLY_AUTHORIZATION_VERSION,
)
from backend.pipeline.viewpoint_grouping_repository import SharedGroupingRepository
from backend.pipeline.viewpoint_historical_source_eligibility import build_historical_source_eligibility
from backend.pipeline.viewpoint_scope_packet_runner import build_scope_packet
from backend.pipeline.viewpoint_scoped_audit_disposition import scope_audit_disposition
from backend.pipeline.viewpoint_source_attestation_runner import _write_immutable
from backend.pipeline.viewpoint_cvp_policy import load_cvp_policy, cvp_policy_fingerprint, cvp_policy_prompt_sha256s
from backend.pipeline.viewpoint_route_policy import load_route_policy, route_policy_fingerprint, route_policy_prompt_sha256s
from backend.pipeline.viewpoint_resolution_runtime import PROMPT_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def verify_parent_current_pins(manifest: dict[str, Any], records: dict[tuple[str, str], dict[str, Any]]) -> None:
    pins: dict[tuple[str, str], tuple[int, str]] = {}
    def pin(collection, oid, revision, content_sha):
        key, value = (collection, oid), (revision, content_sha)
        if key in pins and pins[key] != value:
            raise CvpProductionBlocked([f"{collection}/{oid}: conflicting accepted pins"])
        pins[key] = value
    for claim in manifest["claim_packets"]:
        pin("claims", claim["claim_id"], claim["claim_revision"], claim["claim_content_sha256"])
        for step in claim["evidence_steps"]:
            pin("evidence_steps", step["evidence_step_id"], step["revision"], step["content_sha256"])
            for fragment in step["fragments"]:
                pin("source_fragments", fragment["fragment_id"], fragment["revision"], fragment["content_sha256"])
    for source in manifest["sources"]:
        pin("source_documents", source["source_id"], source["source_revision"], source["source_content_sha256"])
    for key, expected in pins.items():
        current = records.get(key)
        if current is None or current.get("retired") or (current["revision"], current["content_sha256"]) != expected:
            raise CvpProductionBlocked([f"{key[0]}/{key[1]}: accepted parent current pin drift"])


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    load_dotenv(PROJECT_ROOT / ".env")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, text=True).strip():
        raise CvpProductionBlocked(["commit implementation before production preparation"])
    runner_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
    repository = SharedGroupingRepository(args.grouping_repository)
    loaded = repository.load(args.accepted_grouping_sha)
    manifest = loaded["manifest"]
    stage = "group:" + args.group_key if getattr(args, "group_key", None) else "canary"
    canary = accepted_grouping_phase_group(manifest, stage)
    selected_ids = sorted(canary["claim_ids"])
    wanted = set(selected_ids)
    all_ids = {row["claim_id"] for row in manifest["claim_packets"]}
    for claim in manifest["claim_packets"]:
        for step in claim["evidence_steps"]:
            all_ids.add(step["evidence_step_id"])
            all_ids.update(row["fragment_id"] for row in step["fragments"])
    all_ids.update(row["source_id"] for row in manifest["sources"])
    store = PostgresKnowledgeStore()
    records = {}
    with store.connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        for collection, oid, revision, content_sha, payload, retired_at in connection.execute(
            "SELECT collection,object_id,revision,content_sha256,payload,retired_at FROM wang_knowledge.objects WHERE object_id=ANY(%s)", (sorted(all_ids),),
        ).fetchall():
            records[(collection, oid)] = dict(revision=revision, content_sha256=content_sha, payload=payload, retired=retired_at is not None)
    verify_parent_current_pins(manifest, records)
    claims = [records[("claims", cid)]["payload"] for cid in selected_ids]
    source_ids = {row["source_id"] for row in manifest["claim_packets"] if row["claim_id"] in wanted}
    sources = [records[("source_documents", sid)]["payload"] for sid in sorted(source_ids)]
    evidence = [state["payload"] for (collection, _), state in records.items() if collection == "evidence_steps"]
    fragments = [state["payload"] for (collection, _), state in records.items() if collection == "source_fragments"]
    inputs = args.output_root / "inputs"
    universe = {"schema_version": "wang_cvp_phase_source_universe_v1",
                "source_universe_manifest_id": "CVP357-CANARY-" + args.accepted_grouping_sha[:20],
                "accepted_parent_manifest_sha256": args.accepted_grouping_sha,
                "stage": stage, "coverage_status": "partial",
                "sources": sorted([{"source_id": row["source_id"], "source_revision_id": f"{row['source_id']}@{row['revision']}", "source_sha256": row["source_sha256"]} for row in sources], key=lambda row: row["source_revision_id"])}
    universe["manifest_sha256"] = sha256_json(universe)
    coverage = build_coverage_snapshot(sources, roles_by_source={sid: ["source_universe"] for sid in source_ids},
                                      source_universe_manifest=universe, created_at=args.created_at, coverage_status="partial")
    claim_manifest = build_input_claim_manifest(claims, evidence, fragments, coverage)
    eligibility = build_historical_source_eligibility(claim_manifest=claim_manifest, bridge_manifest_path=args.historical_bridges, store=store)
    source_lineage = json.loads(args.historical_bridges.read_text(encoding="utf-8"))
    audit = json.loads(args.independent_audit.read_text(encoding="utf-8"))
    disposition = scope_audit_disposition(audit=audit, manifest=accepted_grouping_audit_scope(manifest, stage),
                                         audit_file_sha256=hashlib.sha256(args.independent_audit.read_bytes()).hexdigest(), authorized_by=args.scope_authorized_by)
    if disposition["status"] != "passed":
        raise CvpProductionBlocked(["first-group independent audit has in-scope findings"])
    # Both reports derive from checks above, not an operator-supplied pass flag.
    phase = {"schema_version": "wang_cvp_qualified_group_phase_v1", "status": "passed", "stage": stage,
             "accepted_parent_manifest_sha256": args.accepted_grouping_sha,
             "parent_claim_count": len(manifest["claim_packets"]), "parent_group_count": len(manifest["groups"]),
             "qualified_claim_ids": selected_ids, "qualified_group_key": canary["group_key"],
             "deferred_claim_ids": sorted({row["claim_id"] for row in manifest["claim_packets"]} - wanted),
             "parent_current_revision_content_pins_verified": True,
             "historical_bridge_sha256": source_lineage["artifact_sha256"],
             "source_eligibility_sha256": eligibility.artifact_sha256,
             "scope_audit_disposition_sha256": disposition["artifact_sha256"],
             "full_corpus_source_eligibility_asserted": False, "master_data_mutations": 0}
    phase["artifact_sha256"] = sha256_json(phase)
    packet = build_scope_packet(scope=None, scope_label="Matthew accepted group: " + canary["group_key"],
                               passage_unit_ids=[], claim_manifest=claim_manifest, source_attestation=eligibility,
                               repository_root=args.wang_repository, database_url=None,
                               partition_claim_ids=selected_ids, partition_manifest_sha256=args.accepted_grouping_sha,
                               include_source_context=True)
    packet.update(scope_kind="accepted_grouping_canary", accepted_grouping_stage=stage)
    packet.pop("packet_sha256")
    packet["packet_sha256"] = sha256_json(packet)
    files = {"source_universe_manifest": ("source-universe.json", universe), "coverage_snapshot": ("coverage.json", coverage.model_dump(mode="json")),
             "claim_manifest": ("claim-manifest.json", claim_manifest), "source_lineage_manifest": ("historical-lineage.json", source_lineage),
             "source_attestation": ("source-eligibility.json", eligibility.model_dump(mode="json")),
             "source_state_reconciliation": ("phase-reconciliation.json", phase), "source_state_validation": ("phase-validation.json", phase),
             "audit_disposition": ("audit-disposition.json", disposition)}
    prereqs = {"independent_audit": args.independent_audit,
               "reviewed_grouping_reference": repository.entry_path(args.accepted_grouping_sha)}
    for role, (name, artifact) in files.items():
        path = inputs / name
        _write_immutable(path, artifact)
        if role != "coverage_snapshot":
            prereqs[role] = path
    packet_path = inputs / "scope-packet.json"
    _write_immutable(packet_path, packet)
    cvp_policy = load_cvp_policy(args.cvp_policy)
    route_policy = load_route_policy(args.route_policy)
    freeze = build_cvp_freeze(ticket_id=357, scope_packet_path=packet_path, prerequisite_paths=prereqs, store=store,
        cvp_policy_sha256=cvp_policy_fingerprint(cvp_policy, prompt_sha256s=cvp_policy_prompt_sha256s(cvp_policy, prompt_dir=PROMPT_DIR)),
        route_policy_sha256=route_policy_fingerprint(route_policy, prompt_sha256s=route_policy_prompt_sha256s(route_policy, prompt_dir=PROMPT_DIR)),
        runner_commit=runner_commit, worktree_root=PROJECT_ROOT, output_root=args.output_root / "run", global_lock_path=args.global_lock)
    grouping = repository.admit(args.accepted_grouping_sha, scope_packet=packet, freeze=freeze)
    selection = build_canary_selection(grouping_envelope=grouping, freeze=freeze, scope_packet=packet, batch_size=int(cvp_policy["batch_size"])) if stage == "canary" else None
    backup_sha = hashlib.sha256(args.backup_dump.read_bytes()).hexdigest()
    if backup_sha != args.backup_sha256:
        raise CvpProductionBlocked(["backup dump differs from verified backup SHA"])
    restore = subprocess.run(["pg_restore", "--list", str(args.backup_dump)], check=True, capture_output=True).stdout
    authorization = {"schema_version": CVP_APPLY_AUTHORIZATION_VERSION, "status": "authorized", "authorized_by": args.scope_authorized_by,
                     "freeze_sha256": freeze["artifact_sha256"], "grouping_sha256": grouping["artifact_sha256"],
                     "backup_dump": {"path": str(args.backup_dump.resolve()), "sha256": backup_sha},
                     "pg_restore_list_sha256": hashlib.sha256(restore).hexdigest()}
    authorization["artifact_sha256"] = sha256_json(authorization)
    validate_apply_authorization(authorization, freeze=freeze, grouping_sha256=grouping["artifact_sha256"])
    for name, artifact in [("freeze.json", freeze), ("grouping.json", grouping), ("canary-selection.json", selection), ("apply-authorization.json", authorization)]:
        if artifact is not None:
            _write_immutable(inputs / name, artifact)
    return {"status": "prepared", "stage": stage, "group_key": canary["group_key"], "claims": len(selected_ids),
            "parent_claims": len(manifest["claim_packets"]), "parent_groups": len(manifest["groups"]),
            "runner_commit": runner_commit, "freeze_sha256": freeze["artifact_sha256"], "model_calls_executed": 0, "master_data_mutations": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("grouping-repository", "historical-bridges", "independent-audit", "output-root", "wang-repository", "cvp-policy", "route-policy", "backup-dump", "global-lock"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("accepted-grouping-sha", "scope-authorized-by", "created-at", "backup-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--group-key", help="one intact accepted group explicitly authorized for sequential continuation")
    print(json.dumps(prepare(parser.parse_args()), ensure_ascii=False))


if __name__ == "__main__":
    main()
