"""Return a repaired Claim to EXISTING CVP resolution, then prove its whole group.

No Claim/group/source constants, new proposal rules or missing-CVP factory.
Original applied batches and incomplete ledgers remain immutable.
"""
from copy import deepcopy
import json
import os
from pathlib import Path

from backend.pipeline import source_reconciliation as c
from backend.pipeline.source_reconciliation_runner import binding,body,read,write
from backend.pipeline.source_reconciliation_runtime import ProductionPorts,expected_invalidations
from backend.pipeline import source_reconciliation_epoch as epoch
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.postgres_store import ChangeOperation,stored_operation_payload

VERSION = "wang_deferred_claim_group_completion_v1"


def registry_collections(states):
    # Must be exactly the same cut as c.fingerprint, including empty tables.
    return sorted(set(c.KNOWLEDGE_COLLECTIONS) | {c.split_key(k)[0] for k in states})


def validate_prewrite_conflict(log, before, current, plan, intent, package, source_root):
    """A recorded pre-write SQL guard failure is not an uncertain commit."""
    log=Path(log); text=log.read_text()
    process=read(log.parent/"process.json"); result=read(log.parent/"exit-result.json")
    argv=process["argv"]
    if (result["exit_code"] != 1 or "--continue-saved" not in argv
            or "--source-root" not in argv
            or argv[argv.index("--source-root")+1] != str(source_root)
            or current != before or not plan["operations"]
            or any(o["operation"] != "create" or c.key(o["collection"],o["object_id"]) in current for o in plan["operations"])
            or intent["plan_sha256"] != sha256_json(plan)
            or intent["package_sha256"] != sha256_json(package)):
        raise ValueError("prewrite conflict lacks exact unchanged Registry/plan/invocation proof")
    old_cut=sorted({c.split_key(k)[0] for k in before})
    observed=c.fingerprint(before,collections=old_cut)
    expected=c.fingerprint(before)
    exact=("backend.api.canonical_repository.postgres_store.ChangeSetConflict: "
           f"CanonicalViewpoint Registry cut is stale: expected {expected}, found {observed}")
    if (observed == expected or "self._assert_registry_fingerprint(" not in text
            or text.rstrip().splitlines()[-1] != exact):
        raise ValueError("not the proven empty-collection prewrite guard conflict")
    return {"failure_log":binding(log),"failed_process":binding(log.parent/"process.json"),
        "failed_exit":binding(log.parent/"exit-result.json"),"plan_sha256":sha256_json(plan),
        "full_registry_sha256":expected,"old_nonempty_cut_sha256":observed,
        "omitted_empty_collections":sorted(set(registry_collections(before))-set(old_cut)),
        "old_intent_preserved":True,"automatic_retry":False}


def apply_normal(root, package, before, ports):
    from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock
    from backend.pipeline.source_reconciliation_runtime import workspace
    with exclusive_cvp_run_lock({"global_lock_path":str(workspace().parent/"cvp-production-357/registry-cvp.lock")}):
        return ports.store.apply_plan(ports.store.plan_package(package,source_kind="cvp_batch_resolution"),
            metadata={"generic_deferred_claim_normal_cvp":str(root)},
            expected_registry_fingerprint_sha256=c.fingerprint(before),registry_collections=registry_collections(before))


def committed(plan,store):
    with store.connect() as conn,conn.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SELECT source_kind,source_sha256,fingerprint_sha256,status FROM wang_knowledge.change_sets WHERE change_set_id=%s",(plan["change_set_id"],))
        row = cursor.fetchone()
    if row is None: return False
    if tuple(row) != ("cvp_batch_resolution",plan["source_sha256"],plan["fingerprint_sha256"],"applied"):
        raise ValueError("normal CVP actual SQL source/plan/status mismatch")
    return True


def validate_delta(plan, repair_claim_ids):
    allowed = {"canonical_viewpoints","viewpoint_revisions","viewpoint_claim_links",
        "viewpoint_identity_candidates","viewpoint_identity_decisions","viewpoint_relations",
        "viewpoint_structures","viewpoint_structure_revisions"}
    seen = set()
    for op in plan["operations"]:
        k = c.key(op["collection"],op["object_id"])
        if (k in seen or op["operation"] != "create" or op["collection"] not in allowed
                or op["before_revision"] is not None or op["after_revision"] != 1):
            raise ValueError("normal CVP delta changed an existing identity/record or replayed a write")
        if op["collection"] == "viewpoint_claim_links" and op["payload"]["claim_id"] not in repair_claim_ids:
            raise ValueError("normal delta added a foreign Claim/member")
        seen.add(k)


def resolved_report(report,ids):
    if report["approval_outcome"] != "pass" or report["escalations"]:
        raise ValueError("normal repaired-Claim CVP review unresolved; no retry loop")
    p = report["_effective_proposal"]
    if sorted(d.claim_id for d in p.claim_decisions) != sorted(ids):
        raise ValueError("normal CVP decision denominator changed")
    if any(x.disposition == "deferred" for d in p.claim_decisions for x in d.components):
        raise ValueError("repaired Claim still deferred; do not fabricate group completion")
    # The strict CVP response has no Route field; Route proposals have their
    # own schema. Still reject any supplied Route-bearing object rather than
    # assuming that absent field exists on the real CVP response.
    if p.viewpoint_revisions or getattr(p,"argument_route_candidates",[]):
        raise ValueError("normal delta needs existing CVP body/route changes outside Claim repair")


def adapters(cfg,cached_only=False):
    from backend.pipeline.viewpoint_batch_resolution_runner import build_proposer,build_reviewer,build_reconsiderer,build_consolidator
    from backend.pipeline.viewpoint_cvp_policy import load_cvp_policy
    from backend.pipeline.viewpoint_cvp_coverage_contract import enabled
    policy = load_cvp_policy(Path(cfg["cvp_policy"]["path"]))
    if binding(Path(cfg["cvp_policy"]["path"])) != cfg["cvp_policy"] or not enabled(policy):
        raise ValueError("normal continuation needs the exact current required-coverage policy")
    result = {}
    for role,builder in (("proposer",build_proposer),("reviewer",build_reviewer),("reconsiderer",build_reconsiderer),("consolidator",build_consolidator)):
        gpt = role in {"proposer","reconsiderer"}
        obj = builder("gpt-6.1-sol" if gpt else "claude-opus-5-5","high",provider="codex" if gpt else "claude",cvp_policy=policy)
        obj.max_request_bytes = 500000
        if cached_only:
            def no_fallback(payload): raise ValueError("saved normal CVP response missing; no model fallback")
            obj.generate = no_fallback
        result[role] = obj
    return result


def compile_claims(states,ids,claim_package):
    from backend.api.canonical_repository.viewpoint_resolution import compile_review_claim
    from backend.api.canonical_repository.models import Citation
    from backend.pipeline.viewpoint_evidence_source_context import attach_source_context
    attestation = binding(claim_package)
    claims = []
    for cid in sorted(ids):
        claim = states[c.key("claims",cid)]
        steps = {eid:states[c.key("evidence_steps",eid)] for eid in claim["evidence_step_ids"]}
        fids = {fid for e in steps.values() for fid in set(e.get("source_fragment_ids",[]))|({e["source_fragment_id"]} if e.get("source_fragment_id") else set())}
        fragments = {fid:states[c.key("source_fragments",fid)] for fid in fids}
        bridge = epoch.bridge(claim_id=cid,current_claim=claim,current_evidence=steps,current_fragments=fragments,source_repair_binding=attestation)
        sources = {f["source_id"] for f in fragments.values()}
        if len(sources) != 1: raise ValueError("normal repaired Claim crossed own source")
        source = states[c.key("source_documents",next(iter(sources)))]
        item,findings = compile_review_claim(claim=c.KNOWLEDGE_COLLECTIONS["claims"][0].model_validate(claim),
            evidence_index={eid:c.KNOWLEDGE_COLLECTIONS["evidence_steps"][0].model_validate(e) for eid,e in steps.items()},
            fragment_index={fid:c.KNOWLEDGE_COLLECTIONS["source_fragments"][0].model_validate(f) for fid,f in fragments.items()},
            citation_index={c.split_key(k)[1]:Citation.model_validate(p) for k,p in states.items() if k.startswith("citations/")},
            coverage_source_sha256={source["source_id"]:source["source_body_sha256"]},attestation_sha=bridge["artifact_sha256"])
        if item is None or findings: raise ValueError("fresh repaired Claim failed normal compilation: "+str(findings))
        claims.append(attach_source_context(item,source))
    return claims


def package_from_saved_or_new(root, packet, cfg, cached_only=False):
    from backend.pipeline.viewpoint_batch_resolution_runner import run_batch
    from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
    from backend.api.canonical_repository.viewpoint_batch_changeset import compile_cvp_batch_package
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,stable_decided_at
    from backend.pipeline.owner_model_reissue import directory
    models = adapters(cfg,cached_only)
    review_dir = directory(root,models["proposer"])
    report = run_batch(batch_id=packet["batch_id"],scope_label=packet["scope_label"],
        claims=[ReviewClaim.model_validate(x) for x in packet["claims"]],registry_context=packet["registry_context"],
        pending_candidates=[],output_dir=review_dir,current_claim_links=packet["existing_links"],
        **models)
    resolved_report(report,[x["claim_id"] for x in packet["claims"]])
    proposal = read_artifact(review_dir/("raw-reconsideration.json" if report["_final_review"] is not None else "raw-proposal.json"))
    review = read_artifact(review_dir/("raw-final-review.json" if report["_final_review"] is not None else "raw-review.json"))
    return compile_cvp_batch_package(proposal=report["_effective_proposal"],review=report["_review"],
        deterministic_validation_sha256=report["_effective_validation"]["artifact_sha256"],
        scope_manifest_sha256=packet["current_claim_manifest_sha256"],claims=[ReviewClaim.model_validate(x) for x in packet["claims"]],
        registry_context=packet["registry_context"],revision_dependents=report["_revision_dependents"],
        proposal_artifact_sha256=proposal["artifact_sha256"],review_artifact_sha256=review["artifact_sha256"],
        proposer_model_id=proposal["model_id"],reviewer_model_id=review["model_id"],decided_at=stable_decided_at(root),
        current_claim_links=packet["existing_links"])


def rewind(plan,states,before):
    result = deepcopy(states)
    for raw in plan["operations"]:
        op = ChangeOperation(**raw); k = c.key(op.collection,op.object_id)
        if result.get(k) != stored_operation_payload(op): raise ValueError("exact applied delta postimage drift")
        if op.operation == "create": del result[k]
        elif op.operation == "update": result[k] = before[k]
        else: raise ValueError("retirement not authorized")
    return result


def build_proof(root,campaign,ids,ports):
    context = body(read(root/"context.json")); packet = body(read(root/"packet.json"))
    if context["campaign_root"] != str(campaign) or sorted(context["claim_ids"]) != sorted(ids):
        raise ValueError("deferred completion original group/member binding drift")
    from backend.pipeline.viewpoint_cvp_recovery_executor import load_campaign
    cfg,_,_,groups = load_campaign(campaign)
    if sorted(groups[context["group_key"]]["claim_ids"]) != sorted(ids): raise ValueError("accepted group changed")
    claim_package = Path(context["claim_package"]["path"]); c.verify_file(context["claim_package"])
    epoch.verified_applied(binding(claim_package),ports.store)
    before = c.index(read(root/"before.json")["records"])
    plan = body(read(root/"plan.json")); validate_delta(plan,context["repair_claim_ids"])
    package = body(read(root/"effective-package.json"))
    if package_from_saved_or_new(root,packet,cfg,True) != package: raise ValueError("normal actual raw/package mismatch")
    c.assert_plan_postimages(package,plan,before)
    receipt = read(root/"apply-receipt.json")
    if receipt["status"] != "applied" or receipt["change_set_id"] != plan["change_set_id"] or not committed(plan,ports.store):
        raise ValueError("normal delta receipt lacks actual SQL proof")
    current = ports.snapshot(); reversed_normal = rewind(plan,current,before)
    if reversed_normal != before: raise ValueError("full Registry normal-delta reverse mismatch")
    claim_root = claim_package.parent
    frozen = body(read(claim_root/"frozen.json")); claim_plan = body(read(claim_root/"plan.json"))
    if sorted(frozen["manifest"]["deferred_repair"]["claim_ids"]) != sorted(context["repair_claim_ids"]):
        raise ValueError("normal CVP scope differs from actually repaired Claim identities")
    old = c.index(frozen["before_records"])
    from backend.pipeline.owner_approved_link_correction import source_projection
    projected = source_projection(read(claim_root/"complete.json"),reversed_normal,ports.store)
    if rewind(claim_plan,projected,old) != old: raise ValueError("full Registry Claim-repair reverse mismatch")
    original = Path(context["original_batch"])
    original_plan = json.loads((original/"cvp-change-plan.json").read_text())
    if ports.store.get_change_set_status(original_plan["fingerprint_sha256"]) != "applied": raise ValueError("original group not applied")
    for raw in original_plan["operations"]:
        op = ChangeOperation(**raw)
        if old.get(c.key(op.collection,op.object_id)) != stored_operation_payload(op): raise ValueError("original group postimage was not preserved before repair")
    ledger = json.loads((original.parent.parent/"scope-disposition-ledger.json").read_text())
    if ledger["status"] != "incomplete" or sorted(ledger["grouping_claim_ids"]) != sorted(ids): raise ValueError("original ledger changed")
    deferred = sorted(r["claim_id"] for r in ledger["claim_dispositions"] if r["resolution_status"] == "deferred")
    if deferred != sorted(context["repair_claim_ids"]): raise ValueError("deferred population changed")
    paths = [root/n for n in ("context.json","packet.json","before.json","effective-package.json","plan.json","backup.json","apply-intent.json","apply-receipt.json","readback.json")]
    paths += [p for p in (root/"review").glob("*.json") if p.name not in {"batch-run.json","measurements.json","current-state.json"}]
    from backend.pipeline.owner_model_reissue import directory, NAME
    if (root/NAME).exists():
        paths += [root/NAME,root/"runtime-proof.json"]
        paths += [p for p in directory(root).glob("*.json") if p.name not in {"batch-run.json","measurements.json","current-state.json"}]
    retry=root/"empty-collection-prewrite-recovery-start.json"
    if retry.exists():
        saved=read(retry); paths += [retry]
        paths += [c.verify_file(saved[k]) for k in ("failure_log","failed_process","failed_exit")]
    paths += [original/n for n in ("cvp-change-plan.json","cvp-change-package.json","cvp-readback-receipt.json")]
    paths += [original.parent.parent/n for n in ("scope-run.json","scope-disposition-ledger.json")]
    paths += [claim_root/n for n in ("complete.json","effective-package.json","frozen.json","plan.json","apply-intent.json","readback.json","backup.json")]
    for b in frozen["manifest"]["current_sources"]: c.verify_file(b)
    views = sorted({o["payload"]["viewpoint_id"] for o in plan["operations"] if o["collection"] == "viewpoint_claim_links"})
    return {"schema_version":VERSION,"campaign_root":str(campaign),"group_key":context["group_key"],"claim_ids":sorted(ids),
        "resolved_claim_ids":deferred,"status":"complete","historical_scope_run_preserved":True,"viewpoint_ids":views,
        "current_registry_sha256":c.fingerprint(current),"bindings":[binding(p) for p in sorted(set(paths))]+frozen["manifest"]["current_sources"],
        "new_model_calls":0,"registry_writes":0}


def verify_proof(campaign_root,path,claim_ids,store=None):
    root = Path(path).parent
    proof = read(Path(path)); rebuilt = build_proof(root,Path(campaign_root),claim_ids,ProductionPorts(store))
    if body(proof) != rebuilt: raise ValueError("generic deferred completion proof drift")
    return rebuilt


def run(context,claim_root,ports, *, prewrite_conflict_log=None):
    from backend.pipeline.viewpoint_cvp_recovery_executor import load_campaign,resume_after_repair
    from backend.pipeline.viewpoint_scope_packet_runner import registry_context
    campaign = Path(context["campaign_root"]); cfg,_,_,_ = load_campaign(campaign)
    root = claim_root/"normal-cvp"; root.mkdir(exist_ok=True)
    package_path = claim_root/"effective-package.json"
    epoch.verified_applied(binding(package_path),ports.store)
    write(root/"context.json",{**context,"claim_package":binding(package_path)})
    if not (root/"packet.json").exists():
        states = ports.snapshot(); claims = compile_claims(states,context["repair_claim_ids"],package_path)
        links = sorted([p for k,p in states.items() if k.startswith("viewpoint_claim_links/") and p["claim_id"] in context["repair_claim_ids"] and p["effective_state"] == "active"],key=lambda p:p["viewpoint_claim_link_id"])
        write(root/"before.json",{"records":c.rows(states),"fingerprint":c.fingerprint(states)})
        write(root/"packet.json",{"batch_id":"CVB-DEFERRED-"+sha256_json(binding(package_path))[:24],
            "scope_label":"Normal current-Claim CVP resolution after audited Claim repair",
            "claims":[x.model_dump(mode="json") for x in claims],"existing_links":links,
            "current_claim_manifest_sha256":sha256_json([states[c.key("claims",x)] for x in context["repair_claim_ids"]]),
            "registry_context":registry_context([p for k,p in states.items() if k.startswith("canonical_viewpoints/")],[p for k,p in states.items() if k.startswith("viewpoint_revisions/")])})
    packet = body(read(root/"packet.json")); before = c.index(read(root/"before.json")["records"])
    if not (root/"apply-intent.json").exists():
        ports._environment_guard()
        if ports.snapshot() != before: raise ValueError("normal CVP review base Registry drift")
        package = package_from_saved_or_new(root,packet,cfg)
        plan = json.loads(json.dumps(ports.store.plan_package(package,source_kind="cvp_batch_resolution").as_dict()))
        validate_delta(plan,context["repair_claim_ids"])
        write(root/"effective-package.json",package); write(root/"plan.json",plan)
        backup = ports.backup(root); write(root/"backup.json",backup)
        if ports.snapshot() != before or json.loads(json.dumps(ports.store.plan_package(package,source_kind="cvp_batch_resolution").as_dict())) != plan:
            raise ValueError("normal CVP dry-run/base changed before apply")
        write(root/"apply-intent.json",{"plan_sha256":sha256_json(plan),"package_sha256":sha256_json(package),"backup":binding(root/"backup.json")})
        receipt = apply_normal(root,package,before,ports)
        write(root/"apply-receipt.json",receipt)
    plan = body(read(root/"plan.json"))
    if not committed(plan,ports.store) and prewrite_conflict_log is not None:
        ports._environment_guard()
        package=body(read(root/"effective-package.json"))
        if package_from_saved_or_new(root,packet,cfg,True) != package:
            raise ValueError("saved approved package drift before prewrite recovery")
        validate_delta(plan,context["repair_claim_ids"])
        c.assert_plan_postimages(package,plan,before)
        if (root/"apply-receipt.json").exists() or ports.store.get_change_set_status(plan["fingerprint_sha256"]) is not None:
            raise ValueError("prewrite recovery cannot replay any recorded SQL ChangeSet")
        if json.loads(json.dumps(ports.store.plan_package(package,source_kind="cvp_batch_resolution").as_dict())) != plan:
            raise ValueError("saved exact plan drift")
        intent=body(read(root/"apply-intent.json")); c.verify_file(intent["backup"])
        backup=read(root/"backup.json"); dump=c.verify_file(backup["dump"])
        import subprocess,hashlib
        listing=subprocess.run(["pg_restore","--list",str(dump)],capture_output=True,check=True)
        if (not backup["restore_catalog_nonempty"] or not listing.stdout.strip()
                or hashlib.sha256(listing.stdout).hexdigest() != backup["catalog_sha256"]):
            raise ValueError("saved backup SHA/catalog proof drift")
        proof=validate_prewrite_conflict(prewrite_conflict_log,before,ports.snapshot(),plan,intent,package,claim_root)
        from backend.pipeline.viewpoint_resolution_runtime import write_immutable
        # One mechanical recovery reservation for this same uncommitted plan.
        write_immutable(root/"empty-collection-prewrite-recovery-start.json",proof,require_new=True)
        write(root/"apply-receipt.json",apply_normal(root,package,before,ports))
    if not committed(plan,ports.store): raise ValueError("normal CVP intent outcome unknown; do not replay")
    expected = deepcopy(before)
    for op in plan["operations"]: expected[c.key(op["collection"],op["object_id"])] = stored_operation_payload(ChangeOperation(**op))
    current = ports.snapshot()
    if current != expected_invalidations(before,expected,plan): raise ValueError("normal CVP full Registry readback mismatch")
    write(root/"readback.json",{"status":"verified","post_fingerprint_sha256":c.fingerprint(current)})
    proof = build_proof(root,campaign,context["claim_ids"],ports); write(root/"component-completion.json",proof)
    original = Path(context["original_batch"])
    from backend.pipeline.viewpoint_group_audit_population import group_audit_targets
    targets = sorted(set(group_audit_targets(original/"cvp-change-plan.json"))|set(proof["viewpoint_ids"]))
    auditroot = root/"full-group-audit"
    if (auditroot/"intent.json").exists(): outcome = ports.audit_saved(auditroot,sorted(context["claim_ids"]),targets)
    else:
        write(auditroot/"intent.json",{"claim_ids":sorted(context["claim_ids"]),"viewpoint_ids":targets})
        outcome = ports.audit(auditroot,sorted(context["claim_ids"]),targets)
    ports.validate_audit(outcome,sorted(context["claim_ids"]),targets)
    write(root/"full-group-audit-result.json",outcome)
    result = resume_after_repair(campaign,root/"continuation",campaign.parent/"immediate-recovery-20261002-v1",Path(os.environ["CODEX_EXECUTABLE"]),
        component_resolution_path=root/"component-completion.json",saved_audit_path=Path(outcome["audit"]["path"]),
        owner_resolution_path=Path(outcome["resolution"]["path"]) if outcome["resolution"] else None,
        additional_source_repair_paths=[package_path],additional_proof_bindings=proof["bindings"])
    write(root/"result.json",result)
    return result
