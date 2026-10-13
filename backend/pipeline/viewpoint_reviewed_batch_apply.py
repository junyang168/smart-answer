"""Apply a saved passing batch after a non-semantic Registry link repair.

No model can be called. Reuses the normal batch validators/compiler, records a
new execution freeze without changing the old semantic freeze, and verifies
the complete ChangeSet readback before enqueuing ArgumentRoute work.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import (
    REGISTRY_COLLECTIONS, CORPUS_COLLECTIONS, build_cvp_freeze, validate_cvp_freeze,
    validate_content_addressed, collection_fingerprint, exclusive_cvp_run_lock,
    build_apply_intent, validate_registry_transition, build_plan_readback_receipt,
    file_sha256,
)
from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
from backend.api.canonical_repository.viewpoint_batch_changeset import compile_cvp_batch_package, load_revision_dependents
from backend.api.canonical_repository.viewpoint_batch_resolution import build_batch_disposition, build_route_resolution_job
from backend.api.canonical_repository.viewpoint_route_queue import FileRouteResolutionQueue
from backend.pipeline.viewpoint_batch_resolution_runner import (
    run_batch, build_proposer, build_reviewer, build_reconsiderer, build_consolidator,
    _route_receipt_from_package,
)
from backend.pipeline.viewpoint_scope_packet_runner import registry_context
from backend.pipeline.viewpoint_cvp_policy import load_cvp_policy, cvp_policy_fingerprint, cvp_policy_prompt_sha256s
from backend.pipeline.viewpoint_route_policy import load_route_policy, route_policy_fingerprint, route_policy_prompt_sha256s
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable, write_current_state, stable_decided_at

ROOT = Path(__file__).resolve().parents[2]


def prohibit_model_calls(adapter):
    def reject(*args, **kwargs):
        raise ValueError("saved reviewed batch is incomplete: model calls prohibited")
    adapter.generate = reject
    return adapter


def require_same_semantic_input(saved_packet, current_context):
    if saved_packet["registry_context"] != current_context:
        raise ValueError("Registry proposition context changed: saved review cannot be reused")


def run(phase: Path, output: Path, backup: Path, apply: bool):
    store = PostgresKnowledgeStore()
    old = read_artifact(phase / "inputs/freeze.json")
    validate_content_addressed(old, "artifact_sha256")
    with exclusive_cvp_run_lock(old):
        cp = load_cvp_policy(ROOT / "backend/pipeline/policies/wang_cvp_resolution_policy_v3.json")
        rp = load_route_policy(ROOT / "backend/pipeline/policies/wang_route_resolution_policy_v1.json")
        prompt_dir = ROOT / "backend/pipeline/prompts"
        cp_sha = cvp_policy_fingerprint(cp, prompt_sha256s=cvp_policy_prompt_sha256s(cp, prompt_dir=prompt_dir))
        rp_sha = route_policy_fingerprint(rp, prompt_sha256s=route_policy_prompt_sha256s(rp, prompt_dir=prompt_dir))
        if cp_sha != old["cvp_policy_sha256"] or rp_sha != old["route_policy_sha256"]:
            raise ValueError("model or route policy changed since review")
        if collection_fingerprint(store, CORPUS_COLLECTIONS) != old["corpus_fingerprint_sha256"]:
            raise ValueError("Claim/source corpus changed since review")
        batches = list((phase / "run/batches").iterdir())
        if len(batches) != 1:
            raise ValueError("recovery must select exactly one reviewed batch")
        batch = batches[0]
        saved_packet = read_artifact(batch / "batch-packet.json")
        saved_package = read_artifact(batch / "cvp-change-package.json")
        context = registry_context(store.list_records("canonical_viewpoints"), store.list_records("viewpoint_revisions"))
        require_same_semantic_input(saved_packet, context)
        claims = [ReviewClaim.model_validate(row) for row in saved_packet["claims"]]
        freeze = build_cvp_freeze(ticket_id=357, scope_packet_path=Path(old["scope_packet"]["path"]),
            prerequisite_paths={k: Path(v["path"]) for k, v in old["prerequisites"].items()},
            store=store, cvp_policy_sha256=cp_sha, route_policy_sha256=rp_sha,
            runner_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            worktree_root=ROOT, output_root=output, global_lock_path=Path(old["global_lock_path"]))
        write_immutable(output / "execution-freeze.json", freeze)
        adapters = []
        for builder, role in ((build_proposer, "proposal"), (build_reviewer, "review"),
                              (build_reconsiderer, "correction"), (build_consolidator, "consolidation")):
            cfg = cp[role]
            adapters.append(prohibit_model_calls(builder(cfg["model"], cfg["effort"], provider=cfg["provider"])))
        report = run_batch(batch_id=saved_packet["batch_id"], scope_label=saved_packet["scope_label"],
            claims=claims, registry_context=context, pending_candidates=[], output_dir=batch,
            proposer=adapters[0], reviewer=adapters[1], reconsiderer=adapters[2], consolidator=adapters[3],
            dependents_loader=lambda ids: load_revision_dependents(store=store, target_revision_ids=ids))
        if report["approval_outcome"] != "pass" or any(v for k, v in report["measurements"].items() if k.endswith("calls_executed")):
            raise ValueError("saved batch review failed or a model call occurred")
        proposal_raw = read_artifact(batch / "raw-reconsideration.json")
        review_raw = read_artifact(batch / "raw-final-review.json")
        package = compile_cvp_batch_package(proposal=report["_effective_proposal"], review=report["_review"],
            deterministic_validation_sha256=report["_effective_validation"]["artifact_sha256"],
            scope_manifest_sha256=old["claim_manifest_sha256"], claims=claims, registry_context=context,
            revision_dependents=report["_revision_dependents"], proposal_artifact_sha256=proposal_raw["artifact_sha256"],
            review_artifact_sha256=review_raw["artifact_sha256"], proposer_model_id=proposal_raw["model_id"],
            reviewer_model_id=review_raw["model_id"], decided_at=stable_decided_at(batch))
        if package != saved_package:
            raise ValueError("revalidated package differs from the reviewed saved package")
        plan = store.plan_package(package, source_kind="cvp_batch_resolution")
        write_immutable(output / "cvp-change-plan.json", plan.as_dict())
        grouping = read_artifact(phase / "inputs/grouping.json")
        grouping_sha = grouping["artifact_sha256"]
        identity = {"batch_id": saved_packet["batch_id"], "semantic_freeze_sha256": old["artifact_sha256"],
                    "execution_freeze_sha256": freeze["artifact_sha256"], "grouping_sha256": grouping_sha}
        authorization = {"user_message": "写入吧", "original_freeze_sha256": old["artifact_sha256"],
            "execution_freeze_sha256": freeze["artifact_sha256"], "batch_identity": identity,
            "package_sha256": sha256_json(package), "proposal_artifact_sha256": proposal_raw["artifact_sha256"],
            "final_review_artifact_sha256": review_raw["artifact_sha256"], "model_calls_executed": 0}
        if not apply:
            write_immutable(output / "dry-run-result.json", authorization | {"status": "passed"})
            return {"status": "dry_run_pass", "new_cvps": len(package["canonical_viewpoints"]), "operations": len(plan.operations)}
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
            raise ValueError("apply requires committed clean code")
        listing = subprocess.check_output(["pg_restore", "--list", str(backup)])
        authorization.update(backup_path=str(backup), backup_sha256=file_sha256(backup), pg_restore_list_sha256=__import__("hashlib").sha256(listing).hexdigest())
        authorization["artifact_sha256"] = sha256_json(authorization)
        write_immutable(output / "apply-authorization.json", authorization)
        validate_cvp_freeze(freeze, store=store, cvp_policy_sha256=cp_sha, route_policy_sha256=rp_sha, runner_commit=freeze["runner_commit"])
        intent = build_apply_intent(plan=plan, store=store, batch_identity=identity, freeze_sha256=freeze["artifact_sha256"],
            grouping_sha256=grouping_sha, expected_pre_registry_fingerprint_sha256=freeze["registry_fingerprint_sha256"])
        write_immutable(output / "cvp-apply-intent.json", intent)
        result = store.apply_plan(plan, metadata={"reviewed_batch_recovery": authorization},
            expected_registry_fingerprint_sha256=freeze["registry_fingerprint_sha256"], registry_collections=REGISTRY_COLLECTIONS)
        write_immutable(output / "cvp-apply-result.json", result)
        post = validate_registry_transition(intent, store=store)
        readback = build_plan_readback_receipt(plan=plan, store=store, batch_identity=identity, freeze_sha256=freeze["artifact_sha256"],
            grouping_sha256=grouping_sha, pre_registry_fingerprint_sha256=freeze["registry_fingerprint_sha256"], post_registry_fingerprint_sha256=post)
        write_immutable(output / "cvp-plan-readback-receipt.json", readback)
        receipt = _route_receipt_from_package(store=store, package=package, scope_label=saved_packet["scope_label"],
            scope_manifest_sha256=old["claim_manifest_sha256"], batch_id=saved_packet["batch_id"], plan=plan)
        write_immutable(output / "cvp-readback-receipt.json", receipt.model_dump(mode="json"))
        job = build_route_resolution_job(receipt=receipt, evidence_scope_sha256=old["scope_packet_sha256"], route_policy_fingerprint_sha256=rp_sha)
        FileRouteResolutionQueue(phase / "run/route-queue").enqueue(job)
        disposition = build_batch_disposition(proposal=report["_effective_proposal"], batch_identity=identity,
            effective_proposal_sha256=report["effective_proposal_sha256"], apply_status=result["status"], readback_receipt_sha256=readback["artifact_sha256"])
        write_immutable(output / "batch-disposition-applied.json", disposition)
        write_current_state(batch, schema_version="wang_canonical_viewpoint_batch_current_state_v1", identity=identity,
            status="applied_and_route_enqueued", authoritative_artifact=str(output / "cvp-plan-readback-receipt.json"), authoritative_artifact_sha256=readback["artifact_sha256"])
        return {"status": "applied_readback_verified", "new_cvps": len(package["canonical_viewpoints"]),
                "links": len(package["viewpoint_claim_links"]), "records_verified": len(readback["records"]),
                "change_set_id": plan.change_set_id, "route_job_id": job.job_id, "model_calls": 0}


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.phase_root, args.output_root, args.backup, args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
