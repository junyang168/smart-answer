"""Create the immutable freeze and backup authorization for production CVP."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import (
    CVP_APPLY_AUTHORIZATION_VERSION,
    CvpProductionBlocked,
    build_canary_selection,
    build_cvp_freeze,
    exclusive_cvp_run_lock,
    file_sha256,
    validate_cvp_freeze,
    validate_grouping_envelope,
)
from backend.pipeline.viewpoint_cvp_policy import (
    DEFAULT_CVP_POLICY_PATH,
    cvp_policy_fingerprint,
    cvp_policy_prompt_sha256s,
    load_cvp_policy,
)
from backend.pipeline.viewpoint_resolution_runtime import PROJECT_ROOT, PROMPT_DIR
from backend.pipeline.viewpoint_route_policy import (
    DEFAULT_ROUTE_POLICY_PATH,
    load_route_policy,
    route_policy_fingerprint,
    route_policy_prompt_sha256s,
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_immutable(path: Path, payload: dict) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise CvpProductionBlocked([f"immutable artifact differs at {path}"])
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")


def _repository_commit() -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status.strip():
        raise CvpProductionBlocked(["production preflight worktree is not clean"])
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _policy_fingerprints(cvp_path: Path, route_path: Path) -> tuple[str, str]:
    cvp = load_cvp_policy(cvp_path)
    route = load_route_policy(route_path)
    return (
        cvp_policy_fingerprint(
            cvp,
            prompt_sha256s=cvp_policy_prompt_sha256s(cvp, prompt_dir=PROMPT_DIR),
        ),
        route_policy_fingerprint(
            route,
            prompt_sha256s=route_policy_prompt_sha256s(route, prompt_dir=PROMPT_DIR),
        ),
    )


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cvp-policy", type=Path, default=DEFAULT_CVP_POLICY_PATH)
    parser.add_argument("--route-policy", type=Path, default=DEFAULT_ROUTE_POLICY_PATH)
    parser.add_argument("--database-url")
    subcommands = parser.add_subparsers(dest="command", required=True)

    freeze_parser = subcommands.add_parser("freeze")
    freeze_parser.add_argument("--packet", type=Path, required=True)
    freeze_parser.add_argument("--source-universe-manifest", type=Path, required=True)
    freeze_parser.add_argument("--claim-manifest", type=Path, required=True)
    freeze_parser.add_argument("--source-lineage-manifest", type=Path, required=True)
    freeze_parser.add_argument("--source-attestation", type=Path, required=True)
    freeze_parser.add_argument("--source-state-reconciliation", type=Path, required=True)
    freeze_parser.add_argument("--source-state-validation", type=Path, required=True)
    freeze_parser.add_argument("--independent-audit", type=Path, required=True)
    freeze_parser.add_argument("--audit-disposition", type=Path, required=True)
    freeze_parser.add_argument("--output-root", type=Path, required=True)
    freeze_parser.add_argument("--global-lock-path", type=Path, required=True)
    freeze_parser.add_argument("--output", type=Path, required=True)

    auth_parser = subcommands.add_parser("authorize")
    auth_parser.add_argument("--freeze", type=Path, required=True)
    auth_parser.add_argument("--grouping", type=Path, required=True)
    auth_parser.add_argument("--backup-dump", type=Path, required=True)
    auth_parser.add_argument("--output", type=Path, required=True)

    canary_parser = subcommands.add_parser("select-canary")
    canary_parser.add_argument("--freeze", type=Path, required=True)
    canary_parser.add_argument("--grouping", type=Path, required=True)
    canary_parser.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    commit = _repository_commit()
    cvp_sha, route_sha = _policy_fingerprints(args.cvp_policy, args.route_policy)
    store = PostgresKnowledgeStore(args.database_url)
    if args.command == "freeze":
        freeze = build_cvp_freeze(
            ticket_id=357,
            scope_packet_path=args.packet,
            prerequisite_paths={
                "source_universe_manifest": args.source_universe_manifest,
                "claim_manifest": args.claim_manifest,
                "source_lineage_manifest": args.source_lineage_manifest,
                "source_attestation": args.source_attestation,
                "source_state_reconciliation": args.source_state_reconciliation,
                "source_state_validation": args.source_state_validation,
                "independent_audit": args.independent_audit,
                "audit_disposition": args.audit_disposition,
            },
            store=store,
            cvp_policy_sha256=cvp_sha,
            route_policy_sha256=route_sha,
            runner_commit=commit,
            worktree_root=PROJECT_ROOT,
            output_root=args.output_root,
            global_lock_path=args.global_lock_path,
        )
        _write_immutable(args.output, freeze)
        print(json.dumps({"status": "frozen", "artifact_sha256": freeze["artifact_sha256"]}))
        return 0

    freeze = _read(args.freeze)
    with exclusive_cvp_run_lock(freeze):
        validate_cvp_freeze(
            freeze,
            store=store,
            cvp_policy_sha256=cvp_sha,
            route_policy_sha256=route_sha,
            runner_commit=commit,
        )
        grouping = _read(args.grouping)
        grouping_sha = validate_grouping_envelope(grouping, freeze=freeze)
        if args.command == "select-canary":
            scope_packet = _read(Path(str(freeze["scope_packet"]["path"])))
            selection = build_canary_selection(
                grouping_envelope=grouping,
                freeze=freeze,
                scope_packet=scope_packet,
                batch_size=int(load_cvp_policy(args.cvp_policy)["batch_size"]),
            )
            _write_immutable(args.output, selection)
            print(
                json.dumps(
                    {
                        "status": "canary_selected",
                        "group_key": selection["selected_group"]["group_key"],
                        "artifact_sha256": selection["artifact_sha256"],
                    }
                )
            )
            return 0
        if not args.backup_dump.is_file() or args.backup_dump.stat().st_size <= 0:
            raise CvpProductionBlocked(["backup dump is missing or empty"])
        completed = subprocess.run(
            ["pg_restore", "--list", str(args.backup_dump)],
            check=True,
            capture_output=True,
        )
        if not completed.stdout.strip():
            raise CvpProductionBlocked(["pg_restore returned an empty archive listing"])
        body = {
            "schema_version": CVP_APPLY_AUTHORIZATION_VERSION,
            "status": "authorized",
            "freeze_sha256": freeze["artifact_sha256"],
            "grouping_sha256": grouping_sha,
            "backup_dump": {
                "path": str(args.backup_dump.resolve()),
                "sha256": file_sha256(args.backup_dump),
                "size_bytes": args.backup_dump.stat().st_size,
            },
            "pg_restore_list_sha256": hashlib.sha256(completed.stdout).hexdigest(),
            "registry_fingerprint_sha256": freeze["registry_fingerprint_sha256"],
        }
        authorization = body | {"artifact_sha256": sha256_json(body)}
        _write_immutable(args.output, authorization)
        print(json.dumps({"status": "authorized", "artifact_sha256": authorization["artifact_sha256"]}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
