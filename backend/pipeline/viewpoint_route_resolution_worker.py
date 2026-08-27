"""Resolve queued ArgumentRoutes from committed CanonicalViewpoint Registry state."""

from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import (
    ChangeSetConflict,
    PostgresKnowledgeStore,
    record_content_sha,
)
from backend.api.canonical_repository.viewpoint_batch_resolution import RouteResolutionWorkUnit
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
from backend.api.canonical_repository.viewpoint_route_changeset import (
    compile_argument_route_package,
)
from backend.api.canonical_repository.viewpoint_route_queue import FileRouteResolutionQueue
from backend.pipeline.viewpoint_resolution_runtime import (
    PROJECT_ROOT,
    read_artifact as _read,
    stable_decided_at as _stable_decided_at,
    write_derived as _write_derived,
    write_immutable as _write_immutable,
)
from backend.pipeline.viewpoint_route_resolution import (
    build_registry_route_packet,
    build_route_proposer,
    build_route_reconsiderer,
    build_route_reviewer,
    run_route_scope,
)
from backend.pipeline.viewpoint_scope_packet_runner import (
    SCOPE_PACKET_VERSION,
    registry_context,
)
from backend.pipeline.viewpoint_route_policy import (
    DEFAULT_ROUTE_POLICY_PATH,
    load_route_policy,
    route_policy_fingerprint,
    route_policy_prompt_sha256s,
)

DEFAULT_ROUTE_MAX_JOBS = 1


def _current_viewpoint_revisions(store: PostgresKnowledgeStore) -> dict[str, str]:
    return {
        str(item["viewpoint_id"]): str(item["current_revision_id"])
        for item in store.list_records("canonical_viewpoints")
        if item.get("route_status") != "retired"
    }


def _existing_route_context(store: PostgresKnowledgeStore) -> list[dict[str, Any]]:
    revisions = {
        str(item["argument_route_revision_id"]): item
        for item in store.list_records("argument_route_revisions")
    }
    return [
        {
            "argument_route_id": str(route["argument_route_id"]),
            "route_revision_id": str(route["current_revision_id"]),
            "conclusion_viewpoint_revision_id": str(
                revisions[str(route["current_revision_id"])][
                    "validated_against_conclusion_viewpoint_revision_id"
                ]
            ),
            "route": route,
            "revision": revisions[str(route["current_revision_id"])],
        }
        for route in store.list_records("argument_routes")
        if route.get("route_status") == "active"
        and str(route.get("current_revision_id")) in revisions
    ]


def build_route_apply_readback_receipt(
    *,
    route_work_unit_sha256: str,
    route_packet_sha256: str,
    route_proposal_sha256: str,
    route_review_sha256: str,
    changeset_fingerprint_sha256: str,
    expected_current_viewpoint_revisions: dict[str, str],
    observed_current_viewpoint_revisions: dict[str, str | None],
    expected_records: list[tuple[str, str, dict[str, Any]]],
    observed_records: dict[tuple[str, str], dict[str, Any] | None],
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Build a receipt and distinguish master-record drift from CVP-cut drift."""

    record_readbacks = []
    record_mismatches = []
    for collection, object_id, expected_payload in expected_records:
        observed = observed_records.get((collection, object_id))
        expected_sha = record_content_sha(expected_payload)
        observed_sha = record_content_sha(observed) if observed is not None else None
        record_readbacks.append(
            {
                "collection": collection,
                "object_id": object_id,
                "expected_content_sha256": expected_sha,
                "observed_content_sha256": observed_sha,
            }
        )
        if expected_sha != observed_sha:
            record_mismatches.append(f"{collection}:{object_id}")
    after_cut = {
        key: observed_current_viewpoint_revisions.get(key)
        for key in expected_current_viewpoint_revisions
    }
    cvp_cut_mismatches = []
    if after_cut != expected_current_viewpoint_revisions:
        cvp_cut_mismatches.extend(
            f"canonical_viewpoints:{key}:expected={value}:observed={after_cut.get(key)}"
            for key, value in sorted(expected_current_viewpoint_revisions.items())
            if after_cut.get(key) != value
        )
    receipt = {
        "schema_version": "wang_argument_route_apply_readback_receipt_v1",
        "route_work_unit_sha256": route_work_unit_sha256,
        "route_packet_sha256": route_packet_sha256,
        "route_proposal_sha256": route_proposal_sha256,
        "route_review_sha256": route_review_sha256,
        "changeset_fingerprint_sha256": changeset_fingerprint_sha256,
        "before_current_viewpoint_revisions": dict(
            sorted(expected_current_viewpoint_revisions.items())
        ),
        "after_current_viewpoint_revisions": dict(sorted(after_cut.items())),
        "approved_cvps_unchanged": after_cut == expected_current_viewpoint_revisions,
        "record_readbacks": sorted(
            record_readbacks,
            key=lambda item: (item["collection"], item["object_id"]),
        ),
        "record_mismatches": record_mismatches,
        "cvp_cut_mismatches": cvp_cut_mismatches,
        "readback_status": (
            "verified"
            if not record_mismatches and not cvp_cut_mismatches
            else "mismatch"
        ),
    }
    receipt["artifact_sha256"] = sha256_json(receipt)
    return receipt, record_mismatches, cvp_cut_mismatches


def _persist_cvp_re_review_exceptions(
    *, output_dir: Path, work: RouteResolutionWorkUnit, report: dict[str, Any]
) -> str | None:
    exceptions = list(report.get("cvp_re_review_exceptions") or [])
    if not exceptions:
        return None
    body = {
        "schema_version": "wang_cvp_re_review_exception_inbox_v1",
        "route_work_unit_sha256": work.artifact_sha256,
        "route_review_sha256": report["route_review_sha256"],
        "exceptions": exceptions,
        "status": "open",
    }
    artifact = body | {"artifact_sha256": sha256_json(body)}
    _write_immutable(
        output_dir
        / "exceptions"
        / f"cvp-rereview-{artifact['artifact_sha256']}.json",
        artifact,
    )
    return str(artifact["artifact_sha256"])


def classify_route_apply_readback(
    *, record_mismatches: list[str], cvp_cut_mismatches: list[str]
) -> str:
    """Name the post-commit state without pretending an applied write failed."""

    if record_mismatches:
        return "applied_with_readback_error"
    if cvp_cut_mismatches:
        return "applied_then_superseded"
    return "applied"


def process_work_unit(
    *,
    work: RouteResolutionWorkUnit,
    scope_packet: dict[str, Any],
    output_dir: Path,
    store: PostgresKnowledgeStore,
    proposer: Any,
    reviewer: Any,
    reconsiderer: Any,
    apply: bool,
    review_targets_per_batch: int = 12,
    call_timeout_seconds: float = 900.0,
) -> dict[str, Any]:
    """Run one claimed unit; database mutation remains an explicit option."""

    if scope_packet.get("schema_version") != SCOPE_PACKET_VERSION:
        raise ValueError("Route worker requires the current viewpoint scope packet")
    if scope_packet.get("scope_label") != work.scope_label:
        raise ValueError("Route work and evidence packet scope differ")
    if scope_packet.get("claim_manifest_sha256") != work.scope_manifest_sha256:
        raise ValueError("Route work and Claim manifest differ")
    if scope_packet.get("packet_sha256") != work.evidence_scope_sha256:
        raise ValueError("Route work and evidence packet SHA differ")

    claims = [ReviewClaim.model_validate(item) for item in scope_packet["claims"]]
    all_context = registry_context(
        store.list_records("canonical_viewpoints"),
        store.list_records("viewpoint_revisions"),
    )
    expected = {
        item.viewpoint_id: item.viewpoint_revision_id
        for item in work.current_viewpoint_revisions
    }
    approved = [
        item for item in all_context
        if expected.get(str(item["viewpoint_id"])) == str(item["viewpoint_revision_id"])
    ]
    if len(approved) != len(expected):
        raise ValueError("Registry no longer contains the work unit conclusion cut")
    existing_routes = _existing_route_context(store)
    packet = build_registry_route_packet(
        scope_label=work.scope_label,
        approved_viewpoints=approved,
        claims=claims,
        viewpoint_claim_links=store.list_records("viewpoint_claim_links"),
        existing_routes=existing_routes,
    )
    _write_immutable(output_dir / "work-unit.json", work.model_dump(mode="json"))
    report = run_route_scope(
        scope_label=work.scope_label,
        claims=claims,
        existing_routes=existing_routes,
        output_dir=output_dir,
        proposer=proposer,
        reviewer=reviewer,
        reconsiderer=reconsiderer,
        route_packet=packet,
        review_targets_per_batch=review_targets_per_batch,
        call_timeout_seconds=call_timeout_seconds,
    )
    cvp_exception_artifact_sha256 = _persist_cvp_re_review_exceptions(
        output_dir=output_dir, work=work, report=report
    )
    if not report["passing_route_keys"]:
        result = {
            "status": "no_passing_routes",
            "change_set_id": None,
            "change_count": 0,
            "exceptions": report["exceptions"] or ["no_passing_routes"],
            "cvp_re_review_exceptions": report["cvp_re_review_exceptions"],
            "cvp_re_review_exception_artifact_sha256": cvp_exception_artifact_sha256,
        }
        result["artifact_sha256"] = sha256_json(result)
        _write_derived(output_dir / "route-worker-result.json", result)
        return result
    raw_proposal = _read(output_dir / "raw-route-proposal.json")
    final_review_manifest = output_dir / "raw-route-final-review-manifest.json"
    raw_review = _read(
        final_review_manifest
        if report.get("route_final_review_sha256")
        else output_dir / "raw-route-review-manifest.json"
    )
    package = compile_argument_route_package(
        proposal=report["_effective_proposal"],
        passing_route_keys=report["passing_route_keys"],
        passing_attestation_keys=report["passing_attestation_keys"],
        route_packet=packet,
        existing_routes=existing_routes,
        existing_attestation_ids=[
            str(item["argument_route_attestation_id"])
            for item in store.list_records("argument_route_attestations")
        ],
        claims=claims,
        proposal_artifact_sha256=str(report["effective_route_proposal_sha256"]),
        review_artifact_sha256=str(report["route_review_sha256"]),
        proposer_model_id=str(raw_proposal["model_id"]),
        reviewer_model_id=str(raw_review["model_id"]),
        decided_at=_stable_decided_at(output_dir),
    )
    _write_immutable(output_dir / "route-change-package.json", package)
    # Revising a route strands the attestations pinned to the revision it
    # replaces: the projection rejects an attestation whose route revision is
    # not current. They are immutable, so they are withdrawn rather than edited,
    # and in the same change set as the revision that stranded them -- both
    # because two change sets leave a window where the store holds both or
    # neither, and because the validator inside plan_package would otherwise
    # refuse the package for a state neither half intends to leave behind.
    superseded = set(package.get("superseded_route_revision_ids") or [])
    keeping = {
        str(row["argument_route_attestation_id"])
        for row in package.get("argument_route_attestations") or []
    }
    stale = [
        ("argument_route_attestations", str(item["argument_route_attestation_id"]))
        for item in store.list_records("argument_route_attestations")
        if str(item.get("validated_against_route_revision_id")) in superseded
        and str(item["argument_route_attestation_id"]) not in keeping
    ] if superseded else []
    plan = store.plan_package(
        package,
        source_kind="argument_route_resolution",
        retiring_keys=stale,
    )
    plan_document = plan.as_dict() | {
        "schema_version": "wang_argument_route_changeset_plan_v2",
        "apply_allowed": apply,
    }
    plan_document["artifact_sha256"] = sha256_json(plan_document)
    _write_derived(output_dir / "route-change-plan.json", plan_document)
    result: dict[str, Any] = {
        "status": "planned",
        "change_set_id": plan.change_set_id,
        "change_count": len(plan.operations),
        "exceptions": report["exceptions"],
        "cvp_re_review_exceptions": report["cvp_re_review_exceptions"],
        "cvp_re_review_exception_artifact_sha256": cvp_exception_artifact_sha256,
    }
    if apply:
        applied = store.apply_plan(
            plan,
            metadata={
                "route_work_unit_sha256": work.artifact_sha256,
                "route_packet_sha256": packet["packet_sha256"],
                "route_review_sha256": report["route_review_sha256"],
            },
            expected_current_viewpoint_revisions=expected,
        )
        expected_records = []
        observed_records = {}
        for collection, id_field in (
            ("argument_routes", "argument_route_id"),
            ("argument_route_revisions", "argument_route_revision_id"),
            ("argument_route_attestations", "argument_route_attestation_id"),
        ):
            for item in package.get(collection) or []:
                object_id = str(item[id_field])
                observed = store.get_record(collection, object_id)
                expected_records.append((collection, object_id, item))
                observed_records[(collection, object_id)] = observed
        observed_current = _current_viewpoint_revisions(store)
        (
            receipt,
            record_mismatches,
            cvp_cut_mismatches,
        ) = build_route_apply_readback_receipt(
            route_work_unit_sha256=work.artifact_sha256,
            route_packet_sha256=packet["packet_sha256"],
            route_proposal_sha256=report["effective_route_proposal_sha256"],
            route_review_sha256=report["route_review_sha256"],
            changeset_fingerprint_sha256=plan.fingerprint_sha256,
            expected_current_viewpoint_revisions=expected,
            observed_current_viewpoint_revisions=observed_current,
            expected_records=expected_records,
            observed_records=observed_records,
        )
        _write_immutable(output_dir / "route-apply-readback-receipt.json", receipt)
        result |= {
            "status": classify_route_apply_readback(
                record_mismatches=record_mismatches,
                cvp_cut_mismatches=cvp_cut_mismatches,
            ),
            "apply_result": applied,
            "readback_receipt_sha256": receipt["artifact_sha256"],
            "record_mismatches": record_mismatches,
            "cvp_cut_mismatches": cvp_cut_mismatches,
            "observed_current_viewpoint_revisions": observed_current,
        }
    result["artifact_sha256"] = sha256_json(result)
    _write_derived(output_dir / "route-worker-result.json", result)
    return result


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--worker-id", default=f"{socket.gethostname()}:{os.getpid()}")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--route-policy", type=Path, default=DEFAULT_ROUTE_POLICY_PATH)
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=DEFAULT_ROUTE_MAX_JOBS,
        help=(
            "take at most this many queued jobs into one work unit; the route "
            "packet's size follows the viewpoints it covers (default: 1)"
        ),
    )
    parser.add_argument(
        "--retry-exceptions",
        action="store_true",
        help="also claim jobs that ended in exception, for retrying after a fix",
    )
    args = parser.parse_args()

    policy = load_route_policy(args.route_policy)
    policy_sha256 = route_policy_fingerprint(
        policy,
        prompt_sha256s=route_policy_prompt_sha256s(
            policy,
            prompt_dir=Path(__file__).resolve().parent / "prompts",
        ),
    )
    scope_packet = _read(args.packet)
    if scope_packet.get("schema_version") != SCOPE_PACKET_VERSION:
        raise SystemExit("supplied packet is not a current viewpoint scope packet")
    for field_name in ("scope_label", "claim_manifest_sha256", "packet_sha256"):
        if not scope_packet.get(field_name):
            raise SystemExit(f"supplied packet has no {field_name}")
    packet_body = {
        key: value for key, value in scope_packet.items() if key != "packet_sha256"
    }
    if sha256_json(packet_body) != scope_packet["packet_sha256"]:
        raise SystemExit("supplied viewpoint scope packet SHA mismatch")

    store = PostgresKnowledgeStore(args.database_url)
    queue = FileRouteResolutionQueue(args.queue_dir)
    work = queue.claim(
        worker_id=args.worker_id,
        current_viewpoint_revisions=_current_viewpoint_revisions(store),
        scope_label=str(scope_packet["scope_label"]),
        scope_manifest_sha256=str(scope_packet["claim_manifest_sha256"]),
        evidence_scope_sha256=str(scope_packet["packet_sha256"]),
        route_policy_fingerprint_sha256=policy_sha256,
        retry_exceptions=args.retry_exceptions,
        max_jobs=args.max_jobs,
    )
    if work is None:
        print(json.dumps({"status": "idle"}, ensure_ascii=False))
        return 0
    run_dir = args.output_dir / work.artifact_sha256
    try:
        jobs = queue.jobs_for_work_unit(work)
        if {
            item.route_policy_fingerprint_sha256 for item in jobs
        } != {policy_sha256} or work.route_policy_fingerprint_sha256 != policy_sha256:
            raise ValueError("claimed Route work does not bind its selected policy")
        if {item.evidence_scope_sha256 for item in jobs} != {
            str(scope_packet["packet_sha256"])
        } or work.evidence_scope_sha256 != str(scope_packet["packet_sha256"]):
            raise ValueError("claimed Route work does not bind its selected evidence packet")
        result = process_work_unit(
            work=work,
            scope_packet=scope_packet,
            output_dir=run_dir,
            store=store,
            proposer=build_route_proposer(
                str(policy["proposal"]["model"]),
                str(policy["proposal"]["effort"]),
                provider=str(policy["proposal"]["provider"]),
                prompt_file=str(policy["prompts"]["proposal"]),
                timeout_seconds=float(policy["call_timeout_seconds"]),
            ),
            reviewer=build_route_reviewer(
                str(policy["review"]["model"]),
                str(policy["review"]["effort"]),
                provider=str(policy["review"]["provider"]),
                prompt_file=str(policy["prompts"]["review"]),
                timeout_seconds=float(policy["call_timeout_seconds"]),
            ),
            reconsiderer=build_route_reconsiderer(
                str(policy["correction"]["model"]),
                str(policy["correction"]["effort"]),
                provider=str(policy["correction"]["provider"]),
                prompt_file=str(policy["prompts"]["correction"]),
                timeout_seconds=float(policy["call_timeout_seconds"]),
            ),
            review_targets_per_batch=int(policy["review"]["targets_per_batch"]),
            call_timeout_seconds=float(policy["call_timeout_seconds"]),
            apply=args.apply,
        )
    except ChangeSetConflict as exc:
        current = _current_viewpoint_revisions(store)
        stale = {
            item.viewpoint_id: {
                "expected": item.viewpoint_revision_id,
                "current": current.get(item.viewpoint_id),
            }
            for item in work.current_viewpoint_revisions
            if current.get(item.viewpoint_id) != item.viewpoint_revision_id
        }
        if not stale:
            queue.finish(work, worker_id=args.worker_id, status="exception", detail=str(exc))
            raise
        detail = "Route conclusion cut superseded: " + json.dumps(
            stale, ensure_ascii=False, sort_keys=True
        )
        transition = queue.resolve_supersession(
            work,
            worker_id=args.worker_id,
            current_viewpoint_revisions=current,
            detail=detail,
        )
        print(json.dumps(transition | {"detail": detail}, ensure_ascii=False))
        return 0 if transition["status"] == "superseded" else 1
    except Exception as exc:
        queue.finish(work, worker_id=args.worker_id, status="exception", detail=str(exc))
        raise
    if args.apply and result["status"] == "applied_then_superseded":
        detail = "Route applied, then conclusion cut advanced"
        transition = queue.resolve_supersession(
            work,
            worker_id=args.worker_id,
            current_viewpoint_revisions=result[
                "observed_current_viewpoint_revisions"
            ],
            detail=detail,
        )
        result["queue_transition"] = transition
        result["artifact_sha256"] = sha256_json(
            {key: value for key, value in result.items() if key != "artifact_sha256"}
        )
        _write_derived(run_dir / "route-worker-result.json", result)
    elif args.apply:
        attention = list(result["exceptions"])
        attention.extend(
            item.get("reason", "CVP re-review requested")
            for item in result["cvp_re_review_exceptions"]
        )
        if result["status"] == "applied_with_readback_error":
            attention.extend(result["record_mismatches"])
        queue.finish(
            work,
            worker_id=args.worker_id,
            status="exception" if attention else "resolved",
            detail="; ".join(attention) if attention else "applied and read back",
        )
    else:
        queue.release(work, worker_id=args.worker_id, detail="plan-only run; no master mutation")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
