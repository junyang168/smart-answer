"""Whole-CVP route proposal and multi-round independent review.

This service has no Registry writer. The worker applies only a complete,
passing route set whose final review binds the exact proposal being written.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.api.canonical_repository.viewpoint_batch_resolution import (
    ArgumentRouteProposalV2Response,
    ArgumentRouteReconsiderationV2Response,
    ArgumentRouteReviewV2Response,
    RouteComponentBinding,
    canonicalize_proposal,
    canonicalize_review,
    validate_route_proposal,
    validate_route_reconsideration,
    validate_route_review,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
from backend.api.canonical_repository.viewpoint_route_changeset import compile_argument_route_package
from backend.pipeline.viewpoint_resolution_runtime import (
    call_model,
    write_derived,
    write_immutable,
)
from backend.pipeline.viewpoint_route_resolution import (
    apply_confirmed_unattested_members,
    build_route_review_batches,
    route_member_source_review_targets,
)


class CvpRouteReviewIncomplete(ValueError):
    """The current CVP route set has no complete independent approval."""


def compile_cvp_route_package(
    *, result: dict[str, Any], route_packet: dict[str, Any],
    materials: dict[str, Any], proposer_model_id: str,
    reviewer_model_id: str, decided_at: str,
) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """Derive one coherent write and withdraw instances absent from that set."""

    proposal = result["_effective_proposal"]
    claims = [ReviewClaim.model_validate(item) for item in route_packet["claims"]]
    package = compile_argument_route_package(
        proposal=proposal,
        passing_route_keys=result["passing_route_keys"],
        passing_attestation_keys=result["passing_attestation_keys"],
        route_packet=route_packet,
        existing_routes=materials["existing_routes"],
        existing_attestation_ids=[item["argument_route_attestation_id"]
                                  for item in materials["existing_attestations"]],
        claims=claims,
        proposal_artifact_sha256=result["proposal_sha256"],
        review_artifact_sha256=result["review_sha256"],
        proposer_model_id=proposer_model_id,
        reviewer_model_id=reviewer_model_id,
        decided_at=decided_at,
    )
    old = {item["route_revision_id"]: item for item in materials["existing_routes"]}
    for revision_id in result["retired_route_revision_ids"]:
        if revision_id not in old:
            raise ValueError("reviewed route retirement is not in the input cut")
        package.setdefault("argument_routes", []).append(
            dict(old[revision_id]["route"]) | {"route_status": "retired"}
        )
    keeping = {item["argument_route_attestation_id"]
               for item in package.get("argument_route_attestations", [])} | set(
                   package.get("unchanged_attestation_ids", [])
               )
    retiring = sorted(("argument_route_attestations", item["argument_route_attestation_id"])
                      for item in materials["existing_attestations"]
                      if item["argument_route_attestation_id"] not in keeping)
    return package, retiring


def run_cvp_route_scope(
    *,
    route_packet: dict[str, Any],
    existing_routes: list[dict[str, Any]],
    output_dir: Path,
    proposer: Any,
    reviewer: Any,
    reconsiderer: Any,
    max_correction_rounds: int,
) -> dict[str, Any]:
    """Review the entire replacement set on every round, then stop at a pass.

    Model calls are cached by round. Any reject/defer, stale binding, lack of
    progress, or exhausted correction policy leaves the CVP untouched.
    """

    if max_correction_rounds < 0:
        raise ValueError("max_correction_rounds must be nonnegative")
    if route_packet.get("membership_ledger", {}).get("no_route_semantics") != (
        "no_attested_route_in_complete_cvp_material"
    ):
        raise ValueError("whole-CVP route review requires complete CVP material")
    scope_label = str(route_packet["scope_label"])
    claims = [ReviewClaim.model_validate(item) for item in route_packet["claims"]]
    bindings = [RouteComponentBinding.model_validate(item)
                for item in route_packet["claim_components"]]
    known = [str(item["route_revision_id"]) for item in existing_routes]
    known_conclusions = {str(item["route_revision_id"]): str(
        item["conclusion_viewpoint_revision_id"]) for item in existing_routes}
    known_viewpoints = {str(item["route_revision_id"]): str(
        item["route"]["conclusion_viewpoint_id"]) for item in existing_routes}
    validation_args = dict(
        scope_label=scope_label,
        claims=claims,
        approved_viewpoint_revision_ids=route_packet["approved_viewpoint_revision_ids"],
        known_route_revision_ids=known,
        known_route_conclusions=known_conclusions,
        known_route_viewpoint_ids=known_viewpoints,
        approved_viewpoint_ids={str(item["viewpoint_revision_id"]): str(item["viewpoint_id"])
                                for item in route_packet["approved_viewpoints"]},
        component_bindings=bindings,
        allow_unresolved_member_sources=True,
        complete_cvp=True,
    )
    write_immutable(output_dir / "route-packet.json", route_packet)
    raw, _, _ = call_model(proposer, route_packet,
                           output_dir / "raw-route-proposal-000.json")
    proposal = ArgumentRouteProposalV2Response.model_validate(
        canonicalize_proposal(raw)[0]
    )
    seen: set[str] = set()
    rounds: list[dict[str, Any]] = []
    last_reconsideration: dict[str, Any] | None = None
    for round_number in range(max_correction_rounds + 1):
        proposal_payload = proposal.model_dump(mode="json")
        proposal_sha = sha256_json(proposal_payload)
        if proposal_sha in seen:
            raise CvpRouteReviewIncomplete("route correction repeated an earlier proposal")
        seen.add(proposal_sha)
        proposal_validation = validate_route_proposal(routes=proposal, **validation_args)
        # The complete set, including retirements and every old attestation,
        # is visible to one independent review call. No target slicing.
        review_packet = build_route_review_batches(
            proposal=proposal,
            route_packet=route_packet,
            route_proposal_sha256=proposal_sha,
            whole_cvp=True,
        )[0]
        write_immutable(output_dir / f"route-proposal-{round_number:03d}.json", {
            "proposal_sha256": proposal_sha,
            "proposal": proposal_payload,
            "validation": proposal_validation,
        })
        write_immutable(output_dir / f"route-review-packet-{round_number:03d}.json",
                        review_packet)
        raw_review, _, _ = call_model(
            reviewer, review_packet,
            output_dir / f"raw-route-review-{round_number:03d}.json",
        )
        review = ArgumentRouteReviewV2Response.model_validate(
            canonicalize_review(raw_review)[0]
        )
        review_validation = validate_route_review(
            review=review,
            proposal=proposal,
            route_proposal_sha256=proposal_sha,
            route_evidence_packet_sha256=route_packet["packet_sha256"],
            expected_targets={(item["target_kind"], item["target_key"])
                              for item in review_packet["review_targets"]},
            allowed_claim_component_keys={item.claim_component_key for item in bindings},
            member_source_targets=set(route_member_source_review_targets(
                proposal=proposal, route_packet=route_packet
            )),
        )
        review_payload = review.model_dump(mode="json")
        review_sha = sha256_json(review_payload)
        write_immutable(output_dir / f"route-review-{round_number:03d}.json", {
            "proposal_sha256": proposal_sha,
            "review_sha256": review_sha,
            "review": review_payload,
            "validation": review_validation,
        })
        rounds.append({"round": round_number, "proposal_sha256": proposal_sha,
                       "review_sha256": review_sha,
                       "outcome": review_validation["outcome"]})
        if review_validation["outcome"] == "pass":
            effective = apply_confirmed_unattested_members(proposal=proposal, review=review)
            validate_route_proposal(routes=effective, **(validation_args | {
                "allow_unresolved_member_sources": False
            }))
            result = {
                "schema_version": "wang_cvp_route_scope_run_v2",
                "scope_label": scope_label,
                "route_packet_sha256": route_packet["packet_sha256"],
                "proposal_sha256": sha256_json(effective.model_dump(mode="json")),
                "review_sha256": review_sha,
                "rounds": rounds,
                "passing_route_keys": sorted(item.local_route_key for item in effective.argument_route_candidates),
                "passing_attestation_keys": sorted(item.local_attestation_key for item in effective.source_route_attestations),
                "retired_route_revision_ids": effective.retired_route_revision_ids,
                "cvp_re_review_exceptions": [item.model_dump(mode="json")
                                               for item in review.cvp_re_review_exceptions],
                "_effective_proposal": effective,
            }
            persisted = {key: value for key, value in result.items() if key != "_effective_proposal"}
            write_derived(output_dir / "route-cvp-run.json",
                          persisted | {"artifact_sha256": sha256_json(persisted)})
            return result
        if (not review_validation["reconsideration_required"]
                or review_validation["exception_keys"]
                or round_number == max_correction_rounds):
            raise CvpRouteReviewIncomplete(
                f"CVP route review stopped at round {round_number}: {review_validation}"
            )
        reconsider_packet = {
            "scope_label": scope_label,
            "route_evidence_packet": route_packet,
            "route_proposal_sha256": proposal_sha,
            "route_proposal": proposal_payload,
            "route_review_sha256": review_sha,
            "route_review": review_payload,
            "previous_correction": last_reconsideration,
            "instruction": "Return a complete revised route set; the next review sees every route and attestation.",
        }
        raw_reconsideration, _, _ = call_model(
            reconsiderer, reconsider_packet,
            output_dir / f"raw-route-reconsideration-{round_number:03d}.json",
        )
        reconsideration = ArgumentRouteReconsiderationV2Response.model_validate(
            canonicalize_proposal(raw_reconsideration)[0]
        )
        reconsideration_validation = validate_route_reconsideration(
            reconsideration=reconsideration,
            proposal=proposal,
            review=review,
            route_proposal_sha256=proposal_sha,
            route_review_sha256=review_sha,
            whole_cvp=True,
        )
        if reconsideration_validation["outcome"] != "resolved":
            raise CvpRouteReviewIncomplete("route correction left a reviewer finding unresolved")
        last_reconsideration = reconsideration.model_dump(mode="json")
        write_immutable(output_dir / f"route-reconsideration-{round_number:03d}.json", {
            "reconsideration": last_reconsideration,
            "validation": reconsideration_validation,
        })
        proposal = reconsideration.revised_proposal
    raise AssertionError("route review loop did not terminate")
