"""Whole-CVP route proposal and multi-round independent review.

This service has no Registry writer. The worker applies only a complete,
passing route set whose final review binds the exact proposal being written.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend.api.canonical_repository.viewpoint_batch_resolution import (
    ArgumentRouteProposalV2Response,
    ArgumentRouteReconsiderationV2Response,
    ArgumentRouteReviewV2Response,
    BatchResolutionError,
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
    CLAIM_EVIDENCE_BINDING_CONTRACT,
    apply_confirmed_unattested_members,
    build_route_review_batches,
    route_member_source_review_targets,
)
from backend.pipeline.viewpoint_route_review_continuity import (
    continuity_packet, review_history_entry,
)
from backend.pipeline.viewpoint_route_correction_changes import (
    correction_adapter, expand_correction, CorrectionChanges, MARKER, VERSION as CHANGES_VERSION,
    proposal_adapter, expand_proposal, ProposalChanges, PROPOSAL_MARKER, PROPOSAL_VERSION,
)
from backend.pipeline.viewpoint_route_update_projection import update_baseline
from backend.pipeline.viewpoint_route_validation_repair import validate_changes, REVIEW_REPAIR_INSTRUCTION


class CvpRouteReviewIncomplete(ValueError):
    """The current CVP route set has no complete independent approval."""


def route_validation_args(packet, existing_routes=None):
    routes = packet['existing_routes'] if existing_routes is None else existing_routes
    return dict(scope_label=packet['scope_label'],
        claims=[ReviewClaim.model_validate(c) for c in packet['claims']],
        approved_viewpoint_revision_ids=packet['approved_viewpoint_revision_ids'],
        known_route_revision_ids=[r['route_revision_id'] for r in routes],
        known_route_conclusions={r['route_revision_id']:r['conclusion_viewpoint_revision_id'] for r in routes},
        known_route_viewpoint_ids={r['route_revision_id']:r['route']['conclusion_viewpoint_id'] for r in routes},
        approved_viewpoint_ids={v['viewpoint_revision_id']:v['viewpoint_id'] for v in packet['approved_viewpoints']},
        component_bindings=[RouteComponentBinding.model_validate(c) for c in packet['claim_components']],
        allow_unresolved_member_sources=True, complete_cvp=True,
        claim_evidence_for_intermediate_steps=packet.get('evidence_binding_contract') == CLAIM_EVIDENCE_BINDING_CONTRACT,
        existing_observation_supports=packet.get('existing_observation_supports', []))


def parse_proposal_changes(raw, baseline, packet, validation_args=None):
    from backend.api.canonical_repository.viewpoint_observation_support import (
        CONTRACT, ArgumentRouteProposalV3Response, bind_revision_retirements)
    model = ArgumentRouteProposalV3Response if packet.get('observation_support_contract') == CONTRACT else ArgumentRouteProposalV2Response
    expanded = expand_proposal(raw, baseline, packet)
    bound, source_bindings = bind_attestation_source_ids(expanded, packet)
    proposal = model.model_validate(canonicalize_proposal(bound)[0])
    proposal, retirements = bind_revision_retirements(proposal, packet)
    validate_route_proposal(routes=proposal, **(validation_args or route_validation_args(packet)))
    return proposal, source_bindings, retirements


def bind_attestation_source_ids(
    raw: dict[str, Any], route_packet: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Restore source IDs uniquely determined by pinned Claims and source SHA.

    Model prose can change the script of a Chinese source ID.  Preserve the raw
    response, and only bind an unknown ID when every named Claim belongs to one
    source whose pinned revision SHA matches the attestation.  Known but wrong
    source IDs, mixed Claims, and stale SHAs remain validator errors.
    """

    payload = deepcopy(raw)
    proposal = payload.get("revised_proposal", payload)
    claims = {item["claim_id"]: item["source_id"] for item in route_packet["claims"]}
    source_shas = route_packet["source_revisions"]
    bindings: list[dict[str, str]] = []
    for attestation in proposal.get("source_route_attestations", []):
        declared = attestation.get("source_id")
        claim_ids = attestation.get("claim_ids") or []
        if declared in source_shas or not claim_ids or any(cid not in claims for cid in claim_ids):
            continue
        sources = {claims[cid] for cid in claim_ids}
        if len(sources) != 1:
            continue
        source_id = next(iter(sources))
        if attestation.get("source_revision_sha256") != source_shas[source_id]:
            continue
        attestation["source_id"] = source_id
        bindings.append({
            "attestation_key": str(attestation.get("local_attestation_key") or ""),
            "model_source_id": str(declared),
            "pinned_source_id": source_id,
        })
    return payload, bindings


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
    if hasattr(proposal, 'observation_supports'):
        from backend.api.canonical_repository.viewpoint_observation_support import compile_observation_package, COLLECTION
        observations, withdrawn, unchanged = compile_observation_package(
            proposal, route_packet, review_sha256=result['review_sha256'])
        package[COLLECTION] = observations
        package['unchanged_observation_support_ids'] = unchanged
        retiring.extend(withdrawn)
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
    recovery_seed: dict[str, Any] | None = None,
    connection_seed: dict[str, Any] | None = None,
    round_extension: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Review the entire replacement set on every round, then stop at a pass.

    Model calls are cached by round. Any reject/defer, stale binding, lack of
    progress, or exhausted correction policy leaves the CVP untouched.
    """

    if max_correction_rounds < 0:
        raise ValueError("max_correction_rounds must be nonnegative")
    if round_extension and max_correction_rounds != round_extension['effective_max_correction_rounds']:
        raise ValueError('Route correction limit differs from its authorized extension')
    max_program_repairs = max_correction_rounds
    if round_extension and round_extension.get('stage') in {'initial_program_validation','program_validation'}:
        # Continue one technical repair without enlarging the semantic review
        # allowance or erasing any consumed validation/provider history.
        max_correction_rounds = round_extension['original_max_correction_rounds']
    contract = route_packet.get("evidence_binding_contract")
    if contract not in (None, CLAIM_EVIDENCE_BINDING_CONTRACT):
        raise ValueError("unknown Route evidence binding contract")
    if route_packet.get("membership_ledger", {}).get("no_route_semantics") != (
        "no_attested_route_in_complete_cvp_material"
    ):
        raise ValueError("whole-CVP route review requires complete CVP material")
    scope_label = str(route_packet["scope_label"])
    from backend.api.canonical_repository.viewpoint_observation_support import (
        CONTRACT, ArgumentRouteProposalV3Response, ArgumentRouteReviewV3Response,
        ArgumentRouteReconsiderationV3Response, bind_revision_retirements,
    )
    has_observations = route_packet.get('observation_support_contract') == CONTRACT
    if route_packet.get('observation_support_contract') not in (None, CONTRACT):
        raise ValueError('unknown observation support contract')
    proposal_model = ArgumentRouteProposalV3Response if has_observations else ArgumentRouteProposalV2Response
    review_model = ArgumentRouteReviewV3Response if has_observations else ArgumentRouteReviewV2Response
    correction_model = ArgumentRouteReconsiderationV3Response if has_observations else ArgumentRouteReconsiderationV2Response
    claims = [ReviewClaim.model_validate(item) for item in route_packet["claims"]]
    bindings = [RouteComponentBinding.model_validate(item)
                for item in route_packet["claim_components"]]
    validation_args = route_validation_args(route_packet, existing_routes)
    write_immutable(output_dir / "route-packet.json", route_packet)
    last_reconsideration = None
    consumed_corrections = 0
    initial_changes = False
    program_repairs = {'used': 0, 'attempts': []}
    if recovery_seed is not None and connection_seed is not None:
        raise ValueError('Route continuations cannot be combined')
    if connection_seed is not None:
        if 'previous_program_validation' in connection_seed['receipt']:
            initial_changes = True
            program_repairs = deepcopy(connection_seed['receipt']['previous_program_validation'])
            max_program_repairs=connection_seed['receipt'].get('original_program_validation_limit',max_program_repairs)
        consumed_corrections = connection_seed['receipt']['previous_corrections_consumed']
        if consumed_corrections > max_correction_rounds:
            raise ValueError('connection continuation exhausted its original correction budget')
        raw = connection_seed['proposal']
        last_reconsideration = connection_seed['previous_correction']
        if connection_seed['receipt']['mode'] == 'correction':
            if consumed_corrections == max_correction_rounds:
                raise ValueError('connection continuation has no remaining correction')
            seed_packet = {'scope_label':scope_label,'route_evidence_packet':route_packet,
                'route_proposal':raw,'route_proposal_sha256':connection_seed['proposal_sha256'],
                'route_review':connection_seed['review'],'route_review_sha256':connection_seed['review_sha256'],
                'previous_correction':last_reconsideration,
                'connection_continuation':connection_seed['receipt'],
                'instruction':('Continue this exact saved proposal after the exception-targeted upstream member repair. '
                    'Resolve the saved CVP exception using current approved links and their actual rationale. '
                    'Return the complete revised set, including the restored source argument if justified. '
                    'Keep all unaffected evidence and findings; this receipt is not approval. '
                    'This correction consumes the original remaining correction budget.')}
            # All ordinary findings already passed; only the saved upstream
            # exception remains. Use the complete-proposal schema for this
            # correction rather than inventing a mandatory "correct" finding
            # just to satisfy the ordinary reconsideration response schema.
            seed_path=output_dir/'connection-correction-packet.json'
            response_path=output_dir/'raw-connection-correction.json'
            active=proposal_adapter(proposer,seed_path,response_path)
            compact=getattr(active,'_response_model',None) is ProposalChanges
            if compact:
                initial_changes=True
                baseline=deepcopy(raw)
                seed_packet.update({PROPOSAL_MARKER:PROPOSAL_VERSION,'route_proposal_baseline':baseline,
                    'instruction':'Continue the exact saved complete proposal after the approved member repair. Return only actual edits against route_proposal_baseline. Account for the restored member, preserve the existing valid observations and no_route judgment unless current source evidence requires a change; this is not a new initial proposal or approval.'})
            seed_packet=continuity_packet(seed_packet,[],packet_path=seed_path,response_path=response_path)
            answer,_,_=call_model(active,seed_packet,response_path)
            if compact:
                parsed,_=validate_changes(answer,
                    validate=lambda value:parse_proposal_changes(value,baseline,route_packet,validation_args),
                    adapter=active,packet=seed_packet,cache=response_path,state=program_repairs,
                    max_repairs=max_program_repairs)
                corrected=parsed[0]
            else:
                corrected=proposal_model.model_validate(canonicalize_proposal(answer)[0])
            raw=corrected.model_dump(mode='json')
            if not compact and sha256_json(raw) == connection_seed['proposal_sha256']:
                raise CvpRouteReviewIncomplete('restored membership correction made no change')
            write_immutable(output_dir/'connection-correction.json',{'proposal':raw,
                'original_proposal_sha256':connection_seed['proposal_sha256'],
                'original_review_sha256':connection_seed['review_sha256']})
            consumed_corrections+=1
    elif recovery_seed is None:
        input_path = output_dir / 'route-proposal-input.json'
        response_path = output_dir / 'raw-route-proposal-000.json'
        active_proposer = proposal_adapter(proposer, input_path, response_path)
        initial_changes = getattr(active_proposer, '_response_model', None) is ProposalChanges
        proposal_input = route_packet
        if initial_changes:
            baseline = update_baseline(route_packet)
            proposal_input = route_packet | {PROPOSAL_MARKER: PROPOSAL_VERSION,
                'route_proposal_baseline': baseline}
        proposal_input = continuity_packet(proposal_input, [],
            packet_path=input_path, response_path=response_path)
        raw, _, _ = call_model(active_proposer, proposal_input, response_path)
        if initial_changes:
            parsed, validated_raw = validate_changes(raw,
                validate=lambda answer: parse_proposal_changes(answer, baseline, route_packet, validation_args),
                adapter=active_proposer, packet=proposal_input, cache=response_path,
                state=program_repairs, max_repairs=max_program_repairs)
            initial_proposal, source_bindings, retirements = parsed
            raw = initial_proposal.model_dump(mode='json')
            write_immutable(output_dir/'route-proposal-changes-result.json', {
                'schema_version': PROPOSAL_VERSION, 'baseline_sha256': sha256_json(baseline),
                'response_sha256': sha256_json(validated_raw), 'proposal_sha256': sha256_json(raw),
                'source_bindings': source_bindings, 'retirement_binding': retirements})
    else:
        if max_correction_rounds < 1 or contract != CLAIM_EVIDENCE_BINDING_CONTRACT:
            raise ValueError("evidence input recovery requires its original remaining correction budget")
        seed_packet = {
            "scope_label": scope_label, "route_evidence_packet": route_packet,
            "route_proposal": recovery_seed["proposal"],
            "route_proposal_sha256": recovery_seed["proposal_sha256"],
            "route_review": recovery_seed["review"],
            "route_review_sha256": recovery_seed["review_sha256"],
            "engineering_input_recovery": recovery_seed["receipt"],
            "instruction": (
                "Continue the saved proposal and ALL original Opus findings. Respond to every correct "
                "finding in finding_dispositions. The original defer remains an immutable verdict; "
                "the packet now distinguishes intermediate Claim evidence from conclusion link evidence. "
                "Use that corrected binding contract to supply the deferred source's actual attestation, "
                "and explain its evidence in reason. Do not obey obsolete restrictions that excluded "
                "Claim-owned intermediate evidence solely because it was not selected by a CVP link. "
                "Do not rewrite the original review, invent evidence or treat this receipt as approval. "
                "Return the complete revised set for independent whole-CVP review."
            ),
        }
        write_immutable(output_dir / "input-recovery-correction-packet.json", seed_packet)
        answer, _, _ = call_model(reconsiderer, seed_packet,
                                  output_dir / "raw-input-recovery-correction.json")
        corrected = ArgumentRouteReconsiderationV2Response.model_validate(canonicalize_proposal(answer)[0])
        validation = validate_route_reconsideration(
            reconsideration=corrected,
            proposal=ArgumentRouteProposalV2Response.model_validate(recovery_seed["proposal"]),
            review=ArgumentRouteReviewV2Response.model_validate(recovery_seed["review"]),
            route_proposal_sha256=recovery_seed["proposal_sha256"],
            route_review_sha256=recovery_seed["review_sha256"], whole_cvp=True)
        if validation["outcome"] != "resolved":
            raise CvpRouteReviewIncomplete("input recovery left original correct findings unresolved")
        for source_id in recovery_seed["deferred_source_ids"]:
            if not any(a.source_id == source_id for a in corrected.revised_proposal.source_route_attestations):
                raise CvpRouteReviewIncomplete("input recovery did not resolve the deferred source coverage")
        last_reconsideration = corrected.model_dump(mode="json")
        write_immutable(output_dir / "input-recovery-correction.json",
                        {"reconsideration": last_reconsideration, "validation": validation})
        raw = corrected.revised_proposal.model_dump(mode="json")
        consumed_corrections = 1
    invalid_proposals: set[str] = set()
    validation_repairs = 0
    while True:
        try:
            bound_raw, source_bindings = bind_attestation_source_ids(raw, route_packet)
            proposal = proposal_model.model_validate(
                canonicalize_proposal(bound_raw)[0]
            )
            proposal, retirement_binding = bind_revision_retirements(proposal, route_packet)
            if retirement_binding:
                write_immutable(output_dir / 'observation-retirement-bindings' /
                    (sha256_json(raw) + '.json'), retirement_binding)
            validate_route_proposal(routes=proposal, **validation_args)
            if source_bindings:
                write_immutable(output_dir / "route-proposal-source-bindings.json", {
                    "raw_response_sha256": sha256_json(raw), "bindings": source_bindings,
                })
            break
        except (ValidationError, BatchResolutionError) as exc:
            if isinstance(exc, ValidationError):
                findings = [f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                            for item in exc.errors(include_input=False)]
            else:
                findings = exc.findings
            raw_sha = sha256_json(raw)
            if raw_sha in invalid_proposals or validation_repairs + consumed_corrections >= max_correction_rounds:
                raise CvpRouteReviewIncomplete(
                    f"route proposal still fails deterministic validation: {findings}"
                ) from exc
            invalid_proposals.add(raw_sha)
            write_immutable(output_dir / f"route-proposal-validation-{validation_repairs:03d}.json", {
                "invalid_proposal_sha256": raw_sha,
                "findings": findings,
            })
            repair_packet = route_packet | {
                "prior_invalid_proposal": raw,
                "validation_findings": findings,
                "instruction": "Return the complete corrected proposal in the same schema; do not invent bindings or omit old routes.",
            }
            repair_packet = continuity_packet(repair_packet, [],
                packet_path=output_dir / f'route-proposal-validation-input-{validation_repairs:03d}.json',
                response_path=output_dir / f'raw-route-proposal-validation-{validation_repairs:03d}.json')
            raw, _, _ = call_model(
                proposer, repair_packet,
                output_dir / f"raw-route-proposal-validation-{validation_repairs:03d}.json",
            )
            validation_repairs += 1
    seen: set[str] = set()
    rounds: list[dict[str, Any]] = []
    review_history = []
    remaining_review_corrections = max_correction_rounds - validation_repairs - consumed_corrections
    for round_number in range(remaining_review_corrections + 1):
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
        if connection_seed:
            review_packet['connection_continuation_context'] = {
                'original_review':connection_seed['review'],
                'receipt':connection_seed['receipt'],
                'instruction':('Independently review the entire postimage against current evidence and approved '
                    'connection judgments. Recheck the saved CVP exception with its previously omitted identity '
                    'rationale or restored member. Do not repeat an exception solely because a supporting or '
                    'qualifying source has a complete inference; identify a concrete unresolved identity/scope '
                    'problem if one remains. Prior approval does not prove this route set correct. '
                    'Keep the old verdict immutable; this continuation is not content approval.')}
            if connection_seed['receipt'].get('review_scope_contract'):
                review_packet['connection_continuation_context']['instruction'] = (
                    '材料、成员和完整候选与旧审核相同。依据本次 route_review_scope_contract，独立区分'
                    '当前结果无效与将来可补充上游成员；复核全部 targets 和旧异常。若当前确有错误仍报告，'
                    '仅属完善建议则在 review.reason 保留具体建议与依据。旧判定不改写，本接续不是批准。')
        if recovery_seed:
            review_packet["input_recovery_context"] = {
                "original_review": recovery_seed["review"],
                "recovery_receipt_sha256": recovery_seed["receipt"]["artifact_sha256"],
                "evidence_binding_contract": contract,
                "finding_dispositions": last_reconsideration["finding_dispositions"],
                "instruction": (
                    "Independently review the entire current postimage and resolution of ALL saved findings, "
                    "including the deferred source. The old verdict remains unchanged; engineering recovery "
                    "is not content approval. Apply the corrected evidence binding contract, not the old "
                    "assumption that a CVP link's selected evidence limits intermediate Claim evidence."
                ),
            }
        review_packet = continuity_packet(review_packet, review_history,
            packet_path=output_dir / f'route-review-packet-{round_number:03d}.json',
            response_path=output_dir / f'raw-route-review-{round_number:03d}.json')
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
        def parse_review(answer):
            parsed = review_model.model_validate(canonicalize_review(answer)[0])
            validated = validate_route_review(
                review=parsed, proposal=proposal,
                route_proposal_sha256=proposal_sha,
                route_evidence_packet_sha256=route_packet['packet_sha256'],
                expected_targets={(item['target_kind'], item['target_key'])
                                  for item in review_packet['review_targets']},
                allowed_claim_component_keys={item.claim_component_key for item in bindings},
                member_source_targets=set(route_member_source_review_targets(
                    proposal=proposal, route_packet=route_packet)),
            )
            return parsed, validated
        if max_program_repairs == 0:
            review, review_validation = parse_review(raw_review)
        else:
            (review, review_validation), _ = validate_changes(
                raw_review, validate=parse_review, adapter=reviewer, packet=review_packet,
                cache=output_dir / f'raw-route-review-{round_number:03d}.json',
                state={'used':0, 'attempts':[]}, max_repairs=max_program_repairs,
                repair_instruction=REVIEW_REPAIR_INSTRUCTION)
        review_payload = review.model_dump(mode="json")
        review_sha = sha256_json(review_payload)
        review_history.append(review_history_entry(
            round_number, proposal_sha, review_sha, review_payload))
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
                "proposal_validation_repairs": validation_repairs,
                **({'program_validation_repairs': program_repairs['used'],
                    'program_validation_attempts': program_repairs['attempts']} if initial_changes else {}),
                **({'correction_round_extension_sha256':round_extension['artifact_sha256']}
                   if round_extension else {}),
                **({'connection_continuation':connection_seed['receipt'],
                    'corrections_consumed_including_parent':consumed_corrections + validation_repairs + round_number}
                   if connection_seed else {}),
                **({"input_recovery": recovery_seed["receipt"],
                    "input_recovery_corrections": consumed_corrections} if recovery_seed else {}),
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
                or round_number == remaining_review_corrections):
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
        correction_path = output_dir / f'route-reconsideration-packet-{round_number:03d}.json'
        response_path = output_dir / f'raw-route-reconsideration-{round_number:03d}.json'
        active_reconsiderer = correction_adapter(reconsiderer, correction_path, response_path)
        if getattr(active_reconsiderer, '_response_model', None) is CorrectionChanges:
            reconsider_packet[MARKER] = CHANGES_VERSION
            reconsider_packet['instruction'] = 'Return only actual edits and finding dispositions; the program preserves omitted fields and submits the complete expanded result for review.'
        reconsider_packet = continuity_packet(reconsider_packet, review_history,
            packet_path=correction_path, response_path=response_path)
        raw_reconsideration, _, _ = call_model(
            active_reconsiderer, reconsider_packet, response_path,
        )
        def parse_correction(answer, response_context=None):
            from backend.pipeline.viewpoint_route_observation_restore import MARKER as RESTORE, contract
            restore_allowed=(response_context or {}).get(RESTORE)==contract(route_packet)
            corrected = correction_model.model_validate(canonicalize_proposal(
                expand_correction(answer, proposal, review, route_packet,
                    expected_contract=reconsider_packet.get(MARKER),
                    allow_observation_restore=restore_allowed))[0])
            bound_proposal, retirement = bind_revision_retirements(corrected.revised_proposal, route_packet)
            if retirement:
                corrected = corrected.model_copy(update={'revised_proposal': bound_proposal})
            checked = validate_route_reconsideration(reconsideration=corrected,
                proposal=proposal, review=review, route_proposal_sha256=proposal_sha,
                route_review_sha256=review_sha, whole_cvp=True,
                allow_unchanged_escalation=initial_changes)
            # Unaccepted semantic findings stop normally; technical repairs
            # must never force the model to accept a substantive objection.
            can_re_review = (initial_changes and checked['outcome'] == 'exception'
                and all(d.disposition != 'deferred' for d in corrected.finding_dispositions)
                and sha256_json(corrected.revised_proposal.model_dump(mode='json')) != proposal_sha)
            if initial_changes and (checked['outcome'] == 'resolved' or can_re_review):
                validate_route_proposal(routes=corrected.revised_proposal, **validation_args)
            return corrected, retirement, checked, can_re_review
        if initial_changes:
            parsed, raw_reconsideration = validate_changes(raw_reconsideration,
                validate=parse_correction, adapter=active_reconsiderer,
                packet=reconsider_packet, cache=response_path, state=program_repairs,
                max_repairs=max_program_repairs, contextual=True)
        else:
            parsed = parse_correction(raw_reconsideration,reconsider_packet)
        reconsideration, retirement_binding, reconsideration_validation, can_re_review = parsed
        if retirement_binding:
            write_immutable(output_dir / 'observation-retirement-bindings' /
                (sha256_json(raw_reconsideration) + '.json'), retirement_binding)
        if reconsideration_validation["outcome"] != "resolved" and not can_re_review:
            raise CvpRouteReviewIncomplete("route correction left a reviewer finding unresolved")
        last_reconsideration = reconsideration.model_dump(mode="json")
        review_history[-1]['finding_dispositions'] = deepcopy(last_reconsideration['finding_dispositions'])
        write_immutable(output_dir / f"route-reconsideration-{round_number:03d}.json", {
            "reconsideration": last_reconsideration,
            "validation": reconsideration_validation,
            **({'correction_expansion': {'schema_version': CHANGES_VERSION,
                'raw_response_sha256': sha256_json(raw_reconsideration),
                'base_proposal_sha256': proposal_sha,
                'expanded_proposal_sha256': sha256_json(last_reconsideration['revised_proposal'])}}
               if raw_reconsideration.get('schema_version') == CHANGES_VERSION else {}),
        })
        bound_reconsideration, source_bindings = bind_attestation_source_ids(
            last_reconsideration, route_packet
        )
        if source_bindings:
            bound = correction_model.model_validate(bound_reconsideration)
            write_immutable(output_dir / f"route-reconsideration-{round_number:03d}-bound.json", {
                "raw_response_sha256": sha256_json(raw_reconsideration),
                "source_bindings": source_bindings,
                "reconsideration": bound.model_dump(mode="json"),
            })
            proposal = bound.revised_proposal
        else:
            proposal = reconsideration.revised_proposal
    raise AssertionError("route review loop did not terminate")
