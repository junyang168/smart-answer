"""Sequential, fail-closed continuation over an owner-approved accepted schedule.

Each intact group is freshly qualified/frozen, resolved by the existing runner,
and independently audited before advancing. No route worker is launched.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_source_attestation_runner import _write_immutable
from backend.pipeline.viewpoint_scoped_audit_disposition import scope_audit_disposition
from backend.api.canonical_repository.viewpoint_production_safety import accepted_grouping_audit_scope

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON = PROJECT_ROOT / "backend/.venv/bin/python"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def binding(path):
    path = Path(path).resolve()
    return {"path": str(path), "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def validate_schedule(schedule, manifest):
    groups = {g["group_key"]: g for g in manifest["groups"]}
    rows = schedule["schedule"]
    keys = [r["group_key"] for r in rows]
    if schedule["upstream_manifest_sha256"] != manifest["artifact_sha256"]:
        raise ValueError("schedule upstream SHA mismatch")
    if len(keys) != len(set(keys)) or set(keys) != set(groups):
        raise ValueError("schedule must cover each original accepted group exactly once")
    units = {u["unit_id"]: u for u in manifest["units"]}
    for i, row in enumerate(rows, 1):
        g = groups[row["group_key"]]
        if row["sequence"] != i or row["claim_count"] != len(g["claim_ids"]) or not 1 <= len(g["claim_ids"]) <= 20:
            raise ValueError("schedule sequence/count/capacity mismatch")
        if row["passage_key"] != units[g["unit_id"]]["passage_key"]:
            raise ValueError("schedule passage mismatch")
        if row["state"] not in {"pending", "already_complete"}:
            raise ValueError("invalid schedule state")
    for edge in schedule["precedence_annotations"]:
        if keys.index(edge["prerequisite"]) >= keys.index(edge["dependent"]):
            raise ValueError("schedule violates approved precedence")
    ids = [cid for g in groups.values() for cid in g["claim_ids"]]
    if len(ids) != len(set(ids)) or set(ids) != {c["claim_id"] for c in manifest["claim_packets"]}:
        raise ValueError("accepted grouping does not have exact unique Claim coverage")
    return groups


def validate_audit(audit, claim_ids, viewpoint_ids):
    if (audit.get("meta") or {}).get("model") != "gemini-3.8-flash":
        raise ValueError("unexpected independent audit model")
    for layer, expected, id_key in (("3", set(claim_ids), "claim_id"), ("4", set(viewpoint_ids), "viewpoint_id")):
        report = audit["layers"][layer]
        results = report["results"]
        if (not report.get("complete") or report.get("judged") != len(expected)
                or len(results) != len(expected) or {r[id_key] for r in results} != expected
                or any(report.get(k, 0) for k in ("disputed", "model_errors", "blocked", "no_source_text"))):
            raise ValueError(f"independent layer {layer} failed or incomplete")
        allowed = {"supported"} if layer == "3" else {"same"}
        if any(r.get("verdict") not in allowed for r in results):
            raise ValueError(f"independent layer {layer} has unresolved verdict")


def command(args):
    print("COMMAND", json.dumps([str(a) for a in args]), flush=True)
    subprocess.run([str(a) for a in args], cwd=PROJECT_ROOT, check=True)


def save_state(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".partial")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def investigate_deferred(root):
    dispositions = list((root / "production/run/batches").glob("*/batch-disposition-applied.json"))
    if len(dispositions) != 1 or not any(c["disposition"] == "deferred"
            for item in read(dispositions[0])["claims"] for c in item["components"]):
        return None
    command([PYTHON, "-m", "backend.pipeline.viewpoint_deferred_source_check",
        "--packet", dispositions[0].parent / "batch-packet.json",
        "--disposition", dispositions[0], "--output-root", root / "deferred-source-check"])
    return read(root / "deferred-source-check/handoff.json")


def build_selection(group, inventory, root):
    """Discover physical inputs; proof verification is performed by the adapter.

    A matching filename or inventory status never counts as review approval.
    Missing lineage and historical human holds stop the current group.
    """
    selected, sealed = [], []
    for cid in sorted(group["claim_ids"]):
        variants = inventory[cid]["variants"]
        usable = [v for v in variants if v["changed_substantive_fields"] in ([], ["occurrences"])]
        if len(usable) != 1:
            raise ValueError(f"{cid}: need unique current review lineage, found {len(usable)}")
        variant = usable[0]
        candidate = Path(variant["path"])
        if binding(candidate)["file_sha256"] != variant["file_sha256"]:
            raise ValueError(f"{cid}: inventory candidate file SHA drift")
        name = candidate.name.removesuffix(".reviewed-candidate.json")
        batch = candidate.parent.parent
        review_path = batch / "reviews" / (name + ".independent-review.json")
        review = read(review_path)
        input_path = Path(review["source"]["package_path"])
        row = {"claim_id": cid, "review_binding": binding(review_path), "review_input_binding": binding(input_path)}
        if variant["contract"] == "sealed_valid":
            paths = {"reviewed_candidate": candidate, "independent_review": review_path,
                     "adjudication": batch / "adjudications" / (name + ".ai-adjudication.json"),
                     "overrides": batch / "overrides" / (name + ".consensus-overrides.json")}
            sealed.append({"claim_id": cid, **{k + "_path": str(p.resolve()) for k, p in paths.items()},
                           **{k + "_sha256": binding(p)["file_sha256"] for k, p in paths.items()}})
        selected.append(row)
    if sealed:
        path = root / "sealed-lineage.json"
        body = {"claims": sealed}
        _write_immutable(path, body | {"artifact_sha256": sha256_json(body)})
        for row in selected:
            if row["claim_id"] in {s["claim_id"] for s in sealed}:
                row["sealed_lineage_binding"] = binding(path)
    _write_immutable(root / "review-selection.json", {"claims": selected})


def runner_args(root, args):
    inputs = root / "production/inputs"
    return [PYTHON, "-m", "backend.pipeline.viewpoint_batch_resolution_runner",
            "--packet", inputs / "scope-packet.json", "--freeze", inputs / "freeze.json",
            "--output-dir", root / "production/run", "--apply-authorization", inputs / "apply-authorization.json",
            "--accepted-grouping-sha", args.accepted_grouping_sha, "--grouping-repository", args.grouping_repository,
            "--cvp-policy", "backend/pipeline/policies/wang_cvp_resolution_policy_v3.json",
            "--route-policy", "backend/pipeline/policies/wang_route_resolution_policy_v1.json",
            "--proposal-provider", "codex", "--proposal-model", "gpt-6.1-sol", "--proposal-effort", "high",
            "--review-provider", "claude", "--review-model", "claude-opus-5-5", "--review-effort", "high",
            "--consolidation-provider", "claude", "--consolidation-model", "claude-opus-5-5", "--consolidation-effort", "high",
            "--group-model", "claude-opus-5-5", "--group-effort", "high", "--max-batches", "1"]


def qualify(row, group, inventory, root, args):
    inputs = root / "production/inputs"
    if (inputs / "freeze.json").exists():
        # Existing immutable phase is revalidated by the normal runner.
        return
    build_selection(group, inventory, root)
    command([PYTHON, "-m", "backend.pipeline.viewpoint_historical_review_bridge", "--selection", root / "review-selection.json", "--output", root / "historical-bridges.json"])
    # One verified full snapshot before this serialized campaign, plus the
    # normal per-group compensatable ChangeSets/readback history. Repeating a
    # full 129MB snapshot 154 times would exhaust the local machine's disk.
    backup = args.output_root / "backup.json"
    if not backup.exists():
        label = f"before-cvp-357-{args.output_root.name}"
        result = subprocess.run(["scripts/backup-registry.sh", label], cwd=PROJECT_ROOT, check=True, capture_output=True, text=True)
        print(result.stdout, flush=True)
        match = re.search(r"backup-registry: wrote (.+\.dump) \(", result.stdout)
        if not match:
            raise ValueError("backup path not reported")
        dump = Path(match[1])
        subprocess.run(["pg_restore", "--list", str(dump)], check=True, capture_output=True)
        _write_immutable(backup, binding(dump))
    b = read(backup)
    if binding(b["path"]) != b:
        raise ValueError("campaign baseline backup SHA drift")
    command([PYTHON, "-m", "backend.pipeline.viewpoint_reviewed_cvp_prepare",
             "--group-key", group["group_key"], "--grouping-repository", args.grouping_repository,
             "--accepted-grouping-sha", args.accepted_grouping_sha, "--historical-bridges", root / "historical-bridges.json",
             "--independent-audit", args.entrance_audit, "--output-root", root / "production",
             "--wang-repository", args.wang_repository,
             "--cvp-policy", "backend/pipeline/policies/wang_cvp_resolution_policy_v3.json",
             "--route-policy", "backend/pipeline/policies/wang_route_resolution_policy_v1.json",
             "--backup-dump", b["path"], "--backup-sha256", b["file_sha256"],
             "--global-lock", args.global_lock, "--scope-authorized-by", args.authorized_by,
             "--created-at", datetime.now(timezone.utc).isoformat()])


def execute(args):
    if binding(args.schedule)["file_sha256"] != args.schedule_sha256:
        raise ValueError("approved schedule file SHA mismatch")
    schedule = read(args.schedule)
    manifest = read(schedule["upstream_manifest_path"])
    groups = validate_schedule(schedule, manifest)
    if manifest["artifact_sha256"] != args.accepted_grouping_sha:
        raise ValueError("campaign grouping differs")
    inventory = {r["claim_id"]: r for r in read(args.lineage_inventory)["claims"]}
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
    config = {"schedule": binding(args.schedule), "lineage_inventory": binding(args.lineage_inventory),
              "entrance_audit": binding(args.entrance_audit), "completed_proofs": binding(args.completed_proofs),
              "grouping_repository": str(args.grouping_repository.resolve()), "wang_repository": str(args.wang_repository.resolve()),
              "global_lock": str(args.global_lock.resolve()), "runner_commit": commit, "authorized_by": args.authorized_by,
              "route_execution": False, "already_complete": schedule["completed_groups"]}
    _write_immutable(args.output_root / "campaign.json", config)
    seed = read(args.completed_proofs)
    if {r["group_key"] for r in seed["groups"]} != set(schedule["completed_groups"]):
        raise ValueError("completed-group proofs differ from schedule")
    for row in seed["groups"]:
        for b in row["bindings"]:
            if binding(b["path"]) != b:
                raise ValueError("completed-group evidence SHA drift")
        # A link-only repair does not invalidate the earlier unchanged Claim
        # audit. Keep both physical reports explicit, never synthesize a pass.
        audited = read(row["audit_path"])
        claim_audit = read(row.get("claim_audit_path", row["audit_path"]))
        validate_audit({"meta": audited["meta"], "layers": {"3": claim_audit["layers"]["3"], "4": audited["layers"]["4"]}},
                       groups[row["group_key"]]["claim_ids"], row["viewpoint_ids"])
    done = []
    for row in schedule["schedule"]:
        if row["state"] == "already_complete":
            continue
        root = args.output_root / f"g{row['sequence']:03d}"
        completion = root / "completion.json"
        if completion.exists():
            proof = read(completion)
            if proof["group_key"] != row["group_key"] or any(binding(b["path"]) != b for b in proof["bindings"]):
                raise ValueError("completed campaign group proof drift")
            validate_audit(read(proof["audit_path"]), groups[row["group_key"]]["claim_ids"], proof["viewpoint_ids"])
            done.append(row["group_key"])
            continue
        state = {"status": "qualifying", "group_key": row["group_key"], "sequence": row["sequence"], "completed_new_groups": len(done), "remaining_groups": 154-len(done)}
        save_state(args.output_root / "progress.json", state)
        try:
            if shutil.disk_usage(args.output_root).free < 2 * 1024**3:
                raise ValueError("less than 2 GiB free disk; stop safely before another group")
            qualify(row, groups[row["group_key"]], inventory, root, args)
            command(runner_args(root, args) + ["--dry-run"])
            if args.dry_run:
                save_state(args.output_root / "progress.json", state | {"status": "preflight_passed_no_model_calls"})
                return 0
            save_state(args.output_root / "progress.json", state | {"status": "generating_reviewing"})
            command(runner_args(root, args) + ["--apply"])
            run = read(root / "production/run/scope-run.json")
            if run["disposition_status"] != "complete":
                save_state(args.output_root / "progress.json", state | {"status": "investigating_deferred_source"})
                handoff = investigate_deferred(root)
                if handoff is not None:
                    raise ValueError("source investigation completed; required actions: " +
                                     ", ".join(sorted({a["next_action"] for a in handoff["actions"]})))
                raise ValueError("scope not completely applied/readback verified")
            plans = list((root / "production/run/batches").glob("*/cvp-change-plan.json"))
            if len(plans) != 1:
                raise ValueError("expected exactly one intact group plan")
            plan = read(plans[0])
            target_ids = sorted({o["payload"]["viewpoint_id"] for o in plan["operations"] if o["collection"] == "viewpoint_claim_links"})
            if not target_ids:
                raise ValueError("no viewpoint audit targets; needs explicit disposition review")
            audit_root = root / "independent-audit"
            save_state(args.output_root / "progress.json", state | {"status": "independent_audit", "viewpoint_ids": target_ids})
            audit_path = audit_root / "audit.json"
            if not audit_path.exists():
                command([PYTHON, "scripts/audit-library.py", "--scope", "all", "--layers", "1,2,3,4", "--claim-ids", ",".join(groups[row["group_key"]]["claim_ids"]), "--viewpoint-ids", ",".join(target_ids), "--workers", "2", "--model", "gemini-3.8-flash", "--out", audit_root, "--no-status-file"])
            audit = read(audit_path)
            validate_audit(audit, groups[row["group_key"]]["claim_ids"], target_ids)
            mechanical = {"meta": audit["meta"], "layers": {key: audit["layers"][key] for key in ("1", "2")}}
            disposition = scope_audit_disposition(audit=mechanical,
                manifest=accepted_grouping_audit_scope(manifest, "group:" + row["group_key"]),
                audit_file_sha256=binding(audit_path)["file_sha256"], authorized_by=args.authorized_by)
            _write_immutable(root / "post-apply-audit-disposition.json", disposition)
            if disposition["status"] != "passed":
                raise ValueError("post-apply mechanical audit has in-scope/global Registry findings")
            _write_immutable(completion, {"group_key": row["group_key"], "viewpoint_ids": target_ids, "audit_path": str(audit_path), "bindings": [binding(audit_path), binding(plans[0]), binding(root / "production/run/scope-run.json")], "argument_routes_completed": False})
            done.append(row["group_key"])
            save_state(args.output_root / "progress.json", state | {"status": "group_complete", "completed_new_groups": len(done), "remaining_groups": 154-len(done)})
        except Exception as exc:
            save_state(args.output_root / "progress.json", state | {"status": "blocked", "error": str(exc), "no_next_group_started": True})
            raise
    save_state(args.output_root / "progress.json", {"status": "complete", "completed_new_groups": len(done), "remaining_groups": 0, "argument_routes_completed": False})
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("schedule", "lineage-inventory", "completed-proofs", "grouping-repository", "entrance-audit", "output-root", "wang-repository", "global-lock"):
        parser.add_argument("--" + key, type=Path, required=True)
    for key in ("schedule-sha256", "accepted-grouping-sha", "authorized-by"):
        parser.add_argument("--" + key, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    with (args.output_root / "campaign.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return execute(args)


if __name__ == "__main__":
    sys.exit(main())
