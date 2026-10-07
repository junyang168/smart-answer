"""Real subscription, PostgreSQL, backup and independent-audit boundaries.

No campaign/route/article launcher is present. Constructing model adapters is
local; models are invoked only by the runner after durable reservations.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock
from backend.pipeline import source_reconciliation as contract
from backend.pipeline.source_reconciliation_runner import Stop, binding, body, read, write, validate_package
from backend.pipeline.viewpoint_resolution_runtime import subscription_client
from backend.pipeline.viewpoint_semantic_contract import semantic_prompt

PROJECT = Path(__file__).resolve().parents[2]
PROMPTS = Path(__file__).resolve().parent / "prompts"


def workspace() -> Path:
    data = os.environ.get("DATA_BASE_DIR")
    if not data or not Path(data).is_absolute():
        raise ValueError("absolute DATA_BASE_DIR required; no repository-local runtime artifacts")
    return Path(data) / "wang-knowledge-platform/staging/source-reconciliation"


@contextmanager
def maintenance_lock():
    # Cooperates with #357 maintenance while it is paused. Different campaigns
    # can supply their existing coordinator path, never a new retry directory.
    recovery = Path(os.environ.get("WANG_CVP_RECOVERY_ROOT",
        str(workspace().parent / "cvp-production-357/immediate-recovery-20261002-v1")))
    import fcntl
    with (recovery / "recovery.lock").open("r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def model_adapter(stage: str, *, live: bool = False, deferred: bool = False):
    review = "review" in stage
    model, backend, provider = ("claude-opus-5-5", "claude_subscription", "claude") if review else ("gpt-6.1-sol", "codex_subscription", "codex")
    filename = "source_reconciliation_review.md" if review else "source_reconciliation_proposal.md"
    prompt = semantic_prompt((PROMPTS / filename).read_text(encoding="utf-8"))
    if deferred:
        prompt += "\n" + (PROMPTS / "deferred_claim_evidence_repair_v1.md").read_text(encoding="utf-8")
        if not review:
            prompt += "\n输出采用无损 JSON records 协议：每条 records 的 payload_json 是完整拟后像 JSON 字符串，不删字段；unadmitted_candidates_json 同理。程序逐字段解析为原 Proposal 再机械验证和交给 Opus。"
    if stage == "correction":
        prompt += "\n本次是唯一一轮纠正。逐条回应初次 Opus 意见，不得扩大范围；未解决项明确 unresolved。"
    if stage == "final_review":
        prompt += "\n本次是终局审核实际纠正稿。只通过或保留拒绝，不再安排第二轮纠正。"
    client = subscription_client(provider, model, "high") if live else SimpleNamespace(
        model=model, backend=backend, reasoning_effort="high", max_output_tokens=64000)
    if deferred and not review:
        from backend.pipeline.deferred_claim_repair import JsonProposal
    adapter = StructuredJsonReviewerAdapter(client=client, prompt=prompt,
        response_model=contract.Review if review else JsonProposal if deferred else contract.Proposal,
        schema_name="source_reconciliation_review_v1" if review else "deferred_claim_json_records_v1" if deferred else "source_reconciliation_proposal_v1",
        compact_payload=True, shared_text_payload=True, shared_record_payload=True)
    adapter.max_request_bytes = 500000
    return adapter


def expected_invalidations(before: dict, planned_post: dict, plan: dict) -> dict:
    """Mirror the SQL writer's deterministic dependency effects, not waive them."""
    result = deepcopy(planned_post)
    causes = {}
    for op in plan["operations"]:
        changed_collection, changed_id = op["collection"], op["object_id"]
        ids = []
        for k, p in before.items():
            if not k.startswith("product_dependencies/") or p.get("status", "current") != "current":
                continue
            references = p.get("dependency_manifest", [])
            if (changed_collection == "claims" and p.get("claim_id") == changed_id) or any(
                    r.get("collection") == changed_collection and r.get("record_id") == changed_id for r in references):
                ids.append(contract.split_key(k)[1])
                causes.setdefault(k, []).append(op)
        if ids:
            event_id = f"IMPACT-{plan['change_set_id']}-{changed_collection}-{changed_id}"
            event = {"impact_event_id": event_id, "changed_record_type": changed_collection,
                "changed_record_id": changed_id, "from_revision": op["before_revision"] or 0,
                "to_revision": op["after_revision"], "affected_dependency_ids": sorted(ids),
                "required_actions": ["review_affected_products", "withdraw_or_rebuild_published_consumers", "invalidate_qa_and_search_cache"],
                "status": "open", "review_status": "system_generated", "visibility": "internal", "revision": 1}
            event_key = contract.key("impact_events", event_id)
            if event_key in before:
                raise ValueError("derived impact event already exists before commit")
            result[event_key] = event
    for k, ops in causes.items():
        p = deepcopy(before[k])
        event_ids = [f"IMPACT-{plan['change_set_id']}-{o['collection']}-{o['object_id']}" for o in ops]
        p.update(status="invalidated", revision=p["revision"] + 1,
            invalidation_change_set_ids=sorted(set(p.get("invalidation_change_set_ids", [])) | {plan["change_set_id"]}),
            invalidation_event_ids=sorted(set(p.get("invalidation_event_ids", [])) | set(event_ids)))
        result[k] = p
    return result


class ProductionPorts:
    def __init__(self, store=None):
        self.store = store or PostgresKnowledgeStore()

    def snapshot(self) -> dict:
        with self.store.connect() as conn, conn.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor.execute("SELECT collection,object_id,payload FROM wang_knowledge.objects WHERE retired_at IS NULL")
            return {contract.key(c, o): p for c, o, p in cursor.fetchall()}

    def adapter(self, stage: str):
        return model_adapter(stage, live=True)

    def deferred_adapter(self, stage: str):
        return model_adapter(stage, live=True, deferred=True)

    def before_model_call(self, stage: str) -> None:
        # Cached responses do not run model/auth diagnostics. A new actual
        # invocation needs the committed implementation and physical backup
        # environment too, not only a check after the models have finished.
        self._environment_guard()

    def plan(self, package: dict) -> dict:
        return json.loads(json.dumps(self.store.plan_package(package, source_kind=contract.VERSION).as_dict()))

    def backup(self, root: Path) -> dict:
        self._environment_guard()
        completed = subprocess.run(["scripts/backup-registry.sh", "before-source-357-" + contract.sha256_json(str(root))[:32]],
            cwd=PROJECT, capture_output=True, text=True, check=True)
        match = re.search(r"backup-registry: wrote (.+\.dump) \(", completed.stdout)
        if not match:
            raise ValueError("backup did not identify the actual dump")
        path = Path(match.group(1))
        listing = subprocess.run(["pg_restore", "--list", str(path)], capture_output=True, check=True)
        if not listing.stdout.strip():
            raise ValueError("empty pg_restore catalog")
        return {"dump": binding(path), "restore_catalog_nonempty": True,
                "catalog_sha256": hashlib.sha256(listing.stdout).hexdigest(), "restore_list_exit_code": listing.returncode}

    def _environment_guard(self):
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=PROJECT, text=True).strip():
            raise ValueError("committed clean implementation required before production apply")
        if shutil.disk_usage(PROJECT).free < 2 * 1024**3:
            raise ValueError("less than 2GiB free disk; do not lower the threshold")
        # Preserve the owner's SSD requirement and UUID/archive validation.
        from backend.pipeline.viewpoint_backup_archive import verify_volume
        verify_volume()

    def committed(self, plan: dict) -> bool:
        with self.store.connect() as conn, conn.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT source_kind,source_sha256,fingerprint_sha256,status FROM wang_knowledge.change_sets WHERE change_set_id=%s", (plan["change_set_id"],))
            row = cursor.fetchone()
        if not row:
            return False
        if tuple(row) != (contract.VERSION, plan["source_sha256"], plan["fingerprint_sha256"], "applied"):
            raise ValueError("actual ChangeSet source/version/fingerprint/status drift")
        return True

    def apply(self, package: dict, plan: dict, frozen: dict) -> dict:
        self._environment_guard()
        validate_package(package)
        lock = {"global_lock_path": str(workspace().parent / "cvp-production-357/registry-cvp.lock")}
        with exclusive_cvp_run_lock(lock):
            contract.guard_files(frozen)
            if contract.fingerprint(self.snapshot()) != frozen["registry_sha256"]:
                raise ValueError("full Registry drift before write")
            rebuilt = self.store.plan_package(package, source_kind=contract.VERSION)
            if json.loads(json.dumps(rebuilt.as_dict())) != plan:
                raise ValueError("actual plan differs from exact dry-run")
            return self.store.apply_plan(rebuilt,
                metadata={"source_reconciliation_package": package},
                expected_registry_fingerprint_sha256=frozen["registry_sha256"],
                registry_collections=sorted({contract.split_key(k)[0] for k in contract.index(frozen["before_records"])}))

    def readback(self, plan: dict, expected: dict, before: dict) -> dict:
        if not self.committed(plan):
            raise ValueError("readback requires actual applied ChangeSet")
        actual = self.snapshot()
        if actual != expected_invalidations(before, expected, plan):
            raise ValueError("full Registry postimage or dependency-invalidation drift")
        return actual

    def audit(self, root: Path, claim_ids: list[str], viewpoint_ids: list[str]) -> dict:
        report = root / "independent-audit/audit.json"
        if not report.exists():
            from backend.pipeline.viewpoint_cvp_recovery_executor import run_logged, PYTHON
            args = [PYTHON, "scripts/audit-library.py", "--scope", "all", "--layers", "1,2,3,4" if viewpoint_ids else "1,2,3",
                "--claim-ids", ",".join(claim_ids), "--workers", "2", "--model", "gemini-3.8-flash",
                "--out", report.parent, "--no-status-file", "--preserve-full-context"]
            if viewpoint_ids:
                args += ["--viewpoint-ids", ",".join(viewpoint_ids)]
            run_logged(args, root, "independent-audit")
        return self._audit_result(root, claim_ids, viewpoint_ids, fresh=True)

    def audit_saved(self, root: Path, claim_ids: list[str], viewpoint_ids: list[str]) -> dict:
        if not (root / "independent-audit/audit.json").exists():
            raise Stop("audit reserved without a complete saved report; no rerun", status="call_uncertain", waiting_for="system")
        # Existing filtering/arbitration reservations are respected by their
        # precise caches. An uncertain stage may not be silently called again.
        return self._audit_result(root, claim_ids, viewpoint_ids, fresh=False)

    def _audit_result(self, root: Path, claim_ids: list[str], viewpoint_ids: list[str], *, fresh: bool) -> dict:
        from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit, apply as fallback
        from backend.pipeline.viewpoint_audit_source_adjudication import disputes, run as adjudicate
        from backend.pipeline.viewpoint_audit_owner_resolution import create_gpt_resolution
        from backend.pipeline.viewpoint_resolution_runtime import read_artifact
        original = root / "independent-audit/audit.json"
        proofs = verify_saved_full_audit(original)
        # This adapter only resumes a fully materialized result. No speculative
        # semantic/model restart to fill a partial audit chain.
        if not fresh and not (root / "validated-audit.json").exists():
            raise Stop("saved audit needs explicit recovery of its unfinished proof chain", status="proof_failure", waiting_for="system")
        if not fresh:
            result = body(read(root / "validated-audit.json"))
            self.validate_audit(result, claim_ids, viewpoint_ids)
            return result
        audit = fallback(original, root / "content-blocked-fallback")
        resolution = None
        if disputes(read_artifact(audit)):
            report = adjudicate(audit, root / "own-source-adjudication", self.store, final_gpt=True)
            if not report["actions"] or any(a["verdict"] != "audit_objection_rejected" for a in report["actions"]):
                raise Stop("independent objection upheld/unresolved; no automatic content correction")
            resolution = root / "gpt-source-final-resolution.json"
            create_gpt_resolution(audit, root / "own-source-adjudication", resolution, self.store)
        result = {"audit": binding(Path(audit)), "original_audit": binding(original), "source_proofs": proofs,
            "resolution": binding(resolution) if resolution else None,
            "claim_ids": claim_ids, "viewpoint_ids": viewpoint_ids,
            "revisit_required": Path(audit) != original,
            "independent_audit_complete": Path(audit) == original}
        self.validate_audit(result, claim_ids, viewpoint_ids)
        write(root / "validated-audit.json", result)
        return result

    def validate_audit(self, result: dict, claim_ids: list[str], viewpoint_ids: list[str]) -> None:
        from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
        from backend.pipeline.viewpoint_cvp_campaign_runner import validate_audit
        from backend.pipeline.viewpoint_resolution_runtime import read_artifact
        if result["claim_ids"] != claim_ids or result["viewpoint_ids"] != viewpoint_ids:
            raise ValueError("audit population differs from affected full group")
        path = contract.verify_file(result["audit"])
        original = contract.verify_file(result["original_audit"])
        states = self.snapshot()
        from backend.pipeline.immutable_proof_reuse import verify_once
        def verify_saved():
            if verify_saved_full_audit(original) != result["source_proofs"]:
                raise ValueError("actual independent input/cache/source proof drift")
        verify_once((str(original),contract.fingerprint(states)),
            [result['original_audit']]+result['source_proofs'],self.store,verify_saved)
        for b in result["source_proofs"]:
            contract.verify_file(b)
        resolution = contract.verify_file(result["resolution"]) if result["resolution"] else None
        audit = read_artifact(path)
        if not viewpoint_ids:
            # Existing validator has no zero-population special case. Supply a
            # mechanically empty layer, never a fabricated semantic judgement.
            audit = deepcopy(audit)
            audit["layers"]["4"] = {"complete": True, "judged": 0, "results": [], "disputed": 0}
        validate_audit(audit, claim_ids, viewpoint_ids, owner_resolution_path=resolution)
        # Isolate global Layer 1/2 findings using an actual current Claim scope.
        from backend.pipeline.viewpoint_scoped_audit_disposition import scope_audit_disposition
        packets = []
        for cid in claim_ids:
            claim = states[contract.key("claims", cid)]
            steps = []
            for eid in claim.get("evidence_step_ids", []):
                evidence = states[contract.key("evidence_steps", eid)]
                fids = set(evidence.get("source_fragment_ids", [])) | ({evidence["source_fragment_id"]} if evidence.get("source_fragment_id") else set())
                fragments = [states[contract.key("source_fragments", fid)] for fid in sorted(fids)]
                steps.append({"fragments": fragments})
            source_ids = {f["source_id"] for s in steps for f in s["fragments"]}
            if len(source_ids) != 1:
                raise ValueError("audit Claim source attribution missing or crossed sources")
            packets.append({"claim_id": cid, "source_id": next(iter(source_ids)), "evidence_steps": steps})
        scope = {"claim_packets": packets, "artifact_sha256": contract.sha256_json(packets)}
        disposition = scope_audit_disposition(audit={"meta": audit["meta"], "layers": {k: audit["layers"][k] for k in ("1", "2")}},
            manifest=scope, audit_file_sha256=result["audit"]["file_sha256"], authorized_by="source_reconciliation_scope")
        if disposition["status"] != "passed":
            raise ValueError("affected mechanical audit incomplete/failed")
        temporary = path != original
        if result["revisit_required"] != temporary or result["independent_audit_complete"] != (not temporary):
            raise ValueError("temporary non-independent audit flags changed")


def validate_apply_plan(plan, metadata: dict | None) -> str:
    """Called by PostgreSQL apply_plan before a transaction can write."""
    package = (metadata or {}).get("source_reconciliation_package")
    if not isinstance(package, dict):
        raise ValueError("source reconciliation requires its exact reviewed package at apply")
    validate_package(package)
    root = Path(package["source_repair_application"]["run_root"])
    frozen = body(read(root / "frozen.json"))
    saved = body(read(root / "plan.json"))
    intent = body(read(root / "apply-intent.json"))
    actual = json.loads(json.dumps(plan.as_dict()))
    if saved != actual or intent["plan_sha256"] != contract.sha256_json(actual) or intent["package_sha256"] != contract.sha256_json(package):
        raise ValueError("apply plan differs from durable reviewed intent")
    backup = body(read(root / "backup.json"))
    contract.verify_file(backup["dump"])
    if intent["backup_sha256"] != read(root / "backup.json")["artifact_sha256"] or not backup["restore_catalog_nonempty"]:
        raise ValueError("backup proof differs at write boundary")
    contract.assert_plan_postimages(package, actual, contract.index(frozen["before_records"]))
    return frozen["registry_sha256"]
