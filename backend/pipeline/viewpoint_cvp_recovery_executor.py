"""Trusted, deterministic #357 repair handoff. Never executes model commands.

The model returns a read-only patch proposal. This parent validates its scope,
applies one patch, runs fixed tests, commits exact files, and verifies the failed
group before preparing a fresh campaign. No Claim/source or semantic repair is
authorized here. Frozen inputs and failed receipts are never overwritten.
"""
from __future__ import annotations

import argparse
import ast
import copy
import difflib
import os
from pathlib import Path, PurePosixPath
import subprocess
import time

from backend.pipeline.viewpoint_cvp_campaign_runner import (
    PROJECT_ROOT, PYTHON, binding, read, validate_audit, validate_schedule,
)
from backend.pipeline.viewpoint_resolution_runtime import write_immutable
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_scoped_audit_disposition import scope_audit_disposition
from backend.api.canonical_repository.viewpoint_production_safety import accepted_grouping_audit_scope

# Trusted policy, not model-selected paths or commands. Broader repairs require
# a separately reviewed policy change; the executor cannot repair itself.
REPAIR_FILES = ("scripts/audit-library.py",)
TESTS = (
    "backend/tests/test_audit_library_viewpoint_complete_sources.py",
    "backend/tests/test_audit_library_source_coordinates.py",
    "backend/tests/test_viewpoint_cvp_campaign_runner.py",
    "backend/tests/test_viewpoint_scoped_audit_disposition.py",
    "backend/tests/test_viewpoint_scoped_audit_admission.py",
    "backend/tests/test_viewpoint_cvp_recovery_executor.py",
    "backend/tests/test_viewpoint_cvp_recovery_supervisor.py",
)
PROPOSAL_SCHEMA = {"type": "object", "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["code_bug", "needs_owner"]},
        "reason": {"type": "string"},
        "changes": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": {"path": {"type": "string"}, "before_sha256": {"type": ["string", "null"]},
                           "after_text": {"type": "string"}},
            "required": ["path", "before_sha256", "after_text"]}}},
    "required": ["decision", "reason", "changes"]}


def validate_source_reader_patch(before, after):
    """Permit source helpers and source-read calls, not verdict/coverage changes."""
    helpers = {"_paragraphs_for_fragments", "_segment_for", "_whole_sources_for_claim"}
    class NormalizeSourceCalls(ast.NodeTransformer):
        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id in helpers:
                return ast.Name(id="BOUND_SOURCE_READ", ctx=ast.Load())
            return self.generic_visit(node)
    old, new = ast.parse(before), ast.parse(after)
    if len(old.body) != len(new.body):
        raise ValueError("protected audit policy changed: top-level structure")
    for a, b in zip(old.body, new.body):
        if isinstance(a, ast.FunctionDef) and isinstance(b, ast.FunctionDef) and a.name == b.name:
            if a.name in helpers:
                if ast.dump(a.args) != ast.dump(b.args) or ast.dump(ast.Module(body=a.decorator_list, type_ignores=[])) != ast.dump(ast.Module(body=b.decorator_list, type_ignores=[])):
                    raise ValueError("protected source-reader signature changed")
                continue
            if a.name in {"audit_claims", "audit_viewpoints"}:
                a, b = NormalizeSourceCalls().visit(a), NormalizeSourceCalls().visit(b)
        if ast.dump(a) != ast.dump(b):
            raise ValueError("protected audit policy changed outside source reading")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True).rstrip("\n")


def run_logged(argv, receipt_dir, name, *, input=None, env=None):
    """Only callers in this module supply argv; no shell or model commands."""
    if any((receipt_dir / (name + suffix)).exists() for suffix in (".stdout", ".stderr", ".json")):
        raise ValueError("existing command evidence; never overwrite/retry")
    with (receipt_dir / (name + ".stdout")).open("w") as out, (receipt_dir / (name + ".stderr")).open("w") as err:
        result = subprocess.run([str(a) for a in argv], cwd=PROJECT_ROOT, input=input,
                                text=True, stdout=out, stderr=err, check=False, env=env)
    write_immutable(receipt_dir / (name + ".json"), {"argv": [str(a) for a in argv], "exit_code": result.returncode})
    if result.returncode:
        raise ValueError(f"{name} failed; retained logs; no retry")


def snapshot(campaign_root=None):
    if PROJECT_ROOT.name != "wkp-357-worktree-per-session":
        raise ValueError("only the #357 worktree is authorized")
    if git("status", "--porcelain"):
        raise ValueError("dirty worktree; do not overwrite another session")
    result = {"head": git("rev-parse", "HEAD"),
            "branch": git("branch", "--show-current"),
            "files": {name: binding(PROJECT_ROOT / name)["file_sha256"] for name in REPAIR_FILES},
            "tests": {name: binding(PROJECT_ROOT / name)["file_sha256"] for name in TESTS}}
    if campaign_root is not None:
        load_campaign(Path(campaign_root))
        result["campaign_root"] = str(campaign_root)
        result["campaign_bindings"] = [binding(Path(campaign_root) / n) for n in ("campaign.json", "progress.json")]
    return result


def safe_path(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or str(path) != name:
        raise ValueError("noncanonical repair path")
    target = PROJECT_ROOT / name
    if target.is_symlink() or target.resolve() != target.absolute():
        raise ValueError("symlink repair target")
    return target


def validate_changes(proposal, baseline):
    if set(proposal) != {"decision", "reason", "changes"}:
        raise ValueError("unexpected proposal fields; commands are not accepted")
    if proposal["decision"] != "code_bug":
        raise ValueError("not a code-bug proposal; owner disposition required")
    changes = proposal["changes"]
    if not 1 <= len(changes) <= 3 or len({c["path"] for c in changes}) != len(changes):
        raise ValueError("invalid/duplicate repair files")
    patch = []
    for c in changes:
        if set(c) != {"path", "before_sha256", "after_text"}:
            raise ValueError("invalid change fields")
        name, text = c["path"], c["after_text"]
        target = safe_path(name)
        is_new_test = (name.startswith("backend/tests/test_cvp_recovery_case_") and
                       name.endswith(".py") and "/" not in name.removeprefix("backend/tests/"))
        if name not in REPAIR_FILES and not is_new_test:
            raise ValueError("file outside trusted repair policy")
        if not isinstance(text, str) or not text.strip() or len(text.encode()) > 250000 or not text.endswith("\n"):
            raise ValueError("empty/oversized/noncanonical replacement")
        if is_new_test:
            if target.exists() or c["before_sha256"] is not None:
                raise ValueError("existing tests cannot be changed")
            before = ""
        else:
            expected = baseline["files"][name]
            if c["before_sha256"] != expected or binding(target)["file_sha256"] != expected:
                raise ValueError("repair baseline SHA drift")
            before = target.read_text()
            validate_source_reader_patch(before, text)
        ast.parse(text)
        if text == before:
            raise ValueError("no-op patch")
        header = f"diff --git a/{name} b/{name}\n"
        if not before:
            header += "new file mode 100644\n"
        patch.append(header + "".join(difflib.unified_diff(before.splitlines(True), text.splitlines(True),
            fromfile=f"a/{name}" if before else "/dev/null", tofile=f"b/{name}")))
    if not any(c["path"] in REPAIR_FILES for c in changes):
        raise ValueError("test-only proposal is not a code repair")
    if not any(c["path"].startswith("backend/tests/") for c in changes):
        raise ValueError("repair requires a new regression test")
    return "".join(patch)


def apply_and_commit(proposal, baseline, attempt):
    if snapshot(baseline.get("campaign_root")) != baseline:
        raise ValueError("HEAD/branch/test/input drift since proposal began")
    patch = validate_changes(proposal, baseline)
    write_immutable(attempt / "accepted-patch.json", {"patch": patch, "baseline": baseline,
        "proposal_file_sha256": binding(attempt / "proposal.json")["file_sha256"]})
    run_logged(["git", "apply", "--check", "-"], attempt, "patch-check", input=patch)
    run_logged(["git", "apply", "-"], attempt, "patch-apply", input=patch)
    names = sorted(c["path"] for c in proposal["changes"])
    after = {name: binding(PROJECT_ROOT / name)["file_sha256"] for name in names}
    added_tests = [name for name in names if name.startswith("backend/tests/")]
    run_logged([PYTHON, "-m", "pytest", *TESTS, *added_tests, "-q"], attempt, "regression")
    run_logged(["git", "diff", "--check"], attempt, "diff-check")
    if git("rev-parse", "HEAD") != baseline["head"] or git("branch", "--show-current") != baseline["branch"]:
        raise ValueError("Git identity changed during tests")
    if {name: binding(PROJECT_ROOT / name)["file_sha256"] for name in names} != after:
        raise ValueError("tests changed repair files")
    if {name: binding(PROJECT_ROOT / name)["file_sha256"] for name in TESTS} != baseline["tests"]:
        raise ValueError("existing gate tests changed")
    for b in baseline.get("campaign_bindings", []):
        verify_binding(b)
    if baseline.get("campaign_root"):
        load_campaign(Path(baseline["campaign_root"]))
    dirty = {line[3:] for line in git("status", "--porcelain", "--untracked-files=all").splitlines()}
    if dirty != set(names) or git("diff", "--cached", "--name-only"):
        raise ValueError("unexpected worktree/index changes")
    run_logged(["git", "add", "--", *names], attempt, "stage")
    if set(git("diff", "--cached", "--name-only").splitlines()) != set(names):
        raise ValueError("staged files differ from accepted repair")
    run_logged(["git", "commit", "-m", "fix(cvp): apply validated bounded recovery patch"], attempt, "commit")
    receipt = {"commit": git("rev-parse", "HEAD"), "files": after, "repair_count": 1,
               "master_data_mutations": 0, "production_restarted": False}
    write_immutable(attempt / "code-repair-receipt.json", receipt)
    return receipt


def verify_binding(b, *, parse_json=True):
    if binding(b["path"]) != b:
        raise ValueError("physical artifact SHA drift")
    return read(b["path"]) if parse_json else None


def load_campaign(root):
    cfg = read(root / "campaign.json")
    for key in ("schedule", "lineage_inventory", "entrance_audit", "completed_proofs"):
        verify_binding(cfg[key])
    for b in cfg.get("completed_source_repairs", []) + ([cfg["random_spot_check_policy"]] if cfg.get("random_spot_check_policy") else []):
        verify_binding(b)
    schedule = read(cfg["schedule"]["path"])
    manifest = read(schedule["upstream_manifest_path"])
    groups = validate_schedule(schedule, manifest)
    return cfg, schedule, manifest, groups


def verify_group_audit(root, cfg, schedule, manifest, groups, audit_path, output):
    state = read(root / "progress.json")
    group_key = state["group_key"]
    group_root = root / f"g{state['sequence']:03d}"
    scope_run = group_root / "production/run/scope-run.json"
    if read(scope_run)["disposition_status"] != "complete":
        raise ValueError("cannot promote a partially applied group")
    plans = list((group_root / "production/run/batches").glob("*/cvp-change-plan.json"))
    if len(plans) != 1:
        raise ValueError("expected one intact group plan")
    targets = sorted({o["payload"]["viewpoint_id"] for o in read(plans[0])["operations"]
                      if o["collection"] == "viewpoint_claim_links"})
    if not targets:
        raise ValueError("empty viewpoint population cannot prove completion")
    rb = plans[0].parent / "cvp-readback-receipt.json"
    if read(rb).get("readback_status") != "verified":
        raise ValueError("missing verified readback")
    audit = read(audit_path)
    validate_audit(audit, groups[group_key]["claim_ids"], targets)
    disposition = scope_audit_disposition(
        audit={"meta": audit["meta"], "layers": {k: audit["layers"][k] for k in ("1", "2")}},
        manifest=accepted_grouping_audit_scope(manifest, "group:" + group_key),
        audit_file_sha256=binding(audit_path)["file_sha256"], authorized_by=cfg["authorized_by"])
    write_immutable(output / "audit-disposition.json", disposition)
    if disposition["status"] != "passed":
        raise ValueError("mechanical audit has selected/global integrity findings")
    proof = {"group_key": group_key, "viewpoint_ids": targets, "audit_path": str(audit_path),
             "bindings": [binding(audit_path), binding(plans[0]), binding(scope_run), binding(rb)],
             "argument_routes_completed": False}
    write_immutable(output / "completion.json", proof)
    return proof


def continuation_inputs(root, output, extra_completion=None, *, additional_source_repair_paths=()):
    cfg, schedule, manifest, groups = load_campaign(root)
    seeds = read(cfg["completed_proofs"]["path"])["groups"][:]
    for row in schedule["schedule"]:
        completion = root / f"g{row['sequence']:03d}" / "completion.json"
        if row["state"] == "pending" and completion.exists():
            seeds.append(read(completion))
    if extra_completion:
        seeds.append(extra_completion)
    keys = [s["group_key"] for s in seeds]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate completion proof")
    for seed in seeds:
        for b in seed["bindings"]:
            # A proof may bind an immutable explanatory Markdown record as
            # well as its JSON evidence. Verify every byte; only the actual
            # audit/plan consumers parse their required JSON below.
            verify_binding(b, parse_json=False)
        audited = read(seed["audit_path"])
        ca = read(seed.get("claim_audit_path", seed["audit_path"]))
        validate_audit({"meta": audited["meta"], "layers": {"3": ca["layers"]["3"], "4": audited["layers"]["4"]}},
                       groups[seed["group_key"]]["claim_ids"], seed["viewpoint_ids"])
    derived = copy.deepcopy(schedule)
    for row in derived["schedule"]:
        row["state"] = "already_complete" if row["group_key"] in keys else "pending"
    derived["completed_groups"] = [r["group_key"] for r in derived["schedule"] if r["state"] == "already_complete"]
    validate_schedule(derived, manifest)
    write_immutable(output / "schedule.json", derived)
    write_immutable(output / "completed-proofs.json", {"groups": seeds})
    argv = [PYTHON, "-m", "backend.pipeline.viewpoint_cvp_campaign_runner",
            "--schedule", output / "schedule.json", "--schedule-sha256", binding(output / "schedule.json")["file_sha256"],
            "--completed-proofs", output / "completed-proofs.json", "--output-root", output,
            "--accepted-grouping-sha", manifest["artifact_sha256"], "--authorized-by", cfg["authorized_by"]]
    for flag, key in (("lineage-inventory", "lineage_inventory"), ("entrance-audit", "entrance_audit")):
        argv.extend(["--" + flag, cfg[key]["path"]])
    for key in ("grouping_repository", "wang_repository", "global_lock"):
        argv.extend(["--" + key.replace("_", "-"), cfg[key]])
    for b in cfg.get("completed_source_repairs", []):
        argv.extend(["--completed-source-repair", b["path"]])
    for path in additional_source_repair_paths:
        argv.extend(["--completed-source-repair", str(path)])
    if cfg.get("random_spot_check_policy"):
        argv.extend(["--random-spot-check-policy", cfg["random_spot_check_policy"]["path"]])
    return argv


def continuation_root(root, attempt):
    # Different immutable repair roots may have identically named handoffs.
    # Their absolute identity, not a truncated basename, owns the new root.
    return root.parent / (root.name + '-recovery-' + sha256_json({'attempt':str(attempt.resolve())})[:12])


def resume_after_repair(root, attempt, recovery_root, executable, *, additional_proof_bindings=(), additional_source_repair_paths=(), saved_audit_path=None, prior_model_batch=None, reuse_correction=False):
    cfg, schedule, manifest, groups = load_campaign(root)
    state = read(root / "progress.json")
    group_root = root / f"g{state['sequence']:03d}"
    extra = None
    if (group_root / "production/run/scope-run.json").exists():
        # Already-written records are never generated/applied twice. Re-audit
        # exactly the bound Claim and Viewpoint population in a fresh directory.
        if read(group_root / "production/run/scope-run.json")["disposition_status"] != "complete":
            raise ValueError("partial/deferred content needs owner disposition")
        plans = list((group_root / "production/run/batches").glob("*/cvp-change-plan.json"))
        if len(plans) != 1:
            raise ValueError("ambiguous intact group plan")
        targets = sorted({o["payload"]["viewpoint_id"] for o in read(plans[0])["operations"]
                          if o["collection"] == "viewpoint_claim_links"})
        audit_dir = attempt / "independent-reaudit"
        if saved_audit_path is None:
            run_logged([PYTHON, "scripts/audit-library.py", "--scope", "all", "--layers", "1,2,3,4",
                        "--claim-ids", ",".join(groups[state["group_key"]]["claim_ids"]),
                        "--viewpoint-ids", ",".join(targets), "--workers", "2", "--model", "gemini-3.8-flash",
                        "--out", audit_dir, "--no-status-file"], attempt, "independent-reaudit")
            saved_audit_path = audit_dir / "audit.json"
        else:
            # Caller first verifies its applied target/full-registry readback.
            # Reuse a saved real audit, never fabricate a fresh model pass.
            write_immutable(attempt / 'saved-audit-binding.json', binding(saved_audit_path))
        extra = verify_group_audit(root, cfg, schedule, manifest, groups, saved_audit_path, attempt / "verified-group")
        if additional_proof_bindings:
            for b in additional_proof_bindings:
                verify_binding(b)
            extra = extra | {"bindings": extra["bindings"] + list(additional_proof_bindings)}
            write_immutable(attempt / 'verified-group/completion-with-content-repair.json', extra)
    output = continuation_root(root,attempt)
    if output.exists():
        raise ValueError("continuation root already exists; never overwrite/retry")
    argv = continuation_inputs(root, output, extra, additional_source_repair_paths=additional_source_repair_paths) if additional_source_repair_paths else continuation_inputs(root, output, extra)
    # Normal qualification makes and verifies the fresh pg_dump backup; normal
    # dry-run proves zero model calls/writes before the unique production screen.
    env = os.environ.copy()
    env["CODEX_EXECUTABLE"] = str(executable)
    run_logged([*argv, "--dry-run"], attempt, "campaign-preflight", env=env)
    progress = read(output / "progress.json")
    if progress["status"] == "complete":
        receipt = {"status": "complete", "campaign_root": str(output), "progress": progress,
                   "production_complete": True, "production_restarted": False}
        write_immutable(attempt / "continuation-receipt.json", receipt)
        return receipt
    if progress["status"] != "preflight_passed_no_model_calls":
        raise ValueError("dry-run did not verify the next group")
    backup = read(output / "backup.json")
    if binding(backup["path"]) != backup:
        raise ValueError("fresh backup SHA drift")
    run_logged(["pg_restore", "--list", backup["path"]], attempt, "backup-restore-list")
    if prior_model_batch is not None:
        from backend.pipeline.viewpoint_model_cache_handoff import prepare_handoff, verify_prefix
        from backend.api.canonical_repository.viewpoint_production_safety import build_batch_identity
        target_group = output / f"g{progress['sequence']:03d}" / 'production'
        freeze_path = target_group / 'inputs/freeze.json'
        freeze = read(freeze_path)
        scope = read(target_group / 'inputs/scope-packet.json')
        grouping = read(target_group / 'inputs/grouping.json')
        identity = build_batch_identity(scope_label=scope['scope_label'],
            freeze_sha256=freeze['artifact_sha256'], grouping_sha256=grouping['artifact_sha256'],
            claim_ids=groups[progress['group_key']]['claim_ids'], cvp_policy_sha256=freeze['cvp_policy_sha256'])
        prepare_handoff(prior_model_batch, target_group / 'run/batches' / identity['batch_key_sha256'],
                        target_batch_id=identity['batch_id'], target_freeze=freeze_path,
                        source_eligibility=prior_model_batch.parents[2] / 'inputs/source-eligibility.json',
                        target_eligibility=target_group / 'inputs/source-eligibility.json',
                        include_correction=reuse_correction)
        policy = read(PROJECT_ROOT / 'backend/pipeline/policies/wang_cvp_resolution_policy_v3.json')
        verify_prefix(scope, identity['batch_id'], target_group / 'run/batches' / identity['batch_key_sha256'],
                      int(policy['max_request_bytes']))
    from backend.pipeline.viewpoint_cvp_recovery_supervisor import process_table, remaining_producers, current_job_process_ids
    if [p for p in remaining_producers(process_table(), root.parent) if p not in current_job_process_ids()]:
        raise ValueError("producer active; do not start another")
    # macOS ships screen 4.00.03: it has no -Logfile option. Supply a fixed
    # config so logging begins before the first production command.
    logfile = str(output / "screenlog")
    if any(c in logfile for c in ('\n', '\r', '"', '\\')):
        raise ValueError("noncanonical screen logfile")
    screenrc = attempt / "campaign.screenrc"
    with screenrc.open("x") as stream:
        stream.write(f'logfile "{logfile}"\nlogfile flush 1\ndeflog on\n')
    run_logged(["screen", "-c", screenrc, "-dmS", "wkp357-cvp-matthew", *argv],
               attempt, "start-campaign", env=env)
    pid = None
    for _ in range(5):
        table = process_table()
        matches = [p for p, c in table.items() if "-m backend.pipeline.viewpoint_cvp_campaign_runner " in c
                   and f"--output-root {output}" in c and not c.startswith(("SCREEN", "login"))]
        if len(matches) == 1:
            pid = matches[0]
            break
        time.sleep(1)
    if pid is None:
        raise ValueError("could not verify new campaign PID; never claim started")
    run_logged(["screen", "-dmS", "wkp357-cvp-recovery", PYTHON, "-m",
                "backend.pipeline.viewpoint_cvp_recovery_supervisor", "--campaign-root", output,
                "--campaign-pid", pid, "--recovery-root", recovery_root,
                "--codex-executable", executable], attempt, "attach-supervisor", env=env)
    receipt = {"status": "resumed", "campaign_root": str(output), "campaign_pid": pid,
               "progress": read(output / "progress.json"), "backup": backup,
               "production_complete": False}
    write_immutable(attempt / "continuation-receipt.json", receipt)
    return receipt


def execute(proposal, baseline, attempt, event, recovery_root, executable):
    if (attempt / "executor-start.json").exists():
        raise ValueError("executor already attempted; no duplicate repair/verification")
    write_immutable(attempt / "executor-start.json", {"fingerprint": event["fingerprint"], "baseline": baseline})
    if set(proposal) != {"decision", "reason", "changes"} or not isinstance(proposal["reason"], str):
        raise ValueError("malformed proposal")
    if proposal.get("decision") == "needs_owner":
        if proposal["changes"]:
            raise ValueError("owner disposition cannot include edits")
        return {"status": "needs_owner", "reason": proposal["reason"], "production_complete": False}
    apply_and_commit(proposal, baseline, attempt)
    return resume_after_repair(Path(event["campaign_root"]), attempt, recovery_root, executable)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-audit", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    cfg, schedule, manifest, groups = load_campaign(args.campaign_root)
    verify_group_audit(args.campaign_root, cfg, schedule, manifest, groups, args.check_audit, args.output_root)


if __name__ == "__main__":
    main()
