"""Run generic deferred Claim revision; CVPs are impacts, not a new generator."""
import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from backend.pipeline import source_reconciliation as c
from backend.pipeline import deferred_claim_repair as d
from backend.pipeline.source_reconciliation_runner import Runner, binding, body, read, write, build_proposal_packet
from backend.pipeline.source_reconciliation_runtime import ProductionPorts, workspace, maintenance_lock, model_adapter, PROJECT
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


def prepare(campaign, output, owner, ports):
    from backend.pipeline.viewpoint_cvp_recovery_executor import load_campaign
    cfg,schedule,accepted,groups = load_campaign(campaign)
    progress = json.loads((campaign / "progress.json").read_text())
    group = campaign / f"g{progress['sequence']:03d}"
    paths = list((group / "production/run/batches").glob("*/batch-disposition-applied.json"))
    if len(paths) != 1: raise ValueError("one intact applied deferred batch required")
    batch = paths[0].parent
    ledger = json.loads((group / "production/run/scope-disposition-ledger.json").read_text())
    rows = ledger["claim_dispositions"]
    ids = groups[progress["group_key"]]["claim_ids"]
    if (ledger.get("artifact_sha256") != sha256_json({k:v for k,v in ledger.items() if k != "artifact_sha256"})
            or ledger["status"] != "incomplete" or ledger["grouping_claim_ids"] != sorted(ids)
            or len(rows) != len(ids) or {r["claim_id"] for r in rows} != set(ids)
            or any(ledger.get(k) for k in ("findings","blocked_claims","excluded_claims","unresolved_claim_ids"))
            or any(r["apply_status"] != "applied" or r["resolution_status"] not in {"resolved","deferred"} for r in rows)):
        raise ValueError("original ledger not intact; do not omit a held/unresolved Claim")
    deferred = sorted({r["claim_id"] for r in rows if r["resolution_status"] == "deferred"})
    auth = json.loads(owner.read_text())
    if auth.get("schema_version") != d.VERSION or not auth.get("authorization_id") or not auth.get("authorization_quote") or auth.get("human_content_approval") is not False:
        raise ValueError("explicit owner standing Claim repair authorization required")
    investigation = group / "deferred-source-check"
    handoff = d.artifact(c.FileBinding.model_validate(binding(investigation / "handoff.json")))
    repair = c.DeferredRepair(kind=d.route(handoff["actions"]),claim_ids=deferred,
        **{name:binding(investigation / filename) for name,filename in (
            ("investigation_packet","packet.json"),("handoff","handoff.json"),("raw_gpt","raw-gpt.json"),("raw_opus","raw-opus.json"))})
    states = ports.snapshot()
    packet = d.artifact(repair.investigation_packet)
    sources = {e["source_id"] for item in packet["items"] for e in item["evidence"]}
    if len(sources) != 1: raise ValueError("one Claim repair transaction must have one exact own source")
    sid = sources.pop()
    perms = d.permissions(states,sid,deferred,repair.kind)
    write(output / "authorization.json",{"source_id":sid,"authorization_id":d.VERSION+":"+d.problem_identity(packet,repair.kind),
        "authorization_quote":auth["authorization_quote"],"human_content_approval":False,"standing_policy":d.VERSION,
        "standing_authorization":binding(owner),"deferred_repair":repair.model_dump(),"permissions":[p.model_dump() for p in perms]})
    source = binding(Path(states[c.key("source_documents",sid)]["source_path"]))
    seeds = json.loads(Path(cfg["completed_proofs"]["path"]).read_text())["groups"]
    flags = {g["group_key"]:g for g in seeds}
    views_by_claim = {}
    for k,p in states.items():
        if k.startswith("viewpoint_claim_links/") and p.get("effective_state") == "active":
            views_by_claim.setdefault(p["claim_id"],set()).add(p["viewpoint_id"])
    manifest = c.Manifest(source_id=sid,before_source=source,current_sources=[source],authorization=binding(output / "authorization.json"),
        authorized_paragraph_keys=sorted({p["paragraph_key"] for item in packet["items"] for p in item["source_paragraphs"]}),
        permissions=perms,groups=[c.Group(group_key=g["group_key"],claim_ids=g["claim_ids"],
            viewpoint_ids=sorted({vid for cid in g["claim_ids"] for vid in views_by_claim.get(cid,set())}),
            revisit_required=flags.get(g["group_key"],{}).get("revisit_required",False),
            independent_audit_complete=flags.get(g["group_key"],{}).get("independent_audit_complete",True)) for g in accepted["groups"]],
        campaign_claim_ids=[cid for g in accepted["groups"] for cid in g["claim_ids"]],accepted_membership=binding(Path(schedule["upstream_manifest_path"])),
        created_at=auth["created_at"],deferred_repair=repair)
    d.validate_authority(manifest,states)
    from backend.api.canonical_repository.postgres_store import ChangeOperation, stored_operation_payload
    plan = json.loads((batch / "cvp-change-plan.json").read_text())
    if ports.store.get_change_set_status(plan["fingerprint_sha256"]) != "applied":
        raise ValueError("original deferred group ChangeSet not actually applied")
    for op in plan["operations"]:
        if states.get(c.key(op["collection"],op["object_id"])) != stored_operation_payload(ChangeOperation(**op)):
            raise ValueError("original applied group exact postimage drift")
    context = {"schema_version":d.VERSION,"campaign_root":str(campaign),"group_key":progress["group_key"],
        "claim_ids":ids,"repair_claim_ids":deferred,"kind":repair.kind,"original_batch":str(batch),
        "standing_authorization":binding(owner),"manifest":manifest.model_dump(),
        "original_applied_batch_never_replayed":True,"new_model_calls":0,"registry_writes":0}
    write(output / "input.json",context)
    return manifest,context


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root",type=Path,required=True)
    parser.add_argument("--output-root",type=Path,required=True)
    parser.add_argument("--authorization",type=Path,required=True)
    parser.add_argument("--prepare-only",action="store_true")
    args = parser.parse_args()
    load_dotenv(PROJECT / ".env")
    if PROJECT.name != "wkp-357-worktree-per-session": raise ValueError("#357 worktree only; primary read-only")
    with maintenance_lock():
        ports = ProductionPorts()
        output = args.output_root.resolve()
        try:
            manifest,context = prepare(args.campaign_root.resolve(),output,args.authorization.resolve(),ports)
            runner = Runner(workspace(),ports)
            frozen = runner.prepare(manifest)
            write(output / "claim-task.json",{"root":str(runner.root),"update_id":frozen["update_id"]})
            if args.prepare_only:
                adapter = model_adapter("proposal",deferred=True)
                packet = build_proposal_packet(frozen)
                size = adapter.request_bytes(packet)
                if size > 500000: raise ValueError("Claim repair actual request exceeds frozen 500000 byte ceiling")
                write(output / "preflight.json",{"status":"prepared_no_models_no_writes","request_bytes":size,
                    "request_encoding":adapter.request_encoding_proof(packet),"affected_records":len(frozen["inventory"]["affected_keys"]),
                    "model_calls":0,"Registry_writes":0})
                return
            from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
            runtime = canonical_runtime()
            os.environ["CODEX_EXECUTABLE"] = runtime["identity"]["cli"]["path"]
            write(output / "runtime-proof.json",runtime)
            result = runner.run(manifest,apply=True,audit=True)
            write(output / "result.json",{"status":"claim_repaired_affected_groups_audited",
                "claim_completion":binding(runner.root / "complete.json"),"effective_package":binding(runner.root / "effective-package.json"),
                "affected_group_keys":result["affected_group_keys"],"campaign_root":context["campaign_root"],
                "next_stage":"normal_current_claim_cvp_resolution","production_resumed":False,"group_complete":False,
                "Claim_revision_does_not_complete_deferred_CVP":True})
        except Exception as exc:
            write(output / "failure.json",{"error":str(exc),"automatic_retry":False,"group_complete":False,"production_resumed":False})
            raise


if __name__ == "__main__": main()
