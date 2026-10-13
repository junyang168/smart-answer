"""Freeze, dry-run and atomically repair authenticated stale CVP link history."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, VIEWPOINT_VALIDATION_COLLECTIONS
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import file_sha256, collection_fingerprint, exclusive_cvp_run_lock
from backend.pipeline.viewpoint_claim_repin_runner import _write_immutable
from backend.pipeline.viewpoint_link_history_repair import repair_link, verify_chain, authenticated_plan

ROOT = Path(__file__).resolve().parents[2]
SOURCE_KIND = "wkp357_authenticated_registry_link_repair"


def _history(cursor, collection, oid, first_revision=None, before_time=None):
    cursor.execute("""SELECT v.revision,v.content_sha256,v.payload,v.change_set_id,
                      cs.source_kind,cs.source_sha256,cs.status,cs.metadata,v.recorded_at
                      FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets cs USING(change_set_id)
                      WHERE v.collection=%s AND v.object_id=%s ORDER BY v.revision""", (collection, oid))
    rows = [dict(zip(("revision", "content_sha256", "payload", "change_set_id", "source_kind", "source_sha256", "status", "metadata", "recorded_at"), r)) for r in cursor.fetchall()]
    if first_revision is None:
        earlier = [r for r in rows if r["recorded_at"] <= before_time]
        if not earlier:
            raise ValueError(f"{collection}/{oid}: no historical generation before link")
        first_revision = earlier[-1]["revision"]
    return [r for r in rows if r["revision"] >= first_revision]


def run(output: Path, backup: Path | None, apply: bool) -> dict:
    store = PostgresKnowledgeStore()
    with exclusive_cvp_run_lock({"global_lock_path": str(output.parent / "registry-cvp.lock")}):
        authenticated_plan.cache_clear()
        with store.connect() as conn, conn.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor.execute("SELECT collection,object_id,revision,content_sha256,payload FROM wang_knowledge.objects WHERE retired_at IS NULL")
            states = {(col, oid): {"revision": rev, "content_sha256": sha, "payload": p} for col, oid, rev, sha, p in cursor.fetchall()}
            collections = sorted(set(VIEWPOINT_VALIDATION_COLLECTIONS) | {k[0] for k in states})
            pre = sha256_json({col: sorted(sha256_json(v["payload"]) for (c, _), v in states.items() if c == col) for col in collections})
            updated, proofs, blockers = [], [], []
            dependencies = {}
            for (col, oid), state in sorted(states.items()):
                if col != "viewpoint_claim_links" or state["payload"].get("effective_state") != "active":
                    continue
                link = state["payload"]
                claim = states[("claims", link["claim_id"])]["payload"]
                if claim["revision"] == link["pinned_claim_revision"]:
                    continue
                try:
                    chain = _history(cursor, "claims", link["claim_id"], link["pinned_claim_revision"])
                    new, proof = repair_link(link, claim=claim, history=chain)
                    # A later metadata-only repin must not move the evidence
                    # baseline forward and hide a change since the real decision.
                    cursor.execute("SELECT payload,recorded_at FROM wang_knowledge.object_versions WHERE collection=%s AND object_id=%s ORDER BY revision", (col, oid))
                    def basis(payload):
                        locator = deepcopy(payload.get("component_locator"))
                        if locator:
                            locator.pop("claim_sha256", None)
                        return {"decision_id": payload.get("decision_id"), "link_type": payload.get("link_type"),
                                "viewpoint_id": payload.get("viewpoint_id"), "evidence_bindings": payload.get("evidence_bindings"),
                                "component_locator": locator}
                    created = next(t for p, t in cursor.fetchall() if basis(p) == basis(link))
                    dependency_proofs = []
                    for binding in link.get("evidence_bindings", []):
                        for dc, did in (("evidence_steps", binding["evidence_step_id"]), ("source_fragments", binding["source_fragment_id"])):
                            key = (dc, did, str(created))
                            if key not in dependencies:
                                dchain = _history(cursor, dc, did, before_time=created)
                                dependencies[key] = {"collection": dc, "object_id": did,
                                                     "current_sha256": states[(dc, did)]["content_sha256"],
                                                     "transitions": verify_chain(dchain, collection=dc, object_id=did, current=states[(dc, did)]["payload"])}
                            dependency_proofs.append(dependencies[key])
                    proof["evidence_dependency_proofs"] = dependency_proofs
                    updated.append(new)
                    proofs.append(proof)
                except (ValueError, KeyError) as exc:
                    blockers.append({"link_id": oid, "claim_id": link["claim_id"], "reason": str(exc)})
            ids = {p["link_id"] for p in proofs} | {p["link_id"] for p in blockers}
            route_dependencies = [{"collection": col, "object_id": oid, "revision": row["revision"], "content_sha256": row["content_sha256"]}
                                  for (col, oid), row in sorted(states.items()) if col in {"argument_routes", "argument_route_revisions", "argument_route_attestations"}
                                  and any(lid in json.dumps(row["payload"]) for lid in ids)]
        report = {"schema_version": "wang_registry_link_history_repair_v1", "ticket": 357,
                  "status": "blocked" if blockers else "proved", "stale_link_count": len(ids),
                  "proved_link_count": len(proofs), "claim_count": len({p["claim_id"] for p in proofs}),
                  "viewpoint_count": len({r["viewpoint_id"] for r in updated}), "proofs": proofs,
                  "blockers": blockers, "route_dependencies": route_dependencies,
                  "registry_collections": collections, "pre_fingerprint_sha256": pre,
                  "model_calls": 0, "claim_writes": 0, "new_evidence_added_to_cvp": False,
                  "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
        report["artifact_sha256"] = sha256_json(report)
        _write_immutable(output / "proof-report.json", report)
        if blockers:
            return {k: report[k] for k in ("status", "stale_link_count", "proved_link_count", "blockers")}
        package = {"package_id": "VCL-HISTORY-REPAIR-" + report["artifact_sha256"][:20], "viewpoint_claim_links": updated}
        _write_immutable(output / "repair-package.json", package)
        plan = store.plan_package(package, source_kind=SOURCE_KIND)
        if any(op.collection != "viewpoint_claim_links" or op.operation != "update" for op in plan.operations) or len(plan.operations) != len(proofs):
            raise ValueError("repair must update exactly the proved existing links")
        _write_immutable(output / "dry-run-plan.json", plan.as_dict())
        result = {"status": "dry_run_pass", "repaired_link_count": len(proofs), "claim_count": report["claim_count"],
                  "viewpoint_count": report["viewpoint_count"], "dependent_route_records": len(route_dependencies)}
        if not apply:
            return result
        if not backup or not backup.is_absolute() or not backup.is_file():
            raise ValueError("independent database backup is required")
        subprocess.run(["pg_restore", "--list", str(backup)], check=True, capture_output=True)
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
        if dirty.strip():
            raise ValueError("repair apply requires committed clean code")
        # Reopen source bytes and plan files immediately before the transaction.
        authenticated_plan.cache_clear()
        for proof in proofs:
            all_transitions = list(proof["claim_transitions"])
            for dependency in proof["evidence_dependency_proofs"]:
                all_transitions.extend(dependency["transitions"])
            for transition in all_transitions:
                binding = transition.get("plan_binding")
                if binding:
                    authenticated_plan(binding["path"], binding["file_sha256"])
        intent = {"authorization": "user: 那你把他们修好", "proof_report_sha256": report["artifact_sha256"],
                  "plan_fingerprint_sha256": plan.fingerprint_sha256,
                  "backup_path": str(backup), "backup_file_sha256": file_sha256(backup),
                  "git_commit": report["git_commit"]}
        _write_immutable(output / "apply-intent.json", intent)
        receipt = store.apply_plan(plan, metadata={"authenticated_link_history_repair": intent},
                                   expected_registry_fingerprint_sha256=pre, registry_collections=collections)
        _write_immutable(output / "apply-receipt.json", receipt)
        # All old objects/versions and every non-link collection must be unchanged.
        for new in updated:
            actual = store.get_record_state("viewpoint_claim_links", new["viewpoint_claim_link_id"])
            if actual["payload"] != new:
                raise ValueError("link readback differs from planned version")
        with store.connect() as conn, conn.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            for proof in proofs:
                old = states[("viewpoint_claim_links", proof["link_id"])]
                cursor.execute("SELECT content_sha256,payload FROM wang_knowledge.object_versions WHERE collection='viewpoint_claim_links' AND object_id=%s AND revision=%s",
                               (proof["link_id"], old["revision"]))
                if cursor.fetchone() != (old["content_sha256"], old["payload"]):
                    raise ValueError("historical link version was not preserved")
        expected_states = deepcopy(states)
        for new in updated:
            expected_states[("viewpoint_claim_links", new["viewpoint_claim_link_id"])]["payload"] = new
        post = sha256_json({col: sorted(sha256_json(v["payload"]) for (c, _), v in expected_states.items() if c == col) for col in collections})
        if collection_fingerprint(store, collections) != post:
            raise ValueError("post-image mismatch or non-link records changed")
        # Global validator, including all existing routes, sees the real readback.
        store.plan_package(package, source_kind=SOURCE_KIND)
        result.update(status="applied_verified", change_set_id=plan.change_set_id,
                      pre_fingerprint_sha256=pre, post_fingerprint_sha256=post,
                      unchanged_claims_and_cvps=True, historical_versions_preserved=True)
        _write_immutable(output / "verified-result.json", result)
        return result


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.output_root, args.backup, args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
